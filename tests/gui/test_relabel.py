"""에피소드 재판정 경로 검증.

- owned 파일(세션이 쥔 파일): h5py.File 재오픈 없이 saver 큐로만 전달.
- 캐시에 없는 이름: 걸러냄 집계, 예외 없음.
- 비소유 파일: 기존 직접 수정 동작 유지 (scene + legacy).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import h5py

WT = str(Path(__file__).resolve().parents[2])  # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/experiments")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from gello.scene_format import list_scene_episodes  # noqa: E402

d = Path(tempfile.mkdtemp(prefix="relabel_"))
subprocess.run([sys.executable, WT + "/scripts/check_scene_file.py",
                "--selftest", "--keep", str(d)], check=True, capture_output=True)
scene = d / "scene_000.hdf5"
legacy = d / "selftest_task_demo.hdf5"

cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None

win = cw.WorkspaceWindow(None)


class StubSaver:
    def __init__(self):
        self.calls = []

    def enqueue_set_success(self, name, success):
        self.calls.append((name, success))


class StubWorker:
    def __init__(self, saver):
        self.saver = saver

    def cmd_set_episode_success(self, name, success):
        self.saver.enqueue_set_success(name, success)


stub_saver = StubSaver()
win.worker = StubWorker(stub_saver)

# ---- 1. owned scene 파일 재판정: h5py.File 호출 금지, saver 큐로 전달 ----
eps = list_scene_episodes(scene)
success_ep = next(e for e in eps if e["quality_status"] == "success")
bad_data_ep = next(e for e in eps if e["quality_status"] == "bad_data")

real_h5py_File = h5py.File
h5py_calls = []


def fail_h5py_File(*args, **kwargs):
    h5py_calls.append((args, kwargs))
    raise RuntimeError("owned 파일 재판정에서 h5py.File 호출 금지")


h5py.File = fail_h5py_File
try:
    win.active_file_path = scene
    win.active_episode_cache = eps
    ok = win._relabel_episodes(
        {scene: [success_ep["name"], bad_data_ep["name"]]})
    assert ok
    # bad_data 는 success/failed 가 아니므로 skipped 되고 saver 호출은 1개뿐
    assert len(stub_saver.calls) == 1
    assert stub_saver.calls[0] == (success_ep["name"], False)
    assert len(h5py_calls) == 0, h5py_calls
finally:
    h5py.File = real_h5py_File
print("1 통과: owned scene 재판정이 h5py.File 없이 saver 큐로 전달")

# ---- 2. 캐시에 없는 이름은 걸러냄 + 예외 없음 ----
stub_saver.calls.clear()
win.active_episode_cache = [success_ep]
ok = win._relabel_episodes({scene: [success_ep["name"], bad_data_ep["name"]]})
assert ok
assert len(stub_saver.calls) == 1
assert stub_saver.calls[0] == (success_ep["name"], False)
print("2 통과: 캐시에 없는 이름은 걸러냄")

# ---- 3. 비소유 파일 경로 회귀 (scene + legacy) ----
win.active_file_path = None
win.active_episode_cache = None

eps = list_scene_episodes(scene)
target_scene = eps[0]
old_q = target_scene["quality_status"]
ok = win._relabel_episodes({scene: [target_scene["name"]]})
assert ok
eps_after = list_scene_episodes(scene)
new_q = next(e["quality_status"] for e in eps_after
             if e["name"] == target_scene["name"])
assert new_q != old_q
assert new_q in ("success", "failed")

with h5py.File(legacy) as f:
    demos = sorted(f["data"].keys())
    target_legacy = demos[0]
    old_ok = bool(f["data"][target_legacy].attrs.get("success", True))
ok = win._relabel_episodes({legacy: [target_legacy]})
assert ok
with h5py.File(legacy) as f:
    new_ok = bool(f["data"][target_legacy].attrs.get("success", True))
assert new_ok != old_ok
print("3 통과: 비소유 파일(scene+legacy) 직접 수정 회귀")

print("\n재판정 경로 검증 통과")
import os  # noqa: E402

os._exit(0)
