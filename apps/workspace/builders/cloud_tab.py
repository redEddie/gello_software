"""Point-cloud tab builder for WorkspaceWindow."""
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


def build_cloud_tab(win) -> QWidget:
    """agent 카메라의 depth 포인트클라우드 뷰 (탭이 보일 때만 스트림).

    depth 는 상시로 켜 두면 USB 대역·안정성을 잡아먹으므로, 이 탭에
    들어올 때 RGB 미리보기를 잠깐 내리고 depth 워커를 올린다. 탭을
    떠나면 반대로 되돌린다 (수집 세션과는 아예 공존 불가 -- 세션 중엔
    안내만 보여준다).
    """
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(4, 4, 4, 4)
    win.cloud_view = VideoView()
    win.cloud_view.setText(tr("탭에 들어오면 depth 스트림을 켭니다"))
    # 크롭 가이드는 학습 프레이밍용 -- 3D 뷰에는 의미가 없고 어둡게만 보인다
    win.cloud_view.set_square_guide(False)
    col.addWidget(win.cloud_view, 1)
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("카메라")))
    win.cloud_cam_combo = QComboBox()
    win.cloud_cam_combo.addItem("Agent", "agent")
    win.cloud_cam_combo.addItem("Wrist", "wrist")
    win.cloud_cam_combo.setToolTip(tr(
        "포인트클라우드를 읽을 카메라. 탭이 열려 있으면 즉시 전환합니다."))
    win.cloud_cam_combo.currentIndexChanged.connect(win._on_cloud_cam_changed)
    row.addWidget(win.cloud_cam_combo)
    row.addSpacing(12)
    row.addWidget(QLabel(tr("회전")))
    win.cameras.cloud_yaw = QSlider(Qt.Orientation.Horizontal)
    win.cameras.cloud_yaw.setRange(-80, 80)
    win.cameras.cloud_yaw.setValue(25)
    win.cameras.cloud_yaw.valueChanged.connect(lambda *_: win._render_cloud())
    row.addWidget(win.cameras.cloud_yaw, 1)
    row.addWidget(QLabel(tr("기울임")))
    win.cameras.cloud_pitch = QSlider(Qt.Orientation.Horizontal)
    win.cameras.cloud_pitch.setRange(-80, 80)
    win.cameras.cloud_pitch.setValue(-30)
    win.cameras.cloud_pitch.valueChanged.connect(lambda *_: win._render_cloud())
    row.addWidget(win.cameras.cloud_pitch, 1)
    col.addLayout(row)
    win.cloud_status = QLabel("")
    win.cloud_status.setStyleSheet("color:#888;")
    col.addWidget(win.cloud_status)
    return w

