"""User-configurable LIBERO dataset schema: which action space to compute
``actions`` from, and which observation fields actually get written.

Persisted as JSON so a custom configuration chosen in the GUI (see
collect_libero_gui.py's "사용자 지정" dialog) survives across restarts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ACTION_SPACE_EE_DELTA = "ee_delta"
ACTION_SPACE_EE_ABSOLUTE = "ee_absolute"
ACTION_SPACE_JOINT_DELTA = "joint_delta"
ACTION_SPACE_JOINT_ABSOLUTE = "joint_absolute"
ACTION_SPACES = (
    ACTION_SPACE_EE_DELTA,
    ACTION_SPACE_EE_ABSOLUTE,
    ACTION_SPACE_JOINT_DELTA,
    ACTION_SPACE_JOINT_ABSOLUTE,
)

ACTION_SPACE_LABELS = {
    ACTION_SPACE_EE_DELTA: "EE-delta (LIBERO 기본)",
    ACTION_SPACE_EE_ABSOLUTE: "EE-pose absolute (절대 목표 pose)",
    ACTION_SPACE_JOINT_DELTA: "Joint-angle delta (변화량)",
    ACTION_SPACE_JOINT_ABSOLUTE: "Joint-angle absolute (절대 목표값)",
}

DEFAULT_CONFIG_PATH = Path.home() / "libero_gui_logs" / "dataset_schema.json"


@dataclass
class DatasetSchemaConfig:
    """``use_default=True`` is the escape hatch back to LIBERO's original
    fixed schema, regardless of what the other fields below say -- see
    :meth:`effective`. Every field already defaults to that original schema,
    so ``DatasetSchemaConfig()`` and ``DatasetSchemaConfig(use_default=True)``
    produce byte-identical files; only a saved *custom* config (use_default
    False, something toggled) changes what gets written.
    """

    use_default: bool = True
    action_space: str = ACTION_SPACE_EE_DELTA
    # LIBERO's original actions always end with a gripper component (see
    # libero_format.py's compute_delta_action) -- True keeps that. Off drops
    # the trailing gripper dimension from `actions` for every action space
    # (e.g. a policy that controls the gripper separately from arm motion).
    action_include_gripper: bool = True
    # Off (default): action's gripper is -1=open/+1=close, robosuite's Panda
    # sign convention (this module's original design, kept for LIBERO-format
    # consumer compatibility). On: action's gripper uses the SAME 0=open/
    # 1=closed encoding as observation's gripper_states -- so obs and action
    # match not just in shape (action_space=joint_absolute/joint_delta) but
    # in the gripper VALUE convention too.
    gripper_action_match_obs: bool = False

    # 256 matches LIBERO/OpenVLA's convention (center-cropped square + resized
    # -- see libero_format.py's resize_rgb). None keeps the raw camera frame
    # as-is (e.g. RealSense's native 480x640, not center-cropped to square).
    image_size: int | None = 256

    save_agentview_rgb: bool = True
    save_eye_in_hand_rgb: bool = True
    save_joint_states: bool = True
    save_gripper_states: bool = True
    save_ee_states: bool = True
    save_ee_pos: bool = True
    save_ee_ori: bool = True

    # Off by default: not part of LIBERO's original schema, computed for
    # free from data the control loop already produces (see
    # gello/libero_gui_worker.py's _get_obs).
    save_joint_velocities: bool = False
    save_timestamp: bool = False

    def effective(self) -> "DatasetSchemaConfig":
        """The config that actually governs a write. Toggling ``use_default``
        back off in the GUI restores whatever custom selection was last made
        -- it isn't lost just because default was on for a session."""
        return DatasetSchemaConfig() if self.use_default else self

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "DatasetSchemaConfig":
        data = json.loads(s)
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid})


def load_schema_config(path: Path = DEFAULT_CONFIG_PATH) -> DatasetSchemaConfig:
    """Never raises -- a missing/corrupt config file just means "no custom
    config saved yet", not a startup failure."""
    try:
        return DatasetSchemaConfig.from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return DatasetSchemaConfig()


def save_schema_config(cfg: DatasetSchemaConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.to_json(), encoding="utf-8")
