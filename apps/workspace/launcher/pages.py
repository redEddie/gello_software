"""Launcher wizard pages — mode / continue / new dataset / hardware."""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWizardPage,
)

from gello.config.station import list_stations, load_station
from gello.scene.dataset_meta import (
    DEFAULT_DATASETS_PARENT,
    DatasetEntry,
    discover_datasets,
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
        form = QFormLayout(self)
        self.station_combo = QComboBox()
        for s in list_stations():
            self.station_combo.addItem(s, s)
        form.addRow(tr("스테이션"), self.station_combo)
        self.agent_combo = QComboBox()
        self.wrist_combo = QComboBox()
        for c in (self.agent_combo, self.wrist_combo):
            c.setEditable(True)
        form.addRow(tr("Agent 카메라"), self.agent_combo)
        form.addRow(tr("Wrist 카메라"), self.wrist_combo)
        row = QHBoxLayout()
        detect = QPushButton(tr("카메라 감지"))
        detect.clicked.connect(self.detect_cameras)
        row.addWidget(detect)
        self.cam_hint = QLabel("")
        self.cam_hint.setStyleSheet("color:#888;")
        row.addWidget(self.cam_hint, 1)
        form.addRow(row)
        self.station_combo.currentIndexChanged.connect(self._apply_station_defaults)

    def initializePage(self) -> None:  # noqa: N802 - Qt override
        import os
        want = os.environ.get("GELLO_STATION", "")
        idx = self.station_combo.findData(want)
        if idx >= 0:
            self.station_combo.setCurrentIndex(idx)
        self._apply_station_defaults()

    def _apply_station_defaults(self) -> None:
        """스테이션 YAML 의 카메라 시리얼을 채운다. recents 에 기록이 있으면
        그쪽이 우선 (마지막으로 실제 쓴 조합이 가장 그럴듯하다)."""
        recents = Recents()
        try:
            cfg = load_station(self.station_combo.currentData() or None)
        except Exception:  # noqa: BLE001
            cfg = None
        for combo, role, key in ((self.agent_combo, "agent", "agent_serial"),
                                 (self.wrist_combo, "wrist", "wrist_serial")):
            serial = recents.most_recent(key, "")
            if not serial and cfg is not None:
                try:
                    serial = cfg.camera(role).serial
                except Exception:  # noqa: BLE001
                    serial = ""
            combo.clear()
            combo.addItem(tr("(선택 안함)"), _NO_CAMERA)
            if serial:
                combo.addItem(serial, serial)
                combo.setCurrentIndex(1)

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
        for combo in (self.agent_combo, self.wrist_combo):
            cur = combo.currentText().strip()
            combo.clear()
            combo.addItem(tr("(선택 안함)"), _NO_CAMERA)
            for serial, label in entries:
                combo.addItem(label, serial)
            for i in range(combo.count()):
                if combo.itemData(i) == cur:
                    combo.setCurrentIndex(i)
                    break
        self.cam_hint.setText(tr("{n}대 감지됨").format(n=len(entries)))

    def station(self) -> str:
        return str(self.station_combo.currentData() or "")

    def cameras(self) -> "tuple[str, str]":
        def _serial(combo: QComboBox) -> str:
            data = combo.currentData()
            return str(data if data is not None else combo.currentText()).strip()
        return _serial(self.agent_combo), _serial(self.wrist_combo)

    def isComplete(self) -> bool:  # noqa: N802 - Qt override
        return True     # 카메라 미선택도 허용

    def nextId(self) -> int:  # noqa: N802 - Qt override
        return -1       # 마지막 페이지
