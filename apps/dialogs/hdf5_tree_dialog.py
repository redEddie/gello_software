"""HDF5 structure viewer dialog."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gello.gui.gui_widgets import np_to_pixmap
from gello.gui.i18n import tr

from apps.dialogs._image_utils import _depth_colormap


class Hdf5TreeDialog(QDialog):
    """HDF5 내, by构造 구조 뷰어 — myHDF5(h5web)처럼 트리 + attrs + 미리보기.

    구조(이름·shape·dtype·압축·attrs)는 열 때 한 번 읽고 파일을 바로
    닫는다 — 뷰어가 파일을 쥔 채로 있으면 수집/재압축과 부딪힌다. 값·이미지
    미리보기만 항목을 클릭할 때 잠깐 다시 연다.
    """

    PREVIEW_ELEMS = 120     # 이 개수 이하의 수치 데이터셋은 값을 그대로 보여준다

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(tr("HDF5 구조 — {n}").format(n=self._path.name))
        self.resize(960, 620)
        col = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("이름"), tr("정보")])
        self.tree.setColumnWidth(0, 300)
        self.tree.currentItemChanged.connect(self._on_select)
        split.addWidget(self.tree)
        right = QWidget()
        rcol = QVBoxLayout(right)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(200)
        rcol.addWidget(self.preview)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setStyleSheet(
            "font-family: 'DejaVu Sans Mono', monospace; font-size: 12px;")
        rcol.addWidget(self.detail, 1)
        split.addWidget(right)
        split.setSizes([420, 540])
        col.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        try:
            with h5py.File(self._path, "r") as f:
                self._populate(f, self.tree.invisibleRootItem())
        except BlockingIOError:
            self.detail.setPlainText(tr(
                "파일이 사용 중입니다 (수집 세션/재압축). 끝난 뒤 다시 여세요."))
        except OSError as e:
            self.detail.setPlainText(tr("파일을 열지 못했습니다: {e}").format(e=e))
        self.tree.expandToDepth(0)

    def _populate(self, node, parent_item) -> None:
        # metadata 를 맨 위로 -- 파일을 여는 사람이 먼저 찾는 것이 scene
        # 정의다 (HDF5 그룹은 순서가 없어 뷰어가 정렬을 정한다).
        for key in sorted(node, key=lambda k: (k != "metadata", k)):
            obj = node[key]
            if isinstance(obj, h5py.Group):
                it = QTreeWidgetItem(
                    [key, tr("그룹 · 항목 {n} · attrs {a}")
                     .format(n=len(obj), a=len(obj.attrs))])
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
                it.setData(0, Qt.ItemDataRole.UserRole, obj.name)
                parent_item.addChild(it)
                self._add_attr_items(obj, it)
                self._populate(obj, it)
            else:
                shape = " × ".join(str(s) for s in obj.shape) or tr("스칼라")
                info = f"{shape} · {obj.dtype}"
                if obj.compression:
                    info += f" · {obj.compression}"
                it = QTreeWidgetItem([key, info])
                it.setData(0, Qt.ItemDataRole.UserRole, obj.name)
                parent_item.addChild(it)
                self._add_attr_items(obj, it)

    def _add_attr_items(self, obj, parent_item) -> None:
        """attrs 를 트리에 '@이름' 회색 항목으로 직접 보여준다 -- myHDF5 는
        오른쪽 패널에만 보여줘서 'scene_id 가 없다'는 오해가 실제로 있었다."""
        for k in sorted(obj.attrs):
            s = str(obj.attrs[k])
            it = QTreeWidgetItem(
                [f"@{k}", s[:80] + ("…" if len(s) > 80 else "")])
            # 회색은 '비활성/숨김'으로 읽힌다 (실사용 피드백) -- 기울임꼴로
            # 데이터셋과 구분하고 색은 그대로 둔다.
            f = it.font(0)
            f.setItalic(True)
            it.setFont(0, f)
            it.setFont(1, f)
            it.setData(0, Qt.ItemDataRole.UserRole, ("attr", obj.name, k))
            parent_item.addChild(it)

    def _on_select(self, item, _prev=None) -> None:
        self.preview.clear()
        if item is None:
            return
        h5path = item.data(0, Qt.ItemDataRole.UserRole)
        if not h5path:
            return
        if isinstance(h5path, tuple) and h5path[0] == "attr":
            _tag, owner, key = h5path
            try:
                with h5py.File(self._path, "r") as f:
                    v = f[owner].attrs[key]
                self.detail.setPlainText(
                    f"attr: {owner}/@{key}\n타입: {type(v).__name__}\n\n{v}")
            except Exception as e:  # noqa: BLE001
                self.detail.setPlainText(f"{type(e).__name__}: {e}")
            return
        try:
            with h5py.File(self._path, "r") as f:
                obj = f[h5path]
                lines = [f"경로: {h5path}"]
                if isinstance(obj, h5py.Dataset):
                    lines.append(f"shape: {tuple(obj.shape)}   dtype: {obj.dtype}")
                    lines.append(f"압축: {obj.compression or '-'}   "
                                 f"chunks: {obj.chunks or '-'}")
                    nbytes = obj.size * obj.dtype.itemsize
                    lines.append(f"크기(비압축): {nbytes / 1e6:.1f} MB")
                if len(obj.attrs):
                    lines.append("")
                    lines.append("── attrs " + "─" * 30)
                    for k in sorted(obj.attrs):
                        v = obj.attrs[k]
                        s = str(v)
                        lines.append(f"{k}: {s[:500]}" + ("…" if len(s) > 500 else ""))
                if isinstance(obj, h5py.Dataset):
                    arr = None
                    if obj.dtype == np.uint8 and obj.ndim == 4 and obj.shape[-1] == 3:
                        arr = obj[0]
                        lines.append("")
                        lines.append(tr("(첫 프레임 미리보기)"))
                    elif obj.dtype == np.uint8 and obj.ndim == 3 and obj.shape[-1] == 3:
                        arr = obj[...]
                        lines.append("")
                        lines.append(tr("(이미지 미리보기)"))
                    elif obj.dtype == np.uint16 and obj.ndim in (2, 3):
                        # depth (#17): mm -> m 변환 후 라이브 Depth 탭과 같은
                        # 컬러맵 + 척도 바
                        z = (obj[0] if obj.ndim == 3 else obj[...]) / 1000.0
                        valid = z > 0
                        zmax = float(np.percentile(z[valid], 98)) if valid.any() else 1.0
                        arr = _depth_colormap(z.astype(np.float32), zmax)
                        lines.append("")
                        lines.append(tr("(depth 첫 프레임 · 척도 ~{m:.2f}m)")
                                     .format(m=zmax))
                    elif obj.size and obj.size <= self.PREVIEW_ELEMS:
                        lines.append("")
                        lines.append("── 값 " + "─" * 32)
                        lines.append(np.array2string(
                            np.asarray(obj[...]), precision=4, threshold=200))
                    if arr is not None:
                        pix = np_to_pixmap(np.ascontiguousarray(arr))
                        self.preview.setPixmap(pix.scaled(
                            self.preview.width(), max(200, self.preview.height()),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))
                self.detail.setPlainText("\n".join(lines))
        except BlockingIOError:
            self.detail.setPlainText(tr("파일이 사용 중이라 값을 읽지 못했습니다."))
        except Exception as e:  # noqa: BLE001
            self.detail.setPlainText(f"{type(e).__name__}: {e}")
