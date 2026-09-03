"""slot 카운터 (issue #38) 검증 -- 수집 화면의 현재 (scene, instruction)
누계/계획 target 상시 표시. HDF5 실측 기반이라 에피소드를 지우면 숫자가
줄어든다 (GUI 를 켠 순간 누계라면 안 줄어든다 -- 이 검사가 이슈의 핵심)."""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from gello.scene.scene_format import (  # noqa: E402
    QUALITY_FAILED,
    QUALITY_SUCCESS,
    SceneMetadata,
    SceneWriter,
    count_by_slot,
)

INSTR = "pick up the blue cup"
IID = "I003"

d = Path(tempfile.mkdtemp(prefix="slotctr_"))
md = SceneMetadata(scene_id="S000", objects=["OBJ-CUP-BLU-01"],
                   layout={"grid": [3, 3],
                           "placements": {"OBJ-CUP-BLU-01": {"zone": [0, 0]}}},
                   description="slot 카운터 테스트")
w = SceneWriter(d, metadata=md, collector="t")
r = np.random.default_rng(0)


def _ep() -> None:
    w.start_episode()
    for _ in range(5):
        w.add_frame(
            agentview_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            eye_in_hand_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            joint_positions=r.standard_normal(7).astype(np.float32),
            gripper_position=0.5, ee_pos_quat=np.zeros(7), gripper_closed=False,
            commanded_joint_positions=r.standard_normal(7).astype(np.float32),
            commanded_gripper=0.0)


_ep()
ok0 = w.save_buffer(w.detach_buffer(), instruction=INSTR, instruction_id=IID,
                    success=True, collector="t")
_ep()
ok1 = w.save_buffer(w.detach_buffer(), instruction=INSTR, instruction_id=IID,
                    success=True, collector="t")
_ep()
bad = w.save_buffer(w.detach_buffer(), instruction=INSTR, instruction_id=IID,
                    success=False, collector="t")
w.close()
scene = d / "scene_000.hdf5"

# ---- 1. count_by_slot: 성공 2 + 실패 1 -> usable=2, total=3 ----
counts = count_by_slot(scene)
assert counts[IID] == {"total": 3, "usable": 2}, counts
import h5py  # noqa: E402
with h5py.File(scene) as f:
    assert f[ok0].attrs["quality_status"] == QUALITY_SUCCESS
    assert f[bad].attrs["quality_status"] == QUALITY_FAILED
print("1 통과: count_by_slot usable=2/total=3 (실패 1개는 usable 에 안 센다)")

# ---- 2. GUI: scene+instruction 선택, 계획 없음 -> 누계만 ----
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402

cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
cw.QMessageBox.question = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
win = cw.WorkspaceWindow(None)
win.root_edit.setText(str(d))
win.scene_ops.refresh_scene_combo()
win.scene_combo.setCurrentIndex(win.scene_combo.findData("S000"))
# 기동 시 복원되는 실제 계획이 이 slot 을 가질 수 있다 -- '계획 없음' 으로 명시 해제
win.plan_combo.setCurrentIndex(0)
win.scene_iid_edit.setText(IID)
win.collection.refresh_slot_counter()
t = win.slot_counter.text()
assert t == f"S000 · {IID} · 2", t          # target 없음 -- 누계만, 0/0 도 아님
assert "/" not in t
print(f"2 통과: 계획 없음 -- 누계만 ({t})")

# ---- 3. 계획 target 10 -> '2/10', 미달이면 초록 아님 ----
plan10 = d / "plan10.json"
plan10.write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": IID, "instruction": INSTR, "target": 10}]}]},
    ensure_ascii=False), encoding="utf-8")
win.plan_combo.addItem(plan10.name, str(plan10))
win.plan_combo.setCurrentIndex(win.plan_combo.count() - 1)
win.collection.refresh_slot_counter()
t = win.slot_counter.text()
assert t == f"S000 · {IID} · 2/10", t
assert "#2ecc71" not in win.slot_counter.styleSheet()
print(f"3 통과: 계획 target 과 맞춤 -- {t} (미달이라 초록 아님)")

# ---- 4. target 도달(2/2) 이면 초록, 초과(11/10)도 숫자 정확 ----
plan2 = d / "plan2.json"
plan2.write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": IID, "instruction": INSTR, "target": 2}]}]},
    ensure_ascii=False), encoding="utf-8")
win.plan_combo.addItem(plan2.name, str(plan2))
win.plan_combo.setCurrentIndex(win.plan_combo.findText(plan2.name))
win.collection.refresh_slot_counter()
assert win.slot_counter.text() == f"S000 · {IID} · 2/2", win.slot_counter.text()
assert "#2ecc71" in win.slot_counter.styleSheet(), win.slot_counter.styleSheet()
# 임시로 셋째(실패) 에피소드를 success 로 뒤집어 3/2 -- 넘어도 정확히 보여준다
with h5py.File(scene, "a") as f:
    f[bad].attrs["quality_status"] = QUALITY_SUCCESS
win.collection.refresh_slot_counter()
assert win.slot_counter.text() == f"S000 · {IID} · 3/2", win.slot_counter.text()
with h5py.File(scene, "a") as f:
    f[bad].attrs["quality_status"] = QUALITY_FAILED
win.collection.refresh_slot_counter()
print("4 통과: target 도달=초록, 초과(3/2)도 숫자 그대로")

# ---- 5. 계획에 없는 slot -> 누계만 ----
win.scene_iid_edit.setText("I009")
win.collection.refresh_slot_counter()
t = win.slot_counter.text()
assert t == "S000 · I009 · 0" and "/" not in t, t
win.scene_iid_edit.setText(IID)
print(f"5 통과: 계획에 없는 slot -- 누계만 ({t})")

# ---- 6. 에피소드 삭제 -> 숫자가 줄어든다 (이슈 #38 핵심) ----
#    GUI DatasetOps 삭제 경로로 지우고, 카운터가 HDF5 를 다시 읽는지 본다.
win.plan_combo.setCurrentIndex(win.plan_combo.findText(plan10.name))
win.collection.refresh_slot_counter()
assert win.slot_counter.text() == f"S000 · {IID} · 2/10"
ok = win.dataset_ops.delete_episodes({scene: [ok0]})
assert ok
# 삭제 직후 count_by_slot 실측
counts = count_by_slot(scene)
assert counts[IID] == {"total": 2, "usable": 1}, counts
# 카운터도 줄었다 -- GUI 를 켠 순간 누계였다면 여전히 2/10 이다
assert win.slot_counter.text() == f"S000 · {IID} · 1/10", win.slot_counter.text()
assert "#2ecc71" not in win.slot_counter.styleSheet()
print("6 통과: 에피소드 삭제 뒤 카운터 감소 2/10 -> 1/10 (HDF5 실측, GUI 누계 아님)")

# ---- 7. scene 미선택 -> 자리 비움 (0/0 같은 것 안 냄) ----
win.scene_combo.setCurrentIndex(0)
win.collection.refresh_slot_counter()
assert win.slot_counter.text() == "—", win.slot_counter.text()
print("7 통과: scene 미선택 -- 자리 비움")

print("\nslot 카운터 (issue #38) 검증 통과")
import os  # noqa: E402

os._exit(0)
