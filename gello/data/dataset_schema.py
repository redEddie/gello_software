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


# --------------------------------------------------------- dataset schema 버전
# 우리 데이터셋 형식의 버전 (GitHub issue #41). SemVer 를 쓰되 접두사로
# 출처를 밝힌다: knu-MAJOR.MINOR.PATCH.
#
#   MINOR = 필드 '추가'만. 옛 리더가 새 파일을 열어도 자기가 아는 필드는
#           그대로 있으니 읽힌다(전방 호환), 새 리더가 옛 파일을 열면
#           추가 필드만 없다(후방 호환). 예: 조인트 토크 추가 -> knu-1.1.0.
#   MAJOR = 기존 필드의 의미·단위·이름 변경이나 삭제. 리더는 자기 MAJOR
#           안의 모든 MINOR 를 읽을 수 있어야 한다.
#   PATCH = 데이터에 영향 없는 명세 문서 수정.
#
# 명세 문서는 docs/dataset-schema.md 이고, 이 상수들이 그 문서의 코드 쪽
# 정본이다 (문서와 어긋나면 검증기가 잡는다).
SCHEMA_VERSION = "knu-1.0.0"

# --------------------------------------------------------- observation/dataset keys
# Robot observation keys (returned by Robot.get_observations / RobotEnv.get_obs).
ROBOT_JOINT_POSITIONS = "joint_positions"
ROBOT_JOINT_VELOCITIES = "joint_velocities"
ROBOT_EE_POS_QUAT = "ee_pos_quat"
ROBOT_GRIPPER_POSITION = "gripper_position"

# Dataset observation keys (stored under episode/obs in HDF5).
OBS_AGENTVIEW_RGB = "agentview_rgb"
OBS_EYE_IN_HAND_RGB = "eye_in_hand_rgb"
OBS_JOINT_STATES = "joint_states"
OBS_COMMANDED_JOINT_STATES = "commanded_joint_states"
OBS_GRIPPER_STATES = "gripper_states"
OBS_COMMANDED_GRIPPER_STATES = "commanded_gripper_states"
OBS_EE_POS_QUAT = "ee_pos_quat"
OBS_EE_STATES = "ee_states"
OBS_EE_POS = "ee_pos"
OBS_EE_ORI = "ee_ori"
OBS_JOINT_VELOCITIES = "joint_velocities"

#: 버전 문자열이 없던 시절의 표기 -> 현재 버전. 기존 파일(scene_000~014)은
#: 전부 ``dataset_version="scene-v1"`` 이고 필드 구성이 knu-1.0.0 과 완전히
#: 같아, 소급 기록 없이 별칭으로만 해석한다.
SCHEMA_VERSION_ALIASES = {
    # 2026-08-31 이전 파일의 표기. 대부분은 아래 stamp 스크립트로 실제
    # knu-1.0.0 을 써 넣었지만, Hub 사본·백업·old_data 처럼 손대지 않은
    # 사본이 남아 있으므로 별칭은 영구히 유지한다.
    "scene-v1": "knu-1.0.0",
    "": "knu-1.0.0",      # 아주 초기 파일: 표기 자체가 없다
}

#: 버전별 필수 필드. 검증기(scripts/check/check_scene_file.py)가 이걸 본다.
#: 새 MINOR 를 추가할 때는 이전 항목을 고치지 말고 새 키를 넣는다 -- 옛
#: 파일을 옛 규칙으로 계속 검사할 수 있어야 한다.
SCHEMA_FIELDS = {
    "knu-1.0.0": {
        # episode 그룹 바로 아래
        "episode_datasets": ("actions", "dones", "rewards"),
        # episode/obs 아래. depth 는 여기 없다 -- 카메라 드라이버가 아직
        # depth 읽기를 지원하지 않아 수집 자체가 꺼져 있다(_FIXED 참조).
        # 되살아나면 필드 '추가'이므로 knu-1.1.0 이다.
        "obs_datasets": (
            OBS_AGENTVIEW_RGB, OBS_EYE_IN_HAND_RGB,
            OBS_JOINT_STATES, OBS_COMMANDED_JOINT_STATES,
            OBS_GRIPPER_STATES, OBS_COMMANDED_GRIPPER_STATES,
            OBS_EE_STATES, OBS_EE_POS, OBS_EE_ORI,
        ),
        # episode 그룹 attrs
        "episode_attrs": (
            "instruction", "instruction_id", "episode_id", "episode_uid",
            "num_samples", "success", "quality_status", "scene_id",
            "slot_episode_idx", "collector", "station", "timestamp",
            "action_space", "action_column_names",
            "gripper_action_convention", "crop_params",
        ),
        # metadata 그룹 attrs
        "metadata_attrs": (
            "scene_id", "objects", "layout", "description", "station",
            "dataset_version", "created", "next_episode_idx",
        ),
    },
}


