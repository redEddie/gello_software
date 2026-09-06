"""Stats page builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.gui.fonts import set_bold
from gello.gui.i18n import tr

from apps.workspace.shared.sizing import cap_rows


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
                set_bold(lab, 10)
            else:
                lab.setStyleSheet("color:#888;")
            grid.addWidget(lab, row, c)
            store[key] = lab
    col.addWidget(box)

    # 계획 진행률 트리는 Collect 패널 "진행" 상자로 옮겼다 (2026-09-04) --
    # 수집 중에 보는 정보는 수집 화면에 있어야 하고, 같은 정보를 두 패널에
    # 두지 않는다 (아래 파일 목록 주석과 같은 원칙).

    # 디스크 상자는 상태바로 옮겼다 (2026-09-06 사용자 요청). 저장 경로의
    # 여유는 수집 중에 알아야 하는 값인데, 여기 있으면 화면을 옮겨야 보였다.

    # 수집 이력 -- 이 화면의 카운터는 GUI 를 닫으면 사라진다. 이력은 세션이
    # 끝날 때마다 한 줄씩 파일에 쌓여서, "오늘 이 속도가 평소만 한가"에
    # 답한다. 정본은 <state_dir>/collection_history.jsonl (collection_history.py).
    board = QGroupBox(tr("수집 속도 순위 (이 데이터셋)"))
    bcol = QVBoxLayout(board)
    win.board_hint = QLabel(tr("(이력 없음)"))
    win.board_hint.setStyleSheet("color:#888;")
    win.board_hint.setWordWrap(True)
    bcol.addWidget(win.board_hint)
    win.board_tree = QTreeWidget()
    win.board_tree.setHeaderLabels([tr("수집자"), tr("분당"), tr("저장"),
                                    tr("성공률"), tr("시간")])
    win.board_tree.setRootIsDecorated(False)
    win.board_tree.setToolTip(tr(
        "같은 데이터셋을 찍은 세션만 셉니다 — task 가 다르면 한 에피소드에 드는 "
        "시간도 달라서, 다른 데이터셋과 한 줄에 세우면 비교가 되지 않습니다.\n"
        "'분당' 은 자리에 앉아 있던 시간 기준입니다 (리셋·재배치 포함)."))
    cap_rows(win.board_tree, 6)
    bcol.addWidget(win.board_tree)
    col.addWidget(board)

    hist = QGroupBox(tr("최근 세션"))
    hcol = QVBoxLayout(hist)
    win.history_tree = QTreeWidget()
    win.history_tree.setHeaderLabels([tr("시작"), tr("수집자"), tr("저장"),
                                      tr("시간"), tr("분당")])
    win.history_tree.setRootIsDecorated(False)
    win.history_tree.setToolTip(tr(
        "Connect 부터 Disconnect 까지가 한 줄입니다. 이번에 켜고 찍은 것은 "
        "'이번 실행' 으로 굵게 표시됩니다."))
    cap_rows(win.history_tree, 6)
    hcol.addWidget(win.history_tree)
    col.addWidget(hist)

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
