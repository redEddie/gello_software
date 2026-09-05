"""Settings page builder for WorkspaceWindow."""
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from gello.gui.i18n import tr



def build_settings(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)
    # 언어 전환 버튼은 없앴다 (2026-09-05). 반쪽만 동작했고 아무도 찾지
    # 않았다 -- 이제 문자열마다 언어가 고정이다 (gello/gui/i18n.py 참고).
    layout_btn = QPushButton(tr("카메라 레이아웃 확인 (LIBERO 초기 배치와 비교)"))
    layout_btn.setToolTip(tr(
        "LIBERO 초기 배치 이미지와 현재 카메라를 50% 투명도로 겹쳐 보여줍니다."))
    layout_btn.clicked.connect(
        lambda: win.center_tabs.setCurrentIndex(win._layout_tab_index))
    col.addWidget(layout_btn)
    schema = QPushButton(tr("데이터셋 구조 사용자 설정..."))
    schema.setToolTip(tr("Action 구조는 고정입니다. Observation 필드만 고를 수 "
                         "있습니다."))
    schema.clicked.connect(win._on_schema)
    col.addWidget(schema)
    win.schema_label = QLabel("")
    win.schema_label.setStyleSheet("color:#888;")
    win.schema_label.setWordWrap(True)
    col.addWidget(win.schema_label)
    win._refresh_schema_label()
    col.addStretch()
    return w

