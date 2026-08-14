"""scene 다양성 추천(#33) + Point Cloud 탭 검증 (offscreen, 카메라 불필요)."""
import subprocess
import sys
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/experiments")
sys.argv = ["t"]

# ---- 1. 추천기 selftest (거리·제약·결정성·비중복) ----
r = subprocess.run([sys.executable, WT + "/scripts/recommend_scene.py",
                    "--selftest"], capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
assert "selftest 통과" in r.stdout
print("1 통과: recommend_scene --selftest")

# ---- 2. 추천 결과의 검증 통과 + 거리 근거 ----
from gello.props import active_prop_ids, props_by_id  # noqa: E402
from gello.scene_diversity import recommend  # noqa: E402
from gello.scene_format import SceneMetadata  # noqa: E402

props = props_by_id()
base = SceneMetadata(
    scene_id="S000",
    objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"],
    layout={"grid": [3, 3], "placements": {
        "OBJ-CUP-BLU-01": {"zone": [0, 0]},
        "OBJ-BOWLS-WHT-01": {"zone": [1, 1]}}})
recs = recommend([base], props, k=3, seed=5, scene_id="S001")
ids = active_prop_ids()
assert len(recs) == 3
for md, dist in recs:
    md.validate(known_prop_ids=ids)
    assert 0 < dist <= 1
assert recs[0][1] >= recs[1][1] >= recs[2][1]   # farthest-point 순서
print(f"2 통과: 추천 3안 validate + 거리 내림차순 ({[d for _, d in recs]})")

# ---- 3. Point Cloud 탭 (카메라 없이 렌더 경로만) ----
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402

cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win = cw.WorkspaceWindow(None)
# 탭 진입: 카메라 미선택 -> 안내만, 워커 없음
win._on_center_tab_changed(win._cloud_tab_index)
assert win._cloud_worker is None
# 합성 클라우드 렌더
rng = np.random.default_rng(0)
pts = rng.standard_normal((2000, 3)).astype(np.float32)
rgb = rng.integers(0, 255, (2000, 3), dtype=np.uint8)
win._on_cloud(pts, rgb)
assert win.cloud_view.pixmap() is not None
assert "2,000" in win.cloud_status.text() or "2000" in win.cloud_status.text()
win.cloud_yaw.setValue(60)      # 시점 변경 -> 재렌더 경로
win._on_center_tab_changed(0)   # 탭 이탈 -> 워커 없음이면 no-op
assert win._cloud_worker is None
# 세션 중 진입 차단
win.worker = object()
win._on_center_tab_changed(win._cloud_tab_index)
assert win._cloud_worker is None
win.worker = None
print("3 통과: 탭 진입/이탈 가드 + 합성 클라우드 렌더 + 세션 차단")

# ---- 4. GUI 추천 받기: 다이얼로그 + NewScene 자동 채움 ----
rdlg = cw.RecommendDialog(None, [base], props, "S001")
assert len(rdlg._radios) == 3 and rdlg._radios[0].isChecked()
rdlg._radios[1].setChecked(True)
rdlg._accept()
assert rdlg.picked is not None
assert rdlg.picked.objects == rdlg._recs[1][0].objects
nd = cw.NewSceneDialog(None, "S001")
nd._apply_recommendation(rdlg.picked)
assert set(nd._checked_ids()) == set(rdlg.picked.objects)
want_zones = {o: s["zone"] for o, s in
              rdlg.picked.layout["placements"].items()}
assert nd._placements == want_zones
assert nd.preview.text()                     # 격자 미리보기 갱신됨
# seed 바꿔 다시 추천 -> 카드 재구성
rdlg2 = cw.RecommendDialog(None, [base], props, "S001")
first = [m.objects for m, _ in rdlg2._recs]
rdlg2.seed_spin.setValue(7)
rdlg2._fill()
assert len(rdlg2._radios) == 3
print("4 통과: 추천 다이얼로그(선택/재추천) + NewScene 자동 채움")

# ---- 5. Point Cloud 카메라 선택 ----
assert win.cloud_cam_combo.currentData() == "agent"
win.cloud_cam_combo.setCurrentIndex(1)       # 워커 없음 -> 전환은 다음 진입 때
assert win.cloud_cam_combo.currentData() == "wrist"
assert win._cloud_worker is None
print("5 통과: 클라우드 카메라 콤보 (닫힌 탭에서는 지연 반영)")

# ---- 6. Depth 탭: 소비자 전환 + 컬러맵 렌더 ----
win._on_center_tab_changed(win._depth_tab_index)
assert win._depth_consumer == "depth" and win._cloud_worker is None
z = np.full((48, 64), 0.6, np.float32)
z[:10] = 0.0            # 무측정
z[20:30] = 5.0          # 최대 거리 밖
win._on_depth_img(z)
assert win.depth_view.pixmap() is not None
assert "유효 픽셀" in win.depth_status.text()
win.depth_range_slider.setValue(50)          # 0.5m -> 0.6m 픽셀도 무효
win._render_depth()
assert "0.5 m" in win.depth_range_label.text()
win._on_center_tab_changed(0)
assert win._depth_consumer is None
print("6 통과: Depth 탭 -- 진입/이탈, 컬러맵 렌더, 범위 슬라이더")

print("\n다양성 추천 + Point Cloud 검증 통과")
import os  # noqa: E402

os._exit(0)
