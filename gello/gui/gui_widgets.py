"""Widgets shared by the collector GUIs.

Split out of the old wizard GUI (experiments/collect_libero_gui.py, replaced
by apps/collect_workspace.py in 62cad92) so the workspace UI could
reuse them without importing a module that also defined a whole competing
main window. Nothing here knows about the window it lives in -- these are the
pieces that were already independent of the 3-phase wizard: the video view,
the episode loader, and the camera preview thread.
"""

from __future__ import annotations

import os

# Must run before numpy/cv2/h5py/torch(via lerobot) are imported below --
# these each read the env var once at their own C-level init and spin up a
# BLAS/parallel-executor thread pool sized to the CPU core count (measured:
# 39 extra OS threads on this 20-core machine, for a GUI that does no heavy
# matrix math or bulk image processing at all). Setting these first keeps
# that pool at 1 with zero measured functional difference for this script's
# actual workload (light resize/color-convert calls). Don't copy this into
# scripts/convert/convert_libero_to_lerobot.py -- that one genuinely benefits from
# parallel video encoding.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gello.core.station import load_station  # noqa: E402

# The OMP/OPENBLAS/MKL env vars above only cap numpy's BLAS backend --
# OpenCV's own parallel_for_ executor is a separate thread pool controlled
# only by this runtime call, not an env var.
cv2.setNumThreads(1)

# launch_nodes.py needs pylibfranka, which only exists in this separate venv
# (this GUI itself runs in lerobot-venv -- see module docstring). Spawned as
# a subprocess rather than imported, same as running it by hand in a second
# terminal. 경로는 스테이션 설정의 node.python.
PYLIBFRANKA_PYTHON = load_station().node.python_path
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "launch" / "launch_nodes.py")
RUNME_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "runme.sh")
REPACK_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "convert" / "repack_hdf5.py")

# Repo IDs and output paths get retyped every session otherwise, and a typo in
# a repo ID silently creates a *new* Hub dataset rather than failing.
RECENTS_PATH = Path.home() / "libero_gui_logs" / "recent_inputs.json"
# Episodes are recorded at 20 Hz, so playing them back at 20 fps shows the
# motion at the speed it actually happened -- which is the point of reviewing.
PLAYBACK_FPS = 20
_RECENTS_MAX = 8


class Recents:
    """Most-recently-used values per field key, persisted as JSON.

    Never raises: a corrupt or unwritable file just means "no history", which
    must not be able to stop the GUI from starting or a conversion from running.
    """

    def __init__(self, path: "Path | None" = None) -> None:
        # 기본 경로를 def 시점이 아니라 실행 시점에 읽는다 -- 테스트가
        # gui_widgets.RECENTS_PATH 를 임시 파일로 바꿔 실제 GUI 기억값
        # (~/libero_gui_logs/recent_inputs.json) 을 오염시키지 않게.
        # (2026-08-26: 테스트가 PipelineDialog.steps() 를 부르면서 test 용
        # repo 'org/x' 등이 실제 파일 최상단에 박혀, GUI 전체 처리 화면이
        # 존재하지 않는 Hub 와 대조해 "전부 없음"으로 보인 사고.)
        self._path = path if path is not None else RECENTS_PATH
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str) -> list[str]:
        v = self._data.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    def most_recent(self, key: str, fallback: str = "") -> str:
        v = self.get(key)
        return v[0] if v else fallback

    def add(self, key: str, value: str) -> None:
        value = (value or "").strip()
        if not value:
            return
        cur = [v for v in self.get(key) if v != value]
        self._data[key] = [value] + cur[: _RECENTS_MAX - 1]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # history is a convenience, never a hard failure


# 미개발 표시. collect_workspace 가 gui_widgets 를 import 하므로 아래쪽 모듈인
# 여기에 두고 위에서 가져다 쓴다 -- 반대로 두면 순환 import 가 된다.
TODO_MARK = "미개발"

JOINT_LABELS = [f"J{i}" for i in range(1, 8)] + ["grip"]

STATE_LABELS_KO = {
    "idle": "대기",
    "connecting": "연결 중...",
    "homing": "홈 복귀 중",
    "reset_wait": "환경 리셋 대기",
    "gate": "자세 맞추는 중",
    "approach": "접근 중",
    "recording": "기록 중",
}


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
        # 부르면 바 7개 x 50 Hz = 초당 350 회가 GUI 스레드를 잡는다 -- 게이지
        # 갱신이 더디게 따라오던 이유다 (2026-09-01).
        if color != self._color:
            self._color = color
            self.bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {color}; }}"
            )


