"""User-configurable LIBERO dataset schema: which action space to compute
``actions`` from, and which observation fields actually get written.

Persisted as JSON so a custom configuration chosen in the GUI (see
gello/gui_widgets.py's DatasetSchemaDialog) survives across restarts.
"""

from __future__ import annotations

import json
import warnings
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

    # ---- 아래 다섯은 GUI 에서 고를 수 없는 고정값이다 ----
    # 액션 구조가 파일마다 갈리면 한 데이터셋 안에 조용히 호환되지 않는 파일이
    # 섞이고, 그걸 잡아주는 장치가 지금 없다(issue #12). 관측 필드는 더하거나
    # 빼도 파일끼리 호환되므로 계속 고를 수 있게 둔다.
    action_space: str = ACTION_SPACE_JOINT_ABSOLUTE
    # LIBERO's original actions always end with a gripper component (see
    # libero_format.py's compute_delta_action) -- True keeps that. Off drops
    # the trailing gripper dimension from `actions` for every action space
    # (e.g. a policy that controls the gripper separately from arm motion).
    action_include_gripper: bool = True   # 고정
    # 고정 On: action 의 그리퍼를 observation 의 gripper_states 와 같은
    # 0=open/1=close 로 쓴다. robosuite 의 -1/+1 대신 이걸 쓰는 이유는 열 이름과
    # 마찬가지로 Hugging Face 뷰어에서 obs 와 action 을 짝지어 보기 위해서다.
    #
    # 다만 "같은 인코딩"이지 "같은 신호"는 아니다. action 은 리더 트리거를
    # 이진화한 값이라 0 또는 1 뿐이고, observation 은 실제 핑거 폭이라 명령 후
    # ~0.3초 뒤부터 8~10Hz 로 0..1 사이를 연속으로 지난다(gello/gripper_synth.py).
    # 그 간격이 정책이 학습해야 할 그리퍼 지연이다.
    gripper_action_match_obs: bool = True

    # 정사각 크롭 후 리사이즈할 한 변(px). 기본 None = 크롭도 리사이즈도 하지
    # 않고 카메라가 준 프레임을 그대로 쓴다(현재 640x480).
    #
    # .hdf5 는 원본 보관소라 최대한 남기고, 줄이는 것은 LeRobot 변환에서 한다
    # (scripts/convert_libero_to_lerobot.py --image-size). 그래야 학습 해상도를
    # 바꿀 때 다시 찍지 않아도 된다. 대신 정사각이 아니므로, 무엇이 크롭되어
    # 살아남는지는 Live 탭의 정사각 가이드로 본다.
    image_size: int | None = None

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

    # Off by default (issue #17): depth 는 카메라 ASIC 이 이미 계산하는 값이라
    # 호스트 연산은 공짜지만 데이터는 이미지급이다 -- 캠당 640x480 uint16 로
    # 에피소드당 수십 MB, USB 대역 +~176Mbps. 무손실(lzf)로 원본 해상도
    # 그대로 저장하고 crop/resize 는 하지 않는다 (RGB-depth 픽셀 대응은
    # D455 에서 원래 안 맞으므로, 원시 저장 + 필요할 때 후처리가 일관적).
    # LeRobot 변환은 depth 를 무시한다 -- HDF5 원본 보관소에만 남는다.
    save_agentview_depth: bool = False
    save_eye_in_hand_depth: bool = False

    # 고정 빈 dict = 내장 이름(joint1.pos .. joint7.pos, gripper.pos) 사용.
    # 내장 이름이 곧 observation 의 열 이름이라, Hugging Face 뷰어가 obs 와
    # action 을 같은 축에 짝지어 그려준다. 필드는 남겨두지만 GUI 에서는 고를 수
    # 없다 -- 이름을 바꿔서 얻을 것보다 짝이 깨져서 잃을 것이 크다.
    action_column_name_overrides: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    # GUI 에서 고를 수 없는 필드들. 저장된 JSON 에 옛 값이 남아 있어도 무시한다
    # -- 다이얼로그에서 뺐다는 이유만으로 고정이 되지는 않는다. 이 목록이 실제
    # 강제 지점이고, 다이얼로그는 그 결과를 보여줄 뿐이다.
    #
    # depth 수집은 lerobot 0.5.0 RealSenseCamera 가 read_latest_depth 를
    # 지원하지 않아 당분간 비활성화한다. 코드(버퍼/저장 경로)는 남겨두고
    # 플래그만 강제 Off 로 막는다 (fix/depth-gate).
    _FIXED = ("action_space", "action_include_gripper", "gripper_action_match_obs",
              "action_column_name_overrides",
              "save_agentview_depth", "save_eye_in_hand_depth")

    @classmethod
    def from_json(cls, s: str) -> "DatasetSchemaConfig":
        data = json.loads(s)
        valid = {f.name for f in fields(cls)} - set(cls._FIXED)
        filtered = {k: v for k, v in data.items() if k in valid}
        # depth 플래그가 True 로 저장돼 있어도 드라이버 미지원으로 인해 Off
        for flag in ("save_agentview_depth", "save_eye_in_hand_depth"):
            if data.get(flag):
                warnings.warn(
                    f"{flag}=True 인 설정을 무시합니다: "
                    "카메라 드라이버(lerobot RealSenseCamera)가 depth 읽기를 "
                    "지원하지 않아 수집이 비활성화되어 있습니다.",
                    stacklevel=2,
                )
        return cls(**filtered)


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
