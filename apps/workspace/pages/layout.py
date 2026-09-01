"""Layout page builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr


def build_layout_page(win) -> QWidget:
    """카메라 레이아웃 설정 -- 레이아웃 탭의 뷰를 보면서 조작하는 왼쪽 패널."""
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    open_btn = QPushButton(tr("레이아웃 탭 열기"))
    open_btn.clicked.connect(
        lambda: win.center_tabs.setCurrentIndex(win._layout_tab_index))
    col.addWidget(open_btn)

    # Configure 의 카메라 그룹 복제 -- 여기서 고르나 저기서 고르나 같다.
    # Configure 쪽 콤보가 원본이고 이쪽은 미러: 이쪽에서 바꾸면 원본으로
    # 밀어넣고(_on_layout_camera_changed), 원본이 바뀌면 여기로 복사한다
    # (_mirror_camera_combos). 미리보기 재시작은 원본의 시그널이 담당한다.
    cam = QGroupBox(tr("카메라"))
    cform = QFormLayout(cam)
    win.layout_agent_combo = QComboBox()
    win.layout_wrist_combo = QComboBox()
    for c in (win.layout_agent_combo, win.layout_wrist_combo):
        c.setEditable(True)
        c.currentTextChanged.connect(win.camera_ops.on_layout_camera_changed)
    cform.addRow(tr("Agent"), win.layout_agent_combo)
    cform.addRow(tr("Wrist"), win.layout_wrist_combo)
    refresh = QPushButton(tr("카메라 새로고침"))
    refresh.clicked.connect(win.camera_ops.refresh_cameras)
    cform.addRow(refresh)
    win.layout_preview_btn = QPushButton(tr("미리보기 시작"))
    win.layout_preview_btn.clicked.connect(win.camera_ops.on_toggle_previews)
    cform.addRow(win.layout_preview_btn)
    win.layout_camera_hint = QLabel("")
    win.layout_camera_hint.setStyleSheet("color:#888;")
    win.layout_camera_hint.setWordWrap(True)
    cform.addRow(win.layout_camera_hint)
    col.addWidget(cam)

    show = QGroupBox(tr("슬라이드쇼"))
    sform = QFormLayout(show)
    win.layout_suite_combo = QComboBox()
    win.layout_suite_combo.currentIndexChanged.connect(win._layout_refilter)
    sform.addRow(tr("Suite"), win.layout_suite_combo)
    nav = QWidget()
    nrow = QHBoxLayout(nav)
    nrow.setContentsMargins(0, 0, 0, 0)
    prev_btn = QPushButton("◀")
    prev_btn.clicked.connect(lambda: win._layout_step(-1))
    nrow.addWidget(prev_btn)
    win.layout_play_btn = QPushButton(tr("일시정지"))
    win.layout_play_btn.clicked.connect(win._layout_toggle_play)
    nrow.addWidget(win.layout_play_btn, 1)
    next_btn = QPushButton("▶")
    next_btn.clicked.connect(lambda: win._layout_step(+1))
    nrow.addWidget(next_btn)
    sform.addRow(nav)
    win.layout_interval_combo = QComboBox()
    for sec in (3, 5, 10):
        win.layout_interval_combo.addItem(tr("{s}초마다").format(s=sec), sec)
    win.layout_interval_combo.setCurrentIndex(1)
    win.layout_interval_combo.currentIndexChanged.connect(
        win._layout_apply_interval)
    sform.addRow(tr("전환 간격"), win.layout_interval_combo)
    col.addWidget(show)

    disp = QGroupBox(tr("표시"))
    dform = QFormLayout(disp)
    # 스틸(LIBERO)이 카메라 위. 0% = 카메라만, 100% = 스틸만.
    win.layout_alpha_slider = QSlider(Qt.Orientation.Horizontal)
    win.layout_alpha_slider.setRange(0, 100)
    win.layout_alpha_slider.setValue(50)
    win.layout_alpha_slider.valueChanged.connect(win._layout_alpha_changed)
    win.layout_alpha_label = QLabel(tr("스틸 50%"))
    win.layout_alpha_label.setStyleSheet("color:#888;")
    dform.addRow(win.layout_alpha_label, win.layout_alpha_slider)
    win.layout_blink_check = QCheckBox(tr("카메라/스틸 번갈아 보기"))
    win.layout_blink_check.toggled.connect(win._layout_blink_toggled)
    dform.addRow(win.layout_blink_check)
    win.layout_blink_slider = QSlider(Qt.Orientation.Horizontal)
    win.layout_blink_slider.setRange(50, 500)      # ms
    win.layout_blink_slider.setValue(500)
    win.layout_blink_label = QLabel(tr("전환 0.50초"))
    win.layout_blink_label.setStyleSheet("color:#888;")
    win.layout_blink_slider.valueChanged.connect(
        win._layout_blink_interval_changed)
    dform.addRow(win.layout_blink_label, win.layout_blink_slider)
    win.layout_grid_check = QCheckBox(tr("격자 표시 (수평 확인)"))
    win.layout_grid_check.toggled.connect(
        lambda _on: win._layout_rerender())
    dform.addRow(win.layout_grid_check)
    ws_grid_btn = QPushButton(tr("3×3 워크스페이스 격자 편집..."))
    ws_grid_btn.setToolTip(tr(
        "카메라에 비친 작업면의 꼭짓점 4개를 드래그해 3×3 격자를 만들고 "
        "저장합니다.\nLive 탭의 '3×3 격자' 체크박스로 겹쳐 볼 수 있습니다."))
    ws_grid_btn.clicked.connect(win._on_edit_grid)
    dform.addRow(ws_grid_btn)
    col.addWidget(disp)

    # 크롭 정렬 -- 값은 640 폭 기준 px. 라이브 가이드·레이아웃 겹침·변환이
    # 같은 값을 쓰고, 에피소드마다 attrs["crop_params"] 로 저장된다.
    crop = QGroupBox(tr("크롭 정렬"))
    crform = QFormLayout(crop)
    p = win.cameras.crop_params

    def _slider(lo: int, hi: int, val: int) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    win.crop_agent_zoom = _slider(100, 200, round(p["agent"]["zoom"] * 100))
    win.crop_agent_zoom_label = QLabel("")
    crform.addRow(win.crop_agent_zoom_label, win.crop_agent_zoom)
    win.crop_agent_x = _slider(-80, 80, int(p["agent"]["x"]))
    win.crop_agent_x_label = QLabel("")
    crform.addRow(win.crop_agent_x_label, win.crop_agent_x)
    win.crop_agent_y = _slider(-100, 100, int(p["agent"]["y"]))
    win.crop_agent_y_label = QLabel("")
    crform.addRow(win.crop_agent_y_label, win.crop_agent_y)
    win.crop_wrist_x = _slider(-80, 80, int(p["wrist"]["x"]))
    win.crop_wrist_x_label = QLabel("")
    crform.addRow(win.crop_wrist_x_label, win.crop_wrist_x)
    for s in (win.crop_agent_zoom, win.crop_agent_x,
              win.crop_agent_y, win.crop_wrist_x):
        s.valueChanged.connect(win._crop_changed)
    reset_btn = QPushButton(tr("기본값으로"))
    reset_btn.clicked.connect(win._crop_reset)
    crform.addRow(reset_btn)
    win._crop_widgets = [win.crop_agent_zoom, win.crop_agent_x,
                          win.crop_agent_y, win.crop_wrist_x, reset_btn]
    win._refresh_crop_labels()
    col.addWidget(crop)
    col.addStretch()
    return w

