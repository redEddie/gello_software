"""Launcher wizard pages — mode / continue / new dataset / hardware."""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from gello.comm.camera_client import set_cams
from gello.gui.workers import CameraPreviewWorker
from apps.workspace.launcher.camera_panel import CameraPreviewColumn
from apps.workspace.shared.robot_node_proc import (
    spawn_node as spawn_robot_node,
)
from apps.workspace.shared.camera_node_proc import (
    node_specs,
    spawn_node,
    spec_key,
)
from gello.config.station import CameraSpec, load_station
from apps.workspace.constants import WT_ROOT
from apps.workspace.shared.sizing import shrinkable_combo
from apps.workspace.launcher.station_editor import StationEditor
import numpy as np

from gello.comm.zmq_core.robot_node import probe_observation
from gello.data.dataset_schema import (
    FT_OBS_KEYS,
    SCHEMA_FIELDS,
    SCHEMA_VERSION,
    schema_required_fields,
)
from gello.scene.dataset_meta import (
    DEFAULT_DATASETS_PARENT,
    DatasetEntry,
    schema_version_spans,
    discover_datasets,
    load_identity,
    plan_progress,
    validate_dataset_name,
)
from gello.gui.i18n import tr
from gello.gui.widgets import Recents

# 페이지 ID — wizard.py 의 nextId 분기가 쓴다.
PAGE_MODE = 0
PAGE_CONTINUE = 1
PAGE_NEW = 2
PAGE_HW = 3

_NO_CAMERA = ""      # "(선택 안함)" 항목의 data

#: 버전이 요구하는 **로봇 관측 키**. HDF5 필드명과 같지만 층이 다르다 --
#: 이쪽은 "로봇이 줘야 하는 값" 이고, 확인 버튼이 이것으로 검사한다.
#: 포스·토크는 FR3 펌웨어가 노출할 때만 오므로 장비마다 다를 수 있다.
#: [확인] 이 로봇 노드를 직접 띄웠을 때 기다려 주는 시간(초). FCI 연결 +
#: 첫 read_once 까지가 대략 3~5초라 그보다 넉넉히 둔다. 넘기면 포기하고
#: 경고만 남긴다 -- 확인은 선택이지 진행 조건이 아니다.
_ROBOT_NODE_WAIT_S = 15.0

_ROBOT_OBS_FIELDS = {
    "knu-1.0.0": (),
    "knu-1.1.0": FT_OBS_KEYS,
}

# 카메라 역할은 더 이상 여기 고정돼 있지 않다 -- 스테이션이 정한다
# (cam id -> role). 3층 분리: 하드웨어=시리얼 / 데이터세트=역할 /
# 인터페이스=cam id (2026-09-05).


class ModePage(QWizardPage):
    """첫 화면 — 버튼 2개만. 클릭이 곧 분기 이동이라 Next 는 숨긴다
    (wizard.currentIdChanged 에서 처리)."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle(tr("데이터 수집 시작"))
        col = QVBoxLayout(self)
        col.addStretch()
        big = QFont("", 14, QFont.Weight.Bold)
        self.continue_btn = QPushButton(tr("이어서 하기"))
        self.new_btn = QPushButton(tr("새 데이터세트"))
        for b, tip in ((self.continue_btn,
                        tr("기존 데이터셋을 골라 바로 수집을 시작합니다")),
                       (self.new_btn,
                        tr("이름·컨셉·저장 위치를 정해 새 데이터셋을 만듭니다"))):
            b.setFont(big)
            b.setMinimumHeight(80)
            b.setToolTip(tip)
            col.addWidget(b)
        col.addStretch()
        self.continue_btn.clicked.connect(lambda: self._go("continue"))
        self.new_btn.clicked.connect(lambda: self._go("new"))

    def _go(self, mode: str) -> None:
        self.wizard().mode = mode
        self.wizard().next()

    def nextId(self) -> int:  # noqa: N802 - Qt override
        return PAGE_CONTINUE if self.wizard().mode == "continue" else PAGE_NEW

    def isComplete(self) -> bool:  # noqa: N802 - Qt override
        return False    # 이 페이지는 Next 로 못 간다 -- 버튼 2개가 유일한 출구


class ContinuePage(QWizardPage):
    """이어서 하기 — 발견된 데이터셋 목록에서 하나를 고른다."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle(tr("이어서 하기"))
        self.setSubTitle(tr("수집을 이어갈 데이터셋을 선택하세요."))
        col = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda *_: self.completeChanged.emit())
        col.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#888;")
        row.addWidget(self.hint, 1)
        refresh = QPushButton(tr("새로고침"))
        refresh.clicked.connect(self.reload)
        row.addWidget(refresh)
        col.addLayout(row)
        self._entries: list[DatasetEntry] = []

    def initializePage(self) -> None:  # noqa: N802 - Qt override
        self.reload()

    def reload(self) -> None:
        recents = Recents()
        candidates = [Path(r) for r in recents.get("data_root")]
        candidates.append(DEFAULT_DATASETS_PARENT)
        self._entries = discover_datasets(candidates)
        self.list.clear()
        for e in self._entries:
            parts = [f"scene {e.scene_files}개", f"에피소드 {e.episodes}개"]
            prog = plan_progress(e.path)
            if prog is not None:
                d, t = prog
                pct = (100 * d // t) if t else 0
                parts.append(tr("계획 {d}/{t} ({p}%)").format(d=d, t=t, p=pct))
            else:
                parts.append(tr("계획 없음"))
            if e.mtime:
                parts.append(time.strftime("%Y-%m-%d", time.localtime(e.mtime)))
            title = e.name
            if e.identity is None:
                title += tr("  (메타 없음)")
            item = QListWidgetItem(f"{title}\n{' · '.join(parts)}")
            concept = e.identity.concept if e.identity else ""
            item.setToolTip(concept or str(e.path))
            self.list.addItem(item)
        # 가장 최근에 쓴 data_root 를 미리 선택
        want = recents.most_recent("data_root", "")
        for i, e in enumerate(self._entries):
            if str(e.path) == want:
                self.list.setCurrentRow(i)
                break
        else:
            if self.list.count():
                self.list.setCurrentRow(0)
        self.hint.setText(tr("{n}개 데이터셋 발견").format(n=self.list.count()))
        self.completeChanged.emit()

    def selected_path(self) -> "Path | None":
        row = self.list.currentRow()
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row].path

    def isComplete(self) -> bool:  # noqa: N802 - Qt override
        return self.selected_path() is not None

    def nextId(self) -> int:  # noqa: N802 - Qt override
        return PAGE_HW


