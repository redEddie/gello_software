"""Leader/follower delta bar widget."""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


class DeltaBar(QWidget):
    """One joint's leader/follower delta, colored green (OK) / red (out of gate)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        self.name_label = QLabel(name)
        self.name_label.setFixedWidth(32)
        self._color: str | None = None   # 마지막으로 적용한 chunk 색 (아래 참고)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%v")
        layout.addWidget(self.name_label)
        layout.addWidget(self.bar)

    def update_delta(self, delta: float, threshold: float) -> None:
        pct = int(min(100, abs(delta) / threshold * 100))
        self.bar.setValue(pct)
        self.bar.setFormat(f"{delta:+.3f} rad")
        color = "#2ecc71" if abs(delta) <= threshold else "#e74c3c"
        # 색이 실제로 바뀔 때만 스타일시트를 건드린다. setStyleSheet 은 Qt 가
        # 위젯 스타일을 통째로 다시 파싱·재적용하게 만드는 호출이라, 매 갱신마다
        # 부륍면 바 7개 x 50 Hz = 초당 350 회가 GUI 스레드를 잡는다 -- 게이지
        # 갱신이 더디게 따라오던 이유다 (2026-09-01).
        if color != self._color:
            self._color = color
            self.bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {color}; }}"
            )
