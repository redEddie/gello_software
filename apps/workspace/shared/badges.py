"""누를 수 있는 뱃지 -- 좁은 범위를 골라 그 안에서만 고민하게 한다.

두 화면이 같은 물건을 쓴다. 닥터의 문장 고치기(동작 · 무엇을 · 어디에)와
scene 추천의 지시문 고르기(동작별로 나눠 체크)다. 둘 다 **고를 것이 많을 때
축을 나눠 한 번에 보는 수를 줄이는** 같은 처방이라, 위젯도 하나여야 한다.

QPushButton 이 아닌 이유는 모양이다 -- 버튼은 플랫폼 테마를 따라가서 뱃지로
안 보인다.

색은 밝은 테마 기준이고 대비를 맞춰 두었다 (#49):

    기본 글 #444444 / #f2f2f2 = 8.70:1     테두리 #949494 / 흰 패널 = 3.03:1
    선택 글 #1d5c32 / #eef7f0 = 7.30:1     테두리 #2e7d46          = 5.07:1
    못 고름 #6f6f6f / #f7f7f7 = 4.69:1

뱃지 바탕(#f2f2f2)은 흰 패널과 1.05:1 이라 **테두리로 알아본다** -- 그래서
테두리가 비텍스트 3:1 을 지켜야 한다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

SEL = ("ClickableBadge{background:#eef7f0; color:#1d5c32;"
       " border:2px solid #2e7d46; border-radius:9px;}")
DEF = ("ClickableBadge{background:#f2f2f2; color:#444444;"
       " border:1px solid #949494; border-radius:9px;}")
#: 고를 수 없는 뱃지. **지우지 않고 취소선을 긋는다** -- 사라지면 "왜 없지"
#: 가 되고, 남아 있으면 툴팁이 이유를 말할 수 있다 (2026-09-07 사용자).
OFF = ("ClickableBadge{background:#f7f7f7; color:#6f6f6f;"
       " border:1px dashed #949494; border-radius:9px;}")


class ClickableBadge(QFrame):
    """``clicked`` 이 ``key`` 를 실어 보낸다.

    ``note`` 는 이름 옆에 흐리게 붙는 보조 글자다 -- 스킬의 한국어 뜻이나
    "7/9" 같은 진행 수. 이름과 같은 굵기로 두면 이름이 안 읽힌다.
    """

    clicked = pyqtSignal(str)

    def __init__(self, key: str, note: str = "", parent=None,
                 wrap: bool = False) -> None:
        super().__init__(parent)
        self.key = key
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(6)
        self._name = QLabel(key)
        self._name.setWordWrap(wrap)
        self._name.setStyleSheet(
            "border:none; font-size:11px; font-weight:bold;")
        row.addWidget(self._name)
        self._note = QLabel(note)
        self._note.setStyleSheet("border:none; font-size:11px;")
        self._note.setVisible(bool(note))
        row.addWidget(self._note)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._off = False
        self._on = False
        self.set_selected(False)

    def set_note(self, note: str) -> None:
        self._note.setText(note)
        self._note.setVisible(bool(note))

    def set_selected(self, on: bool) -> None:
        self._on = on
        self.setStyleSheet(OFF if self._off else (SEL if on else DEF))

    def set_available(self, on: bool, why: str = "") -> None:
        """못 고르는 뱃지는 취소선 + 사유 툴팁. 스타일시트의 text-decoration
        은 위젯에 따라 안 먹어서 글꼴로 긋는다."""
        self._off = not on
        self.setToolTip(why)
        for lab in (self._name, self._note):
            f = lab.font()
            f.setStrikeOut(not on)
            lab.setFont(f)
        self.setCursor(Qt.CursorShape.ArrowCursor if self._off
                       else Qt.CursorShape.PointingHandCursor)
        self.set_selected(self._on)

    def mousePressEvent(self, _e) -> None:
        if not self._off:
            self.clicked.emit(self.key)
