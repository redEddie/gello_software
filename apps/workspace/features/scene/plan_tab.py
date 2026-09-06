"""Instruction 탭 -- 이 데이터셋의 scene × 지시문 수집 현황, 그리고 계획 편집.

② Configure(Scene 설정) 화면의 중앙 탭이다. 그 화면에 온 목적은 "다음에
무엇을 찍을까"를 정하는 것인데, 지금까지 중앙에는 카메라 라이브가 떠 있었다
-- scene 배치를 정하는 데 카메라는 필요 없고, 필요한 것은 어느 scene 의 어느
지시문이 몇 개 남았는가다 (2026-09-06 사용자 지적).

이 표는 원래 Collect 화면 왼쪽 맨 아래에 있었다. 거기서는 쓸 데가 없었다 --
scene 마다 Connect/Disconnect 하는 운용이라 수집 **중**에 다른 scene 의 숫자를
볼 일이 없고, 그 표를 그리는 데 계획의 모든 scene 파일을 열어야 해서 저장
한 번에 543ms 씩 멈췄다. 정하는 화면으로 옮기면 둘 다 해결된다.

**줄을 누르면 그것이 시작 지시문이 된다** -- scene 과 지시문이 함께 정해진다
(2026-09-06 사용자 요청). 왼쪽에서 scene 을 고르고 다시 문장을 고르던 두
단계가, 보고 있던 그 줄을 누르는 한 번이 됐다.

계획 파일(instructions.json)의 이름은 화면에 적지 않는다. 지시문이 그 파일
하나로 통일된 뒤로는 고를 것이 아니고, 편집만 필요하다 -- 그 버튼이 여기 있다.

숫자의 정본은 scene 파일이다 (계획 파일에는 카운트가 없다 -- 두 개의 진실
금지). 채우는 것은 ScenePlanningOps.refresh_plan_progress 하나다.
"""
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr


def build_plan_tab(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(6, 6, 6, 6)

    win.plan_progress_label = QLabel(tr("계획을 읽는 중..."))
    win.plan_progress_label.setWordWrap(True)
    col.addWidget(win.plan_progress_label)

    win.plan_progress_tree = QTreeWidget()
    win.plan_progress_tree.setHeaderLabels(
        [tr("scene / 지시문"), tr("수집"), tr("목표"), tr("문장")])
    win.plan_progress_tree.setRootIsDecorated(True)
    win.plan_progress_tree.header().setSectionResizeMode(
        3, QHeaderView.ResizeMode.Stretch)
    win.plan_progress_tree.setToolTip(tr(
        "지시문 줄을 누르면 그 scene 과 지시문이 시작 설정이 됩니다.\n"
        "(수집 중에는 바꿀 수 없습니다 — 세션을 끝낸 뒤 고르세요.)"))
    win.plan_progress_tree.itemClicked.connect(
        lambda item, _c: win.scene_planning.on_plan_row_picked(item))
    col.addWidget(win.plan_progress_tree, 1)

    row = QHBoxLayout()
    # 계획 편집은 여기다 (2026-09-06: Configure 왼쪽에서 이동). 표를 보다가
    # "이 지시문의 목표를 올려야겠다" 고 생각하는 자리가 바로 여기다.
    edit = QPushButton(tr("계획 편집..."))
    edit.setToolTip(tr("이 데이터셋의 지시문과 목표 개수를 고칩니다 "
                       "(저장할 때 규칙을 검사합니다).\n"
                       "계획이 없으면 만들고 엽니다."))
    edit.clicked.connect(win.scene_planning.on_edit_plan)
    row.addWidget(edit)
    # [새 계획]·[계획 삭제] 는 뺐다 (2026-09-06 사용자). 편집이 없으면
    # 만들어 주므로 "새 계획" 은 같은 버튼이 됐고, 삭제는 계획이 필수가 된
    # 뒤로 데이터셋을 수집 불가로 만드는 버튼이라 자주 쓰는 자리에 둘 것이
    # 아니다 -- 메뉴 색인(Scene)에는 그대로 있다.
    row.addStretch(1)
    refresh = QPushButton(tr("새로고침"))
    refresh.setToolTip(tr(
        "계획의 모든 scene 파일을 다시 읽습니다 (파일 수에 비례해 몇백 ms)."))
    refresh.clicked.connect(win.scene_planning.refresh_plan_progress)
    row.addWidget(refresh)
    col.addLayout(row)
    return w
