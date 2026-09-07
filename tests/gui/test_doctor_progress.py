"""진행 닥터 (#48) 인수 테스트.

  1. 미달 목록이 많이 모자란 것부터, 계획과 파일을 대조해 나온다
  2. 잠긴 파일에서 죽지 않는다 (수집 중이면 그 scene 은 다른 프로세스가 쓴다)
  3. 줄을 고르면 배치도가 뜬다 -- 책상을 그 모양으로 만들라는 뜻이다
  4. "이어 찍으면 어느 버전인가" 를 누르기 전에 말한다
  5. [수집으로] 가 시작 설정을 걸고 Collect 로 데려간다
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

from PyQt6.QtWidgets import QApplication  # noqa: E402

from gello.scene.collection_progress import scan  # noqa: E402

TASKS = [("I000", "pick up the white cup and place it inside the small blue bowl", 3),
         ("I001", "pick up the white cup and place it inside the small gray bowl", 3)]
#: 두 번째 지시문은 하나만 찍었다 -- 미달 1건이 나와야 한다.
COLLECTED = {"I000": 3, "I001": 1}


def _make(root: Path) -> None:
    with h5py.File(root / "scene_000.hdf5", "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["scene_id"] = "S000"
        meta.attrs["objects"] = json.dumps(["OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01"])
        meta.attrs["layout"] = json.dumps({"grid": [3, 3], "placements": {
            "OBJ-CUP-WHT-02": {"zone": [0, 0]},
            "OBJ-BOWLS-BLU-01": {"zone": [2, 1]}}})
        meta.attrs["station"] = "knu-eng7"
        meta.attrs["dataset_version"] = "knu-1.0.0"
        meta.attrs["created"] = "2026-08-22T16:35:57+09:00"
        meta.create_dataset("reference_image",
                            data=np.full((48, 64, 3), 120, dtype=np.uint8))
        i = 0
        for iid, text, _t in TASKS:
            for _ in range(COLLECTED[iid]):
                g = f.create_group(f"episode_{i:03d}")
                g.attrs["scene_id"] = "S000"
                g.attrs["instruction_id"] = iid
                g.attrs["instruction"] = text
                g.attrs["episode_uid"] = f"S000-{iid}-E{i:03d}"
                g.attrs["quality_status"] = "success"
                g.attrs["episode_id"] = i
                g.attrs["num_samples"] = 2
                g.create_dataset("actions", data=np.zeros((2, 7), np.float32))
                i += 1
    # S001 은 계획에만 있고 파일이 없다 -- "아직 안 만든 scene" 도 미달이다.
    (root / "instructions.json").write_text(json.dumps({
        "plan_version": 1,
        "scenes": [
            {"scene_id": "S000", "slots": [
                {"instruction_id": iid, "instruction": t, "target": n}
                for iid, t, n in TASKS]},
            {"scene_id": "S001", "slots": [
                {"instruction_id": "I000",
                 "instruction": TASKS[0][1], "target": 5}]},
        ]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make(root)
        plan = root / "instructions.json"

        prog = scan(root, plan)
        assert prog.tasks == 3, prog.tasks
        assert (prog.usable, prog.target) == (3 + 1 + 0, 3 + 3 + 5), prog
        ids = [(s.scene_id, s.instruction_id, s.remaining)
               for s in prog.shortfalls]
        # 많이 모자란 것부터 -- 아직 안 만든 S001(5) 이 S000 I001(2) 보다 앞
        assert ids == [("S001", "I000", 5), ("S000", "I001", 2)], ids
        assert prog.shortfalls[0].missing_file
        assert prog.shortfalls[1].version == "knu-1.0.0"
        print("1. 미달 목록 OK")

        # 못 읽는 파일에서 죽지 않는다. 실제 잠금은 **프로세스 간**에만
        # 걸리므로(같은 프로세스는 h5py 가 다시 열어 준다) 읽기 자체를
        # 실패시켜 검사한다 -- 여기서 볼 것은 잠금이 아니라 그 처리다.
        import gello.scene.collection_progress as cp
        _real = cp.count_by_slot

        def _boom(_path):
            raise OSError("unable to lock file")

        cp.count_by_slot = _boom
        try:
            locked = scan(root, plan)
        finally:
            cp.count_by_slot = _real
        got = [s for s in locked.shortfalls if s.scene_id == "S000"]
        assert got and all(s.unreadable for s in got), got
        assert all("쓰는 중" in s.reason for s in got), got
        assert any(s.scene_id == "S001" for s in locked.shortfalls), \
            "한 파일을 못 읽는다고 나머지를 못 세면 쓸모가 없다"
        # **0개로 세지 않는다.** 못 읽은 것은 모르는 것이고, 0 으로 세면
        # 닥터가 "전부 다시 찍어라" 라고 말한다.
        assert locked.usable == 0 and locked.target == 11, locked
        print("2. 못 읽는 파일 OK")

        import apps.collect_workspace as cw
        from apps.workspace.shared.tabs import center_tab_key

        app = QApplication.instance() or QApplication([])
        win = cw.WorkspaceWindow(None)
        win.root_edit.setText(str(root))
        win._set_activity("doctor")
        t = win.progress_tree
        assert t.topLevelItemCount() == 2, t.topLevelItemCount()
        assert "목표 미달 2개" in win.progress_title.text(), \
            win.progress_title.text()

        # 3. 배치도 -- 파일이 있는 줄을 고른다
        row = next(t.topLevelItem(i) for i in range(t.topLevelItemCount())
                   if t.topLevelItem(i).text(0) == "S000")
        win.doctor.on_shortfall_picked(row)
        cells = [w.text() for w in win.progress_zones.findChildren(type(
            win.progress_title))]
        assert "CUP-WHT-02" in " ".join(cells), cells
        assert not win.progress_photo.pixmap().isNull(), "기준 사진이 안 붙었다"
        print("3. 배치도 OK")

        # 4. 이어 찍기의 버전 결과를 미리 말한다
        assert "knu-1.0.0" in win.progress_note.text(), win.progress_note.text()
        print("4. 버전 예고 OK")

        # 5. [수집으로] -- 시작 설정 + 활동 전환
        win.scene_ops.refresh_scene_combo()
        win.doctor.go_collect()
        assert win.scene_iid_edit.text() == "I001", win.scene_iid_edit.text()
        assert win._activity == "collect", win._activity
        assert center_tab_key(win) == "live", center_tab_key(win)
        print("5. 수집으로 OK")

        # 아직 안 만든 scene 은 데려갈 수 없다 (파일이 없으면 붙일 데가 없다)
        win._set_activity("doctor")
        row1 = next(t.topLevelItem(i) for i in range(t.topLevelItemCount())
                    if t.topLevelItem(i).text(0) == "S001")
        win.doctor.on_shortfall_picked(row1)
        assert "만들지 않은" in win.progress_note.text(), win.progress_note.text()
        # 6. 왼쪽 목록이 기록/진행을 나란히 센다
        win._set_activity("doctor")
        lt = win.doctor_tree
        row0 = next(lt.topLevelItem(i) for i in range(lt.topLevelItemCount())
                    if lt.topLevelItem(i).text(0) == "S000")
        assert row0.text(3).startswith("1"), row0.text(3)   # 진행 1건
        print("6. 목록의 기록/진행 두 칸 OK")

        # 7. scene 을 고르면 진행 닥터가 그 안만, Space 로 풀면 전체
        win.doctor.select_scene("S000")
        assert win.progress_tree.topLevelItemCount() == 1, \
            win.progress_tree.topLevelItemCount()
        assert "S000" in win.progress_title.text(), win.progress_title.text()
        # Space 는 깊은 쪽부터 푼다 -- 고른 줄이 있으면 그것부터
        win.doctor.on_shortfall_picked(win.progress_tree.topLevelItem(0))
        assert win.doctor.clear_selection(), "고른 줄을 못 풀었다"
        assert win.progress_tree.topLevelItemCount() == 1, "scene 까지 풀렸다"
        assert win.doctor.clear_selection(), "scene 을 못 풀었다"
        assert win.progress_tree.topLevelItemCount() == 2, \
            win.progress_tree.topLevelItemCount()
        assert "전체" in win.progress_title.text(), win.progress_title.text()
        assert not win.doctor.clear_selection(), "풀 것이 없는데 풀었다고 한다"
        print("7. scene 필터 + Space 로 풀기 OK")

        win.close()
    print("test_doctor_progress OK")


if __name__ == "__main__":
    main()
