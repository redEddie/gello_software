"""User-configurable LIBERO dataset schema: which action space to compute
``actions`` from, and which observation fields actually get written.

Persisted as JSON so a custom configuration chosen in the GUI (see
collect_libero_gui.py's "사용자 지정" dialog) survives across restarts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
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
    ACTION_SPACE_JOINT_ABSOLUTE: "Joint-angle absolute (리더 명령, ACT 규약)",
}

DEFAULT_CONFIG_PATH = Path.home() / "libero_gui_logs" / "dataset_schema.json"


@dataclass
class DatasetSchemaConfig:
    """What gets written to the HDF5. Every field defaults to LIBERO's
    original fixed schema, so a bare ``DatasetSchemaConfig()`` reproduces it.

    There used to be a ``use_default`` flag that overrode every other field
    at write time. It was removed: it silently discarded the operator's
    ``action_space`` choice (a session set to ``joint_absolute`` would write
    ``ee_delta`` instead, with no error), which is exactly the kind of
    invisible mismatch this schema exists to prevent. Old saved JSON that
    still carries the key is simply ignored by :meth:`from_json`.
    """

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

    # Per-dimension action column name overrides, keyed by the BUILT-IN
    # default name (see libero_format._ACTION_COLUMNS / "gripper.pos") so
    # switching action_space doesn't carry stale overrides from a different
    # space's columns. Empty (default) means "use the built-in names" --
    # only affects the human-readable names shown in the schema preview and
    # the LeRobotDataset feature names built from them (see
    # libero_format.resolved_action_column_names); the underlying array data
    # and column ORDER are unaffected.
    action_column_name_overrides: dict[str, str] = field(default_factory=dict)

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
