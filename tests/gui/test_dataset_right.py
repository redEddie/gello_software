"""Dataset 의 우측 패널 -- 고른 에피소드의 값과 그 scene 의 배치.

왼쪽 목록의 열(프레임·결과·수집자)은 패널이 좁으면 잘린다. 열을 없애지 않고
고른 한 줄만 오른쪽에서 온전히 읽히게 한다 (2026-09-07 조작자).

배치도는 **고른 scene 의 것**이다 -- 전에는 "수집 중" 에만 채워져 세션이
없으면 늘 비어 있었고, 큐레이션 중에 "이 에피소드가 어떤 배치였지" 를 물으려면
다른 활동으로 건너가야 했다.
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

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

OBJ = ["OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01"]


def _make(root: Path) -> None:
    with h5py.File(root / "scene_000.hdf5", "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["scene_id"] = "S000"
        meta.attrs["objects"] = json.dumps(OBJ)
        meta.attrs["layout"] = json.dumps({"grid": [3, 3], "placements": {
            OBJ[0]: {"zone": [0, 0]}, OBJ[1]: {"zone": [2, 2]}}})
        meta.attrs["station"] = "knu-eng7"
        meta.attrs["dataset_version"] = "knu-1.0.0"
        meta.attrs["created"] = "2026-08-21T15:36:39+09:00"
        meta.create_dataset("reference_image",
                            data=np.full((48, 64, 3), 120, dtype=np.uint8))
        g = f.create_group("episode_000")
        g.attrs.update({
            "scene_id": "S000", "instruction_id": "I000",
            "instruction": "pick up the white cup and place it inside "
                           "the small blue bowl",
            "episode_uid": "EP-S000-I000-E000", "episode_id": 0,
            "quality_status": "success", "collector": "jeongrim",
            "timestamp": "2026-08-21T15:40:08+09:00", "num_samples": 185,
        })
        g.create_dataset("actions", data=np.zeros((2, 7), np.float32))


def _text(card) -> str:
    return " | ".join(w.text() for w in card.findChildren(QLabel) if w.text())


def main() -> None:
    import apps.collect_workspace as cw
    from apps.workspace.shell.right_builders import RIGHT_BUILDERS

    assert "dataset" in RIGHT_BUILDERS, "Dataset 이 자기 우측 페이지를 안 가졌다"

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make(root)
        win = cw.WorkspaceWindow(None)
        win.root_edit.setText(str(root))
        # 활동 전환이 자동 분석 때문에 죽지 않는다. 이 합성 파일에는 통계가
        # 쓰는 필드가 없어서 실제로 KeyError 로 활동 전환이 막혔다
        # (2026-09-07). 분석은 실패해도 되지만 화면을 끌고 죽으면 안 된다.
        win._set_activity("dataset")
        assert win.right_pages["dataset"] != win.right_pages["collect"], \
            "Dataset 이 세션 페이지를 함께 쓰고 있다"
        assert win.right_stack.currentIndex() == win.right_pages["dataset"]
        win.dataset_ops.refresh_dataset_tree()

        t = win.dataset_tree
        # 목록에 수집자 열이 있다 (여럿을 훑는 자리)
        assert t.headerItem().text(3) == "수집자", t.headerItem().text(3)
        top = t.topLevelItem(0)
        assert top.childCount() == 1, top.childCount()

        # 고른 한 줄은 오른쪽에서 온전히 -- 목록에 없는 값까지
        win.dataset_ops._fill_right(top.child(0))
        ep = _text(win.ds_episode_card)
        for want in ("episode_000", "185", "success", "jeongrim",
                     "I000", "2026-08-21T15:40:08"):
            assert want in ep, (want, ep)
        print("1. 고른 에피소드의 값 OK")

        # 배치는 세션이 아니라 **고른 scene** 의 것
        assert win.worker is None, "이 검사는 세션 없이 도는 것이 요점이다"
        cells = [w.text() for w in win.ds_scene_zones.findChildren(QLabel)]
        assert "CUP-WHT-02" in " ".join(cells), cells
        assert "S000" in _text(win.ds_scene_card)
        assert win.ds_scene_photo.pixmap() is not None
        assert not win.ds_scene_photo.pixmap().isNull(), "기준 사진이 안 붙었다"
        print("2. 고른 scene 의 배치 + 기준 사진 OK")

        # 고른 것이 없으면 비운다 (지난 선택이 남아 있으면 안 된다)
        win.dataset_ops._fill_right(None)
        assert "미선택" in _text(win.ds_episode_card)
        assert "기준 사진 없음" in win.ds_scene_photo.text()
        print("3. 선택 없음 OK")
        win.close()
    print("test_dataset_right OK")


if __name__ == "__main__":
    main()
