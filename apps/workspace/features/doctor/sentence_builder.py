"""문장 고치기 -- 자유 입력이 아니라 블럭 조립.

동작(스킬) 뱃지를 누르고 문장을 고른다 (2026-09-07 사용자 제안: "클릭해서
코딩하는 것처럼 블럭을 조립"). 자유 입력이었을 때는 고치다가 더 나쁜 문장을
넣는 것을 lint 가 **뒤에서** 막았는데, 막힌 뒤에 아는 것과 애초에 틀린 것을
못 만드는 것은 다르다.

**후보는 문법이 만든다.** ``enumerate_instructions(md, props)`` 가 이 scene
에서 가능한 문장을 전부 내놓으므로, 조립한 것이 합법인지 검사할 필요가 없다
-- 합법인 것만 화면에 있다. S016 에서 6개, 그 안에 'on' 문장은 없다 (그릇
목적지는 언제나 inside 라서 애초에 생성되지 않는다).

물체를 안 고르게 한 이유: 스킬을 고르면 남는 선택은 "어느 물체를 어디로"
뿐이고, 그것은 문장 목록 그대로다. 콤보 두 개로 나누면 조합 중 절반이
비어 있고(같은 물체끼리는 안 된다) 그 빈 조합을 다시 막아야 한다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.features.doctor.confirm import side_by_side
from gello.gui.i18n import tr

#: 스킬 -> 사람 말. 뱃지에 "pick-inside" 를 그대로 쓰면 조작자가 문법 이름을
#: 외워야 한다 -- 이름은 남기되 무슨 뜻인지 옆에 적는다.
SKILL_KO = {
    "pick-on": "집어서 위에",
    "pick-inside": "집어서 안에",
    "pick-next_to": "집어서 옆에",
    "pick-on_top_of": "집어서 위(서랍)",
    "drag-next_to": "밀어서 옆에",
    "tidy-into": "정리",
    "stack-all": "쌓기",
    "drawer-open": "서랍 열기",
    "drawer-close": "서랍 닫기",
}

_SEL = ("_SkillBadge{background:#eef7f0; color:#1d5c32;"
        " border:2px solid #2e7d46; border-radius:9px;}")
_DEF = ("_SkillBadge{background:#f2f2f2; color:#444444;"
        " border:1px solid #c9c9c9; border-radius:9px;}")


class _SkillBadge(QFrame):
    """누를 수 있는 뱃지. QPushButton 이 아닌 이유는 모양이다 -- 버튼은
    플랫폼 테마를 따라가서 뱃지로 안 보인다."""

    clicked = pyqtSignal(str)

    def __init__(self, skill: str, parent=None) -> None:
        super().__init__(parent)
        self.skill = skill
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(6)
        name = QLabel(skill)
        name.setStyleSheet("border:none; font-size:11px; font-weight:bold;")
        row.addWidget(name)
        ko = SKILL_KO.get(skill)
        if ko:
            lab = QLabel(ko)
            lab.setStyleSheet("border:none; font-size:11px;")
            row.addWidget(lab)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        self.setStyleSheet(_SEL if on else _DEF)

    def mousePressEvent(self, _e) -> None:
        self.clicked.emit(self.skill)


class SentenceDialog(QDialog):
    """options = [(skill, sentence), ...] -- 문법이 만든 것만.

    ``chosen`` 이 고른 문장 (취소면 None).
    """

    def __init__(self, parent, title: str, current: str, options: list,
                 note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("문장 고치기"))
        self.chosen = None
        self._by_skill: dict = {}
        for skill, sent in options:
            self._by_skill.setdefault(skill or "?", []).append(sent)

        col = QVBoxLayout(self)
        head = QLabel(title)
        head.setWordWrap(True)
        head.setStyleSheet("font-weight:bold;")
        col.addWidget(head)

        # 지금과 고친 뒤를 나란히 -- 모든 수정이 같은 모양을 지난다
        # (confirm.side_by_side, 2026-09-07 사용자).
        diff, self._now, self._after = side_by_side()
        self._now.setText(current)
        col.addWidget(diff)

        col.addWidget(QLabel(tr("동작")))
        self._badges = {}
        strip = QHBoxLayout()
        strip.setSpacing(6)
        for skill in sorted(self._by_skill):
            b = _SkillBadge(skill)
            b.clicked.connect(self._pick_skill)
            self._badges[skill] = b
            strip.addWidget(b)
        strip.addStretch(1)
        col.addLayout(strip)

        col.addWidget(QLabel(tr("문장")))
        self._holder = QWidget()
        self._holder_col = QVBoxLayout(self._holder)
        self._holder_col.setContentsMargins(0, 0, 0, 0)
        self._group = QButtonGroup(self)
        col.addWidget(self._holder)

        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setStyleSheet("color:#8a4b00;")
            col.addWidget(n)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        self._ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        col.addWidget(bb)

        # 지금 문장과 물체가 가장 많이 겹치는 쪽을 미리 골라 둔다 -- 'on' 을
        # 'inside' 로 바꾸는 일이 뱃지 한 번, 확인 한 번이 된다.
        self._pick_skill(self._nearest_skill(current))

    # --------------------------------------------------------------
    def _nearest_skill(self, current: str) -> str:
        words = set(current.lower().split())
        best, score = next(iter(sorted(self._by_skill))), -1
        for skill, sents in sorted(self._by_skill.items()):
            for s in sents:
                n = len(words & set(s.lower().split()))
                if n > score:
                    best, score = skill, n
        return best

    def _pick_skill(self, skill: str) -> None:
        for key, b in self._badges.items():
            b.set_selected(key == skill)
        while self._holder_col.count():
            w = self._holder_col.takeAt(0).widget()
            if w is not None:
                self._group.removeButton(w)
                w.setParent(None)          # deleteLater 는 같은 틱에 안 지워진다
        for sent in self._by_skill.get(skill, []):
            r = QRadioButton(sent)
            r.setStyleSheet("color:#333;")
            self._group.addButton(r)
            self._holder_col.addWidget(r)
        first = self._group.buttons()
        for b in first:
            b.toggled.connect(self._redraw_after)
        if first:
            first[0].setChecked(True)
        self._ok.setEnabled(bool(first))
        self._redraw_after()

    def _redraw_after(self, *_a) -> None:
        for b in self._group.buttons():
            if b.isChecked():
                self._after.setText(b.text())
                return
        self._after.setText("")

    def _accept(self) -> None:
        for b in self._group.buttons():
            if b.isChecked():
                self.chosen = b.text()
                break
        self.accept()
