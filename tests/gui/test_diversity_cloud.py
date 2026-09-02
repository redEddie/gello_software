"""scene 다양성 추천(#33) + Point Cloud 탭 검증 (offscreen, 카메라 불필요)."""
import subprocess
import sys
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from apps.workspace.shared.image_utils import depth_colormap  # noqa: E402

# ---- 1. 추천기 selftest (거리·제약·결정성·비중복) ----
r = subprocess.run([sys.executable, WT + "/scripts/analyze/recommend_scene.py",
                    "--selftest"], capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
assert "selftest 통과" in r.stdout
print("1 통과: recommend_scene --selftest")

# ---- 2. 추천 결과의 검증 통과 + 거리 근거 ----
from gello.scene.props import active_prop_ids, props_by_id  # noqa: E402
from gello.scene.scene_diversity import AXES, recommend, recommend_detailed  # noqa: E402
from gello.scene.scene_format import SceneMetadata  # noqa: E402

props = props_by_id()
base = SceneMetadata(
    scene_id="S000",
    objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"],
    layout={"grid": [3, 3], "placements": {
        "OBJ-CUP-BLU-01": {"zone": [0, 0]},
        "OBJ-BOWLS-WHT-01": {"zone": [1, 1]}}})
det = recommend_detailed([base], props, k=3, seed=5, scene_id="S001")
ids = active_prop_ids()
assert len(det) == 3
for rec in det:
    rec["md"].validate(known_prop_ids=ids)
    assert 0 < rec["min_dist"] <= 1
    assert rec["bucket"] in ("원거리", "중간", "근거리")
    assert set(rec["axes"]) == set(AXES)          # 축별 거리 리포트
assert det[0]["bucket"] == "원거리"               # 첫 픽은 가장 새로운 것
# 호환 래퍼는 detailed 와 같은 내용의 (md, dist) 를 준다
recs = recommend([base], props, k=3, seed=5, scene_id="S001")
assert [m.objects for m, _ in recs] == [r["md"].objects for r in det]
print(f"2 통과: detailed 추천 3안 validate + 버킷/축 리포트 "
      f"({[(r['bucket'], r['min_dist']) for r in det]})")

# ---- 3. Point Cloud 탭 (카메라 없이 렌더 경로만) ----
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from tests.gui.helpers import _wait_recs  # noqa: E402

import collect_workspace as cw  # noqa: E402
from apps.workspace.features.scene.dialogs.new_scene_dialog import NewSceneDialog  # noqa: E402
from apps.workspace.features.scene.dialogs.recommend_dialog import RecommendDialog  # noqa: E402

cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win = cw.WorkspaceWindow(None)
# 탭 진입: 카메라 미선택 -> 안내만, 워커 없음
win._on_center_tab_changed(win._cloud_tab_index)
assert win.cameras.cloud_worker is None
# 합성 클라우드 렌더
rng = np.random.default_rng(0)
pts = rng.standard_normal((2000, 3)).astype(np.float32)
rgb = rng.integers(0, 255, (2000, 3), dtype=np.uint8)
win.depth_ops.on_cloud(pts, rgb)
assert win.cloud_view.pixmap() is not None
assert "2,000" in win.cloud_status.text() or "2000" in win.cloud_status.text()
win.cloud_yaw.setValue(60)      # 시점 변경 -> 재렌더 경로
win._on_center_tab_changed(0)   # 탭 이탈 -> 워커 없음이면 no-op
assert win.cameras.cloud_worker is None
# 세션 중 진입 차단
win.worker = object()
win._on_center_tab_changed(win._cloud_tab_index)
assert win.cameras.cloud_worker is None
win.worker = None
print("3 통과: 탭 진입/이탈 가드 + 합성 클라우드 렌더 + 세션 차단")

# ---- 4. GUI 추천 받기: 다이얼로그 + NewScene 자동 채움 ----
rdlg = RecommendDialog(None, [base], props, "S001")
_wait_recs(rdlg)
assert len(rdlg._radios) == 3 and rdlg._radios[0].isChecked()
rdlg._radios[1].setChecked(True)
rdlg._accept()
assert rdlg.picked is not None
assert rdlg.picked.objects == rdlg._recs[1]["md"].objects
nd = NewSceneDialog(None, "S001")
nd._apply_recommendation(rdlg.picked)
assert set(nd._checked_ids()) == set(rdlg.picked.objects)
want_zones = {o: s["zone"] for o, s in
              rdlg.picked.layout["placements"].items()}
assert nd._placements == want_zones
assert nd.preview.text()                     # 격자 미리보기 갱신됨
# seed 바꿔 다시 추천 -> 카드 재구성
rdlg2 = RecommendDialog(None, [base], props, "S001")
_wait_recs(rdlg2)
first = [r["md"].objects for r in rdlg2._recs]
rdlg2.seed_spin.setValue(7)
rdlg2._fill()
_wait_recs(rdlg2)
assert len(rdlg2._radios) == 3
print("4 통과: 추천 다이얼로그(선택/재추천) + NewScene 자동 채움")

# ---- 5. Point Cloud 카메라 선택 ----
assert win.cloud_cam_combo.currentData() == "agent"
win.cloud_cam_combo.setCurrentIndex(1)       # 워커 없음 -> 전환은 다음 진입 때
assert win.cloud_cam_combo.currentData() == "wrist"
assert win.cameras.cloud_worker is None
print("5 통과: 클라우드 카메라 콤보 (닫힌 탭에서는 지연 반영)")

# ---- 6. Depth 탭: 소비자 전환 + 컬러맵 렌더 ----
win._on_center_tab_changed(win._depth_tab_index)
assert win.cameras.depth_consumer == "depth" and win.cameras.cloud_worker is None
z = np.full((48, 64), 0.6, np.float32)
z[:10] = 0.0            # 무측정
z[20:30] = 5.0          # 최대 거리 밖
win.depth_ops.on_depth_img(z)
assert win.depth_view.pixmap() is not None
assert "유효 픽셀" in win.depth_status.text()
win.depth_range_slider.setValue(50)          # 0.5m -> 0.6m 픽셀도 무효
win.depth_ops.render_depth()
assert "0.5 m" in win.depth_range_label.text()
win._on_center_tab_changed(0)
assert win.cameras.depth_consumer is None
print("6 통과: Depth 탭 -- 진입/이탈, 컬러맵 렌더, 범위 슬라이더")

# ---- 7. 척도 바: 큰 프레임엔 그려지고 작은 프레임엔 생략 ----
big = np.full((480, 640), 0.6, np.float32)
out = depth_colormap(big, 1.2)
bar = out[120:360, 640 - 28:640 - 10]        # 오른쪽 컬러바 영역
assert bar.std() > 20, "척도 바가 안 그려짐"    # 그라데이션 존재
small = depth_colormap(np.full((48, 64), 0.6, np.float32), 1.2)
assert small.shape == (48, 64, 3)              # 작은 입력은 바 생략, 무오류
print("7 통과: depth 척도 바 (대형 프레임 표시 / 소형 생략)")

# ---- 8. 커서 거리 표시 + 워커 모드 게이팅 ----
win.cameras.depth_img = np.full((48, 64), 0.6, np.float32)


class _P:                                      # QPointF 흉내
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


uv = win.depth_ops.depth_uv(_P(5.0, 30.0))              # 라벨 안쪽 -> 픽셀 좌표
assert uv is not None and 0 <= uv[0] < 64 and 0 <= uv[1] < 48
assert win.depth_ops.depth_uv(_P(-30.0, -30.0)) is None  # 프레임 밖
win.cameras.depth_cursor = (10, 20)
win.depth_ops.render_depth()
assert "커서 (10,20)" in win.depth_status.text()
assert "0.600 m" in win.depth_status.text()
win.cameras.depth_cursor = None
from gello.gui.workers import DepthCloudWorker  # noqa: E402

assert DepthCloudWorker("x", mode="depth").mode == "depth"
assert DepthCloudWorker("x").mode == "cloud"
# 프레임 크기 변경 등으로 범위 밖에 남은 커서는 조용히 지운다
win.cameras.depth_cursor = (999, 999)
win.depth_ops.render_depth()
assert win.cameras.depth_cursor is None
assert "커서" not in win.depth_status.text()
assert not win.cloud_view._square_guide and not win.depth_view._square_guide
print("8 통과: 커서 실거리 + 경계 가드 + 워커 모드 + 크롭 가이드 미적용")

print("\n다양성 추천 + Point Cloud 검증 통과")
import os  # noqa: E402

os._exit(0)
