"""Small QPainter plot widgets — no plotting dependency.

pyqtgraph/matplotlib/QtCharts are all absent from this venv, and what these
panels need is a line chart, a bar strip and a histogram. Painting them
directly keeps the install as it is and stays fast enough to redraw on every
playback tick.

Colors come from the widget palette rather than fixed hex, so the panels
follow whatever theme the app is running under.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

# LeRobot 뷰어와 같은 규약: 실측은 실선, 명령은 점선. 액션은 명령과 거의 겹치므로
# (리더 명령 액션 공간) 점선보다 촘촘한 파선으로 구분한다.
SERIES_STYLES = {
    "state": (Qt.PenStyle.SolidLine, 2.0, "observation.state"),
    "commanded": (Qt.PenStyle.DashLine, 1.6, "observation.commanded_state"),
    "action": (Qt.PenStyle.DotLine, 1.6, "action"),
}
JOINT_COLORS = ["#4a9eff", "#ff8c42", "#43c59e", "#e05c8a",
                "#b07fdb", "#e0c341", "#5ec9d8", "#9aa0a6"]


def _fmt(v: float) -> str:
    a = abs(v)
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


class SeriesPlot(QWidget):
    """Time series for one or two dimensions, three aligned series each.

    Draws state / commanded / action on a shared time axis, which is what makes
    the action-space question answerable by eye: with the leader-command
    convention `action` should sit on top of `commanded` and *lead* `state`.
    """

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.title = title
        self._dims: list[tuple[int, str]] = []
        self._series: dict = {}
        self._cursor: float | None = None
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, series: dict, dims: list) -> None:
        """`series` is {"state"|"commanded"|"action": (T, D)}; `dims` a list of
        (column index, label)."""
        # SERIES_STYLES에 있는 키만 받는다. load_series는 프레임 수("n")도 함께
        # 돌려주는데, 그것까지 계열로 잡으면 정수를 배열처럼 슬라이스하게 된다.
        self._series = {k: v for k, v in series.items()
                        if k in SERIES_STYLES and v is not None}
        self._dims = dims
        self.update()

    def set_cursor(self, frame: int | None) -> None:
        self._cursor = frame
        self.update()

    def clear(self) -> None:
        self._series, self._dims = {}, []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        fg = pal.windowText().color()
        grid = QColor(fg)
        grid.setAlpha(38)
        p.fillRect(self.rect(), pal.base())

        m_l, m_r, m_t, m_b = 46, 8, 20, 18
        plot = QRectF(m_l, m_t, max(1, self.width() - m_l - m_r),
                      max(1, self.height() - m_t - m_b))
        f = QFont(); f.setPointSize(8)
        p.setFont(f)
        p.setPen(QPen(QColor(fg), 1))
        p.drawText(QRectF(4, 2, self.width() - 8, 16),
                   Qt.AlignmentFlag.AlignLeft, self.title)

        if not self._series or not self._dims:
            p.setPen(QPen(grid, 1))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "데이터 없음")
            return

        cols = [i for i, _ in self._dims]
        vals = [arr[:, cols] for arr in self._series.values()]
        lo = float(min(v.min() for v in vals))
        hi = float(max(v.max() for v in vals))
        if hi - lo < 1e-9:
            lo, hi = lo - 0.5, hi + 0.5
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        n = max(1, max(arr.shape[0] for arr in self._series.values()))

        def X(i: float) -> float:
            return plot.left() + plot.width() * (i / max(1, n - 1))

        def Y(v: float) -> float:
            return plot.bottom() - plot.height() * ((v - lo) / (hi - lo))

        p.setPen(QPen(grid, 1))
        for k in range(4):
            y = plot.top() + plot.height() * k / 3
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.drawText(QRectF(0, y - 7, m_l - 5, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _fmt(hi - (hi - lo) * k / 3))
        p.drawText(QRectF(plot.left(), plot.bottom() + 2, 40, 14),
                   Qt.AlignmentFlag.AlignLeft, "0.0s")
        p.drawText(QRectF(plot.right() - 44, plot.bottom() + 2, 44, 14),
                   Qt.AlignmentFlag.AlignRight, f"{n / 20:.1f}s")

        # 점이 픽셀보다 많으면 화면 열마다 min/max 두 점만 찍는다. 250프레임짜리를
        # 매 재생 틱마다 다시 그리므로, 안 그러면 커서 이동이 눈에 띄게 느려진다.
        step = max(1, int(n / max(1.0, plot.width())))
        for key, arr in self._series.items():
            style, width, _ = SERIES_STYLES.get(key, (Qt.PenStyle.SolidLine, 1.5, key))
            for col, _label in self._dims:
                color = QColor(JOINT_COLORS[col % len(JOINT_COLORS)])
                p.setPen(QPen(color, width, style))
                pts = []
                for i in range(0, arr.shape[0], step):
                    chunk = arr[i:i + step, col]
                    pts.append(QPointF(X(i), Y(float(chunk[0]))))
                    if step > 1 and len(chunk) > 1:
                        pts.append(QPointF(X(i), Y(float(chunk.max()))))
                        pts.append(QPointF(X(i), Y(float(chunk.min()))))
                if len(pts) > 1:
                    p.drawPolyline(*pts)

        if self._cursor is not None and 0 <= self._cursor < n:
            c = QColor(fg); c.setAlpha(140)
            p.setPen(QPen(c, 1))
            x = X(self._cursor)
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # 범례는 제목 줄의 오른쪽 끝에서 왼쪽으로 채운다. 왼쪽부터 그리면
        # 제목이 길 때 그 위에 겹쳐 쓴다.
        x = self.width() - 6
        for col, label in reversed(self._dims):
            w = p.fontMetrics().horizontalAdvance(label)
            x -= w
            p.setPen(QPen(QColor(fg), 1))
            p.drawText(QRectF(x, 2, w + 2, 14), Qt.AlignmentFlag.AlignLeft, label)
            x -= 20
            p.setPen(QPen(QColor(JOINT_COLORS[col % len(JOINT_COLORS)]), 2.5))
            p.drawLine(QPointF(x + 2, 9), QPointF(x + 16, 9))
            x -= 8


class BarStrip(QWidget):
    """Labelled horizontal bars — per-dimension σ, task rollups, and the like."""

    def __init__(self, unit: str = "") -> None:
        super().__init__()
        self._rows: list[tuple[str, float, str]] = []
        self.unit = unit
        self.setMinimumHeight(80)

    def set_rows(self, rows: list) -> None:
        """rows: [(label, value, color_hex_or_empty)]"""
        self._rows = rows
        self.setMinimumHeight(max(60, 18 * len(rows) + 8))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        fg = pal.windowText().color()
        p.fillRect(self.rect(), pal.base())
        if not self._rows:
            return
        f = QFont(); f.setPointSize(8); p.setFont(f)
        vmax = max((v for _l, v, _c in self._rows), default=1.0) or 1.0
        label_w, value_w = 74, 66
        for i, (label, value, color) in enumerate(self._rows):
            y = 4 + i * 18
            p.setPen(QPen(QColor(fg), 1))
            p.drawText(QRectF(2, y, label_w, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            track = self.width() - label_w - value_w - 8
            w = max(1.0, track * (value / vmax))
            p.fillRect(QRectF(label_w + 4, y + 4, w, 9),
                       QColor(color or JOINT_COLORS[i % len(JOINT_COLORS)]))
            p.drawText(QRectF(self.width() - value_w - 2, y, value_w, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{_fmt(value)}{self.unit}")


class Histogram(QWidget):
    """Distribution with optional markers.

    The point of showing it: this dataset's |Δa| spread is narrow (p99/p50 =
    1.7), and a curator who sees the shape stops looking for outliers that
    are not there.
    """

    clicked_value = pyqtSignal(float)

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.title = title
        self._values = np.array([])
        self._marks: list[tuple[float, str]] = []
        self.setMinimumHeight(110)

    def set_values(self, values, marks=None) -> None:
        self._values = np.asarray(values, dtype=float)
        self._marks = marks or []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        fg = pal.windowText().color()
        p.fillRect(self.rect(), pal.base())
        f = QFont(); f.setPointSize(8); p.setFont(f)
        p.setPen(QPen(QColor(fg), 1))
        p.drawText(QRectF(4, 2, self.width() - 8, 14),
                   Qt.AlignmentFlag.AlignLeft, self.title)
        if self._values.size < 2:
            return
        m_l, m_r, m_t, m_b = 8, 8, 20, 16
        plot = QRectF(m_l, m_t, max(1, self.width() - m_l - m_r),
                      max(1, self.height() - m_t - m_b))
        counts, edges = np.histogram(self._values, bins=28)
        cmax = counts.max() or 1
        bw = plot.width() / len(counts)
        base = QColor("#4a9eff"); base.setAlpha(180)
        for i, c in enumerate(counts):
            h = plot.height() * (c / cmax)
            p.fillRect(QRectF(plot.left() + i * bw, plot.bottom() - h,
                              max(1.0, bw - 1), h), base)
        lo, hi = float(edges[0]), float(edges[-1])
        for value, label in self._marks:
            if not (lo <= value <= hi):
                continue
            x = plot.left() + plot.width() * (value - lo) / max(1e-12, hi - lo)
            c = QColor("#e74c3c")
            p.setPen(QPen(c, 2))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.drawText(QRectF(x + 3, plot.top() - 2, 90, 14),
                       Qt.AlignmentFlag.AlignLeft, label)
        p.setPen(QPen(QColor(fg), 1))
        p.drawText(QRectF(plot.left(), plot.bottom() + 1, 80, 14),
                   Qt.AlignmentFlag.AlignLeft, _fmt(lo))
        p.drawText(QRectF(plot.right() - 80, plot.bottom() + 1, 80, 14),
                   Qt.AlignmentFlag.AlignRight, _fmt(hi))
