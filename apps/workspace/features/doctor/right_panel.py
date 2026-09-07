"""Doctor 의 우측 패널 -- 이 활동만의 구현.

우측 패널은 활동탭마다 **자기 구현**을 갖는다 (2026-09-07 사용자 결정).
공용 정보 상자를 활동에 따라 골라 보여주는 자리가 아니다 -- 그렇게 하면
패널이 무엇을 하는 곳인지가 활동마다 달라지는데 화면은 같아 보인다.
닥터가 그 첫 구현이다.

여기 있는 것은 **고른 지시문 한 줄에 하는 일**이다. 표는 가운데에서 보고,
고칠 때는 여기서 누른다.
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

#: 이 패널이 다루는 조치. (키, 라벨, 툴팁) -- ops 의 같은 이름 메서드를 부른다.
ACTIONS = (
    ("edit_task_text", "문장 고치기...",
     "이 지시문의 에피소드가 모두 바뀝니다. Hub 에 올렸다면 전체 "
     "재빌드·재푸시가 필요해집니다."),
    ("swap_task_text", "다른 지시문과 교환...",
     "두 지시문의 문장을 맞바꿉니다. 라벨이 서로 바뀌어 기록된 것을 되돌릴 "
     "때, 그리고 안 찍은 빈 칸의 옳은 문장을 가져올 때 씁니다."),
    ("remove_task", "계획에서 빼기",
     "더는 이 문장으로 찍지 않습니다. 에피소드는 지우지 않습니다 "
     "(있으면 뺄 수 없습니다)."),
)


def build_doctor_right(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # --- 고른 지시문 -------------------------------------------------
    box = QGroupBox(tr("지시문"))
    bv = QVBoxLayout(box)
    bv.setContentsMargins(6, 6, 6, 6)
    win.doctor_task_detail = QLabel(tr("가운데 표에서 지시문을 고르세요"))
    win.doctor_task_detail.setWordWrap(True)
    win.doctor_task_detail.setStyleSheet("color:#333;")
    bv.addWidget(win.doctor_task_detail)

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("color:#cccccc;")
    bv.addWidget(sep)

    win.doctor_task_buttons = {}
    for name, label, tip in ACTIONS:
        b = QPushButton(tr(label))
        b.setToolTip(tr(tip))
        # ops 를 이름으로 늦게 찾는다 -- 이 위젯은 DoctorOps 보다 먼저
        # 만들어질 수 있다 (창이 패널을 짓고 나서 ops 를 붙인다).
        b.clicked.connect(
            lambda _c=False, n=name: getattr(win.doctor, n)())
        b.setEnabled(False)
        win.doctor_task_buttons[name] = b
        bv.addWidget(b)
    col.addWidget(box)

    # --- 이 파일의 상태 ----------------------------------------------
    # 닥터에만 뜻이 있는 값이다. edit_count 가 0 이 아니면 변환기가
    # 이어붙이기를 거부하므로(전체 재빌드만 허용), 고치기 전에 이미
    # 고쳐진 파일인지 보이는 편이 낫다.
    fbox = QGroupBox(tr("이 scene 파일"))
    fv = QVBoxLayout(fbox)
    fv.setContentsMargins(6, 6, 6, 6)
    win.doctor_file_detail = QLabel(tr("scene 을 고르세요"))
    win.doctor_file_detail.setWordWrap(True)
    win.doctor_file_detail.setStyleSheet("color:#333;")
    fv.addWidget(win.doctor_file_detail)
    col.addWidget(fbox)

    col.addStretch(1)
    return w