class NewDatasetPage(QWizardPage):
    """새 데이터세트 — 이름/위치/컨셉 + 기존 데이터셋 설정 복사."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle(tr("새 데이터세트"))
        self.setSubTitle(tr("데이터셋 이름·컨셉·저장 위치를 정합니다. "
                            "폴더와 dataset-identity.json 이 만들어집니다."))
        form = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("fr3-tabletop")
        form.addRow(tr("이름"), self.name_edit)
        loc_row = QHBoxLayout()
        self.location_edit = QLineEdit(str(DEFAULT_DATASETS_PARENT))
        loc_row.addWidget(self.location_edit, 1)
        browse = QPushButton("...")
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse)
        loc_row.addWidget(browse)
        form.addRow(tr("저장 위치"), loc_row)
        self.preview = QLabel("")
        self.preview.setStyleSheet("color:#888;")
        form.addRow(tr("생성될 경로"), self.preview)
        self.copy_combo = QComboBox()
        self.copy_combo.currentIndexChanged.connect(self._on_copy_pick)
        form.addRow(tr("설정 가져오기"), self.copy_combo)
        self.concept_edit = QPlainTextEdit()
        self.concept_edit.setPlaceholderText(
            tr("이 데이터셋이 어떤 태스크·장면을 모으는지 (업로드 시 설명으로 쓰입니다)"))
        self.concept_edit.setMaximumHeight(110)
        form.addRow(tr("컨셉"), self.concept_edit)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#e74c3c;")
        self.error.setWordWrap(True)
        form.addRow(self.error)
        self._entries: list[DatasetEntry] = []
        for w in (self.name_edit, self.location_edit):
            w.textChanged.connect(self._validate)

    def initializePage(self) -> None:  # noqa: N802 - Qt override
        recents = Recents()
        candidates = [Path(r) for r in recents.get("data_root")]
        candidates.append(DEFAULT_DATASETS_PARENT)
        self._entries = discover_datasets(candidates)
        self.copy_combo.blockSignals(True)
        self.copy_combo.clear()
        self.copy_combo.addItem(tr("(비어 있게 시작)"), None)
        for e in self._entries:
            # data 는 str 로 -- Path 객체는 findData 가 identity 비교라 못 찾는다
            self.copy_combo.addItem(e.name, str(e.path))
        self.copy_combo.blockSignals(False)
        # 직전 저장 위치의 부모를 기본값으로
        last = recents.most_recent("data_root", "")
        if last:
            self.location_edit.setText(str(Path(last).parent))
        self._validate()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("저장 위치 선택"),
                                             self.location_edit.text())
        if d:
            self.location_edit.setText(d)

    def target_path(self) -> Path:
        return Path(self.location_edit.text().strip() or ".") / self.name_edit.text().strip()

    def _validate(self) -> None:
        name = self.name_edit.text().strip()
        err = validate_dataset_name(name) if name else tr("이름을 입력하세요.")
        target = self.target_path()
        self.preview.setText(str(target))
        if err is None and target.exists() and any(target.iterdir()):
            err = tr("이미 존재하는 폴더이고 비어 있지 않습니다.")
        if err is None and not Path(self.location_edit.text().strip()).is_dir():
            err = tr("저장 위치(부모 폴더)가 존재하지 않습니다.")
        self.error.setText(err or "")
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 - Qt override
        return not self.error.text()

    def _on_copy_pick(self, idx: int) -> None:
        path = self.copy_combo.itemData(idx)
        if path is None:
            return
        entry = next((e for e in self._entries if e.path == Path(path)), None)
        if entry is not None and entry.identity is not None and entry.identity.concept:
            self.concept_edit.setPlainText(entry.identity.concept)

    def copy_source(self) -> "Path | None":
        """설정을 복사해올 원본 데이터셋 폴더 (없으면 None)."""
        data = self.copy_combo.currentData()
        return Path(data) if data else None

    def nextId(self) -> int:  # noqa: N802 - Qt override
        return PAGE_HW


class HardwarePage(QWizardPage):
    """하드웨어 — 스테이션과 카메라. 여기서 고른 station 이 3×3 격자
    저장소(grids/<station>.json)도 결정한다."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle(tr("하드웨어"))
        self.setSubTitle(tr("수집 스테이션과 카메라를 선택하세요."))
        # 왼쪽은 미리보기, 오른쪽은 설정. 아래로 쌓지 않는 이유는 둘이다:
        # 모니터가 16:9 라 세로가 아쉽고, 무엇보다 칼럼이 갈려 있어야 "여기는
        # 보는 곳, 저기는 설정하는 곳"이 한눈에 읽힌다 (2026-09-05 사용자 결정).
        # 예전엔 한 줄에 [콤보][미리보기] 를 짝지어 두었는데, 역할이 셋만 되어도
        # 둘이 번갈아 나와 못 읽고, 긴 콤보에 밀려 미리보기가 9px 세로줄로
        # 잘리기까지 했다.
        #
        # 설정을 오른쪽에 두는 이유: 글은 왼쪽에서 시작해 오른쪽으로 흐르므로,
        # 값이 길어질 때(경로·설명) 오른쪽이 자유롭게 넓어지는 것이 자연스럽다.
        # 그래서 늘어나는 몫(stretch)도 설정 쪽이 크다.
        two_col = QHBoxLayout(self)
        self.preview_column = CameraPreviewColumn()
        two_col.addWidget(self.preview_column, 2)
        right = QWidget()
        outer = QVBoxLayout(right)
        outer.setContentsMargins(0, 0, 0, 0)
        two_col.addWidget(right, 3)
        self.station_editor = StationEditor()
        self.station_editor.save_requested.connect(self._on_save_station)
        outer.addWidget(self.station_editor)
        self.station_editor.cams_changed.connect(self._rebuild_cam_rows)
        # 커밋 안 된 스테이션 파일이 있으면 아이콘의 자동 git pull 이 건너뛰어진다.
        # 막지는 않고 알리기만 한다 (2026-09-05 사용자 결정).
        # 데이터세트 버전. 한 데이터셋에 여러 버전이 섞이는 것을 허용하되
        # (2026-09-05 결정), **기본값은 최신**이다 -- 새 필드를 쓰기 시작했으면
        # 버전이 따라 올라가는 것이 맞고, 옛 버전으로 이어 찍고 싶으면 여기서
        # 명시적으로 내린다. 언제 바뀌었는지는 파일에서 파생해 보여준다.
        ds_box = QGroupBox(tr("데이터세트"))
        ds_col = QVBoxLayout(ds_box)
        ver_row = QHBoxLayout()
        ver_row.addWidget(QLabel(tr("스키마 버전")))
        self.schema_combo = QComboBox()
        for v in sorted(SCHEMA_FIELDS):
            self.schema_combo.addItem(v, v)
        self.schema_combo.currentIndexChanged.connect(self._refresh_schema_label)
        ver_row.addWidget(self.schema_combo, 1)
        self.schema_test_btn = QPushButton(tr("확인"))
        self.schema_test_btn.setToolTip(tr(
            "로봇 노드에 관측을 한 번 요청해, 이 버전이 요구하는 필드가 실제로 "
            "오는지 확인합니다. 노드가 안 떠 있으면 여기서 띄웁니다 (FCI 필요)."))
        self.schema_test_btn.clicked.connect(self._on_schema_selftest)
        ver_row.addWidget(self.schema_test_btn)
        ds_col.addLayout(ver_row)
        self.schema_label = QLabel("")
        self.schema_label.setWordWrap(True)
        self.schema_label.setStyleSheet("color:#888;")
        ds_col.addWidget(self.schema_label)
        self.schema_test_label = QLabel("")
        self.schema_test_label.setWordWrap(True)
        self.schema_test_label.setStyleSheet("color:#888;")
        ds_col.addWidget(self.schema_test_label)
        outer.addWidget(ds_box)
        self.git_warn = QLabel("")
        self.git_warn.setWordWrap(True)
        self.git_warn.setStyleSheet("color:#e67e22;")
        # cam id -> 시리얼. 스테이션이 정한 cam 줄을 따라간다 (역할은 저쪽,
        # 실물 바인딩은 여기). 이쪽은 언제나 편집 가능하다 -- 카메라를 바꿔
        # 꽂는 것은 흔한 일이고, 그 기록은 데이터셋에 남는다.
        self.combos: dict[str, QComboBox] = {}
        self.previews: dict = {}
        self._entries: list[tuple[str, str]] = []   # (serial, label)
        self._preview_workers: dict[str, CameraPreviewWorker] = {}
        # cam id -> 지금 그 화면이 붙어 있는 시리얼. 바뀐 것만
        # 다시 걸기 위해 필요하다.
        self._preview_serial: dict[str, str] = {}
        cam_box = QGroupBox(tr("카메라 (cam id → 실물)"))
        cam_col = QVBoxLayout(cam_box)
        self.cam_form = QFormLayout()
        cam_col.addLayout(self.cam_form)
        row = QHBoxLayout()
        detect = QPushButton(tr("카메라 감지"))
        detect.clicked.connect(self.detect_cameras)
        row.addWidget(detect)
        self.cam_hint = QLabel("")
        self.cam_hint.setStyleSheet("color:#888;")
        row.addWidget(self.cam_hint, 1)
        cam_col.addLayout(row)
        outer.addWidget(cam_box)
        # StationEditor 는 생성 중에 이미 cams_changed 를 냈다 (reload ->
        # _on_pick -> _fill_from). 그 신호는 연결 전이라 놓쳤으므로 여기서
        # 한 번 직접 그린다 -- 안 그러면 두 박스가 빈 채로 뜬다.
        self._rebuild_cam_rows()
        outer.addWidget(self.git_warn)
        outer.addStretch(1)

        # 시리얼도 모델명도 "어느 쪽이 손목인지"는 안 알려준다. 특히 같은
        # 모델이 두 대면 구별할 방법이 없다. 그림이면 즉시 갈린다 -- 이걸
        # 위해 이 페이지에서 카메라 노드를 띄운다(_on_camera_pick).
        self.station_editor.combo.currentIndexChanged.connect(
            self._apply_station_defaults)
        # 이 페이지가 띄운 노드. 마법사가 끝나면 워크스페이스가 물려받는다
        # (take_node) -- 넘기지 않으면 창이 같은 카메라를 두 번 열려다
        # 포트 6021 충돌로 죽는다.
        self._node = None
        self._node_key = ""
        #: 버전 [확인] 이 띄운 로봇 노드. 워크스페이스가 이어받는다 -- FCI 는
        #: 클라이언트 하나만 받으므로, 확인용으로 띄우고 두면 창에서 다시
        #: 띄울 때 실패한다.
        self._robot_node = None

    def initializePage(self) -> None:  # noqa: N802 - Qt override
        import os
        # 이 페이지에 들어오는 데 3~8초가 걸렸다 (2026-09-05). 카메라 USB
        # 열거가 느린 것도 있지만, 진짜 원인은 콤보를 다시 채울 때마다
        # currentIndexChanged 가 터져 _on_camera_pick 이 여러 번 불린 것이다 --
        # 매번 노드를 죽이고(waitForFinished 3초 + kill 2초) 다시 띄웠다.
        # 목록을 만드는 동안은 신호를 막고, 노드는 마지막에 한 번만 맞춘다.
        #
        # 그 사이 클릭이 큐에 쌓였다가 한꺼번에 들어와 설정을 확인하지도 못하고
        # 넘어가는 일이 있었다. 마법사를 비활성으로 두면 Qt 가 그 클릭을
        # 큐에 쌓지 않고 버린다 -- 대기 커서와 함께.
        wiz = self.wizard()
        if wiz is not None:
            wiz.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()   # 비활성·커서를 먼저 화면에 반영
        try:
            # 기본 선택: 이어서 수집이면 그 데이터세트가 쓰던 스테이션.
            want = self._wanted_station() or os.environ.get("GELLO_STATION", "")
            if want:
                self.station_editor.reload(select=want)
            self._refresh_git_warning()
            # 기본은 최신 버전 -- 옛 버전으로 이어 찍으려면 명시적으로 내린다.
            i = self.schema_combo.findData(SCHEMA_VERSION)
            self.schema_combo.setCurrentIndex(max(0, i))
            self._refresh_schema_label()
            # 목록부터 만들고(모델명 포함) 그 안에서 기억된 것을 고른다. 버튼을
            # 눌러야만 모델명이 보이면 아무도 안 누른다.
            with self._combos_quiet():
                self.detect_cameras()
                self._apply_station_defaults()
            self._on_camera_pick()
        finally:
            QApplication.restoreOverrideCursor()
            if wiz is not None:
                wiz.setEnabled(True)

    @contextmanager
    def _combos_quiet(self):
        """콤보를 다시 채우는 동안 currentIndexChanged 를 막는다."""
        for c in self.combos.values():
            c.blockSignals(True)
        try:
            yield
        finally:
            for c in self.combos.values():
                c.blockSignals(False)

    # ----------------------------------------------------------- cam 줄
    def cam_ids(self) -> list:
        """스테이션이 정한 cam 순서. 시리얼 줄과 미리보기가 이것을 따른다."""
        return list(self.station_editor.cam_roles())

    def _rebuild_cam_rows(self) -> None:
        """스테이션의 cam 목록이 바뀌면 시리얼 줄과 미리보기를 맞춘다."""
        keep = self.serials()
        while self.cam_form.count():
            it = self.cam_form.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.combos = {}
        roles = self.station_editor.cam_roles()
        for cam in roles:
            combo = QComboBox()
            combo.setEditable(True)
            # 항목 문자열("Intel RealSense D405 (2304...)")이 콤보의 최소 폭을
            # 정하게 두면 옆칸을 밀어낸다 -- 줄여서 말줄임하게 한다.
            shrinkable_combo(combo)
            # currentIndexChanged 가 아니라 activated 다. 전자는 목록을 다시
            # 채우기만 해도 터진다 -- clear() 로 -1, 첫 addItem 으로 0, 마지막
            # setCurrentIndex 로 또 한 번. 그때마다 "조작자가 카메라를 바꿨다"로
            # 읽혀 노드를 죽였다 띄웠고(각 3~5초), 그것이 페이지 진입이 3~8초
            # 걸린 원인이었다. activated 는 사람이 고른 경우에만 온다.
            combo.activated.connect(lambda _i, c=cam: self._on_camera_pick(c))
            self.combos[cam] = combo
            self.cam_form.addRow(cam, combo)
        self._fill_camera_combos(keep)
        self.preview_column.set_cams(roles)
        self.previews = self.preview_column.views()

    # ------------------------------------------------------------ 미리보기
    def serials(self) -> "dict[str, str]":
        """cam id -> 시리얼 (지금 화면 그대로)."""
        return {cam: self._serial(c) for cam, c in self.combos.items()}

    def cameras(self) -> tuple:
        """역할 순서가 아니라 cam 순서의 시리얼 튜플 (마법사 결과용)."""
        s = self.serials()
        return tuple(s[cam] for cam in self.cam_ids() if cam in s)

    @staticmethod
    def _serial(combo: QComboBox) -> str:
        data = combo.currentData()
        if data and data != _NO_CAMERA:
            return str(data)
        text = combo.currentText().strip()
        return "" if text.startswith("(") else text

    def _dedup(self, picked_cam: str) -> None:
        """한 카메라가 두 cam 에 붙지 못하게 한다.

        방금 고른 쪽을 남기고, 같은 시리얼을 쥐고 있던 다른 cam 을 (선택
        안함) 으로 내린다 -- 둘 다 살려두면 노드가 같은 장치를 두 번 열려다
        실패하고, 어느 쪽이 이겼는지도 화면에 안 보인다.
        """
        want = self._serial(self.combos[picked_cam])
        if not want:
            return
        for cam, combo in self.combos.items():
            if cam == picked_cam or self._serial(combo) != want:
                continue
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(_NO_CAMERA)))
            combo.blockSignals(False)

    def _on_camera_pick(self, picked_cam: "str | None" = None, *_a) -> None:
        """선택이 바뀌면 노드 구성을 맞추고, **바뀐 cam 의 미리보기만** 다시 건다.

        예전에는 카메라를 하나만 바꿔도 노드 프로세스를 죽이고 새로 띄웠다.
        그러면 바꾸지 않은 카메라까지 닫혔다 열려서 옆 화면도 "연결 중..." 으로
        깜빡였고, 프로세스 정리에만 1.8초가 들었다 (2026-09-05 실측).
        도는 노드가 있으면 set_cams 로 **바뀐 장치만** 여닫는다.
        """
        if picked_cam:
            self._dedup(picked_cam)
        serials = self.serials()
        # 노드는 시리얼만 안다. 역할을 바꿔도 여는 장치가 같으면 spec 이
        # 그대로라 아무 일도 일어나지 않는다 (3층 분리의 실질적 이득).
        specs = node_specs(serials.values())
        key = spec_key(specs)
        if key == self._node_key and self._node is not None:
            return
        before = dict(self._preview_serial)
        alive = (self._node is not None
                 and self._node.state() != QProcess.ProcessState.NotRunning)
        if alive and specs and set_cams(specs) is not None:
            pass                      # 프로세스 유지 -- 바뀐 장치만 여닫았다
        else:
            self._stop_previews()
            self._stop_node()
            self._node = spawn_node(specs)
            before = {}               # 새 프로세스라 전부 다시 걸어야 한다
        self._node_key = key if self._node is not None else ""
        if self._node is None:
            for v in self.previews.values():
                v.clear_frame(tr("카메라를 고르세요"))
            self._preview_serial = {}
            return
        for cam, serial in serials.items():
            view = self.previews.get(cam)
            if view is None:
                continue
            if before.get(cam) == serial:
                continue              # 이 화면은 그대로다 -- 건드리지 않는다
            self._release_preview(cam)
            if not serial:
                view.clear_frame(tr("(선택 안함)"))
                self._preview_serial.pop(cam, None)
                continue
            view.clear_frame(tr("연결 중..."))
            # 라벨은 cam id, 전송은 시리얼.
            w = CameraPreviewWorker(cam, serial)
            w.frame_ready.connect(lambda f, v=view: v.set_frame(f))
            w.error.connect(lambda m, v=view: v.clear_frame(m[:40]))
            w.start()
            self._preview_workers[cam] = w
            self._preview_serial[cam] = serial

    def _release_preview(self, cam: str) -> None:
        w = self._preview_workers.pop(cam, None)
        if w is None:
            return
        for sig in (w.frame_ready, w.error):
            try:
                sig.disconnect()
            except TypeError:
                pass
        w.stop()
        w.wait(3000)
        self._preview_serial.pop(cam, None)

    def _stop_previews(self) -> None:
        for w in self._preview_workers.values():
            for sig in (w.frame_ready, w.error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            w.stop()
            w.wait(3000)
        self._preview_workers.clear()
        self._preview_serial.clear()

    def _stop_node(self) -> None:
        if self._node is None:
            return
        self._node.terminate()
        if not self._node.waitForFinished(3000):
            self._node.kill()
            self._node.waitForFinished(2000)
        self._node = None
        self._node_key = ""
        #: 버전 [확인] 이 띄운 로봇 노드. 워크스페이스가 이어받는다 -- FCI 는
        #: 클라이언트 하나만 받으므로, 확인용으로 띄우고 두면 창에서 다시
        #: 띄울 때 실패한다.
        self._robot_node = None

    def take_node(self):
        """(QProcess, spec key) 를 넘기고 이 페이지는 손을 뗀다.

        미리보기는 여기서 끝낸다 -- 창이 자기 미리보기를 새로 걸기 때문에,
        구독자가 겹치면 같은 프레임을 두 번 디코딩하게 된다.
        """
        self._stop_previews()
        proc, key = self._node, self._node_key
        self._node, self._node_key = None, ""
        return proc, key

    def cleanup(self) -> None:
        """마법사를 취소하고 나갈 때 -- 넘기지 않은 노드는 정리한다."""
        self._stop_previews()
        self._stop_node()
        if self._robot_node is not None:
            self._robot_node.terminate()
            if not self._robot_node.waitForFinished(3000):
                self._robot_node.kill()
                self._robot_node.waitForFinished(2000)
            self._robot_node = None

    def _apply_station_defaults(self) -> None:
        """어느 카메라를 고를지 정한다 -- 목록은 detect_cameras 가 만든다.

        정본은 **데이터셋**이다 (dataset-identity.json 의 cameras: cam id ->
        시리얼). 그 데이터가 실제로 어떤 실물로 찍혔는지가 거기 남기 때문이다.
        없으면 스테이션 폴백, 그것도 없으면 비운다 -- recents 는 쓰지 않는다.
        데이터셋을 바꿨는데 직전 데이터셋의 카메라가 기본값으로 붙는 것이
        정확히 이 층위를 섞은 결과였다.
        """
        wiz = self.wizard()
        root = None
        if wiz is not None and getattr(wiz, "mode", "") == "continue":
            root = wiz.page(PAGE_CONTINUE).selected_path()
        ident = load_identity(root) if root is not None else None
        bound = dict(ident.cameras) if ident is not None else {}
        try:
            cfg = load_station(self.station_editor.current_name() or None)
        except Exception:  # noqa: BLE001
            cfg = None
        for cam, combo in self.combos.items():
            serial = bound.get(cam, "")
            if not serial and cfg is not None:
                spec = cfg.cameras.get(cam)
                serial = spec.serial if spec is not None else ""
            if not serial:
                continue
            i = combo.findData(serial)
            if i < 0:
                # 기억된 카메라가 지금 안 꽂혀 있다. 지우지 않고 그 사실을
                # 보여준다 -- 목록에서 사라지면 왜 없는지 알 수 없다.
                combo.addItem(tr("{s} — 연결 안 됨").format(s=serial), serial)
                i = combo.count() - 1
            combo.setCurrentIndex(i)

    def detect_cameras(self) -> None:
        try:
            from lerobot.cameras.realsense import RealSenseCamera

            cams = RealSenseCamera.find_cameras()
        except Exception as e:  # noqa: BLE001
            self.cam_hint.setText(tr("감지 실패: {e}").format(e=e))
            return
        entries = []
        for c in cams:
            serial = str(c.get("serial_number") or c.get("id") or "")
            name = str(c.get("name") or "RealSense")
            if serial:
                entries.append((serial, f"{name} ({serial})"))
        self._entries = entries
        self._fill_camera_combos(self.serials())
        self.cam_hint.setText(tr("{n}대 감지됨").format(n=len(entries)))

    def _fill_camera_combos(self, keep: "dict[str, str]") -> None:
        """감지 결과로 모든 cam 콤보를 채우고 keep 의 선택을 되살린다."""
        for cam, combo in self.combos.items():
            # 선택은 시리얼(itemData)로 기억한다. 표시 문자열로 맞추면
            # 라벨에 모델명이 붙는 순간 못 찾는다.
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("(선택 안함)"), _NO_CAMERA)
            for serial, label in self._entries:
                combo.addItem(label, serial)
            want = keep.get(cam, "")
            i = combo.findData(want) if want else -1
            combo.setCurrentIndex(max(0, i))
            combo.blockSignals(False)

    def station(self) -> str:
        return self.station_editor.current_name()

    def _wanted_station(self) -> str:
        """이어서 수집이면 그 데이터세트가 쓰던 스테이션 (identity 에 기록)."""
        wiz = self.wizard()
        root = None
        if wiz is not None and getattr(wiz, "mode", "") == "continue":
            root = wiz.page(PAGE_CONTINUE).selected_path()
        if root is None:
            return ""
        ident = load_identity(root)
        return ident.station if ident is not None else ""

    def _on_save_station(self) -> None:
        """편집기의 저장 버튼 -- 카메라 시리얼은 이 페이지가 안다."""
        agent, wrist = self.cameras()
        cams = {"agent": CameraSpec(serial=agent),
                "wrist": CameraSpec(serial=wrist)}
        if self.station_editor.save_new(cams):
            self._refresh_git_warning()

    def schema_version(self) -> str:
        return str(self.schema_combo.currentData() or SCHEMA_VERSION)

    def _dataset_root(self) -> "Path | None":
        wiz = self.wizard()
        if wiz is not None and getattr(wiz, "mode", "") == "continue":
            return wiz.page(PAGE_CONTINUE).selected_path()
        return None

    def _refresh_schema_label(self, *_a) -> None:
        """고른 버전과, 이 데이터셋에 이미 있는 버전 이력을 보여준다.

        한 데이터셋에 여러 버전이 섞이는 것은 허용한다 (2026-09-05 결정) --
        새 필드를 쓰기 시작하면 그 시점부터 버전이 올라가는 것이 자연스럽고,
        "언제부터 바뀌었나" 는 파일에서 그대로 읽어 보여준다. 별도 이력을
        적어 두지 않는 이유는 그것이 두 번째 진실이 되기 때문이다.
        """
        picked = self.schema_version()
        root = self._dataset_root()
        lines = []
        if root is not None:
            spans = schema_version_spans(root)
            if spans:
                lines.append(tr("이미 있는 파일: ") + "   ".join(
                    f"{v} ({a})" if a == b else f"{v} ({a}~{b})"
                    for v, a, b in spans))
                newest = max(s[0] for s in spans)
                if picked != newest:
                    lines.append(tr("고른 버전이 마지막으로 쓴 것과 다릅니다 "
                                    "— 이 시점부터 {v} 로 기록됩니다.")
                                 .format(v=picked))
        else:
            lines.append(tr("새 데이터셋입니다."))
        req = schema_required_fields(picked)
        if req:
            extra = [f for f in req["obs_datasets"]
                     if f not in (schema_required_fields("knu-1.0.0") or
                                  {"obs_datasets": ()})["obs_datasets"]]
            if extra:
                lines.append(tr("{v} 추가 관측: {f}")
                             .format(v=picked, f=", ".join(extra)))
        self.schema_label.setText("\n".join(lines))
        self.schema_label.setStyleSheet("color:#888;")
        self.schema_test_label.setText("")

    def _on_schema_selftest(self) -> None:
        """로봇 노드에 관측을 한 번 요청해, 고른 버전의 필드가 실제로 오는지 본다.

        포스·토크는 FR3 펌웨어가 그 필드를 노출할 때만 온다 -- 안 오는 장비에서
        1.1.0 을 고르면 필수 필드가 빠진 파일이 된다. 찍기 전에 알아야 한다.
        """
        picked = self.schema_version()
        req = schema_required_fields(picked)
        if req is None:
            self.schema_test_label.setText(tr("모르는 버전입니다: {v}").format(v=picked))
            self.schema_test_label.setStyleSheet("color:#e74c3c;")
            return
        try:
            cfg = load_station(self.station_editor.current_name() or None)
        except Exception as e:  # noqa: BLE001
            self.schema_test_label.setText(tr("스테이션 설정을 읽지 못했습니다: {e}")
                                           .format(e=e))
            self.schema_test_label.setStyleSheet("color:#e74c3c;")
            return
        self.schema_test_btn.setEnabled(False)
        try:
            obs = self._probe_robot(cfg)
        finally:
            self.schema_test_btn.setEnabled(True)
        if obs is None:
            return
        missing = [f for f in _ROBOT_OBS_FIELDS.get(picked, ())
                   if f not in obs]
        if missing:
            self.schema_test_label.setText(tr(
                "{v} 가 요구하는 값이 로봇에서 오지 않습니다: {m}\n"
                "이 장비로는 낮은 버전을 고르세요.")
                .format(v=picked, m=", ".join(missing)))
            self.schema_test_label.setStyleSheet("color:#e74c3c;")
            return
        shapes = ", ".join(
            f"{f}{tuple(np.shape(obs[f]))}" for f in _ROBOT_OBS_FIELDS.get(picked, ())
        ) or tr("(추가 필드 없음)")
        self.schema_test_label.setText(
            tr("확인됨 — {v} 의 값이 로봇에서 옵니다.  {s}")
            .format(v=picked, s=shapes))
        self.schema_test_label.setStyleSheet("color:#27ae60;")

    def _probe_robot(self, cfg):
        """관측을 한 번 받아 온다. 노드가 없으면 띄워 보고, 실패하면 None.

        노드를 여기서 띄우는 이유: 확인은 이 화면에서 끝나야 한다. "다른 데서
        노드를 띄우고 오세요" 는 대부분 그냥 확인을 건너뛰게 만든다.
        """
        self.schema_test_label.setText(tr("로봇 노드에 물어보는 중..."))
        self.schema_test_label.setStyleSheet("color:#888;")
        QApplication.processEvents()
        try:
            return probe_observation(cfg.node.host, int(cfg.node.port))
        except Exception:  # noqa: BLE001 -- 안 떠 있는 것이 정상 경로다
            pass
        if self._robot_node is not None:
            # 우리가 띄웠는데도 안 붙는다 -- FCI 쪽 문제다. 노드 자체의 로그가
            # 원인을 갖고 있으므로 그리로 보낸다.
            self.schema_test_label.setText(tr(
                "로봇 노드를 띄웠지만 응답이 없습니다. FR3 Desk 에서 FCI 가 "
                "켜져 있고 다른 프로그램이 잡고 있지 않은지 확인하세요."))
            self.schema_test_label.setStyleSheet("color:#e74c3c;")
            return None
        self.schema_test_label.setText(tr("로봇 노드를 띄우는 중..."))
        QApplication.processEvents()
        self._robot_node = spawn_robot_node(cfg, self)
        if self._robot_node is None:
            self.schema_test_label.setText(tr("노드 실행이 꺼져 있습니다 "
                                              "(GELLO_NO_ROBOT_NODE=1)."))
            self.schema_test_label.setStyleSheet("color:#e67e22;")
            return None
        # FCI 연결 + 첫 read_once 까지 몇 초 걸린다. 붙을 때까지 짧게 되묻는다.
        deadline = time.monotonic() + _ROBOT_NODE_WAIT_S
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if self._robot_node.state() == QProcess.ProcessState.NotRunning:
                self.schema_test_label.setText(tr(
                    "로봇 노드가 곧바로 종료했습니다. 워크스페이스의 로그에서 "
                    "원인을 볼 수 있습니다 (FCI 미활성, IP 오류 등)."))
                self.schema_test_label.setStyleSheet("color:#e74c3c;")
                self._robot_node = None
                return None
            try:
                return probe_observation(cfg.node.host, int(cfg.node.port))
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        self.schema_test_label.setText(tr(
            "로봇 노드가 {s}초 안에 응답하지 않았습니다. 확인 없이 진행해도 "
            "되지만, 필드가 없으면 그 버전으로 찍힌 파일이 검증을 통과하지 "
            "못합니다.").format(s=int(_ROBOT_NODE_WAIT_S)))
        self.schema_test_label.setStyleSheet("color:#e67e22;")
        return None

    def take_robot_node(self):
        """[확인] 이 띄운 로봇 노드를 넘기고 이 페이지는 손을 뗀다."""
        proc, self._robot_node = self._robot_node, None
        return proc

    def _refresh_git_warning(self) -> None:
        """커밋 안 된 스테이션 파일이 있으면 알린다.

        막지는 않는다 -- 사용자가 감수하기로 한 부분이다. 다만 그 상태에서는
        아이콘 실행 때 `git pull --ff-only` 가 실패해 **최신 코드를 받지
        못한 채** 돌게 되므로, 모르고 지나가지는 않게 한다.
        """
        try:
            out = subprocess.run(
                ["git", "status", "--porcelain", "--", "configs/stations"],
                cwd=str(WT_ROOT), capture_output=True, text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001 -- git 이 없거나 저장소가 아니면 조용히
            out = ""
        dirty = [ln[3:] for ln in out.splitlines() if ln.strip()]
        self.git_warn.setText(tr(
            "커밋되지 않은 스테이션 파일이 있습니다: {f}\n"
            "이 상태에서는 아이콘 실행 때 자동 git pull 이 건너뛰어져 옛 코드로 "
            "돌 수 있습니다. 커밋해 두세요.").format(f=", ".join(dirty))
            if dirty else "")

    def isComplete(self) -> bool:  # noqa: N802 - Qt override
        return True     # 카메라 미선택도 허용

    def nextId(self) -> int:  # noqa: N802 - Qt override
        return -1       # 마지막 페이지
