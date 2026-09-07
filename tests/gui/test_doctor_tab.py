"""기록 닥터(Doctor 활동탭) 인수 테스트.

합성 scene 으로 S008 과 같은 오등록을 만들어 두고, 화면이 그것을 찾아
보여주고 고치는가를 본다. 로봇도 카메라도 24GB 데이터셋도 필요 없다.

  1. 활동탭이 Dataset 다음에 있고, 열면 중앙이 기록 닥터다
  2. 문제 있는 scene 이 굵게, 문제 수가 **task 수**로 보인다
  3. scene 을 고르면 기준 사진과 기록이 함께 채워진다
  4. 오등록 제안 줄이 보이고, 적용하면 실제로 고쳐지고 줄이 사라진다
  5. 수집 중에는 검사하지 않는다
  6. 화면에 "slot" 이라는 낱말이 없다
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QCheckBox,
    QGroupBox,
    QLabel,
    QPushButton,
)

TASKS = [
    ("I000", "pick up the white cup and place it inside the small blue bowl", 3),
    ("I001", "pick up the white cup and place it inside the small gray bowl", 2),
]
OBJECTS = ["OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01", "OBJ-BOWLS-GRN-01"]
ZONES = {"OBJ-CUP-WHT-02": [0, 0], "OBJ-BOWLS-BLU-01": [0, 1],
         "OBJ-BOWLS-GRN-01": [2, 0]}


def _make_dataset(root: Path) -> None:
    with h5py.File(root / "scene_000.hdf5", "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["scene_id"] = "S000"
        meta.attrs["objects"] = json.dumps(OBJECTS)
        meta.attrs["layout"] = json.dumps(
            {"grid": [3, 3],
             "placements": {o: {"zone": z} for o, z in ZONES.items()}})
        meta.attrs["station"] = "knu-eng7"
        meta.attrs["dataset_version"] = "knu-1.0.0"
        meta.attrs["created"] = "2026-08-22T16:35:57+09:00"
        # 기준 사진이 있어야 "사진과 기록을 나란히" 를 검사할 수 있다.
        meta.create_dataset(
            "reference_image",
            data=np.full((48, 64, 3), 120, dtype=np.uint8))
        i = 0
        for iid, text, n in TASKS:
            for _ in range(n):
                g = f.create_group(f"episode_{i:03d}")
                g.attrs["scene_id"] = "S000"
                g.attrs["instruction_id"] = iid
                g.attrs["instruction"] = text
                g.attrs["episode_uid"] = f"S000-{iid}-E{i:03d}"
                g.create_dataset("actions", data=np.zeros((2, 7), np.float32))
                i += 1
    (root / "instructions.json").write_text(json.dumps({
        "plan_version": 1,
        "scenes": [{"scene_id": "S000",
                    "slots": [{"instruction_id": iid, "instruction": t,
                               "target": n} for iid, t, n in TASKS]}],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


#: 안 찍은 빈 칸에 들어 있는 "옳게 적힌" 문장 (S016 의 I006/I007 자리).
GOOD = "drag the white cup next to the small gray bowl"


def _add_empty_task(root: Path) -> None:
    """계획에만 있고 에피소드가 없는 지시문을 하나 넣는다."""
    p = root / "instructions.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["scenes"][0]["slots"].append(
        {"instruction_id": "I009", "instruction": GOOD, "target": 2})
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def _card_text(card) -> str:
    """InfoCard 안의 모든 라벨 글을 이어 붙인다 (배치도 셀 포함)."""
    return " ".join(w.text() for w in card.findChildren(QLabel))


def main() -> None:
    from apps.workspace.constants import ACTIVITIES, CENTER_TABS_BY_ACTIVITY

    keys = [a[0] for a in ACTIVITIES]
    assert keys.index("doctor") == keys.index("dataset") + 1, keys
    assert CENTER_TABS_BY_ACTIVITY["doctor"][0] == "doc_record"
    print("1. 활동탭 자리와 중앙 탭 OK")

    import apps.collect_workspace as cw
    from apps.workspace.shared.tabs import center_tab_key

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_dataset(root)
        win = cw.WorkspaceWindow(None)
        win.root_edit.setText(str(root))
        win._set_activity("doctor")
        assert center_tab_key(win) == "doc_record", center_tab_key(win)

        win.doctor.rescan()
        tree = win.doctor_tree
        assert tree.topLevelItemCount() == 1, tree.topLevelItemCount()
        it = tree.topLevelItem(0)
        # 문제는 task 수다 -- gray 를 말하는 지시문 1건이지 에피소드 2개가 아니다
        assert it.text(0) == "S000" and it.text(1) == "5", (it.text(0), it.text(1))
        assert it.text(2).startswith("1"), it.text(2)
        assert it.font(0).bold(), "문제 있는 scene 이 굵지 않다"
        print("2. 문제 수가 task 단위 OK")

        win.doctor.select_scene("S000")
        assert win.doctor_photo.pixmap() is not None
        assert not win.doctor_photo.pixmap().isNull(), "기준 사진이 안 붙었다"
        assert "OBJ-BOWLS-GRN-01" in win.doctor_info.text(), win.doctor_info.text()
        assert win.doctor_task_tree.topLevelItemCount() == 2
        print("3. 사진과 기록이 나란히 OK")

        # 소품 고치기 -- 추천이 미리 골라져 있고, 지금/고친 뒤가 나란히
        assert win.doctor._suggestion is not None, "제안이 없다"
        sug = win.doctor._suggestion
        assert (sug.old_id, sug.new_id) == ("OBJ-BOWLS-GRN-01",
                                            "OBJ-BOWLS-GRY-01"), sug
        import apps.workspace.features.doctor.ops as dops
        grab = {}
        real_obj = dops.ObjectDialog

        class _AutoObj(real_obj):
            def exec(self):
                grab["changes"] = self._picked()
                # 배치도는 이제 위젯이다 (ZoneMap) -- 글이 아니라 셀을 읽는다
                grab["now"] = _card_text(self._now)
                grab["after"] = _card_text(self._after)
                grab["note"] = self.note.text()
                self.accept()
                return int(QDialog.DialogCode.Accepted)

        dops.ObjectDialog = _AutoObj
        try:
            win.doctor.edit_objects()
        finally:
            dops.ObjectDialog = real_obj
        # 추천이 콤보에 미리 골라져 있으므로 누르기만 해도 그 정정이 담긴다
        assert grab["changes"] == {"OBJ-BOWLS-GRN-01": "OBJ-BOWLS-GRY-01"}, grab
        # 지금과 고친 뒤가 서로 다르고, 각각 옛/새 물체를 담는다
        assert "GRN" in grab["now"], grab["now"]
        assert "GRY" in grab["after"], grab["after"]
        assert grab["now"] != grab["after"]
        # 위반이 줄어드는 것을 누르기 전에 말한다
        assert "건 → " in grab["note"], grab["note"]

        with h5py.File(root / "scene_000.hdf5", "r") as f:
            objs = json.loads(f["metadata"].attrs["objects"])
            lay = json.loads(f["metadata"].attrs["layout"])
            assert int(f["metadata"].attrs.get("edit_count", 0)) == 0, \
                "기록 정정이 edit_count 를 올렸다 (이어붙이기를 막는다)"
        assert "OBJ-BOWLS-GRY-01" in objs and "OBJ-BOWLS-GRN-01" not in objs, objs
        assert lay["placements"]["OBJ-BOWLS-GRY-01"]["zone"] == [2, 0], lay
        assert win.doctor._suggestion is None, "고쳤는데 제안이 남아 있다"
        assert win.doctor_tree.topLevelItem(0).text(2) == "—", "문제가 안 사라졌다"
        print("4. 정정 적용 OK")

        # 5. 수집 중에는 검사하지 않는다
        win.worker = object()
        try:
            win.doctor.rescan()
            assert win.doctor_tree.topLevelItemCount() == 0
            assert "수집 중에는" in win.doctor_hint.text(), win.doctor_hint.text()
        finally:
            win.worker = None
        print("5. 수집 중 비검사 OK")

        # 6. "slot" 없음 -- 왼쪽 패널 + 중앙 탭 둘 다
        bad = []
        for page in (win.left_stack.widget(win.left_pages["doctor"]),
                     win.center_tab_widgets["doc_record"]):
            for kind in (QLabel, QPushButton, QGroupBox, QCheckBox):
                for wdg in page.findChildren(kind):
                    t = wdg.title() if isinstance(wdg, QGroupBox) else wdg.text()
                    if "slot" in t.lower():
                        bad.append(t)
        assert not bad, f"화면에 'slot' 이 남아 있다: {bad}"
        print("6. 화면에 'slot' 없음 OK")

        # 7. 우측 패널이 닥터 자기 페이지다 (공용 상자를 고르는 게 아니다)
        from apps.workspace.shell.right_builders import RIGHT_BUILDERS

        assert "doctor" in RIGHT_BUILDERS, "닥터가 자기 우측 페이지를 안 가졌다"
        doctor_idx = win.right_pages["doctor"]
        session_idx = win.right_pages["collect"]
        assert doctor_idx != session_idx, "닥터가 세션 페이지를 쓰고 있다"
        # 아직 자기 것이 없는 활동은 세션 페이지를 함께 쓴다
        assert win.right_pages["layout"] == session_idx
        win._set_activity("doctor")
        assert win.right_stack.currentIndex() == doctor_idx
        # Dataset 이 아니라 Settings 로 나간다 -- Dataset 은 들어가면서 통계
        # 분석을 돌리는데, 이 합성 파일에는 그 필드가 없다 (이 테스트의
        # 관심사가 아니다).
        win._set_activity("settings")
        assert win.right_stack.currentIndex() == session_idx
        win._set_activity("doctor")

        # 고른 것이 없으면 조치 버튼이 전부 꺼져 있다
        assert not any(b.isEnabled() for b in win.doctor_task_buttons.values())
        # scene 범위 조치는 scene 을 고른 것만으로 쓸 수 있다
        assert all(b.isEnabled() for b in win.doctor_scene_buttons.values())
        tt = win.doctor_task_tree
        win.doctor.on_task_picked(tt.topLevelItem(0))
        assert "I000" in _card_text(win.doctor_task_card)
        assert win.doctor_task_buttons["edit_task_text"].isEnabled()
        # 에피소드가 있는 줄은 [계획에서 빼기] 가 꺼져 있다
        assert not win.doctor_task_buttons["remove_task"].isEnabled()
        # 파일 상태 칸: 기록 정정만 했으므로 편집 이력이 없다
        assert "S000" in _card_text(win.doctor_scene_card)
        # 칸은 늘 있다 -- 값이 없으면 "없음" 이라고 적지 숨기지 않는다
        assert win.doctor_scene_diag.text(), "진단 칸이 비었다"
        assert win.doctor_scene_cost.text(), "대가 칸이 비었다"
        assert "편집" not in _card_text(win.doctor_scene_card) or \
            "없음" in _card_text(win.doctor_scene_card)
        print("7. 우측 패널 = 닥터 자기 페이지 OK")

        # 8. S016 의 해법 -- 안 찍은 빈 칸과 교환하고, 그 칸을 계획에서 뺀다
        _add_empty_task(root)
        win.doctor.select_scene("S000")
        # 안 찍은 빈 칸도 줄로 보인다 -- 고칠 재료가 화면에 있어야 한다
        # (S016 에서 8개 중 6개만 보이던 문제).
        rows = {tt.topLevelItem(i).text(0): tt.topLevelItem(i).text(1)
                for i in range(tt.topLevelItemCount())}
        assert rows == {"I000": "3", "I001": "2", "I009": "0"}, rows
        assert tt.topLevelItem(2).text(3) == "빈 칸", tt.topLevelItem(2).text(3)
        assert "빈 칸" in win.doctor_task_hint.text(), win.doctor_task_hint.text()
        win.doctor.on_task_picked(tt.topLevelItem(1))       # I001 (2개, gray)
        # 대화상자를 띄우는 대신 실제 위젯을 만들어, 화면이 그리는 '바꾼 뒤'
        # 가 맞는지까지 본다 -- 이 대화상자의 존재 이유가 그 미리보기다.
        seen = {}
        real = dops.SwapDialog

        class _Auto(real):
            def exec(self):
                # 안 찍은 빈 칸(I009)을 고른다.
                for i in range(self.combo.count()):
                    if self.combo.itemData(i) == "I009":
                        self.combo.setCurrentIndex(i)
                        break
                else:
                    raise AssertionError("교환 후보에 빈 칸 I009 가 없다")
                seen["after_mine"] = self._a.text()
                seen["after_other"] = self._b.text()
                seen["note"] = self.note.text()
                self._accept()
                return int(QDialog.DialogCode.Accepted)

        dops.SwapDialog = _Auto
        try:
            win.doctor.swap_task_text()
        finally:
            dops.SwapDialog = real
        # 미리보기가 실제 결과와 같아야 한다 (내 줄은 상대 문장을 받는다).
        assert seen["after_mine"] == GOOD, seen
        assert seen["after_other"] == TASKS[1][1], seen
        assert "2" in seen["note"], seen["note"]
        plan = json.loads((root / "instructions.json").read_text(encoding="utf-8"))
        by = {s["instruction_id"]: s["instruction"]
              for s in plan["scenes"][0]["slots"]}
        assert by["I001"] == GOOD, by["I001"]
        assert by["I009"] == TASKS[1][1], by["I009"]
        with h5py.File(root / "scene_000.hdf5", "r") as f:
            got = {str(f[k].attrs["instruction"]) for k in f
                   if k.startswith("episode")
                   and str(f[k].attrs["instruction_id"]) == "I001"}
        assert got == {GOOD}, got
        print("8. 빈 칸과 교환 OK")

        # 9. 문장이 바뀌었으니 파일 상태가 그것을 말한다 (전체 재빌드 경고)
        win.doctor.select_scene("S000")
        # 편집 횟수는 값 칸, 그 대가는 "고치면" 칸 -- 섞지 않는다
        facts = _card_text(win.doctor_scene_card)
        assert "1회" in facts, facts
        assert "재변환" not in facts, facts
        assert "재변환" in win.doctor_scene_cost.text()
        print("9. 편집 이력 표시 OK")

        # 10. 문장 고치기는 블럭 조립이다 -- 문법이 만든 것만 고를 수 있다
        import apps.workspace.features.doctor.sentence_builder as sb

        win.doctor.select_scene("S000")
        win.doctor.on_task_picked(tt.topLevelItem(0))       # I000
        grabbed = {}
        real_dlg = dops.SentenceDialog

        class _Grab(real_dlg):
            def exec(self):
                grabbed["skills"] = sorted(self._index)
                grabbed["badges"] = sorted(self._badges)
                # 뱃지를 눌러 스킬을 바꾸면 문장 목록이 그것만 남는다
                target = "drag-next_to"
                self._pick_skill(target)
                # 블럭이 셋이다: 동작 · 무엇을 · 어디에
                grabbed["src"] = list(self._src_badges)
                grabbed["dst"] = list(self._dst_badges)
                grabbed["chosen"] = self._chosen_sentence
                # 못 고르는 뱃지는 지우지 않고 취소선 + 사유 툴팁
                grabbed["off"] = {k: b.toolTip()
                                  for k, b in self._dst_badges.items()
                                  if b._off}
                self._accept()
                return int(QDialog.DialogCode.Accepted)

        dops.SentenceDialog = _Grab
        try:
            win.doctor.edit_task_text()
        finally:
            dops.SentenceDialog = real_dlg
        assert grabbed["badges"] == grabbed["skills"], grabbed
        assert "pick-inside" in grabbed["skills"], grabbed["skills"]
        # 그릇 목적지 'on' 은 문법이 아예 만들지 않는다
        assert "pick-on" not in grabbed["skills"], grabbed["skills"]
        assert grabbed["chosen"].startswith("drag "), grabbed
        # 축이 나뉘어 있어 뱃지 수가 조합이 아니라 합이다
        assert all(x.startswith("the ") for x in grabbed["src"]), grabbed
        assert all(x.startswith("the ") for x in grabbed["dst"]), grabbed
        # 고른 동작에 맞는 짝만 남는다
        from apps.workspace.features.doctor.sentence_builder import _split
        src, dst = _split(grabbed["chosen"])
        assert src in grabbed["src"] and dst in grabbed["dst"], (src, dst, grabbed)
        # 이미 쓰이는 조합은 남아 있되 사유가 붙는다 (사라지면 왜 없는지 모른다)
        for name, why in grabbed["off"].items():
            assert why, f"취소선인데 사유가 없다: {name}"
        # 뜻으로 대조한다 -- 어순이 달라도 같은 문장이면 잡힌다
        from apps.workspace.features.doctor.sentence_builder import sense_key
        assert sense_key("pick up the blue small bowl and place it inside "
                         "the small gray bowl") == \
            sense_key("pick up the small blue bowl and place it inside "
                      "the gray small bowl")
        assert sb.SKILL_KO["pick-inside"] == "집어서 안에"
        print("10. 블럭 조립 문장 고치기 OK")

        win.close()
    print("test_doctor_tab OK")


if __name__ == "__main__":
    main()
