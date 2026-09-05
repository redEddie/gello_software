from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.dialogs.hf_account import HfAccountDialog, hf_account
from gello.gui.widgets import Recents
from gello.gui.fonts import MONO_STACK
from gello.gui.i18n import tr
from gello.gui.text_utils import repo_id_error


class HdfUploadDialog(QDialog):
    """Collects args for scripts/convert/upload_to_hub.py before running it as a
    subprocess (see LiberoCollectorWindow._open_hdf5_upload). Uploads the
    RAW curated .hdf5 as-is -- the converted/LeRobot half of the dual
    upload (see ~/huggingface_upload_process.md) is the separate "LeRobot
    변환..." button's --push option.
    """

    def __init__(self, parent: QWidget, start_dir: str = "") -> None:
        """``start_dir`` is where 찾아보기 opens -- NOT a preselected file.

        It used to be a "default file" that callers filled with the data-root
        *directory*. The field then held a directory, and 'Repo 안 파일 이름'
        derived from it, so an upload went to the Hub under the folder's name
        (`libero_datasets`, 0.81 GB). Worse, that name later collided with the
        folder a multi-file upload wanted to create, and the Hub rejected the
        commit with 'Invalid file change'. Starting empty removes the whole
        class: nothing is ever uploaded under a name the operator didn't pick.
        """
        super().__init__(parent)
        self.setWindowTitle(tr("HDF5 원본 업로드"))
        self._start_dir = start_dir
        default_file = ""
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("업로드할 .hdf5 파일 (여러 개 선택 가능, 이미 큐레이션 끝난 파일):")))
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit(default_file)
        self.file_edit.textChanged.connect(lambda: self._on_files_changed())
        file_row.addWidget(self.file_edit, 1)
        browse_btn = QPushButton(tr("찾아보기..."))
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        self._recents = Recents()

        grid = QGridLayout()
        # Each row shows a filled-in example next to the field. Parts that must
        # be replaced are written as **** so a copy-paste of the example alone
        # can never be mistaken for a working value.
        ex = QLabel(tr("예)  knu-physical-ai/****"))
        ex.setStyleSheet(f"color: #888; font-family: {MONO_STACK};")
        grid.addWidget(QLabel(tr("Repo ID:")), 0, 0)
        grid.addWidget(ex, 0, 3)
        self.repo_id_edit = QComboBox()
        self.repo_id_edit.setEditable(True)
        self.repo_id_edit.addItems(self._recents.get("hdf5_repo_id"))
        self.repo_id_edit.setCurrentText(self._recents.most_recent("hdf5_repo_id"))
        self.repo_id_edit.lineEdit().setPlaceholderText(
            tr("<org>/<dataset-name> 형식")
        )
        grid.addWidget(self.repo_id_edit, 0, 1)

        ex_name = QLabel(tr("예)  ****_demo.hdf5"))
        ex_name.setStyleSheet(f"color: #888; font-family: {MONO_STACK};")
        self.path_in_repo_label = QLabel(tr("Repo 안 파일 이름:"))
        grid.addWidget(self.path_in_repo_label, 1, 0)
        grid.addWidget(ex_name, 1, 2)
        self.path_in_repo_edit = QLineEdit(Path(default_file).name if default_file else "")
        self.path_in_repo_edit.setPlaceholderText(
            tr("비워두면 로컬 파일 이름 그대로")
        )
        grid.addWidget(self.path_in_repo_edit, 1, 1)
        layout.addLayout(grid)

        self.file_count_label = QLabel("")
        self.file_count_label.setStyleSheet("color: #888;")
        layout.addWidget(self.file_count_label)

        self.private_check = QCheckBox(tr("비공개 데이터셋으로 업로드 (--private)"))
        layout.addWidget(self.private_check)

        self.delete_existing_check = QCheckBox(
            tr("업로드 전 Hub의 기존 파일 삭제 (다른 이름으로 올렸던 예전 파일 정리용)")
        )
        self.delete_existing_check.toggled.connect(self._on_delete_existing_toggled)
        layout.addWidget(self.delete_existing_check)

        old_name_row = QHBoxLayout()
        self.old_path_label = QLabel(tr("삭제할 기존 파일 이름:"))
        self.old_path_label.setEnabled(False)
        old_name_row.addWidget(self.old_path_label)
        self.old_path_in_repo_edit = QLineEdit()
        self.old_path_in_repo_edit.setPlaceholderText(tr("비워두면 위 'Repo 안 파일 이름'과 동일"))
        self.old_path_in_repo_edit.setEnabled(False)
        old_name_row.addWidget(self.old_path_in_repo_edit, 1)
        layout.addLayout(old_name_row)

        acct_text, acct_color = hf_account()
        acct_row = QHBoxLayout()
        self.hf_account_label = QLabel(acct_text)
        self.hf_account_label.setStyleSheet(f"color: {acct_color}; font-weight: bold;")
        self.hf_account_label.setWordWrap(True)
        acct_row.addWidget(self.hf_account_label, 1)
        switch_btn = QPushButton(tr("계정 전환..."))
        switch_btn.clicked.connect(self._open_account_dialog)
        acct_row.addWidget(switch_btn)
        layout.addLayout(acct_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("업로드 시작"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._on_files_changed()


    def _open_account_dialog(self) -> None:
        dlg = HfAccountDialog(self)
        dlg.exec()
        text, color = hf_account()
        self.hf_account_label.setText(text)
        self.hf_account_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _on_delete_existing_toggled(self, on: bool) -> None:
        self.old_path_label.setEnabled(on)
        self.old_path_in_repo_edit.setEnabled(on)

    def _browse_file(self) -> None:
        first = self.file_edit.text().split()[0] if self.file_edit.text().strip() else ""
        start = str(Path(first).parent) if first else (self._start_dir or str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("업로드할 .hdf5 파일 (여러 개 선택 가능)"), start, "HDF5 (*.hdf5)")
        if not paths:
            return
        self.file_edit.setText(" ".join(paths))
        self._on_files_changed()

    def _on_files_changed(self) -> None:
        """Keeps the repo-name field honest about what it will do.

        With one file it renames; with several it can only be a folder, since
        one name for many uploads would leave just the last one. Saying so here
        is cheaper than discovering it on the Hub afterwards.
        """
        files = self.file_edit.text().split()
        multi = len(files) > 1
        if multi:
            self.path_in_repo_label.setText(tr("Repo 안 폴더:"))
            self.path_in_repo_edit.setPlaceholderText(
                tr("비워두면 repo 최상위. 파일 이름은 각자 그대로 유지됩니다"))
            if self.path_in_repo_edit.text().strip() in {Path(f).name for f in files}:
                self.path_in_repo_edit.clear()
            self.file_count_label.setText(
                tr("파일 {n}개 선택됨").format(n=len(files)))
        else:
            self.path_in_repo_label.setText(tr("Repo 안 파일 이름:"))
            self.path_in_repo_edit.setPlaceholderText(tr("비워두면 로컬 파일 이름 그대로"))
            if files and not self.path_in_repo_edit.text().strip():
                # 디렉터리에서 이름을 따오지 않는다 -- 그렇게 만들어진 게
                # Hub의 `libero_datasets` 파일이다.
                if Path(files[0]).is_file():
                    self.path_in_repo_edit.setText(Path(files[0]).name)
            self.file_count_label.setText("")
        # 여러 개일 때 '기존 파일 삭제'의 다른 이름 지정은 의미가 없다.
        self.old_path_label.setVisible(not multi)
        self.old_path_in_repo_edit.setVisible(not multi)

    def build_args(self) -> "list[str] | None":
        """Returns the script's argv (sans program name), or None (with a
        warning dialog already shown) if required fields are missing."""
        files = self.file_edit.text().split()
        if not files:
            QMessageBox.warning(self, tr("파일 필요"), tr(".hdf5 파일을 하나 이상 선택하세요."))
            return None
        repo_id = self.repo_id_edit.currentText().strip()
        err = repo_id_error(repo_id)
        if err:
            QMessageBox.warning(self, tr("Repo ID 오류"), tr(err))
            return None
        self._recents.add("hdf5_repo_id", repo_id)
        args = [*files, "--repo-id", repo_id]
        path_in_repo = self.path_in_repo_edit.text().strip()
        if path_in_repo:
            args += ["--path-in-repo", path_in_repo]
        args.append("--private" if self.private_check.isChecked() else "--no-private")
        if self.delete_existing_check.isChecked():
            args.append("--delete-existing")
            old_path_in_repo = self.old_path_in_repo_edit.text().strip()
            if old_path_in_repo and len(files) == 1:
                args += ["--old-path-in-repo", old_path_in_repo]
        return args
