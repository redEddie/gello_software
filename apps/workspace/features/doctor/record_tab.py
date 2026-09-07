"""기록 닥터 -- 이 scene 이 자기 배치·문장에 대해 하는 말이 맞는가.

**기준 사진과 기록을 나란히 둔다.** 이것이 이 화면의 존재 이유다. S008 은
metadata 에 초록 그릇이 적혀 있는데 문장 120개가 전부 회색을 말했고, 그
어긋남이 수집·업로드·변환을 모두 지나갔다 -- 사진과 기록을 같이 볼 자리가
없어서 아무도 대조하지 않았기 때문이다.

아래쪽 표는 지시문(task)이다. 에피소드 한 줄씩이 아니다: 문장을 고치는 것은
task 단위이지 에피소드 단위가 아니고, 같은 task 안에서 문장이 갈리는 것은
기능이 아니라 결함이다.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.shared.widgets import SceneInfoView
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

    win.doctor_info = SceneInfoView()
    top.addWidget(win.doctor_info, 1)
    col.addLayout(top)

    # --- 오등록 제안 --------------------------------------------------
    # 제안이 없으면 통째로 숨긴다. 늘 보이는 빈 칸은 "고칠 것이 없다" 가
    # 아니라 "아직 안 봤다" 로 읽힌다.
    win.doctor_fix_box = QFrame()
    win.doctor_fix_box.setStyleSheet(
        "QFrame{background:#fff6e5; border:1px solid #d9a441;"
        " border-radius:4px;}")
    fix = QHBoxLayout(win.doctor_fix_box)
    fix.setContentsMargins(8, 6, 8, 6)
    win.doctor_fix_label = QLabel("")
    win.doctor_fix_label.setWordWrap(True)
    win.doctor_fix_label.setStyleSheet("border:none; color:#333;")
    fix.addWidget(win.doctor_fix_label, 1)
    win.doctor_fix_btn = QPushButton(tr("이 정정 적용"))
    win.doctor_fix_btn.setToolTip(tr(
        "기록만 고칩니다 — 에피소드도 지시문도 바뀌지 않고, "
        "이어붙이기(resume)도 막히지 않습니다."))
    win.doctor_fix_btn.setStyleSheet("border:1px solid #b98a2f;")
    win.doctor_fix_btn.clicked.connect(win.doctor.apply_suggestion)
    fix.addWidget(win.doctor_fix_btn)
    win.doctor_fix_box.setVisible(False)
    col.addWidget(win.doctor_fix_box)

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
