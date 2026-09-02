"""Gallery tab builder for WorkspaceWindow."""
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr

from apps.workspace.shared.sizing import shrinkable_combo


def build_gallery_tab(win) -> QWidget:
    """scene 에피소드 갤러리 (#31): 썸네일 그리드 + instruction 필터.

    더블클릭 = Playback 재생(기존 경로 재사용), 재판정 버튼 = Dataset
    페이지와 같은 코어(_relabel_episodes). 썸네일은 uid 기반 캐시라
    (에피소드 immutable) 첫 로드 이후에는 즉시 뜬다.
    """
    w = QWidget()
    col = QVBoxLayout(w)
    row = QHBoxLayout()
    win.gallery_scene_combo = QComboBox()
    shrinkable_combo(win.gallery_scene_combo)
    win.gallery_scene_combo.currentIndexChanged.connect(win.gallery_ops.refresh_gallery)
    row.addWidget(win.gallery_scene_combo, 2)
    win.gallery_filter_combo = QComboBox()
    shrinkable_combo(win.gallery_filter_combo)
    win.gallery_filter_combo.currentIndexChanged.connect(win.gallery_ops.apply_gallery_filter)
    row.addWidget(win.gallery_filter_combo, 2)
    b = QPushButton("↻")
    b.setToolTip(tr("scene 목록·썸네일 새로고침"))
    b.setMaximumWidth(32)
    b.clicked.connect(win.gallery_ops.refresh_gallery_scenes)
    row.addWidget(b)
    win.gallery_relabel_btn = QPushButton(tr("선택 재판정"))
    win.gallery_relabel_btn.clicked.connect(win.dataset_ops.on_gallery_relabel)
    row.addWidget(win.gallery_relabel_btn)
    win.gallery_replay_btn = QPushButton(tr("실로봇 재생"))
    win.gallery_replay_btn.setToolTip(tr(
        "선택한 에피소드의 관절 명령을 실로봇에 다시 보냅니다.\n"
        "로봇 노드가 켜져 있어야 하고, 로봇이 실제로 움직입니다."))
    win.gallery_replay_btn.clicked.connect(win.playback_ops.on_gallery_replay)
    row.addWidget(win.gallery_replay_btn)
    col.addLayout(row)
    win.gallery_list = QListWidget()
    win.gallery_list.setViewMode(QListWidget.ViewMode.IconMode)
    win.gallery_list.setResizeMode(QListWidget.ResizeMode.Adjust)
    win.gallery_list.setMovement(QListWidget.Movement.Static)
    win.gallery_list.setIconSize(QSize(200, 150))
    win.gallery_list.setSpacing(8)
    win.gallery_list.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection)
    win.gallery_list.itemActivated.connect(win.gallery_ops.on_gallery_activated)
    col.addWidget(win.gallery_list, 1)
    win.gallery_status = QLabel(tr("scene 을 선택하세요"))
    win.gallery_status.setStyleSheet("color:#888;")
    win.gallery_status.setWordWrap(True)
    col.addWidget(win.gallery_status)
    win._gallery_loader = None
    win._gallery_episodes = []
    win.gallery_ops.refresh_gallery_scenes()
    return w

