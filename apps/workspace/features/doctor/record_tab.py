"""기록 닥터 -- 이 scene 이 자기 배치·문장에 대해 하는 말이 맞는가.

**기준 사진과 기록을 나란히 둔다.** 이것이 이 화면의 존재 이유다. S008 은
metadata 에 초록 그릇이 적혀 있는데 문장 120개가 전부 회색을 말했고, 그
어긋남이 수집·업로드·변환을 모두 지나갔다 -- 사진과 기록을 같이 볼 자리가
없어서 아무도 대조하지 않았기 때문이다.

아래쪽 표는 지시문(task)이다. 에피소드 한 줄씩이 아니다: 문장을 고치는 것은
task 단위이지 에피소드 단위가 아니고, 같은 task 안에서 문장이 갈리는 것은
기능이 아니라 결함이다.

**여기에 조작은 없다.** 가운데는 보는 자리고, 진단과 조치는 오른쪽에 있다
(2026-09-07 사용자: 정정 상자가 가운데 있어 "ux 가 일관되지 않다"). 표의
⚠ 는 어느 줄을 볼지 알려 주는 표시일 뿐이고, 사유와 고칠 수단은 그 줄을
누르면 오른쪽에 나온다.
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

from apps.workspace.shared.info import InfoCard
from gello.gui.i18n import tr

#: 기준 사진의 가로 상한. 사진이 배치 설명을 밀어내면 나란히 두는 뜻이 없다.
PHOTO_W = 360


def build_record_tab(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(6, 6, 6, 6)

    win.doctor_title = QLabel(tr("왼쪽에서 scene 을 고르세요"))
    win.doctor_title.setStyleSheet("font-weight:bold;")
    col.addWidget(win.doctor_title)

    # --- 프리뷰: 사진 | 기록 ------------------------------------------
    top = QHBoxLayout()
    win.doctor_photo = QLabel(tr("기준 사진 없음"))
    win.doctor_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    win.doctor_photo.setMinimumWidth(200)
    win.doctor_photo.setStyleSheet(
        "background:#e4e4e4; color:#444; border:1px solid #c9c9c9;")
    top.addWidget(win.doctor_photo)

    win.doctor_info = InfoCard()
    top.addWidget(win.doctor_info, 1)
    col.addLayout(top)

    # --- 트랙: 지시문 -------------------------------------------------
    col.addWidget(QLabel(tr("지시문")))
    win.doctor_task_tree = QTreeWidget()
    win.doctor_task_tree.setHeaderLabels(
        [tr("ID"), tr("개수"), tr("문장"), tr("상태")])
    win.doctor_task_tree.setRootIsDecorated(False)
    win.doctor_task_tree.header().setSectionResizeMode(
        2, QHeaderView.ResizeMode.Stretch)
    win.doctor_task_tree.setToolTip(tr(
        "줄을 누르면 오른쪽에 그 지시문의 상세와 고칠 수단이 나옵니다."))
    win.doctor_task_tree.itemClicked.connect(
        lambda item, _c: win.doctor.on_task_picked(item))
    col.addWidget(win.doctor_task_tree, 1)

    # 줄 하나에 하는 일(고치기·교환·빼기)은 오른쪽 패널에 있다 -- 세 패널의
    # 분담이 "왼쪽=화면 전체 / 가운데=보는 것 / 오른쪽=고른 한 줄" 이다
    # (2026-09-07, layout.build_right 의 주석이 정본). 여기 남는 것은 무엇을
    # 고르면 되는지 알려 주는 한 줄이다.
    win.doctor_task_hint = QLabel("")
    win.doctor_task_hint.setWordWrap(True)
    win.doctor_task_hint.setStyleSheet("color:#444;")
    col.addWidget(win.doctor_task_hint)
    return w
