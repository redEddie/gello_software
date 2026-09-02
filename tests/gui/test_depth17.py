"""depth 기록(#17) 검증 + depth 수집 게이트 검증 (offscreen, 로봇/카메라 불필요).

lerobot 0.5.0 RealSenseCamera 는 read_latest_depth 가 없어 depth 수집이
즉사하는 것을 막는 게이트를 추가한다. 이 테스트는 저장 경로 + UI 게이트 +
워커 방어 가드 + 설정 파일 강제 무시를 검증한다.
"""
import sys
import tempfile
import warnings
from pathlib import Path

import h5py
import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.path.insert(0, WT + "/scripts")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from gello.data.dataset_schema import (  # noqa: E402
    OBS_AGENTVIEW_RGB,
    ROBOT_EE_POS_QUAT,
    ROBOT_JOINT_POSITIONS,
    ROBOT_JOINT_VELOCITIES,
    DatasetSchemaConfig,
)
from gello.gui.dialogs import DatasetSchemaDialog  # noqa: E402
from gello.data.libero_format import LiberoTaskWriter  # noqa: E402
from gello.data.schema_description import schema_from_episode  # noqa: E402
from gello.collect.worker import CollectionWorker, WorkerConfig  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="depth17_"))
r = np.random.default_rng(0)


def frame_kwargs(depth: bool):
    kw = dict(
        agentview_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        eye_in_hand_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        joint_positions=r.standard_normal(7).astype(np.float32),
        gripper_position=0.5, ee_pos_quat=np.zeros(7), gripper_closed=False,
        commanded_joint_positions=r.standard_normal(7).astype(np.float32),
        commanded_gripper=0.0)
    if depth:
        kw["agentview_depth"] = r.integers(0, 4000, (48, 64), dtype=np.uint16)
        kw["eye_in_hand_depth"] = r.integers(0, 4000, (48, 64), dtype=np.uint16)
    return kw


# ---- 1. depth 켠 스키마: uint16 + lzf + 원본 해상도 (RGB 리사이즈와 무관) ----
schema = DatasetSchemaConfig(save_agentview_depth=True,
                             save_eye_in_hand_depth=True,
                             save_timestamp=True, image_size=32)
w = LiberoTaskWriter(root=TMP, task_name="d_on", language_instruction="t",
                     schema=schema)
w.start_episode()
for _ in range(5):
    w.add_frame(timestamp=1.0, **frame_kwargs(depth=True))
w.save_buffer(w.detach_buffer(), success=True)
w.close()
with h5py.File(TMP / "d_on_demo.hdf5") as f:
    obs = f["data/demo_0/obs"]
    for key in ("agentview_depth", "eye_in_hand_depth"):
        d = obs[key]
        assert d.dtype == np.uint16 and d.compression == "lzf"
        assert d.shape == (5, 48, 64), d.shape      # depth 는 원본 해상도
    assert obs[OBS_AGENTVIEW_RGB].shape == (5, 32, 32, 3)  # RGB 만 리사이즈
    sc = schema_from_episode(f["data/demo_0"])
    assert sc.save_agentview_depth and sc.save_eye_in_hand_depth
print("1 통과: uint16+lzf 저장, depth 원본 해상도(RGB 리사이즈 비적용), 스키마 왕복")

# ---- 2. 스키마 꺼짐(기본): depth 인자를 줘도 안 쓴다 ----
w2 = LiberoTaskWriter(root=TMP, task_name="d_off", language_instruction="t")
w2.start_episode()
for _ in range(3):
    w2.add_frame(**frame_kwargs(depth=True))
w2.save_buffer(w2.detach_buffer(), success=True)
w2.close()
with h5py.File(TMP / "d_off_demo.hdf5") as f:
    assert "agentview_depth" not in f["data/demo_0/obs"]
print("2 통과: 기본 스키마는 depth 미기록 (opt-in)")

# ---- 3. 변환기: depth 유무가 섞여도 스키마 일치 판정 ----
# import 만 ImportError 로 감싼다 (lerobot 버전 의존) -- 검증(assert)까지
# except 로 덮으면 진짜 회귀가 "import 불가" 로 위장되어 통과한다.
try:
    from convert_libero_to_lerobot import _episode_schema  # noqa: E402
except ImportError as e:
    print(f"3 건너뜀: convert_libero_to_lerobot import 불가 (ImportError: {e})")
else:
    with h5py.File(TMP / "d_on_demo.hdf5") as f_on, \
            h5py.File(TMP / "d_off_demo.hdf5") as f_off:
        s_on = _episode_schema(f_on["data/demo_0"])
        s_off = _episode_schema(f_off["data/demo_0"])
    assert s_on == s_off, (s_on, s_off)
    print("3 통과: 소비 키 기준 비교 -- depth 혼합 변환 가능")

