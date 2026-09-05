"""단축키가 '누르고 있는 것'을 연타로 읽지 않는지 검증.

2026-09-05 사고: 같은 Space 가 상태마다 뜻이 다른데(gate=텔레옵 시작 /
recording=저장), 눌러 둔 키의 반복분이 그 경계를 넘어 방금 시작한 에피소드를
즉시 끝냈다 -- 2프레임짜리가 저장됐다. 이 기계는 500ms 뒤부터 33Hz 로
반복분을 보낸다(xset q).

여기서는 창을 통째로 띄우지 않고 eventFilter 만 실제 객체에 물려 확인한다.
창 생성은 카메라·노드·데이터셋을 건드려 이 스위트의 계약(하드웨어 불필요)을
깬다.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)
sys.argv = ["t"]

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402

app = QApplication(sys.argv)

from apps.collect_workspace import WorkspaceWindow  # noqa: E402


def Spy(state):
    """창 대신 세워 두는 최소 대역. eventFilter 가 부르는 것만 채운다.

    __init__ 을 건너뛰고 QMainWindow 만 초기화한다 -- WorkspaceWindow.__init__
    은 카메라·노드·데이터셋을 건드려 이 스위트의 계약(하드웨어 불필요)을
    깬다. 그래도 타입은 WorkspaceWindow 여야 한다: eventFilter 의 마지막
    줄이 인자 없는 super() 라 진짜 상속 관계를 요구한다.
    """
    w = WorkspaceWindow.__new__(WorkspaceWindow)
    QMainWindow.__init__(w)
    w.sent = []
    w.worker = object()          # None 이면 eventFilter 가 통째로 건너뛴다
    w.session = SimpleNamespace(current_state=state, gate_ok=True,
                                no_dataset_session=False)
    w.collection = SimpleNamespace(
        save=lambda ok: w.sent.append(("save", ok)),
        cmd=lambda name: w.sent.append((name,)))
    w.cameras = SimpleNamespace(live_maximized=None, depth_img=None,
                                depth_cursor=None)
    w.live_views = {}
    w.log = lambda *_a: None
    return w


def press(spy, key, *, repeat):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier,
                   0, 0, 0, autorep=repeat)
    return WorkspaceWindow.eventFilter(spy, QWidget(), ev)


# --- 1) 최초 입력은 상태별로 그대로 듣는다 --------------------------------
spy = Spy("gate")
press(spy, Qt.Key.Key_Space, repeat=False)
assert spy.sent == [("cmd_start_teleop",)], spy.sent

spy = Spy("recording")
press(spy, Qt.Key.Key_Space, repeat=False)
assert spy.sent == [("save", True)], spy.sent
print("1 통과: 최초 KeyPress 는 상태별 동작을 그대로 수행한다")

# --- 2) 반복분은 어떤 상태에서도 무시된다 ---------------------------------
# "아무 입력 없음"이 아니라 "한 번 누른 것" -- 최초분은 위에서 이미 들었다.
for state, key in (("gate", Qt.Key.Key_Space),
                   ("recording", Qt.Key.Key_Space),
                   ("recording", Qt.Key.Key_Escape),
                   ("recording", Qt.Key.Key_Delete),
                   ("reset_wait", Qt.Key.Key_Return)):
    spy = Spy(state)
    press(spy, key, repeat=True)
    assert spy.sent == [], f"{state}/{key}: 반복분이 명령을 만들었다 -- {spy.sent}"
print("2 통과: 반복분은 상태와 무관하게 명령을 만들지 않는다")

# --- 3) 사고 재현: 눌러 둔 Space 가 상태 경계를 넘는 경우 ------------------
# gate 에서 누르기 시작 -> 기록이 시작된 뒤 반복분이 도착. 고치기 전에는
# 여기서 save 가 나가 2프레임짜리 에피소드가 저장됐다.
spy = Spy("gate")
press(spy, Qt.Key.Key_Space, repeat=False)     # 텔레옵 시작
spy.session.current_state = "recording"        # approach 가 끝나 기록 시작
press(spy, Qt.Key.Key_Space, repeat=True)      # 500ms 뒤 첫 반복분
assert spy.sent == [("cmd_start_teleop",)], \
    f"눌러 둔 키가 기록을 끝냈다: {spy.sent}"
print("3 통과: 눌러 둔 Space 가 상태 경계를 넘어 저장을 걸지 않는다")

# --- 4) 뗐다 다시 누르면 정상 동작 ----------------------------------------
press(spy, Qt.Key.Key_Space, repeat=False)
assert spy.sent == [("cmd_start_teleop",), ("save", True)], spy.sent
print("4 통과: 떼고 다시 누르면 저장이 정상 동작한다")

print("\n키 반복 검증 통과")
