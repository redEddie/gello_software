"""Read-only schema/episode description helpers for LIBERO-format HDF5.

These functions do not write files; they summarize the structure implied by a
``DatasetSchemaConfig`` or read shapes/dtypes/attrs from an existing
``h5py.Group``.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from gello.data.dataset_schema import (
    ACTION_SPACE_EE_ABSOLUTE,
    ACTION_SPACE_EE_DELTA,
    ACTION_SPACE_JOINT_ABSOLUTE,
    ACTION_SPACE_JOINT_DELTA,
    ACTION_SPACE_LABELS,
    OBS_AGENTVIEW_RGB,
    OBS_COMMANDED_GRIPPER_STATES,
    OBS_COMMANDED_JOINT_STATES,
    OBS_EE_ORI,
    OBS_EE_POS,
    OBS_EE_STATES,
    OBS_EYE_IN_HAND_RGB,
    OBS_GRIPPER_STATES,
    OBS_JOINT_STATES,
    OBS_JOINT_VELOCITIES,
    DatasetSchemaConfig,
)

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
    LeRobotDataset ``features`` dict (see scripts/convert/convert_libero_to_lerobot.py)
    without hardcoding a copy of :data:`_ACTION_COLUMNS` elsewhere."""
    return list(_ACTION_COLUMNS[action_space])


def resolved_action_column_names(schema: DatasetSchemaConfig) -> list[str]:
    """The exact action column names ``save_episode`` will write to the
    ``action_column_names`` attr, and that ``describe_schema``/
    ``_build_features`` (scripts/convert/convert_libero_to_lerobot.py) show/use --
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
        obs_rows.append((OBS_AGENTVIEW_RGB, f"(T, {img_dims}, 3) uint8{img_note}"))
    if schema.save_eye_in_hand_rgb:
        obs_rows.append((OBS_EYE_IN_HAND_RGB, f"(T, {img_dims}, 3) uint8{img_note}"))
    if schema.save_joint_states:
        obs_rows.append((OBS_JOINT_STATES, "(T, 7) float32"))
    if schema.save_gripper_states:
        obs_rows.append((OBS_GRIPPER_STATES, "(T, 1) float32  -- continuous, 0=open..1=closed"))
    if schema.save_ee_states:
        obs_rows.append((OBS_EE_STATES, "(T, 6) float32  -- pos(3) + axis-angle(3)"))
    if schema.save_ee_pos:
        obs_rows.append((OBS_EE_POS, "(T, 3) float32"))
    if schema.save_ee_ori:
        obs_rows.append((OBS_EE_ORI, "(T, 3) float32  -- axis-angle"))
    if schema.save_joint_velocities:
        obs_rows.append((OBS_JOINT_VELOCITIES, "(T, 7) float32"))
    if schema.save_agentview_depth:
        obs_rows.append(("agentview_depth", "(T, H, W) uint16 mm · 원본 해상도 · lzf"))
    if schema.save_eye_in_hand_depth:
        obs_rows.append(("eye_in_hand_depth", "(T, H, W) uint16 mm · 원본 해상도 · lzf"))
    if schema.save_timestamp:
        obs_rows.append(("timestamp", "(T,) float64  -- wall-clock seconds"))

    # Not schema-gated: the GUI worker always supplies the teleop command
    # stream, so every episode it records carries these (see save_episode).
    obs_rows.append((OBS_COMMANDED_JOINT_STATES, "(T, 7) float32  -- GELLO leader command (rad)"))
    obs_rows.append((OBS_COMMANDED_GRIPPER_STATES, "(T, 1) float32  -- commanded, 0=open..1=closed"))

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
    OBS_GRIPPER_STATES: "  -- continuous, 0=open..1=closed",
    OBS_EE_STATES: "  -- pos(3) + axis-angle(3)",
    OBS_EE_ORI: "  -- axis-angle",
    "timestamp": "  -- wall-clock seconds",
    OBS_COMMANDED_JOINT_STATES: "  -- GELLO leader command (rad)",
    OBS_COMMANDED_GRIPPER_STATES: "  -- commanded, 0=open..1=closed",
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
    for key in (OBS_AGENTVIEW_RGB, OBS_EYE_IN_HAND_RGB):
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
        save_agentview_rgb=OBS_AGENTVIEW_RGB in obs_keys,
        save_eye_in_hand_rgb=OBS_EYE_IN_HAND_RGB in obs_keys,
        save_joint_states=OBS_JOINT_STATES in obs_keys,
        save_gripper_states=OBS_GRIPPER_STATES in obs_keys,
        save_ee_states=OBS_EE_STATES in obs_keys,
        save_ee_pos=OBS_EE_POS in obs_keys,
        save_ee_ori=OBS_EE_ORI in obs_keys,
        save_joint_velocities=OBS_JOINT_VELOCITIES in obs_keys,
        save_timestamp="timestamp" in obs_keys,
        save_agentview_depth="agentview_depth" in obs_keys,
        save_eye_in_hand_depth="eye_in_hand_depth" in obs_keys,
        action_column_name_overrides=overrides,
    )
