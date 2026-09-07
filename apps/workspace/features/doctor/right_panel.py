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
    QStackedWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.shared.collapsible import CollapsibleBox
from apps.workspace.shared.info import InfoCard
from gello.gui.i18n import tr

#: (ops 메서드 이름, 라벨, 툴팁)
SCENE_ACTIONS = (
    ("edit_objects", "소품 고치기...",
     "기록된 물체를 인벤토리에서 다시 고릅니다. 고치기 전에 지금과 고친 뒤를 "
     "나란히 봅니다. 에피소드는 바뀌지 않습니다."),
)

#: 진행 닥터의 조치. 여기 처방은 "고쳐라" 가 아니라 "찍으러 가라" 하나다.
SHORTFALL_ACTIONS = (
    ("go_collect", "수집으로",
     "이 scene 과 지시문을 시작 설정으로 걸고 Collect 화면으로 갑니다. "
     "책상을 위 배치대로 만든 뒤 누르세요."),
)

#: 스키마 닥터의 조치.
#:
#: "스탬프" 라고 부르지 않는다 (2026-09-07 사용자) -- 파일에 적힌 것은
#: **데이터세트 버전**이고, 그것이 검증·변환·업로드가 읽는 이름이다. 도장은
#: 구현의 비유일 뿐 조작자가 다루는 대상의 이름이 아니다.
#:
#: [버전 맞추기] 는 **양방향**이다. 라벨과 안내는 방향에 따라 화면에서 다시
#: 쓴다 (_show_schema_detail) -- 올리는 것과 내리는 것은 뜻이 정반대라 같은
#: 문구로 덮을 수 없다. 내용이 못 따라가는 버전을 찍는 길은 여전히 없다:
#: 그것이 knu-1.1.0 을 만든 조작이다.
SCHEMA_ACTIONS = (
    ("fill_payload", "부하 모델 채우기...",
     "빠진 부하 모델을 채우고, 그러고 나면 만족하는 버전으로 올립니다. "
     "같은 데이터셋의 다른 scene 에 적힌 값을 기본으로 보여줍니다."),
    ("align_version", "데이터세트 버전 맞추기",
     "파일의 데이터세트 버전을 그 내용이 실제로 만족하는 버전으로 바꿉니다. "
     "에피소드는 건드리지 않습니다."),
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


def _buttons(win, box: QVBoxLayout, actions, store: dict) -> None:
    for name, label, tip in actions:
        b = QPushButton(tr(label))
        b.setToolTip(tr(tip))
        # ops 를 이름으로 늦게 찾는다 -- 패널이 DoctorOps 보다 먼저 생긴다.
        b.clicked.connect(lambda _c=False, n=name: getattr(win.doctor, n)())
        b.setEnabled(False)
        store[name] = b
        box.addWidget(b)


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
    """**중앙 탭마다 자기 쪽**을 갖는다.

    닥터의 탭 둘은 하는 일이 다르다 -- 기록 닥터는 고른 scene·지시문을
    고치고, 진행 닥터는 고른 미달 줄로 데려간다. 우측에 넷을 다 두면 진행
    탭에서 앞의 셋이 고를 것도 없이 비어 있다 (2026-09-07 사용자 지적).
    활동탭마다 자기 구현이라는 규칙을 중앙 탭까지 민 것이다.

    상자는 여닫힌다. 우측은 세로로 길어지는데, 지금 안 보는 상자 때문에
    스크롤하는 것이 아깝다.
    """
    stack = QStackedWidget()
    win.doctor_right_pages = {}
    for key, build in (("doc_record", _record_page),
                       ("doc_progress", _progress_page),
                       ("doc_schema", _schema_page)):
        win.doctor_right_pages[key] = stack.count()
        stack.addWidget(build(win))
    win.doctor_right_stack = stack
    return stack


def _record_page(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    box = CollapsibleBox(tr("Scene"))
    win.doctor_scene_card = InfoCard()
    box.body.addWidget(win.doctor_scene_card)
    box.body.addWidget(_rule())
    win.doctor_scene_buttons = {}
    _buttons(win, box.body, SCENE_ACTIONS, win.doctor_scene_buttons)
    col.addWidget(box)

    box = CollapsibleBox(tr("Diagnosis"))
    win.doctor_scene_diag = _slot(box.body, tr("맞지 않는 것"))
    win.doctor_scene_cost = _slot(box.body, tr("수정 영향"))
    col.addWidget(box)

    box = CollapsibleBox(tr("Instruction"))
    win.doctor_task_card = InfoCard()
    box.body.addWidget(win.doctor_task_card)
    win.doctor_task_diag = _slot(box.body, tr("맞지 않는 것"))
    box.body.addWidget(_rule())
    win.doctor_task_buttons = {}
    _buttons(win, box.body, TASK_ACTIONS, win.doctor_task_buttons)
    col.addWidget(box)

    col.addStretch(1)
    return w


def _schema_page(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    box = CollapsibleBox(tr("Schema"))
    win.schema_card = InfoCard()
    box.body.addWidget(win.schema_card)
    win.schema_missing = _slot(box.body, tr("빠진 것"))
    win.schema_drift = _slot(box.body, tr("초기 자세"))
    win.schema_plan = _slot(box.body, tr("고치면"))
    box.body.addWidget(_rule())
    win.schema_buttons = {}
    _buttons(win, box.body, SCHEMA_ACTIONS, win.schema_buttons)
    col.addWidget(box)

    col.addStretch(1)
    return w


def _progress_page(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    box = CollapsibleBox(tr("Shortfall"))
    win.progress_card = InfoCard()
    box.body.addWidget(win.progress_card)
    win.progress_note = _slot(box.body, tr("이어 찍으면"))
    box.body.addWidget(_rule())
    win.progress_buttons = {}
    _buttons(win, box.body, SHORTFALL_ACTIONS, win.progress_buttons)
    win.progress_box = box
    col.addWidget(box)

    col.addStretch(1)
    return w
