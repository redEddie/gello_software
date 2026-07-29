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
"""

from __future__ import annotations

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


def compute_joint_absolute_action(q_next: np.ndarray, gripper_closed: bool) -> np.ndarray:
    """Absolute target joint position (rad) at frame t+1 -- NOT a delta.

    Some BC policies prefer predicting an absolute next-joint-position target
    over a frame-to-frame difference; this is that convention, as opposed to
    :func:`compute_joint_delta_action`'s ``q_next - q_curr``. Note this looks
    similar to (but is semantically distinct from) ``obs/joint_states``: the
    obs field is what the robot measured *at* frame t, this is the target the
    action at frame t is driving *towards* (frame t+1's realized position).

    Args:
        q_next: (7,) measured joint positions (rad) at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (8,) float32: (joint1..joint7, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    q = np.asarray(q_next, dtype=np.float32)
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([q, [gripper]]).astype(np.float32)


_ACTION_COLUMNS = {
    ACTION_SPACE_EE_DELTA: ["dx", "dy", "dz", "d_axis_x", "d_axis_y", "d_axis_z"],
    ACTION_SPACE_EE_ABSOLUTE: ["x", "y", "z", "axis_x", "axis_y", "axis_z"],
    ACTION_SPACE_JOINT_DELTA: [f"d_joint{i}" for i in range(1, 8)],
    ACTION_SPACE_JOINT_ABSOLUTE: [f"joint{i}" for i in range(1, 8)],
}


def action_column_names(action_space: str) -> list[str]:
    """Non-gripper column names for ``action_space``, e.g. for building a
    LeRobotDataset ``features`` dict (see scripts/convert_libero_to_lerobot.py)
    without hardcoding a copy of :data:`_ACTION_COLUMNS` elsewhere."""
    return list(_ACTION_COLUMNS[action_space])


def describe_schema(cfg: DatasetSchemaConfig) -> str:
    """Human-readable summary of the exact ``obs``/``actions`` structure
    ``LiberoTaskWriter.save_episode`` would write for ``cfg``.

    Pure description, no robot/episode needed -- backs the GUI's "구조
    미리보기" so an operator can check a custom schema before committing to
    it (and before ever connecting). Resolves ``cfg.effective()`` first, so
    it always reflects what would actually be written, not what a possibly
    unrelated set of checkboxes says while "기본값 사용" is on.
    """
    schema = cfg.effective()

    cols = list(_ACTION_COLUMNS[schema.action_space])
    if schema.action_include_gripper:
        gripper_note = "0=open/1=close, matches obs" if schema.gripper_action_match_obs else "-1=open/+1=close"
        cols.append(f"gripper ({gripper_note})")
    lines = [
        f"Action space: {ACTION_SPACE_LABELS.get(schema.action_space, schema.action_space)}",
        f"  actions: (T, {len(cols)}) float32 = [{', '.join(cols)}]",
        "",
        "obs/:",
    ]

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
    if schema.save_timestamp:
        obs_rows.append(("timestamp", "(T,) float64  -- wall-clock seconds"))

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
    cols = base_cols + ([f"gripper ({gripper_note})"] if has_gripper else [])

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
    return "\n".join(lines)


def resize_rgb(img: np.ndarray, size: int = IMAGE_SIZE) -> np.ndarray:
    """Resize an (H, W, 3) uint8 RGB image to (size, size, 3), center-cropped square first."""
    import cv2

    h, w = img.shape[:2]
    s = min(h, w)
    y0, x0 = (h - s) // 2, (w - s) // 2
    cropped = img[y0 : y0 + s, x0 : x0 + s]
    return cv2.resize(cropped, (size, size), interpolation=cv2.INTER_AREA)


class LiberoEpisodeBuffer:
    """Accumulates one episode's frames in memory before it is committed or discarded.

    ``joint_states``/``ee_pos_quat``/``gripper_closed`` are always buffered
    regardless of ``schema`` -- action computation needs them no matter which
    action space is selected, and no matter which obs fields end up written
    (see LiberoTaskWriter.save_episode). Only the genuinely optional/costly
    fields (images, joint velocities, timestamps) are gated on ``schema``.
    """

    def __init__(self, schema: Optional[DatasetSchemaConfig] = None) -> None:
        self.schema = (schema or DatasetSchemaConfig()).effective()
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
    ) -> None:
        self.joint_states.append(np.asarray(joint_positions, dtype=np.float32))
        self.gripper_states.append(np.array([gripper_position], dtype=np.float32))
        self.ee_pos_quat.append(np.asarray(ee_pos_quat, dtype=np.float64))
        self.gripper_closed.append(bool(gripper_closed))
        if self.schema.save_agentview_rgb:
            self.agentview_rgb.append(self._process_image(agentview_rgb))
        if self.schema.save_eye_in_hand_rgb:
            self.eye_in_hand_rgb.append(self._process_image(eye_in_hand_rgb))
        if self.schema.save_joint_velocities and joint_velocities is not None:
            self.joint_velocities.append(np.asarray(joint_velocities, dtype=np.float32))
        if self.schema.save_timestamp and timestamp is not None:
            self.timestamps.append(float(timestamp))

    def _process_image(self, img: np.ndarray) -> np.ndarray:
        if self.schema.image_size is None:
            return np.asarray(img, dtype=np.uint8)
        return resize_rgb(img, size=self.schema.image_size)

    def clear(self) -> None:
        self._reset_lists()


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
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = task_name.strip().replace(" ", "_")
        self.path = self.root / f"{safe_name}_demo.hdf5"
        self.language_instruction = language_instruction
        self.schema = (schema or DatasetSchemaConfig()).effective()
        self._buffer = LiberoEpisodeBuffer(self.schema)

        if self.path.exists() and not resume:
            raise FileExistsError(
                f"{self.path} already exists; pass resume=True to append episodes."
            )

        self._file = h5py.File(self.path, "a")
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
            # Monotonic, never reused -- so deleting demo_2 and recording a new
            # episode afterwards can't collide with the name of a demo that
            # still exists. `num_episodes` (live count) and this counter
            # (total ever assigned) intentionally diverge once anything has
            # been deleted.
            existing = [int(k.split("_")[1]) for k in self._data.keys()]
            self._data.attrs["next_demo_idx"] = max(existing, default=-1) + 1
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
        """Removes a ``demo_N`` group.

        HDF5 does not shrink the file on delete -- the freed space is only
        reusable by later writes *within this same file*, not returned to the
        OS. Run ``h5repack`` afterwards if reclaiming disk space matters.
        """
        if name not in self._data:
            raise KeyError(f"{name!r} not found in {self.path}")
        del self._data[name]
        self._file.flush()

    def start_episode(self) -> None:
        self._buffer.clear()

    def add_frame(self, **kwargs: Any) -> None:
        """Forwards to :meth:`LiberoEpisodeBuffer.add_frame`."""
        self._buffer.add_frame(**kwargs)

    def discard_episode(self) -> None:
        self._buffer.clear()

    def save_episode(self, success: Optional[bool] = None) -> Optional[str]:
        """Commits the buffered episode as a new ``demo_N`` group.

        Args:
            success: operator-labeled outcome (no simulator goal-check exists
                for a real robot). Not a canonical LIBERO field; stored as a
                per-demo attr for downstream filtering. ``None`` if unlabeled.

        Returns the group name, or None if the buffer was empty.
        """
        n = len(self._buffer)
        if n < 2:
            self._buffer.clear()
            return None

        schema = self.schema
        if schema.action_space == ACTION_SPACE_JOINT_DELTA:
            q = np.stack(self._buffer.joint_states)  # (n, 7)
            actions = np.zeros((n, 8), dtype=np.float32)
            for t in range(n - 1):
                actions[t] = compute_joint_delta_action(
                    q[t], q[t + 1], self._buffer.gripper_closed[t]
                )
            # Terminal frame: no further motion recorded; hold gripper state.
            actions[n - 1, :7] = 0.0
            actions[n - 1, 7] = 1.0 if self._buffer.gripper_closed[-1] else -1.0
        elif schema.action_space == ACTION_SPACE_JOINT_ABSOLUTE:
            q = np.stack(self._buffer.joint_states)  # (n, 7)
            actions = np.zeros((n, 8), dtype=np.float32)
            for t in range(n - 1):
                actions[t] = compute_joint_absolute_action(
                    q[t + 1], self._buffer.gripper_closed[t]
                )
            # Terminal frame: no further target recorded; hold current position.
            actions[n - 1, :7] = q[n - 1]
            actions[n - 1, 7] = 1.0 if self._buffer.gripper_closed[-1] else -1.0
        elif schema.action_space == ACTION_SPACE_EE_ABSOLUTE:
            ee = np.stack(self._buffer.ee_pos_quat)  # (n, 7)
            actions = np.zeros((n, 7), dtype=np.float32)
            for t in range(n - 1):
                actions[t] = compute_ee_absolute_action(
                    ee[t + 1], self._buffer.gripper_closed[t]
                )
            # Terminal frame: no further target recorded; hold current pose.
            actions[n - 1, :3] = ee[n - 1, :3]
            actions[n - 1, 3:6] = _quat_to_axis_angle(*ee[n - 1, 3:7])
            actions[n - 1, 6] = 1.0 if self._buffer.gripper_closed[-1] else -1.0
        else:
            ee = np.stack(self._buffer.ee_pos_quat)  # (n, 7)
            actions = np.zeros((n, 7), dtype=np.float32)
            for t in range(n - 1):
                actions[t] = compute_delta_action(
                    ee[t], ee[t + 1], self._buffer.gripper_closed[t]
                )
            actions[n - 1, :6] = 0.0
            actions[n - 1, 6] = 1.0 if self._buffer.gripper_closed[-1] else -1.0

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

        demo_idx = int(self._data.attrs["next_demo_idx"])
        self._data.attrs["next_demo_idx"] = demo_idx + 1
        name = f"demo_{demo_idx}"
        grp = self._data.create_group(name)
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

        obs = grp.create_group("obs")
        if schema.save_agentview_rgb:
            obs.create_dataset(
                "agentview_rgb",
                data=np.stack(self._buffer.agentview_rgb),
                compression="gzip",
                compression_opts=4,
            )
        if schema.save_eye_in_hand_rgb:
            obs.create_dataset(
                "eye_in_hand_rgb",
                data=np.stack(self._buffer.eye_in_hand_rgb),
                compression="gzip",
                compression_opts=4,
            )
        if schema.save_joint_states:
            obs.create_dataset("joint_states", data=np.stack(self._buffer.joint_states))
        if schema.save_gripper_states:
            obs.create_dataset(
                "gripper_states", data=np.stack(self._buffer.gripper_states)
            )
        if schema.save_ee_states or schema.save_ee_pos or schema.save_ee_ori:
            ee = np.stack(self._buffer.ee_pos_quat)  # (n, 7)
            ee_ori = np.stack([_quat_to_axis_angle(*q[3:7]) for q in ee]).astype(np.float32)
            ee_pos = ee[:, :3].astype(np.float32)
            if schema.save_ee_states:
                ee_states = np.concatenate([ee_pos, ee_ori], axis=1).astype(np.float32)
                obs.create_dataset("ee_states", data=ee_states)
            if schema.save_ee_pos:
                obs.create_dataset("ee_pos", data=ee_pos)
            if schema.save_ee_ori:
                obs.create_dataset("ee_ori", data=ee_ori)
        if schema.save_joint_velocities and self._buffer.joint_velocities:
            obs.create_dataset(
                "joint_velocities", data=np.stack(self._buffer.joint_velocities)
            )
        if schema.save_timestamp and self._buffer.timestamps:
            obs.create_dataset(
                "timestamp", data=np.array(self._buffer.timestamps, dtype=np.float64)
            )

        grp.create_dataset("actions", data=actions)
        grp.create_dataset("rewards", data=np.zeros(n, dtype=np.float32))
        dones = np.zeros(n, dtype=np.float32)
        dones[-1] = 1.0
        grp.create_dataset("dones", data=dones)

        self._file.flush()
        self._buffer.clear()
        return name

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "LiberoTaskWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
