"""게이트 자동정렬 범위 조건 + 리셋 중 프레임 방출 + 시작 버튼 잠금 검증."""
import sys
from pathlib import Path
import threading
import time

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from gello.agents.lerobot_plugin import JOINT_KEYS  # noqa: E402
from gello.gui.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402

CFG = WorkerConfig(task_name="t", language_instruction="t", data_root="/tmp",
                   auto_match_pose=True, reset_wait_seconds=0.4)


class FakeTeleop:
    def __init__(self):
        self.leader_q = np.full(7, 1.5)      # 팔로워(0)에서 멀리
        self.match_started = 0
        self.cancelled = 0
        self.match_done = True               # False 면 정렬이 계속 진행 중

    def get_action(self):
        d = {k: float(self.leader_q[i]) for i, k in enumerate(JOINT_KEYS[:7])}
        d["gripper.pos"] = 0.0
        return d

    def start_pose_match(self, q):
        self.match_started += 1

    def cancel_pose_match(self):
        self.cancelled += 1

    def pose_match_status(self):
        return {"error": 0.0, "done": self.match_done}


def make_worker():
    w = CollectionWorker(CFG)
    w._teleop = FakeTeleop()
    obs = {k: 0.0 for k in JOINT_KEYS}
    obs["agent"] = np.zeros((4, 4, 3), np.uint8)
    obs["wrist"] = np.zeros((4, 4, 3), np.uint8)
    w._get_obs = lambda with_cameras=True: dict(obs)
    return w


# ---- 1. 리더가 범위 밖이어도: 자동정렬은 걸리고, 시작은 여전히 거부 ----
# 2026-09-01 사용자 결정: 정렬은 오차와 무관하게 시작할 수 있다 (모터 보호는
# wall 이 관절별로 맡는다 -- 정렬 반경 밖 관절은 토크 자체가 꺼져 있다).
# 반면 '텔레옵 시작' 은 자세가 맞아야 한다는 기존 게이트를 그대로 유지한다.
w = make_worker()
logs = []
w.log_message.connect(logs.append)
result = {}
t = threading.Thread(target=lambda: result.update(r=w._pose_gate(timeout=30)))
t.start()
time.sleep(0.4)
app.processEvents()      # 크로스 스레드 시그널(큐잉) 전달
assert w._teleop.match_started == 1, "오차가 커도 자동정렬은 걸려야 한다"
w.cmd_start_teleop()
time.sleep(0.3)
app.processEvents()
assert t.is_alive(), "자세 불일치인데 start 가 통과됨"
assert any("자세가 맞지 않습니다" in m for m in logs)
print("1 통과: 오차가 커도 자동정렬 발동 + start 는 거부")

# ---- 2. 범위 안으로 들어오면 start 통과 (정렬은 이미 1회 발동했다) ----
w._teleop.leader_q = np.full(7, GATE_RAD * 0.5)
time.sleep(0.4)
assert w._teleop.match_started == 1, "정렬이 중복 발동했다"
w.cmd_start_teleop()
t.join(3)
assert not t.is_alive() and result["r"] == "ok"
assert w._teleop.cancelled >= 1        # finally 에서 홀드 해제
print("2 통과: 범위 진입 -- 자동정렬 1회 + start 통과 + 홀드 해제")

# ---- 2b. 정렬 중 범위 이탈 -> 중단, 복귀 -> 자동 재시도 ----
wb = make_worker()
logs_b = []
wb.log_message.connect(logs_b.append)
wb._teleop.leader_q = np.full(7, GATE_RAD * 0.5)   # 범위 안에서 시작
wb._teleop.match_done = False                       # 정렬이 진행 중인 상태 유지
rb = {}
tb = threading.Thread(target=lambda: rb.update(r=wb._pose_gate(timeout=30)))
tb.start()
time.sleep(0.3)
assert wb._teleop.match_started == 1                # 정렬 발동
# 2026-09-01: 정렬 중 리더를 끌어도 중단하지 않는다. wall 이 그 관절을
# 관절별로 놓아 주고(우물이 약해지고 정렬 반경 밖은 토크 오프), 손을 놓으면
# 이어서 당긴다 -- 모터가 사람 손과 싸우는 상황이 구조적으로 없어졌다.
wb._teleop.leader_q = np.full(7, 1.5)               # 정렬 중 범위 밖으로 끌기
time.sleep(0.3)
app.processEvents()
assert tb.is_alive(), "정렬 루프가 죽었다"
assert not any("중단" in m for m in logs_b), logs_b
assert wb._teleop.match_started == 1, "정렬이 중복 발동했다"
wb._teleop.match_done = True
wb._teleop.leader_q = np.full(7, GATE_RAD * 0.5)    # 다시 범위 안으로
time.sleep(0.3)
wb.cmd_start_teleop()
tb.join(3)
assert rb["r"] == "ok"
print("2b 통과: 정렬 중 이탈해도 중단 없이 이어짐 (wall 이 관절별로 처리)")

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

# ---- 4. 리셋 대기: 자동 종료 없음, 버튼으로만 끝, 프레임은 흐른다 ----
w3 = make_worker()
# 2026-09-01: 기록 외 단계에서 이 루프는 카메라를 읽지 않는다 (게이지를
# 빠르게 유지하려고). 리셋 중 살아 있어야 하는 것은 오차 게이지이고,
# 라이브 뷰는 미리보기 스레드가 노드 속도로 직접 그린다.
frames = []
w3.gate_status.connect(lambda a, b, c: frames.append(1))
r4 = {}
t4 = threading.Thread(target=lambda: r4.update(r=w3._reset_wait()))
t4.start()
time.sleep(0.6)          # cfg 의 0.4s 를 지나도
assert t4.is_alive(), "리셋 대기가 시간 경과로 저절로 끝남"
w3.cmd_skip_reset_wait()
t4.join(3)
assert r4["r"] == "ok"
app.processEvents()
assert len(frames) >= 2, f"리셋 중 게이지 갱신 {len(frames)}회"
# _get_obs 가 죽어도 루프는 계속되고 버튼으로 끝난다
w3b = make_worker()
def boom():
    raise RuntimeError("cam")
w3b._get_obs = boom
r4b = {}
t4b = threading.Thread(target=lambda: r4b.update(r=w3b._reset_wait()))
t4b.start()
time.sleep(0.3)
assert t4b.is_alive()
w3b.cmd_skip_reset_wait()
t4b.join(3)
assert r4b["r"] == "ok"
print(f"4 통과: 자동 종료 없음 + 버튼 종료 + 프레임 {len(frames)}회 + obs 오류 내성")

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
