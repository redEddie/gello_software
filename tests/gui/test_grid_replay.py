"""3×3 격자 오버레이 + 실로봇 재생 버튼 검증 (offscreen)."""
import sys
import tempfile
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from gello.gui.grid_overlay import (  # noqa: E402
    DEFAULT_CORNERS, active_corners, draw_grid, grid_segments,
    load_grid_store, save_grid_store,
)

TMP = Path(tempfile.mkdtemp(prefix="grid_"))

# ---- 1. grid_overlay 단위 ----
segs = grid_segments([[0, 0], [1, 0], [1, 1], [0, 1]], 300, 300)
assert len(segs) == 8  # 세로 4 + 가로 4
# 항등 사각형이면 1/3 위치의 세로선은 x=100
xs = sorted(s[0][0] for s in segs[::2])
assert xs == [0, 100, 200, 300], xs
img = np.full((240, 320, 3), 30, np.uint8)
out = draw_grid(img, DEFAULT_CORNERS, 80)
assert out.shape == img.shape and out.dtype == np.uint8
assert (out != img).any() and (img == 30).all()  # 사본에만 그림
sp = TMP / "grids.json"
st = load_grid_store(sp)
st["grids"]["g1"] = DEFAULT_CORNERS
st["active"] = "g1"
st["live_on"] = True
st["alpha"] = 42
save_grid_store(st, sp)
st2 = load_grid_store(sp)
assert active_corners(st2) == DEFAULT_CORNERS and st2["alpha"] == 42
assert load_grid_store(TMP / "none.json")["grids"] == {}
print("1 통과: 격자 계산(원근 항등 검증)·그리기·저장 왕복")

import collect_workspace as cw  # noqa: E402
from gello.gui.grid_overlay import DEFAULT_CORNERS  # noqa: E402

# ---- 2. 편집 다이얼로그: 정렬/변환/저장/불러오기 ----
saved = {}
cw.save_grid_store = lambda store, path=None: saved.update(store)
bg = np.full((240, 320, 3), 50, np.uint8)
store = {"active": None, "live_on": False, "alpha": 60, "grids": {}}
dlg = cw.GridEditorDialog(None, bg, store)
dlg.canvas.corners = [[0.2, 0.10], [0.8, 0.30], [0.9, 0.9], [0.1, 0.9]]
dlg._align(0, 1, 1)
assert dlg.canvas.corners[0][1] == dlg.canvas.corners[1][1] == 0.20
dlg._align(0, 3, 0)     # 좌 정렬: 1·4번 x 평균
assert dlg.canvas.corners[0][0] == dlg.canvas.corners[3][0]
dlg._undo()             # 좌 정렬 취소
assert dlg.canvas.corners[0][0] == 0.2
# 크롭 가이드: crop_params 없이 열면 비활성, 주면 켜지고 화면이 달라진다
assert not dlg.crop_check.isEnabled()
dlg2 = cw.GridEditorDialog(None, bg, dict(store),
                           crop_params={"zoom": 1.2, "x": 10, "y": 0})
assert dlg2.crop_check.isChecked() and dlg2.canvas.show_crop
shaded = dlg2.canvas._crop_shade(bg.copy())
assert shaded.shape == bg.shape and (shaded != bg).any()
assert (shaded[0, 0] <= bg[0, 0]).all()      # 크롭 밖은 어두워진다
dlg2.crop_check.setChecked(False)
assert not dlg2.canvas.show_crop
dlg.canvas.full_grid = False
dlg._transform()
assert dlg.canvas.full_grid
dlg.name_edit.setText("front_cam")
dlg._save()
assert saved["active"] == "front_cam"
assert saved["grids"]["front_cam"][0] == [0.2, 0.20]
dlg.load_combo.setCurrentIndex(dlg.load_combo.findText("front_cam"))
dlg.canvas.corners = [list(c) for c in DEFAULT_CORNERS]
dlg._load_selected()
assert dlg.canvas.corners[0] == [0.2, 0.20]
print("2 통과: 정렬(y 평균)·변환 플래그·저장(active 지정)·불러오기")

# ---- 3. 윈도우: 라이브 오버레이 + 재생 버튼 가드 ----
infos, warns = [], []
cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(
    lambda *a, **k: warns.append(a[2] if len(a) > 2 else ""))
cw.QMessageBox.information = staticmethod(
    lambda *a, **k: infos.append(a[2] if len(a) > 2 else ""))
win = cw.WorkspaceWindow(None)
win.cameras.grid_store = {"active": "g", "live_on": True, "alpha": 60,
                   "grids": {"g": DEFAULT_CORNERS}}
