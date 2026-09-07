"""문장 고치기 -- 자유 입력이 아니라 블럭 조립.

동작(스킬) 뱃지를 누르고 문장을 고른다 (2026-09-07 사용자 제안: "클릭해서
코딩하는 것처럼 블럭을 조립"). 자유 입력이었을 때는 고치다가 더 나쁜 문장을
넣는 것을 lint 가 **뒤에서** 막았는데, 막힌 뒤에 아는 것과 애초에 틀린 것을
못 만드는 것은 다르다.

**후보는 문법이 만든다.** ``enumerate_instructions(md, props)`` 가 이 scene
에서 가능한 문장을 전부 내놓으므로, 조립한 것이 합법인지 검사할 필요가 없다
-- 합법인 것만 화면에 있다. S016 에서 6개, 그 안에 'on' 문장은 없다 (그릇
목적지는 언제나 inside 라서 애초에 생성되지 않는다).

**블럭은 셋이다: 동작 · 무엇을 · 어디에.**

처음에는 "무엇을 → 어디에" 를 한 뱃지에 묶었는데, 그러면 물체 조합 수만큼
뱃지가 생겨서 (소품 4개면 12장) 문장 목록을 뱃지로 옮겨 적은 것일 뿐이었다
-- 고르는 일이 줄지 않는다 (2026-09-07 사용자). 축을 나누면 4 + 4 장이다.

**고른 동작에 맞는 뱃지만 켜진다.** 동작을 고르면 그 동작으로 실제 만들 수
있는 짝만 남고(같은 물체끼리 등 못 만드는 조합은 애초에 없다), 지금 지시문의
물체가 그 안에 있으면 그것이 골라져 있다 -- 'on' 을 'inside' 로 바꾸는 일이
동작 뱃지 한 번이 된다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
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
#: 고를 수 없는 뱃지. **지우지 않고 취소선을 긋는다** (2026-09-07 사용자).
#: 사라지면 "왜 없지" 가 되고, 남아 있으면 툴팁이 이유를 말할 수 있다.
_OFF = ("_SkillBadge{background:#f7f7f7; color:#8a8a8a;"
        " border:1px dashed #d5d5d5; border-radius:9px;}")


def _badge_row(col, title: str):
    """제목 한 줄 + 뱃지가 놓일 가로줄. (레이아웃, 뱃지 dict)."""
    col.addWidget(QLabel(title))
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    col.addWidget(holder)
    return row, {}


def _fill(row, store: dict, names: list, on_click) -> None:
    """뱃지 줄을 다시 그린다. 지운 위젯은 즉시 부모에서 떼어 낸다 --
    deleteLater 는 같은 틱에 안 지워져서 옛 뱃지가 겹쳐 보인다."""
    while row.count():
        w = row.takeAt(0).widget()
        if w is not None:
            w.setParent(None)
    store.clear()
    for name in names:
        b = _SkillBadge(name, wrap=True)
        b.clicked.connect(lambda _n, v=name: on_click(v))
        store[name] = b
        row.addWidget(b)
    row.addStretch(1)


def sense_key(sentence: str) -> tuple:
    """문장을 **뜻**으로 만든 열쇠 -- (동작, 무엇을, 어디에), 물체는 (색, 종류).

    글자로 대조하면 안 된다. 2026-09-07 에 형용사 어순을 고치면서
    "the blue small bowl"(옛 수집분)과 "the small blue bowl"(지금 생성)이
    같은 물체를 가리키는 다른 글자가 됐다 -- 글자로 비교하면 이미 쓰이는
    문장을 "안 쓰인다" 고 판정해서, 같은 뜻의 지시문이 둘 생긴다.
    """
    from gello.scene.instruction_grammar import _parse_object_phrase, skill_of

    src, dst = _split(sentence)
    return (skill_of(sentence),
            _parse_object_phrase(src), _parse_object_phrase(dst))


def _split(sentence: str) -> "tuple[str, str]":
    """문장 -> (무엇을, 어디에). 정본 문법의 두 꼴만 다룬다."""
    t = sentence.strip()
    for head, mid in (("pick up ", " and place it "), ("drag ", " next to ")):
        if t.startswith(head) and mid in t:
            src, _sep, dst = t[len(head):].partition(mid)
            if mid.endswith("it "):
                for rel in ("on top of ", "next to ", "inside ", "on "):
                    if dst.startswith(rel):
                        dst = dst[len(rel):]
                        break
            return src, dst
    return t, ""


def _pair_label(sentence: str) -> str:
    """문장에서 "무엇을 → 어디에" 만 뽑는다.

    문장 전체를 뱃지에 넣으면 동작 부분이 뱃지마다 똑같이 반복돼, 정작
    다른 부분(물체)이 눈에 안 띈다. 정본 문법의 두 꼴만 다루면 된다.
    """
    s = sentence.strip()
    for head, mid in (("pick up ", " and place it "), ("drag ", " next to ")):
        if s.startswith(head) and mid in s:
            src, _sep, dst = s[len(head):].partition(mid)
            if mid.endswith("it "):
                # 관계어를 뗀다. 긴 것부터 -- "on top of" 가 "on" 에
                # 잡아먹히면 "top of the drawer" 가 남는다.
                for rel in ("on top of ", "next to ", "inside ", "on "):
                    if dst.startswith(rel):
                        dst = dst[len(rel):]
                        break
            return f"{src}\n→ {dst}"
    return s


class _SkillBadge(QFrame):
    """누를 수 있는 뱃지. QPushButton 이 아닌 이유는 모양이다 -- 버튼은
    플랫폼 테마를 따라가서 뱃지로 안 보인다."""

    clicked = pyqtSignal(str)

    def __init__(self, skill: str, parent=None, wrap: bool = False) -> None:
        super().__init__(parent)
        self.skill = skill
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(6)
        name = QLabel(skill)
        name.setWordWrap(wrap)
        name.setStyleSheet("border:none; font-size:11px; font-weight:bold;")
        row.addWidget(name)
        ko = None if wrap else SKILL_KO.get(skill)
        if ko:
            lab = QLabel(ko)
            lab.setStyleSheet("border:none; font-size:11px;")
            row.addWidget(lab)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._off = False
        self._on = False
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        self._on = on
        self.setStyleSheet(_OFF if self._off else (_SEL if on else _DEF))

    def set_available(self, on: bool, why: str = "") -> None:
        """못 고르는 뱃지는 취소선 + 사유 툴팁. 스타일시트의
        text-decoration 은 위젯에 따라 안 먹어서 글꼴로 긋는다."""
        self._off = not on
        self.setToolTip(why)
        for lab in self.findChildren(QLabel):
            f = lab.font()
            f.setStrikeOut(not on)
            lab.setFont(f)
        self.setCursor(Qt.CursorShape.ArrowCursor if self._off
                       else Qt.CursorShape.PointingHandCursor)
        self.set_selected(self._on)

    def mousePressEvent(self, _e) -> None:
        if not self._off:
            self.clicked.emit(self.skill)


class SentenceDialog(QDialog):
    """options = [(skill, sentence), ...] -- 문법이 만든 것만.

    ``chosen`` 이 고른 문장 (취소면 None).
    """

    def __init__(self, parent, title: str, current: str, options: list,
                 note: str = "", used_by: "dict | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("문장 고치기"))
        self.chosen = None
        self._current = current
        # skill -> src -> dst -> 문장. 문법이 만든 것만 들어오므로 여기
        # 있는 조합은 전부 합법이다.
        self._index: dict = {}
        for skill, sent in options:
            src, dst = _split(sent)
            self._index.setdefault(skill or "?", {}).setdefault(
                src, {})[dst] = sent
        self._cur_src, self._cur_dst = _split(current)
        # {문장: 그것을 쓰는 다른 지시문 id}. 후보에서 빼지 않는다 -- 뱃지는
        # 남기고 취소선을 그어 이유를 툴팁으로 말한다.
        # 뜻으로 대조한다 (sense_key) -- 글자로 하면 어순이 다른 같은 뜻을
        # 놓친다.
        self._used_by = {sense_key(t): i for t, i in (used_by or {}).items()}

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
        for skill in sorted(self._index):
            b = _SkillBadge(skill)
            b.clicked.connect(self._pick_skill)
            self._badges[skill] = b
            strip.addWidget(b)
        strip.addStretch(1)
        col.addLayout(strip)

        self._src_row, self._src_badges = _badge_row(col, tr("무엇을"))
        self._dst_row, self._dst_badges = _badge_row(col, tr("어디에"))

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
        """지금 문장과 물체가 가장 많이 겹치는 동작 -- 미리 골라 둔다."""
        words = set(current.lower().split())
        best, score = next(iter(sorted(self._index))), -1
        for skill, by_src in sorted(self._index.items()):
            for dsts in by_src.values():
                for sent in dsts.values():
                    n = len(words & set(sent.lower().split()))
                    if n > score:
                        best, score = skill, n
        return best

    def _pick_skill(self, skill: str) -> None:
        for key, b in self._badges.items():
            b.set_selected(key == skill)
        self._skill = skill
        by_src = self._index.get(skill, {})
        # 줄은 **그 동작의 전량**이다. 고를 수 없는 것도 자리를 지킨다 --
        # 사라지면 "왜 없지" 가 되고, 남아 있으면 이유를 말할 수 있다.
        self._all_src = sorted(by_src)
        self._all_dst = sorted({d for dsts in by_src.values() for d in dsts})
        _fill(self._src_row, self._src_badges, self._all_src, self._pick_src)
        _fill(self._dst_row, self._dst_badges, self._all_dst, self._pick_dst)
        for src, b in self._src_badges.items():
            free = [d for d, sent in by_src.get(src, {}).items()
                    if sense_key(sent) not in self._used_by]
            b.set_available(bool(free), "" if free else tr(
                "이 물체로 만들 수 있는 문장은 이미 다 쓰이고 있습니다"))
        # 지금 지시문의 물체가 이 동작으로도 고를 수 있으면 그것을 켠다.
        src = self._cur_src
        if src not in self._src_badges or self._src_badges[src]._off:
            src = next((x for x in self._all_src
                        if not self._src_badges[x]._off),
                       self._all_src[0] if self._all_src else "")
        self._pick_src(src)

    def _pick_src(self, src: str) -> None:
        self._src = src
        for key, b in self._src_badges.items():
            b.set_selected(key == src)
        dsts = self._index.get(self._skill, {}).get(src, {})
        pick = ""
        for dst, b in self._dst_badges.items():
            sent = dsts.get(dst)
            if sent is None:
                b.set_available(False, tr("같은 물체끼리는 만들 수 없습니다"))
                continue
            owner = self._used_by.get(sense_key(sent))
            b.set_available(owner is None, "" if owner is None else tr(
                "{iid} 가 이미 쓰는 문장입니다").format(iid=owner))
            if owner is None and (not pick or dst == self._cur_dst):
                pick = dst
        self._pick_dst(pick)

    def _pick_dst(self, dst: str) -> None:
        for key, b in self._dst_badges.items():
            b.set_selected(key == dst)
        sent = self._index.get(self._skill, {}).get(self._src, {}).get(dst, "")
        if sent and sense_key(sent) in self._used_by:
            sent = ""
        self._chosen_sentence = sent
        self._after.setText(sent or tr("(고를 수 있는 조합이 없습니다)"))
        self._ok.setEnabled(bool(sent))

    def _accept(self) -> None:
        self.chosen = self._chosen_sentence
        self.accept()
