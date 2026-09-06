"""Plan 탭 -- 이 데이터셋의 scene × 지시문 수집 현황.

② Configure(Scene 설정) 화면의 중앙 탭이다. 그 화면에 온 목적은 "다음에
무엇을 찍을까"를 정하는 것인데, 지금까지 중앙에는 카메라 라이브가 떠 있었다
-- scene 배치를 정하는 데 카메라는 필요 없고, 필요한 것은 어느 scene 의 어느
지시문이 몇 개 남았는가다 (2026-09-06 사용자 지적).

이 표는 원래 Collect 화면 왼쪽 맨 아래에 있었다. 거기서는 쓸 데가 없었다 --
scene 마다 Connect/Disconnect 하는 운용이라 수집 **중**에 다른 scene 의 숫자를
볼 일이 없고, 그 표를 그리는 데 계획의 모든 scene 파일을 열어야 해서 저장
한 번에 543ms 씩 멈췄다. 정하는 화면으로 옮기면 둘 다 해결된다.

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
    col.addWidget(win.plan_progress_tree, 1)

    row = QHBoxLayout()
    row.addStretch(1)
    refresh = QPushButton(tr("새로고침"))
    refresh.setToolTip(tr(
        "계획의 모든 scene 파일을 다시 읽습니다 (파일 수에 비례해 몇백 ms)."))
    refresh.clicked.connect(win.scene_planning.refresh_plan_progress)
    row.addWidget(refresh)
    col.addLayout(row)
    return w
