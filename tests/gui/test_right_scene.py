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


# ---- 새 scene 번호는 만드는 순간 다시 센다 -----------------------------
# 구성기가 들고 있던 번호는 맥락을 물린 시점의 것이라 낡을 수 있다. 실기에서
# S000 인 채로 남아 이미 있는 파일과 부딪혔다 (2026-09-07). 표시가 틀리는
# 것은 불편이지만 그 번호로 파일을 만드는 것은 사고다.
import tempfile as _tf2  # noqa: E402
from pathlib import Path as _P  # noqa: E402

import numpy as _np  # noqa: E402

from gello.scene.scene_format import (  # noqa: E402
    SceneMetadata as _MD,
    SceneWriter as _W,
    next_scene_id as _next,
)
from gello.scene.props import active_prop_ids as _apid  # noqa: E402

_root = _P(_tf2.mkdtemp(prefix="sceneid_"))
_mk = lambda sid: _W(_root, metadata=_MD(  # noqa: E731
    scene_id=sid, objects=["OBJ-CUP-WHT-02"],
    layout={"grid": [3, 3], "placements": {"OBJ-CUP-WHT-02": {"zone": [0, 0]}}}),
    known_prop_ids=_apid(), session_version="knu-1.2.0")
for _sid in ("S000", "S001"):
    _w = _mk(_sid)
    _w.close()
assert _next(_root) == "S002", _next(_root)

# 낡은 번호(S000)를 든 구성으로 만들어도, 만드는 쪽이 다시 세면 부딪히지 않는다
_stale = _MD(scene_id="S000", objects=["OBJ-CUP-WHT-02"],
             layout={"grid": [3, 3],
                     "placements": {"OBJ-CUP-WHT-02": {"zone": [0, 0]}}})
_stale.scene_id = _next(_root)          # on_compose_done 이 하는 일
_w = _W(_root, metadata=_stale, known_prop_ids=_apid(),
        session_version="knu-1.2.0")
_w.close()
assert (_root / "scene_002.hdf5").is_file(), sorted(p.name for p in _root.iterdir())
print("통과: 새 scene 번호를 만드는 순간 다시 센다")


# os._exit 는 버퍼를 비우지 않는다 -- 먼저 비운다. 없으면 이 파일의
# 출력이 통째로 사라져서, 검사가 실제로 돌았는지 알 수 없다.
sys.stdout.flush()
os._exit(0)
