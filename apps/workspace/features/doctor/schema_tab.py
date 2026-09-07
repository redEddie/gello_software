"""스키마 닥터 -- 파일에 찍힌 버전이 그 내용과 맞는가.

셋 중 이것만 **파일이 스스로에 대해 하는 주장**을 다룬다. 기록 닥터는 문장과
기록을 맞대고, 진행 닥터는 목표와 개수를 맞대는데, 여기서는 데이터세트 버전과 내용을
맞댄다.

어긋나면 조용하지 않다 -- 검증이 통째로 실패한다. S015 가 그랬다: knu-1.1.0
으로 찍혀 있는데 그 버전이 요구하는 필드가 없어 40 에피소드가 전부 실패였고,
고치는 데 손으로 짠 스크립트가 필요했다. 그때 그 사실을 볼 수 있는 자리는
터미널의 check_scene_file.py 하나였다.

**버전 통일을 권하지 않는다.** 섞여 있는 것 자체는 이미 처리된다 (변환기는
여분 필드를 허용한다). 1.2.0 을 1.0.0 으로 내리면 힘·토크 필드가 파일에
그대로인 채 버전만 그 존재를 안 알리게 되어, 버전 보고 읽는 소비자가 있는
데이터를 안 읽는다. 그래서 이 화면은 분포를 보여주고 **어긋남만** 고친다.
"""
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr


def build_schema_tab(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(6, 6, 6, 6)

    win.schema_title = QLabel(tr("데이터셋을 읽는 중..."))
    win.schema_title.setStyleSheet("font-weight:bold;")
    col.addWidget(win.schema_title)

    win.schema_tree = QTreeWidget()
    win.schema_tree.setHeaderLabels(
        [tr("Scene"), tr("에피소드"), tr("찍힘"), tr("내용"), tr("상태")])
    win.schema_tree.setRootIsDecorated(False)
    win.schema_tree.header().setSectionResizeMode(
        4, QHeaderView.ResizeMode.Stretch)
    win.schema_tree.setToolTip(tr(
        "'찍힘' 은 파일이 주장하는 버전, '내용' 은 실제로 만족하는 가장 높은 "
        "버전입니다.\n둘이 다르면 검증이 실패합니다. 줄을 누르면 무엇이 "
        "빠졌는지 오른쪽에 나옵니다."))
    win.schema_tree.itemClicked.connect(
        lambda item, _c: win.doctor.on_schema_picked(item))
    col.addWidget(win.schema_tree, 1)

    win.schema_hint = QLabel("")
    win.schema_hint.setWordWrap(True)
    win.schema_hint.setStyleSheet("color:#444;")
    col.addWidget(win.schema_hint)
    return w
