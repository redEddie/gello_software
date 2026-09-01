"""RecommendDialog 문장 체크리스트 + 계획 등록, NewSceneDialog lint (offscreen)."""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from tests.gui.helpers import _wait_recs  # noqa: E402

import collect_workspace as cw  # noqa: E402
from gello.scene.props import props_by_id  # noqa: E402
from gello.scene.scene_format import SceneMetadata  # noqa: E402

cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
cw.QMessageBox.information = staticmethod(lambda *a, **k: None)
cw.QMessageBox.question = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)

props = props_by_id()
base = SceneMetadata(
    scene_id="S000",
    objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"],
    layout={"grid": [3, 3], "placements": {
        "OBJ-CUP-BLU-01": {"zone": [0, 0]},
        "OBJ-BOWLS-WHT-01": {"zone": [1, 1]}}})

TMP = Path(tempfile.mkdtemp(prefix="recreg_"))
plan_copy = TMP / "pilot.json"
shutil.copy(f"{WT}/configs/collection/plans/pilot.json", plan_copy)
orig = json.loads(plan_copy.read_text())

# ---- 1. RecommendDialog: 문장 체크리스트 표시 + 전체 기본 선택 ----
rdlg = cw.RecommendDialog(None, [base], props, "S999", plan_path=plan_copy)
_wait_recs(rdlg)
assert len(rdlg._radios) == 3
idx = 0
sents = rdlg._sentence_checks[idx]
assert len(sents) >= 1, "추천 문장 체크리스트가 비어 있음"
assert all(cb.isChecked() for cb in sents), "문장은 기본적으로 선택되어야 함"
print(f"1 통과: 추천 문장 체크리스트 ({len(sents)}개)")

# ---- 2. 계획 등록: 선택 문장이 plan 파일에 추가됨 ----
rdlg._accept()
assert rdlg.registered_plan_path == plan_copy
plan = json.loads(plan_copy.read_text())
s999 = [s for s in plan["scenes"] if s["scene_id"] == "S999"]
assert s999, "S999 scene 이 생성됨"
added = s999[0]["slots"]
assert len(added) == len(sents)
assert added[0]["target"] == 10
assert added[0]["instruction_id"].startswith("I")
print(f"2 통과: 계획 등록 {len(added)}개 슬롯 (target=10, ID 자동)")

# ---- 3. 등록 검증 게이트: load_plan 을 통과해야 함 ----
from gello.scene.collection_plan import load_plan  # noqa: E402
loaded = load_plan(plan_copy)
assert loaded.scene("S999") is not None
print("3 통과: 등록된 계획 load_plan 검증 통과")

# ---- 4. NewSceneDialog lint: 규칙 위반 시 경고 표시 ----
nd = cw.NewSceneDialog(None, "S100")
# 위반 배치: 흰 컵 2개 + drawer 중앙
nd.prop_list.blockSignals(True)
for i in range(nd.prop_list.count()):
    it = nd.prop_list.item(i)
    oid = it.data(cw.Qt.ItemDataRole.UserRole)
    if oid in {"OBJ-CUP-WHT-01", "OBJ-CUP-WHT-02", "OBJ-DRAWER-01"}:
        it.setCheckState(cw.Qt.CheckState.Checked)
nd.prop_list.blockSignals(False)
nd._placements = {
    "OBJ-CUP-WHT-01": [0, 0],
    "OBJ-CUP-WHT-02": [0, 1],
    "OBJ-DRAWER-01": [1, 1],
}
nd._refresh()
lint_text = nd.lint_label.text()
assert "no_lookalike_pair" in lint_text or "color_diverse" in lint_text, lint_text
assert "ban_zones" in lint_text, lint_text
print("4 통과: NewSceneDialog 규칙 위반 경고")

# ---- 5. NewSceneDialog lint: 규칙 통과 시 경고 없음 ----
nd2 = cw.NewSceneDialog(None, "S101")
nd2.prop_list.blockSignals(True)
for i in range(nd2.prop_list.count()):
    it = nd2.prop_list.item(i)
    if it.data(cw.Qt.ItemDataRole.UserRole) in {
            "OBJ-CUP-WHT-01", "OBJ-CUP-BLU-01",
            "OBJ-BOWLS-WHT-01", "OBJ-BOWLS-BLU-01"}:
        it.setCheckState(cw.Qt.CheckState.Checked)
nd2.prop_list.blockSignals(False)
# pair_if_present(2026-08-24 확정): 등장하는 category 는 2개 이상(색 다름)
# -- 컵 2색 + small bowl 2색으로 충족
nd2._placements = {"OBJ-CUP-WHT-01": [0, 0], "OBJ-CUP-BLU-01": [1, 0],
                   "OBJ-BOWLS-WHT-01": [0, 1], "OBJ-BOWLS-BLU-01": [2, 2]}
nd2._refresh()
assert nd2.lint_label.text() == "", nd2.lint_label.text()
# 컵 1 + 그릇 1 이면 pair_if_present 경고가 떠야 한다 (shortcut 방지)
nd3 = cw.NewSceneDialog(None, "S102")
nd3.prop_list.blockSignals(True)
for i in range(nd3.prop_list.count()):
    it = nd3.prop_list.item(i)
    if it.data(cw.Qt.ItemDataRole.UserRole) in {"OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"}:
        it.setCheckState(cw.Qt.CheckState.Checked)
nd3.prop_list.blockSignals(False)
nd3._placements = {"OBJ-CUP-BLU-01": [0, 0], "OBJ-BOWLS-WHT-01": [0, 1]}
nd3._refresh()
assert "pair_if_present" in nd3.lint_label.text(), nd3.lint_label.text()
print("5 통과: 규칙 통과 시 경고 없음 + 단일 개체 조합엔 shortcut 경고")

# ---- 6. 문법 lint 자기일관성 + 계획 lint 는 경고로만 ----
# 계획 파일은 실사용 중 계속 바뀌고, 사람이 쓴 문장이 통일 문법 밖이어도
# lint 는 '경고'다 (차단 아님 -- load_plan 설계). 여기서는
# (a) 문법이 스스로 생성한 문장은 전부 lint 통과 (자기일관성),
# (b) 계획 전 문장에 lint 가 예외 없이 돌아간다(경고 수만 보고)를 검증한다.
from gello.scene.instruction_grammar import lint  # noqa: E402
for cb in sents:                          # 추천 체크리스트 = 문법이 생성한 문장
    assert lint(cb.text()) is None, (cb.text(), lint(cb.text()))
plan = json.loads(Path(f"{WT}/configs/collection/plans/pilot.json").read_text())
n_warn = 0
total = 0
for sc in plan["scenes"]:
    for sl in sc["slots"]:
        total += 1
        if lint(sl["instruction"]):
            n_warn += 1
# 2026-08-24 정본 문법 확정 + 전 데이터 교정 이후로는 계획 전체가 통과해야
# 한다 -- 경고가 생기면 새 문장이 정본 밖이라는 뜻 (문법 확장 또는 문장 수정).
assert n_warn == 0, f"정본 문법 밖 문장 {n_warn}개 -- lint 경고 확인"
print(f"6 통과: 생성 문장 자기일관성 + 계획 {total}개 문장 전부 정본 문법 통과")

print("\nRecommendDialog 문장/등록 + NewSceneDialog lint 검증 통과")
import os  # noqa: E402

os._exit(0)