# ---- 4. 워커의 depth 역할 파생 ----
cfg = WorkerConfig(task_name="t", language_instruction="t", data_root=str(TMP),
                   schema=DatasetSchemaConfig(save_eye_in_hand_depth=True))
cw = CollectionWorker(cfg)
assert cw._depth_roles == {"wrist"}
cw2 = CollectionWorker(WorkerConfig(task_name="t", language_instruction="t",
                                    data_root=str(TMP)))
assert cw2._depth_roles == set()
print("4 통과: 스키마 -> depth 역할 파생 (wrist 만 / 기본 없음)")

# ---- 5. 설정 파일 depth True 강제 무시 ----
print("5. DatasetSchemaConfig.from_json depth 플래그 강제 무시")
raw_cfg = DatasetSchemaConfig(
    save_agentview_depth=True,
    save_eye_in_hand_depth=True,
)
json_text = raw_cfg.to_json()
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    loaded = DatasetSchemaConfig.from_json(json_text)
    assert loaded.save_agentview_depth is False
    assert loaded.save_eye_in_hand_depth is False
    assert len(w) == 1
    assert "depth 읽기" in str(w[0].message)
# 무시 사실이 인스턴스 속성으로도 남아야 한다 -- warnings 는 stderr 로만 가서
# 데스크톱 아이콘 실행에서 소실되므로, GUI 가 이 속성을 보고 로그 뷰에 재보고한다.
assert loaded.ignored_depth_flags == [
    "save_agentview_depth", "save_eye_in_hand_depth"]
clean = DatasetSchemaConfig.from_json(DatasetSchemaConfig().to_json())
assert getattr(clean, "ignored_depth_flags", []) == []
print("   통과: depth 플래그 False 강제 + 경고 1건 + ignored_depth_flags 기록")

# ---- 6. _get_obs 가 read_latest_depth 없는 카메라에서도 예외 없이 진행 ----
print("6. _get_obs 가 read_latest_depth 없는 카메라에서도 예외 없이 진행")


class FakeClient:
    def get_observations(self):
        return {
            ROBOT_JOINT_POSITIONS: [0.0] * 7,
            ROBOT_EE_POS_QUAT: [0.0] * 7,
            ROBOT_JOINT_VELOCITIES: [0.0] * 7,
        }


class FakeCam:
    """read_latest 는 있지만 read_latest_depth 는 없는 카메라."""

    def read_latest(self, max_age_ms=500):
        return np.zeros((480, 640, 3), dtype=np.uint8)


class FakeRobot:
    def __init__(self):
        self._client = FakeClient()
        self.cameras = {"agent": FakeCam(), "wrist": FakeCam()}


cfg = WorkerConfig(
    task_name="test_depth17",
    language_instruction="test",
    data_root=str(TMP),
    schema=DatasetSchemaConfig(
        save_agentview_depth=True,
        save_eye_in_hand_depth=True,
    ),
)
worker = CollectionWorker(cfg)
worker._robot = FakeRobot()

# depth 역할이 스키마로 인해 채워져 있어야 한다.
assert {"agent", "wrist"} == worker._depth_roles

obs = worker._get_obs()

# depth 출력 키가 생성되지 않아야 한다.
assert "_agent_depth" not in obs
assert "_wrist_depth" not in obs
# RGB 와 상태 키는 정상 생성.
assert "agent" in obs
assert "wrist" in obs
assert "_ee_pos_quat" in obs
# 역할이 제거되었고 1회 경고 플래그가 세팅되었어야 한다.
assert worker._depth_roles == set()
assert worker._depth_unsupported_warned is True
print("   통과: depth 역할 제거 + 세션 지속")

# ---- 7. DatasetSchemaDialog 의 depth 체크박스 비활성화 + 결과 강제 False ----
print("7. DatasetSchemaDialog 의 depth 체크박스 비활성화 + 결과 강제 False")
dlg = DatasetSchemaDialog(None, raw_cfg)
for attr in ("save_agentview_depth", "save_eye_in_hand_depth"):
    cb = dlg.field_checks[attr]
    assert cb.isEnabled() is False
    assert "지원하지 않아" in cb.toolTip()
# 대화상자를 OK 없이도 _current_config 로 결과를 읽을 수 있다.
result = dlg._current_config()
assert result.save_agentview_depth is False
assert result.save_eye_in_hand_depth is False
print("   통과: 체크박스 비활성 + 결과 depth False")

print("\ndepth 기록 + 게이트 검증 통과")
import os  # noqa: E402

os._exit(0)
