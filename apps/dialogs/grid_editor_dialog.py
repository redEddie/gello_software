"""Workspace 3×3 grid editor dialog."""

from __future__ import annotations

import sys

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from gello.gui.gui_widgets import np_to_pixmap
from gello.gui.grid_overlay import (
    DEFAULT_CORNERS,
    active_corners,
    draw_grid,
    save_grid_store as _default_save_grid_store,
)
from gello.gui.i18n import tr


def _workspace_save_grid_store(store: dict) -> None:
    """Allow tests/apps to patch save_grid_store via collect_workspace.

    collect_workspace may be imported as either ``collect_workspace`` or
    ``apps.collect_workspace`` depending on the entry point; resolve the
    callback at runtime so monkey-patches are respected.
    """
    for name in ("collect_workspace", "apps.collect_workspace"):
        cw = sys.modules.get(name)
        if cw is not None and hasattr(cw, "save_grid_store"):
            cw.save_grid_store(store)
            return
    _default_save_grid_store(store)


class _GridCanvas(QLabel):
    """격자 편집 캔버스 — 배경 이미지 위에서 꼭짓점 4개를 드래그한다.

    꼭짓점은 정규화 좌표(0..1)로 들고 있어 배경 해상도와 무관하다.
    드래그 중에는 외곽선·핸들만 갱신하고, 내부 3×3 선은 '변환' 버튼이
    다시 그린다 (full_grid 플래그).
    """

    changed = pyqtSignal()
    drag_started = pyqtSignal()     # 실행취소 스냅샷 시점
    HANDLE_PX = 22          # 위젯 픽셀 기준 잡기 반경

    def __init__(self, background: np.ndarray, corners: list) -> None:
        super().__init__()
        self._img = np.ascontiguousarray(background)
        self.corners = [list(c) for c in corners]
        self.full_grid = True
        self.crop_params: "dict | None" = None   # {"zoom","x","y"} -- agent 크롭
        self.show_crop = False
        self._drag: "int | None" = None
        self._fit = (1.0, 0, 0)     # scale, x-offset, y-offset (위젯 좌표계)
        self.setMinimumSize(480, 360)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(False)

    # ---- 렌더 ----
    def render_grid(self) -> None:
        import cv2

        h, w = self._img.shape[:2]
        if self.full_grid:
            out = draw_grid(self._img, self.corners, 80)
        else:
            out = self._img.copy()
        if self.show_crop and self.crop_params:
            out = self._crop_shade(out)
        pts = np.int32([[c[0] * w, c[1] * h] for c in self.corners])
        cv2.polylines(out, [pts.reshape(-1, 1, 2)], True, (80, 255, 140),
                      max(1, round(w / 320)), cv2.LINE_AA)
        r = max(4, round(w / 90))
        for i, (x, y) in enumerate(pts):
            cv2.circle(out, (int(x), int(y)), r, (255, 80, 80), -1, cv2.LINE_AA)
            cv2.putText(out, "1234"[i], (int(x) + r + 2, int(y) - r),
                        cv2.FONT_HERSHEY_SIMPLEX, w / 1200,
                        (255, 220, 220), 1, cv2.LINE_AA)
        pix = np_to_pixmap(out)
        avail_w, avail_h = max(1, self.width()), max(1, self.height())
        scale = min(avail_w / w, avail_h / h)
        sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
        self._fit = (scale, (avail_w - sw) // 2, (avail_h - sh) // 2)
        self.setPixmap(pix.scaled(sw, sh,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))

    def _crop_shade(self, img: np.ndarray) -> np.ndarray:
        """변환 파이프라인의 정사각 크롭 밖을 어둡게 -- 라이브 뷰의 크롭
        가이드(VideoView._decorate)와 같은 수식이라 보이는 영역이 일치한다."""
        import cv2

        h, w = img.shape[:2]
        side = min(w, h)
        z = float(self.crop_params.get("zoom", 1.0))
        if z > 1.0:
            side = max(16, round(side / z))
        sc = w / 640
        x0 = min(max((w - side) // 2
                     + round(self.crop_params.get("x", 0) * sc), 0), w - side)
        y0 = min(max((h - side) // 2
                     + round(self.crop_params.get("y", 0) * sc), 0), h - side)
        img[:y0] //= 2
        img[y0 + side:] //= 2
        img[y0:y0 + side, :x0] //= 2
        img[y0:y0 + side, x0 + side:] //= 2
        cv2.rectangle(img, (x0, y0), (x0 + side - 1, y0 + side - 1),
                      (255, 255, 255), 1)
        return img

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.render_grid()

    # ---- 좌표 변환/드래그 ----
    def _to_norm(self, pos) -> "tuple[float, float]":
        scale, ox, oy = self._fit
        h, w = self._img.shape[:2]
        return ((pos.x() - ox) / (w * scale), (pos.y() - oy) / (h * scale))

    def _pick(self, pos) -> "int | None":
        scale, ox, oy = self._fit
        h, w = self._img.shape[:2]
        best, best_d = None, self.HANDLE_PX
        for i, (cx, cy) in enumerate(self.corners):
            dx = cx * w * scale + ox - pos.x()
            dy = cy * h * scale + oy - pos.y()
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._drag = self._pick(event.position())
        if self._drag is not None:
            self.drag_started.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag is None:
            return
        x, y = self._to_norm(event.position())
        self.corners[self._drag] = [min(1.0, max(0.0, x)),
                                    min(1.0, max(0.0, y))]
        self.full_grid = False      # 내부선은 '변환'이 다시 계산한다
        self.render_grid()
        self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._drag = None


class GridEditorDialog(QDialog):
    """워크스페이스 3×3 격자 편집 — 드래그·정렬·변환·저장/불러오기.

    배경은 호출 시점의 agent 카메라 프레임(없으면 레이아웃 스틸/회색판).
    저장하면 workspace_grids.json 의 해당 이름에 기록되고 active 로 지정돼
    Live 오버레이가 바로 이 격자를 쓴다.
    """

    def __init__(self, parent, background: np.ndarray, store: dict,
                 crop_params: "dict | None" = None,
                 save_callback: "callable | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("3×3 격자 편집"))
        self._store = store
        self._crop_params = crop_params
        self._save_callback = save_callback or _workspace_save_grid_store
        corners = active_corners(store) or DEFAULT_CORNERS
        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "꼭짓점(1=좌상, 2=우상, 3=우하, 4=좌하)을 드래그해 작업면에 맞추고 "
            "'변환'으로 내부 3×3 선을 다시 그립니다. 정렬 버튼은 위/아래 두 "
            "꼭짓점의 높이를 맞춥니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)
        self.canvas = _GridCanvas(background, corners)
        col.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        for text, slot, tip in (
                ("위 정렬", lambda: self._align(0, 1, 1), "1·2번 꼭짓점을 같은 높이로"),
                ("아래 정렬", lambda: self._align(3, 2, 1), "4·3번 꼭짓점을 같은 높이로"),
                ("좌 정렬", lambda: self._align(0, 3, 0), "1·4번 꼭짓점을 같은 가로 위치로"),
                ("우 정렬", lambda: self._align(1, 2, 0), "2·3번 꼭짓점을 같은 가로 위치로"),
                ("변환 (3×3 다시 그리기)", self._transform,
                 "현재 꼭짓점으로 내부 격자선을 원근 계산해 그립니다"),
                ("실행취소", self._undo, "마지막 드래그/정렬 하나를 되돌립니다")):
            b = QPushButton(tr(text))
            b.setToolTip(tr(tip))
            b.clicked.connect(slot)
            row.addWidget(b)
        self.crop_check = QCheckBox(tr("크롭 가이드"))
        self.crop_check.setToolTip(tr(
            "LeRobot 변환 때 남는 정사각 영역 밖을 어둡게 표시합니다.\n"
            "격자(물체 배치)가 학습 화면 안에 들어오는지 확인용."))
        self.crop_check.setEnabled(crop_params is not None)
        self.crop_check.setChecked(crop_params is not None)
        self.crop_check.toggled.connect(self._on_crop_toggled)
        row.addWidget(self.crop_check)
        row.addStretch(1)
        col.addLayout(row)

        srow = QHBoxLayout()
        self.load_combo = QComboBox()
        for name in sorted(store["grids"]):
            self.load_combo.addItem(name)
        srow.addWidget(self.load_combo, 1)
        load_btn = QPushButton(tr("불러오기"))
        load_btn.clicked.connect(self._load_selected)
        srow.addWidget(load_btn)
        srow.addSpacing(16)
        self.name_edit = QLineEdit(store.get("active") or "default")
        self.name_edit.setPlaceholderText(tr("저장 이름"))
        srow.addWidget(self.name_edit, 1)
        save_btn = QPushButton(tr("저장"))
        save_btn.setToolTip(tr("이 이름으로 저장하고 active 격자로 지정합니다."))
        save_btn.clicked.connect(self._save)
        srow.addWidget(save_btn)
        col.addLayout(srow)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#2ecc71;")
        col.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._undo_stack: list = []
        self.canvas.changed.connect(lambda: self.status_label.setText(""))
        self.canvas.drag_started.connect(self._push_undo)
        self.canvas.crop_params = crop_params
        self.canvas.show_crop = self.crop_check.isChecked()
        self.canvas.render_grid()

    def _on_crop_toggled(self, on: bool) -> None:
        self.canvas.show_crop = bool(on)
        self.canvas.render_grid()

    def _push_undo(self) -> None:
        self._undo_stack.append([list(c) for c in self.canvas.corners])
        del self._undo_stack[:-20]      # 최근 20단계면 충분

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self.canvas.corners = self._undo_stack.pop()
        self.canvas.full_grid = True
        self.canvas.render_grid()

    def _align(self, i: int, j: int, axis: int) -> None:
        self._push_undo()
        c = self.canvas.corners
        v = (c[i][axis] + c[j][axis]) / 2
        c[i][axis] = c[j][axis] = v
        self.canvas.render_grid()

    def _transform(self) -> None:
        self.canvas.full_grid = True
        self.canvas.render_grid()

    def _load_selected(self) -> None:
        name = self.load_combo.currentText()
        corners = self._store["grids"].get(name)
        if not corners:
            return
        self.canvas.corners = [list(c) for c in corners]
        self.canvas.full_grid = True
        self.canvas.render_grid()
        self.name_edit.setText(name)
        self.status_label.setText(tr("{n} 불러옴").format(n=name))

    def _save(self) -> None:
        name = self.name_edit.text().strip() or "default"
        self._store["grids"][name] = [list(c) for c in self.canvas.corners]
        self._store["active"] = name
        self._save_callback(self._store)
        if self.load_combo.findText(name) < 0:
            self.load_combo.addItem(name)
        self.status_label.setText(tr("{n} 저장됨 (active)").format(n=name))
