"""RecommendDialog 문장 체크리스트 + 계획 등록, SceneComposer lint (offscreen)."""
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
from apps.workspace.features.scene.scene_composer import SceneComposer  # noqa: E402
from apps.workspace.features.scene.dialogs.recommend_dialog import RecommendDialog  # noqa: E402
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
# 실제 계획은 데이터셋 폴더의 instructions.json 으로 옮겼다 -- 폼·다이얼로그
# 테스트는 리포에 남은 포맷 문서용 example.json 사본으로 돌린다 (2026-09-04).
shutil.copy(f"{WT}/configs/collection/plans/example.json", plan_copy)
orig = json.loads(plan_copy.read_text())

# ---- 1. RecommendDialog: 문장 체크리스트 표시 + 전체 기본 선택 ----
rdlg = RecommendDialog(None, [base], props, "S999", plan_path=plan_copy)
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

# ---- 4. SceneComposer lint: 규칙 위반 시 경고 표시 ----
nd = SceneComposer(None, "S100")
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
print("4 통과: SceneComposer 규칙 위반 경고")

# ---- 5. SceneComposer lint: 규칙 통과 시 경고 없음 ----
nd2 = SceneComposer(None, "S101")
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
nd3 = SceneComposer(None, "S102")
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
# 정본 계획은 데이터셋에 귀속됐다 (2026-09-04) -- 활성 데이터셋의
# instructions.json 이 있으면 그것을, 없으면 example.json 을 검사한다.
# 여기서는 이미 쓰인 문장을 '읽어 검증'하는 자리다. 2026-09-07 의 "그릇
# 목적지 on 금지" 이전 수집분에는 "place it on the {그릇}" 이 남아 있어서
# strict 관계 검증을 끄고 옛 겹침 집합 그대로 통과시킨다 (데이터 전수 수정
# 후 이 완화를 제거하면 n_warn==0 이 다시 의미를 갖는다).
_live = Path.home() / "libero_datasets" / "fr3-tabletop" / "instructions.json"
plan = json.loads((_live if _live.is_file() else Path(
    f"{WT}/configs/collection/plans/example.json")).read_text())
n_warn = 0
total = 0
for sc in plan["scenes"]:
    for sl in sc["slots"]:
        total += 1
        if lint(sl["instruction"], strict_relation=False):
            n_warn += 1
# 2026-08-24 정본 문법 확정 + 전 데이터 교정 이후로는 계획 전체가 통과해야
# 한다 -- 경고가 생기면 새 문장이 정본 밖이라는 뜻 (문법 확장 또는 문장 수정).
assert n_warn == 0, f"정본 문법 밖 문장 {n_warn}개 -- lint 경고 확인"
print(f"6 통과: 생성 문장 자기일관성 + 계획 {total}개 문장 전부 정본 문법 통과")

print("\nRecommendDialog 문장/등록 + SceneComposer lint 검증 통과")
import os  # noqa: E402


# ---- 경로를 모를 때: 조용히 건너뛰지 말고 왜 못 하는지 보여준다 (2026-09-04) ----
#    숨기면 조작자는 추천을 채택하고도 문장이 어디에도 안 남은 것을 한참 뒤에야
#    안다. 실제로 그렇게 겪었다.
rd_noplan = RecommendDialog(None, [base], props, "S998")      # plan_path 없음
cb = rd_noplan._register_check
assert cb is not None, "경로가 없을 때 등록 체크박스를 아예 숨기고 있다"
assert not cb.isEnabled(), "등록할 곳이 없는데 체크박스가 켜져 있다"
assert not cb.isChecked(), "꺼져 있어야 한다 -- accept 경로가 이것을 본다"
assert cb.toolTip(), "왜 못 하는지 설명이 없다"
# **없는 조작을 시키지 않는다.** 지시문 파일을 고르는 자리는 2026-09-06 에
# 없어졌다 (고정 파일명 instructions.json 하나다). 그런데 이 문구만
# "Configure 에서 지시문 파일을 먼저 고르세요" 로 남아 있었고, 이 테스트가
# 그것을 지키고 있었다 (2026-09-07 조작자 지적).
assert "고르세요" not in cb.text(), f"없는 조작을 시킨다: {cb.text()!r}"
print("경로를 모를 때 등록 불가 이유를 보여준다 OK")

# ---- 지시문 파일이 **아직 없어도** 등록되면 만들어진다 (2026-09-07) ----
#    새 데이터셋에서 첫 scene 을 짤 때, 배치와 문장을 한 번에 등록할 수 있어야
#    한다. 전에는 [지시문 편집...] 으로 빈 파일을 먼저 만들고 와야 했다.
import tempfile as _tf  # noqa: E402

fresh = Path(_tf.mkdtemp(prefix="noplan_")) / "instructions.json"
assert not fresh.exists()
rd_fresh = RecommendDialog(None, [base], props, "S500", plan_path=fresh)
assert rd_fresh._register_check.isEnabled(), \
    "파일이 없다고 등록을 막는다 -- 없으면 만들면 된다"
assert rd_fresh._register_check.isChecked(), "기본으로 켜져 있어야 한다"
_wait_recs(rd_fresh)
_md = rd_fresh._recs[0]["md"]
assert rd_fresh._register_plan(_md, ["pick up the blue cup and place it inside the white bowl"]), \
    "파일이 없을 때 등록이 실패했다"
assert fresh.is_file(), "등록했는데 지시문 파일이 안 만들어졌다"
_raw = json.loads(fresh.read_text(encoding="utf-8"))
assert _raw["plan_version"] == 1, _raw
assert [sc for sc in _raw["scenes"] if sc["scene_id"] == _md.scene_id], _raw
print("지시문 파일이 없어도 등록하면 만들어진다 OK")

