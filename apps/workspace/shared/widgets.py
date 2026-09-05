"""Small reusable widgets and helpers originally defined in collect_workspace.py."""

from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from gello.gui.fonts import MONO_STACK
from gello.gui.constants import TODO_MARK
from gello.gui.i18n import tr

# Status-dot colors used by StatusLight and similar indicators.
_DOT = {
    "ok": "#2ecc71",
    "busy": "#f39c12",
    "off": "#7f8c8d",
    "bad": "#e74c3c",
}

# Style applied to disabled "not yet implemented" placeholders.
TODO_STYLE = "color:#6b6b6b; font-style:italic;"


def mark_todo(widget: QWidget, note: str = "") -> QWidget:
    """Disable a widget and mark it as not-yet-implemented."""
    widget.setEnabled(False)
    widget.setStyleSheet(TODO_STYLE)
    widget.setToolTip(f"{TODO_MARK}: " + (note or tr("아직 구현되지 않은 기능입니다.")))
    return widget


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
        self._grid.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
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

    def text(self) -> str:
        return "\n".join(x for x in (self._text.text(), self._grid.text()) if x)


class StatusLight(QLabel):
    """One status-bar indicator: a colored dot plus a short label."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label
        self.set("off", "-")

    def set(self, state: str, text: str) -> None:
        self.setText(_dot(state, f"{self._label} {text}"))
