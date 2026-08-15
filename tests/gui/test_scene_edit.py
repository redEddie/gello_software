"""scene 에피소드 편집(삭제·트림) 검증 -- 툼스톤 삭제, uid 재사용 금지,
GUI 삭제 경로(양포맷), Trim 양포맷, 파일 삭제 status."""
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

# ---- 1. 직접 삭제: 번호 유지(빈자리) + 툼스톤 기록 ----
delete_scene_episodes(scene, [victim["name"]])
eps1 = list_scene_episodes(scene)
assert len(eps1) == n_before - 1
assert victim["name"] not in [e["name"] for e in eps1]
# 남은 그룹 이름은 그대로 (renumber 없음)
assert [e["name"] for e in eps1] == [n for n in names0 if n != victim["name"]]
with h5py.File(scene) as f:
    tomb = json.loads(f["metadata"].attrs["deleted_uids"])
    nxt = int(f["metadata"].attrs["next_episode_idx"])
assert tomb == [victim_uid]
print(f"1 통과: 삭제 -> 번호 유지, 툼스톤 {tomb}")

# ---- 2. 같은 slot 에 새 에피소드 -> 지운 E번호 재사용 금지, 그룹 번호는 next 이어감 ----
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
assert new_name == f"episode_{nxt:03d}"          # 빈자리 아닌 다음 번호
eps2 = list_scene_episodes(scene)
new_ep = next(e for e in eps2 if e["name"] == new_name)
assert new_ep["episode_uid"] != victim_uid, "지운 uid 재사용됨"
old_e = int(victim_uid.rsplit("-E", 1)[1])
new_e = int(new_ep["episode_uid"].rsplit("-E", 1)[1])
assert new_e == old_e + 1, (old_e, new_e)
print(f"2 통과: 재수집 uid {new_ep['episode_uid']} (지운 E{old_e:03d} 건너뜀), 그룹 {new_name}")

# ---- 3. SceneWriter.delete_episode (세션 경유 경로) ----
w2 = SceneWriter(d, scene_id=md.scene_id, resume=True)
w2.delete_episode(new_name)
assert new_name not in [e["name"] for e in w2.list_episodes()]
w2.close()
with h5py.File(scene) as f:
    tomb = json.loads(f["metadata"].attrs["deleted_uids"])
assert new_ep["episode_uid"] in tomb and victim_uid in tomb
print("3 통과: SceneWriter.delete_episode + 툼스톤 누적")

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
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
cw.QMessageBox.question = staticmethod(
    lambda *a, **k: cw.QMessageBox.StandardButton.Yes)
win = cw.WorkspaceWindow(None)
legacy = d / "selftest_task_demo.hdf5"
with h5py.File(legacy) as f:
    n_leg = len(f["data"])
sc_names = [e["name"] for e in list_scene_episodes(scene)]
ok = win._delete_episodes({scene: [sc_names[0]], legacy: ["demo_0"]})
assert ok
assert sc_names[0] not in [e["name"] for e in list_scene_episodes(scene)]
with h5py.File(legacy) as f:
    assert len(f["data"]) == n_leg - 1
    if n_leg > 1:
        assert "demo_0" in f["data"]                # legacy 는 renumber (앞당김)
st = hdf5_repack_status(scene)
assert st["error"] is None and st["episodes"] == len(sc_names) - 1
print("5 통과: GUI 삭제(scene 툼스톤 + legacy renumber 혼합) + 파일 status 정상")

print("\nscene 편집(삭제·트림) 검증 통과")
import os  # noqa: E402

os._exit(0)
