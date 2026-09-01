"""Layout tab builder for WorkspaceWindow."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gello.gui.gui_widgets import VideoView
from gello.gui.i18n import tr



def build_layout_tab(win) -> QWidget:
    """LIBERO 초기 배치와 현재 카메라를 비교하는 탭.

    위: 참조 이미지와 카메라를 50%씩 섞은 겹침 뷰 (agent / wrist).
    아래: 참조·카메라 4장을 나란히. 로그 자리가 필요하므로 이 탭이
    보이는 동안은 하단 로그 패널을 접는다(_on_center_tab_changed).
    카메라 쪽은 변환 파이프라인과 같은 크롭(wrist 는 +31px)을 거치므로
    보이는 그대로가 학습 입력 프레이밍이다.
    """
    win._layout_entries: list = []      # (suite, name, agent_png, wrist_png)
    win._layout_idx = 0
    win._layout_playing = True
    win._layout_ref: dict = {}          # role -> (224,224,3) RGB
    win._last_cam_frame: dict = {}      # role -> 카메라 원본 (640x480)

    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(4, 4, 4, 4)

    # 컨트롤(suite·재생·간격·투명도·번갈아 보기)은 왼쪽 Layout 페이지에
    # 있다(_page_layout) -- 뷰를 보면서 조작할 수 있도록. 탭에는 지금 몇
    # 번째 스틸인지만 남긴다.
    win._layout_blink_state = False
    win.layout_name_label = QLabel("")
    win.layout_name_label.setStyleSheet("color:#888;")
    col.addWidget(win.layout_name_label)

    split = QSplitter(Qt.Orientation.Horizontal)
    win.layout_overlay_views = {}
    for role, title in (("agent", "Agent 비교"), ("wrist", "Wrist 비교")):
        box = QGroupBox(tr(title))
        inner = QVBoxLayout(box)
        inner.setContentsMargins(4, 4, 4, 4)
        v = VideoView()
        v.setText(tr("참조 이미지 없음"))
        inner.addWidget(v)
        win.layout_overlay_views[role] = v
        split.addWidget(box)
    split.setSizes([600, 600])
    col.addWidget(split, 1)

    strip = QHBoxLayout()
    win.layout_strip_views = {}
    for key, cap in (("agent_ref", "LIBERO agent"), ("agent_live", tr("카메라 agent")),
                     ("wrist_ref", "LIBERO wrist"), ("wrist_live", tr("카메라 wrist"))):
        cell = QVBoxLayout()
        v = VideoView()
        v.setMinimumSize(120, 120)
        cell.addWidget(v, 1)
        lab = QLabel(cap)
        lab.setStyleSheet("color:#888;")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cell.addWidget(lab)
        win.layout_strip_views[key] = v
        strip.addLayout(cell, 1)
    strip_w = QWidget()
    strip_w.setLayout(strip)
    strip_w.setMinimumHeight(150)
    strip_w.setMaximumHeight(220)
    col.addWidget(strip_w)

    win._layout_timer = QTimer(win)
    win._layout_timer.setInterval(5000)
    win._layout_timer.timeout.connect(lambda: win._layout_step(+1, user=False))
    win._layout_blink_timer = QTimer(win)
    win._layout_blink_timer.setInterval(500)
    win._layout_blink_timer.timeout.connect(win._layout_blink_tick)
    return w

