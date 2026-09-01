"""Depth tab builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gello.gui.gui_widgets import VideoView
from gello.gui.i18n import tr


def build_depth_tab(win) -> QWidget:
    """depth 컬러맵 라이브 뷰 -- Point Cloud 와 같은 워커·같은 수명주기.

    스키마의 depth 기록(#17)과 별개다: 여기는 수집 전에 depth 품질과
    범위를 눈으로 확인하는 뷰고, 기록 여부는 Settings 의 스키마
    체크박스가 정한다.
    """
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(4, 4, 4, 4)
    win.depth_view = VideoView()
    win.depth_view.setText(tr("탭에 들어오면 depth 스트림을 켭니다"))
    # depth 는 원본 해상도 그대로 기록/표시 -- 크롭 가이드 비적용
    win.depth_view.set_square_guide(False)
    # 마우스가 가리키는 지점의 실거리 표시 (eventFilter 에서 처리)
    win.depth_view.setMouseTracking(True)
    win.depth_view.installEventFilter(win)
    win._depth_cursor = None
    col.addWidget(win.depth_view, 1)
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("카메라")))
    win.depth_cam_combo = QComboBox()
    win.depth_cam_combo.addItem("Agent", "agent")
    win.depth_cam_combo.addItem("Wrist", "wrist")
    win.depth_cam_combo.currentIndexChanged.connect(win._on_cloud_cam_changed)
    row.addWidget(win.depth_cam_combo)
    row.addSpacing(12)
    row.addWidget(QLabel(tr("최대 거리")))
    win.depth_range_slider = QSlider(Qt.Orientation.Horizontal)
    win.depth_range_slider.setRange(30, 300)      # 0.3 ~ 3.0 m
    win.depth_range_slider.setValue(120)
    win.depth_range_slider.valueChanged.connect(
        lambda *_: win._render_depth())
    row.addWidget(win.depth_range_slider, 1)
    win.depth_range_label = QLabel("1.2 m")
    win.depth_range_label.setStyleSheet("color:#888;")
    row.addWidget(win.depth_range_label)
    col.addLayout(row)
    win.depth_status = QLabel(tr(
        "가까움=빨강, 멂=파랑, 검정=측정 불가. 기록 여부는 Settings 의 "
        "스키마 체크박스(#17)가 정합니다."))
    win.depth_status.setStyleSheet("color:#888;")
    win.depth_status.setWordWrap(True)
    col.addWidget(win.depth_status)
    return w

