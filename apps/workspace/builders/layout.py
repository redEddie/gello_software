"""Main layout builders for WorkspaceWindow (center, left, right, bottom, layout)."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from gello.data.libero_format import load_crop_params
from gello.gui.constants import TODO_MARK
from gello.gui.widgets import VideoView
from gello.gui.i18n import tr

from apps.dialogs._widgets import SceneInfoView, TODO_STYLE, mark_todo
from apps.workspace.builders.sizing import relax_min_widths
from apps.workspace.constants import ACTIVITIES, PLAYBACK_SPEEDS, WIDE_FIELDS
from apps.workspace.pages import PAGE_BUILDERS

# Tab builders are imported here rather than through the package __init__ to
# avoid a circular import (this module is re-exported by __init__, and the tabs
# are only needed inside build_center).
from .analysis_tab import build_analysis_tab
from .cloud_tab import build_cloud_tab
from .depth_tab import build_depth_tab
from .gallery_tab import build_gallery_tab
from .layout_tab import build_layout_tab
from .trim_tab import build_trim_tab


def build_center(win) -> None:
    # 카메라별 크롭 정렬 -- 뷰 가이드·레이아웃 겹침·수집·변환이 전부 이
    # 값을 쓴다. 파일(~/libero_gui_logs/crop_params.json)에서 복원하고,
    # Layout 페이지 슬라이더가 바꾸면 저장한다.
    win.cameras.crop_params = load_crop_params()
    """Camera views. This widget is created once and never replaced --
    every other panel changes around it."""
    win.center_tabs = QTabWidget()
    win.center_tabs.setDocumentMode(True)

    live = QWidget()
    live_col = QVBoxLayout(live)
    live_col.setContentsMargins(4, 4, 4, 4)
    win.live_split = QSplitter(Qt.Orientation.Horizontal)
    win.live_views = {}
    win.live_boxes = {}
    win.cameras.live_maximized = None
    for key, title in (("agent", "Agent (정면)"), ("wrist", "Wrist (손목)")):
        box = QGroupBox(tr(title))
        inner = QVBoxLayout(box)
        inner.setContentsMargins(4, 4, 4, 4)
        view = VideoView()
        view.setText(tr("카메라를 선택하세요"))
        view.set_crop_guide(**win.cameras.crop_params[key])
        view.setToolTip(tr("더블클릭: 이 카메라 최대화 / 복원"))
        view.setMinimumSize(60, 45)   # 최대화 시 반대쪽이 아주 작아질 수 있게
        view.installEventFilter(win)
        inner.addWidget(view)
        win.live_views[key] = view
        win.live_boxes[key] = box
        win.live_split.addWidget(box)
    win.live_split.setSizes([600, 600])
    live_col.addWidget(win.live_split, 1)
    win.square_guide_check = QCheckBox(tr("정사각 크롭 가이드"))
    win.square_guide_check.setChecked(True)
    win.square_guide_check.setToolTip(tr(
        "LeRobot 변환은 가운데 정사각만 남깁니다. 켜면 그 바깥이 어둡게 표시됩니다."))
    win.square_guide_check.toggled.connect(win._on_square_guide)
    grow = QHBoxLayout()
    grow.addWidget(QLabel(tr("보기")))
    # 한 카메라를 전체로 키우고 반대쪽을 왼쪽 아래 PiP 로 겹친다 --
    # 뷰 더블클릭으로도 토글된다.
    win.live_view_combo = QComboBox()
    win.live_view_combo.addItem(tr("나란히"), None)
    win.live_view_combo.addItem(tr("Agent 최대"), "agent")
    win.live_view_combo.addItem(tr("Wrist 최대"), "wrist")
    win.live_view_combo.currentIndexChanged.connect(
        lambda *_: win.camera_ops.set_live_maximized(win.live_view_combo.currentData()))
    grow.addWidget(win.live_view_combo)
    grow.addSpacing(16)
    grow.addWidget(win.square_guide_check)
    grow.addSpacing(16)
    # 3×3 워크스페이스 격자 -- 편집은 격자 편집 다이얼로그, 여기는 표시만.
    win.grid_live_check = QCheckBox(tr("3×3 격자"))
    win.grid_live_check.setChecked(bool(win.cameras.grid_store.get("live_on")))
    win.grid_live_check.setToolTip(tr(
        "저장된 워크스페이스 격자를 agent 라이브 화면에 겹쳐 보입니다.\n"
        "물체를 어느 칸(A1..C3)에 놓을지 확인하는 용도입니다."))
    win.grid_live_check.toggled.connect(win.camera_ops.on_grid_live_toggled)
    grow.addWidget(win.grid_live_check)
    win.grid_alpha_slider = QSlider(Qt.Orientation.Horizontal)
    win.grid_alpha_slider.setRange(10, 100)
    win.grid_alpha_slider.setValue(int(win.cameras.grid_store.get("alpha", 60)))
    win.grid_alpha_slider.setMaximumWidth(140)
    win.grid_alpha_slider.valueChanged.connect(win.scene_ops.on_grid_alpha)
    win.grid_alpha_slider.sliderReleased.connect(win.scene_ops.on_grid_alpha_done)
    grow.addWidget(win.grid_alpha_slider)
    win.grid_alpha_label = QLabel(
        tr("{v}%").format(v=win.grid_alpha_slider.value()))
    win.grid_alpha_label.setStyleSheet("color:#888;")
    grow.addWidget(win.grid_alpha_label)
    grid_edit_btn = QPushButton(tr("격자 편집..."))
    grid_edit_btn.clicked.connect(win.scene_ops.on_edit_grid)
    grow.addWidget(grid_edit_btn)
    grow.addStretch(1)
    live.layout().addLayout(grow)
    win._live_tab_index = win.center_tabs.addTab(live, tr("Live"))

    play = QWidget()
    play_col = QVBoxLayout(play)
    play_col.setContentsMargins(4, 4, 4, 4)
    win.play_split = QSplitter(Qt.Orientation.Horizontal)
    win.play_views = {}
    for key, title in (("agent", "Agent (정면)"), ("wrist", "Wrist (손목)")):
        box = QGroupBox(tr(title))
        inner = QVBoxLayout(box)
        inner.setContentsMargins(4, 4, 4, 4)
        view = VideoView()
        view.setText(tr("에피소드를 선택하세요"))
        view.set_crop_guide(**win.cameras.crop_params[key])
        inner.addWidget(view)
        win.play_views[key] = view
        win.play_split.addWidget(box)
    win.play_split.setSizes([600, 600])
    play_col.addWidget(win.play_split, 1)

    row = QHBoxLayout()
    win.play_btn = QPushButton(tr("재생"))
    win.play_btn.setEnabled(False)
    win.play_btn.clicked.connect(win.playback_ops.on_play_toggle)
    row.addWidget(win.play_btn)
    win.play_slider = QSlider(Qt.Orientation.Horizontal)
    win.play_slider.setEnabled(False)
    win.play_slider.valueChanged.connect(win.playback_ops.show_frame)
    row.addWidget(win.play_slider, 1)
    win.play_pos = QLabel("-/-")
    win.play_pos.setMinimumWidth(80)
    win.play_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(win.play_pos)
    # 배속. 3배까지는 타이머 주기만 줄이면 되고(20 -> 60Hz) 프레임을 건너뛸
    # 필요가 없어서, 빠르게 훑을 때도 놓치는 프레임이 없다.
    row.addWidget(QLabel(tr("배속")))
    win.speed_combo = QComboBox()
    for label, mult in PLAYBACK_SPEEDS:
        win.speed_combo.addItem(label, mult)
    win.speed_combo.setCurrentIndex(
        [m for _l, m in PLAYBACK_SPEEDS].index(1.0))
    win.speed_combo.currentIndexChanged.connect(win.playback_ops.on_speed_changed)
    win.speed_combo.setMaximumWidth(80)
    row.addWidget(win.speed_combo)
    play_col.addLayout(row)
    win.play_caption = QLabel(tr("Dataset 패널에서 에피소드를 고르면 여기서 재생됩니다."))
    win.play_caption.setStyleSheet("color:#888;")
    play_col.addWidget(win.play_caption)
    win.center_tabs.addTab(play, tr("Playback"))
    win.center_tabs.addTab(build_analysis_tab(win), tr("Analysis"))
    win.playback.trim_tab_index = win.center_tabs.addTab(build_trim_tab(win), tr("Trim"))
    win._layout_tab_index = win.center_tabs.addTab(
        build_layout_tab(win), tr("레이아웃"))
    win._gallery_tab_index = win.center_tabs.addTab(
        build_gallery_tab(win), tr("Gallery"))
    win._cloud_tab_index = win.center_tabs.addTab(
        build_cloud_tab(win), tr("Point Cloud"))
    win._depth_tab_index = win.center_tabs.addTab(
        build_depth_tab(win), tr("Depth"))
    win.center_tabs.currentChanged.connect(win._on_center_tab_changed)



def build_left(win) -> None:
    win.left_stack = QStackedWidget()
    win.left_pages = {}
    for key, _icon, title, _tip in ACTIVITIES:
        page = PAGE_BUILDERS[key](win)
        wrapper = QWidget()
        col = QVBoxLayout(wrapper)
        col.setContentsMargins(6, 6, 6, 6)
        head = QLabel(title.upper())
        f = head.font()
        f.setPointSize(max(8, f.pointSize() - 1))
        f.setBold(True)
        head.setFont(f)
        head.setStyleSheet("color:#888; letter-spacing:1px;")
        col.addWidget(head)
        # 페이지가 창보다 길어지면(예: Configure 의 scene 그룹) 세로
        # 스크롤. 가로 스크롤은 쓰지 않는다 -- 내용이 패널 폭에 맞게
        # 접히는 것이 원칙이다 (긴 한 줄 표시는 SceneInfoView 처럼 줄바꿈
        # 또는 Ignored 정책으로 해결).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        relax_min_widths(page)
        scroll.setWidget(page)
        col.addWidget(scroll, 1)
        win.left_pages[key] = win.left_stack.count()
        win.left_stack.addWidget(wrapper)



def build_right(win) -> None:
    win.right_panel = QWidget()
    col = QVBoxLayout(win.right_panel)
    col.setContentsMargins(6, 6, 6, 6)

    win.right_fields = {}
    for title, keys in (
        ("Robot", (("robot", "연결"), ("node", "노드"), ("state", "상태"))),
        ("Camera", (("cam_agent", "Agent"), ("cam_wrist", "Wrist"), ("fps", "FPS"))),
        ("Recording", (("recording", "기록"), ("episode", "마지막 에피소드"),
                       ("frames", "프레임"))),
        # 파일과 스키마가 한 칸에 같이 있어야 "지금 어디에, 어떤 형식으로
        # 쌓이는가"가 한눈에 잡힌다. 세션 중에는 그 세션의 값이, 아닐 때는
        # 트리에서 고른 파일의 값이 뜬다.
        ("Dataset", (("ds_file", "파일"), ("ds_task", "태스크"),
                     ("ds_episodes", "에피소드"), ("ds_action", "액션 공간"),
                     ("ds_gripper", "그리퍼 규약"), ("ds_image", "이미지"),
                     ("ds_fps", "FPS"), ("ds_repack", "재압축"))),
    ):
        box = QGroupBox(tr(title))
        form = QFormLayout(box)
        form.setVerticalSpacing(6)
        for key, label in keys:
            lab = QLabel("-")
            lab.setWordWrap(True)
            if key in WIDE_FIELDS:
                # 파일명과 자연어 지시문만 길다. 라벨-값을 좌우로 놓으면 값이
                # 150px 남짓에 갇혀 서너 줄로 접히는데, 정작 수집 중 가장
                # 자주 확인하는 두 줄이다. 이 둘만 캡션을 위에 올리고 값이
                # 패널 폭을 다 쓰게 한다.
                cap = QLabel(tr(label))
                cap.setStyleSheet("color:#888; font-size:11px;")
                lab.setStyleSheet("padding: 2px 0 6px 0;")
                lab.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                # QLabel은 wordWrap을 켜도 sizePolicy의 heightForWidth가
                # 꺼져 있어 레이아웃이 높이를 한 줄치로만 준다 -- 두 줄짜리
                # 지시문이 잘려서 뒤가 안 보였다. 켜 줘야 접힌 만큼 높이가
                # 확보된다.
                sp = lab.sizePolicy()
                sp.setHeightForWidth(True)
                sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
                lab.setSizePolicy(sp)
                form.addRow(cap)
                form.addRow(lab)
            else:
                form.addRow(tr(label), lab)
            win.right_fields[key] = lab
        col.addWidget(box)

    # 지금 수집 중인 scene 의 물체 배치(3×3)를 세션 내내 보여준다 --
    # 물체를 제자리에 되돌릴 때 Configure 로 오갈 필요가 없게.
    scene_box = QGroupBox(tr("Scene 배치 (수집 중)"))
    sv = QVBoxLayout(scene_box)
    sv.setContentsMargins(6, 6, 6, 6)
    win.right_scene_view = SceneInfoView()
    win.right_scene_view.setText(tr("(scene 세션 없음)"))
    sv.addWidget(win.right_scene_view)
    col.addWidget(scene_box)

    sysbox = QGroupBox(f"System ({TODO_MARK})")
    sform = QFormLayout(sysbox)
    for label in ("CPU", "GPU", "Memory"):
        sform.addRow(label, QLabel("-"))
    mark_todo(sysbox, tr("시스템 사용률 표시는 아직 없습니다. 디스크는 Statistics에 있습니다."))
    col.addWidget(sysbox)
    col.addStretch()



def build_bottom(win) -> None:
    win.bottom_tabs = QTabWidget()
    win.bottom_tabs.setDocumentMode(True)
    win.log_view = QPlainTextEdit()
    win.log_view.setReadOnly(True)
    win.log_view.setMaximumBlockCount(4000)
    win.bottom_tabs.addTab(win.log_view, tr("Log"))
    win.upload_view = QPlainTextEdit()
    win.upload_view.setReadOnly(True)
    win.upload_view.setMaximumBlockCount(4000)
    win.bottom_tabs.addTab(win.upload_view, tr("Upload"))
    win.validation_view = QPlainTextEdit()
    win.validation_view.setReadOnly(True)
    win.bottom_tabs.addTab(win.validation_view, tr("Validation"))
    for title, why in (
        (tr("ROS2"), tr("이 스택은 ROS2가 아니라 pylibfranka로 직접 구동합니다.")),
        (tr("Terminal"), tr("임베디드 셸은 아직 없습니다. 로그 탭을 쓰세요.")),
    ):
        ph = QPlainTextEdit(f"{title} — {TODO_MARK}\n\n{why}")
        ph.setReadOnly(True)
        ph.setStyleSheet(TODO_STYLE)
        idx = win.bottom_tabs.addTab(ph, f"{title} ({TODO_MARK})")
        win.bottom_tabs.setTabEnabled(idx, False)



def build_layout(win) -> None:
    win.activity_bar = QToolBar()
    win.activity_bar.setOrientation(Qt.Orientation.Vertical)
    win.activity_bar.setMovable(False)
    win.activity_bar.setIconSize(win.activity_bar.iconSize())
    win.activity_bar.setStyleSheet(
        "QToolBar{background:#2b2b2b; border:none; spacing:2px; padding:4px;}"
        "QToolButton{color:#bbb; font-size:20px; padding:8px; border:none;}"
        "QToolButton:hover{background:#3a3a3a;}"
        "QToolButton:checked{background:#3a3a3a; color:#fff;"
        " border-left:2px solid #2ecc71;}"
    )
    win._activity_group = QActionGroup(win)
    win._activity_group.setExclusive(True)
    win._activity_actions = {}
    for key, icon, title, tip in ACTIVITIES:
        act = QAction(icon, win)
        act.setCheckable(True)
        act.setToolTip(f"{title} — {tr(tip)}")
        act.triggered.connect(lambda _c, k=key: win._set_activity(k))
        win._activity_group.addAction(act)
        win.activity_bar.addAction(act)
        win._activity_actions[key] = act

    # 로그는 중앙 열 안에, 카메라 바로 아래에만 둔다. 창 전체 폭으로 깔면
    # 왼쪽/오른쪽 패널이 로그 높이만큼 잘려서, 정작 세로로 긴 것들(에피소드
    # 트리, 상태 목록)이 먼저 손해를 본다. VS Code의 사이드바가 전체 높이를
    # 쓰고 패널이 에디터 아래에만 오는 것과 같은 이유다.
    win.center_split = QSplitter(Qt.Orientation.Vertical)
    win.center_split.addWidget(win.center_tabs)
    win.center_split.addWidget(win.bottom_tabs)
    win.center_split.setStretchFactor(0, 1)
    win.center_split.setStretchFactor(1, 0)
    win.center_split.setSizes([720, 220])
    win.center_split.setChildrenCollapsible(False)

    win.upper_split = QSplitter(Qt.Orientation.Horizontal)
    win.left_stack.setMinimumWidth(200)
    win.right_panel.setMinimumWidth(200)
    # 배치도가 붙으면서 패널이 창보다 길어질 수 있다 -- 세로 스크롤로
    # 감싼다 (가로는 원칙대로 없음, 내용이 접힌다).
    relax_min_widths(win.right_panel)
    right_scroll = QScrollArea()
    right_scroll.setWidgetResizable(True)
    right_scroll.setFrameShape(QFrame.Shape.NoFrame)
    right_scroll.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    right_scroll.setWidget(win.right_panel)
    right_scroll.setMinimumWidth(200)
    win.right_scroll = right_scroll
    win.center_tabs.setMinimumWidth(420)
    win.bottom_tabs.setMinimumHeight(90)
    win.upper_split.addWidget(win.left_stack)
    win.upper_split.addWidget(win.center_split)
    win.upper_split.addWidget(win.right_scroll)
    # Only the center grows when the window does: the two side panels hold
    # text at a readable width, the camera is the thing worth more pixels.
    win.upper_split.setStretchFactor(0, 0)
    win.upper_split.setStretchFactor(1, 1)
    win.upper_split.setStretchFactor(2, 0)
    win.upper_split.setSizes([320, 1120, 300])
    win.upper_split.setChildrenCollapsible(False)

    central = QWidget()
    row = QHBoxLayout(central)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.addWidget(win.activity_bar)
    row.addWidget(win.upper_split, 1)
    win.setCentralWidget(central)
