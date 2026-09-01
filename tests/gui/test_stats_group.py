"""Analysis 그룹 기준 = (scene, 문장) 검증 -- 같은 문장이라도 scene 이 다르면
별도 그룹, legacy 는 문장 단위."""
import subprocess
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)

from gello.data.episode_stats import scan_dataset, task_table  # noqa: E402
from gello.scene.scene_format import SceneMetadata, SceneWriter, list_scene_episodes  # noqa: E402

d = Path(tempfile.mkdtemp(prefix="statsgrp_"))
subprocess.run([sys.executable, WT + "/scripts/check/check_scene_file.py",
                "--selftest", "--keep", str(d)], check=True, capture_output=True)
scene0 = d / "scene_000.hdf5"
eps0 = list_scene_episodes(scene0)
sentence = eps0[0]["instruction"]

# 같은 문장으로 S001 을 하나 더 만든다 -- 훨씬 느린(작은 |Δa|) 궤적으로
md = SceneMetadata(scene_id="S001", objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"],
                   layout={"grid": [3, 3], "placements": {
                       "OBJ-CUP-BLU-01": {"zone": [0, 0]},
                       "OBJ-BOWLS-WHT-01": {"zone": [2, 2]}}})
w = SceneWriter(d, metadata=md)
r = np.random.default_rng(0)
for _ in range(3):
    w.start_episode()
    q = np.zeros(7, np.float32)
    for _ in range(30):
        q = q + 0.001                          # 아주 느린 움직임
        w.add_frame(
            agentview_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            eye_in_hand_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            joint_positions=q, gripper_position=0.5, ee_pos_quat=np.zeros(7),
            gripper_closed=False, commanded_joint_positions=q, commanded_gripper=0.0)
    w.save_buffer(w.detach_buffer(), instruction=sentence, instruction_id="I000",
                  success=True, collector="t")
w.close()

stats = scan_dataset([str(scene0), str(d / "scene_001.hdf5"),
                      str(d / "selftest_task_demo.hdf5")])
by_group = {}
for s in stats:
    by_group.setdefault(s.group, []).append(s)
groups_same_sentence = [g for g in by_group if g[1] == sentence]
scenes = sorted(g[0] for g in groups_same_sentence)
assert scenes == ["S000", "S001"], scenes                # 같은 문장이 scene 별로 분리
# S001 그룹은 자기들끼리만 비교 -> 전부 느려도 그룹 내 편차는 ~0 (튀는 것 없음)
s1 = by_group[("S001", sentence)]
assert all(abs(s.task_dev) < 1e-6 for s in s1), [s.task_dev for s in s1]
assert not any(s.flagged for s in s1)
# 그룹 라벨은 scene 이 앞에 붙는다, legacy 는 문장만
assert s1[0].group_label.startswith("S001 · ")
leg = [s for s in stats if s.scene == ""]
assert leg and leg[0].group_label == leg[0].task
# task_table 도 그룹 단위 행
rows = task_table(stats)
assert any(r_["scene"] == "S001" and r_["task"] == sentence for r_ in rows)
assert any(r_["scene"] == "S000" and r_["task"] == sentence for r_ in rows)
print(f"통과: (scene,문장) 그룹 분리 -- 그룹 {len(by_group)}개, S001 느린 궤적이 S000 기준으로 튀지 않음")

# ---- GUI: 큐레이션 후보의 그룹 콤보 필터 ----
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402

cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None
cw.WorkspaceWindow._startup_tuning = lambda self: None   # pkexec 비밀번호 창 차단
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win = cw.WorkspaceWindow(None)
win._stats = stats
win._refresh_group_combo()
assert win.group_combo.count() == 1 + len(by_group)
i = next(k for k in range(win.group_combo.count())
         if win.group_combo.itemData(k) == ("S001", sentence))
assert i > 0
win.group_combo.setCurrentIndex(i)          # -> _refresh_rank_list
shown = [win.rank_tree.topLevelItem(k).data(0, cw.Qt.ItemDataRole.UserRole)
         for k in range(win.rank_tree.topLevelItemCount())]
assert shown and all(p.endswith("scene_001.hdf5") for p, _ in shown), shown
assert len(shown) == len(s1)
win.group_combo.setCurrentIndex(0)          # (전체)
assert win.rank_tree.topLevelItemCount() == len(stats)
print("통과: 큐레이션 후보 그룹 콤보 -- 한 그룹만 / 전체 복귀")
import os  # noqa: E402

os._exit(0)
