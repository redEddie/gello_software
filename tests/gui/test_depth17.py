"""depth 기록(#17) 검증 — 버퍼→저장 왕복, 원본 해상도 유지, 스키마 혼합 변환."""
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/scripts")

from gello.dataset_schema import DatasetSchemaConfig  # noqa: E402
from gello.libero_format import LiberoTaskWriter, schema_from_episode  # noqa: E402

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
    assert obs["agentview_rgb"].shape == (5, 32, 32, 3)  # RGB 만 리사이즈
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
from convert_libero_to_lerobot import _episode_schema  # noqa: E402

with h5py.File(TMP / "d_on_demo.hdf5") as f_on, \
        h5py.File(TMP / "d_off_demo.hdf5") as f_off:
    s_on = _episode_schema(f_on["data/demo_0"])
    s_off = _episode_schema(f_off["data/demo_0"])
assert s_on == s_off, (s_on, s_off)
print("3 통과: 소비 키 기준 비교 -- depth 혼합 변환 가능")

# ---- 4. 워커의 depth 역할 파생 ----
from gello.libero_gui_worker import CollectionWorker, WorkerConfig  # noqa: E402

cfg = WorkerConfig(task_name="t", language_instruction="t", data_root=str(TMP),
                   schema=DatasetSchemaConfig(save_eye_in_hand_depth=True))
cw = CollectionWorker(cfg)
assert cw._depth_roles == {"wrist"}
cw2 = CollectionWorker(WorkerConfig(task_name="t", language_instruction="t",
                                    data_root=str(TMP)))
assert cw2._depth_roles == set()
print("4 통과: 스키마 -> depth 역할 파생 (wrist 만 / 기본 없음)")

print("\ndepth 기록(#17) 검증 통과")
import os  # noqa: E402

os._exit(0)
