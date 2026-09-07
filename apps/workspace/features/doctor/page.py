"""Doctor 활동탭의 왼쪽 패널 -- 데이터셋의 scene 목록이 '재료'다.

영상편집기의 bin 과 같은 자리다. 왼쪽에서 scene 을 고르면 중앙 탭이 그
scene 의 기준 사진·배치·지시문을 편다.

"문제" 열은 **task 수**지 에피소드 수가 아니다. 같은 문장 10개는 하나의
결정이지 열 개가 아니라서, 에피소드로 세면 작은 실수 하나가 큰 문제처럼
보인다 (S008 의 오등록 하나가 60 으로 세어졌다).
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr


def build_doctor(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    win.doctor_dataset_label = QLabel(tr("데이터셋을 읽는 중..."))
    win.doctor_dataset_label.setWordWrap(True)
    win.doctor_dataset_label.setStyleSheet("font-weight:bold;")
    col.addWidget(win.doctor_dataset_label)

    win.doctor_hint = QLabel(tr(
        "scene 을 고르면 기준 사진과 기록을 나란히 봅니다. "
        "색이나 물체가 사진과 다르면 그 자리에서 고칩니다."))
    win.doctor_hint.setWordWrap(True)
    win.doctor_hint.setStyleSheet("color:#444;")
    col.addWidget(win.doctor_hint)

    win.doctor_tree = QTreeWidget()
    win.doctor_tree.setHeaderLabels(
        [tr("Scene"), tr("에피소드"), tr("기록"), tr("진행")])
    win.doctor_tree.setRootIsDecorated(False)
    win.doctor_tree.header().setSectionResizeMode(
        0, QHeaderView.ResizeMode.Stretch)
    win.doctor_tree.setToolTip(tr(
        "둘 다 지시문(task) 수입니다 — 같은 문장 10개는 1건입니다.\n"
        "  기록 = 문장이 scene 기록과 맞지 않는 것\n"
        "  진행 = 목표를 아직 못 채운 것\n"
        "줄을 누르면 그 scene 을 엽니다. Space 로 선택을 풉니다."))
    win.doctor_tree.itemClicked.connect(
        lambda item, _c: win.doctor.on_row_picked(item))
    col.addWidget(win.doctor_tree, 1)

    row = QHBoxLayout()
    rescan = QPushButton(tr("다시 검사"))
    rescan.setToolTip(tr(
        "데이터셋의 scene 파일을 전부 다시 읽습니다 (파일 수에 비례해 "
        "몇백 ms)."))
    rescan.clicked.connect(win.doctor.rescan)
    row.addWidget(rescan)
    row.addStretch(1)
    col.addLayout(row)
    return w


def fill_scene_rows(win, rows: list) -> None:
    """rows = [(scene_id, 에피소드 수, 기록 건수, 진행 건수), ...].

    두 닥터의 수를 한 줄에 나란히 둔다 -- 어느 scene 을 열지 정할 때 묻는
    것은 "여기 뭐가 있나" 하나인데, 전에는 '문제' 한 칸이 기록 쪽만 셌다
    (2026-09-07 사용자). 진행 쪽은 다른 탭에 가야 알 수 있었다.

    채우는 것은 DoctorOps 하나다 -- 화면이 스스로 파일을 읽지 않는다.
    """
    tree = win.doctor_tree
    tree.clear()
    for sid, eps, rec, prog in rows:
        it = QTreeWidgetItem([
            sid, str(eps),
            "—" if not rec else f"{rec} ⚠",
            "—" if not prog else f"{prog} ⚠"])
        it.setData(0, Qt.ItemDataRole.UserRole, sid)
        f = QFont(it.font(0))
        f.setBold(True)
        # 칸마다 굵기를 따로 준다 -- 어느 쪽이 문제인지가 눈에 바로 든다.
        if rec or prog:
            it.setFont(0, f)
        if rec:
            it.setFont(2, f)
        if prog:
            it.setFont(3, f)
        tree.addTopLevelItem(it)
