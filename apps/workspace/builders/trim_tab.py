"""Trim tab builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gello.gui.gui_widgets import VideoView
from gello.gui.i18n import tr
from gello.gui.plot_widgets import SeriesPlot


def build_trim_tab(win) -> QWidget:
    """Analysis's layout, aimed at one question: where should this take end.

    The plots are the same five as Analysis -- the tail wobble is visible
    there as clearly as anywhere -- but the right column is the episode's
    own video instead of dataset-wide statistics, because the check that
    actually matters ("did I cut the release?") is a thing you look at, not
    a number. Nothing is written until 확정; every button before that only
    moves a pending count.
    """
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(4, 4, 4, 4)
    split = QSplitter(Qt.Orientation.Horizontal)

    left = QWidget()
    lcol = QVBoxLayout(left)
    lcol.setContentsMargins(0, 0, 0, 0)
    win.trim_summary = QLabel(tr("Dataset 트리나 Analysis 순위표에서 에피소드를 고르세요."))
    win.trim_summary.setWordWrap(True)
    win.trim_summary.setStyleSheet("font-weight:bold;")
    lcol.addWidget(win.trim_summary)

    grid = QGridLayout()
    win.trim_plots = {}
    for i, (title, dims) in enumerate((
        ("joint1.pos, joint2.pos", [(0, "joint1.pos"), (1, "joint2.pos")]),
        ("joint4.pos, joint5.pos", [(3, "joint4.pos"), (4, "joint5.pos")]),
        ("joint6.pos, joint7.pos", [(5, "joint6.pos"), (6, "joint7.pos")]),
        ("joint3.pos", [(2, "joint3.pos")]),
        ("gripper.pos", [(7, "gripper.pos")]),
    )):
        plot = SeriesPlot(title)
        win.trim_plots[title] = (plot, dims)
        grid.addWidget(plot, i // 2, i % 2)
    lcol.addLayout(grid, 1)
    legend = QLabel(tr("실선 observation.state   ┄ 파선 observation.commanded_state"
                       "   ┈ 점선 action     ▨ 빨간 음영 = 잘려나갈 구간"))
    legend.setStyleSheet("color:#888;")
    lcol.addWidget(legend)
    split.addWidget(left)

    right = QWidget()
    rcol = QVBoxLayout(right)
    rcol.setContentsMargins(0, 0, 0, 0)

    vids = QHBoxLayout()
    win.trim_views = {}
    for role, cap in (("agent", tr("agent")), ("wrist", tr("wrist"))):
        box = QVBoxLayout()
        v = VideoView()
        v.clear_frame(tr("에피소드를 선택하세요"))
        v.set_crop_guide(**win.cameras.crop_params[role])
        win.trim_views[role] = v
        box.addWidget(v, 1)
        lab = QLabel(cap)
        lab.setStyleSheet("color:#888;")
        lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(lab)
        vids.addLayout(box, 1)
    rcol.addLayout(vids, 1)

    # 슬라이더는 '지금 몇 번째 프레임을 보고 있나'다. 자를 지점을 정하는
    # 것과 별개로, 잘린 뒤 마지막 프레임이 어떤 장면인지 눈으로 확인해야
    # 하기 때문에 재생/스크럽을 그대로 둔다.
    srow = QHBoxLayout()
    win.trim_play_btn = QPushButton(tr("재생"))
    win.trim_play_btn.setEnabled(False)
    win.trim_play_btn.clicked.connect(win.playback_ops.on_trim_play)
    srow.addWidget(win.trim_play_btn)
    win.trim_slider = QSlider(Qt.Orientation.Horizontal)
    win.trim_slider.setEnabled(False)
    win.trim_slider.valueChanged.connect(win.playback_ops.on_trim_scrub)
    srow.addWidget(win.trim_slider, 1)
    win.trim_pos = QLabel("-/-")
    win.trim_pos.setMinimumWidth(72)
    srow.addWidget(win.trim_pos)
    rcol.addLayout(srow)

    box = QGroupBox(tr("끝 다듬기"))
    bcol = QVBoxLayout(box)
    win.trim_count = QLabel(tr("에피소드를 고르세요"))
    win.trim_count.setStyleSheet("font-size:15px; font-weight:bold;")
    win.trim_count.setWordWrap(True)
    bcol.addWidget(win.trim_count)

    # 누른 만큼 쌓이고, + 로 되물린다. -1..-20 을 늘어놓는 대신 네 개만 두면
    # 한 자리에서 오르내릴 수 있어 "몇 번 눌렀더라"를 셀 필요가 없다.
    step_row = QHBoxLayout()
    # 라벨의 부호는 *에피소드 길이* 기준이다: "−5" 는 5프레임 짧아진다는 뜻이라
    # 자를 양(pending)은 +5 만큼 는다. 둘을 같은 부호로 두면 −5 가 되돌리기가
    # 되어 버린다.
    for label, n in ((tr("−5"), 5), (tr("−1"), 1), (tr("+1"), -1), (tr("+5"), -5)):
        b = QPushButton(label)
        b.setToolTip(
            tr("누를 때마다 {n}프레임씩 더 자릅니다 (아직 파일은 그대로)")
            .format(n=n) if n > 0 else
            tr("누를 때마다 {n}프레임씩 되돌립니다 (원본 길이 이상으로는 안 갑니다)")
            .format(n=-n))
        b.clicked.connect(lambda _=False, k=n: win.playback_ops.trim_add(k))
        step_row.addWidget(b)
    bcol.addLayout(step_row)

    act_row = QHBoxLayout()
    sug = QPushButton(tr("추천"))
    sug.setToolTip(tr("끝에서부터 속도가 그 에피소드 중앙값 아래로 떨어지는 "
                      "지점까지를 제안합니다 (최대 15프레임)"))
    sug.clicked.connect(win.playback_ops.trim_suggest)
    act_row.addWidget(sug)
    win.trim_reset_btn = QPushButton(tr("정정"))
    win.trim_reset_btn.setToolTip(tr("고른 프레임 수를 0으로 되돌립니다. "
                                      "확정 전에는 파일이 바뀌지 않습니다."))
    win.trim_reset_btn.clicked.connect(win.playback_ops.trim_reset)
    act_row.addWidget(win.trim_reset_btn)
    win.trim_apply_btn = QPushButton(tr("확정 (파일에 적용)"))
    win.trim_apply_btn.setStyleSheet("background-color:#c0392b; color:white; padding:6px;")
    win.trim_apply_btn.setToolTip(tr("여기서부터 .hdf5 가 실제로 바뀝니다. "
                                      "되돌릴 수 없습니다."))
    win.trim_apply_btn.clicked.connect(win.playback_ops.trim_apply)
    act_row.addWidget(win.trim_apply_btn, 1)
    bcol.addLayout(act_row)

    win.trim_warn = QLabel("")
    win.trim_warn.setWordWrap(True)
    win.trim_warn.setStyleSheet("color:#e67e22;")
    bcol.addWidget(win.trim_warn)
    rcol.addWidget(box)
    split.addWidget(right)
    split.setSizes([640, 490])
    outer.addWidget(split)
    win.playback_ops.trim_update()
    return page

