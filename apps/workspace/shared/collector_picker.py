"""수집자 칸 — 이메일 수신인처럼 태그로 고른다.

수집자는 GUI 를 켤 때마다 손대는 칸인데, 지금까지는 빈 QLineEdit 하나였다.
같은 사람이 매번 자기 이름을 다시 쳤고, 오타가 나면 그 오타가 그대로
에피소드 attr 로 들어가 데이터셋 안에서 사람이 둘로 갈라졌다.

메일 클라이언트의 수신인 칸과 같은 모양으로 바꾼다 (2026-09-06 사용자 요청):

* 칸을 누르면 아는 이름이 태그로 펼쳐진다.
* 치면 태그가 걸러진다 -- ``chanwook`` 을 치면 ``jeonchanwook`` 이 남는다
  (앞부분만이 아니라 **포함**으로 찾는다. 성을 빼고 부르는 이름을 치는 것이
  실제 습관이라, 앞글자 일치로는 안 걸린다).
* 태그를 누르면 그 이름으로 확정되고 **치던 글자는 지운다** -- 태그와 입력이
  섞인 상태로 남으면 어느 쪽이 저장될지 알 수 없다.
* 처음 보는 이름은 그냥 치면 된다. 저장은 Connect 가 한다 (그때 실제로
  쓰였다는 것이 확인되므로, 오타를 쳐 보기만 한 것은 안 남는다).

기억하는 사람 수의 정본은 :data:`gello.gui.widgets.recents.COLLECTOR_MAX` 다.
칸 옆의 "max N" 도 그 값에서 만든다 -- 저장 개수와 화면 문구가 갈라질 수 없다.
"""
from __future__ import annotations

import zlib

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr
from gello.gui.widgets.recents import COLLECTOR_MAX

#: 태그 줄 하나에 몇 개까지. 좌측 패널은 가로 스크롤이 없으므로(sizing.py)
#: 넘치는 것은 다음 줄로 접는다.
_PER_ROW = 3

#: 태그 바탕색 팔레트. 어두운 창이라 **어둡게** 두고(2026-09-06 사용자),
#: 사람마다 다른 색을 준다 -- 이름을 읽기 전에 색으로 먼저 알아보게 하려는
#: 것이라, 색은 이름에서 결정론적으로 뽑는다 (같은 사람은 늘 같은 색).
#: 무작위로 주면 GUI 를 켤 때마다 달라져서 오히려 못 알아본다.
_TAG_COLORS = (
    ("#2d3f4f", "#7fb3d5"),   # 청
    ("#2f4535", "#82c99a"),   # 녹
    ("#4a3a2b", "#e0a96d"),   # 주황
    ("#42303f", "#cf9fd0"),   # 보라
    ("#4a3030", "#e08e8e"),   # 적
    ("#2b4444", "#7fcfcf"),   # 청록
    ("#3d3d2b", "#d4d47f"),   # 황
    ("#33344a", "#9aa0e0"),   # 남
    ("#3a4a2b", "#aed07f"),   # 연두
    ("#4a2b3a", "#d07fa8"),   # 자주
)


def _tag_colors(names) -> dict:
    """이름 -> (바탕, 글자). 화면에 함께 뜨는 것들끼리는 **겹치지 않는다**.

    바라는 색은 이름에서 결정론적으로 뽑는다 (crc32) -- 같은 사람은 늘 같은
    색이라야 이름을 읽기 전에 색으로 알아본다. 파이썬 ``hash()`` 를 안 쓰는
    이유는 실행마다 소금이 달라 GUI 를 다시 켜면 색이 바뀌기 때문이고,
    글자 코드의 단순 합을 안 쓰는 이유는 비슷한 이름이 한 값으로 몰리기
    때문이다 (실측: 여섯 중 넷이 한 색).

    그래도 부딪히면 다음 빈 색으로 밀어 준다 -- 나란히 놓인 두 태그가 같은
    색이면 다채롭게 만든 뜻이 없다. 팔레트가 모자라면 그때는 겹친다.
    """
    out: dict = {}
    used: set = set()
    for name in names:
        want = zlib.crc32(name.encode("utf-8")) % len(_TAG_COLORS)
        for step in range(len(_TAG_COLORS)):
            i = (want + step) % len(_TAG_COLORS)
            if i not in used:
                break
        used.add(i)
        out[name] = _TAG_COLORS[i]
    return out


