"""Scene 탭 -- 새 scene 구성 (소품 선택 + 3×3 존 배치 + 규칙 lint).

옛 ``NewSceneDialog`` 이다. 대화상자였던 것을 ② Configure 의 중앙 탭으로
옮겼다 (2026-09-06 사용자 요청: "새 scene 구성 또한 plan 탭처럼 탭으로").

왜 탭이 나은가: 새 scene 을 짜는 동안 조작자는 **실제 책상 위 물체를 옮기고
있다**. 모달 대화상자는 그동안 창의 나머지를 전부 막는데, 정작 그때 보고
싶은 것이 옆의 Instruction 탭(이 scene 에 무슨 지시문이 몇 개 남았나)과
카메라다. 탭이면 오가며 짤 수 있다.

**동작 버튼은 여기 없다 -- 우측 패널에 있다** (2026-09-07 사용자 결정,
규칙의 정본은 layout.build_right). 이 위젯은 "무엇을 짜고 있나" 를 그리고
그 상태를 ``changed`` 로 알린다. 만들기·추천을 누르는 자리는 오른쪽이다.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
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
    QWidget,
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
    scene_filename,
)
from gello.scene.scene_rules import check, object_count_range


class SceneComposer(QWidget):
    """소품 선택 + 3×3 배치 + 규칙 lint. 탭이 이것을 담는다.

    동작 버튼은 우측 패널이 갖는다. 이 위젯은 구성이 바뀔 때마다
    ``changed`` 를 쏘고, 패널은 그때 버튼의 상태를 다시 묻는다."""

    #: 체크·배치·설명·scene 번호 중 하나라도 바뀌었다.
    changed = pyqtSignal()

    def __init__(self, parent=None, scene_id: str = "S000",
                 data_root: "Path | None" = None,
                 plan_path: "Path | None" = None,
                 station_name: str = "",
                 schema_version: str = SCHEMA_VERSION) -> None:
        super().__init__(parent)
        self._scene_id = scene_id
        self._data_root = data_root
        self._plan_path = plan_path
        self._station_name = station_name
        self._schema_version = schema_version
        self._placements: dict = {}
        self.metadata = None  # accept 시 SceneMetadata

        layout = QVBoxLayout(self)
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight:bold;")
        layout.addWidget(self.title_label)
        hrow = QHBoxLayout()
        hint = QLabel(tr(
            "① 포함할 물체를 체크  ② 목록에서 물체를 클릭해 선택  "
            "③ 오른쪽 격자 칸을 눌러 그 존에 배치  ([0,0]=왼쪽 위)"))
        hint.setWordWrap(True)
        hrow.addWidget(hint, 1)
        # [Recommend scene...]·[Recommend layout...]·[만들기] 는 우측 패널로
        # 갔다 (2026-09-07). 이 탭에는 격자 칸 버튼만 9개가 남는데, 그것들은
        # "일" 이 아니라 직접 조작이다 -- 누르면 그 칸에 물체가 놓인다.
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

        self._refresh()

    def set_context(self, scene_id: str, data_root: "Path | None",
                    plan_path: "Path | None", station_name: str,
                    schema_version: str) -> None:
        """탭은 한 번 만들어 계속 쓴다 -- 다음 scene 번호와 데이터셋 경로는
        열 때마다 달라지므로 여기서 갈아 끼운다."""
        self._scene_id = scene_id
        self._data_root = data_root
        self._plan_path = plan_path
        self._station_name = station_name
        self._schema_version = schema_version
        self.title_label.setText(
            tr("새 Scene {sid} 구성").format(sid=scene_id))
        self._refresh()

    def build_valid(self):
        """규칙까지 통과한 SceneMetadata, 아니면 None (사유는 대화상자로)."""
        md = self._build()
        try:
            from gello.scene.props import active_prop_ids

            md.validate(known_prop_ids=active_prop_ids())
        except ValueError as e:
            QMessageBox.warning(self, tr("Scene 구성 오류"), str(e))
            return None
        return md

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

    def open_recommend_scene(self) -> None:
        """소품 조합부터 통째로 추천받는다 (우측 패널이 부른다)."""
        self._open_recommend(None)

    def open_recommend_layout(self) -> None:
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
        try:
            self._redraw()
        finally:
            # 우측 패널의 버튼은 이 신호로만 산다 -- 미리보기가 실패해도
            # 반드시 쏜다. 안 그러면 버튼이 옛 상태로 굳는다.
            self.changed.emit()

    def _redraw(self) -> None:
        checked = set(self._checked_ids())
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

    # ---- 우측 패널이 묻는 것 -------------------------------------------
    #
    # 버튼은 오른쪽에 있지만 **누를 수 있는지를 아는 것은 여기**다. 패널은
    # ``changed`` 를 받을 때마다 아래 둘을 물어 상태를 갈아 끼운다. 규칙은
    # 하나다: 못 누르면 왜 못 누르는지를 툴팁이 말한다 (숨기지 않는다).

    @property
    def scene_id(self) -> str:
        """지금 짜고 있는 scene 번호 -- 버튼 라벨에 들어간다."""
        return self._scene_id

    def create_button_state(self) -> tuple:
        """[만들기] 의 (누를 수 있나, 툴팁).

        여기서 ``validate`` 를 미리 돌리는 이유: 전에는 누른 **뒤에**
        대화상자로 야단쳤다. 못 누르는 버튼이 왜 못 누르는지 말하는 편이
        누르게 해 놓고 거절하는 것보다 낫다.
        """
        try:
            md = self._build()
        except Exception as e:  # noqa: BLE001
            return False, tr("구성을 읽지 못했습니다: {e}").format(e=e)
        try:
            from gello.scene.props import active_prop_ids

            md.validate(known_prop_ids=active_prop_ids())
        except ValueError as e:
            return False, str(e)
        return True, tr(
            "{f} 를 지금 만듭니다 (에피소드 0개).\n"
            "여러 개를 미리 만들어 두고 나중에 골라 찍을 수 있습니다.\n"
            "잘못 만들었으면 Dataset 의 [파일 삭제] 로 지웁니다."
        ).format(f=scene_filename(self._scene_id))

    def layout_button_state(self) -> tuple:
        """[Recommend layout...] 의 (누를 수 있나, 툴팁)."""
        n = len(self._checked_ids())
        lo, _hi = object_count_range()
        cap = 3 * 3
        if n < lo:
            return False, tr(
                "물체를 {lo}개 이상 체크해야 배치를 추천할 수 있습니다 "
                "(지금 {n}개).").format(lo=lo, n=n)
        if n > cap:
            return False, tr(
                "물체 {n}개는 3×3 격자 {cap}칸보다 많습니다."
            ).format(n=n, cap=cap)
        return True, tr(
            "체크한 물체 {n}개는 그대로 두고, 규칙을 만족하는 배치 전부에서\n"
            "기존 scene 들과 가장 다른 배치 3안을 추천받습니다.").format(n=n)
