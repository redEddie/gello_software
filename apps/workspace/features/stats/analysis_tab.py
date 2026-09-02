"""Analysis tab builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.data.episode_stats import STILL_VEL, TASK_DEV_LIMIT
from gello.gui.i18n import tr
from gello.gui.plot_widgets import BarStrip, Histogram, SeriesPlot

from apps.workspace.sizing import shrinkable_combo


def build_analysis_tab(win) -> QWidget:
    """Center-tab analysis: the curve view plus the curation list.

    It lives in the center, next to Live/Playback, because judging a take
    means looking at its curves and its video together -- putting the plots
    in a side panel would have made them too narrow to read.
    """
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(4, 4, 4, 4)
    split = QSplitter(Qt.Orientation.Horizontal)

    left = QWidget()
    lcol = QVBoxLayout(left)
    lcol.setContentsMargins(0, 0, 0, 0)
    win.analysis_summary = QLabel(tr("Statistics 패널에서 '다시 분석'을 누르세요."))
    win.analysis_summary.setWordWrap(True)
    win.analysis_summary.setStyleSheet("font-weight:bold;")
    lcol.addWidget(win.analysis_summary)

    win.plot_grid = QGridLayout()
    win.series_plots = {}
    # LeRobot 뷰어와 같은 묶음: 인접 관절끼리 스케일이 비슷해 같은 축에 얹힌다.
    for i, (title, dims) in enumerate((
        ("joint1.pos, joint2.pos", [(0, "joint1.pos"), (1, "joint2.pos")]),
        ("joint4.pos, joint5.pos", [(3, "joint4.pos"), (4, "joint5.pos")]),
        ("joint6.pos, joint7.pos", [(5, "joint6.pos"), (6, "joint7.pos")]),
        ("joint3.pos", [(2, "joint3.pos")]),
        ("gripper.pos", [(7, "gripper.pos")]),
    )):
        plot = SeriesPlot(title)
        win.series_plots[title] = (plot, dims)
        win.plot_grid.addWidget(plot, i // 2, i % 2)
    lcol.addLayout(win.plot_grid, 1)
    legend = QLabel(tr("실선 observation.state   ┄ 파선 observation.commanded_state"
                       "   ┈ 점선 action"))
    legend.setStyleSheet("color:#888;")
    lcol.addWidget(legend)
    split.addWidget(left)

    right = QWidget()
    rcol = QVBoxLayout(right)
    rcol.setContentsMargins(0, 0, 0, 0)

    win.dim_bars = BarStrip()
    dim_box = QGroupBox(tr("차원별 σ(Δa) — 전체 평균"))
    QVBoxLayout(dim_box).addWidget(win.dim_bars)
    rcol.addWidget(dim_box)

    win.da_hist = Histogram(tr("에피소드 평균 |Δa| 분포"))
    rcol.addWidget(win.da_hist)

    filt = QGroupBox(tr("큐레이션 후보"))
    fcol = QVBoxLayout(filt)
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("기준")))
    win.rank_combo = QComboBox()
    # 정렬 키는 전부 아래 표에 칼럼으로도 나온다 -- 정렬 기준을 바꿔야만
    # 보이는 "점수" 칸이 있으면 지금 무슨 수를 보고 있는지 알 수 없다.
    for label, key in (("평균과 차이 큰 순 = 급한 순 (권장)", "fast"),
                       ("평균과 차이 작은 순 = 느린 순", "slow"),
                       ("멈춤 비율 높은 순", "still"),
                       ("길이 짧은 순", "short"),
                       ("길이 긴 순", "long")):
        win.rank_combo.addItem(tr(label), key)
    win.rank_combo.currentIndexChanged.connect(win.stats_ops.refresh_rank_list)
    row.addWidget(win.rank_combo, 1)
    fcol.addLayout(row)

    # 그룹(scene·문장) 필터 -- 편차는 이미 그룹 단위로 계산되지만, 후보
    # 목록도 한 그룹만 놓고 보아야 "이 작업 안에서 어떤 테이크가 튀나"가
    # 읽힌다 (파일 선택은 scene 단위까지만 좁혀 주었다).
    grow = QHBoxLayout()
    grow.addWidget(QLabel(tr("그룹")))
    win.group_combo = QComboBox()
    shrinkable_combo(win.group_combo)
    win.group_combo.addItem(tr("(전체)"), None)
    win.group_combo.currentIndexChanged.connect(win.stats_ops.refresh_rank_list)
    grow.addWidget(win.group_combo, 1)
    fcol.addLayout(grow)

    len_row = QHBoxLayout()
    len_row.addWidget(QLabel(tr("길이(초)")))
    win.len_min_spin = QSlider(Qt.Orientation.Horizontal)
    win.len_max_spin = QSlider(Qt.Orientation.Horizontal)
    for s in (win.len_min_spin, win.len_max_spin):
        s.setRange(0, 300)
        s.valueChanged.connect(win.stats_ops.refresh_rank_list)
    win.len_min_spin.setValue(0)
    win.len_max_spin.setValue(300)
    len_row.addWidget(win.len_min_spin, 1)
    len_row.addWidget(win.len_max_spin, 1)
    win.len_label = QLabel("-")
    win.len_label.setMinimumWidth(96)
    len_row.addWidget(win.len_label)
    fcol.addLayout(len_row)

    win.rank_tree = QTreeWidget()
    win.rank_tree.setColumnCount(5)
    win.rank_tree.setHeaderLabels([tr("에피소드"), tr("평균과 차이"), tr("멈춤%"),
                                    tr("길이"), tr("task")])
    win.rank_tree.setRootIsDecorated(False)
    win.rank_tree.setColumnWidth(0, 150)
    for c in range(1, 4):
        win.rank_tree.setColumnWidth(c, 76)
    for c, tip in enumerate((
            tr("파일 · 에피소드"),
            tr("이 에피소드의 평균 |Δa| 에서 같은 (scene·문장) 그룹 평균을 뺀 값 (rad/frame).\n"
               "+ 는 그 작업의 보통 테이크보다 급하게, - 는 느리게 움직인 것.\n"
               "±{d} 를 넘으면 빨강/파랑").format(d=TASK_DEV_LIMIT),
            tr("속도가 {v} rad/frame 미만이던 프레임 비율 — 망설임").format(v=STILL_VEL),
            tr("에피소드 길이 (초)"),
            tr("language instruction"))):
        win.rank_tree.headerItem().setToolTip(c, tip)
    win.rank_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    win.rank_tree.itemSelectionChanged.connect(win.scene_planning.on_rank_selected)
    win.rank_tree.setMinimumHeight(220)
    fcol.addWidget(win.rank_tree, 1)
    # 판정선만 한 줄로 남긴다. 나머지 정의는 헤더 툴팁 -- 조작자가 코드를
    # 열지 않고도 "몇이면 이상한가"를 알아야 하지만, 그게 목록을 밀어내면
    # 정작 봐야 할 후보가 안 보인다.
    cols_row = QHBoxLayout()
    cols = QLabel(tr("같은 (scene·문장) 그룹 평균과의 차 — ±{d} 밖이면 급함(빨강)/느림(파랑)")
                  .format(d=TASK_DEV_LIMIT))
    cols.setStyleSheet("color:#888;")
    cols_row.addWidget(cols, 1)
    helpb = QPushButton("?")
    helpb.setFixedWidth(24)
    helpb.setToolTip(tr("칼럼 정의와 판정 기준 (docs/curation-metrics.md)"))
    helpb.clicked.connect(win.stats_ops.on_metric_help)
    cols_row.addWidget(helpb)
    fcol.addLayout(cols_row)

    btns = QHBoxLayout()
    for text, slot in ((tr("재생해서 확인"), win.playback_ops.on_rank_play),
                       (tr("선택 삭제"), win.stats_ops.on_rank_delete)):
        b = QPushButton(text)
        b.clicked.connect(slot)
        btns.addWidget(b)
    fcol.addLayout(btns)
    rcol.addWidget(filt, 1)
    split.addWidget(right)
    split.setSizes([700, 430])
    outer.addWidget(split)
    return page