def _tag_style(bg: str, fg: str) -> str:
    return (f"QPushButton{{background:{bg}; color:{fg};"
            f" border:1px solid {fg}44; border-radius:9px;"
            " padding:2px 9px;}"
            f"QPushButton:hover{{border-color:{fg};}}")


class CollectorPicker(QWidget):
    """``recents["collector"]`` 를 태그로 보여주는 이름 칸."""

    #: 이름이 확정됐을 때 (태그 클릭 또는 입력 끝).
    picked = pyqtSignal(str)

    def __init__(self, recents, key: str = "collector") -> None:
        super().__init__()
        self._recents = recents
        self._key = key
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(recents.most_recent(key, ""))
        self.edit.setPlaceholderText(tr("수집자 식별자 (예: gibeom)"))
        self.edit.textEdited.connect(self._on_typed)
        self.edit.editingFinished.connect(
            lambda: self.picked.emit(self.edit.text().strip()))
        # 칸을 누르면 태그가 펼쳐진다 -- 평소에는 접혀 있어 자리를 안 먹는다.
        self.edit.installEventFilter(self)
        row.addWidget(self.edit, 1)
        cap = QLabel(tr("max {n}").format(n=COLLECTOR_MAX))
        cap.setStyleSheet("color:#888; font-size:10px;")
        cap.setToolTip(tr("최근 수집자 {n}명까지 기억합니다. 그보다 오래된 "
                          "이름은 자동으로 지워집니다.").format(n=COLLECTOR_MAX))
        row.addWidget(cap)
        col.addLayout(row)

        self._tag_box = QWidget()
        self._tag_col = QVBoxLayout(self._tag_box)
        self._tag_col.setContentsMargins(0, 0, 0, 0)
        self._tag_col.setSpacing(2)
        self._tag_box.setVisible(False)
        col.addWidget(self._tag_box)
        self.refresh()

    # ------------------------------------------------------------------ API
    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802 - QLineEdit 과 같은 이름
        self.edit.setText(value)

    def setEnabled(self, on: bool) -> None:  # noqa: N802 - Qt override
        super().setEnabled(on)
        if not on:
            self._tag_box.setVisible(False)

    def refresh(self) -> None:
        """저장된 이름 목록을 다시 읽어 태그를 만든다."""
        self._names = self._recents.get(self._key)[:COLLECTOR_MAX]
        self._rebuild(self.edit.text().strip())

    # -------------------------------------------------------------- 내부
    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        from PyQt6.QtCore import QEvent

        if obj is self.edit and event.type() == QEvent.Type.FocusIn:
            self._rebuild(self.edit.text().strip())
        return False

    def _on_typed(self, text: str) -> None:
        self._rebuild(text.strip())

    def _clear(self) -> None:
        """옛 태그를 **즉시** 떼어 낸다.

        deleteLater() 만으로는 안 된다 -- 그것은 이벤트 루프가 돌 때 지우므로,
        같은 틱 안에서 다시 그리면 옛 버튼이 아직 자식으로 남아 두 번 보인다
        (실측: 'chanwook' 을 치자 jeonchanwook 이 두 개). setParent(None) 이
        부모에서 그 자리에서 떼어 내고, 참조가 없으니 파이썬이 정리한다.
        """
        while self._tag_col.count():
            item = self._tag_col.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().setParent(None)
                lay.deleteLater()
            elif item.widget() is not None:
                item.widget().setParent(None)

    def _rebuild(self, typed: str) -> None:
        self._clear()
        # 친 글자가 **포함된** 이름만 남긴다 (chanwook -> jeonchanwook).
        low = typed.lower()
        shown = [n for n in self._names if not low or low in n.lower()]
        self._tag_box.setVisible(bool(shown))
        if not shown:
            return
        colors = _tag_colors(shown)
        row = None
        for i, name in enumerate(shown):
            if i % _PER_ROW == 0:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                self._tag_col.addLayout(row)
            btn = QPushButton(name)
            btn.setToolTip(tr("이 이름으로 확정합니다 (치던 글자는 지워집니다)"))
            btn.setSizePolicy(QSizePolicy.Policy.Maximum,
                              QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(_tag_style(*colors[name]))
            btn.clicked.connect(lambda _c=False, n=name: self._pick(n))
            row.addWidget(btn)
        row.addStretch(1)

    def _pick(self, name: str) -> None:
        """태그 클릭 = 확정. 치던 글자는 버린다 (둘이 섞이면 안 된다)."""
        self.edit.setText(name)
        self._rebuild(name)
        self.picked.emit(name)