def normalize_schema_version(value) -> str:
    """파일에 적힌 버전 표기를 정본 형태로. 별칭은 풀고, 나머지는 그대로.

    파일이 정본이므로 모르는 표기라고 던지지 않는다 -- 그대로 돌려주고
    판단은 호출자(검증기)에게 맡긴다. 여기서 죽으면 옛 파일을 열지 못하게
    되는데, 그게 버저닝을 도입한 이유와 정반대다.
    """
    s = str(value or "").strip()
    return SCHEMA_VERSION_ALIASES.get(s, s)


def parse_schema_version(value) -> "tuple[int, int, int] | None":
    """``knu-1.0.0`` -> ``(1, 0, 0)``. 형식이 아니면 None."""
    s = normalize_schema_version(value)
    if not s.startswith("knu-"):
        return None
    parts = s[4:].split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def schema_is_readable(value, reader: str = SCHEMA_VERSION) -> bool:
    """이 리더가 그 파일을 읽을 수 있는가.

    같은 MAJOR 안에서는 MINOR 가 위든 아래든 읽을 수 있다: MINOR 는 필드
    추가만 하기로 했으므로, 위 버전 파일에는 모르는 필드가 더 있을 뿐이고
    아래 버전 파일에는 나중에 생긴 필드가 없을 뿐이다. MAJOR 가 다르면
    필드의 의미가 달라졌을 수 있어 읽을 수 없다고 본다.
    """
    a, b = parse_schema_version(value), parse_schema_version(reader)
    return bool(a and b and a[0] == b[0])


def schema_required_fields(value) -> "dict | None":
    """그 버전이 요구하는 필드 목록. 모르는 버전이면 None."""
    return SCHEMA_FIELDS.get(normalize_schema_version(value))


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
    # (scripts/convert/convert_libero_to_lerobot.py --image-size). 그래야 학습 해상도를
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
        cfg = cls(**filtered)
        # depth 플래그가 True 로 저장돼 있어도 드라이버 미지원으로 인해 Off.
        # warnings 는 stderr 로만 가서 데스크톱 아이콘 실행에서는 아무 데도 안
        # 남는다 (collect_workspace.py 의 stderr 주석 참조) -- 무시된 플래그를
        # 인스턴스 속성으로도 들고 있어 GUI 가 로그 뷰가 생긴 뒤 보이는 로그로
        # 재보고할 수 있게 한다. dataclass 필드가 아니므로 to_json 에는 안 실린다.
        ignored = [flag for flag in ("save_agentview_depth", "save_eye_in_hand_depth")
                   if data.get(flag)]
        if ignored:
            warnings.warn(
                f"{', '.join(ignored)}=True 인 설정을 무시합니다: "
                "카메라 드라이버(lerobot RealSenseCamera)가 depth 읽기를 "
                "지원하지 않아 수집이 비활성화되어 있습니다.",
                stacklevel=2,
            )
            cfg.ignored_depth_flags = ignored
        return cfg


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


def selftest() -> None:
    """버저닝 규약 자체 검증 (issue #41). 하드웨어·파일 불필요."""
    # 별칭: 옛 표기는 현재 버전으로 풀린다 (파일 소급 수정 없이)
    assert normalize_schema_version("scene-v1") == "knu-1.0.0"
    assert normalize_schema_version("") == "knu-1.0.0"
    assert normalize_schema_version("knu-1.1.0") == "knu-1.1.0"
    assert parse_schema_version("scene-v1") == (1, 0, 0)
    assert parse_schema_version("모르는버전") is None

    # 같은 MAJOR 안이면 위·아래 MINOR 모두 읽는다 (MINOR = 추가만)
    assert schema_is_readable("knu-1.0.0", reader="knu-1.0.0")
    assert schema_is_readable("knu-1.0.0", reader="knu-1.1.0")   # 후방 호환
    assert schema_is_readable("knu-1.1.0", reader="knu-1.0.0")   # 전방 호환
    assert not schema_is_readable("knu-2.0.0", reader="knu-1.0.0")  # MAJOR 다름
    assert not schema_is_readable("이상한거")

    # 현재 버전은 필드 목록을 갖고 있고, 그 목록이 문서와 같은 정본이다
    cur = schema_required_fields(SCHEMA_VERSION)
    assert cur is not None and SCHEMA_VERSION in SCHEMA_FIELDS
    assert set(cur) == {"episode_datasets", "obs_datasets",
                        "episode_attrs", "metadata_attrs"}
    # depth 는 0.1.0 에 없다 -- 드라이버 미지원으로 수집 자체가 꺼져 있고,
    # 되살아나면 '추가'라 1.1.0 이다 (docs/dataset-schema.md)
    assert not any("depth" in f for f in cur["obs_datasets"])
    assert "save_agentview_depth" in DatasetSchemaConfig._FIXED
    # 토크도 아직 없다 (issue #16 -> 1.1.0)
    assert not any("torque" in f for f in cur["obs_datasets"])

    # 모르는 버전은 필드 목록이 없다 -> 검증기가 "모르는 스키마 버전" 으로 잡는다
    assert schema_required_fields("knu-9.9.9") is None
    print("dataset_schema selftest 통과")


if __name__ == "__main__":
    selftest()
