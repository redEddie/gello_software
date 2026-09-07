"""수정 확정 화면 -- 지금과 고친 뒤를 나란히 놓고 누른다.

데이터세트를 고치는 조작은 전부 이 모양을 지난다 (2026-09-07 사용자:
"현재와 수정후 결과를 나란히 두고 본 뒤에 확정하도록"). 전에는 조작마다
달랐다 -- 교환만 전후를 그렸고, 물체 정정은 확인 문구 한 줄이었고, 문장
고치기는 고른 것만 보였다. 무엇이 어떻게 되는지 예상할 수 없는 버튼이
데이터세트를 고치는 자리에 있으면 안 된다.

왼쪽이 지금, 오른쪽이 고친 뒤다. 오른쪽만 초록으로 두어 어느 쪽이 결과인지
색으로도 구분된다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr

_NOW = "QFrame{background:#f2f2f2; border:1px solid #cccccc; border-radius:4px;}"
_AFTER = "QFrame{background:#eef7f0; border:1px solid #2e7d46; border-radius:4px;}"
_MONO = "border:none; color:#333; font-family:monospace; font-size:11px;"
_TEXT = "border:none; color:#333;"


def pane(head: str, after: bool, mono: bool = False) -> "tuple[QFrame, QLabel]":
    """(상자, 내용 라벨). 내용은 호출자가 setText 로 채우고 다시 채운다."""
    f = QFrame()
    f.setStyleSheet(_AFTER if after else _NOW)
    col = QVBoxLayout(f)
    col.setContentsMargins(8, 6, 8, 6)
    col.setSpacing(2)
    cap = QLabel(head)
    cap.setStyleSheet("border:none; color:#555; font-size:11px;")
    col.addWidget(cap)
    body = QLabel("")
    body.setWordWrap(not mono)
    body.setStyleSheet(_MONO if mono else _TEXT)
    col.addWidget(body)
    col.addStretch(1)
    return f, body


def side_by_side(mono: bool = False) -> "tuple[QWidget, QLabel, QLabel]":
    """(위젯, 지금, 고친 뒤). 두 칸을 같은 폭으로 나눈다."""
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    left, now = pane(tr("지금"), False, mono)
    right, after = pane(tr("고친 뒤"), True, mono)
    row.addWidget(left, 1)
    row.addWidget(right, 1)
    return w, now, after


class ConfirmDialog(QDialog):
    """머리글 + (호출자가 넣는 본문) + 영향 문구 + 확인/취소.

    본문은 조작마다 다르다 -- 물체는 배치 그림, 문장은 문장. 공통은 "왼쪽이
    지금, 오른쪽이 고친 뒤, 그리고 무엇이 얼마나 바뀌는지" 다.
    """

    def __init__(self, parent, title: str, body: QWidget,
                 ok_text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        col = QVBoxLayout(self)
        head = QLabel(title)
        head.setWordWrap(True)
        head.setStyleSheet("font-weight:bold;")
        col.addWidget(head)
        col.addWidget(body, 1)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#8a4b00;")
        col.addWidget(self.note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        if ok_text:
            self.buttons.button(
                QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        col.addWidget(self.buttons)

    def set_note(self, text: str) -> None:
        self.note.setText(text)

    def set_ok_enabled(self, on: bool) -> None:
        self.buttons.button(
            QDialogButtonBox.StandardButton.Ok).setEnabled(on)
