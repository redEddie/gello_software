"""Collection plan form editor dialog."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from apps.dialogs.plan_json_dialog import PlanJsonDialog
from gello.gui.gui_widgets import Recents
from gello.gui.i18n import tr
from gello.scene.collection_plan import load_plan
from gello.scene.scene_format import INSTRUCTION_ID_RE, SCENE_ID_RE


class PlanEditDialog(QDialog):
    """수집 계획 편집 — scene 별로 (문장, 목표)만 표에서 고친다.

    나머지는 자동이다: 기존 행은 파일의 instruction_id 를 그대로 유지하고
    (수집된 에피소드와의 연결이 ID 에 걸려 있다), 새 행은 저장 시점에 그
    scene 의 다음 빈 번호를 받는다. 행을 지워도 남은 행의 ID 는 바뀌지
    않고, 지운 ID 번호도 재사용하지 않는다 -- 같은 번호가 다른 문장으로
    되살아나면 이미 수집된 데이터와 어긋난다. note 같은 부가 필드는 그대로
    보존하며, 저장은 여전히 load_plan 검증을 통과해야 반영된다.
    """

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.warnings: list = []
        self.setWindowTitle(tr("수집 계획 편집 — {n}").format(n=self._path.name))
        self.setMinimumSize(720, 480)
        self._cur_sid: "str | None" = None

        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "문장과 목표 개수만 고치면 됩니다. ID 는 자동입니다 — 기존 행은 "
            "번호를 유지하고, 새 행은 저장할 때 다음 번호를 받습니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)

        srow = QHBoxLayout()
        srow.addWidget(QLabel(tr("Scene")))
        self.scene_combo = QComboBox()
        self.scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        srow.addWidget(self.scene_combo, 1)
        add_scene_btn = QPushButton(tr("scene 추가"))
        add_scene_btn.clicked.connect(self._on_add_scene)
        srow.addWidget(add_scene_btn)
        del_scene_btn = QPushButton(tr("scene 삭제"))
        del_scene_btn.setToolTip(tr(
            "이 scene 을 계획에서 뺍니다. 이미 수집한 파일은 지워지지 않지만 "
            "계획 대조가 사라집니다."))
        del_scene_btn.clicked.connect(self._on_del_scene)
        srow.addWidget(del_scene_btn)
        json_btn = QPushButton(tr("JSON 직접 편집..."))
        json_btn.setToolTip(tr("note 등 폼이 다루지 않는 필드를 고칠 때 씁니다."))
        json_btn.clicked.connect(self._on_raw_edit)
        srow.addWidget(json_btn)
        col.addLayout(srow)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("ID"), tr("문장 (instruction)"), tr("목표")])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        col.addWidget(self.tree, 1)

        rrow = QHBoxLayout()
        add_btn = QPushButton(tr("행 추가"))
        add_btn.clicked.connect(lambda: self._add_row(
            {"id": None, "instr": "", "target": 10}))
        rrow.addWidget(add_btn)
        del_btn = QPushButton(tr("선택 행 삭제"))
        del_btn.clicked.connect(self._on_del_row)
        rrow.addWidget(del_btn)
        up_btn = QPushButton("▲")
        up_btn.setMaximumWidth(36)
        up_btn.setToolTip(tr("선택 행을 위로 (표시 순서만 -- ID 는 안 바뀝니다)"))
        up_btn.clicked.connect(lambda: self._move_row(-1))
        rrow.addWidget(up_btn)
        down_btn = QPushButton("▼")
        down_btn.setMaximumWidth(36)
        down_btn.clicked.connect(lambda: self._move_row(+1))
        rrow.addWidget(down_btn)
        compact_btn = QPushButton(tr("번호 정리"))
        compact_btn.setToolTip(tr(
            "이 scene 의 ID 를 표 순서대로 I000..I{N-1} 로 다시 매깁니다.\n"
            "에피소드가 하나도 수집되지 않은 scene 에서만 가능합니다 --\n"
            "수집된 scene 의 ID 는 데이터와의 연결 고리라 재부여하지 않습니다."))
        compact_btn.clicked.connect(self._on_compact_ids)
        rrow.addWidget(compact_btn)
        rrow.addStretch(1)
        col.addLayout(rrow)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#e74c3c;")
        col.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._load_file()

    def _load_file(self) -> None:
        """파일 -> 작업본 -> 표. raw 편집 뒤에도 이걸로 되돌아온다.

        작업본은 scene ID -> [{"id": I000|None, "instr", "target"}] 이고,
        표는 scene 전환 때마다 여기서 다시 그린다.
        """
        try:
            self._raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("읽기 실패"), str(e))
            self._raw = {"plan_version": 1, "scenes": []}
        if not isinstance(self._raw.get("scenes"), list):
            self._raw["scenes"] = []
        self._work = {}
        self._scene_order = []
        for sc in self._raw["scenes"]:
            sid = sc.get("scene_id")
            if not isinstance(sid, str):
                continue
            self._scene_order.append(sid)
            self._work[sid] = [
                {"id": sl.get("instruction_id"),
                 "instr": str(sl.get("instruction", "")),
                 "target": int(sl.get("target") or 1)}
                for sl in sc.get("slots", []) if isinstance(sl, dict)]
        self._cur_sid = None
        self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        for sid in self._scene_order:
            self.scene_combo.addItem(sid)
        self.scene_combo.blockSignals(False)
        self.tree.clear()
        if self._scene_order:
            self._cur_sid = self._scene_order[0]
            self.scene_combo.setCurrentIndex(0)
            self._load_rows(self._cur_sid)

    # ---- 표 <-> 작업본 ----
    def _add_row(self, row: dict) -> None:
        it = QTreeWidgetItem([row["id"] or tr("(자동)"), "", ""])
        it.setData(0, Qt.ItemDataRole.UserRole, row["id"])
        self.tree.addTopLevelItem(it)
        instr = QLineEdit(row["instr"])
        instr.setPlaceholderText(tr("예) pick up the blue cup and place it on the blue bowl"))
        instr.textChanged.connect(self._check_dups)   # 중복은 저장 전에 보이게
        self.tree.setItemWidget(it, 1, instr)
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(max(1, row["target"]))
        self.tree.setItemWidget(it, 2, spin)

    def _collect_rows(self) -> list:
        rows = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            rows.append({"id": it.data(0, Qt.ItemDataRole.UserRole),
                         "instr": self.tree.itemWidget(it, 1).text().strip(),
                         "target": self.tree.itemWidget(it, 2).value()})
        return rows

    def _load_rows(self, sid: str) -> None:
        self.tree.clear()
        for row in self._work.get(sid, []):
            self._add_row(row)

    def _stash_current(self) -> None:
        if self._cur_sid is not None:
            self._work[self._cur_sid] = self._collect_rows()

    def _on_scene_changed(self, *_args) -> None:
        self._stash_current()
        self._cur_sid = self.scene_combo.currentText() or None
        if self._cur_sid is not None:
            self._load_rows(self._cur_sid)

    def _on_del_row(self) -> None:
        for it in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        self._check_dups()

    def _move_row(self, delta: int) -> None:
        idx = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        j = idx + delta
        if idx < 0 or not 0 <= j < self.tree.topLevelItemCount():
            return
        rows = self._collect_rows()
        rows[idx], rows[j] = rows[j], rows[idx]
        self.tree.clear()
        for r in rows:
            self._add_row(r)
        self.tree.setCurrentItem(self.tree.topLevelItem(j))

    def _check_dups(self, *_args) -> None:
        instrs = [r["instr"] for r in self._collect_rows() if r["instr"]]
        dup = sorted({s for s in instrs if instrs.count(s) > 1})
        if dup:
            self.error_label.setText(tr(
                "같은 문장이 여러 행에 있습니다: {s}").format(s=dup[0][:60]))
        elif self.error_label.text().startswith(tr("같은 문장이")):
            self.error_label.setText("")

    def _scene_has_episodes(self, sid: str) -> "bool | None":
        """이 scene 의 수집 파일에 에피소드가 있는가. None = 확인 불가(잠금 등)."""
        try:
            from gello.scene.scene_format import list_scene_episodes, scene_filename

            root = Path(Recents().most_recent("data_root",
                                              str(Path.home() / "libero_datasets")))
            path = root / scene_filename(sid)
            if not path.exists():
                return False
            return len(list_scene_episodes(path)) > 0
        except Exception:  # noqa: BLE001 -- 세션이 쥔 파일 등
            return None

    def _on_compact_ids(self) -> None:
        """빈 scene 한정 ID 압축 (2026-08-24 결정: 데이터가 없으면 번호를
        다시 매겨도 안전하고, 있으면 일관성을 위해 건드리지 않는다)."""
        sid = self._cur_sid
        if sid is None:
            return
        has = self._scene_has_episodes(sid)
        if has is None:
            QMessageBox.warning(self, tr("번호 정리 불가"), tr(
                "{s} 의 수집 파일을 확인할 수 없습니다 (수집 세션이 사용 중일 수 "
                "있음). 세션 종료 후 다시 시도하세요.").format(s=sid))
            return
        if has:
            QMessageBox.warning(self, tr("번호 정리 불가"), tr(
                "{s} 에는 이미 수집된 에피소드가 있습니다. ID 는 데이터와의 "
                "연결 고리라 재부여하지 않습니다 (지운 번호 재사용 금지 규칙)."
            ).format(s=sid))
            return
        rows = self._collect_rows()
        for i, r in enumerate(rows):
            r["id"] = f"I{i:03d}"
        self._work[sid] = rows
        self._load_rows(sid)
        self.error_label.setText("")
        QMessageBox.information(self, tr("번호 정리"), tr(
            "{s} 의 ID 를 I000..I{n:03d} 로 다시 매겼습니다. 저장을 눌러야 "
            "반영됩니다.").format(s=sid, n=len(rows) - 1))

    def _on_del_scene(self) -> None:
        sid = self._cur_sid
        if sid is None:
            return
        ans = QMessageBox.question(
            self, tr("scene 삭제"),
            tr("{s} 를 계획에서 뺄까요? (수집 파일은 그대로 남습니다)")
            .format(s=sid))
        if ans != QMessageBox.StandardButton.Yes:
            return
        i = self._scene_order.index(sid)
        self._scene_order.remove(sid)
        self._work.pop(sid, None)
        self._cur_sid = None
        self.scene_combo.blockSignals(True)
        self.scene_combo.removeItem(i)
        self.scene_combo.blockSignals(False)
        self.tree.clear()
        if self._scene_order:
            self._cur_sid = self.scene_combo.currentText() or None
            if self._cur_sid:
                self._load_rows(self._cur_sid)

    def _on_add_scene(self) -> None:
        used = [int(m.group(1)) for sid in self._scene_order
                if (m := SCENE_ID_RE.match(sid))]
        sid = f"S{(max(used) + 1) if used else 0:03d}"
        self._scene_order.append(sid)
        self._work[sid] = []
        self.scene_combo.addItem(sid)
        self.scene_combo.setCurrentIndex(self.scene_combo.count() - 1)

    def _on_raw_edit(self) -> None:
        dlg = PlanJsonDialog(self, self._path)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # 파일이 정본이므로 폼을 파일 기준으로 다시 세운다 (표의 미저장
        # 수정은 버려진다 -- raw 편집이 이미 파일을 바꿨다)
        self.warnings = list(getattr(dlg, "warnings", []))
        self._load_file()

    # ---- 저장 ----
    def _save(self) -> None:
        import tempfile

        self._stash_current()
        raw = json.loads(json.dumps(self._raw))     # 부가 필드 보존용 사본
        raw.setdefault("plan_version", 1)
        by_id = {s.get("scene_id"): s for s in raw["scenes"]}
        # scene 목록은 폼이 정본 -- 폼에서 지운 scene 은 파일에서도 빠진다
        raw["scenes"] = []
        for sid in self._scene_order:
            sc = by_id.get(sid)
            if sc is None:
                sc = {"scene_id": sid, "slots": []}
            raw["scenes"].append(sc)
            old = {sl.get("instruction_id"): sl
                   for sl in sc.get("slots", []) if isinstance(sl, dict)}
            rows = self._work.get(sid, [])
            # 지워진 ID 도 사용된 번호로 친다 -- 번호 재사용 금지
            used = {int(m.group(1)) for iid in
                    list(old) + [r["id"] for r in rows if r["id"]]
                    if (m := INSTRUCTION_ID_RE.match(iid or ""))}
            slots = []
            for r in rows:
                if not r["id"]:
                    n = max(used, default=-1) + 1
                    used.add(n)
                    r["id"] = f"I{n:03d}"
                sl = dict(old.get(r["id"], {}))
                sl["instruction_id"] = r["id"]
                sl["instruction"] = r["instr"]
                sl["target"] = r["target"]
                slots.append(sl)
            sc["slots"] = slots
        text = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
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


