"""진행 닥터 -- 무엇이 덜 찍혔나, 그리고 거기로 데려간다.

기록 닥터와 처방이 다르다. 저쪽은 파일을 고치고, 이쪽은 **고칠 것이 없다**
-- 답은 "더 찍어라" 하나뿐이다. 그래서 이 탭의 마지막 버튼은 [수집으로]이고,
누르면 그 scene 과 지시문이 시작 설정이 된 채 Collect 화면으로 넘어간다.

세 단계로 읽힌다 (2026-09-07 사용자):

    무엇이 빠졌나   표 -- 많이 모자란 것부터
    어떻게 놓나     기준 사진 + 3×3 배치도 (책상을 그 모양으로 만든다)
    찍으러 간다     [수집으로]

가운데 단계가 이 화면의 값이다. 미달 목록만 있으면 "S017 I001 0/10" 을 보고
그 scene 이 어떻게 생겼는지 다시 찾아야 한다.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.shared.info import ZoneMap
from gello.gui.i18n import tr

#: 기준 사진의 가로 상한 (기록 닥터와 같은 값).
PHOTO_W = 360


def build_progress_tab(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(6, 6, 6, 6)

    win.progress_title = QLabel(tr("데이터셋을 읽는 중..."))
    win.progress_title.setStyleSheet("font-weight:bold;")
    col.addWidget(win.progress_title)

    win.progress_tree = QTreeWidget()
    win.progress_tree.setHeaderLabels(
        [tr("Scene"), tr("지시문"), tr("수집"), tr("남음"), tr("문장")])
    win.progress_tree.setRootIsDecorated(False)
    win.progress_tree.header().setSectionResizeMode(
        4, QHeaderView.ResizeMode.Stretch)
    win.progress_tree.setToolTip(tr(
        "많이 모자란 것부터입니다. 줄을 누르면 그 scene 의 배치가 아래에 "
        "뜹니다."))
    win.progress_tree.itemClicked.connect(
        lambda item, _c: win.doctor.on_shortfall_picked(item))
    col.addWidget(win.progress_tree, 1)

    # --- 어떻게 놓나 --------------------------------------------------
    col.addWidget(QLabel(tr("배치")))
    row = QHBoxLayout()
    win.progress_photo = QLabel(tr("줄을 고르세요"))
    win.progress_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    win.progress_photo.setMinimumWidth(200)
    win.progress_photo.setStyleSheet(
        "background:#e4e4e4; color:#444; border:1px solid #949494;")
    row.addWidget(win.progress_photo)
    win.progress_zones = ZoneMap()
    row.addWidget(win.progress_zones, 1)
    col.addLayout(row)

    win.progress_hint = QLabel("")
    win.progress_hint.setWordWrap(True)
    win.progress_hint.setStyleSheet("color:#444;")
    col.addWidget(win.progress_hint)
    return w
