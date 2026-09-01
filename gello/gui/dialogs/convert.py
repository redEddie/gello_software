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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.dialogs.hf_account import HfAccountDialog, hf_account
from gello.gui.gui_widgets import Recents
from gello.gui.i18n import tr
from gello.gui.text_utils import repo_id_error


class LerobotConvertDialog(QDialog):
    """Collects args for scripts/convert/convert_libero_to_lerobot.py before running
    it as a subprocess (see LiberoCollectorWindow._open_lerobot_convert).
    Curation (deleting bad takes) already happened in the HDF5 workflow --
    this dialog only picks which already-curated files to convert and where
    the result goes, mirroring the script's own CLI 1:1.
    """

    def __init__(self, parent: QWidget, default_root: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("LeRobot 변환 / 업로드"))
        layout = QVBoxLayout(self)

        # 모드가 맨 위에 온다. 아래 항목 중 무엇이 보이고 무엇이 쓰이는지를
        # 이 선택이 결정하므로, 다 읽은 뒤에 고르게 두면 순서가 거꾸로다.
        # Conversion and upload are separate jobs: conversion is minutes of
        # AV1 encoding, upload is seconds. Bundling them meant "I already
        # converted, just upload it" had no answer -- re-running re-encoded
        # everything. Two explicit modes instead of one ambiguous checkbox.
        mode_box = QGroupBox(tr("실행 모드"))
        mode_col = QVBoxLayout(mode_box)
        # Deliberately NO "convert and upload in one go": an episode pushed
        # to the Hub stays there even after it is deleted locally, so the
        # local result must be reviewed before anything is uploaded. A
        # combined mode exists only to skip that review.
        self.mode_convert = QRadioButton(tr("변환만 (로컬에 만들고 결과를 확인)"))
        self.mode_push_only = QRadioButton(
            tr("업로드만 (확인 끝난 '로컬 출력 경로'를 그대로 올림 -- 재변환 없음)")
        )
        self.mode_convert.setChecked(True)
        for b in (self.mode_convert, self.mode_push_only):
            mode_col.addWidget(b)
            b.toggled.connect(self._on_mode_changed)
        layout.addWidget(mode_box)

        layout.addWidget(QLabel(tr("변환할 .hdf5 파일 (여러 개 선택 가능, 이미 큐레이션 끝난 파일):")))
        file_row = QHBoxLayout()
        self.files_edit = QLineEdit()
        self.files_edit.setPlaceholderText(tr("찾아보기로 선택하세요"))
        file_row.addWidget(self.files_edit, 1)
        browse_files_btn = QPushButton(tr("찾아보기..."))
        browse_files_btn.clicked.connect(lambda: self._browse_files(default_root))
        file_row.addWidget(browse_files_btn)
        layout.addLayout(file_row)

        self._recents = Recents()

        grid = QGridLayout()
        # Each row shows a filled-in example next to the field. Parts that must
        # be replaced are written as **** so a copy-paste of the example alone
        # can never be mistaken for a working value.
        ex = QLabel(tr("예)  knu-physical-ai/****"))
        ex.setStyleSheet("color: #888; font-family: monospace;")
        grid.addWidget(QLabel(tr("Repo ID:")), 0, 0)
        grid.addWidget(ex, 0, 3)
        # Editable combo, not a plain edit: the previous repo IDs are right
        # there in the dropdown, so a session that appends to an existing Hub
        # dataset never depends on retyping the ID exactly.
        self.repo_id_edit = QComboBox()
        self.repo_id_edit.setEditable(True)
        self.repo_id_edit.addItems(self._recents.get("repo_id"))
        self.repo_id_edit.setCurrentText(self._recents.most_recent("repo_id"))
        self.repo_id_edit.lineEdit().setPlaceholderText(
            tr("<org>/<dataset-name> 형식")
        )
        grid.addWidget(self.repo_id_edit, 0, 1, 1, 2)

        ex_root = QLabel(tr("예)  /home/franka/****"))
        ex_root.setStyleSheet("color: #888; font-family: monospace;")
        grid.addWidget(QLabel(tr("로컬 출력 경로:")), 1, 0)
        grid.addWidget(ex_root, 1, 3)
        self.out_root_edit = QComboBox()
        self.out_root_edit.setEditable(True)
        self.out_root_edit.addItems(self._recents.get("lerobot_root"))
        self.out_root_edit.setCurrentText(
            self._recents.most_recent("lerobot_root", str(Path.home() / "lerobot_upload"))
        )
        grid.addWidget(self.out_root_edit, 1, 1)
        browse_root_btn = QPushButton(tr("찾아보기..."))
        browse_root_btn.clicked.connect(self._browse_root)
        grid.addWidget(browse_root_btn, 1, 2)

        grid.addWidget(QLabel(tr("FPS:")), 2, 0)
        self.fps_edit = QLineEdit("20")
        grid.addWidget(self.fps_edit, 2, 1)
        layout.addLayout(grid)

        # Options live under the mode they belong to, so a mode's irrelevant
        # knobs are not just disabled-but-visible next to the ones that matter.
        self.convert_opts = QGroupBox(tr("변환 옵션"))
        conv_col = QVBoxLayout(self.convert_opts)
        self.only_success_check = QCheckBox(tr("성공(success=True) 에피소드만 포함 (--only-success)"))
        conv_col.addWidget(self.only_success_check)

        self.resume_check = QCheckBox(
            tr(
                "처음부터 새로 만들지 않고 기존 Hub 데이터셋에 이어붙이기 (--resume) -- 다른 task를 "
                "추가할 때, 기존 데이터는 재변환/재업로드하지 않음"
            )
        )
        conv_col.addWidget(self.resume_check)
        resume_warning = QLabel(
            tr(
                "⚠ 동시에 두 명이 같은 Repo ID로 --resume 변환하지 마세요 -- 같은 파일 경로를 서로 "
                "덮어써서 조용히 데이터가 유실될 수 있습니다."
            )
        )
        resume_warning.setStyleSheet("color: #e67e22;")
        resume_warning.setWordWrap(True)
        conv_col.addWidget(resume_warning)
        layout.addWidget(self.convert_opts)

        self.upload_opts = QGroupBox(tr("업로드 옵션"))
        up_col = QVBoxLayout(self.upload_opts)

        # Which account a --push actually uploads as. This machine is shared,
        # so "whose token is cached right now" is not something to assume.
        acct_text, acct_color = hf_account()
        acct_row = QHBoxLayout()
        self.hf_account_label = QLabel(acct_text)
        self.hf_account_label.setStyleSheet(f"color: {acct_color}; font-weight: bold;")
        self.hf_account_label.setWordWrap(True)
        acct_row.addWidget(self.hf_account_label, 1)
        switch_btn = QPushButton(tr("계정 전환..."))
        switch_btn.clicked.connect(self._open_account_dialog)
        acct_row.addWidget(switch_btn)
        up_col.addLayout(acct_row)

        self.private_check = QCheckBox(tr("비공개 데이터셋으로 업로드 (--private)"))
        up_col.addWidget(self.private_check)
        layout.addWidget(self.upload_opts)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText(tr("변환 시작"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        # The default mode's radio is already checked, so toggled never fires
        # for it -- apply the initial visibility explicitly.
        self._on_mode_changed()


    def _open_account_dialog(self) -> None:
        dlg = HfAccountDialog(self)
        dlg.exec()
        text, color = hf_account()
        self.hf_account_label.setText(text)
        self.hf_account_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _on_mode_changed(self) -> None:
        push_only = self.mode_push_only.isChecked()
        self.convert_opts.setVisible(not push_only)
        self.upload_opts.setVisible(push_only)
        # The .hdf5 picker and FPS belong to conversion; they sit above the
        # mode box (shared layout) so hide-by-disable rather than by removal.
        for w in (self.files_edit, self.fps_edit):
            w.setEnabled(not push_only)
        if hasattr(self, "_ok_btn"):
            self._ok_btn.setText(tr("업로드 시작") if push_only else tr("변환 시작"))
        self.adjustSize()

    def _browse_files(self, default_root: str) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("변환할 .hdf5 파일"), default_root or str(Path.home()), "HDF5 (*.hdf5)"
        )
        if paths:
            self.files_edit.setText(" ".join(paths))

    def _browse_root(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("로컬 출력 경로"), self.out_root_edit.currentText()
        )
        if d:
            self.out_root_edit.setCurrentText(d)

    def build_args(self) -> "list[str] | None":
        """Returns the script's argv (sans program name), or None (with a
        warning dialog already shown) if required fields are missing."""
        push_only = self.mode_push_only.isChecked()
        paths = self.files_edit.text().split()
        if not paths and not push_only:
            QMessageBox.warning(self, tr("파일 필요"), tr(".hdf5 파일을 하나 이상 선택하세요."))
            return None
        repo_id = self.repo_id_edit.currentText().strip()
        err = repo_id_error(repo_id)
        if err:
            QMessageBox.warning(self, tr("Repo ID 오류"), tr(err))
            return None
        out_root = self.out_root_edit.currentText().strip()
        if not out_root:
            QMessageBox.warning(self, tr("출력 경로 필요"), tr("로컬 출력 경로를 입력하세요."))
            return None
        # Only remember values that made it past validation.
        self._recents.add("repo_id", repo_id)
        self._recents.add("lerobot_root", out_root)
        if push_only:
            return ["--repo-id", repo_id, "--root", out_root, "--push-only",
                    "--private" if self.private_check.isChecked() else "--no-private"]
        args = list(paths) + [
            "--repo-id", repo_id,
            "--root", out_root,
            "--fps", self.fps_edit.text().strip() or "20",
        ]
        if self.only_success_check.isChecked():
            args.append("--only-success")
        if self.resume_check.isChecked():
            args.append("--resume")
        return args
