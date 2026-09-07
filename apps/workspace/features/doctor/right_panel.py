"""Doctor 의 우측 패널 -- 고른 것의 진단과 조치.

우측 패널은 활동탭마다 자기 구현을 갖는다 (2026-09-07 사용자 결정). 닥터가
첫 구현이다.

**고른 것에는 두 범위가 있다.** 왼쪽에서 고른 scene, 그리고 가운데 표에서
고른 지시문. 그래서 상자도 둘이고, 진단과 조치가 각각 자기 범위 안에 있다:

    이 scene   무엇이 잘못됐나 · 소품·배치 고치기 · 파일 상태
    이 지시문  왜 걸렸나 · 문장 고치기 / 교환 / 계획에서 빼기

전에는 소품 정정이 가운데에 있었다 -- scene 범위의 조치인데 자리가 달라서,
같은 화면인데 어디를 눌러야 할지가 대상마다 달랐다 (2026-09-07 사용자
지적). 진단 표시도 가운데와 오른쪽에 흩어져 있었다. 지금은 가운데가 보는
자리, 오른쪽이 고치는 자리로 갈렸다.
"""
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr

#: (ops 메서드 이름, 라벨, 툴팁)
SCENE_ACTIONS = (
    ("edit_objects", "소품 고치기...",
     "기록된 물체를 인벤토리에서 다시 고릅니다. 고치기 전에 지금과 고친 뒤를 "
     "나란히 봅니다. 에피소드는 바뀌지 않습니다."),
)

TASK_ACTIONS = (
    ("edit_task_text", "문장 고치기...",
     "동작을 고르고 문장을 고릅니다 (문법이 만든 것만). 이 지시문의 "
     "에피소드가 모두 바뀌고, Hub 에 올렸다면 전체 재빌드·재푸시가 "
     "필요해집니다."),
    ("swap_task_text", "다른 지시문과 교환...",
     "두 지시문의 문장을 맞바꿉니다. 라벨이 서로 바뀌어 기록된 것을 되돌릴 "
     "때, 그리고 안 찍은 빈 칸의 옳은 문장을 가져올 때 씁니다."),
    ("remove_task", "계획에서 빼기",
     "더는 이 문장으로 찍지 않습니다. 에피소드는 지우지 않습니다 "
     "(있으면 뺄 수 없습니다)."),
)


def _rule() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#cccccc;")
    return f


def _buttons(win, box: QVBoxLayout, actions, store: dict) -> None:
    for name, label, tip in actions:
        b = QPushButton(tr(label))
        b.setToolTip(tr(tip))
        # ops 를 이름으로 늦게 찾는다 -- 패널이 DoctorOps 보다 먼저 생긴다.
        b.clicked.connect(lambda _c=False, n=name: getattr(win.doctor, n)())
        b.setEnabled(False)
        store[name] = b
        box.addWidget(b)


def build_doctor_right(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # --- 이 scene ------------------------------------------------------
    sbox = QGroupBox(tr("이 scene"))
    sv = QVBoxLayout(sbox)
    sv.setContentsMargins(6, 6, 6, 6)
    win.doctor_scene_detail = QLabel(tr("왼쪽에서 scene 을 고르세요"))
    win.doctor_scene_detail.setWordWrap(True)
    win.doctor_scene_detail.setStyleSheet("color:#333;")
    sv.addWidget(win.doctor_scene_detail)
    sv.addWidget(_rule())
    win.doctor_scene_buttons = {}
    _buttons(win, sv, SCENE_ACTIONS, win.doctor_scene_buttons)
    col.addWidget(sbox)

    # --- 이 지시문 -----------------------------------------------------
    tbox = QGroupBox(tr("이 지시문"))
    tv = QVBoxLayout(tbox)
    tv.setContentsMargins(6, 6, 6, 6)
    win.doctor_task_detail = QLabel(tr("가운데 표에서 지시문을 고르세요"))
    win.doctor_task_detail.setWordWrap(True)
    win.doctor_task_detail.setStyleSheet("color:#333;")
    tv.addWidget(win.doctor_task_detail)
    tv.addWidget(_rule())
    win.doctor_task_buttons = {}
    _buttons(win, tv, TASK_ACTIONS, win.doctor_task_buttons)
    col.addWidget(tbox)

    col.addStretch(1)
    return w
