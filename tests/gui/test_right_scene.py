"""오른쪽 패널 scene 배치도 검증 (offscreen)."""
import sys
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from gello.scene.scene_format import SceneMetadata  # noqa: E402

cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win = cw.WorkspaceWindow(None)
assert "세션 없음" in win.right_scene_view.text()
# 합성 metadata -- 실파일은 수집 세션이 잠그고 있을 수 있다
md = SceneMetadata(
    scene_id="S000",
    objects=["OBJ-cup-blue-01", "OBJ-bowl-blue-01"],
    layout={"grid": [3, 3],
            "placements": {"OBJ-cup-blue-01": {"zone": [0, 0]},
                           "OBJ-bowl-blue-01": {"zone": [1, 1]}}},
    description="테스트 배치")
win.scene_ops.set_right_scene(md, "S000")
t = win.right_scene_view.text()
assert "S000" in t, t[:120]
# 배치도는 진짜 격자 위젯(ZoneMap)이다 -- 물체가 놓인 칸을 셀이 말한다.
# 선문자(│┌…) 글 검사는 옛 ASCII 격자 대상이라 이 표현에선 성립하지 않는다.
cells = win.right_scene_view._zones.cell_texts()
assert "cup-blue-01" in cells and "bowl-blue-01" in cells, cells
win.scene_ops.set_right_scene(None, "S007")   # metadata 읽기 실패 케이스
assert "S007" in win.right_scene_view.text()
win.scene_ops.set_right_scene(None)
assert "세션 없음" in win.right_scene_view.text()
print("통과: 배치도 표시(3x3 격자 포함)/읽기실패/초기화")
import os  # noqa: E402

os._exit(0)
