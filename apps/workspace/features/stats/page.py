"""Stats page builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr


def build_stats(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)
    # 두 열: 왼쪽은 지금 찍고 있는 task, 오른쪽은 GUI 를 켠 뒤 전체.
    # task 를 여러 개 도는 세션에서 "이 task 를 몇 개 모았나"와 "오늘 총
    # 몇 개인가"는 서로 다른 질문이고, 한 열에만 두면 둘 중 하나를 못 본다.
    win.stats_labels = {}
    win.stats_total_labels = {}
    box = QGroupBox(tr("수집 현황"))
    grid = QGridLayout(box)
    grid.setColumnStretch(0, 1)
    win.stats_task_header = QLabel(tr("이번 task"))
    for c, head in ((1, win.stats_task_header), (2, QLabel(tr("누적")))):
        head.setStyleSheet("color:#888;")
        head.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(head, 0, c)
    for row, (key, label) in enumerate((
            ("saved", "저장된 에피소드"), ("success", "성공"),
            ("failed", "실패"), ("discarded", "버림"),
            ("frames", "총 프레임"), ("elapsed", "경과 시간"),
            ("rate", "분당 에피소드")), start=1):
        grid.addWidget(QLabel(tr(label)), row, 0)
        for c, store in ((1, win.stats_labels), (2, win.stats_total_labels)):
            lab = QLabel("-")
            lab.setAlignment(Qt.AlignmentFlag.AlignRight)
            # 이번 task 쪽만 굵게. 수집 중에 눈이 가야 할 것은 이쪽이다.
            if c == 1:
                lab.setFont(QFont("", 10, QFont.Weight.Bold))
            else:
                lab.setStyleSheet("color:#888;")
            grid.addWidget(lab, row, c)
            store[key] = lab
    col.addWidget(box)

    # 계획 진행률 트리는 Collect 패널 "진행" 상자로 옮겼다 (2026-09-04) --
    # 수집 중에 보는 정보는 수집 화면에 있어야 하고, 같은 정보를 두 패널에
    # 두지 않는다 (아래 파일 목록 주석과 같은 원칙).

    win.disk_box = QGroupBox(tr("디스크"))
    dform = QFormLayout(win.disk_box)
    win.disk_label = QLabel("-")
    dform.addRow(tr("저장 경로 여유"), win.disk_label)
    col.addWidget(win.disk_box)

    # 파일 목록은 여기 없다. Dataset 패널의 트리가 이미 파일과 에피소드를
    # 모두 들고 있어서, 같은 목록을 두 군데 두면 어느 쪽 선택이 분석에
    # 반영되는지가 매번 헷갈린다. 선택은 Dataset 하나로 모은다.
    motion = QGroupBox(tr("움직임 분석"))
    mcol = QVBoxLayout(motion)
    win.stats_hint = QLabel(tr("Dataset 패널에서 파일이나 에피소드를 고륾면 "
                                "Analysis 탭에 반영됩니다."))
    win.stats_hint.setStyleSheet("color:#888;")
    win.stats_hint.setWordWrap(True)
    mcol.addWidget(win.stats_hint)
    rescan = QPushButton(tr("다시 분석"))
    rescan.clicked.connect(lambda: win.stats_ops.refresh_analysis(force=True))
    mcol.addWidget(rescan)
    col.addWidget(motion)
    col.addStretch()
    return w
