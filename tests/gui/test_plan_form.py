"""계획 폼 편집기 + Configure 계획 문장 드롭다운 검증 (offscreen)."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication, QInputDialog  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from apps.workspace.features.scene.dialogs.plan_edit_dialog import PlanEditDialog  # noqa: E402
from gello.scene.collection_plan import PLANS_DIR  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="planform_"))
plan_copy = TMP / "pilot.json"
shutil.copy(f"{WT}/configs/collection/plans/pilot.json", plan_copy)
orig = json.loads(plan_copy.read_text())
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
cw.QMessageBox.information = staticmethod(lambda *a, **k: None)

# ---- 1. 폼 로드: scene 목록 + 행 내용 ----
dlg = PlanEditDialog(None, plan_copy)
assert [dlg.scene_combo.itemText(i) for i in range(dlg.scene_combo.count())] \
    == [s["scene_id"] for s in orig["scenes"]]
assert dlg.tree.topLevelItemCount() == len(orig["scenes"][0]["slots"])
it0 = dlg.tree.topLevelItem(0)
assert it0.text(0) == "I000"
assert dlg.tree.itemWidget(it0, 1).text() == orig["scenes"][0]["slots"][0]["instruction"]
assert dlg.tree.itemWidget(it0, 2).value() == orig["scenes"][0]["slots"][0]["target"]
print("1 통과: 폼 로드 (scene 목록·ID·문장·목표)")

# ---- 2. 목표/문장 수정 + 행 추가(자동 ID) + 저장 ----
dlg.tree.itemWidget(it0, 2).setValue(15)
dlg._add_row({"id": None, "instr": "", "target": 10})
new_it = dlg.tree.topLevelItem(dlg.tree.topLevelItemCount() - 1)
dlg.tree.itemWidget(new_it, 1).setText("open the top drawer of the cabinet")
dlg.tree.itemWidget(new_it, 2).setValue(5)
dlg._save()
saved = json.loads(plan_copy.read_text())
s000 = saved["scenes"][0]["slots"]
assert s000[0]["target"] == 15
assert s000[-1]["instruction_id"] == f"I{len(orig['scenes'][0]['slots']):03d}"
assert s000[-1]["instruction"] == "open the top drawer of the cabinet"
assert s000[-1]["target"] == 5
# note 등 부가 필드 보존
for k, v in orig["scenes"][0].items():
    if k != "slots":
        assert saved["scenes"][0][k] == v, k
assert saved.get("plan_version") == orig.get("plan_version")
print("2 통과: 목표 수정 + 새 행 자동 ID + 부가 필드 보존")

# ---- 3. 행 삭제 후 남은 ID 유지 + 삭제 번호 재사용 금지 ----
dlg2 = PlanEditDialog(None, plan_copy)
it = dlg2.tree.topLevelItem(1)          # I001 삭제
it.setSelected(True)
dlg2._on_del_row()
dlg2._add_row({"id": None, "instr": "", "target": 3})
ni = dlg2.tree.topLevelItem(dlg2.tree.topLevelItemCount() - 1)
dlg2.tree.itemWidget(ni, 1).setText("close the top drawer of the cabinet")
dlg2._save()
s000 = json.loads(plan_copy.read_text())["scenes"][0]["slots"]
ids = [s["instruction_id"] for s in s000]
assert "I001" not in ids                 # 삭제됨
assert ids[0] == "I000"                  # 남은 행 번호 불변
n0 = len(orig["scenes"][0]["slots"])          # 원본 슬롯 수
assert s000[-1]["instruction_id"] == f"I{n0 + 1:03d}"  # 섹션2에서 +1, 지운 I001 재사용 금지 -> 그 다음
print("3 통과: 행 삭제(번호 유지) + 지운 번호 재사용 금지")

# ---- 4. scene 추가 + 검증 실패 시 파일 무변경 ----
before = plan_copy.read_text()
next_sid = f"S{max(int(s['scene_id'][1:]) for s in json.loads(before)['scenes']) + 1:03d}"
dlg3 = PlanEditDialog(None, plan_copy)
dlg3._on_add_scene()
assert dlg3.scene_combo.currentText() == next_sid
dlg3._add_row({"id": None, "instr": "", "target": 1})
bad = dlg3.tree.topLevelItem(0)
dlg3.tree.itemWidget(bad, 1).setText('"quoted sentence"')   # 규칙 위반
dlg3._save()
assert dlg3.error_label.text(), "검증 실패가 표시되지 않음"
assert plan_copy.read_text() == before
dlg3.tree.itemWidget(bad, 1).setText("push the plate to the left side")
dlg3._save()
saved = json.loads(plan_copy.read_text())
s2 = [s for s in saved["scenes"] if s["scene_id"] == next_sid]
assert s2 and s2[0]["slots"][0]["instruction_id"] == "I000"
w = [x for x in dlg3.warnings if "동사" in x]
assert w, "push 동사 경고가 안 남음"
print(f"4 통과: scene 추가({next_sid}, I000부터) + 검증 게이트 + 동사 경고 전달")

# ---- 5. Configure 계획 문장 드롭다운 ----
cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
win = cw.WorkspaceWindow(None)
i = win.plan_combo.findText("pilot.json")
assert i >= 0
win.plan_combo.setCurrentIndex(i)
# scene 파일이 수집 세션에 잠겨 있어도 돌 수 있게 scene 선택을 주입한다
win.scene_ops.configure_scene_id = lambda: "S000"
win.scene_ops.selected_scene_path = lambda: None
win.scene_planning.refresh_start_plan_combo()
combo = win.start_plan_combo
plan = win.scene_planning.current_plan()
n_slots = len(plan.slots_for("S000"))
assert combo.count() == 1 + n_slots, (combo.count(), n_slots)
combo.setCurrentIndex(1)                 # 첫 계획 문장 선택
d = combo.currentData()
assert d and win.scene_iid_edit.text() == d[0] and win.lang_edit.text() == d[1]
assert "/" in combo.itemText(1)          # 카운트 표기 c/target
print(f"5 통과: 계획 문장 드롭다운 ({n_slots}개) + ID/문장 자동 채움")

# ---- 6. 계획 선택 시: 자유 입력 잠금 + 계획 밖 문장 연결 거부 ----
assert win.lang_edit.isReadOnly() and win.scene_iid_edit.isReadOnly()
win.collector_edit.setText("t")
win.lang_edit.setText("open the top drawer")     # 계획에 없는 문장 (주입)
win.scene_iid_edit.setText("I009")
_, _, _, err = win.scene_ops.scene_config_from_ui()
assert err and "계획" in err, err
combo.setCurrentIndex(1)                          # 계획 문장으로 복귀
win.scene_planning.on_start_plan_pick()   # 인덱스가 그대로면 시그널이 없어 직접 호출
_, _, _, err2 = win.scene_ops.scene_config_from_ui()
assert err2 is None or "계획" not in err2, err2   # 남는 오류는 scene 선택뿐
print("6 통과: 계획 선택 시 자유 입력 잠금 + 계획 밖 시작 문장 거부")

# ---- 7. 계획 파일 새로 만들기 / 삭제 ----
QInputDialog.getText = staticmethod(lambda *a, **k: ("tmp-uitest", True))
cw.QMessageBox.question = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
win.scene_planning.on_edit_plan = lambda: None        # 모달 편집 열림 방지
new_path = PLANS_DIR / "tmp-uitest.json"
new_path.unlink(missing_ok=True)
try:
    win.scene_planning.on_new_plan()
    assert new_path.exists()
    assert win.plan_combo.currentText() == "tmp-uitest.json"
    assert json.loads(new_path.read_text())["scenes"] == []
    win.scene_planning.on_delete_plan()
    assert not new_path.exists()
    assert win.plan_combo.findText("tmp-uitest.json") < 0
finally:
    new_path.unlink(missing_ok=True)
    # 실사용 recents 오염 복구 -- 생성 시 tmp-uitest 가 최근 계획으로 남는다
    win._recents.add("plan_file", "pilot.json")
print("7 통과: 계획 파일 생성(+선택) / 삭제(+목록 갱신)")

# ---- 8. 번호 정리: 빈 scene 만 압축, 수집된 scene 은 거부 ----
warns = []
cw.QMessageBox.warning = staticmethod(lambda *a, **k: warns.append(a[2] if len(a) > 2 else ""))
cw.QMessageBox.information = staticmethod(lambda *a, **k: None)
dlg8 = PlanEditDialog(None, plan_copy)
# 빈 scene 흉내: 존재 확인을 주입 (파일계 의존 제거)
dlg8._scene_has_episodes = lambda sid: False
dlg8._on_del_row()                       # no-op (선택 없음)
for it_ in [dlg8.tree.topLevelItem(0)]:
    it_.setSelected(True)
dlg8._on_del_row()                       # I000 삭제 -> 남은 ID 는 I001..
before_ids = [dlg8.tree.topLevelItem(i).data(0, cw.Qt.ItemDataRole.UserRole)
              for i in range(dlg8.tree.topLevelItemCount())]
assert before_ids and before_ids[0] != "I000"
dlg8._on_compact_ids()
after_ids = [dlg8.tree.topLevelItem(i).data(0, cw.Qt.ItemDataRole.UserRole)
             for i in range(dlg8.tree.topLevelItemCount())]
assert after_ids == [f"I{i:03d}" for i in range(len(after_ids))], after_ids
# 수집된 scene 은 거부
dlg8._scene_has_episodes = lambda sid: True
dlg8._on_compact_ids()
assert warns and "이미 수집된" in warns[-1]
print("8 통과: 번호 정리 -- 빈 scene 압축(I000..) / 수집된 scene 거부")

print("\n계획 폼 + 드롭다운 검증 통과")
import os  # noqa: E402

os._exit(0)
