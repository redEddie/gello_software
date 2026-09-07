"""Main layout builders for WorkspaceWindow (center, left, right, bottom, layout)."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence
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

from gello.data.crop import load_crop_params
from gello.gui.widgets import VideoView
from gello.gui.i18n import tr

from apps.workspace.shared.widgets import SceneInfoView
from apps.workspace.shared.sizing import relax_min_widths
from apps.workspace.shared.tabs import tab_title
from apps.workspace.constants import (
    ACTIVITIES,
    CENTER_TABS,
    PLAYBACK_SPEEDS,
    WIDE_FIELDS,
    workflow_step,
)
from .page_builders import PAGE_BUILDERS
from .right_builders import RIGHT_BUILDERS

# Tab builders are imported here rather than through the package __init__ to
# avoid a circular import (this module is re-exported by __init__, and the tabs
# are only needed inside build_center).
from apps.workspace.features.collection import build_collect_header
from apps.workspace.features.stats.analysis_tab import build_analysis_tab
from apps.workspace.features.camera import build_cloud_tab, build_depth_tab
from apps.workspace.features.gallery import build_gallery_tab
from apps.workspace.features.playback import build_trim_tab
from apps.workspace.features.scene.layout_tab import build_layout_tab
from apps.workspace.features.doctor.record_tab import build_record_tab
from apps.workspace.features.scene.plan_tab import build_plan_tab
from apps.workspace.features.scene.scene_tab import build_scene_tab


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
    win.grid_alpha_slider.valueChanged.connect(win.layout_ref.on_grid_alpha)
    win.grid_alpha_slider.sliderReleased.connect(win.layout_ref.on_grid_alpha_done)
    grow.addWidget(win.grid_alpha_slider)
    win.grid_alpha_label = QLabel(
        tr("{v}%").format(v=win.grid_alpha_slider.value()))
    win.grid_alpha_label.setStyleSheet("color:#888;")
    grow.addWidget(win.grid_alpha_label)
    grid_edit_btn = QPushButton(tr("격자 편집..."))
    grid_edit_btn.clicked.connect(win.layout_ref.on_edit_grid)
    grow.addWidget(grid_edit_btn)
    grow.addStretch(1)
    live.layout().addLayout(grow)

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
    # 탭은 키로 등록한다. 코드가 인덱스로 탭을 가리키면 탭이 하나만 늘어도
    # 전부 밀리고, 그 밀림은 조용하다 (엉뚱한 탭이 열릴 뿐 예외가 안 난다).
    # 순서·제목의 정본은 constants.CENTER_TABS 다.
    win.center_tab_widgets = {
        "live": live,
        "instruction": build_plan_tab(win),
        "scene": build_scene_tab(win),
        "doc_record": build_record_tab(win),
        "playback": play,
        "analysis": build_analysis_tab(win),
        "trim": build_trim_tab(win),
        "layout": build_layout_tab(win),
        "gallery": build_gallery_tab(win),
        "cloud": build_cloud_tab(win),
        "depth": build_depth_tab(win),
    }
    missing = [k for k, _t in CENTER_TABS if k not in win.center_tab_widgets]
    if missing:
        raise RuntimeError(f"CENTER_TABS 에 있는데 만들지 않은 탭: {missing}")
    for key, _title in CENTER_TABS:
        win.center_tabs.addTab(win.center_tab_widgets[key], tab_title(key))
    win.center_tabs.currentChanged.connect(win._on_center_tab_changed)



def build_left(win) -> None:
    win.left_stack = QStackedWidget()
    win.left_pages = {}
    for key, _icon, title, _tip in ACTIVITIES:
        page = PAGE_BUILDERS[key](win)
        wrapper = QWidget()
        col = QVBoxLayout(wrapper)
        col.setContentsMargins(6, 6, 6, 6)
        # 수집 한 바퀴의 단계면 번호를 앞에 붙인다 -- 아이콘 바만으로는
        # 순서가 순서라는 것을 알 수 없다 (아이콘은 그림이지 차례가 아니다).
        step = workflow_step(key)
        head = QLabel(f"{step[0]} {title.upper()}" if step else title.upper())
        f = head.font()
        f.setPointSize(max(8, f.pointSize() - 1))
        f.setBold(True)
        head.setFont(f)
        head.setStyleSheet("color:#888; letter-spacing:1px;")
        col.addWidget(head)
        # "다음 단계" 버튼은 뺐다 (2026-09-06 사용자). 활동 바 아이콘을
        # 누르는 편이 낫다 -- 번호(①②③④)가 이미 순서를 말하고 Ctrl+1~7 도
        # 있어서, 페이지마다 같은 일을 하는 버튼을 하나 더 두는 것은
        # 자리만 먹었다.
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

    # **우측 패널은 활동탭마다 자기 구현을 갖는다** (2026-09-07 사용자 결정).
    #
    # 공용 정보 상자를 활동에 따라 골라 보여주는 자리가 아니다. 그 방식은
    # 한 번 만들어 봤다가 버렸다 -- 화면은 똑같아 보이는데 패널이 무엇을
    # 하는 곳인지가 활동마다 달라지고, "이 활동에 필요한 정보" 라는 기준은
    # 무엇이든 통과시켜서 결국 다시 전부 쌓인다.
    #
    # 그래서 왼쪽 패널과 같은 구조다: 활동마다 페이지 하나, RIGHT_BUILDERS
    # 에 등록된 활동은 자기 위젯을 짓고, 없는 활동은 아래의 세션 페이지를
    # 함께 쓴다. Doctor 가 첫 구현이고(고른 지시문에 하는 일), 나머지는
    # 차례로 자기 것을 갖게 된다.
    #
    # 세션 페이지 -- 지금 쌓이는 데이터의 값. 장치가 살아 있는가는 상태바가
    # (별도 프로세스의 생사), 지금 어느 단계인가는 헤더가 맡는다. 그래서
    # Robot 그룹(연결·노드·상태)과 Recording 의 '기록' 줄이 빠졌다.
    win.right_fields = {}
    for box_key, title, keys in (
        ("camera", "Camera", (("cam_agent", "Agent"), ("cam_wrist", "Wrist"), ("fps", "FPS"))),
        ("recording", "Recording", (("episode", "마지막 에피소드"), ("frames", "프레임"))),
        # 파일과 스키마가 한 칸에 같이 있어야 "지금 어디에, 어떤 형식으로
        # 쌓이는가"가 한눈에 잡힌다. 세션 중에는 그 세션의 값이, 아닐 때는
        # 트리에서 고른 파일의 값이 뜬다.
        # 스키마 버전이 형식 줄들의 머리에 온다 -- 아래 네 줄(액션 공간·
        # 그리퍼·이미지·FPS)이 "무슨 규약인가"의 세부이고, 버전이 그 규약의
        # 이름이다. 데이터셋 하나가 여러 버전을 담을 수 있게 된 뒤로는
        # (knu-1.1.0 부터, 이슈 #12) 파일마다 다를 수 있어서, 미리 볼 때
        # 이 줄이 없으면 어느 규약으로 읽어야 하는지 알 수 없다.
        ("dataset", "Dataset", (("ds_file", "파일"), ("ds_task", "태스크"),
                     ("ds_episodes", "에피소드"), ("ds_schema", "스키마"),
                     ("ds_action", "액션 공간"),
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
        # 줄 수가 고정된 상자는 세로로 늘어나지 않게 못박는다. 안 그러면
        # 패널에서 무언가를 뺐을 때 남은 상자들이 그 자리를 나눠 먹어서,
        # 줄인 효과가 화면에 보이지 않는다 (2026-09-06 실측: Camera 가
        # 95px 를 원하는데 136px 로 늘어나 있었다). Dataset 은 예외 --
        # 파일명·지시문이 접히면서 높이가 늘어야 한다.
        if title != "Dataset":
            box.setSizePolicy(QSizePolicy.Policy.Preferred,
                              QSizePolicy.Policy.Fixed)
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

    # 닥터에서 고른 지시문(task) 한 줄의 상세와, 그 줄에 하는 일.
    # System(CPU/GPU/Memory) 자리표시 상자를 뺐다 (2026-09-06). 값이 늘 "-"
    # 였고 우측 패널 높이의 1/8 을 썼다. 디스크 사용량은 Statistics 에 있다.
    col.addStretch()

    # 활동마다 페이지 하나. RIGHT_BUILDERS 에 없는 활동은 세션 페이지를
    # 함께 쓴다 (인덱스를 나눠 갖는다 -- 위젯은 부모가 하나뿐이라 복제할
    # 수 없고, 복제할 이유도 없다).
    win.right_stack = QStackedWidget()
    win.right_pages = {}
    session_idx = win.right_stack.count()
    win.right_stack.addWidget(_wrap_right(win.right_panel))
    for key, _icon, _title, _tip in ACTIVITIES:
        build = RIGHT_BUILDERS.get(key)
        if build is None:
            win.right_pages[key] = session_idx
            continue
        win.right_pages[key] = win.right_stack.count()
        win.right_stack.addWidget(_wrap_right(build(win)))


def _wrap_right(page: QWidget) -> QWidget:
    """우측 페이지의 공통 껍데기 -- 세로 스크롤, 가로 스크롤 없음."""
    page.setMinimumWidth(200)
    relax_min_widths(page)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(page)
    return scroll



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
    # 비활성 자리표시 탭 ROS2·Terminal 을 뺐다 (2026-09-06). 누를 수 없는
    # 탭이 탭 바의 40% 를 차지하면서 알려주는 것은 "없다"뿐이었다.
    # 원래 적혀 있던 내용은 지금도 사실이라 여기 남긴다:
    #   ROS2     -- 이 스택은 ROS2 가 아니라 pylibfranka 로 직접 구동한다.
    #   Terminal -- 임베디드 셸은 없다. 로그는 Log 탭에서 본다.



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
    for n, (key, icon, title, tip) in enumerate(ACTIVITIES, start=1):
        act = QAction(icon, win)
        act.setCheckable(True)
        # 단계면 번호를, 어느 것이든 단축키를 툴팁에 적는다. 손이 리더암에
        # 있는 동안 화면을 옮기려면 마우스로 아이콘을 조준하는 것보다
        # Ctrl+숫자가 빠르고, 그 사실을 알 자리가 여기밖에 없다.
        step = workflow_step(key)
        mark = f"{step[0]} " if step else ""
        act.setToolTip(f"{mark}{title} — {tr(tip)}   (Ctrl+{n})")
        act.setShortcut(QKeySequence(f"Ctrl+{n}"))
        # 툴바 버튼은 포커스가 그 위에 있어야 단축키를 받는다 -- 창 어디에
        # 포커스가 있든 들어야 하므로 창 범위로 올린다.
        act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act.triggered.connect(lambda _c, k=key: win._set_activity(k))
        win._activity_group.addAction(act)
        win.activity_bar.addAction(act)
        win._activity_actions[key] = act

    # 로그는 중앙 열 안에, 카메라 바로 아래에만 둔다. 창 전체 폭으로 깔면
    # 왼쪽/오른쪽 패널이 로그 높이만큼 잘려서, 정작 세로로 긴 것들(에피소드
    # 트리, 상태 목록)이 먼저 손해를 본다. VS Code의 사이드바가 전체 높이를
    # 쓰고 패널이 에디터 아래에만 오는 것과 같은 이유다.
    win.center_split = QSplitter(Qt.Orientation.Vertical)
    # 수집 HUD 는 탭 위에 고정한다 -- 스플리터에 따로 넣지 않는 이유는
    # 조작자가 그것을 접거나 0 으로 줄일 수 있으면 안 되기 때문이다
    # (왼쪽 패널의 "진행" 상자가 스크롤 밖으로 밀려 안 보이던 것이 이
    # 작업의 출발점이다).
    center_col = QWidget()
    cc = QVBoxLayout(center_col)
    cc.setContentsMargins(0, 0, 0, 0)
    cc.setSpacing(0)
    cc.addWidget(build_collect_header(win))
    cc.addWidget(win.center_tabs, 1)
    win.center_split.addWidget(center_col)
    win.center_split.addWidget(win.bottom_tabs)
    win.center_split.setStretchFactor(0, 1)
    win.center_split.setStretchFactor(1, 0)
    win.center_split.setSizes([720, 220])
    win.center_split.setChildrenCollapsible(False)

    win.upper_split = QSplitter(Qt.Orientation.Horizontal)
    win.left_stack.setMinimumWidth(200)
    # 스크롤은 페이지마다 _wrap_right 가 씌웠다 (패널이 창보다 길어질 수
    # 있다 -- 가로는 원칙대로 없고 내용이 접힌다).
    win.right_stack.setMinimumWidth(200)
    # 옛 이름 -- 우측 패널을 통째로 가리키던 코드가 아직 있다.
    win.right_scroll = win.right_stack
    win.center_tabs.setMinimumWidth(420)
    win.bottom_tabs.setMinimumHeight(90)
    win.upper_split.addWidget(win.left_stack)
    win.upper_split.addWidget(win.center_split)
    win.upper_split.addWidget(win.right_stack)
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
