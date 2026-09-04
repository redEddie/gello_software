"""Launcher wizard pages — mode / continue / new dataset / hardware."""

from __future__ import annotations

import subprocess
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

from gello.gui.widgets import VideoView
from gello.gui.workers import CameraPreviewWorker
from apps.workspace.shared.camera_node_proc import (
    node_specs,
    spawn_node,
    spec_key,
)
from gello.config.station import CameraSpec, load_station
from apps.workspace.constants import WT_ROOT
from apps.workspace.launcher.station_editor import StationEditor
from gello.scene.dataset_meta import (
    DEFAULT_DATASETS_PARENT,
    DatasetEntry,
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
        outer = QVBoxLayout(self)
        self.station_editor = StationEditor()
        self.station_editor.save_requested.connect(self._on_save_station)
        outer.addWidget(self.station_editor)
        form = QFormLayout()
        outer.addLayout(form)
        # 커밋 안 된 스테이션 파일이 있으면 아이콘의 자동 git pull 이 건너뛰어진다.
        # 막지는 않고 알리기만 한다 (2026-09-05 사용자 결정).
        self.git_warn = QLabel("")
        self.git_warn.setWordWrap(True)
        self.git_warn.setStyleSheet("color:#e67e22;")
        outer.addWidget(self.git_warn)
        self.agent_combo = QComboBox()
        self.wrist_combo = QComboBox()
        for c in (self.agent_combo, self.wrist_combo):
            c.setEditable(True)
        # 시리얼도 모델명도 "어느 쪽이 손목인지"는 안 알려준다. 특히 같은
        # 모델이 두 대면 구별할 방법이 없다. 작게라도 그림이 보이면 즉시
        # 갈린다 -- 이걸 위해 이 페이지에서 카메라 노드를 띄운다(_ensure_node).
        self.previews: dict[str, VideoView] = {}
        self._preview_workers: dict[str, CameraPreviewWorker] = {}
        for role, combo, label in (("agent", self.agent_combo, tr("Agent 카메라")),
                                   ("wrist", self.wrist_combo, tr("Wrist 카메라"))):
            cell = QHBoxLayout()
            cell.addWidget(combo, 1)
            view = VideoView()
            view.setFixedSize(160, 120)
            view.setText(tr("대기"))
            self.previews[role] = view
            cell.addWidget(view)
            form.addRow(label, cell)
            combo.currentIndexChanged.connect(self._on_camera_pick)
        row = QHBoxLayout()
        detect = QPushButton(tr("카메라 감지"))
        detect.clicked.connect(self.detect_cameras)
        row.addWidget(detect)
        self.cam_hint = QLabel("")
        self.cam_hint.setStyleSheet("color:#888;")
        row.addWidget(self.cam_hint, 1)
        form.addRow(row)
        self.station_editor.combo.currentIndexChanged.connect(
            self._apply_station_defaults)
        # 이 페이지가 띄운 노드. 마법사가 끝나면 워크스페이스가 물려받는다
        # (take_node) -- 넘기지 않으면 창이 같은 카메라를 두 번 열려다
        # 포트 6021 충돌로 죽는다.
        self._node = None
        self._node_key = ""

    def initializePage(self) -> None:  # noqa: N802 - Qt override
        import os
        # 기본 선택: 이어서 수집이면 그 데이터세트가 쓰던 스테이션.
        want = self._wanted_station() or os.environ.get("GELLO_STATION", "")
        if want:
            self.station_editor.reload(select=want)
        self._refresh_git_warning()
        # 목록부터 만들고(모델명 포함) 그 안에서 기억된 것을 고른다. 버튼을
        # 눌러야만 모델명이 보이면 아무도 안 누른다.
        self.detect_cameras()
        self._apply_station_defaults()
        self._on_camera_pick()

    # ------------------------------------------------------------ 미리보기
    def cameras(self) -> tuple[str, str]:
        return (self._serial(self.agent_combo), self._serial(self.wrist_combo))

    @staticmethod
    def _serial(combo: QComboBox) -> str:
        data = combo.currentData()
        if data and data != _NO_CAMERA:
            return str(data)
        text = combo.currentText().strip()
        return "" if text.startswith("(") else text

    def _on_camera_pick(self, *_a) -> None:
        """선택이 바뀌면 노드를 그 구성으로 맞추고 미리보기를 다시 건다."""
        agent, wrist = self.cameras()
        specs = node_specs(agent, wrist)
        key = spec_key(specs)
        if key == self._node_key and self._node is not None:
            return
        self._stop_previews()
        self._stop_node()
        self._node = spawn_node(specs)
        self._node_key = key if self._node is not None else ""
        if self._node is None:
            for v in self.previews.values():
                v.clear_frame(tr("카메라를 고르세요"))
            return
        for role, serial in (("agent", agent), ("wrist", wrist)):
            view = self.previews[role]
            if not serial:
                view.clear_frame(tr("(선택 안함)"))
                continue
            view.clear_frame(tr("연결 중..."))
            w = CameraPreviewWorker(role, serial)
            w.frame_ready.connect(lambda f, v=view: v.set_frame(f))
            w.error.connect(lambda m, v=view: v.clear_frame(m[:40]))
            w.start()
            self._preview_workers[role] = w

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

    def _stop_node(self) -> None:
        if self._node is None:
            return
        self._node.terminate()
        if not self._node.waitForFinished(3000):
            self._node.kill()
            self._node.waitForFinished(2000)
        self._node = None
        self._node_key = ""

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

    def _apply_station_defaults(self) -> None:
        """어느 카메라를 고를지만 정한다 -- 목록은 detect_cameras 가 만든다.

        예전에는 여기서 목록을 지우고 시리얼만 넣어서, "카메라 감지"를 누르기
        전까지 230422272249 같은 숫자만 보였다. 어느 게 손목인지 알 수 없다.
        recents 가 우선 (마지막으로 실제 쓴 조합이 가장 그럴듯하다).
        """
        recents = Recents()
        try:
            cfg = load_station(self.station_editor.current_name() or None)
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
        for combo in (self.agent_combo, self.wrist_combo):
            # 선택은 시리얼(itemData)로 기억한다. 표시 문자열로 맞추면
            # 라벨에 모델명이 붙는 순간 못 찾는다.
            cur = combo.currentData()
            combo.clear()
            combo.addItem(tr("(선택 안함)"), _NO_CAMERA)
            for serial, label in entries:
                combo.addItem(label, serial)
            i = combo.findData(cur) if cur else -1
            combo.setCurrentIndex(max(0, i))
        self.cam_hint.setText(tr("{n}대 감지됨").format(n=len(entries)))

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
