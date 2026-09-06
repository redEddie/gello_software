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

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from apps.workspace.features.scene.dialogs.plan_edit_dialog import PlanEditDialog  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="planform_"))
# 실제 계획은 데이터셋 폴더로 옮겼고 리포에는 포맷 문서용 example.json 만
# 남는다 (2026-09-04). 폼 테스트는 그 사본으로 돌린다.
plan_copy = TMP / "pilot.json"
shutil.copy(f"{WT}/configs/collection/plans/example.json", plan_copy)
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
# 계획은 데이터셋 폴더 안 instructions.json 에 귀속 -- 저장 경로를 그쪽으로
DS = Path(tempfile.mkdtemp(prefix="planform_ds_"))
shutil.copy(plan_copy, DS / "instructions.json")
win.root_edit.setText(str(DS))
win.scene_ops.refresh_scene_combo()
# scene 파일이 수집 세션에 잠겨 있어도 돌 수 있게 scene 선택을 주입한다
win.scene_ops.configure_scene_id = lambda: "S000"
win.scene_ops.selected_scene_path = lambda: None
# 계획 문장 드롭다운은 없어졌다 (2026-09-06) -- 고르는 자리는 Instruction
# 탭이고, Configure 는 고른 결과를 읽기 전용 한 줄로 보여줄 뿐이다.
win.scene_planning.refresh_start_instruction()
plan = win.scene_planning.current_plan()
n_slots = len(plan.slots_for("S000"))
assert n_slots >= 1
assert win.start_warn.text() == "", win.start_warn.text()
sl = plan.slots_for("S000")[0]
win.scene_iid_edit.setText(sl.instruction_id)
win.lang_edit.setText(sl.instruction)
print(f"5 통과: 계획 지시문 {n_slots}개, 시작 지시문 한 줄 표시")

# ---- 6. 시작 지시문은 손으로 못 친다 + 계획 밖 문장은 연결 거부 ----
assert win.lang_edit.isReadOnly() and win.scene_iid_edit.isReadOnly()
win.collector_edit.setText("t")
win.lang_edit.setText("open the top drawer")     # 계획에 없는 문장 (주입)
win.scene_iid_edit.setText("I009")
_, _, _, err = win.scene_ops.scene_config_from_ui()
assert err and "계획" in err, err
win.scene_iid_edit.setText(sl.instruction_id)     # 계획의 지시문으로 복귀
win.lang_edit.setText(sl.instruction)
_, _, _, err2 = win.scene_ops.scene_config_from_ui()
assert err2 is None or "계획" not in err2, err2   # 남는 오류는 scene 선택뿐
print("6 통과: 시작 지시문 읽기 전용 + 계획 밖 문장 거부")

# ---- 6b. 계획이 아예 없으면 연결 자체를 막는다 (2026-09-06) ----
import os as _os  # noqa: E402

_plan_file = DS / "instructions.json"
_saved = _plan_file.read_text(encoding="utf-8")
_plan_file.unlink()
try:
    _, _, _, err3 = win.scene_ops.scene_config_from_ui()
    assert err3 and "수집 계획이 없습니다" in err3, err3
    win.scene_planning.refresh_start_instruction()
    assert "계획이 없습니다" in win.start_warn.text(), win.start_warn.text()
finally:
    _plan_file.write_text(_saved, encoding="utf-8")
    _os.sync() if hasattr(_os, "sync") else None
win.scene_planning.refresh_start_instruction()
print("6b 통과: 계획 없이는 수집할 수 없다 (연결 거부 + 화면 경고)")

# ---- 7. 계획 파일 새로 만들기 / 삭제 (데이터셋 폴더 안 instructions.json) ----
cw.QMessageBox.question = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
# 모달 편집이 뜨지 않게 대화상자만 갈아 끼운다 -- on_edit_plan 자체를
# 가짜로 두면 안 된다: 계획을 **만드는** 것이 이제 그 안에 있다 (2026-09-06).
from apps.workspace.features.scene import planning as _planning  # noqa: E402


class _NoDialog:
    warnings: list = []

    def __init__(self, *a, **k) -> None:
        pass

    def exec(self):
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Rejected


_planning.PlanEditDialog = _NoDialog
DS2 = Path(tempfile.mkdtemp(prefix="planform_ds2_"))
win.root_edit.setText(str(DS2))
win.scene_ops.refresh_scene_combo()
new_path = DS2 / "instructions.json"
try:
    # [새 계획] 버튼은 없어졌다 -- 편집이 없으면 만든다 (같은 동작).
    win.scene_planning.on_edit_plan()
    assert new_path.exists()
    assert json.loads(new_path.read_text())["scenes"] == []
    # Configure 의 계획 라벨은 없어졌다 (2026-09-06) -- 지시문이
    # instructions.json 하나로 통일된 뒤로 파일 이름을 화면에 적을 이유가
    # 없다. 계획이 로드됐는지는 계획 객체로 본다.
    assert win.scene_planning.current_plan() is not None
    win.scene_planning.on_delete_plan()
    assert not new_path.exists()
    assert win.scene_planning.current_plan() is None
finally:
    new_path.unlink(missing_ok=True)
print("7 통과: 계획 파일 생성(데이터셋 폴더) / 삭제(+표시 갱신)")

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