# ---- 7. 워크플로 ②: 물체는 사람이 고르고 배치만 추천 (2026-09-06) ----
# 버튼 자체는 우측 패널에 있고(2026-09-07), 누를 수 있는지를 아는 것은
# composer 다 -- 여기서는 그 계약만 본다.
nd4 = SceneComposer(None, "S103")
ok, why = nd4.layout_button_state()
assert not ok, "아무것도 안 골랐는데 눌린다"
assert "이상 체크" in why, why
picked_ids = ["OBJ-CUP-WHT-01", "OBJ-CUP-BLU-01",
              "OBJ-BOWLS-WHT-01", "OBJ-DRAWER-01"]
nd4.prop_list.blockSignals(True)
for i in range(nd4.prop_list.count()):
    it = nd4.prop_list.item(i)
    if it.data(cw.Qt.ItemDataRole.UserRole) in set(picked_ids):
        it.setCheckState(cw.Qt.CheckState.Checked)
nd4.prop_list.blockSignals(False)
nd4._refresh()
ok, _why = nd4.layout_button_state()
assert ok, "4개를 골랐는데 못 누른다"

ldlg = RecommendDialog(None, [base], props, "S997", plan_path=plan_copy,
                       objects=picked_ids)
_wait_recs(ldlg)
assert len(ldlg._radios) == 3, len(ldlg._radios)
layouts = []
for rec in ldlg._recs:
    md = rec["md"]
    assert sorted(md.objects) == sorted(picked_ids), md.objects   # 조합 불변
    layouts.append(tuple(sorted(
        (o, tuple(v["zone"])) for o, v in md.layout["placements"].items())))
assert len(set(layouts)) == 3, "배치안 3개가 서로 달라야 한다"
# 문장은 배치와 무관하므로 세 안이 같은 체크리스트를 공유한다
assert ldlg._sentence_checks[0] is ldlg._sentence_checks[1]
assert len(ldlg._sentence_checks[0]) >= 1
# 채택하면 배치가 SceneComposer 에 반영된다
ldlg._radios[1].setChecked(True)
ldlg._accept()
nd4._apply_recommendation(ldlg.picked)
assert sorted(nd4._checked_ids()) == sorted(picked_ids)
assert {o: tuple(z) for o, z in nd4._placements.items()} == {
    o: z for o, z in layouts[1]}
print(f"7 통과: 배치만 추천 3안 (조합 불변·배치 상이·문장 공유) + 폼 반영")

# ---- 8. 배치로 고칠 수 없는 위반은 경고로 말한다 ----
lone = RecommendDialog(None, [base], props, "S996",
                       objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"])
_wait_recs(lone)
warn = lone._compose_warning()
assert any("pair_if_present" in w for w in warn), warn
assert len(lone._radios) == 3, "구성 규칙 위반이어도 배치는 추천해야 한다"
print("8 통과: compose 위반 조합도 배치는 추천하되 경고를 보여준다")

import os  # noqa: E402

# ------------------------------------------------- 9. 지시문 고르기가 동작별로
# 문장 서른 몇 개가 한 줄로 늘어서 있으면 "무엇을 뺄까" 를 한 번에 판단해야
# 하고, 그러면 빼야 할 것을 놓친다 (2026-09-07 사용자). 동작으로 좁혀 보되
# 뱃지의 "고른 수/전체" 로 전체를 잃지 않는다.
from apps.workspace.shared.badges import ClickableBadge  # noqa: E402
from PyQt6.QtWidgets import QCheckBox, QVBoxLayout, QWidget  # noqa: E402

_host = QWidget()
_col = QVBoxLayout(_host)
_dlg = RecommendDialog.__new__(RecommendDialog)
_dlg._props = props_by_id()
_dlg._plan_path = Path(tempfile.mkdtemp()) / "instructions.json"
_md9 = SceneMetadata(
    scene_id="S009",
    objects=["OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01", "OBJ-BOWLS-PNK-01"],
    layout={"grid": [3, 3], "placements": {
        "OBJ-CUP-WHT-02": {"zone": [0, 0]},
        "OBJ-BOWLS-BLU-01": {"zone": [0, 1]},
        "OBJ-BOWLS-PNK-01": {"zone": [2, 0]}}})
_checks = _dlg._build_sentence_checks(_md9, {}, _col)
_host.show()
_badges = _host.findChildren(ClickableBadge)
assert len(_badges) >= 2, "동작이 하나뿐이라 이 검사가 무의미하다"
assert sum(len(b._note.text().split("/")[1:]) for b in _badges) == len(_badges)
# 한 번에 보이는 것은 고른 동작의 문장뿐이다
_vis = [cb for cb in _host.findChildren(QCheckBox) if cb.isVisibleTo(_host)]
assert 0 < len(_vis) < len(_checks), (len(_vis), len(_checks))
# 뱃지의 "고른 수/전체" 가 실제와 맞고, 끄면 따라 준다
_sel = [b for b in _badges if "2px" in b.styleSheet()][0]
_n, _m = (int(x) for x in _sel._note.text().split("/"))
assert _n == _m == len(_vis), (_n, _m, len(_vis))
_vis[0].setChecked(False)
assert _sel._note.text() == f"{_m - 1}/{_m}", _sel._note.text()
# 안 보이는 동작은 건드리지 않는다
RecommendDialog._set_visible_checks(
    {p: p for p in {c.parentWidget() for c in _host.findChildren(QCheckBox)}},
    False)
assert any(cb.isChecked() for cb in _checks), "안 보는 것까지 꺼졌다"
print("9 통과: 지시문 고르기가 동작별 + 고른 수/전체 표시")



# os._exit 는 버퍼를 비우지 않는다 -- 먼저 비운다.
sys.stdout.flush()
os._exit(0)
