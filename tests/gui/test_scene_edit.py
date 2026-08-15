"""scene 에피소드 편집(삭제·트림) 검증 -- 삭제 후 renumber(그룹·episode_id·
slot E번호·uid), GUI 삭제 경로(양포맷)+확인창, Trim 양포맷, 파일 status."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/experiments")
sys.path.insert(0, WT + "/scripts")
sys.argv = ["t"]

from gello.scene_format import (  # noqa: E402
    SceneWriter, delete_scene_episodes, list_scene_episodes, read_scene_metadata,
)

d = Path(tempfile.mkdtemp(prefix="sceneedit_"))
subprocess.run([sys.executable, WT + "/scripts/check_scene_file.py",
                "--selftest", "--keep", str(d)], check=True, capture_output=True)
scene = d / "scene_000.hdf5"
eps0 = list_scene_episodes(scene)
names0 = [e["name"] for e in eps0]
by_slot = {}
for e in eps0:
    by_slot.setdefault(e["instruction_id"], []).append(e)
iid, slot_eps = max(by_slot.items(), key=lambda kv: len(kv[1]))
victim = max(slot_eps, key=lambda e: e["episode_uid"])   # 그 slot 의 최대 E
victim_uid = victim["episode_uid"]
n_before = len(names0)

# ---- 1. 직접 삭제: 번호 재매김 (그룹 0..N-1, episode_id, slot E, uid) ----
delete_scene_episodes(scene, [victim["name"]])
eps1 = list_scene_episodes(scene)
assert len(eps1) == n_before - 1
assert [e["name"] for e in eps1] == [f"episode_{i:03d}" for i in range(n_before - 1)]
with h5py.File(scene) as f:
    assert "deleted_uids" not in f["metadata"].attrs          # 툼스톤 없음
    assert int(f["metadata"].attrs["next_episode_idx"]) == n_before - 1
    for e in eps1:
        assert int(f[e["name"]].attrs["episode_id"]) == int(e["name"].split("_")[1])
# slot 안에서 E번호가 0..k-1 로 다시 채워지고 uid 도 일치
per_slot = {}
for e in eps1:
    per_slot.setdefault(e["instruction_id"], []).append(e)
for sid_iid, lst in per_slot.items():
    es = [int(x["episode_uid"].rsplit("-E", 1)[1]) for x in lst]
    assert es == list(range(len(lst))), (sid_iid, es)
    for x in lst:
        assert x["episode_uid"].endswith(f"-E{int(x['slot_episode_idx']):03d}")
print(f"1 통과: 삭제 후 renumber (그룹 연속, slot E 연속, uid 일치, next={n_before - 1})")

# ---- 2. 같은 slot 에 새 에피소드 -> 이어지는 다음 번호 (지운 번호는 자연히 채워짐) ----
md = read_scene_metadata(scene)
w = SceneWriter(d, scene_id=md.scene_id, resume=True)
w.start_episode()
r = np.random.default_rng(0)
for _ in range(5):
    w.add_frame(
        agentview_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        eye_in_hand_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        joint_positions=r.standard_normal(7).astype(np.float32),
        gripper_position=0.5, ee_pos_quat=np.zeros(7), gripper_closed=False,
        commanded_joint_positions=r.standard_normal(7).astype(np.float32),
        commanded_gripper=0.0)
new_name = w.save_buffer(w.detach_buffer(), instruction=victim["instruction"],
                         instruction_id=iid, success=True, collector="t")
w.close()
assert new_name == f"episode_{n_before - 1:03d}"      # 빈자리 없이 바로 다음
eps2 = list_scene_episodes(scene)
new_ep = next(e for e in eps2 if e["name"] == new_name)
k = len([e for e in eps2 if e["instruction_id"] == iid])
assert int(new_ep["episode_uid"].rsplit("-E", 1)[1]) == k - 1   # slot 의 k번째
print(f"2 통과: 재수집 {new_name} / {new_ep['episode_uid']} (slot 개수 기반)")

# ---- 3. SceneWriter.delete_episode (세션 경유 경로) 도 renumber ----
w2 = SceneWriter(d, scene_id=md.scene_id, resume=True)
w2.delete_episode("episode_000")
names3 = [e["name"] for e in w2.list_episodes()]
w2.close()
assert names3 == [f"episode_{i:03d}" for i in range(len(names3))]
r = np.random.default_rng(1)
print("3 통과: SceneWriter.delete_episode 후 연속 번호")

# ---- 4. Trim: scene 에피소드도 끝 다듬기 가능 ----
from gello.episode_trim import plan_trim, trim_tail  # noqa: E402

# 짧은 selftest 에피소드는 최소 길이 규칙에 막힌다 (규칙 적용 확인)
short = list_scene_episodes(scene)[0]
assert plan_trim(str(scene), [short["name"]], 2)[0].blocked
# 자를 수 있는 길이의 에피소드를 하나 만들어 실제 절단 경로 검증
w3 = SceneWriter(d, scene_id=md.scene_id, resume=True)
w3.start_episode()
for _ in range(40):
    w3.add_frame(
        agentview_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        eye_in_hand_rgb=r.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        joint_positions=r.standard_normal(7).astype(np.float32),
        gripper_position=0.5, ee_pos_quat=np.zeros(7), gripper_closed=False,
        commanded_joint_positions=r.standard_normal(7).astype(np.float32),
        commanded_gripper=0.0)
long_name = w3.save_buffer(w3.detach_buffer(), instruction=victim["instruction"],
                           instruction_id=iid, success=True, collector="t")
w3.close()
plan = plan_trim(str(scene), [long_name], 5)[0]
assert plan.scene and not plan.blocked, plan.blocked
keep = trim_tail(str(scene), long_name, 5)
with h5py.File(scene) as f:
    g = f[long_name]
    assert g["obs/agentview_rgb"].shape[0] == keep == 35
    assert g["actions"].shape[0] == 35
    assert int(g.attrs["num_samples"]) == 35 and g.attrs.get("trimmed")
    assert g.attrs["episode_uid"]                        # uid 등 attrs 보존
print(f"4 통과: scene 트림 40 -> {keep} (프레임축 전부 절단, attrs 보존) + 짧은 것은 규칙으로 차단")

# ---- 5. GUI 삭제 경로 (양포맷, 확인 다이얼로그 스텁) + 파일 삭제 status ----
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from gello.libero_format import hdf5_repack_status  # noqa: E402

cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)   # 성공분 경고 경로도 Yes
cw.QMessageBox.question = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
win = cw.WorkspaceWindow(None)
legacy = d / "selftest_task_demo.hdf5"
with h5py.File(legacy) as f:
    n_leg = len(f["data"])
sc_eps = list_scene_episodes(scene)
sc_names = [e["name"] for e in sc_eps]
victim_uid5 = sc_eps[0]["episode_uid"]
n5 = len(sc_eps)
ok = win._delete_episodes({scene: [sc_names[0]], legacy: ["demo_0"]})
assert ok
after = list_scene_episodes(scene)
assert len(after) == n5 - 1
assert [e["name"] for e in after] == [f"episode_{i:03d}" for i in range(n5 - 1)]
with h5py.File(legacy) as f:
    assert len(f["data"]) == n_leg - 1
    if n_leg > 1:
        assert "demo_0" in f["data"]                # legacy 는 renumber (앞당김)
st = hdf5_repack_status(scene)
assert st["error"] is None and st["episodes"] == len(sc_names) - 1
print("5 통과: GUI 삭제(양포맷 renumber 혼합) + 파일 status 정상")

# ---- 6. 검사기: 편집(삭제·트림) 뒤에도 불변식 통과 ----
from check_scene_file import verify_scene_file  # noqa: E402

probs = verify_scene_file(scene)
assert not probs, probs
print("6 통과: check_scene_file 불변식 (연속 번호·slot E 연속·uid 일치) 통과")

# ---- 7. 삭제 확인창: 목록 행 구성 + Hub 안내 (네트워크 스텁) ----
import gello.dataset_sync as _sync  # noqa: E402

_sync.hub_meta = lambda repo: ({sc_eps[0]["instruction"]: 3}, {}, "")   # 올라가 있음
rows, n_ok, note = win._describe_delete_targets(
    {scene: [e["name"] for e in list_scene_episodes(scene)][:2]})
assert len(rows) == 2 and all("EP-" in r_ for r_ in rows)
assert n_ok >= 0
if win.repo_id_for("repo_id"):
    assert "재빌드" in note, note        # Hub 에 있는 task 면 재빌드 안내
print(f"7 통과: 삭제 확인창 목록 {len(rows)}행 (성공 {n_ok}개, Hub 안내={'O' if note else '-'})")

print("\nscene 편집(삭제·트림) 검증 통과")
import os  # noqa: E402

os._exit(0)
