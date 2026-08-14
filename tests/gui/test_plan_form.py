"""계획 폼 편집기 + Configure 계획 문장 드롭다운 검증 (offscreen)."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/experiments")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="planform_"))
plan_copy = TMP / "pilot.json"
shutil.copy(f"{WT}/configs/collection_plans/pilot.json", plan_copy)
orig = json.loads(plan_copy.read_text())
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
cw.QMessageBox.information = staticmethod(lambda *a, **k: None)

# ---- 1. 폼 로드: scene 목록 + 행 내용 ----
dlg = cw.PlanEditDialog(None, plan_copy)
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
dlg2 = cw.PlanEditDialog(None, plan_copy)
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
assert s000[-1]["instruction_id"] == "I003"  # I001 재사용 금지 -> 다음 번호
print("3 통과: 행 삭제(번호 유지) + 지운 번호 재사용 금지")

# ---- 4. scene 추가 + 검증 실패 시 파일 무변경 ----
before = plan_copy.read_text()
dlg3 = cw.PlanEditDialog(None, plan_copy)
dlg3._on_add_scene()
assert dlg3.scene_combo.currentText() == "S002"
dlg3._add_row({"id": None, "instr": "", "target": 1})
bad = dlg3.tree.topLevelItem(0)
dlg3.tree.itemWidget(bad, 1).setText('"quoted sentence"')   # 규칙 위반
dlg3._save()
assert dlg3.error_label.text(), "검증 실패가 표시되지 않음"
assert plan_copy.read_text() == before
dlg3.tree.itemWidget(bad, 1).setText("push the plate to the left side")
dlg3._save()
saved = json.loads(plan_copy.read_text())
s2 = [s for s in saved["scenes"] if s["scene_id"] == "S002"]
assert s2 and s2[0]["slots"][0]["instruction_id"] == "I000"
w = [x for x in dlg3.warnings if "동사" in x]
assert w, "push 동사 경고가 안 남음"
print("4 통과: scene 추가(S002, I000부터) + 검증 게이트 + 동사 경고 전달")

# ---- 5. Configure 계획 문장 드롭다운 ----
cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None
win = cw.WorkspaceWindow(None)
i = win.plan_combo.findText("pilot.json")
assert i >= 0
win.plan_combo.setCurrentIndex(i)
# scene 파일이 수집 세션에 잠겨 있어도 돌 수 있게 scene 선택을 주입한다
win._configure_scene_id = lambda: "S000"
win._selected_scene_path = lambda: None
win._refresh_start_plan_combo()
combo = win.start_plan_combo
plan = win._current_plan()
n_slots = len(plan.slots_for("S000"))
assert combo.count() == 1 + n_slots, (combo.count(), n_slots)
combo.setCurrentIndex(1)                 # 첫 계획 문장 선택
d = combo.currentData()
assert d and win.scene_iid_edit.text() == d[0] and win.lang_edit.text() == d[1]
assert "/" in combo.itemText(1)          # 카운트 표기 c/target
print(f"5 통과: 계획 문장 드롭다운 ({n_slots}개) + ID/문장 자동 채움")

print("\n계획 폼 + 드롭다운 검증 통과")
import os  # noqa: E402

os._exit(0)
