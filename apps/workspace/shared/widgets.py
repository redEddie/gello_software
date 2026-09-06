"""Small reusable widgets and helpers originally defined in collect_workspace.py."""

from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from gello.gui.fonts import MONO_STACK

# Status-dot colors used by StatusLight and similar indicators.
_DOT = {
    "ok": "#2ecc71",
    "busy": "#f39c12",
    "off": "#7f8c8d",
    "bad": "#e74c3c",
}

# '미개발' 자리표시 헬퍼(mark_todo/TODO_STYLE/TODO_MARK)는 2026-09-06 에
# 없앴다. 누를 수 없는 위젯을 상시 화면에 두지 않기로 했기 때문이다 --
# 알려주는 것은 "없다"뿐인데 자리와 시선은 실제로 쓴다. 계획은 코드 주석과
# 이슈에 남기고, 화면에는 동작하는 것만 올린다.


def _dot(state: str, text: str) -> str:
    return f'<span style="color:{_DOT[state]};">●</span> {text}'


class SceneInfoView(QWidget):
    """describe_scene 출력 표시용 — 좁은 패널에서도 잘리지 않는 반응형.

    일반 문장 줄(objects, 빈 존, 설명)은 줄바꿈으로 접고, 격자 줄(│┌…)만
    고정폭 폰트의 비줄바꿈 라벨에 넣는다. 격자 라벨은 수평 크기 정책을
    Ignored 로 두어 패널 폭을 강제하지 않는다 -- 패널이 격자보다 좁으면
    격자 오른쪽이 살짝 잘릴 뿐, 다른 입력은 전부 접근 가능하게 남는다.
    """

    _GRID_CHARS = set("│┌┬┐├┼┤└┴┘─")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setStyleSheet("color:#888; font-size: 11px;")
        self._grid = QLabel("")
        # 'monospace' 별칭은 한국어 로케일에서 CJK 모노 폰트로 풀리는데, 그
        # 폰트는 격자 선문자(│─┌)를 2칸 폭으로 그려 격자가 어긋난다.
        #
        # D2Coding 은 그 예외라 스택 맨 앞에 둔다 (2026-09-05 실측): CJK
        # 글꼴이면서도 선문자를 1칸으로 그려 격자가 맞는다. Noto Sans Mono
        # CJK KR 은 같은 자리에서 2칸이라 여전히 어긋난다 -- "CJK 는 다
        # 위험"이 아니라 글꼴마다 다르다는 뜻이다. 격자 칸에 들어가는 것은
        # 소품 ID(ASCII)뿐이라 한글 폭은 여기서는 상관없다.
        self._grid.setStyleSheet(
            f"font-family: {MONO_STACK}; color:#888; font-size: 10px;")
        # 세로는 Minimum -- 자리가 모자랄 때 줄어드는 대신 바깥 스크롤이
        # 생겨야 한다. Preferred 로 두면 카드가 여럿인 다이얼로그에서 격자
        # 지도가 위아래로 잘려 "추천된 배치를 볼 수 없는" 화면이 된다
        # (2026-09-06 실측). 가로는 그대로 Ignored -- 좁은 패널에서 격자가
        # 폭을 강제하지 않게 하는 기존 의도다.
        self._grid.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Minimum)
        tp = self._text.sizePolicy()
        tp.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        tp.setHeightForWidth(True)
        self._text.setSizePolicy(tp)
        col.addWidget(self._text)
        col.addWidget(self._grid)

    def setText(self, text: str) -> None:
        grid_lines = [ln for ln in text.splitlines()
                      if set(ln) & self._GRID_CHARS]
        text_lines = [ln for ln in text.splitlines()
                      if not (set(ln) & self._GRID_CHARS)]
        self._text.setText("\n".join(text_lines))
        self._grid.setText("\n".join(grid_lines))
        self._grid.setVisible(bool(grid_lines))
        # 줄바꿈이 없는 라벨이라 sizeHint 높이가 곧 필요한 높이다.
        self._grid.setMinimumHeight(
            self._grid.sizeHint().height() if grid_lines else 0)

    def text(self) -> str:
        return "\n".join(x for x in (self._text.text(), self._grid.text()) if x)


class StatusLight(QLabel):
    """One status-bar indicator: a colored dot plus a short label.

    마지막 값을 들고 있는다 (``state``/``value``). 메뉴가 같은 사실을 거울로
    비추는데, 정본은 여기 하나여야 하기 때문이다 -- 메뉴가 따로 상태를
    계산하면 상태바와 다른 말을 하는 순간이 온다.
    """

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label
        self.state = "off"
        self.value = "-"
        self.set("off", "-")

    def set(self, state: str, text: str) -> None:
        self.state, self.value = state, text
        # 값이 없을 때는 라벨만 남긴다. 회색 점이 이미 "모름/꺼짐"을 말하고
        # 있어 "-" 는 같은 말을 글자로 한 번 더 적는 것이고, 라벨에 붙으면
        # ("Camera -") 오히려 "있어야 할 값이 빈 것"처럼 읽힌다
        # (2026-09-06 사용자 지적. 우측 패널의 "-" 20개를 걷어낸 것과 같은 건).
        body = self._label if text.strip() in ("", "-") else f"{self._label} {text}"
        self.setText(_dot(state, body))

