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

from apps.workspace.shared.info import InfoCard
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
    ("swap_task_text", "다른 지시문과 문장 교환...",
     "두 지시문의 문장을 맞바꿉니다. 라벨이 서로 바뀌어 기록된 것을 되돌릴 "
     "때, 그리고 안 찍은 빈 칸의 옳은 문장을 가져올 때 씁니다."),
    ("remove_task", "지시문 빼기",
     "더는 이 문장으로 찍지 않습니다. 에피소드는 지우지 않습니다 "
     "(있으면 뺄 수 없습니다)."),
)


def _rule() -> QFrame:
    """구분선. HLine 의 color: 대신 1px 상자의 background 로 그린다.

    HLine 은 선 색을 color: 로 받는데, 그러면 "글자 색" 을 재는 검사기가
    본문으로 착각한다 (실제로 대비 미달로 잡혔다). 배경으로 그리면 뜻이
    분명하고 렌더도 플랫폼을 덜 탄다.
    """
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background:#d0d0d0;")
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


def _slot(parent: QVBoxLayout, title: str) -> QLabel:
    """제목이 붙은 고정 칸. **숨기지 않는다** -- 값이 없으면 "없음" 이라고
    적는다. 상자가 늘었다 줄었다 하면 어디에 무엇이 오는지 익힐 수가 없다
    (2026-09-07 사용자)."""
    cap = QLabel(title)
    # #777 은 흰 바탕에서 4.48:1 로 AA(4.5:1)에 못 미친다. 11px 이라
    # 큰 글씨 예외도 못 쓴다 (#49).
    cap.setStyleSheet("color:#666; font-size:11px;")
    parent.addWidget(cap)
    body = QLabel("—")
    body.setWordWrap(True)
    body.setStyleSheet("color:#333;")
    parent.addWidget(body)
    return body


def build_doctor_right(win) -> QWidget:
    """상자 셋. 각 상자의 칸은 늘 같은 순서로 늘 있다.

        Scene        이 파일의 값 · 소품 고치기
        Diagnosis    맞지 않는 것 · 수정 영향
        Instruction  고른 지시문의 값 · 문장 고치기 / 교환 / 빼기

    **진단이 따로인 이유**: 맞지 않는 것은 scene 의 기록과 지시문의 문장을
    맞대어 나온 결과라 어느 한쪽에 속하지 않는다 (2026-09-07 사용자).
    Scene 상자에 넣으면 "이 파일의 값" 과 "두 쪽을 맞댄 판단" 이 한 상자에
    섞인다.
    """
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # --- Scene ---------------------------------------------------------
    sbox = QGroupBox(tr("Scene"))
    sv = QVBoxLayout(sbox)
    sv.setContentsMargins(6, 6, 6, 6)
    win.doctor_scene_card = InfoCard()
    sv.addWidget(win.doctor_scene_card)
    sv.addWidget(_rule())
    win.doctor_scene_buttons = {}
    _buttons(win, sv, SCENE_ACTIONS, win.doctor_scene_buttons)
    col.addWidget(sbox)

    # --- Diagnosis -----------------------------------------------------
    dbox = QGroupBox(tr("Diagnosis"))
    dv = QVBoxLayout(dbox)
    dv.setContentsMargins(6, 6, 6, 6)
    win.doctor_scene_diag = _slot(dv, tr("맞지 않는 것"))
    win.doctor_scene_cost = _slot(dv, tr("수정 영향"))
    col.addWidget(dbox)

    # --- Instruction ---------------------------------------------------
    tbox = QGroupBox(tr("Instruction"))
    tv = QVBoxLayout(tbox)
    tv.setContentsMargins(6, 6, 6, 6)
    win.doctor_task_card = InfoCard()
    tv.addWidget(win.doctor_task_card)
    win.doctor_task_diag = _slot(tv, tr("맞지 않는 것"))
    tv.addWidget(_rule())
    win.doctor_task_buttons = {}
    _buttons(win, tv, TASK_ACTIONS, win.doctor_task_buttons)
    col.addWidget(tbox)

    col.addStretch(1)
    return w