win.grid_live_check.setChecked(True)
frame = np.full((480, 640, 3), 20, np.uint8)
shown = win._with_grid("agent", frame)
assert (shown != frame).any() and (frame == 20).all()
assert win._with_grid("wrist", frame) is frame  # wrist 는 그대로
win.grid_live_check.setChecked(False)
assert win._with_grid("agent", frame) is frame
print("3 통과: agent 라이브만 오버레이, 체크 해제 시 원본")

# ---- 3b. 카메라 최대화: 좌우 배치 유지, 스플리터 비율만 (겹침 없음) ----
win.cameras.last_cam_frame["agent"] = np.full((480, 640, 3), 40, np.uint8)
win.cameras.last_cam_frame["wrist"] = np.full((480, 640, 3), 90, np.uint8)
win._set_live_maximized("wrist")
sizes = win.live_split.sizes()
assert sizes[1] > sizes[0] * 4, sizes                   # wrist 크게, agent 아주 작게
assert not win.live_boxes["agent"].isHidden()           # 둘 다 보인다 (겹침 없음)
assert win.live_view_combo.currentData() == "wrist"     # 콤보 동기화
# 프레임은 각자 자기 뷰로만 간다 (합성 없음)
win._update_live_view("agent", np.full((480, 640, 3), 41, np.uint8))
assert win.live_views["agent"].pixmap() is not None
win._set_live_maximized("agent")
sizes = win.live_split.sizes()
assert sizes[0] > sizes[1] * 4, sizes                   # 반대 선택 시 반대로
win._set_live_maximized(None)                           # 나란히 복원
sizes = win.live_split.sizes()
assert abs(sizes[0] - sizes[1]) <= max(sizes) * 0.2
assert win.live_view_combo.currentIndex() == 0
print("3b 통과: 좌우 최대화(비율 88/12) 왕복 + 겹침 없음 + 콤보 동기화")

# ---- 3c. '실패만 선택' -- scene(failed)과 legacy(실패) 표기 모두 ----
from PyQt6.QtWidgets import QTreeWidgetItem  # noqa: E402

win.dataset_tree.clear()
sp = QTreeWidgetItem(["scene_099.hdf5", "", "scene"])
win.dataset_tree.addTopLevelItem(sp)
for name, q in (("episode_000", "success"), ("episode_001", "failed")):
    sp.addChild(QTreeWidgetItem([name, "10", q]))
lp = QTreeWidgetItem(["t_demo.hdf5", "", ""])
win.dataset_tree.addTopLevelItem(lp)
lp.addChild(QTreeWidgetItem(["  demo_0", "10", cw.tr("실패")]))
win._on_select_failed()
sel = [i.text(0) for i in win.dataset_tree.selectedItems()]
assert "episode_001" in sel, sel                 # scene 실패 선택됨
assert any("demo_0" in s for s in sel), sel      # legacy 실패도
assert "episode_000" not in sel                  # 성공은 제외
win.dataset_tree.clear()   # 합성 항목(UserRole 없음)이 뒤 재생 가드 테스트에
print("3c 통과: 실패만 선택이 scene 'failed' 표기도 잡음")   # 안 섞이게

# 재생 가드: 선택 없음 -> 안내, 세션 중 -> 경고
win._on_replay_selected()
assert infos and "하나만" in infos[-1]
win.worker = object()
win._replay_on_robot(str(TMP / "x.hdf5"), "episode_000")
assert warns and "세션" in warns[-1]
win.worker = None
# 배속 다이얼로그 취소 -> 프로세스 없음
cw.QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.5, False))
win._replay_on_robot(str(TMP / "x.hdf5"), "episode_000")
assert win.procs.replay_process is None
# 승인 경로: 확인 Yes -> QProcess 시작 (더미 프로그램으로 교체)
cw.QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.5, True))
cw.QMessageBox.warning = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
real_repl = cw.REPLAY_SCRIPT
cw.sys = sys
win._replay_on_robot(str(TMP / "x.hdf5"), "episode_000")
assert win.procs.replay_process is not None
args = win.procs.replay_process.arguments()
assert args[0] == real_repl and args[1].endswith("x.hdf5")
assert args[2] == "episode_000" and args[3:] == ["--speed", "0.5", "--yes"]
# 재생 중에는 두 진입점 버튼이 '중단' 토글로 바뀐다
assert "중단" in win.replay_btn.text()
assert "중단" in win.gallery_replay_btn.text()
proc = win.procs.replay_process
win._on_replay_selected()            # 토글: 재생 중 클릭 = 중단
proc.waitForFinished(3000)
for _ in range(20):                  # finished 시그널(큐잉) 전달
    app.processEvents()
    if win.procs.replay_process is None:
        break
    import time
    time.sleep(0.05)
assert win.procs.replay_process is None
assert "중단" not in win.replay_btn.text()
print("4 통과: 재생 가드 + 명령행 + 토글(재생↔중단) 왕복")

print("\n격자 + 실로봇 재생 검증 통과")
import os  # noqa: E402

os._exit(0)
