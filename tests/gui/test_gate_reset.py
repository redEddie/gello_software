"""게이트 자동정렬 범위 조건 + 리셋 중 프레임 방출 + 시작 버튼 잠금 검증."""
import sys
from pathlib import Path
import threading
import time

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/experiments")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from gello.lerobot_plugin import JOINT_KEYS  # noqa: E402
from gello.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402

CFG = WorkerConfig(task_name="t", language_instruction="t", data_root="/tmp",
                   auto_match_pose=True, reset_wait_seconds=0.4)


class FakeTeleop:
    def __init__(self):
        self.leader_q = np.full(7, 1.5)      # 팔로워(0)에서 멀리
        self.match_started = 0
        self.cancelled = 0

    def get_action(self):
        d = {k: float(self.leader_q[i]) for i, k in enumerate(JOINT_KEYS[:7])}
        d["gripper.pos"] = 0.0
        return d

    def start_pose_match(self, q):
        self.match_started += 1

    def cancel_pose_match(self):
        self.cancelled += 1

    def pose_match_status(self):
        return {"error": 0.0, "done": True}


def make_worker():
    w = CollectionWorker(CFG)
    w._teleop = FakeTeleop()
    obs = {k: 0.0 for k in JOINT_KEYS}
    obs["agent"] = np.zeros((4, 4, 3), np.uint8)
    obs["wrist"] = np.zeros((4, 4, 3), np.uint8)
    w._get_obs = lambda: dict(obs)
    return w


# ---- 1. 리더가 범위 밖이면: 자동정렬 발동 금지 + 시작 거부 ----
w = make_worker()
logs = []
w.log_message.connect(logs.append)
result = {}
t = threading.Thread(target=lambda: result.update(r=w._pose_gate(timeout=30)))
t.start()
time.sleep(0.4)
app.processEvents()      # 크로스 스레드 시그널(큐잉) 전달
assert w._teleop.match_started == 0, "범위 밖인데 자동정렬이 당김"
assert any("범위 밖" in m for m in logs), logs
w.cmd_start_teleop()
time.sleep(0.3)
app.processEvents()
assert t.is_alive(), "자세 불일치인데 start 가 통과됨"
assert any("자세가 맞지 않습니다" in m for m in logs)
print("1 통과: 범위 밖 -- 자동정렬 미발동 + start 거부")

# ---- 2. 범위 안으로 들어오면: 자동정렬 1회 발동, start 통과 ----
w._teleop.leader_q = np.full(7, GATE_RAD * 0.5)
time.sleep(0.4)
assert w._teleop.match_started == 1, "범위 진입 후 자동정렬이 안 걸림"
w.cmd_start_teleop()
t.join(3)
assert not t.is_alive() and result["r"] == "ok"
assert w._teleop.cancelled >= 1        # finally 에서 홀드 해제
print("2 통과: 범위 진입 -- 자동정렬 1회 + start 통과 + 홀드 해제")

# ---- 3. 자동정렬 꺼짐: 발동 없음, 수동 게이트만 ----
import dataclasses  # noqa: E402

w2 = CollectionWorker(dataclasses.replace(CFG, auto_match_pose=False))
w2._teleop = FakeTeleop()
w2._teleop.leader_q = np.zeros(7)
w2._get_obs = w._get_obs
r2 = {}
t2 = threading.Thread(target=lambda: r2.update(r=w2._pose_gate(timeout=30)))
t2.start()
time.sleep(0.3)
assert w2._teleop.match_started == 0
w2.cmd_start_teleop()
t2.join(3)
assert r2["r"] == "ok"
print("3 통과: 자동정렬 꺼짐 -- 발동 없이 수동 게이트")

# ---- 4. 리셋 대기 중에도 프레임이 흐른다 ----
w3 = make_worker()
frames = []
w3.frames_ready.connect(lambda a, b: frames.append(1))
t0 = time.monotonic()
r = w3._reset_wait()
assert r == "ok" and time.monotonic() - t0 >= 0.35
assert len(frames) >= 2, f"리셋 중 프레임 {len(frames)}회"
# _get_obs 가 죽어도 카운트다운은 계속
w3b = make_worker()
def boom():
    raise RuntimeError("cam")
w3b._get_obs = boom
assert w3b._reset_wait() == "ok"
print(f"4 통과: 리셋 중 프레임 {len(frames)}회 + obs 오류에도 카운트다운 지속")

# ---- 5. GUI: 게이트에서 자세 맞을 때만 Start 활성 ----
import collect_workspace as cw  # noqa: E402

cw.WorkspaceWindow._refresh_cameras = lambda self: None
cw.WorkspaceWindow._restart_previews = lambda self: None
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win = cw.WorkspaceWindow(None)
assert not win.start_btn.isEnabled() and not win.tb_actions["record"].isEnabled()
win.worker = object()          # 세션 흉내
win._set_running(True)
win._on_state("gate")
assert not win.start_btn.isEnabled(), "게이트 진입 직후 열려 있음"
eight = np.zeros(8)
win._on_gate(eight + 1.0, eight, False)
assert not win.start_btn.isEnabled()
win._on_gate(eight, eight, True)
assert win.start_btn.isEnabled() and win.tb_actions["record"].isEnabled()
win._on_gate(eight + 1.0, eight, False)   # 다시 어긋나면 잠김
assert not win.start_btn.isEnabled()
win._on_state("recording")
assert win.start_btn.isEnabled()
win.worker = None
win._set_running(False)
assert not win.start_btn.isEnabled()
print("5 통과: Start 버튼/툴바 -- 게이트 자세 조건 연동")

print("\n게이트·리셋 수정 검증 통과")
import os  # noqa: E402

os._exit(0)
