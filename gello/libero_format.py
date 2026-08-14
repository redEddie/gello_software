"""LIBERO-format HDF5 writer for real FR3 + GELLO teleop demonstrations.

Schema mirrors what LIBERO/robomimic loaders actually read, confirmed against the
official LIBERO repo's own reading code (``scripts/get_dataset_info.py``,
``libero/libero/utils/dataset_utils.py``) and OpenVLA's
``experiments/robot/libero/regenerate_libero_dataset.py`` (obs key names, 256x256
image convention)::

    <task_name>_demo.hdf5
      data/                         (attrs: env_args=json str, problem_info=json str)
        demo_0/                     (attrs: num_samples=int)
          obs/
            agentview_rgb           (T, H, W, 3) uint8
            eye_in_hand_rgb         (T, H, W, 3) uint8
            joint_states            (T, 7) float32
            gripper_states          (T, 1) float32   -- continuous width, 0=open..1=closed
            ee_states               (T, 6) float32   -- xyz + axis-angle
            ee_pos                  (T, 3) float32
            ee_ori                  (T, 3) float32   -- axis-angle
          actions                   (T, 7) float32   -- normalized [-1, 1]
          rewards                   (T,) float32
          dones                     (T,) float32

One HDF5 file per task/language-instruction, one ``demo_N`` group per episode,
matching the official per-task file layout (e.g.
``turn_on_the_stove_demo.hdf5``).

Deliberately dropped vs. the simulator-generated original: ``states`` and
``model_file`` (full MuJoCo sim state / scene XML -- meaningless for a real
robot) and a genuine ``env_args`` (there is no BDDL scene/controller config to
report). ``env_args``/``problem_info`` are still written as JSON so
``get_dataset_info.py``-style readers do not KeyError, but ``env_args`` is a
real-robot stub, not simulator-replayable metadata.

ACTION SPACE -- read this before trusting a trained policy
------------------------------------------------------------
GELLO teleop here drives the follower in **joint space** (the leader mirrors
the follower's joints 1:1); there is no native commanded end-effector delta.
To stay drop-in compatible with LIBERO-format consumers (which assume a
robosuite ``OSC_POSE`` controller), ``actions`` is reconstructed *after the
fact* from the realized Cartesian trajectory: at frame ``t`` it is the
world-frame delta pose that carried ``ee_pos_quat[t] -> ee_pos_quat[t+1]``,
normalized by the OSC_POSE defaults (``output_max`` = 0.05 m / 0.5 rad),
clipped to [-1, 1]. Gripper: -1=open / +1=close (robosuite Panda convention).
Frame convention and gripper sign are taken from robosuite's documented
defaults, not verified byte-for-byte against an official demo file (repeated
attempts to stream one in this sandbox hit transient network failures --
before training anything on this data, sanity-check a few saved episodes by
eye: gripper sign flips exactly when the operator's trigger did, and replaying
cumulative ``actions`` roughly reproduces ``ee_states``).

Realized-trajectory actions lose the operator's force intent wherever the
follower is in contact: the leader keeps commanding *through* the obstacle
while the realized pose barely moves, so a realized delta goes to ~zero
exactly where the demonstration is pressing. They also break closed-loop
execution outright -- see ``compute_joint_absolute_action`` for the measured
consequences (backward regression at every replan, 1.3-2.0x slowdown). The
``joint_absolute`` space therefore records the **leader's command**, matching
ACT/ALOHA. To keep that intent recoverable for the other spaces too,
every episode ALSO stores the raw teleop command stream (independent of the
selected action space; see ``save_episode``)::

    obs/commanded_joint_states    (T, 7) float32  -- GELLO leader joints (rad),
                                                     the command sent at frame t
    obs/commanded_gripper_states  (T, 1) float32  -- commanded gripper, 0=open..1=closed

``scripts/derive_commanded_ee_actions.py`` turns these into commanded EE
delta actions (``actions_ee`` / ``actions_world_cmd``) offline via FR3
forward kinematics -- no extra dependency in the collection loop.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Any, Optional

import h5py
import numpy as np

from gello.dataset_schema import (
    ACTION_SPACE_EE_ABSOLUTE,
    ACTION_SPACE_EE_DELTA,
    ACTION_SPACE_JOINT_ABSOLUTE,
    ACTION_SPACE_JOINT_DELTA,
    ACTION_SPACE_LABELS,
    DatasetSchemaConfig,
)
from gello.station import load_station

# robosuite OSC_POSE defaults (position/orientation controllers), see
# robosuite.controllers.parts.arm.osc.OperationalSpaceController.
ACTION_POS_MAX = 0.05  # m per control step
ACTION_ROT_MAX = 0.5  # rad per control step (axis-angle vector component)

IMAGE_SIZE = 256  # matches OpenVLA's LIBERO regeneration convention


def _quat_to_axis_angle(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    """Scalar-last quaternion -> axis*angle (rad), matching franka_fr3's convention."""
    n = np.sqrt(qx * qx + qy * qy + qz * qz)
    if n < 1e-9:
        return np.zeros(3)
    angle = 2.0 * np.arctan2(n, qw)
    if angle > np.pi:
        angle -= 2 * np.pi
    axis = np.array([qx, qy, qz]) / n
    return axis * angle


