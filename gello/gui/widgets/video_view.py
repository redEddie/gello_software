"""Video display widget and numpy-to-pixmap helper."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy


def np_to_pixmap(arr: np.ndarray) -> QPixmap:
    arr = np.ascontiguousarray(arr)
    h, w, ch = arr.shape
    img = QImage(arr.data, w, h, ch * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class VideoView(QLabel):
    """Shows frames at their own aspect ratio without letting them drive layout.

    Two things go wrong with a plain QLabel here. Its sizeHint *is* its
    pixmap's size, so scaling each frame to the label's current size inside a
    layout is a feedback loop -- the pixmap grows the label, the bigger label
    grows the next pixmap. An Ignored size policy breaks it: the layout decides
    the box, the frame fits inside it.

    The other is a fixed 4:3 minimum, which wastes width on the 256x256 square
    frames the collector records (LIBERO/OpenVLA convention) -- the minimum is
    square here, and KeepAspectRatio handles a 640x480 "원본 해상도 유지" file
    just as well.

    Keeping the source frame also means dragging the splitter rescales what is
    on screen, instead of leaving a stale pixmap until the next tick -- which
    never comes while paused.
    """

    # 정사각 밖을 얼마나 어둡게 덮을지. 완전히 가리지 않는 이유는 그 바깥에도
    # 조작자가 봐야 할 것(팔이 프레임에 들어오는 순간, 사람 손)이 있기 때문이다.
    _VIGNETTE_ALPHA = 150

    def __init__(self) -> None:
        super().__init__()
        self._frame = None
        self._square_guide = True
        # 640 폭 기준. 파이프라인의 크롭 정렬(libero_format.square_crop)과
        # 같은 규약이라 가이드가 곧 저장/변환 프레이밍이다.
        self._crop_zoom = 1.0
        self._crop_x = 0
        self._crop_y = 0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet("background-color: #111; color: #666;")

    def set_frame(self, arr) -> None:
        self._frame = arr
        self._rescale()

    def set_square_guide(self, on: bool) -> None:
        """Dims everything outside the centre square.

        The .hdf5 keeps the camera's full 640x480, but the LeRobot copy is
        centre-cropped square before training. Without this the operator frames
        against a 4:3 view and only finds out at conversion that the edges were
        never going to survive.
        """
        self._square_guide = bool(on)
        self._rescale()

    def set_crop_guide(self, zoom: float = 1.0, x: int = 0, y: int = 0) -> None:
        """Aligns the crop guide with the pipeline's square_crop for this
        camera (zoom divides the side; x/y are px at 640 source width)."""
        self._crop_zoom = float(zoom)
        self._crop_x = int(x)
        self._crop_y = int(y)
        self._rescale()

    def clear_frame(self, text: str = "") -> None:
        self._frame = None
        self.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX -- 다음 영상 전까지 해제
        self.setPixmap(QPixmap())
        if text:
            self.setText(text)

    def _decorate(self, pix: QPixmap) -> QPixmap:
        """Draws the crop guide onto a copy -- the stored frame is untouched, so
        toggling the guide never changes what gets recorded."""
        w, h = pix.width(), pix.height()
        if not self._square_guide:
            return pix
        side = min(w, h)
        if self._crop_zoom > 1.0:
            side = max(16, round(side / self._crop_zoom))
        if side == w and side == h:
            return pix          # 크롭이 프레임 전체라 그릴 게 없다
        sc = w / 640
        x0 = min(max((w - side) // 2 + round(self._crop_x * sc), 0), w - side)
        y0 = min(max((h - side) // 2 + round(self._crop_y * sc), 0), h - side)
        out = QPixmap(pix)
        p = QPainter(out)
        shade = QColor(0, 0, 0, self._VIGNETTE_ALPHA)
        # 겹치지 않는 4개 밴드 -- 모서리에 음영이 두 번 깔리지 않게.
        p.fillRect(0, 0, w, y0, shade)
        p.fillRect(0, y0 + side, w, h - y0 - side, shade)
        p.fillRect(0, y0, x0, side, shade)
        p.fillRect(x0 + side, y0, w - x0 - side, side, shade)
        p.setPen(QPen(QColor(255, 255, 255, 120), 1))
        p.drawRect(x0, y0, side - 1, side - 1)
        p.end()
        return out

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._frame is None:
            return
        h, w = self._frame.shape[:2]
        # Height is what's scarce (two views stacked), so the frame's width is
        # decided by it. Shrinking the label to exactly that width means the
        # leftover is normal panel background rather than a black letterbox
        # around a small square -- which is what a 256x256 source in a wide box
        # looks like. Guarded because setMaximumWidth relayouts and re-enters
        # here; the height it depends on is set by the splitter, not by this
        # width, so it settles after one pass.
        want = max(1, round(self.height() * w / h))
        if self.maximumWidth() != want:
            self.setMaximumWidth(want)
        self.setPixmap(self._decorate(np_to_pixmap(self._frame)).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
