"""New scene composition dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from apps.workspace.shared.widgets import SceneInfoView
from apps.workspace.features.scene.dialogs.recommend_dialog import RecommendDialog
from gello.data.dataset_schema import SCHEMA_VERSION
from gello.gui.i18n import tr
from gello.scene.props import load_props, props_by_id
from gello.scene.scene_format import (
    SceneMetadata,
    describe_scene,
    iter_scene_files,
    read_scene_metadata,
)
from gello.scene.scene_rules import check, object_count_range


class NewSceneDialog(QDialog):
    """새 scene 구성 — 소품 선택 + 3×3 존 배치 + 설명 + 규칙 lint."""

    def __init__(self, parent, scene_id: str,
                 data_root: "Path | None" = None,
                 plan_path: "Path | None" = None,
                 station_name: str = "",
                 schema_version: str = SCHEMA_VERSION) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("새 Scene 구성 — {sid}").format(sid=scene_id))
        self.setMinimumWidth(720)
        self._scene_id = scene_id
        self._data_root = data_root
        self._plan_path = plan_path
        self._station_name = station_name
        self._schema_version = schema_version
        self._placements: dict = {}
        self.metadata = None  # accept 시 SceneMetadata

        layout = QVBoxLayout(self)
        hrow = QHBoxLayout()
        hint = QLabel(tr(
            "① 포함할 물체를 체크  ② 목록에서 물체를 클릭해 선택  "
            "③ 오른쪽 격자 칸을 눌러 그 존에 배치  ([0,0]=왼쪽 위)"))
        hint.setWordWrap(True)
        hrow.addWidget(hint, 1)
        # Action 계층은 영어 (i18n.py 정책) -- 두 버튼이 나란히 있어 한쪽만
        # 영어면 오히려 섞여 보인다.
        rec_btn = QPushButton(tr("Recommend scene..."))
        rec_btn.setToolTip(tr(
            "기존 scene 들과 가장 다른 소품 조합·배치 3안을 추천받아\n"
            "체크·배치를 자동으로 채웁니다 (#33, 다양성 최대화)."))
        rec_btn.clicked.connect(self._on_recommend)
        hrow.addWidget(rec_btn)
        self.layout_btn = QPushButton(tr("Recommend layout..."))
        self.layout_btn.clicked.connect(self._on_recommend_layout)
        hrow.addWidget(self.layout_btn)
        layout.addLayout(hrow)

        mid = QHBoxLayout()
        self.prop_list = QListWidget()
        for p in load_props():
            if p.retired:
                continue
            it = QListWidgetItem(f"{p.id}  ({p.category} · {p.color})")
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.prop_list.addItem(it)
        self.prop_list.itemChanged.connect(self._refresh)
        mid.addWidget(self.prop_list, 3)

        grid = QGridLayout()
        self.zone_buttons = {}
        for r in range(3):
            for c in range(3):
                b = QPushButton("")
                b.setMinimumSize(100, 56)
                b.setToolTip(tr("존 [{r},{c}] 에 선택한 물체 배치").format(r=r, c=c))
                b.clicked.connect(lambda _=False, rc=(r, c): self._assign(rc))
                grid.addWidget(b, r, c)
                self.zone_buttons[(r, c)] = b
        mid.addLayout(grid, 4)
        layout.addLayout(mid)

        form = QFormLayout()
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText(
            tr("배치 의도, 지칭하지 않는 물체 등 — 사람용 자유 문장"))
        form.addRow(tr("설명"), self.desc_edit)
        layout.addLayout(form)

        self.lint_label = QLabel("")
        self.lint_label.setWordWrap(True)
        self.lint_label.setStyleSheet("color:#e67e22;")
        layout.addWidget(self.lint_label)

        self.preview = SceneInfoView()
        layout.addWidget(self.preview)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _checked_ids(self) -> list:
        return [self.prop_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.prop_list.count())
                if self.prop_list.item(i).checkState() == Qt.CheckState.Checked]

    def _existing_scenes(self) -> tuple:
        existing = []
        skipped = 0
        if self._data_root is not None:
            for p in iter_scene_files(self._data_root):
                try:
                    existing.append(read_scene_metadata(p))
                except Exception:  # noqa: BLE001 -- 세션이 쥔 파일 등
                    skipped += 1
        return existing, skipped

    def _open_recommend(self, objects: "list | None") -> None:
        existing, skipped = self._existing_scenes()
        dlg = RecommendDialog(self, existing, props_by_id(), self._scene_id,
                              plan_path=self._plan_path,
                              data_root=self._data_root, objects=objects)
        if skipped:
            dlg.setWindowTitle(dlg.windowTitle()
                               + tr(" (읽지 못한 파일 {n}개 제외)").format(n=skipped))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.picked is not None:
            self._apply_recommendation(dlg.picked)

    def _on_recommend(self) -> None:
        self._open_recommend(None)

    def _on_recommend_layout(self) -> None:
        """체크한 물체는 그대로 두고 배치만 추천받는다."""
        self._open_recommend(self._checked_ids())

    def _apply_recommendation(self, md) -> None:
        """추천안을 체크박스·배치에 반영한다. 이후 손으로 고칠 수 있다."""
        want = set(md.objects)
        self.prop_list.blockSignals(True)
        for i in range(self.prop_list.count()):
            it = self.prop_list.item(i)
            oid = it.data(Qt.ItemDataRole.UserRole)
            it.setCheckState(Qt.CheckState.Checked if oid in want
                             else Qt.CheckState.Unchecked)
        self.prop_list.blockSignals(False)
        self._placements = {oid: list(spec["zone"]) for oid, spec
                            in md.layout.get("placements", {}).items()}
        self._refresh()

    def _assign(self, rc) -> None:
        it = self.prop_list.currentItem()
        if it is None:
            return
        it.setCheckState(Qt.CheckState.Checked)  # 배치 = 포함 의사
        self._placements[it.data(Qt.ItemDataRole.UserRole)] = [rc[0], rc[1]]
        self._refresh()

    def _build(self) -> SceneMetadata:
        return SceneMetadata(
            scene_id=self._scene_id,
            objects=self._checked_ids(),
            layout={"grid": [3, 3],
                    "placements": {o: {"zone": z}
                                   for o, z in self._placements.items()}},
            description=self.desc_edit.text().strip(),
            station=self._station_name,
            # 세션 버전을 그대로 -- 이어 찍는 데이터셋과 같은 모양이어야 한다.
            dataset_version=self._schema_version,
        )

    def _refresh(self, *_args) -> None:
        checked = set(self._checked_ids())
        self._update_layout_btn(len(checked))
        self._placements = {k: v for k, v in self._placements.items()
                            if k in checked}
        for (r, c), b in self.zone_buttons.items():
            here = [o.replace("OBJ-", "") for o, z in self._placements.items()
                    if z == [r, c]]
            b.setText("\n".join(here))
        md = None
        try:
            md = self._build()
            self.preview.setText(describe_scene(md))
        except Exception:  # noqa: BLE001 - 미완성 구성의 미리보기는 없어도 된다
            self.preview.setText("")
            self.lint_label.setText("")
            return
        # 규칙 lint (경고만)
        try:
            violations = check(md, props_by_id())
            if violations:
                self.lint_label.setText(tr("규칙 경고: ") + "; ".join(violations))
            else:
                self.lint_label.setText("")
        except Exception as e:  # noqa: BLE001
            self.lint_label.setText(tr("규칙 검사 오류: {e}").format(e=e))

    def _update_layout_btn(self, n: int) -> None:
        """왜 못 누르는지를 그 자리에서 말한다 (숨기지 않는다)."""
        lo, _hi = object_count_range()
        cap = 3 * 3
        ok = lo <= n <= cap
        self.layout_btn.setEnabled(ok)
        if ok:
            self.layout_btn.setToolTip(tr(
                "체크한 물체 {n}개는 그대로 두고, 규칙을 만족하는 배치 전부에서\n"
                "기존 scene 들과 가장 다른 배치 3안을 추천받습니다.").format(n=n))
        elif n < lo:
            self.layout_btn.setToolTip(tr(
                "물체를 {lo}개 이상 체크해야 배치를 추천할 수 있습니다 "
                "(지금 {n}개).").format(lo=lo, n=n))
        else:
            self.layout_btn.setToolTip(tr(
                "물체 {n}개는 3×3 격자 {cap}칸보다 많습니다.")
                .format(n=n, cap=cap))

    def _accept(self) -> None:
        md = self._build()
        try:
            from gello.scene.props import active_prop_ids

            md.validate(known_prop_ids=active_prop_ids())
        except ValueError as e:
            QMessageBox.warning(self, tr("Scene 구성 오류"), str(e))
            return
        self.metadata = md
        super().accept()

