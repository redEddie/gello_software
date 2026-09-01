"""Settings page builder for WorkspaceWindow."""
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from gello.gui.constants import TODO_MARK
from gello.gui.i18n import tr

from apps.dialogs._widgets import mark_todo


def build_settings(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)
    # 언어 전환은 반쪽만 동작한다. tr() 은 위젯을 만들 때 한 번 호출되고
    # 그 문자열이 박히므로, 전역 언어를 바꿔도 이미 만들어진 창은 그대로다
    # -- 전환 뒤 새로 여는 다이얼로그만 바뀌어서 한국어와 영어가 섞인다.
    # 제대로 하려면 모든 위젯에 retranslate 경로가 필요하다. 그때까지는
    # 반쯤 되는 채로 두는 것보다 미개발로 못 박아두는 쪽이 낫다.
    lang = QPushButton(f'{tr("언어 전환 (한국어 / English)")} ({TODO_MARK})')
    col.addWidget(mark_todo(lang, tr(
        "이미 열린 창은 다시 그려지지 않아 한국어와 영어가 섞입니다.")))
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