def _quat_mul(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """Scalar-last (x, y, z, w) quaternion product q1 * q2."""
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return np.array(
        [
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        ]
    )


def _quat_conj(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    return np.array([-x, -y, -z, w])


def compute_delta_action(
    ee_pos_quat_curr: np.ndarray,
    ee_pos_quat_next: np.ndarray,
    gripper_closed: bool,
) -> np.ndarray:
    """World-frame delta pose from ``curr`` to ``next``, OSC_POSE-normalized.

    Args:
        ee_pos_quat_curr: (7,) [x, y, z, qx, qy, qz, qw] at frame t.
        ee_pos_quat_next: (7,) same layout at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (7,) float32, each component clipped to [-1, 1]:
        (dx, dy, dz, d_axis_angle_x, d_axis_angle_y, d_axis_angle_z, gripper).
    """
    p0, p1 = ee_pos_quat_curr[:3], ee_pos_quat_next[:3]
    q0, q1 = ee_pos_quat_curr[3:7], ee_pos_quat_next[3:7]

    dpos = (p1 - p0) / ACTION_POS_MAX
    # relative rotation in the world frame: q_rel = q1 * inverse(q0)
    q_rel = _quat_mul(q1, _quat_conj(q0))
    if q_rel[3] < 0:  # shortest-path: keep positive scalar part
        q_rel = -q_rel
    drot = _quat_to_axis_angle(*q_rel) / ACTION_ROT_MAX

    gripper = 1.0 if gripper_closed else -1.0
    action = np.concatenate([dpos, drot, [gripper]]).astype(np.float32)
    return np.clip(action, -1.0, 1.0)


def compute_joint_delta_action(
    q_curr: np.ndarray, q_next: np.ndarray, gripper_closed: bool
) -> np.ndarray:
    """Raw joint-space delta (rad) from ``curr`` to ``next``, unnormalized.

    Unlike :func:`compute_delta_action` there is no OSC_POSE-style external
    convention to match here -- GELLO already drives the follower in joint
    space (see this module's docstring), so this is just the realized
    per-step joint motion, stored as-is instead of clipped/normalized to
    [-1, 1].

    Args:
        q_curr: (7,) measured joint positions (rad) at frame t.
        q_next: (7,) same at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (8,) float32: (d_joint1..d_joint7, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    dq = (np.asarray(q_next, dtype=np.float64) - np.asarray(q_curr, dtype=np.float64)).astype(
        np.float32
    )
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([dq, [gripper]]).astype(np.float32)


def compute_ee_absolute_action(
    ee_pos_quat_next: np.ndarray, gripper_closed: bool
) -> np.ndarray:
    """Absolute world-frame EE pose at frame t+1 -- NOT a delta.

    Position in meters, orientation as axis-angle (rad) -- the same
    convention ``obs/ee_states`` already uses, NOT OSC_POSE's
    normalized-delta convention that :func:`compute_delta_action` matches
    (there is no controller-output-range to normalize against here).

    Args:
        ee_pos_quat_next: (7,) [x, y, z, qx, qy, qz, qw] at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (7,) float32: (x, y, z, ax, ay, az, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    pos = np.asarray(ee_pos_quat_next[:3], dtype=np.float64)
    axis_angle = _quat_to_axis_angle(*ee_pos_quat_next[3:7])
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([pos, axis_angle, [gripper]]).astype(np.float32)


def compute_joint_absolute_action(q_cmd: np.ndarray, gripper_closed: bool) -> np.ndarray:
    """The GELLO leader's absolute joint target (rad) at frame t -- NOT a delta.

    This is the ACT/ALOHA convention: observation is the follower's *measured*
    joints, action is the leader's *command*, and the force the operator is
    applying lives implicitly in the difference between them (Zhao et al.,
    "Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware").

    Do NOT substitute the follower's realized ``joint_states[t+1]`` here, even
    though it looks like the same quantity. The follower runs behind a
    critically-damped reference filter and trails the leader by ~4 ticks; the
    lead the leader needed to drag the arm forward (up to 0.28 rad measured) is
    absent from the realized trajectory. A policy trained on realized values
    can only ever emit "one tick past where the arm already is", so it cannot
    command a catch-up: its own tracking lag re-anchors at every replan, the
    target regresses behind the previous chunk's frontier, and the motion
    stutters backwards at the replan period while running 1.3-2.0x slow. See
    ``knu-physical-ai/fr3-action-space-case-study`` on the Hub for the
    measurements behind this.

    Args:
        q_cmd: (7,) GELLO leader commanded joint positions (rad) at frame t.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (8,) float32: (joint1..joint7, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    q = np.asarray(q_cmd, dtype=np.float32)
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([q, [gripper]]).astype(np.float32)


_ACTION_COLUMNS = {
    ACTION_SPACE_EE_DELTA: ["dx", "dy", "dz", "d_axis_x", "d_axis_y", "d_axis_z"],
    ACTION_SPACE_EE_ABSOLUTE: ["x", "y", "z", "axis_x", "axis_y", "axis_z"],
    ACTION_SPACE_JOINT_DELTA: [f"d_joint{i}" for i in range(1, 8)],
    # obs/joint_states is exposed to LeRobotDataset as observation.state dims
    # named "joint1.pos".."joint7.pos" (see convert_libero_to_lerobot.py's
    # _build_features) -- this action space's targets are the same physical
    # quantity (an absolute joint position, not a delta), so its column names
    # must match those, or a state<->action dim pairs by name downstream
    # (e.g. a policy computing its own state-to-action correspondence) sees a
    # false mismatch.
    ACTION_SPACE_JOINT_ABSOLUTE: [f"joint{i}.pos" for i in range(1, 8)],
}


def action_column_names(action_space: str) -> list[str]:
    """Non-gripper column names for ``action_space``, e.g. for building a
    LeRobotDataset ``features`` dict (see scripts/convert_libero_to_lerobot.py)
    without hardcoding a copy of :data:`_ACTION_COLUMNS` elsewhere."""
    return list(_ACTION_COLUMNS[action_space])


def resolved_action_column_names(schema: DatasetSchemaConfig) -> list[str]:
    """The exact action column names ``save_episode`` will write to the
    ``action_column_names`` attr, and that ``describe_schema``/
    ``_build_features`` (scripts/convert_libero_to_lerobot.py) show/use --
    the built-in per-``action_space`` names with any operator overrides
    (``schema.action_column_name_overrides``, keyed by the built-in default
    name) applied, plus the gripper column's name if included. Does not
    include the human-readable "(0=open/1=close...)" convention note
    :func:`describe_schema`/:func:`describe_episode` append for display.
    """
    overrides = schema.action_column_name_overrides
    cols = [overrides.get(c, c) for c in _ACTION_COLUMNS[schema.action_space]]
    if schema.action_include_gripper:
        cols.append(overrides.get("gripper.pos", "gripper.pos"))
    return cols


def describe_schema(cfg: DatasetSchemaConfig) -> str:
    """Human-readable summary of the exact ``obs``/``actions`` structure
    ``LiberoTaskWriter.save_episode`` would write for ``cfg``.

    Pure description, no robot/episode needed -- backs the GUI's "구조
    미리보기" so an operator can check a custom schema before committing to
    it (and before ever connecting).
    """
    schema = cfg
    cols = resolved_action_column_names(schema)
    # actions 도 obs/ 와 같은 "헤더 + 들여쓴 항목" 형식으로 쓴다. 예전에는 이
    # 블록만 `= [col, col, ...]` 한 줄이라, 같은 화면 안에서 actions 만 다른
    # 문법으로 읽혔다.
    arm_cols = [c for c in cols if not c.startswith("gripper")]
    lines = [
        f"actions: (T, {len(cols)}) float32  -- "
        f"{ACTION_SPACE_LABELS.get(schema.action_space, schema.action_space)}",
    ]
    if arm_cols:
        lines.append(f"  {arm_cols[0]} .. {arm_cols[-1]}: (rad) 관절 절대각"
                     if len(arm_cols) > 1 else f"  {arm_cols[0]}: (rad)")
    if schema.action_include_gripper:
        note = "0=open / 1=close" if schema.gripper_action_match_obs else "-1=open / +1=close"
        lines.append(f"  {cols[-1]}: {note}")
    lines += ["", "obs/:"]

    obs_rows = []
    if schema.image_size is not None:
        img_dims, img_note = f"{schema.image_size}, {schema.image_size}", ""
    else:
        img_dims, img_note = "H, W", "  -- 원본 해상도, 리사이즈 없음"
    if schema.save_agentview_rgb:
        obs_rows.append(("agentview_rgb", f"(T, {img_dims}, 3) uint8{img_note}"))
    if schema.save_eye_in_hand_rgb:
        obs_rows.append(("eye_in_hand_rgb", f"(T, {img_dims}, 3) uint8{img_note}"))
    if schema.save_joint_states:
        obs_rows.append(("joint_states", "(T, 7) float32"))
    if schema.save_gripper_states:
        obs_rows.append(("gripper_states", "(T, 1) float32  -- continuous, 0=open..1=closed"))
    if schema.save_ee_states:
        obs_rows.append(("ee_states", "(T, 6) float32  -- pos(3) + axis-angle(3)"))
    if schema.save_ee_pos:
        obs_rows.append(("ee_pos", "(T, 3) float32"))
    if schema.save_ee_ori:
        obs_rows.append(("ee_ori", "(T, 3) float32  -- axis-angle"))
    if schema.save_joint_velocities:
        obs_rows.append(("joint_velocities", "(T, 7) float32"))
    if schema.save_agentview_depth:
        obs_rows.append(("agentview_depth", "(T, H, W) uint16 mm · 원본 해상도 · lzf"))
    if schema.save_eye_in_hand_depth:
        obs_rows.append(("eye_in_hand_depth", "(T, H, W) uint16 mm · 원본 해상도 · lzf"))
    if schema.save_timestamp:
        obs_rows.append(("timestamp", "(T,) float64  -- wall-clock seconds"))

    # Not schema-gated: the GUI worker always supplies the teleop command
    # stream, so every episode it records carries these (see save_episode).
    obs_rows.append(("commanded_joint_states", "(T, 7) float32  -- GELLO leader command (rad)"))
    obs_rows.append(("commanded_gripper_states", "(T, 1) float32  -- commanded, 0=open..1=closed"))

    if not obs_rows:
        lines.append("  (선택된 obs 필드 없음)")
    else:
        lines.extend(f"  {name}: {shape}" for name, shape in obs_rows)

    lines += [
        "",
        "rewards: (T,) float32   -- 항상 0 (실기에는 시뮬레이터 보상이 없음)",
        "dones: (T,) float32   -- 마지막 프레임만 1",
    ]
    return "\n".join(lines)


_OBS_KEY_NOTES = {
    "gripper_states": "  -- continuous, 0=open..1=closed",
    "ee_states": "  -- pos(3) + axis-angle(3)",
    "ee_ori": "  -- axis-angle",
    "timestamp": "  -- wall-clock seconds",
    "commanded_joint_states": "  -- GELLO leader command (rad)",
    "commanded_gripper_states": "  -- commanded, 0=open..1=closed",
}


def describe_episode(grp: Any) -> str:
    """Human-readable summary of an ALREADY-SAVED ``demo_N`` group's actual
    on-disk structure (an ``h5py.Group``).

    Unlike :func:`describe_schema` (which describes what a
    ``DatasetSchemaConfig`` *would* write), this reads real attrs/array
    shapes -- older episodes in a ``--resume``'d file may have been written
    under a different schema than whatever's currently configured (see
    ``LiberoTaskWriter.save_episode``'s per-episode ``action_space`` attr),
    so only the file itself is a ground truth for what a given episode
    actually contains.
    """
    obs = grp["obs"]
    action_space = grp.attrs.get("action_space", ACTION_SPACE_EE_DELTA)
    base_cols = action_column_names(action_space)
    actions_shape = tuple(grp["actions"].shape)
    has_gripper = actions_shape[1] == len(base_cols) + 1
    # Old episodes predate this attr (and the option) -- they were always
    # written -1/+1, so that's the correct fallback, not "01".
    gripper_convention = grp.attrs.get("gripper_action_convention", "pm1")
    gripper_note = "0=open/1=close, matches obs" if gripper_convention == "01" else "-1=open/+1=close"
    # Old episodes predate action_column_names too -- fall back to the
    # built-in names (no custom overrides possible for those anyway).
    raw = grp.attrs.get("action_column_names")
    cols = json.loads(raw) if raw else base_cols + (["gripper.pos"] if has_gripper else [])
    if has_gripper:
        cols[-1] = f"{cols[-1]} ({gripper_note})"

    lines = [
        f"Action space: {ACTION_SPACE_LABELS.get(action_space, action_space)}",
        f"  actions: {actions_shape} float32 = [{', '.join(cols)}]",
        "",
        "obs/:",
    ]
    for key in sorted(obs.keys()):
        ds = obs[key]
        note = _OBS_KEY_NOTES.get(key, "")
        lines.append(f"  {key}: {tuple(ds.shape)} {ds.dtype}{note}")

    success = grp.attrs.get("success")
    lines += [
        "",
        f"rewards: {tuple(grp['rewards'].shape)} float32",
        f"dones: {tuple(grp['dones'].shape)} float32",
        f"num_samples: {int(grp.attrs.get('num_samples', actions_shape[0]))}",
        f"success: {None if success is None else bool(success)}",
    ]
    # 어디서, 어떤 프레이밍으로 찍혔는지. 파일에는 계속 들어 있었는데 이 요약이
    # 출력하지 않아서, 구조 확인 창만 보면 없는 것처럼 보였다. 크롭은 변환 때
    # 실제로 재현되는 값이라 눈으로 확인할 수 있어야 한다.
    station = grp.attrs.get("station")
    if station is not None:
        lines.append(f"station: {station}")
    raw_crop = grp.attrs.get("crop_params")
    if raw_crop:
        try:
            cp = json.loads(raw_crop)
            detail = "  ".join(
                f"{role}(zoom {v.get('zoom', 1.0)}, x {v.get('x', 0):+d}, y {v.get('y', 0):+d})"
                for role, v in sorted(cp.items()))
            lines.append(f"crop_params: {detail}")
        except (ValueError, TypeError, AttributeError):
            lines.append(f"crop_params: {raw_crop}")
    return "\n".join(lines)


def schema_from_episode(grp: Any) -> DatasetSchemaConfig:
    """Reconstructs the ``DatasetSchemaConfig`` that (as best as can be
    recovered from what's actually on disk) matches an already-saved
    ``demo_N`` group, so continuing a file cannot silently start recording
    under a different schema than what is already in it. Like
    :func:`describe_episode`, reads real attrs/shapes, not whatever the GUI
    happens to be configured with right now.

    Currently imported but never called: the wizard GUI prefilled the schema
    from a Task 이름 *dropdown* selection, and the workspace UI that replaced
    it (62cad92) takes the task as free text with no such hook. Kept because
    the mismatch it guards against is still possible -- wire it to whatever
    signals "operator is resuming this file" before that bites.
    """
    obs = grp["obs"]
    obs_keys = set(obs.keys())
    action_space = grp.attrs.get("action_space", ACTION_SPACE_EE_DELTA)
    base_cols = action_column_names(action_space)
    actions_shape = tuple(grp["actions"].shape)
    has_gripper = actions_shape[1] == len(base_cols) + 1
    gripper_convention = grp.attrs.get("gripper_action_convention", "pm1")

    image_size = None
    for key in ("agentview_rgb", "eye_in_hand_rgb"):
        if key in obs:
            h, w = obs[key].shape[1:3]
            image_size = int(h) if h == w else None
            break

    overrides: dict[str, str] = {}
    raw_names = grp.attrs.get("action_column_names")
    if raw_names:
        actual = json.loads(raw_names)
        default = base_cols + (["gripper.pos"] if has_gripper else [])
        overrides = {d: a for d, a in zip(default, actual) if d != a}

    return DatasetSchemaConfig(
        action_space=action_space,
        action_include_gripper=has_gripper,
        gripper_action_match_obs=(gripper_convention == "01"),
        image_size=image_size,
        save_agentview_rgb="agentview_rgb" in obs_keys,
        save_eye_in_hand_rgb="eye_in_hand_rgb" in obs_keys,
        save_joint_states="joint_states" in obs_keys,
        save_gripper_states="gripper_states" in obs_keys,
        save_ee_states="ee_states" in obs_keys,
        save_ee_pos="ee_pos" in obs_keys,
        save_ee_ori="ee_ori" in obs_keys,
        save_joint_velocities="joint_velocities" in obs_keys,
        save_timestamp="timestamp" in obs_keys,
        save_agentview_depth="agentview_depth" in obs_keys,
        save_eye_in_hand_depth="eye_in_hand_depth" in obs_keys,
        action_column_name_overrides=overrides,
    )


def renumber_episodes(data: Any) -> None:
    """Closes gaps left by deleted episodes so the remaining ones become a
    contiguous ``demo_0..demo_{n-1}`` run again (``data`` is the file's
    top-level ``data`` group). Renames on disk -- not just a display-only
    renumbering -- so LeRobot conversion, the dataset explorer, and
    anything else that reads names straight off disk all agree, at the
    cost of reversing ``save_episode``'s original "monotonic, never
    reused" numbering (see its ``next_demo_idx`` comment): resets
    ``next_demo_idx`` to the post-renumber count here, so the next
    ``save_episode`` continues right after the last renumbered episode
    instead of leaving a gap again immediately.

    Safe to call with no gaps (every rename below is then a no-op skip).
    Always renames in ascending order of CURRENT index, which is
    collision-free without a temporary name / two-pass shuffle: the k-th
    (0-indexed) episode in sorted order can only have current index >= k
    (deleting can only ever pull indices down, never up), so demo_k can
    never already belong to a later, not-yet-processed episode when it's
    that episode's turn.
    """
    names = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
    for new_idx, name in enumerate(names):
        new_name = f"demo_{new_idx}"
        if name != new_name:
            data.move(name, new_name)
    data.attrs["next_demo_idx"] = len(names)


# 손목 카메라(D405)의 정사각 크롭을 오른쪽으로 미는 양. 640x480 원본 기준 px.
#
# D405 의 RGB 는 좌측 이미저에서 나온다(베이스라인 18.2mm). 모듈 중심이 그리퍼
# 축에 정렬돼 있으므로 광축은 축보다 9.1mm 왼쪽이고, 그리퍼 축은 화면 중앙보다
# 오른쪽에 찍힌다. 수집된 247개 첫 프레임에서 손가락 패드 두 개의 중점을 재면
# 중앙에서 +16.6px(256 기준, σ1.9) = 640 기준 +31px. 물리 검산: fx·d/Z =
# 337 × 9.1mm / 31px → Z ≈ 9.8cm, 손끝 패드까지의 실거리와 일치한다.
#
# 시차 때문에 한 깊이에서만 정확히 가운데가 된다. 파지 평면(손끝 깊이)을
# 기준으로 잡았다 -- 조작 대상이 놓이는 깊이라서다. 더 먼 배경(탁자)은 이보다
# 덜 밀리지만(예: 25cm 에서 ~12px) 조작에는 영향이 없다.
#
# 이 값은 **역사적 상수**이지 현재 설정이 아니다. attrs["crop_params"] 가 없는
# 옛 파일이 실제로 찍힌 값이라, 변환할 때의 fallback 으로만 쓴다
# (scripts/convert_libero_to_lerobot.py). 스테이션 설정을 고쳐도 여기는 31 로
# 남아야 옛 파일의 프레이밍이 재현된다. 지금 수집에 쓰이는 값은
# default_crop_params() 를 보라.
EYE_IN_HAND_CROP_X_SHIFT = 31


def default_crop_params() -> dict:
    """Per-camera square-crop alignment, adjusted in the GUI's Layout page and
    stamped into each episode's attrs (``crop_params``) so the conversion can
    reproduce exactly the framing the operator saw.

    ``x``/``y`` move the crop window (px at 640 source width; +x right,
    +y down). ``zoom`` divides the crop side (1.0 = full square, 2.0 = half).

    값은 스테이션 설정(configs/stations/<이름>.yaml 의 ``crop``)에서 온다 --
    카메라가 어디에 어떻게 달렸는지의 결과라 스테이션마다 다르다. 여기서
    주는 것은 초기값이고, GUI 에서 조정한 값은 crop_params.json 이 이긴다."""
    return load_station().crop_params()


# dataset_schema.json / recent_inputs.json 과 같은 자리. GUI 재시작 간 유지용
# 환경설정이고, 에피소드의 진실은 각 demo attrs["crop_params"] 쪽이다.
#
# **스테이션마다 따로** 둔다. 크롭은 카메라가 어디에 어떻게 달렸는지의 결과라
# 스테이션이 바뀌면 통째로 달라진다. 예전에는 전역 파일 하나였고, 그 탓에
# 스테이션을 바꿔도 이전 스테이션에서 맞춘 값이 그대로 이겨서 -- yaml 에 새 값을
# 적어 두어도 -- 조용히 옛 프레이밍으로 찍혔다.
CROP_PARAMS_DIR = Path.home() / "libero_gui_logs"
# 스테이션이 하나뿐이던 시절의 파일. 기본 스테이션에 한해 한 번 물려받는다.
LEGACY_CROP_PARAMS_PATH = CROP_PARAMS_DIR / "crop_params.json"


def crop_params_path(station: str | None = None) -> Path:
    """이 스테이션의 크롭 설정 파일 경로."""
    name = station or load_station().name
    return CROP_PARAMS_DIR / f"crop_params.{name}.json"


def load_crop_params(path: Path | None = None, station: str | None = None) -> dict:
    """Never raises -- missing/corrupt file just means the station's defaults.

    ``path`` 를 직접 주면 그것만 읽는다(테스트용). 아니면 스테이션별 파일을
    읽고, 그게 없고 기본 스테이션이면 옛 전역 파일을 한 번 물려받는다.
    """
    from gello.station import DEFAULT_STATION

    base = default_crop_params()
    if path is None:
        name = station or load_station().name
        path = crop_params_path(name)
        # 다른 스테이션은 옛 파일을 물려받지 않는다 -- 물려받으면 그게 바로
        # 이 분리로 없애려는 버그다.
        if not Path(path).exists() and name == DEFAULT_STATION:
            path = LEGACY_CROP_PARAMS_PATH
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for role, defaults in base.items():
            got = data.get(role)
            if not isinstance(got, dict):
                continue
            for key, dflt in defaults.items():
                v = got.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    defaults[key] = type(dflt)(v)
    except (OSError, ValueError, TypeError):
        pass
    return base


def save_crop_params(params: dict, path: Path | None = None,
                     station: str | None = None) -> None:
    try:
        p = Path(path) if path is not None else crop_params_path(station)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(params, indent=1), encoding="utf-8")
    except OSError:
        pass


def square_crop(img: np.ndarray, zoom: float = 1.0, x_shift: int = 0,
                y_shift: int = 0) -> np.ndarray:
    """Square window into ``img``: shifted, optionally zoomed, always clamped.

    Shifts are in pixels *at 640 source width* (scaled for other widths), so
    the same numbers mean the same framing at every capture resolution."""
    h, w = img.shape[:2]
    s = min(h, w)
    if zoom > 1.0:
        s = max(16, round(s / zoom))
    sc = w / 640
    x0 = (w - s) // 2 + round(x_shift * sc)
    y0 = (h - s) // 2 + round(y_shift * sc)
    x0 = min(max(x0, 0), w - s)
    y0 = min(max(y0, 0), h - s)
    return img[y0 : y0 + s, x0 : x0 + s]


def resize_rgb(img: np.ndarray, size: int = IMAGE_SIZE, zoom: float = 1.0,
               x_shift: int = 0, y_shift: int = 0) -> np.ndarray:
    """Resize an (H, W, 3) uint8 RGB image to (size, size, 3), square-cropped
    first (see ``square_crop``). Defaults keep the historical center crop."""
    import cv2

    cropped = square_crop(img, zoom=zoom, x_shift=x_shift, y_shift=y_shift)
    # INTER_AREA 는 축소용이다. zoom 이 크면 확대가 되는데 그때는 LINEAR.
    interp = cv2.INTER_AREA if cropped.shape[0] >= size else cv2.INTER_LINEAR
    return cv2.resize(cropped, (size, size), interpolation=interp)


class LiberoEpisodeBuffer:
    """Accumulates one episode's frames in memory before it is committed or discarded.

    ``joint_states``/``ee_pos_quat``/``gripper_closed`` are always buffered
    regardless of ``schema`` -- action computation needs them no matter which
    action space is selected, and no matter which obs fields end up written
    (see LiberoTaskWriter.save_episode). Only the genuinely optional/costly
    fields (images, joint velocities, timestamps) are gated on ``schema``.
    The commanded_* fields are buffered whenever the caller passes them
    (the GUI worker always does) and likewise bypass the schema.
    """

    def __init__(self, schema: Optional[DatasetSchemaConfig] = None,
                 crop_params: Optional[dict] = None) -> None:
        self.schema = schema or DatasetSchemaConfig()
        self.crop_params = crop_params or default_crop_params()
        self._reset_lists()

    def _reset_lists(self) -> None:
        self.agentview_rgb: list[np.ndarray] = []
        self.eye_in_hand_rgb: list[np.ndarray] = []
        self.joint_states: list[np.ndarray] = []
        self.gripper_states: list[np.ndarray] = []
        self.ee_pos_quat: list[np.ndarray] = []
        self.gripper_closed: list[bool] = []
        self.joint_velocities: list[np.ndarray] = []
        self.timestamps: list[float] = []
        self.agentview_depth: list[np.ndarray] = []
        self.eye_in_hand_depth: list[np.ndarray] = []
        self.commanded_joint_positions: list[np.ndarray] = []
        self.commanded_gripper: list[float] = []

    def __len__(self) -> int:
        return len(self.joint_states)

    def add_frame(
        self,
        agentview_rgb: np.ndarray,
        eye_in_hand_rgb: np.ndarray,
        joint_positions: np.ndarray,
        gripper_position: float,
        ee_pos_quat: np.ndarray,
        gripper_closed: bool,
        joint_velocities: Optional[np.ndarray] = None,
        timestamp: Optional[float] = None,
        commanded_joint_positions: Optional[np.ndarray] = None,
        commanded_gripper: Optional[float] = None,
        agentview_depth: Optional[np.ndarray] = None,
        eye_in_hand_depth: Optional[np.ndarray] = None,
    ) -> None:
        self.joint_states.append(np.asarray(joint_positions, dtype=np.float32))
        self.gripper_states.append(np.array([gripper_position], dtype=np.float32))
        self.ee_pos_quat.append(np.asarray(ee_pos_quat, dtype=np.float64))
        self.gripper_closed.append(bool(gripper_closed))
        if commanded_joint_positions is not None:
            self.commanded_joint_positions.append(
                np.asarray(commanded_joint_positions, dtype=np.float32)
            )
        if commanded_gripper is not None:
            self.commanded_gripper.append(float(commanded_gripper))
        if self.schema.save_agentview_rgb:
            self.agentview_rgb.append(self._process_image(agentview_rgb))
        if self.schema.save_eye_in_hand_rgb:
            self.eye_in_hand_rgb.append(self._process_image(
                eye_in_hand_rgb, role="wrist"))
        if self.schema.save_joint_velocities and joint_velocities is not None:
            self.joint_velocities.append(np.asarray(joint_velocities, dtype=np.float32))
        if self.schema.save_timestamp and timestamp is not None:
            self.timestamps.append(float(timestamp))
        # depth 는 crop/resize 없이 원본 해상도 그대로 (#17: 원본 보관소 원칙,
        # D455 는 RGB-depth 픽셀 대응이 원래 안 맞아 RGB 크롭을 따라가면
        # 오히려 거짓 정렬이 된다). .copy() 는 RGB 와 같은 이유 -- 드라이버
        # 프레임 풀에 대한 뷰를 그대로 쌓으면 몇 프레임 뒤 덮어써진다.
        if self.schema.save_agentview_depth and agentview_depth is not None:
            self.agentview_depth.append(
                np.asarray(agentview_depth, dtype=np.uint16).copy())
        if self.schema.save_eye_in_hand_depth and eye_in_hand_depth is not None:
            self.eye_in_hand_depth.append(
                np.asarray(eye_in_hand_depth, dtype=np.uint16).copy())

    def _process_image(self, img: np.ndarray, role: str = "agent") -> np.ndarray:
        """Returns a frame this buffer owns, resized if the schema asks for it.

        The copy is the point, and it is deliberate rather than a side effect.
        Camera drivers hand out a *view* into a small pool of frame buffers
        they recycle (librealsense via lerobot's RealSenseCamera does, and its
        _postprocess_image only copies when a colour conversion or rotation is
        configured -- this collector requests neither). Appending that view
        stores a window onto memory the driver overwrites a few frames later,
        so a 200-frame episode ends up holding the same handful of images
        repeated, at the right shape and count and with no error anywhere.

        This used to be safe only by accident: cv2.resize allocates its output,
        so the resize path copied for free while image_size=None returned the
        view untouched. Copying here instead means the guarantee no longer
        depends on which branch runs.

        ``.copy()`` and not ``np.ascontiguousarray``: the latter is a no-op
        when the input already is contiguous, which a driver frame buffer is
        -- it would have left the aliasing exactly as it was.
        """
        if self.schema.image_size is None:
            return img.copy()
        p = self.crop_params.get(role, {})
        return resize_rgb(img, size=self.schema.image_size,
                          zoom=p.get("zoom", 1.0), x_shift=p.get("x", 0),
                          y_shift=p.get("y", 0))

    def clear(self) -> None:
        self._reset_lists()


def _mark_close_on_exec(f: h5py.File) -> None:
    """Without this, a task .hdf5 opened here stays locked forever by any
    child process spawned (e.g. via QProcess) while the file is open --
    HDF5's C library doesn't set FD_CLOEXEC on the fd it opens, unlike
    Python's own open()/io, so e.g. restarting the robot node mid-session
    (experiments/launch_nodes.py, spawned from the GUI) silently inherits
    and keeps holding the lock via fork+exec even after this writer's own
    session ends and closes its copy -- the file then can't be read/
    converted by anything until that unrelated child process is killed.
    Best-effort: not every HDF5 driver's fd is retrievable this way, so a
    failure here is not fatal.
    """
    try:
        fd = f.id.get_vfd_handle()
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
    except Exception:  # noqa: BLE001
        pass


def write_episode_payload(
    grp: h5py.Group,
    buf: LiberoEpisodeBuffer,
    schema: DatasetSchemaConfig,
    success: Optional[bool] = None,
) -> int:
    """One episode's shared on-disk payload: the ``actions``/``rewards``/
    ``dones`` datasets, the ``obs/`` group, and the per-episode provenance
    attrs (``num_samples``, ``action_space``, ``crop_params``, ``station``,
    ...).

    legacy 포맷(``<task>_demo.hdf5`` 의 ``demo_N``)과 scene 포맷
    (``scene_XXX.hdf5`` 의 ``episode_NNN``, gello/scene_format.py)이 이 함수를
    공유한다 -- 에피소드 안쪽 구조가 같아야 변환기가 두 포맷을 같은 코드로
    읽는다. 여기 없는 attrs(instruction, episode_uid 등)는 각 포맷의 writer 가
    이 함수 호출 뒤에 얹는다.

    Caller owns group creation, file flush and buffer lifetime. Returns the
    frame count ``n`` (the caller rejects buffers with fewer than 2 frames
    before creating the group).
    """
    n = len(buf)
    if schema.action_space == ACTION_SPACE_JOINT_DELTA:
        q = np.stack(buf.joint_states)  # (n, 7)
        actions = np.zeros((n, 8), dtype=np.float32)
        for t in range(n - 1):
            actions[t] = compute_joint_delta_action(
                q[t], q[t + 1], buf.gripper_closed[t]
            )
        # Terminal frame: no further motion recorded; hold gripper state.
        actions[n - 1, :7] = 0.0
        actions[n - 1, 7] = 1.0 if buf.gripper_closed[-1] else -1.0
    elif schema.action_space == ACTION_SPACE_JOINT_ABSOLUTE:
        # The leader's command, verbatim -- see compute_joint_absolute_action
        # for why the follower's realized joint_states must not be used here.
        # No terminal-frame special case is needed: unlike a realized-next-
        # state target, a command exists at every frame including the last.
        if len(buf.commanded_joint_positions) != n:
            raise ValueError(
                "action_space='joint_absolute' needs commanded_joint_positions "
                f"on every frame (got {len(buf.commanded_joint_positions)} of {n}). "
                "The GUI worker supplies them; a caller that does not must use "
                "a different action space."
            )
        q_cmd = np.stack(buf.commanded_joint_positions)  # (n, 7)
        actions = np.zeros((n, 8), dtype=np.float32)
        for t in range(n):
            actions[t] = compute_joint_absolute_action(
                q_cmd[t], buf.gripper_closed[t]
            )
    elif schema.action_space == ACTION_SPACE_EE_ABSOLUTE:
        ee = np.stack(buf.ee_pos_quat)  # (n, 7)
        actions = np.zeros((n, 7), dtype=np.float32)
        for t in range(n - 1):
            actions[t] = compute_ee_absolute_action(
                ee[t + 1], buf.gripper_closed[t]
            )
        # Terminal frame: no further target recorded; hold current pose.
        actions[n - 1, :3] = ee[n - 1, :3]
        actions[n - 1, 3:6] = _quat_to_axis_angle(*ee[n - 1, 3:7])
        actions[n - 1, 6] = 1.0 if buf.gripper_closed[-1] else -1.0
    else:
        ee = np.stack(buf.ee_pos_quat)  # (n, 7)
        actions = np.zeros((n, 7), dtype=np.float32)
        for t in range(n - 1):
            actions[t] = compute_delta_action(
                ee[t], ee[t + 1], buf.gripper_closed[t]
            )
        actions[n - 1, :6] = 0.0
        actions[n - 1, 6] = 1.0 if buf.gripper_closed[-1] else -1.0

    # Every branch above always ends with gripper as the last column,
    # in -1=open/+1=close (robosuite Panda convention) -- remap to
    # 0=open/1=closed here in one place, matching obs/gripper_states'
    # convention, if the operator asked action to match obs.
    if schema.gripper_action_match_obs:
        actions[:, -1] = (actions[:, -1] + 1.0) / 2.0

    # ... then strip it here in one place rather than duplicating the
    # flag check in all four branches.
    if not schema.action_include_gripper:
        actions = actions[:, :-1]

    grp.attrs["num_samples"] = n
    if success is not None:
        grp.attrs["success"] = bool(success)
    # Per-episode provenance: which action space this demo's `actions`
    # was computed with, and which obs fields are actually present
    # (readers should not assume the full original LIBERO obs set --
    # --resume lets a file mix schemas episode-to-episode if the
    # operator changed the "사용자 지정" config between sessions).
    grp.attrs["action_space"] = schema.action_space
    grp.attrs["gripper_action_convention"] = "01" if schema.gripper_action_match_obs else "pm1"
    grp.attrs["action_column_names"] = json.dumps(resolved_action_column_names(schema))
    # 이 에피소드의 이미지에 (원본 저장이면 변환 시점에) 적용할/된 정사각
    # 크롭 정렬. buf 의 것을 쓴다 -- 백그라운드 저장 중 writer 쪽 값이
    # 바뀌어도 찍히는 값은 그 에피소드가 실제로 쓰던 것이어야 한다.
    grp.attrs["crop_params"] = json.dumps(buf.crop_params)
    # 어느 스테이션에서 찍었는지. 형식은 v0 에서 고정이라 코드 버전은 남기지
    # 않지만, 스테이션은 하드웨어가 바뀌면 같이 바뀐다 -- 카메라를 교체하거나
    # 두 번째 스테이션이 생기면 프레이밍이 갈리는 지점이 여기다.
    grp.attrs["station"] = load_station().name

    obs = grp.create_group("obs")
    if schema.save_agentview_rgb:
        obs.create_dataset(
            "agentview_rgb",
            data=np.stack(buf.agentview_rgb),
            compression="lzf",
        )
    if schema.save_eye_in_hand_rgb:
        obs.create_dataset(
            "eye_in_hand_rgb",
            data=np.stack(buf.eye_in_hand_rgb),
            compression="lzf",
        )
    if schema.save_joint_states:
        obs.create_dataset("joint_states", data=np.stack(buf.joint_states))
    if schema.save_gripper_states:
        obs.create_dataset(
            "gripper_states", data=np.stack(buf.gripper_states)
        )
    if schema.save_ee_states or schema.save_ee_pos or schema.save_ee_ori:
        ee = np.stack(buf.ee_pos_quat)  # (n, 7)
        ee_ori = np.stack([_quat_to_axis_angle(*q[3:7]) for q in ee]).astype(np.float32)
        ee_pos = ee[:, :3].astype(np.float32)
        if schema.save_ee_states:
            ee_states = np.concatenate([ee_pos, ee_ori], axis=1).astype(np.float32)
            obs.create_dataset("ee_states", data=ee_states)
        if schema.save_ee_pos:
            obs.create_dataset("ee_pos", data=ee_pos)
        if schema.save_ee_ori:
            obs.create_dataset("ee_ori", data=ee_ori)
    if schema.save_joint_velocities and buf.joint_velocities:
        obs.create_dataset(
            "joint_velocities", data=np.stack(buf.joint_velocities)
        )
    if schema.save_timestamp and buf.timestamps:
        obs.create_dataset(
            "timestamp", data=np.array(buf.timestamps, dtype=np.float64)
        )
    # 무손실 필수 (#17) -- JPEG 류 손실 압축은 depth 값을 파괴한다.
    if schema.save_agentview_depth and buf.agentview_depth:
        obs.create_dataset("agentview_depth",
                           data=np.stack(buf.agentview_depth),
                           compression="lzf")
    if schema.save_eye_in_hand_depth and buf.eye_in_hand_depth:
        obs.create_dataset("eye_in_hand_depth",
                           data=np.stack(buf.eye_in_hand_depth),
                           compression="lzf")
    # Raw teleop command stream -- written whenever the caller supplied it,
    # independent of the schema and of which action space `actions` used:
    # realized-trajectory actions zero out wherever the follower is blocked
    # by contact, and the command is the only record of what the operator
    # was actually asking for there. Tiny (7+1 floats/frame), so never
    # worth a schema toggle. See scripts/derive_commanded_ee_actions.py.
    if len(buf.commanded_joint_positions) == n:
        obs.create_dataset(
            "commanded_joint_states",
            data=np.stack(buf.commanded_joint_positions),
        )
    if len(buf.commanded_gripper) == n:
        obs.create_dataset(
            "commanded_gripper_states",
            data=np.array(buf.commanded_gripper, dtype=np.float32).reshape(-1, 1),
        )

    grp.create_dataset("actions", data=actions)
    grp.create_dataset("rewards", data=np.zeros(n, dtype=np.float32))
    dones = np.zeros(n, dtype=np.float32)
    dones[-1] = 1.0
    grp.create_dataset("dones", data=dones)
    return n


class NullTaskWriter:
    """A writer that records nothing -- for teleoperating without a dataset.

    Setting a scene up, checking a camera angle, or letting someone try the
    leader arm are all things done far more often than a recording session,
    and all of them used to require inventing a throwaway task name and then
    deleting the .hdf5 it left behind. Worse, that file lands in the data root
    next to the real ones, where the next repack or conversion picks it up.

    This stands in for LiberoTaskWriter so the worker's state machine (which
    touches the writer in a dozen places) needs no branching: the episode
    buffer still fills, so the pose gate, the frame counter and the live view
    all behave exactly as they do in a real session -- only the file is
    missing. Saving is accepted and dropped, which is the honest behaviour for
    a mode whose whole point is that nothing is kept.
    """

    def __init__(self, schema: Optional[DatasetSchemaConfig] = None) -> None:
        self.schema = schema or DatasetSchemaConfig()
        # Not None: the worker emits str(writer.path) on connect, and a literal
        # "None" in the GUI's file field reads as a bug rather than a mode.
        self.path = "(기록 안 함)"
        self._buffer = LiberoEpisodeBuffer(self.schema)

    def record_session_config(self, **kwargs: Any) -> None:
        pass

    @property
    def num_episodes(self) -> int:
        return 0

    def list_episodes(self) -> list[dict]:
        return []

    def delete_episode(self, name: str) -> None:
        pass

    def start_episode(self) -> None:
        self._buffer.clear()

    def add_frame(self, **kwargs: Any) -> None:
        self._buffer.add_frame(**kwargs)

    def discard_episode(self) -> None:
        self._buffer.clear()

    def detach_buffer(self) -> LiberoEpisodeBuffer:
        buf = self._buffer
        self._buffer = LiberoEpisodeBuffer(self.schema)
        return buf

    def save_episode(self, success: Optional[bool] = None) -> Optional[str]:
        return self.save_buffer(self.detach_buffer(), success=success)

    def save_buffer(self, buf: LiberoEpisodeBuffer, success: Optional[bool] = None) -> Optional[str]:
        buf.clear()
        return None

    def close(self) -> None:
        pass

    def __enter__(self) -> "NullTaskWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class LiberoTaskWriter:
    """Owns one ``<task>_demo.hdf5`` file: one file per task, one ``demo_N`` per episode.

    Not safe for concurrent writers on the same file; one collection session
    owns one open writer.
    """

    def __init__(
        self,
        root: Path,
        task_name: str,
        language_instruction: str,
        robot_name: str = "fr3_gello_real",
        resume: bool = False,
        schema: Optional[DatasetSchemaConfig] = None,
        crop_params: Optional[dict] = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = task_name.strip().replace(" ", "_")
        self.path = self.root / f"{safe_name}_demo.hdf5"
        self.language_instruction = language_instruction
        self.schema = schema or DatasetSchemaConfig()
        # Connect 시점의 스냅샷. 매 에피소드 attrs 에 그대로 찍힌다 -- 변환이
        # 파일만 보고 조작자가 맞춘 프레이밍을 재현할 수 있도록.
        self.crop_params = crop_params or default_crop_params()
        self._buffer = LiberoEpisodeBuffer(self.schema, self.crop_params)

        if self.path.exists() and not resume:
            raise FileExistsError(
                f"{self.path} already exists; pass resume=True to append episodes."
            )

        self._file = h5py.File(self.path, "a")
        _mark_close_on_exec(self._file)
        self._data = self._file.require_group("data")
        if "env_args" not in self._data.attrs:
            env_args = {
                "env_name": "real_fr3_gello",
                "type": "real_robot",
                "env_kwargs": {
                    "robot": robot_name,
                    "note": (
                        "Real-robot capture -- no simulator/BDDL scene; this "
                        "field exists only so LIBERO-style readers that "
                        "expect it do not KeyError."
                    ),
                },
            }
            self._data.attrs["env_args"] = json.dumps(env_args)
        if "problem_info" not in self._data.attrs:
            problem_info = {
                "language_instruction": f'"{self.language_instruction}"',
                "problem_name": safe_name,
            }
            self._data.attrs["problem_info"] = json.dumps(problem_info)
        if "next_demo_idx" not in self._data.attrs:
            # delete_episode() renumbers to close gaps and resets this attr
            # to the post-renumber count (see renumber_episodes()), so in
            # steady state this is just `num_episodes` -- this fallback
            # only matters for a file that predates renumbering and still
            # has old gaps, where it's still the highest-index-plus-one so
            # a new episode's name can't collide with a surviving one.
            existing = [int(k.split("_")[1]) for k in self._data.keys()]
            self._data.attrs["next_demo_idx"] = max(existing, default=-1) + 1
        self._file.flush()

    def record_session_config(self, **kwargs: Any) -> None:
        """Overwrites the file-level ``session_config`` attr with whatever
        non-camera session settings (reset_pose, grip, enable_wall,
        max_episode_seconds, reset_wait_seconds) this session was started
        with, so a later session can restore them for the same task. Written
        by gello/libero_gui_worker.py; the wizard GUI's dropdown used to read
        it back, and the workspace UI that replaced it (62cad92) does not yet.
        Always reflects the LATEST session, not the first-ever one: intentionally overwritten every time, since this is
        "how to continue this task", not a history log.
        """
        self._data.attrs["session_config"] = json.dumps(kwargs)
        self._file.flush()

    @property
    def num_episodes(self) -> int:
        return len(self._data.keys())

    def list_episodes(self) -> list[dict]:
        """Current demos sorted by index: ``[{"name", "num_samples", "success"}, ...]``."""
        items = []
        for name in self._data.keys():
            grp = self._data[name]
            success = grp.attrs.get("success")
            items.append(
                {
                    "name": name,
                    "num_samples": int(grp.attrs.get("num_samples", grp["actions"].shape[0])),
                    "success": None if success is None else bool(success),
                }
            )
        items.sort(key=lambda d: int(d["name"].split("_")[1]))
        return items

    def delete_episode(self, name: str) -> None:
        """Removes a ``demo_N`` group, then renumbers the rest to close the
        resulting gap (see :func:`renumber_episodes`).

        HDF5 does not shrink the file on delete -- the freed space is only
        reusable by later writes *within this same file*, not returned to the
        OS. Run ``h5repack`` afterwards if reclaiming disk space matters.
        """
        if name not in self._data:
            raise KeyError(f"{name!r} not found in {self.path}")
        del self._data[name]
        renumber_episodes(self._data)
        self._file.flush()

    def set_episode_success(self, name: str, success: bool) -> None:
        """Re-labels an already-saved episode as success/failure.

        The verdict is worth more a few seconds after the take than during it:
        the operator has stopped moving, the arm is going home, and they can
        actually look at what happened. Only the attribute changes -- frames,
        images and numbering are untouched -- so this stays cheap no matter how
        big the episode was.
        """
        if name not in self._data:
            raise KeyError(f"{name!r} not found in {self.path}")
        self._data[name].attrs["success"] = bool(success)
        self._file.flush()

    def start_episode(self) -> None:
        self._buffer.clear()

    def add_frame(self, **kwargs: Any) -> None:
        """Forwards to :meth:`LiberoEpisodeBuffer.add_frame`."""
        self._buffer.add_frame(**kwargs)

    def discard_episode(self) -> None:
        self._buffer.clear()

    def detach_buffer(self) -> LiberoEpisodeBuffer:
        """Swap out the filled episode buffer and install a fresh one, so the
        next episode can start recording while the detached buffer is being
        written by a background thread (see libero_gui_worker.EpisodeSaver)."""
        buf = self._buffer
        self._buffer = LiberoEpisodeBuffer(self.schema, self.crop_params)
        return buf

    def save_episode(self, success: Optional[bool] = None) -> Optional[str]:
        """Synchronous convenience wrapper: detach + save_buffer in one call."""
        return self.save_buffer(self.detach_buffer(), success=success)

    def save_buffer(self, buf: LiberoEpisodeBuffer, success: Optional[bool] = None) -> Optional[str]:
        """Commits one (detached) episode buffer as a new ``demo_N`` group.

        h5py is not thread-safe: every file-touching call on this writer
        (save_buffer / delete_episode / list_episodes / close) must be
        serialized onto ONE thread by the caller -- EpisodeSaver owns exactly
        that serialization in the GUI.

        Args:
            buf: the buffer returned by :meth:`detach_buffer`.
            success: operator-labeled outcome (no simulator goal-check exists
                for a real robot). Not a canonical LIBERO field; stored as a
                per-demo attr for downstream filtering. ``None`` if unlabeled.

        Returns the group name, or None if the buffer was empty.
        """
        n = len(buf)
        if n < 2:
            buf.clear()
            return None

        demo_idx = int(self._data.attrs["next_demo_idx"])
        self._data.attrs["next_demo_idx"] = demo_idx + 1
        name = f"demo_{demo_idx}"
        grp = self._data.create_group(name)
        write_episode_payload(grp, buf, self.schema, success=success)

        self._file.flush()
        buf.clear()
        return name

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "LiberoTaskWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ---------------------------------------------------------------- repack state
# 재압축 직후 파일도 메타데이터 오버헤드로 0.3~0.4%는 남는다(실측). 3%를 넘으면
# 지운 에피소드가 차지하던 자리로 보는 게 안전하다 -- 실측에서 삭제가 있었던
# 파일들은 4.1 / 8.3 / 17.3% 였다.
DEAD_SPACE_RATIO = 0.03


def _stored_bytes(group) -> int:
    """Bytes every dataset in this file actually occupies on disk.

    get_storage_size() is the compressed, on-disk size -- not the logical
    array size -- so this stays meaningful for gzip'd images. Metadata only:
    no chunk is read.
    """
    total = 0
    stack = [group]
    while stack:
        g = stack.pop()
        for key in g:
            item = g[key]
            if isinstance(item, h5py.Group):
                stack.append(item)
            else:
                total += item.id.get_storage_size()
    return total


REPACK_MARKER_ATTR = "repacked"
# Episode count at the moment of repack, so a later run can say how many were
# appended since rather than only that the file changed.
REPACK_COUNT_ATTR = "repacked_episodes"


def hdf5_repack_status(path) -> dict:
    """Has this file been through scripts/repack_hdf5.py?

    Two signals, because the marker only exists on files repacked after it was
    introduced. The image compressor is the retroactive one and is decisive on
    its own: the collector always writes images with ``lzf`` (fast, so the
    background save never stalls the operator), and repack rewrites them with
    ``gzip``. Anything already gzip has been repacked.

    **Every episode is checked, not just the first.** A file that was repacked
    and then collected into again is the common case -- the operator adds a few
    demos to an existing task file -- and it ends up *mixed*: the old episodes
    are gzip, the new ones lzf, and the stale marker still names the earlier
    run. Sampling one episode (or trusting the marker) reports such a file as
    finished and silently drops it from the repack selection, which is exactly
    the file that still has uncompressed episodes in it. So a mixed file counts
    as not repacked, and the marker cannot override that.

    Returns ``{"repacked", "compression", "mixed", "marker", "new_since",
    "size", "episodes", "error"}``; never raises -- an unreadable file comes
    back with ``error`` set so a caller listing a directory can show it
    instead of dying.
    """
    out = {"repacked": False, "compression": None, "mixed": False,
           "marker": None, "new_since": 0, "deleted_since": 0,
           "dead_bytes": 0, "dead_ratio": 0.0, "size": 0, "episodes": 0,
           "error": None}
    try:
        out["size"] = Path(path).stat().st_size
        with h5py.File(path, "r") as f:
            if "data" in f:
                marker_grp = f["data"]        # legacy: 마커도 에피소드도 data/
                container = f["data"]
                episode_names = list(container.keys())
            else:
                # scene-v1: 마커는 metadata 그룹에, 에피소드는 루트에 있다.
                # 에피소드 안쪽 페이로드는 legacy 와 동일해 아래 로직을 공유.
                marker_grp = f["metadata"]
                container = f
                episode_names = [k for k in f.keys() if k.startswith("episode_")]
            out["episodes"] = len(episode_names)
            out["marker"] = marker_grp.attrs.get(REPACK_MARKER_ATTR)
            if isinstance(out["marker"], bytes):
                out["marker"] = out["marker"].decode(errors="replace")
            at_repack = marker_grp.attrs.get(REPACK_COUNT_ATTR)
            if at_repack is not None:
                out["new_since"] = max(0, out["episodes"] - int(at_repack))
                out["deleted_since"] = max(0, int(at_repack) - out["episodes"])
            # Dead space: HDF5 never returns a deleted group's bytes to the OS,
            # it only makes them reusable inside the same file. So a curated
            # file keeps paying for takes that are gone, and nothing about the
            # remaining episodes shows it -- they are all still gzip, the
            # marker is still there, and an episode-count comparison misses
            # the common "delete two, record two more" case entirely.
            # Comparing the file's size against what its datasets actually
            # occupy catches all of it, and costs only metadata reads.
            out["dead_bytes"] = max(0, out["size"] - _stored_bytes(f))
            out["dead_ratio"] = out["dead_bytes"] / out["size"] if out["size"] else 0.0
            comps = set()
            for name in episode_names:
                obs = container[name].get("obs")
                if obs is None:
                    continue
                for key in ("agentview_rgb", "eye_in_hand_rgb"):
                    ds = obs.get(key)
                    if ds is not None:
                        comps.add(ds.compression)
                        break
            out["mixed"] = len(comps) > 1
            if comps:
                out["compression"] = "+".join(sorted(c or "없음" for c in comps))
        fully_gzip = comps == {"gzip"}
        marked = bool(out["marker"]) and not out["mixed"]
        # 죽은 공간이 크면 압축 방식과 무관하게 재압축 대상이다.
        out["repacked"] = (fully_gzip or marked) and out["dead_ratio"] < DEAD_SPACE_RATIO
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out
