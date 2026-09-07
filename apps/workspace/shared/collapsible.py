"""여닫히는 상자 -- 제목을 누르면 접힌다.

우측 패널은 세로로 길어진다. 배치도 하나, 진단 목록 하나만 있어도 화면
아래가 잘리는데, 그 순간 조작자는 **지금 안 보는 상자** 때문에 스크롤한다.
접어 두면 제목 줄만 남아 다음 상자가 올라온다.

QGroupBox 를 그대로 쓰지 않는 이유: QGroupBox 의 checkable 은 체크박스가
붙고 그것이 "이 구획을 켠다/끈다" 로 읽힌다 -- 여기서는 **보이기만** 접는
것이지 기능을 끄는 것이 아니다. 그래서 제목 줄을 직접 그린다.

접힘 상태는 **세션 안에서만** 기억한다. 파일에 남기면 조작자가 접어 둔 것을
다음 사람이 못 찾는데, 우측 패널은 "지금 이 화면에서 할 수 있는 일" 이라
안 보이면 없는 것과 같다. 창을 다시 열면 전부 펴진 상태로 시작한다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

_HEAD = ("QLabel{color:#333; font-weight:bold; padding:2px 0;}")
#: 접힘/펼침 표시. 삼각형 하나로 상태와 조작을 함께 말한다.
_OPEN, _SHUT = "▾", "▸"


class _Header(QLabel):
    def __init__(self, box: "CollapsibleBox") -> None:
        super().__init__()
        self._box = box
        self.setStyleSheet(_HEAD)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, _e) -> None:
        self._box.set_open(not self._box.is_open())


class CollapsibleBox(QFrame):
    """``body`` 레이아웃에 내용을 넣는다. 제목을 누르면 접힌다."""

    def __init__(self, title: str, parent=None, open_: bool = True) -> None:
        super().__init__(parent)
        self._title = title
        self.setStyleSheet(
            "CollapsibleBox{border:1px solid #c8c8c8; border-radius:4px;}")
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 4, 6, 6)
        col.setSpacing(4)
        self._head = _Header(self)
        col.addWidget(self._head)
        self._inner = QWidget()
        self.body = QVBoxLayout(self._inner)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(4)
        col.addWidget(self._inner)
        self.set_open(open_)

    def is_open(self) -> bool:
        return not self._inner.isHidden()

    def set_open(self, on: bool) -> None:
        self._inner.setVisible(on)
        self._head.setText(f"{_OPEN if on else _SHUT} {self._title}")

    def set_title(self, title: str) -> None:
        self._title = title
        self.set_open(self.is_open())
