"""지시문 교환 대화상자 -- 바꾸기 전과 바꾼 뒤를 나란히 보여준다.

목록에서 한 줄 고르는 방식으로 먼저 만들었다가 버렸다 (2026-09-07 사용자:
"무슨 지시문으로 바꾸는지 안 보이게 하는 UI 가 너무 불편하다"). 교환은
**두 줄이 동시에 바뀌는** 조작인데 목록 한 줄은 그중 하나도 제대로 안 보여
준다 -- 고르는 순간 무엇이 어디로 가는지가 화면에 없다.

그래서 상대를 고르면 아래의 '바꾼 뒤' 가 즉시 다시 그려진다. 누르기 전에
결과를 읽을 수 있어야 한다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QVBoxLayout,
)

from apps.workspace.shared.sizing import roomy
from gello.gui.i18n import tr

#: 문장 라벨의 공통 모양. 밝은 바탕에서 본문은 #333 (대비 12:1).
_SENT = "color:#333; padding:2px 4px;"
_BEFORE = "QFrame{background:#f2f2f2; border:1px solid #cccccc; border-radius:4px;}"
_AFTER = "QFrame{background:#eef7f0; border:1px solid #2e7d46; border-radius:4px;}"


def _box(style: str) -> "tuple[QFrame, QVBoxLayout]":
    f = QFrame()
    f.setStyleSheet(style)
    v = QVBoxLayout(f)
    v.setContentsMargins(8, 6, 8, 6)
    v.setSpacing(2)
    return f, v


def _line(parent: QVBoxLayout, head: str, text: str) -> "tuple[QLabel, QLabel]":
    cap = QLabel(head)
    cap.setStyleSheet("color:#555; font-size:11px; border:none;")
    parent.addWidget(cap)
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(_SENT + " border:none;")
    parent.addWidget(lab)
    return cap, lab


class SwapDialog(QDialog):
    """(내 id, 내 문장, 내 개수) 와 후보 [(id, 개수, 문장), ...].

    ``chosen`` 이 고른 상대의 instruction_id (취소면 None).
    """

    def __init__(self, parent, mine: tuple, others: list) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("지시문 교환"))
        self._mine = mine
        self._others = others
        self.chosen = None

        col = QVBoxLayout(self)
        iid, text, n = mine

        col.addWidget(QLabel(tr(
            "두 지시문의 문장을 맞바꿉니다. 두 줄이 동시에 바뀝니다.")))

        before, bv = _box(_BEFORE)
        _line(bv, tr("지금 {iid} — 에피소드 {n}").format(iid=iid, n=n), text)
        col.addWidget(before)

        col.addWidget(QLabel(tr("바꿀 상대")))
        self.combo = QComboBox()
        for oid, cnt, otext in others:
            self.combo.addItem(
                tr("{oid} — 에피소드 {n} — {t}").format(
                    oid=oid, n=cnt, t=otext), oid)
        roomy(self.combo)
        self.combo.currentIndexChanged.connect(self._redraw)
        col.addWidget(self.combo)

        after, av = _box(_AFTER)
        self._a_cap, self._a = _line(av, "", "")
        self._b_cap, self._b = _line(av, "", "")
        col.addWidget(after)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#8a4b00;")
        col.addWidget(self.note)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        col.addWidget(bb)
        self._redraw()

    def _current(self) -> tuple:
        i = max(0, self.combo.currentIndex())
        return self._others[i]

    def _redraw(self, *_a) -> None:
        iid, text, n = self._mine
        oid, cnt, otext = self._current()
        # 바꾼 뒤: 내 줄은 상대 문장을, 상대 줄은 내 문장을 갖는다.
        self._a.setText(otext)
        self._b.setText(text)
        self._a_cap.setText(tr("바꾼 뒤 {iid} — 에피소드 {n}")
                            .format(iid=iid, n=n))
        self._b_cap.setText(tr("바꾼 뒤 {oid} — 에피소드 {n}")
                            .format(oid=oid, n=cnt))
        moved = n + cnt
        self.note.setText(tr(
            "에피소드 {n}개의 문장이 바뀝니다 — 이미 Hub 에 올린 "
            "데이터셋이라면 전체 재빌드·재푸시가 필요해집니다.").format(n=moved)
            if moved else tr(
            "둘 다 안 찍은 빈 칸이라 계획 파일만 바뀝니다."))

    def _accept(self) -> None:
        self.chosen = self._current()[0]
        self.accept()
