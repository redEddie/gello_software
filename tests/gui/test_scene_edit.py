"""scene 에피소드 편집(삭제·트림) 검증 -- 삭제 후 renumber(그룹·episode_id·
slot E번호·uid), GUI 삭제 경로(양포맷)+확인창, Trim 양포맷, 파일 status."""
import ast
import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.path.insert(0, WT + "/scripts/check")
sys.argv = ["t"]

from gello.data.dataset_schema import OBS_AGENTVIEW_RGB  # noqa: E402
from gello.scene.scene_format import (  # noqa: E402
    QUALITY_SUCCESS,
    SceneWriter, delete_scene_episodes, list_scene_episodes, read_scene_metadata,
)

d = Path(tempfile.mkdtemp(prefix="sceneedit_"))
subprocess.run([sys.executable, WT + "/scripts/check/check_scene_file.py",
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
from gello.data.episode_trim import plan_trim, trim_tail  # noqa: E402

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
    assert g[f"obs/{OBS_AGENTVIEW_RGB}"].shape[0] == keep == 35
    assert g["actions"].shape[0] == 35
    assert int(g.attrs["num_samples"]) == 35 and g.attrs.get("trimmed")
    assert g.attrs["episode_uid"]                        # uid 등 attrs 보존
print(f"4 통과: scene 트림 40 -> {keep} (프레임축 전부 절단, attrs 보존) + 짧은 것은 규칙으로 차단")

# ---- 5. GUI 삭제 경로 (양포맷, 확인 다이얼로그 스텁) + 파일 삭제 status ----
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from gello.data.libero_format import hdf5_repack_status  # noqa: E402

cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
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
ok = win.dataset_ops.delete_episodes({scene: [sc_names[0]], legacy: ["demo_0"]})
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

# ---- 7. 삭제 확인창: 목록 + Hub 안내는 uid 단위 (네트워크 스텁 3경로) ----
import gello.data.dataset_sync as _sync  # noqa: E402

cur = list_scene_episodes(scene)
targets = {scene: [e["name"] for e in cur][:2]}
uid0 = cur[0]["episode_uid"]
have_repo = bool(win.upload.repo_id_for("repo_id"))
# (a) 사이드카에 이 uid 가 있음 -> "올라가 있음 + 재빌드"
_sync.hub_episode_uids = lambda repo: ({uid0}, "")
_sync.hub_meta = lambda repo: ({cur[0]["instruction"]: 99}, {}, "")
rows, n_ok, note = win.dataset_ops.describe_delete_targets(targets)
assert len(rows) == 2 and all("EP-" in r_ for r_ in rows)
if have_repo:
    assert "재빌드" in note and uid0 in note, note
# (b) 사이드카는 있는데 이 uid 없음 -> 같은 문장이 있어도 "올라가 있지 않음"
_sync.hub_episode_uids = lambda repo: ({"EP-S999-I000-E000"}, "")
_, _, note_b = win.dataset_ops.describe_delete_targets(targets)
if have_repo:
    assert "올라가 있지 않습니다" in note_b, note_b
# (c) 사이드카 없음(legacy repo) -> 문장 일치는 '참고' 로만, 올라갔다고 하지 않음
_sync.hub_episode_uids = lambda repo: (None, "")
_, _, note_c = win.dataset_ops.describe_delete_targets(targets)
if have_repo:
    assert "참고" in note_c and "올라갔다는 뜻은 아닙니다" in note_c, note_c
print(f"7 통과: 확인창 목록 {len(rows)}행 + Hub 안내 uid 단위 3경로 (repo 설정={'O' if have_repo else '-'})")

# ---- 8. 회귀: WorkspaceWindow 메서드 중복 정의 없음 ----
src = Path(WT) / "apps" / "collect_workspace.py"
tree = ast.parse(src.read_text(encoding="utf-8"))
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == "WorkspaceWindow":
        seen = collections.defaultdict(list)
        for it in node.body:
            if isinstance(it, ast.FunctionDef):
                seen[it.name].append(it.lineno)
        dups = {k: v for k, v in seen.items() if len(v) > 1}
        assert not dups, f"중복 정의: {dups}"
print("8 통과: WorkspaceWindow 메서드 중복 정의 없음")

# ---- 9. 회귀: 버튼 슬롯 경로에서 _describe_delete_targets 가 실제로 호출되고
#          재빌드 안내가 뜨며 툼스톤 문구가 없음 ----
class _MockParent:
    def __init__(self, path): self._path = path
    def data(self, column, role): return str(self._path)

class _MockItem:
    def __init__(self, parent, name): self._parent = parent; self._name = name
    def parent(self): return self._parent
    def data(self, column, role): return self._name

captured_dialogs = []

def _capture_warning(parent, title, body, *args, **kwargs):
    captured_dialogs.append(("warning", str(title), str(body), args, kwargs))
    return cw.QMessageBox.StandardButton.Yes

def _capture_question(parent, title, body, *args, **kwargs):
    captured_dialogs.append(("question", str(title), str(body), args, kwargs))
    return cw.QMessageBox.StandardButton.Yes

cw.QMessageBox.warning = staticmethod(_capture_warning)
cw.QMessageBox.question = staticmethod(_capture_question)
win.dataset_ops.refresh_dataset_tree = lambda: None

win.repo_edits["repo_id"].setText("test/repo")
uid9 = cur[0]["episode_uid"]
_sync.hub_episode_uids = lambda repo: ({uid9}, "")
_sync.hub_meta = lambda repo: ({cur[0]["instruction"]: 99}, {}, "")

win.dataset_tree.selectedItems = lambda: [_MockItem(_MockParent(scene), cur[0]["name"])]
captured_dialogs.clear()
win.dataset_ops.on_delete_selected()

assert captured_dialogs, "확인창이 뜨지 않음"
kind, title, body, wargs, wkw = captured_dialogs[-1]
assert "에피소드 삭제" in title
assert "재빌드" in body, body
assert "번호가 다시 매겨집니다" in body
assert "번호는 그대로" not in body, body
assert "재사용 금지" not in body, body
# 성공(success) 에피소드가 섞이면 warning + 기본 버튼 No 여야 한다 --
# 함수 직접 호출이 아니라 슬롯 경로에서 검증 (기본 Yes 로 되돌아가는 회귀 방지).
if cur[0].get("quality_status") == QUALITY_SUCCESS:
    assert kind == "warning", kind
    default = wkw.get("defaultButton", wargs[-1] if wargs else None)
    assert default == cw.QMessageBox.StandardButton.No, (wargs, wkw)
print("9 통과: 버튼 슬롯 경로에서 확인창 문구 실제 동작 기준 (재빌드 O, 툼스톤 X, 기본 No)")

# ---- 10. 썸네일 캐시 무효화: scene 삭제/renumber 로 uid 가 재배정되면
#    기존 썸네일이 잘못된 에피소드에 표시될 수 있다.
from gello.gui.scene_gallery import invalidate_scene_thumbs  # noqa: E402

td = Path(tempfile.mkdtemp(prefix="thumbs_"))
(td / "EP-S000-I000-E000.jpg").write_text("a")
(td / "EP-S000-I000-E001.jpg").write_text("b")
(td / "EP-S000-I001-E000.jpg").write_text("c")
(td / "EP-S001-I000-E000.jpg").write_text("other")
(td / "legacy.jpg").write_text("legacy")
n = invalidate_scene_thumbs("S000", thumbs_dir=td)
assert n == 3, n
assert not (td / "EP-S000-I000-E000.jpg").exists()
assert not (td / "EP-S000-I000-E001.jpg").exists()
assert not (td / "EP-S000-I001-E000.jpg").exists()
assert (td / "EP-S001-I000-E000.jpg").exists()
assert (td / "legacy.jpg").exists()
# 없는 scene 은 0개
assert invalidate_scene_thumbs("S999", thumbs_dir=td) == 0
print("10 통과: 썸네일 캐시 scene 단위 무효화 (다른 scene/비대상 파일 보존)")

print("\nscene 편집(삭제·트림) 검증 통과")
import os  # noqa: E402

os._exit(0)
