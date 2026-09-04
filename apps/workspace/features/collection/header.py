"""수집 HUD -- 카메라 뷰 바로 위에 고정되는 지시문·수집량·상태 띠.

왜 왼쪽 패널이 아니라 여기인가 (2026-09-04, 이슈 #38/#39):
조작자는 서서 리더암을 두 손으로 잡고, 눈은 로봇에 두고 1~1.5m 떨어진
화면을 곁눈질한다. 그런데 같은 정보가 왼쪽 Collect 페이지의 "진행" 상자에
16pt 로 있었고, 그 상자는 스크롤되는 패널의 맨 아래였다 -- 세션이 시작되면
위쪽 상자들에 밀려 y=800, 높이 395 로 패널(1009px)을 넘어 아래가 잘렸다.
"수집 갯수가 어디 보이냐"는 물음이 세 번 나온 이유다.

그래서 스크롤되지 않는 자리(카메라 위)로 올리고 거리에서 읽히게 키웠다.
숫자의 정본은 그대로 CollectionOps._refresh_slot_counter 하나다 -- 이
모듈은 위젯만 만들고, 채우는 것은 그쪽이다.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gello.gui.i18n import tr

#: 상태별 띠 배경. 기록 중만 눈에 띄게 -- 나머지는 조용히.
STATE_COLORS = {
    "recording": "#c0392b",
    "reset_wait": "#d68910",
    "gate": "#2471a3",
    "approach": "#2471a3",
    "homing": "#5d6d7e",
}
IDLE_COLOR = "#3b3b3b"


def build_collect_header(win) -> QWidget:
    bar = QFrame()
    bar.setFrameShape(QFrame.Shape.NoFrame)
    bar.setAutoFillBackground(True)
    row = QHBoxLayout(bar)
    row.setContentsMargins(14, 8, 14, 8)
    row.setSpacing(18)

    left = QVBoxLayout()
    left.setSpacing(2)
    win.hud_instruction = QLabel(tr("(수집 세션 없음)"))
    win.hud_instruction.setFont(QFont("", 20, QFont.Weight.Bold))
    win.hud_instruction.setWordWrap(True)
    win.hud_instruction.setStyleSheet("color:#fff;")
    left.addWidget(win.hud_instruction)
    win.hud_slot = QLabel("")
    win.hud_slot.setStyleSheet("color:#cfd8dc; font-size:12px;")
    left.addWidget(win.hud_slot)
    row.addLayout(left, 1)

    # 수집량 -- 이 띠에서 가장 큰 글자. 1m 거리에서 읽혀야 한다.
    win.hud_counter = QLabel("—")
    win.hud_counter.setFont(QFont("", 30, QFont.Weight.Bold))
    win.hud_counter.setStyleSheet("color:#fff;")
    win.hud_counter.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
    win.hud_counter.setMinimumWidth(150)
    row.addWidget(win.hud_counter)

    win.hud_state = QLabel(tr("대기"))
    win.hud_state.setFont(QFont("", 13, QFont.Weight.Bold))
    win.hud_state.setStyleSheet("color:#fff;")
    win.hud_state.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
    win.hud_state.setMinimumWidth(210)
    row.addWidget(win.hud_state)

    win.hud_bar = bar
    set_header_state(win, "idle")
    return bar


def set_header_state(win, state: str) -> None:
    """띠 배경색과 상태 문구. 색이 곧 상태다 -- 글자를 읽지 않아도 기록
    중인지 아닌지 보이게."""
    bar = getattr(win, "hud_bar", None)
    if bar is None:
        return
    bar.setStyleSheet(
        f"background-color:{STATE_COLORS.get(state, IDLE_COLOR)};")
    win.hud_state.setText(win.STATE_LABELS.get(state, state))
