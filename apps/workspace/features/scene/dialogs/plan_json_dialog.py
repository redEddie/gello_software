"""Raw JSON editor for collection plans."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QMessageBox,
)

from gello.gui.fonts import MONO_STACK
from gello.gui.i18n import tr
from gello.scene.collection_plan import load_plan


class PlanJsonDialog(QDialog):
    """수집 계획 원문(JSON) 편집 — 저장하려면 load_plan 검증을 통과해야 한다.

    기본 편집기는 폼 방식의 PlanEditDialog 다. 이것은 note 추가처럼 폼이
    다루지 않는 필드를 만질 때 쓰는 고급 진입로로만 남아 있다.
    """

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(tr("수집 계획 JSON 편집 — {n}").format(n=self._path.name))
        self.setMinimumSize(680, 480)
        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "저장하면 규칙 검증(scene 내 ID 유일, 따옴표 금지, target>0)을 "
            "통과해야 반영됩니다. 동사 집합(§4) 밖 문장은 경고만 합니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(
            f"font-family: {MONO_STACK}; font-size: 12px;")
        try:
            self.editor.setPlainText(self._path.read_text(encoding="utf-8"))
        except OSError as e:
            self.editor.setPlainText("")
            QMessageBox.warning(self, tr("읽기 실패"), str(e))
        col.addWidget(self.editor, 1)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#e74c3c;")
        col.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

    def _save(self) -> None:
        text = self.editor.toPlainText()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as tf:
                tf.write(text)
                tmp = Path(tf.name)
            plan = load_plan(tmp)
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            self.error_label.setText(f"{type(e).__name__}: {e}")
            return
        self._path.write_text(text, encoding="utf-8")
        self.warnings = plan.warnings
        super().accept()
