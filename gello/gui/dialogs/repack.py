from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gello.data.libero_format import DEAD_SPACE_RATIO, hdf5_repack_status
from gello.gui.i18n import tr


class RepackDialog(QDialog):
    """Pick which .hdf5 files to repack, pre-checking only the un-repacked ones.

    Repacking is minutes of CPU per GB and gains nothing the second time, so
    running it over a directory that already contains finished files is pure
    waste -- but "which of these did I already do?" is not something the
    operator should have to remember. hdf5_repack_status() answers it from the
    file itself, and only the files that would actually benefit start checked.
    """

    def __init__(self, parent: QWidget, paths: list) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("용량 최적화 (재압축)"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr(
            "재압축할 파일을 선택하세요. 이미 재압축된 파일은 기본으로 해제되어 있습니다."
        )))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels([tr("파일"), tr("크기"), tr("에피소드"),
                                   tr("이미지 압축"), tr("재압축 이력")])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumSize(880, 240)
        self._rows = []
        n_todo = 0
        for path in paths:
            st = hdf5_repack_status(path)
            # 왜 다시 필요한지를 근거별로 모은다. 추가와 삭제는 원인이 다르고
            # (lzf가 섞임 / 지운 자리가 남음) 둘 다 동시에 성립할 수 있다.
            reasons = []
            if st["mixed"]:
                reasons.append(tr("{n}개 추가됨").format(n=st["new_since"] or "?"))
            if st["deleted_since"]:
                reasons.append(tr("{n}개 삭제됨").format(n=st["deleted_since"]))
            if st["dead_ratio"] >= DEAD_SPACE_RATIO:
                # 삭제분이 차지하던 자리. 개수 비교만으로는 '두 개 지우고 두 개
                # 더 찍은' 흔한 경우를 놓치므로, 이쪽이 결정적인 근거다.
                reasons.append(tr("빈 공간 {mb:,.0f} MB ({p:.0f}%)").format(
                    mb=st["dead_bytes"] / 1e6, p=st["dead_ratio"] * 100))
            if reasons:
                history = ", ".join(reasons) + tr(" — 다시 필요")
            elif st["marker"]:
                history = st["marker"]
            elif st["repacked"]:
                history = tr("완료 (gzip 감지)")
            else:
                history = tr("안 됨")
            item = QTreeWidgetItem([
                Path(path).name,
                f"{st['size']/1e6:,.1f} MB",
                str(st["episodes"]),
                st["compression"] or ("?" if st["error"] else "없음"),
                history,
            ])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            todo = not st["repacked"] and not st["error"]
            item.setCheckState(0, Qt.CheckState.Checked if todo else Qt.CheckState.Unchecked)
            if st["error"]:
                item.setText(4, st["error"])
                item.setDisabled(True)
            elif reasons:
                for c in range(5):
                    item.setForeground(c, Qt.GlobalColor.darkYellow)
            elif st["repacked"]:
                item.setForeground(0, Qt.GlobalColor.gray)
            n_todo += bool(todo)
            self.tree.addTopLevelItem(item)
            self._rows.append((path, item))
        for c in range(5):
            self.tree.resizeColumnToContents(c)
        layout.addWidget(self.tree)

        row = QHBoxLayout()
        all_btn = QPushButton(tr("전체 선택"))
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton(tr("전체 해제"))
        none_btn.clicked.connect(lambda: self._set_all(False))
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch()
        row.addWidget(QLabel(tr("재압축 안 된 파일: {n}개").format(n=n_todo)))
        layout.addLayout(row)

        note = QLabel(tr(
            "재압축은 삭제된 에피소드가 차지하던 공간을 회수하고 이미지를 gzip으로 다시 "
            "압축합니다. 내용 검증 후 원본을 교체하며, 실패하면 원본은 그대로 남습니다.\n"
            "수집 세션이 파일을 열고 있으면 실패합니다 -- 세션을 먼저 종료하세요."
        ))
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("재압축 시작"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, on: bool) -> None:
        for _, item in self._rows:
            if not item.isDisabled():
                item.setCheckState(
                    0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )

    def selected(self) -> list:
        return [p for p, it in self._rows
                if it.checkState(0) == Qt.CheckState.Checked and not it.isDisabled()]
