"""PyQt6 GUI for collecting LIBERO-format imitation-learning demos via GELLO teleop.

Run inside lerobot-venv (has PyQt6, gello, dynamixel-sdk, pyrealsense2, h5py)::

    (pylibfranka-venv) python experiments/launch_nodes.py --robot fr3   # terminal 1
    (lerobot-venv)     python experiments/collect_libero_gui.py         # terminal 2

One run = one task/language-instruction = one ``<task>_demo.hdf5`` file. To
collect a different task, finish the session (종료) and restart with a new
task name.

Flow per episode, mirroring experiments/record_dataset.py: 홈 복귀 -> (2회차
부터) 리셋 대기 -> 리더 자세 게이트 -> 접근 램프 -> 기록. The pose-match screen
replaces scripts/gello_match_pose.py's terminal view; Start Teleop replaces
run_env.py --agent gello's start gate -- both reuse the exact same underlying
GelloFR3Teleop/FR3ZMQRobot/threshold logic (gello/libero_gui_worker.py), just
driven by GUI buttons instead of a second terminal + Ctrl-C handoff.
"""

from __future__ import annotations

import os

# Must run before numpy/cv2/h5py/torch(via lerobot) are imported below --
# these each read the env var once at their own C-level init and spin up a
# BLAS/parallel-executor thread pool sized to the CPU core count (measured:
# 39 extra OS threads on this 20-core machine, for a GUI that does no heavy
# matrix math or bulk image processing at all). Setting these first keeps
# that pool at 1 with zero measured functional difference for this script's
# actual workload (light resize/color-convert calls). Don't copy this into
# scripts/convert_libero_to_lerobot.py -- that one genuinely benefits from
# parallel video encoding.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import json
import re
import sys
import time
from pathlib import Path

import cv2
import h5py
import numpy as np
from PyQt6.QtCore import QEvent, QProcess, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.dataset_schema import (  # noqa: E402
    ACTION_SPACES,
    ACTION_SPACE_LABELS,
    DatasetSchemaConfig,
    load_schema_config,
    save_schema_config,
)
from gello.i18n import get_language, set_language, tr  # noqa: E402
from gello.libero_format import (  # noqa: E402
    action_column_names,
    describe_episode,
    describe_schema,
    hdf5_repack_status,
    renumber_episodes,
    schema_from_episode,
)
from gello.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402
from gello.robots.franka_fr3 import FR3_RESET_POSES  # noqa: E402

# The OMP/OPENBLAS/MKL env vars above only cap numpy's BLAS backend --
# OpenCV's own parallel_for_ executor is a separate thread pool controlled
# only by this runtime call, not an env var.
cv2.setNumThreads(1)

# launch_nodes.py needs pylibfranka, which only exists in this separate venv
# (this GUI itself runs in lerobot-venv -- see module docstring). Spawned as
# a subprocess rather than imported, same as running it by hand in a second
# terminal.
PYLIBFRANKA_PYTHON = str(Path.home() / "pylibfranka-venv" / "bin" / "python")
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parent / "launch_nodes.py")
RUNME_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "runme.sh")
REPACK_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "repack_hdf5.py")

# Repo IDs and output paths get retyped every session otherwise, and a typo in
# a repo ID silently creates a *new* Hub dataset rather than failing.
RECENTS_PATH = Path.home() / "libero_gui_logs" / "recent_inputs.json"
_RECENTS_MAX = 8


class Recents:
    """Most-recently-used values per field key, persisted as JSON.

    Never raises: a corrupt or unwritable file just means "no history", which
    must not be able to stop the GUI from starting or a conversion from running.
    """

    def __init__(self, path: Path = RECENTS_PATH) -> None:
        self._path = path
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str) -> list[str]:
        v = self._data.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    def most_recent(self, key: str, fallback: str = "") -> str:
        v = self.get(key)
        return v[0] if v else fallback

    def add(self, key: str, value: str) -> None:
        value = (value or "").strip()
        if not value:
            return
        cur = [v for v in self.get(key) if v != value]
        self._data[key] = [value] + cur[: _RECENTS_MAX - 1]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # history is a convenience, never a hard failure


def hf_account() -> tuple[str, str]:
    """(display text, css color) describing who a --push would upload as.

    Imported lazily and defensively: this runs on a GUI thread at startup and
    huggingface_hub may be missing, or the token cached but expired.
    """
    try:
        from huggingface_hub import whoami

        info = whoami()
        name = info.get("name", "?")
        orgs = [o["name"] for o in info.get("orgs", []) if isinstance(o, dict) and "name" in o]
        text = f"HF 로그인: {name}"
        if orgs:
            text += f"  (orgs: {', '.join(orgs)})"
        return text, "#27ae60"
    except ImportError:
        return "HF: huggingface_hub 미설치 -- 업로드 불가", "#e74c3c"
    except Exception:
        return "HF: 로그인 안 됨 -- 터미널에서 `hf auth login` 실행", "#e67e22"

JOINT_LABELS = [f"J{i}" for i in range(1, 8)] + ["grip"]

STATE_LABELS_KO = {
    "idle": "대기",
    "connecting": "연결 중...",
    "homing": "홈 복귀 중",
    "reset_wait": "환경 리셋 대기",
    "gate": "자세 맞추는 중",
    "approach": "접근 중",
    "recording": "기록 중",
}


# tqdm (huggingface_hub's uploader) redraws one progress line in place using
# carriage returns and ANSI cursor-up codes. Down a pipe there is no cursor to
# move, so every redraw arrives as another line -- a 955 MB upload produced
# dozens of identical "100%|####| 955MB / 955MB" entries. Strip the escapes,
# then collapse consecutive progress redraws to at most one per interval.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")
_PROGRESS_RE = re.compile(r"\d+%\|")


def clean_stream_lines(data: str, state: dict, every_s: float = 3.0) -> list[str]:
    """Split subprocess output into log-worthy lines, de-spamming progress."""
    out = []
    for raw in _ANSI_RE.sub("\n", data).splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _PROGRESS_RE.search(line):
            now = time.monotonic()
            # Always keep a finished bar; throttle the rest.
            done = line.lstrip().startswith("100%") or "100%|" in line
            if not done and now - state.get("t", 0.0) < every_s:
                continue
            if done and state.get("last_done") == line:
                continue
            state["t"] = now
            if done:
                state["last_done"] = line
        out.append(line)
    return out


def np_to_pixmap(arr: np.ndarray) -> QPixmap:
    arr = np.ascontiguousarray(arr)
    h, w, ch = arr.shape
    img = QImage(arr.data, w, h, ch * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class DeltaBar(QWidget):
    """One joint's leader/follower delta, colored green (OK) / red (out of gate)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        self.name_label = QLabel(name)
        self.name_label.setFixedWidth(32)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%v")
        layout.addWidget(self.name_label)
        layout.addWidget(self.bar)

    def update_delta(self, delta: float, threshold: float) -> None:
        pct = int(min(100, abs(delta) / threshold * 100))
        self.bar.setValue(pct)
        self.bar.setFormat(f"{delta:+.3f} rad")
        color = "#2ecc71" if abs(delta) <= threshold else "#e74c3c"
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )


class CameraPreviewWorker(QThread):
    """Opens a single RealSense camera on its own thread just to preview it
    before/independent of a recording session -- separate from
    gello/libero_gui_worker.py's CollectionWorker, which owns both cameras
    for the duration of an actual session. Never run both at once against
    the same serial (the pipeline can't be opened twice); the GUI stops all
    previews before starting a CollectionWorker.
    """

    frame_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, serial: str) -> None:
        super().__init__()
        self.serial = serial
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig

            cam = RealSenseCamera(
                RealSenseCameraConfig(serial_number_or_name=self.serial, fps=30, width=640, height=480)
            )
            cam.connect()
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")
            return
        try:
            while self._running:
                try:
                    # read_latest() returns the newest buffered frame instead
                    # of blocking on wait_for_frames(). stop() is only a flag
                    # this loop checks between reads, so a blocking read made
                    # it unobservable for as long as the camera stalled -- up
                    # to librealsense's 5 s frame timeout, which is what
                    # produced "미리보기 스레드가 3초 내에 종료되지 않았습니다"
                    # on the wrist D405 (marginal USB 2 link, see docs). Now
                    # the flag is seen within one sleep interval.
                    frame = cam.read_latest(max_age_ms=1000)
                except Exception as e:  # noqa: BLE001
                    if not self._running:
                        break
                    self.error.emit(f"{type(e).__name__}: {e}")
                    break
                if self._running:
                    self.frame_ready.emit(frame)
                self.msleep(33)  # preview only needs ~30 fps
        finally:
            try:
                cam.disconnect()
            except Exception:  # noqa: BLE001
                pass


class DatasetSchemaDialog(QDialog):
    """Lets the operator pick the action space and which obs fields get
    written, before connecting.

    There is deliberately no "use LIBERO defaults" master switch: it used to
    override every control here at write time, so a session set to
    ``joint_absolute`` silently wrote ``ee_delta`` instead. A bare
    ``DatasetSchemaConfig()`` already *is* the LIBERO default, so leaving the
    controls alone achieves the same thing visibly.
    """

    _OBS_FIELDS = [
        ("save_agentview_rgb", "Agentview RGB (외부 카메라 이미지)"),
        ("save_eye_in_hand_rgb", "Eye-in-hand RGB (손목 카메라 이미지)"),
        ("save_joint_states", "Joint states (관절 위치)"),
        ("save_gripper_states", "Gripper state (그리퍼 연속값)"),
        ("save_ee_states", "EE states (EE pos + orientation)"),
        ("save_ee_pos", "EE position"),
        ("save_ee_ori", "EE orientation (axis-angle)"),
    ]
    _EXTRA_FIELDS = [
        ("save_joint_velocities", "Joint velocities (관절 속도) -- 제어루프에서 이미 계산됨, 추가 비용 없음"),
        ("save_timestamp", "Timestamp (프레임별 wall-clock 시각) -- 프레임 간격 검증용"),
    ]
    # QComboBox itemData round-trips ints reliably but not Python None, so
    # "원본 해상도" is stored as this sentinel and translated to/from
    # DatasetSchemaConfig.image_size=None at the edges (_current_config/init).
    _IMAGE_SIZE_NATIVE = -1

    def __init__(self, parent: QWidget, cfg: DatasetSchemaConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("데이터셋 구조 사용자 지정"))
        layout = QVBoxLayout(self)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel(tr("Action Space:")))
        self.action_combo = QComboBox()
        for key in ACTION_SPACES:
            self.action_combo.addItem(tr(ACTION_SPACE_LABELS[key]), key)
        idx = self.action_combo.findData(cfg.action_space)
        if idx >= 0:
            self.action_combo.setCurrentIndex(idx)
        action_row.addWidget(self.action_combo, 1)
        layout.addLayout(action_row)

        self.field_checks: dict[str, QCheckBox] = {}

        self.gripper_action_check = QCheckBox(
            tr("Action에 그리퍼 값 포함 (끄면 위 Action Space 어떤 걸 골라도 그리퍼 차원이 빠집니다)")
        )
        self.gripper_action_check.setChecked(cfg.action_include_gripper)
        self.field_checks["action_include_gripper"] = self.gripper_action_check
        layout.addWidget(self.gripper_action_check)

        self.gripper_match_obs_check = QCheckBox(
            tr(
                "Action 그리퍼 값을 Observation과 동일하게 0=open/1=close로 저장 "
                "(기본은 -1/+1, robosuite 컨벤션)"
            )
        )
        self.gripper_match_obs_check.setChecked(cfg.gripper_action_match_obs)
        self.field_checks["gripper_action_match_obs"] = self.gripper_match_obs_check
        # Enabled state depends on TWO things (기본값 사용 off AND 그리퍼 포함
        # on) -- kept out of _editable_widgets (which only knows about the
        # first) and driven by this instead, from both toggle signals.
        self.gripper_action_check.toggled.connect(lambda _: self._update_gripper_match_obs_enabled())
        layout.addWidget(self.gripper_match_obs_check)

        # Per-column action name overrides, keyed by the built-in default
        # name (e.g. "joint1.pos") -- survives switching Action Space back
        # and forth within this dialog session via _pending_overrides, since
        # the edit widgets themselves get rebuilt every time (the column set
        # changes with the action space). Seeded from whatever was already
        # saved (possibly for a different action_space than the one shown
        # first) so a previous custom name isn't silently lost.
        self._pending_overrides: dict[str, str] = dict(cfg.action_column_name_overrides)
        self.name_override_edits: dict[str, QLineEdit] = {}
        names_box = QGroupBox(tr("Action 열 이름 (선택 -- 비워두면 기본값)"))
        self.names_layout = QVBoxLayout(names_box)
        layout.addWidget(names_box)
        self.action_combo.currentIndexChanged.connect(lambda _: self._rebuild_name_edits())
        self.gripper_action_check.toggled.connect(lambda _: self._rebuild_name_edits())

        obs_box = QGroupBox(tr("저장할 Observation 필드"))
        obs_layout = QVBoxLayout(obs_box)

        image_size_row = QHBoxLayout()
        image_size_row.addWidget(QLabel(tr("이미지 해상도:")))
        self.image_size_combo = QComboBox()
        self.image_size_combo.addItem(tr("256x256 (LIBERO 기본, 정사각형 크롭)"), 256)
        self.image_size_combo.addItem(tr("원본 해상도 유지 (리사이즈 안 함)"), self._IMAGE_SIZE_NATIVE)
        target = cfg.image_size if cfg.image_size is not None else self._IMAGE_SIZE_NATIVE
        idx = self.image_size_combo.findData(target)
        if idx >= 0:
            self.image_size_combo.setCurrentIndex(idx)
        image_size_row.addWidget(self.image_size_combo, 1)
        obs_layout.addLayout(image_size_row)

        for attr, label in self._OBS_FIELDS:
            cb = QCheckBox(tr(label))
            cb.setChecked(getattr(cfg, attr))
            self.field_checks[attr] = cb
            obs_layout.addWidget(cb)
        layout.addWidget(obs_box)

        extra_box = QGroupBox(tr("추가 필드 (LIBERO 표준 아님)"))
        extra_layout = QVBoxLayout(extra_box)
        for attr, label in self._EXTRA_FIELDS:
            cb = QCheckBox(tr(label))
            cb.setChecked(getattr(cfg, attr))
            self.field_checks[attr] = cb
            extra_layout.addWidget(cb)
        layout.addWidget(extra_box)

        self._editable_widgets = [
            self.action_combo,
            self.gripper_action_check,
            names_box,
            obs_box,
            extra_box,
        ]
        self._rebuild_name_edits()
        self._update_gripper_match_obs_enabled()

        # Not in _editable_widgets on purpose -- always clickable, since
        # describe_schema() resolves
        # .effective() internally and will just show the LIBERO default
        # structure in that case (still a useful confirmation).
        preview_btn = QPushButton(tr("구조 미리보기..."))
        preview_btn.clicked.connect(self._show_preview)
        layout.addWidget(preview_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_gripper_match_obs_enabled(self) -> None:
        self.gripper_match_obs_check.setEnabled(self.gripper_action_check.isChecked())

    def _rebuild_name_edits(self) -> None:
        """Swaps the name-override row widgets for whatever column set the
        currently-selected Action Space + gripper-include state implies.
        Edits for the OLD column set are folded into _pending_overrides
        first, so switching Action Space back and forth (or toggling
        gripper-include) within this dialog session doesn't lose them --
        only committing (OK) or discarding the whole dialog does.
        """
        for key, edit in self.name_override_edits.items():
            text = edit.text().strip()
            if text and text != key:
                self._pending_overrides[key] = text
            else:
                self._pending_overrides.pop(key, None)

        while self.names_layout.count():
            item = self.names_layout.takeAt(0)
            w = item.widget()
            lay = item.layout()
            if w is not None:
                w.deleteLater()
            elif lay is not None:
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.deleteLater()

        self.name_override_edits = {}
        cols = action_column_names(self.action_combo.currentData())
        if self.gripper_action_check.isChecked():
            cols = cols + ["gripper.pos"]
        for default_name in cols:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{default_name}  ->"))
            edit = QLineEdit(self._pending_overrides.get(default_name, ""))
            edit.setPlaceholderText(default_name)
            row.addWidget(edit)
            self.names_layout.addLayout(row)
            self.name_override_edits[default_name] = edit

    def _current_config(self) -> DatasetSchemaConfig:
        """The config implied by the dialog's current widget state --
        regardless of whether OK has been clicked yet. Shared by
        result_config() (on accept) and _show_preview() (live, before
        committing to anything).
        """
        kwargs = {attr: cb.isChecked() for attr, cb in self.field_checks.items()}
        raw_size = self.image_size_combo.currentData()
        kwargs["image_size"] = None if raw_size == self._IMAGE_SIZE_NATIVE else raw_size

        overrides = dict(self._pending_overrides)
        for key, edit in self.name_override_edits.items():
            text = edit.text().strip()
            if text and text != key:
                overrides[key] = text
            else:
                overrides.pop(key, None)
        kwargs["action_column_name_overrides"] = overrides

        return DatasetSchemaConfig(
            action_space=self.action_combo.currentData(),
            **kwargs,
        )

    def result_config(self) -> DatasetSchemaConfig:
        return self._current_config()

    def _show_preview(self) -> None:
        text = describe_schema(self._current_config())
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("데이터셋 구조 미리보기"))
        layout = QVBoxLayout(dlg)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(QFont("monospace"))
        view.setMinimumSize(480, 360)
        layout.addWidget(view)
        close_btn = QPushButton(tr("닫기"))
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()


class LerobotConvertDialog(QDialog):
    """Collects args for scripts/convert_libero_to_lerobot.py before running
    it as a subprocess (see LiberoCollectorWindow._open_lerobot_convert).
    Curation (deleting bad takes) already happened in the HDF5 workflow --
    this dialog only picks which already-curated files to convert and where
    the result goes, mirroring the script's own CLI 1:1.
    """

    def __init__(self, parent: QWidget, default_root: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("LeRobot 변환 / 업로드"))
        layout = QVBoxLayout(self)

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

        # Conversion and upload are separate jobs: conversion is minutes of
        # AV1 encoding, upload is seconds. Bundling them meant "I already
        # converted, just upload it" had no answer -- re-running re-encoded
        # everything. Three explicit modes instead of one ambiguous checkbox.
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

        # Which account a --push actually uploads as. This machine is shared,
        # so "whose token is cached right now" is not something to assume.
        acct_text, acct_color = hf_account()
        self.hf_account_label = QLabel(acct_text)
        self.hf_account_label.setStyleSheet(f"color: {acct_color}; font-weight: bold;")
        up_col.addWidget(self.hf_account_label)

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
        if not repo_id:
            QMessageBox.warning(self, tr("Repo ID 필요"), tr("Repo ID를 입력하세요."))
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


class HdfUploadDialog(QDialog):
    """Collects args for scripts/upload_to_hub.py before running it as a
    subprocess (see LiberoCollectorWindow._open_hdf5_upload). Uploads the
    RAW curated .hdf5 as-is -- the converted/LeRobot half of the dual
    upload (see ~/huggingface_upload_process.md) is the separate "LeRobot
    변환..." button's --push option.
    """

    def __init__(self, parent: QWidget, default_file: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("HDF5 원본 업로드"))
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("업로드할 .hdf5 파일 (이미 큐레이션 끝난 파일):")))
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit(default_file)
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
        ex.setStyleSheet("color: #888; font-family: monospace;")
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
        ex_name.setStyleSheet("color: #888; font-family: monospace;")
        grid.addWidget(QLabel(tr("Repo 안 파일 이름:")), 1, 0)
        grid.addWidget(ex_name, 1, 2)
        self.path_in_repo_edit = QLineEdit(Path(default_file).name if default_file else "")
        self.path_in_repo_edit.setPlaceholderText(
            tr("비워두면 로컬 파일 이름 그대로")
        )
        grid.addWidget(self.path_in_repo_edit, 1, 1)
        layout.addLayout(grid)

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
        self.hf_account_label = QLabel(acct_text)
        self.hf_account_label.setStyleSheet(f"color: {acct_color}; font-weight: bold;")
        layout.addWidget(self.hf_account_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("업로드 시작"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_delete_existing_toggled(self, on: bool) -> None:
        self.old_path_label.setEnabled(on)
        self.old_path_in_repo_edit.setEnabled(on)

    def _browse_file(self) -> None:
        start = self.file_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, tr("업로드할 .hdf5 파일"), start, "HDF5 (*.hdf5)")
        if path:
            self.file_edit.setText(path)
            if not self.path_in_repo_edit.text().strip():
                self.path_in_repo_edit.setText(Path(path).name)

    def build_args(self) -> "list[str] | None":
        """Returns the script's argv (sans program name), or None (with a
        warning dialog already shown) if required fields are missing."""
        local_file = self.file_edit.text().strip()
        if not local_file:
            QMessageBox.warning(self, tr("파일 필요"), tr(".hdf5 파일을 선택하세요."))
            return None
        repo_id = self.repo_id_edit.currentText().strip()
        if not repo_id:
            QMessageBox.warning(self, tr("Repo ID 필요"), tr("Repo ID를 입력하세요."))
            return None
        self._recents.add("hdf5_repo_id", repo_id)
        args = [local_file, "--repo-id", repo_id]
        path_in_repo = self.path_in_repo_edit.text().strip()
        if path_in_repo:
            args += ["--path-in-repo", path_in_repo]
        args.append("--private" if self.private_check.isChecked() else "--no-private")
        if self.delete_existing_check.isChecked():
            args.append("--delete-existing")
            old_path_in_repo = self.old_path_in_repo_edit.text().strip()
            if old_path_in_repo:
                args += ["--old-path-in-repo", old_path_in_repo]
        return args



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
            item = QTreeWidgetItem([
                Path(path).name,
                f"{st['size']/1e6:,.1f} MB",
                str(st["episodes"]),
                st["compression"] or ("?" if st["error"] else "없음"),
                st["marker"] or (tr("완료 (gzip 감지)") if st["repacked"] else tr("안 됨")),
            ])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            todo = not st["repacked"] and not st["error"]
            item.setCheckState(0, Qt.CheckState.Checked if todo else Qt.CheckState.Unchecked)
            if st["error"]:
                item.setText(4, st["error"])
                item.setDisabled(True)
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


class LiberoCollectorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        # (setter, ko_text) pairs registered via self._reg() -- lets
        # _retranslate_ui() re-apply every static widget's text after the
        # language toggle, without needing self.xxx references for widgets
        # that otherwise wouldn't need to be kept around (e.g. plain
        # QLabels/QPushButtons local to a _build_* method).
        self._i18n_registry: list[tuple] = []
        self._reg(self.setWindowTitle, "FR3 + GELLO -- LIBERO 데이터 수집기")
        self.worker: CollectionWorker | None = None
        self.episodes_this_session = 0
        self.node_ok = True
        self.active_file_path: Path | None = None
        self.active_episode_cache: list[dict] | None = None  # None = not loaded yet
        self.agent_preview_worker: CameraPreviewWorker | None = None
        self.wrist_preview_worker: CameraPreviewWorker | None = None
        self.node_process: QProcess | None = None
        self.convert_process: QProcess | None = None
        self.hdf5_upload_process: QProcess | None = None
        self.runme_process: QProcess | None = None
        self.repack_process: QProcess | None = None
        # per-stream de-spam state, see clean_stream_lines
        self._convert_stdout_state: dict = {}
        self._convert_stderr_state: dict = {}
        self._hdf5_stdout_state: dict = {}
        self._hdf5_stderr_state: dict = {}
        self._node_restart_pending = False
        self._current_state = "idle"
        self._node_status_received = False
        self.schema_cfg = load_schema_config()

        # Persistent log -- the on-screen log panel is in-memory only and
        # disappears when the window closes or scrolls past its line cap,
        # so a live incident (e.g. the control loop dying) leaves no record
        # once it happens. One file per GUI launch, flushed on every line.
        log_dir = Path.home() / "libero_gui_logs"
        self._log_file = None
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"session_{time.strftime('%Y%m%d_%H%M%S')}.log"
            self._log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            log_path = None

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(160)

        # ---- three phases, one at a time -------------------------------
        # Everything used to be on one screen, which meant hunting for the
        # three or four widgets that mattered right now among ~40 that did
        # not. The work is genuinely sequential -- you set up once, then
        # record for a while, then curate/convert/upload -- so the window
        # follows that: 준비 -> 수집 -> 정리.
        self.video_box = self._build_video_box()

        # 준비: everything that must be decided before the robot connects.
        setup_page = QWidget()
        setup_col = QVBoxLayout(setup_page)
        setup_col.addWidget(self._build_node_box())
        setup_col.addWidget(self._build_config_box())
        setup_col.addWidget(self._build_camera_box())
        setup_col.addStretch()
        # Leaving 준비 IS connecting -- there is nothing else to do here, so
        # the transition and the action are the same button. It lives on this
        # page rather than in 제어 because 제어 only exists during 수집.
        self.connect_btn = QPushButton()
        self._reg(self.connect_btn.setText, "로봇 연결하고 수집 시작 →")
        self.connect_btn.setStyleSheet(
            "background-color: #2ecc71; color: white; font-weight: bold; padding: 10px;"
        )
        self.connect_btn.clicked.connect(self._on_connect)
        setup_col.addWidget(self.connect_btn)

        # 수집: only what the operator watches while their hands are on GELLO.
        collect_page = QWidget()
        collect_col = QVBoxLayout(collect_page)
        self.collect_video_slot = QVBoxLayout()
        collect_col.addLayout(self.collect_video_slot)
        collect_row = QHBoxLayout()
        collect_row.addWidget(self._build_gate_box(), 1)
        collect_row.addWidget(self._build_status_box(), 1)
        collect_col.addLayout(collect_row)
        collect_col.addStretch()

        # 정리: nothing here touches the robot.
        review_page = QWidget()
        review_col = QVBoxLayout(review_page)
        review_col.addWidget(self._build_dataset_box())
        review_col.addStretch()

        self.pages = QStackedWidget()
        for page in (setup_page, collect_page, review_page):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(6, 6, 6, 6)
        central_layout.addWidget(self._build_phase_bar())
        central_layout.addWidget(self.pages, 1)
        # 제어 row and the log stay outside the stack: the control row is
        # keyboard-driven during 수집 and must never move, and the log is the
        # one thing worth seeing in every phase.
        central_layout.addWidget(self._build_control_box())
        central_layout.addWidget(self.log_view)
        self.setCentralWidget(central)
        self._set_phase(0)

        self._set_controls_enabled(connected=False, gate_ok=False, recording=False, reset_wait=False)
        self._refresh_dataset_tree()
        self._refresh_cameras()
        self._refresh_tasks()

        if log_path is not None:
            self._log(f"[로그] 이 세션 로그 파일: {log_path}")
        else:
            self._log("[로그] 로그 파일 생성 실패 -- 화면에만 표시됩니다")

        # App-wide (not just this window) so the shortcut works no matter
        # which widget currently has focus -- the operator's hands are on
        # the GELLO leader, not carefully managing GUI focus.
        QApplication.instance().installEventFilter(self)

        self._run_runme_sh()

    # ------------------------------------------------------------------ i18n
    def _reg(self, setter, ko_text: str) -> None:
        """Applies `tr(ko_text)` via `setter` now, and remembers the pair so
        _retranslate_ui() can re-apply it (in whatever language is current
        then) after the language toggle. `setter` is any single-string
        setter -- setText/setTitle/setToolTip/setPlaceholderText/
        setWindowTitle all fit.
        """
        setter(tr(ko_text))
        self._i18n_registry.append((setter, ko_text))

    def _toggle_language(self) -> None:
        set_language("en" if get_language() == "ko" else "ko")
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        for setter, ko_text in self._i18n_registry:
            setter(tr(ko_text))
        self.lang_toggle_btn.setText("English" if get_language() == "ko" else "한국어")
        # Everything below has runtime-derived text (not a fixed source
        # string) -- re-derive each from the state that's already tracked
        # in instance attributes, rather than re-applying a stored string.
        self._update_schema_btn_label()
        self.state_label.setText(tr(STATE_LABELS_KO.get(self._current_state, self._current_state)))
        self.episode_label.setText(tr("에피소드: {n}").format(n=self.episodes_this_session))
        if self._node_status_received:
            self.node_label.setText(
                tr("노드: 정상") if self.node_ok else tr("노드: 응답 없음 (launch_nodes 재시작 필요)")
            )
        else:
            self.node_label.setText(tr("노드: -"))
        if self.node_process is not None and self.node_process.state() != QProcess.ProcessState.NotRunning:
            self.node_proc_label.setText(tr("실행 중 (PID {pid})").format(pid=self.node_process.processId()))
        else:
            self.node_proc_label.setText(tr("중지됨"))
        self.dataset_tree.setHeaderLabels([tr("파일 / 에피소드"), tr("프레임수"), tr("결과")])
        self._refresh_dataset_tree()

    # ------------------------------------------------------------ UI build
    def _build_node_box(self) -> QGroupBox:
        box = QGroupBox()
        self._reg(box.setTitle, "로봇 노드 (launch_nodes.py, pylibfranka-venv)")
        layout = QHBoxLayout(box)

        ip_label = QLabel()
        self._reg(ip_label.setText, "로봇 IP:")
        layout.addWidget(ip_label)
        self.robot_ip_edit = QLineEdit("172.16.0.2")
        self.robot_ip_edit.setFixedWidth(120)
        layout.addWidget(self.robot_ip_edit)

        self.node_start_btn = QPushButton()
        self._reg(self.node_start_btn.setText, "노드 시작")
        self.node_start_btn.setStyleSheet("background-color: #2ecc71;")
        self.node_start_btn.clicked.connect(self._on_start_node)
        layout.addWidget(self.node_start_btn)

        self.node_restart_btn = QPushButton()
        self._reg(self.node_restart_btn.setText, "노드 재시작")
        self.node_restart_btn.setStyleSheet("background-color: #f39c12;")
        self.node_restart_btn.clicked.connect(self._on_restart_node)
        layout.addWidget(self.node_restart_btn)

        self.node_stop_btn = QPushButton()
        self._reg(self.node_stop_btn.setText, "노드 중지")
        self.node_stop_btn.clicked.connect(self._on_stop_node)
        layout.addWidget(self.node_stop_btn)

        self.node_proc_label = QLabel(tr("중지됨"))
        self.node_proc_label.setStyleSheet("color: #888;")
        layout.addWidget(self.node_proc_label)
        layout.addStretch()

        self.lang_toggle_btn = QPushButton("English")
        self.lang_toggle_btn.setToolTip("Switch UI language / 언어 전환")
        self.lang_toggle_btn.clicked.connect(self._toggle_language)
        layout.addWidget(self.lang_toggle_btn)
        return box

    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox()
        self._reg(box.setTitle, "세션 설정 (연결 전에만 수정 가능)")
        grid = QGridLayout(box)

        task_label = QLabel()
        self._reg(task_label.setText, "Task 이름:")
        grid.addWidget(task_label, 0, 0)
        self.task_combo = QComboBox()
        self.task_combo.setEditable(True)
        self._reg(self.task_combo.lineEdit().setPlaceholderText, "예: pick up the red block")
        self._reg(self.task_combo.setToolTip, "저장 경로에 이미 있는 task를 선택하면 이어서 수집 가능 (--resume 자동 체크)")
        self.task_combo.activated.connect(self._on_task_selected)
        grid.addWidget(self.task_combo, 0, 1, 1, 3)

        lang_label = QLabel()
        self._reg(lang_label.setText, "언어 지시문:")
        grid.addWidget(lang_label, 1, 0)
        self.lang_edit = QLineEdit()
        self._reg(self.lang_edit.setPlaceholderText, "비워두면 Task 이름과 동일하게 사용")
        grid.addWidget(self.lang_edit, 1, 1, 1, 3)

        root_label = QLabel()
        self._reg(root_label.setText, "데이터 저장 경로:")
        grid.addWidget(root_label, 2, 0)
        self.root_edit = QLineEdit(str(Path.home() / "libero_datasets"))
        grid.addWidget(self.root_edit, 2, 1, 1, 2)
        browse_btn = QPushButton()
        self._reg(browse_btn.setText, "찾아보기...")
        browse_btn.clicked.connect(self._browse_root)
        grid.addWidget(browse_btn, 2, 3)

        reset_pose_label = QLabel("Reset pose:")
        grid.addWidget(reset_pose_label, 3, 0)
        self.reset_pose_combo = QComboBox()
        self.reset_pose_combo.addItems(sorted(FR3_RESET_POSES.keys()))
        self.reset_pose_combo.setCurrentText("libero")
        grid.addWidget(self.reset_pose_combo, 3, 1)

        grid.addWidget(QLabel("Grip:"), 3, 2)
        self.grip_combo = QComboBox()
        self.grip_combo.addItems(["right", "left"])
        grid.addWidget(self.grip_combo, 3, 3)

        self.wall_check = QCheckBox()
        self._reg(self.wall_check.setText, "GELLO 조인트 한계 벽(wall) 사용")
        self.wall_check.setChecked(True)
        grid.addWidget(self.wall_check, 4, 0, 1, 2)

        self.resume_check = QCheckBox()
        self._reg(self.resume_check.setText, "기존 데이터셋에 이어붙이기 (--resume)")
        grid.addWidget(self.resume_check, 4, 2, 1, 2)

        self.auto_match_check = QCheckBox()
        self._reg(
            self.auto_match_check.setText,
            "항상 같은 초기값에서 시작 (에피소드마다 리더를 리셋 포즈로 자동 정렬)",
        )
        self._reg(
            self.auto_match_check.setToolTip,
            "끄면 리더를 손으로 맞추게 되어 에피소드마다 시작 자세가 달라집니다 "
            "(초기 위치 변동). 배포는 항상 리셋 포즈에서 시작하므로, 켜면 학습과 "
            "배포의 시작 조건이 일치합니다.",
        )
        self.auto_match_check.setChecked(True)
        grid.addWidget(self.auto_match_check, 7, 0, 1, 4)

        max_seconds_label = QLabel()
        self._reg(max_seconds_label.setText, "에피소드 최대 길이(초):")
        grid.addWidget(max_seconds_label, 5, 0)
        self.max_seconds_edit = QLineEdit("20")
        grid.addWidget(self.max_seconds_edit, 5, 1)

        reset_wait_label = QLabel()
        self._reg(reset_wait_label.setText, "리셋 대기(초):")
        grid.addWidget(reset_wait_label, 5, 2)
        self.reset_wait_edit = QLineEdit("10")
        grid.addWidget(self.reset_wait_edit, 5, 3)

        self.schema_btn = QPushButton()
        self.schema_btn.clicked.connect(self._open_schema_dialog)
        grid.addWidget(self.schema_btn, 6, 0, 1, 4)
        self._update_schema_btn_label()

        return box

    # ------------------------------------------------------------- phases
    PHASES = (("1. 준비", "카메라·세션·Task 설정"),
              ("2. 수집", "텔레옵으로 에피소드 기록"),
              ("3. 정리", "큐레이션·용량 최적화·변환·업로드"))

    def _build_phase_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.phase_btns = []
        for i, (name, hint) in enumerate(self.PHASES):
            b = QPushButton(f"{name}\n{hint}")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, idx=i: self._on_phase_clicked(idx))
            row.addWidget(b, 1)
            self.phase_btns.append(b)
        return bar

    def _on_phase_clicked(self, idx: int) -> None:
        """Manual phase switch. 수집 is entered by connecting, not by clicking:
        the page is meaningless without a live worker, and clicking into it
        would suggest otherwise."""
        if idx == 1 and self.worker is None:
            self._set_phase(self.pages.currentIndex())  # undo the button check
            QMessageBox.information(
                self, tr("먼저 로봇 연결"),
                tr("'수집' 단계는 로봇을 연결하면 자동으로 열립니다. 준비 단계에서 '로봇 연결'을 누르세요."),
            )
            return
        if self.worker is not None and idx != 1:
            self._set_phase(1)
            QMessageBox.information(
                self, tr("수집 중"),
                tr("수집이 진행 중입니다. 다른 단계로 가려면 먼저 '세션 종료'를 누르세요."),
            )
            return
        self._set_phase(idx)

    def _set_phase(self, idx: int) -> None:
        self.pages.setCurrentIndex(idx)
        for i, b in enumerate(self.phase_btns):
            b.setChecked(i == idx)
            b.setStyleSheet(
                "font-weight: bold; background-color: #3498db; color: white;"
                if i == idx else "color: #888;"
            )
        # The live preview follows the operator between 준비 and 수집.
        slot = self.collect_video_slot if idx == 1 else self.setup_video_slot
        slot.addWidget(self.video_box)
        self.video_box.setVisible(idx != 2)
        # 제어 is only meaningful while a session exists.
        self.control_box.setVisible(idx == 1)
        if idx == 2:
            self._refresh_dataset_tree()

    def _build_camera_box(self) -> QGroupBox:
        """Camera selection, refresh and live preview in one place.

        Selection used to sit in the session-config grid and the preview in a
        separate box at the other end of the window, so choosing a serial and
        seeing what it points at meant looking in two places. Both belong to
        the same question -- "is this the right camera, aimed correctly?" --
        which is answered once, during setup.
        """
        box = QGroupBox()
        self._reg(box.setTitle, "카메라")
        layout = QVBoxLayout(box)

        grid = QGridLayout()
        agent_cam_label = QLabel()
        self._reg(agent_cam_label.setText, "Agentview 카메라:")
        grid.addWidget(agent_cam_label, 0, 0)
        self.agent_cam_combo = QComboBox()
        self.agent_cam_combo.setEditable(True)
        self._reg(self.agent_cam_combo.setToolTip, "연결된 RealSense 목록에서 선택하거나 시리얼번호를 직접 입력")
        self.agent_cam_combo.currentIndexChanged.connect(lambda _: self._on_camera_selection_changed("agent"))
        self.agent_cam_combo.lineEdit().editingFinished.connect(
            lambda: self._on_camera_selection_changed("agent")
        )
        grid.addWidget(self.agent_cam_combo, 0, 1)

        wrist_cam_label = QLabel()
        self._reg(wrist_cam_label.setText, "Wrist 카메라:")
        grid.addWidget(wrist_cam_label, 0, 2)
        self.wrist_cam_combo = QComboBox()
        self.wrist_cam_combo.setEditable(True)
        self._reg(self.wrist_cam_combo.setToolTip, "연결된 RealSense 목록에서 선택하거나 시리얼번호를 직접 입력")
        self.wrist_cam_combo.currentIndexChanged.connect(lambda _: self._on_camera_selection_changed("wrist"))
        self.wrist_cam_combo.lineEdit().editingFinished.connect(
            lambda: self._on_camera_selection_changed("wrist")
        )
        grid.addWidget(self.wrist_cam_combo, 0, 3)

        self.cam_refresh_btn = QPushButton()
        self._reg(self.cam_refresh_btn.setText, "카메라 목록 새로고침")
        self.cam_refresh_btn.clicked.connect(self._refresh_cameras)
        grid.addWidget(self.cam_refresh_btn, 1, 0, 1, 2)

        self.camera_hint = QLabel("")
        self.camera_hint.setStyleSheet("color: #888;")
        grid.addWidget(self.camera_hint, 1, 2, 1, 2)
        layout.addLayout(grid)

        # The live preview is needed while aiming (준비) AND while recording
        # (수집), but a QWidget has exactly one parent -- so it is built once
        # and moved between the two pages' slots by _set_phase().
        self.setup_video_slot = QVBoxLayout()
        layout.addLayout(self.setup_video_slot)
        return box

    def _camera_serial_from_combo(self, combo: QComboBox) -> str:
        """Returns the serial for the combo's current selection.

        ``QComboBox.setEditText``/manual typing leaves ``currentIndex()`` (and
        thus ``currentData()``) pointing at whatever was selected before --
        Qt does not clear it just because the displayed text no longer
        matches that item. So itemData is only trusted when the visible text
        still equals that item's text; otherwise the text is parsed as a
        manually-entered serial (accepting either a raw serial or a
        "name (serial)" string copied from the list).
        """
        idx = combo.currentIndex()
        text = combo.currentText().strip()
        if idx >= 0 and combo.itemText(idx) == text:
            # Pointing at a real list entry (incl. "(선택 안함)", whose data is
            # "") -- trust itemData as-is, don't fall through to text-parsing.
            return str(combo.itemData(idx) or "")
        if text.endswith(")") and "(" in text:
            return text[text.rfind("(") + 1 : -1].strip()
        return text

    def _refresh_cameras(self) -> None:
        """Re-scans connected RealSense devices and repopulates both combo
        boxes, preserving each combo's current selection (by serial, or a
        manually-typed serial) if possible. Defaults to "(선택 안함)" -- no
        camera is ever auto-selected, since selecting one opens a live
        preview (see ``_on_camera_selection_changed``).
        """
        try:
            from lerobot.cameras.realsense import RealSenseCamera

            cams = RealSenseCamera.find_cameras()
        except Exception as e:  # noqa: BLE001
            self.camera_hint.setText(tr("카메라 목록 조회 실패: {err}").format(err=f"{type(e).__name__}: {e}"))
            self._log(f"[카메라] 목록을 가져오지 못했습니다: {type(e).__name__}: {e}")
            return

        for combo in (self.agent_cam_combo, self.wrist_cam_combo):
            prev = self._camera_serial_from_combo(combo)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("(선택 안함)"), "")
            for c in cams:
                combo.addItem(f"{c.get('name', '?')} ({c['id']})", c["id"])
            idx = combo.findData(prev) if prev else 0
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(prev)
            combo.blockSignals(False)

        if cams:
            self.camera_hint.setText(tr("{n}대 감지됨").format(n=len(cams)))
        else:
            self.camera_hint.setText(tr("감지된 RealSense 카메라가 없습니다 (시리얼번호 직접 입력 가능)"))
        self._log(f"[카메라] {len(cams)}대 감지됨: " + ", ".join(f"{c.get('name', '?')}={c['id']}" for c in cams))

        # blockSignals() suppressed currentIndexChanged during repopulation --
        # sync the preview to whatever ended up selected.
        self._on_camera_selection_changed("agent")
        self._on_camera_selection_changed("wrist")

    def _update_schema_btn_label(self) -> None:
        action_label = tr(ACTION_SPACE_LABELS.get(
            self.schema_cfg.action_space, self.schema_cfg.action_space
        ))
        self.schema_btn.setText(
            tr("데이터셋 구조: action = {action} -- 편집...").format(action=action_label)
        )

    def _open_schema_dialog(self) -> None:
        dlg = DatasetSchemaDialog(self, self.schema_cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.schema_cfg = dlg.result_config()
            save_schema_config(self.schema_cfg)
            self._update_schema_btn_label()
            self._log(f"[구조] 저장됨 (action={self.schema_cfg.action_space})")

    def _on_camera_selection_changed(self, role: str) -> None:
        combo = self.agent_cam_combo if role == "agent" else self.wrist_cam_combo
        self._start_preview(role, self._camera_serial_from_combo(combo))

    def _start_preview(self, role: str, serial: str) -> None:
        """(Re)starts the standalone preview for one camera role. No-ops
        while a recording session owns the cameras (self.worker is not
        None) -- a preview thread and CollectionWorker can't hold the same
        RealSense pipeline open at once.
        """
        view = self.agent_view if role == "agent" else self.wrist_view
        old = self.agent_preview_worker if role == "agent" else self.wrist_preview_worker
        if old is not None:
            old.stop()
            old.wait(3000)
            if role == "agent":
                self.agent_preview_worker = None
            else:
                self.wrist_preview_worker = None

        if self.worker is not None:
            return  # a session is active; it owns whichever cameras it connected to

        if not serial:
            view.setPixmap(QPixmap())
            view.setText(tr("선택 안함 -- 대기 중"))
            return

        view.setPixmap(QPixmap())
        view.setText(tr("{serial} 미리보기 연결 중...").format(serial=serial))
        w = CameraPreviewWorker(serial)
        w.frame_ready.connect(lambda frame, r=role, ww=w: self._on_preview_frame(r, frame, ww))
        w.error.connect(lambda msg, r=role, ww=w: self._on_preview_error(r, msg, ww))
        if role == "agent":
            self.agent_preview_worker = w
        else:
            self.wrist_preview_worker = w
        w.start()

    def _stop_previews(self) -> bool:
        """Stops any running preview threads and returns whether both fully
        exited (each preview's ``finally`` calls ``cam.disconnect()`` before
        its thread returns, so a clean stop here guarantees the RealSense
        pipeline was released). Also waits out a short settle delay:
        librealsense can transiently fail to reopen the same USB device
        immediately after a pipeline.stop(), even once the old handle is
        gone, so callers that are about to reopen the same serials (session
        connect) should treat a False return / rely on this delay rather
        than reconnecting instantly.
        """
        any_stopped = False
        all_ok = True
        for role in ("agent", "wrist"):
            w = self.agent_preview_worker if role == "agent" else self.wrist_preview_worker
            if w is not None:
                any_stopped = True
                w.stop()
                ok = w.wait(3000)
                if not ok:
                    all_ok = False
                    self._log(f"[카메라] {role} 미리보기 스레드가 3초 내에 종료되지 않았습니다")
                if role == "agent":
                    self.agent_preview_worker = None
                else:
                    self.wrist_preview_worker = None
        if any_stopped and all_ok:
            time.sleep(0.5)  # let the RealSense driver actually release the USB device
        return all_ok

    def _on_preview_frame(self, role: str, frame, worker: "CameraPreviewWorker") -> None:
        current = self.agent_preview_worker if role == "agent" else self.wrist_preview_worker
        if current is not worker:
            return  # stale frame from a preview that's since been replaced/stopped
        view = self.agent_view if role == "agent" else self.wrist_view
        view.setPixmap(
            np_to_pixmap(frame).scaled(
                view.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def _on_preview_error(self, role: str, msg: str, worker: "CameraPreviewWorker") -> None:
        current = self.agent_preview_worker if role == "agent" else self.wrist_preview_worker
        if current is not worker:
            return
        view = self.agent_view if role == "agent" else self.wrist_view
        view.setText(tr("미리보기 실패: {msg}").format(msg=msg))
        self._log(f"[카메라 미리보기 실패] {role}: {msg}")

    def _build_video_box(self) -> QGroupBox:
        box = QGroupBox()
        self._reg(box.setTitle, "카메라")
        layout = QHBoxLayout(box)
        self.agent_view = QLabel(tr("선택 안함 -- 대기 중"))
        self.agent_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.agent_view.setMinimumSize(400, 300)
        self.agent_view.setStyleSheet("background-color: #111; color: #888;")
        self.wrist_view = QLabel(tr("선택 안함 -- 대기 중"))
        self.wrist_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wrist_view.setMinimumSize(400, 300)
        self.wrist_view.setStyleSheet("background-color: #111; color: #888;")
        col_a = QVBoxLayout()
        agent_label = QLabel()
        self._reg(agent_label.setText, "Agentview (외부 카메라)")
        col_a.addWidget(agent_label)
        col_a.addWidget(self.agent_view)
        col_w = QVBoxLayout()
        wrist_label = QLabel()
        self._reg(wrist_label.setText, "Eye-in-hand (손목 카메라)")
        col_w.addWidget(wrist_label)
        col_w.addWidget(self.wrist_view)
        layout.addLayout(col_a)
        layout.addLayout(col_w)
        return box

    def _build_gate_box(self) -> QGroupBox:
        box = QGroupBox()
        self._reg(box.setTitle, "자세 매칭 (leader vs follower, 게이트 {gate} rad)".format(gate=GATE_RAD))
        layout = QVBoxLayout(box)
        self.delta_bars = [DeltaBar(name) for name in JOINT_LABELS]
        for bar in self.delta_bars:
            layout.addWidget(bar)
        self.gate_summary = QLabel(tr("로봇 연결 후 표시됩니다"))
        layout.addWidget(self.gate_summary)
        return box

    def _build_control_box(self) -> QGroupBox:
        box = QGroupBox()
        self.control_box = box  # _set_phase() shows it only during 수집
        self._reg(box.setTitle, "제어")
        layout = QHBoxLayout(box)

        self.start_teleop_btn = QPushButton()
        self._reg(self.start_teleop_btn.setText, "텔레옵 시작 (Space)")
        self.start_teleop_btn.clicked.connect(self._on_start_teleop)
        layout.addWidget(self.start_teleop_btn)

        self.skip_reset_btn = QPushButton()
        self._reg(self.skip_reset_btn.setText, "리셋 대기 건너뛰기 (Enter)")
        self.skip_reset_btn.clicked.connect(self._on_skip_reset)
        layout.addWidget(self.skip_reset_btn)

        self.save_success_btn = QPushButton()
        self._reg(self.save_success_btn.setText, "저장 (성공) (Space)")
        self.save_success_btn.setStyleSheet("background-color: #2ecc71;")
        self.save_success_btn.clicked.connect(lambda: self._on_save(True))
        layout.addWidget(self.save_success_btn)

        self.save_fail_btn = QPushButton()
        self._reg(self.save_fail_btn.setText, "저장 (실패) (Esc)")
        self.save_fail_btn.setStyleSheet("background-color: #f1c40f;")
        self.save_fail_btn.clicked.connect(lambda: self._on_save(False))
        layout.addWidget(self.save_fail_btn)

        self.discard_btn = QPushButton()
        self._reg(self.discard_btn.setText, "폐기 (Delete)")
        self.discard_btn.clicked.connect(self._on_discard)
        layout.addWidget(self.discard_btn)

        self.go_home_btn = QPushButton()
        self._reg(self.go_home_btn.setText, "홈으로 이동")
        self.go_home_btn.setStyleSheet("background-color: #3498db; color: white;")
        self.go_home_btn.clicked.connect(self._on_go_home)
        layout.addWidget(self.go_home_btn)

        self.quit_btn = QPushButton()
        self._reg(self.quit_btn.setText, "세션 종료")
        self.quit_btn.setStyleSheet("background-color: #e74c3c; color: white;")
        self.quit_btn.clicked.connect(self._on_quit)
        layout.addWidget(self.quit_btn)

        return box

    def _build_status_box(self) -> QGroupBox:
        box = QGroupBox()
        self._reg(box.setTitle, "상태")
        outer = QVBoxLayout(box)
        status_row = QHBoxLayout()
        self.state_label = QLabel(tr("대기"))
        self.state_label.setStyleSheet("font-weight: bold; font-size: 14pt;")
        self.node_label = QLabel(tr("노드: -"))
        self.episode_label = QLabel(tr("에피소드: {n}").format(n=0))
        self.progress_label = QLabel("")
        self.save_label = QLabel("")  # 백그라운드 저장 상태 (EpisodeSaver.save_status)
        self.save_label.setStyleSheet("color: #888;")
        for w in (self.state_label, self.node_label, self.episode_label, self.progress_label,
                  self.save_label):
            status_row.addWidget(w)
        status_row.addStretch()
        outer.addLayout(status_row)

        note = QLabel()
        self._reg(
            note.setText,
            "⚠ 이 화면의 버튼은 소프트웨어 텔레옵 루프만 멈춥니다. "
            "실제 비상정지는 로봇 하드웨어 E-stop을 사용하세요.",
        )
        note.setStyleSheet("color: #e67e22;")
        note.setWordWrap(True)
        outer.addWidget(note)
        return box

    def _build_dataset_box(self) -> QGroupBox:
        box = QGroupBox()
        self._reg(box.setTitle, "데이터셋 탐색기 (저장 경로 아래의 *_demo.hdf5)")
        layout = QVBoxLayout(box)

        self.dataset_tree = QTreeWidget()
        self.dataset_tree.setColumnCount(3)
        self.dataset_tree.setHeaderLabels([tr("파일 / 에피소드"), tr("프레임수"), tr("결과")])
        self.dataset_tree.setMaximumHeight(220)
        layout.addWidget(self.dataset_tree)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton()
        self._reg(refresh_btn.setText, "새로고침")
        refresh_btn.clicked.connect(self._refresh_dataset_tree)
        btn_row.addWidget(refresh_btn)
        delete_btn = QPushButton()
        self._reg(delete_btn.setText, "선택 삭제")
        delete_btn.setStyleSheet("background-color: #e74c3c; color: white;")
        delete_btn.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(delete_btn)
        structure_btn = QPushButton()
        self._reg(structure_btn.setText, "선택 구조 확인...")
        structure_btn.clicked.connect(self._on_show_structure)
        btn_row.addWidget(structure_btn)
        self.repack_btn = QPushButton()
        self._reg(self.repack_btn.setText, "용량 최적화 (재압축)")
        self.repack_btn.setStyleSheet("background-color: #9b59b6; color: white;")
        self.repack_btn.clicked.connect(self._on_repack)
        btn_row.addWidget(self.repack_btn)
        self.lerobot_convert_btn = QPushButton()
        self._reg(self.lerobot_convert_btn.setText, "LeRobot 변환/업로드...")
        self.lerobot_convert_btn.clicked.connect(self._open_lerobot_convert)
        btn_row.addWidget(self.lerobot_convert_btn)
        self.hdf5_upload_btn = QPushButton()
        self._reg(self.hdf5_upload_btn.setText, "HDF5 업로드...")
        self.hdf5_upload_btn.clicked.connect(self._open_hdf5_upload)
        btn_row.addWidget(self.hdf5_upload_btn)
        btn_row.addStretch()
        hint = QLabel()
        self._reg(
            hint.setText,
            "HDF5는 삭제해도 파일 용량이 즉시 줄지 않습니다 (디스크 공간까지 "
            "회수하려면 h5repack 필요).",
        )
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        layout.addLayout(btn_row)
        layout.addWidget(hint)
        return box

    # -------------------------------------------------------------- helpers
    def _log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)
        if self._log_file is not None:
            ts = time.strftime("%H:%M:%S")
            try:
                self._log_file.write(f"[{ts}] {msg}\n")
                self._log_file.flush()
            except Exception:  # noqa: BLE001
                pass

    def _browse_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("데이터 저장 경로"), self.root_edit.text())
        if d:
            self.root_edit.setText(d)
            self._refresh_dataset_tree()

    def _refresh_tasks(self) -> None:
        """Re-scans the data root for ``<task>_demo.hdf5`` files and repopulates
        the Task 이름 combo, so a previous task can be picked instead of
        retyped. Filenames use ``LiberoTaskWriter``'s exact slugification
        (spaces -> underscores), reversed here to recover the task name --
        so re-selecting an entry and connecting targets the same file.

        Non-recursive on purpose -- only files directly under 데이터 저장
        경로 itself, not subdirectories, so what's listed always matches
        exactly where a new session would actually write (LiberoTaskWriter
        never creates subdirectories of its own).
        """
        prev = self.task_combo.currentText()
        root = Path(self.root_edit.text()).expanduser()
        tasks = []
        if root.is_dir():
            for path in sorted(root.glob("*_demo.hdf5")):
                stem = path.stem
                if stem.endswith("_demo"):
                    stem = stem[: -len("_demo")]
                tasks.append(stem.replace("_", " "))
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        self.task_combo.addItems(tasks)
        idx = self.task_combo.findText(prev)
        if idx >= 0:
            self.task_combo.setCurrentIndex(idx)
        else:
            self.task_combo.setEditText(prev)
        self.task_combo.blockSignals(False)

    def _on_task_selected(self, index: int) -> None:
        """Fires only when the operator explicitly picks a dropdown entry
        (not on every keystroke of a new task name). Prefills the language
        instruction, non-camera session settings (reset pose, grip, wall,
        episode/reset timing), and the dataset schema from the existing
        file's own last-used values, and checks --resume -- picking an
        existing task almost always means continuing it exactly as it was,
        not starting a differently-configured session against the same
        file. Camera selection is deliberately left alone -- which physical
        camera is plugged in now has nothing to do with what a previous
        session used.
        """
        task = self.task_combo.itemText(index)
        root = Path(self.root_edit.text()).expanduser()
        safe_name = task.strip().replace(" ", "_")
        # Non-recursive, matching _refresh_tasks()/_refresh_dataset_tree()'s
        # own search -- only files directly under 데이터 저장 경로.
        path = root / f"{safe_name}_demo.hdf5"
        if not path.exists():
            return
        self.resume_check.setChecked(True)
        try:
            with h5py.File(path, "r") as f:
                data = f["data"]
                info = json.loads(data.attrs["problem_info"])
                lang = info.get("language_instruction", "")
                if len(lang) >= 2 and lang.startswith('"') and lang.endswith('"'):
                    lang = lang[1:-1]
                self.lang_edit.setText(lang)

                session_cfg_raw = data.attrs.get("session_config")
                if session_cfg_raw:
                    session_cfg = json.loads(session_cfg_raw)
                    if session_cfg.get("reset_pose") in FR3_RESET_POSES:
                        self.reset_pose_combo.setCurrentText(session_cfg["reset_pose"])
                    if session_cfg.get("grip") in ("right", "left"):
                        self.grip_combo.setCurrentText(session_cfg["grip"])
                    if "enable_wall" in session_cfg:
                        self.wall_check.setChecked(bool(session_cfg["enable_wall"]))
                    if "max_episode_seconds" in session_cfg:
                        self.max_seconds_edit.setText(str(session_cfg["max_episode_seconds"]))
                    if "reset_wait_seconds" in session_cfg:
                        self.reset_wait_edit.setText(str(session_cfg["reset_wait_seconds"]))

                names = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
                if names:
                    # Last episode, not first -- a --resume'd file may mix
                    # schemas across sessions; the last one is what a new
                    # episode would actually continue from.
                    self.schema_cfg = schema_from_episode(data[names[-1]])
                    self._update_schema_btn_label()
        except Exception as e:  # noqa: BLE001
            self._log(f"[Task] 기존 정보를 읽지 못했습니다: {type(e).__name__}: {e}")

    def _refresh_dataset_tree(self) -> None:
        """Rebuilds the file/episode tree from disk.

        The currently-open task file is NOT reopened here -- only the worker
        thread may touch that h5py.File handle (see
        gello/libero_gui_worker.py's ``_handle_delete_episode`` docstring).
        Its children come from ``self.active_episode_cache`` instead.
        """
        self.dataset_tree.clear()
        root = Path(self.root_edit.text()).expanduser()
        if not root.is_dir():
            return
        # Non-recursive -- only files directly under 데이터 저장 경로, not
        # subdirectories (a nested folder might hold older/archived data
        # that isn't meant to show up as part of the current working set).
        for path in sorted(root.glob("*_demo.hdf5")):
            file_item = QTreeWidgetItem([str(path), "", ""])
            file_item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.dataset_tree.addTopLevelItem(file_item)

            if self.active_file_path is not None and path.samefile(self.active_file_path):
                if self.active_episode_cache is None:
                    # Connected, but the worker's first episode_list_changed
                    # hasn't arrived yet -- don't claim 0 episodes.
                    file_item.setText(1, tr("불러오는 중..."))
                    continue
                episodes = self.active_episode_cache
            else:
                try:
                    with h5py.File(path, "r") as f:
                        data = f["data"]
                        episodes = []
                        for name in data.keys():
                            grp = data[name]
                            success = grp.attrs.get("success")
                            episodes.append(
                                {
                                    "name": name,
                                    "num_samples": int(grp.attrs.get("num_samples", 0)),
                                    "success": None if success is None else bool(success),
                                }
                            )
                        episodes.sort(key=lambda d: int(d["name"].split("_")[1]))
                except OSError as e:
                    file_item.setText(1, tr("(읽기 실패: {e})").format(e=e))
                    continue

            for ep in episodes:
                success = ep["success"]
                result = "-" if success is None else (tr("성공") if success else tr("실패"))
                child = QTreeWidgetItem(["  " + ep["name"], str(ep["num_samples"]), result])
                child.setData(0, Qt.ItemDataRole.UserRole, ep["name"])
                file_item.addChild(child)
            file_item.setText(1, tr("{n}개 에피소드").format(n=len(episodes)))
        self.dataset_tree.expandAll()
        self._refresh_tasks()

    def _on_delete_selected(self) -> None:
        items = self.dataset_tree.selectedItems()
        if not items:
            return
        item = items[0]
        parent = item.parent()

        if parent is None:
            # whole-file deletion
            file_path = Path(item.data(0, Qt.ItemDataRole.UserRole))
            if self.active_file_path is not None and file_path.samefile(self.active_file_path):
                QMessageBox.warning(
                    self, tr("삭제 불가"), tr("현재 세션이 사용 중인 파일입니다. 먼저 '세션 종료'를 누르세요.")
                )
                return
            reply = QMessageBox.question(
                self,
                tr("파일 전체 삭제"),
                tr("{name}\n이 작업의 데이터 전체(모든 에피소드)가 영구 삭제됩니다. 계속할까요?").format(
                    name=file_path.name
                ),
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            file_path.unlink()
            self._log(f"[삭제] 파일 전체: {file_path}")
            self._refresh_dataset_tree()
            return

        # single-episode deletion
        file_path = Path(parent.data(0, Qt.ItemDataRole.UserRole))
        demo_name = item.data(0, Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, tr("에피소드 삭제"), tr("{name} / {demo} 를 삭제할까요?").format(name=file_path.name, demo=demo_name)
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.active_file_path is not None and file_path.samefile(self.active_file_path):
            if self.worker is None:
                QMessageBox.warning(self, tr("삭제 불가"), tr("세션이 종료되었습니다. 새로고침 후 다시 시도하세요."))
                return
            self.worker.cmd_delete_episode(demo_name)  # worker deletes + re-emits episode_list_changed
        else:
            with h5py.File(file_path, "a") as f:
                data = f["data"]
                del data[demo_name]
                renumber_episodes(data)
            self._log(f"[삭제] {file_path.name} / {demo_name}")
            self._refresh_dataset_tree()

    def _on_show_structure(self) -> None:
        """Shows the ACTUAL on-disk structure of the selected file/episode
        (describe_episode, reading real attrs/array shapes) -- distinct from
        the schema dialog's "구조 미리보기" (describe_schema), which shows
        what a *future* recording would write, not what an existing file
        already contains.
        """
        items = self.dataset_tree.selectedItems()
        if not items:
            QMessageBox.information(self, tr("선택 필요"), tr("구조를 확인할 파일 또는 에피소드를 선택하세요."))
            return
        item = items[0]
        parent = item.parent()
        if parent is None:
            file_path = Path(item.data(0, Qt.ItemDataRole.UserRole))
            demo_name = None  # describe the file's first episode below
        else:
            file_path = Path(parent.data(0, Qt.ItemDataRole.UserRole))
            demo_name = item.data(0, Qt.ItemDataRole.UserRole)

        note = ""
        if self.active_file_path is not None and file_path.samefile(self.active_file_path):
            # Currently open (in "a" mode) by the worker thread -- only that
            # thread may touch its h5py.File handle (see
            # gello/libero_gui_worker.py's _handle_delete_episode docstring),
            # so this reads the writer's own schema instead of the file.
            # Accurate for episodes saved so far *this session*; older
            # episodes in a --resume'd file may differ.
            schema = self.worker.current_schema() if self.worker is not None else None
            if schema is None:
                QMessageBox.warning(self, tr("확인 불가"), tr("세션이 아직 완전히 연결되지 않았습니다."))
                return
            note = "[현재 세션이 기록 중인 구조 -- 이 파일의 다른 에피소드는 다를 수 있음]\n\n"
            text = note + describe_schema(schema)
            title = tr("구조 확인 -- {name} (진행 중인 세션)").format(name=file_path.name)
        else:
            try:
                with h5py.File(file_path, "r") as f:
                    data = f["data"]
                    if demo_name is None:
                        names = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
                        if not names:
                            QMessageBox.information(self, tr("빈 파일"), tr("에피소드가 없습니다."))
                            return
                        demo_name = names[0]
                        note = f"[파일의 첫 에피소드({demo_name}) 기준 -- 다른 에피소드는 다를 수 있음]\n\n"
                    text = note + describe_episode(data[demo_name])
            except (OSError, KeyError) as e:
                QMessageBox.warning(self, tr("읽기 실패"), f"{type(e).__name__}: {e}")
                return
            title = tr("구조 확인 -- {name} / {demo}").format(name=file_path.name, demo=demo_name)

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        layout = QVBoxLayout(dlg)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(QFont("monospace"))
        view.setMinimumSize(480, 360)
        layout.addWidget(view)
        close_btn = QPushButton(tr("닫기"))
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()

    def _set_controls_enabled(
        self, connected: bool, gate_ok: bool, recording: bool, reset_wait: bool
    ) -> None:
        self.connect_btn.setEnabled(not connected)
        for w in (
            self.task_combo,
            self.lang_edit,
            self.root_edit,
            self.reset_pose_combo,
            self.grip_combo,
            self.wall_check,
            self.resume_check,
            self.auto_match_check,
            self.max_seconds_edit,
            self.reset_wait_edit,
            self.agent_cam_combo,
            self.wrist_cam_combo,
            self.cam_refresh_btn,
            self.schema_btn,
        ):
            w.setEnabled(not connected)
        self.start_teleop_btn.setEnabled(connected and not recording and self.node_ok)
        self.skip_reset_btn.setEnabled(connected and reset_wait and self.node_ok)
        self.save_success_btn.setEnabled(connected and recording and self.node_ok)
        self.save_fail_btn.setEnabled(connected and recording and self.node_ok)
        self.discard_btn.setEnabled(connected and recording and self.node_ok)
        self.go_home_btn.setEnabled(connected and self.node_ok)
        self.quit_btn.setEnabled(connected)
        # Converting while a session is active can race the worker's open
        # h5py.File handle on the same file (BlockingIOError) -- keep it to
        # after "세션 종료" like the rest of the dataset browser. Also don't
        # re-enable here if a conversion is already running (this method
        # gets called on unrelated state transitions too, e.g. session end).
        converting = (
            self.convert_process is not None
            and self.convert_process.state() != QProcess.ProcessState.NotRunning
        )
        self.lerobot_convert_btn.setEnabled(not connected and not converting)
        # Same reasoning as lerobot_convert_btn -- upload after curation is
        # done and the session's ended, not against a file still being
        # actively recorded to.
        uploading = (
            self.hdf5_upload_process is not None
            and self.hdf5_upload_process.state() != QProcess.ProcessState.NotRunning
        )
        self.hdf5_upload_btn.setEnabled(not connected and not uploading)

    # -------------------------------------------------------------- actions
    def _on_connect(self) -> None:
        task = self.task_combo.currentText().strip()
        if not task:
            QMessageBox.warning(self, tr("Task 이름 필요"), tr("Task 이름을 입력하세요."))
            return
        lang = self.lang_edit.text().strip() or task
        try:
            max_seconds = float(self.max_seconds_edit.text())
            reset_wait = float(self.reset_wait_edit.text())
        except ValueError:
            QMessageBox.warning(self, tr("입력 오류"), tr("에피소드 길이/리셋 대기는 숫자여야 합니다."))
            return

        agent_serial = self._camera_serial_from_combo(self.agent_cam_combo)
        wrist_serial = self._camera_serial_from_combo(self.wrist_cam_combo)
        if not agent_serial or not wrist_serial:
            QMessageBox.warning(self, tr("카메라 선택 필요"), tr("Agentview / Wrist 카메라를 모두 선택하세요."))
            return
        if agent_serial == wrist_serial:
            QMessageBox.warning(self, tr("카메라 중복"), tr("Agentview와 Wrist에 같은 카메라가 선택되었습니다."))
            return

        # Release the preview pipelines before the session opens the same
        # serials -- RealSense can't have two pipelines on one device at once.
        self._log("[카메라] 미리보기 정지 중...")
        if not self._stop_previews():
            QMessageBox.critical(
                self,
                tr("카메라 해제 실패"),
                tr("미리보기 카메라를 제때 해제하지 못했습니다. 잠시 후 다시 시도하세요."),
            )
            return

        cfg = WorkerConfig(
            task_name=task,
            language_instruction=lang,
            data_root=self.root_edit.text(),
            grip=self.grip_combo.currentText(),
            reset_pose=self.reset_pose_combo.currentText(),
            max_episode_seconds=max_seconds,
            reset_wait_seconds=reset_wait,
            enable_wall=self.wall_check.isChecked(),
            auto_match_pose=self.auto_match_check.isChecked(),
            resume=self.resume_check.isChecked(),
            agent_camera_serial=agent_serial,
            wrist_camera_serial=wrist_serial,
            schema=self.schema_cfg,
        )
        self.worker = CollectionWorker(cfg)
        self.worker.state_changed.connect(self._on_state_changed)
        self.worker.frames_ready.connect(self._on_frames)
        self.worker.gate_status.connect(self._on_gate_status)
        self.worker.episode_progress.connect(self._on_episode_progress)
        self.worker.episode_saved.connect(self._on_episode_saved)
        self.worker.episode_discarded.connect(self._on_episode_discarded)
        self.worker.reset_countdown.connect(self._on_reset_countdown)
        self.worker.log_message.connect(self._log)
        self.worker.node_status.connect(self._on_node_status)
        self.worker.fatal_error.connect(self._on_fatal_error)
        self.worker.connected.connect(self._on_connected)
        self.worker.episode_list_changed.connect(self._on_episode_list_changed)
        self.worker.session_summary.connect(self._on_session_summary)
        self.worker.finished.connect(self._on_worker_finished)
        # 백그라운드 저장 스레드(EpisodeSaver): 저장/삭제 완료·목록 갱신·상태 표시는 여기서 옴
        self.worker.saver.episode_saved.connect(self._on_episode_saved)
        self.worker.saver.episode_list_changed.connect(self._on_episode_list_changed)
        self.worker.saver.log_message.connect(self._log)
        self.worker.saver.save_status.connect(self._on_save_status)

        self.active_episode_cache = None  # drop any stale cache from a previous session
        self._log(
            f"[연결] task={task!r} root={cfg.data_root} "
            f"agent_cam={agent_serial} wrist_cam={wrist_serial}"
        )
        self.connect_btn.setEnabled(False)
        self.worker.start()

    @pyqtSlot(int, str)
    def _on_connected(self, start_count: int, file_path: str) -> None:
        self.episodes_this_session = start_count
        self.episode_label.setText(tr("에피소드: {n}").format(n=start_count))
        self.active_file_path = Path(file_path)
        self._log(f"[연결 완료] 기존 {start_count}개 에피소드 ({file_path})")
        self._set_controls_enabled(connected=True, gate_ok=False, recording=False, reset_wait=False)
        self._set_phase(1)  # 준비 -> 수집

    @pyqtSlot(list)
    def _on_episode_list_changed(self, episodes: list) -> None:
        self.active_episode_cache = episodes
        self._refresh_dataset_tree()

    @pyqtSlot(str)
    def _on_state_changed(self, state: str) -> None:
        self._current_state = state
        self.state_label.setText(tr(STATE_LABELS_KO.get(state, state)))
        recording = state == "recording"
        reset_wait = state == "reset_wait"
        self._set_controls_enabled(
            connected=True, gate_ok=(state == "gate"), recording=recording, reset_wait=reset_wait
        )
        if state != "recording":
            self.progress_label.setText("")

    @pyqtSlot(object, object)
    def _on_frames(self, agent_rgb, wrist_rgb) -> None:
        if agent_rgb is not None:
            self.agent_view.setPixmap(
                np_to_pixmap(agent_rgb).scaled(
                    self.agent_view.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
            )
        if wrist_rgb is not None:
            self.wrist_view.setPixmap(
                np_to_pixmap(wrist_rgb).scaled(
                    self.wrist_view.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
            )

    @pyqtSlot(object, object, bool)
    def _on_gate_status(self, leader, follower, all_ok) -> None:
        deltas = np.asarray(leader) - np.asarray(follower)
        for bar, d in zip(self.delta_bars, deltas):
            bar.update_delta(float(d), GATE_RAD)
        if all_ok:
            self.gate_summary.setText(tr("모든 조인트 일치 -- '텔레옵 시작'을 누르세요"))
            self.gate_summary.setStyleSheet("color: #2ecc71; font-weight: bold;")
        else:
            worst = int(np.argmax(np.abs(deltas)))
            self.gate_summary.setText(
                tr("{joint} 조인트를 맞춰주세요 (차이 {diff} rad)").format(
                    joint=JOINT_LABELS[worst], diff=f"{deltas[worst]:+.3f}"
                )
            )
            self.gate_summary.setStyleSheet("color: #e74c3c;")

    @pyqtSlot(int, float)
    def _on_episode_progress(self, n_frames, seconds) -> None:
        self.progress_label.setText(tr("기록 중: {n} 프레임 ({s}s)").format(n=n_frames, s=f"{seconds:.1f}"))

    @pyqtSlot(str, int)
    def _on_episode_saved(self, demo_name, n_frames) -> None:
        self.episodes_this_session += 1
        self.episode_label.setText(tr("에피소드: {n}").format(n=self.episodes_this_session))
        # 저장 로그는 EpisodeSaver.log_message가 소요 시간과 함께 남김

    @pyqtSlot(str)
    def _on_save_status(self, text) -> None:
        self.save_label.setText(text)

    @pyqtSlot(int)
    def _on_episode_discarded(self, n_frames) -> None:
        self._log(f"[폐기] {n_frames} 프레임")

    @pyqtSlot(float)
    def _on_reset_countdown(self, remaining) -> None:
        self.progress_label.setText(
            tr("환경 리셋 대기: {s}s (건너뛰려면 버튼 클릭)").format(s=f"{remaining:.0f}")
        )

    @pyqtSlot(bool)
    def _on_node_status(self, ok) -> None:
        self.node_ok = ok
        self._node_status_received = True
        self.node_label.setText(tr("노드: 정상") if ok else tr("노드: 응답 없음 (launch_nodes 재시작 필요)"))
        self.node_label.setStyleSheet("color: #2ecc71;" if ok else "color: #e74c3c; font-weight: bold;")
        # Compare the tracked state key, not the (possibly-translated)
        # displayed label text -- state_label.text() no longer reliably
        # equals STATE_LABELS_KO's Korean values once the language toggle
        # has switched it to English.
        self._set_controls_enabled(
            connected=True,
            gate_ok=(self._current_state == "gate"),
            recording=(self._current_state == "recording"),
            reset_wait=(self._current_state == "reset_wait"),
        )

    @pyqtSlot(str)
    def _on_fatal_error(self, msg) -> None:
        self._log(f"[오류] {msg}")
        QMessageBox.critical(self, tr("오류"), msg)

    @pyqtSlot(dict)
    def _on_session_summary(self, s: dict) -> None:
        text = (
            f"파일: {s['path']}\n"
            f"에피소드: {s['num_episodes']}개 (성공 {s['num_success']} / "
            f"실패 {s['num_fail']} / 미표시 {s['num_unlabeled']})\n"
            f"총 프레임: {s['total_frames']} "
            f"(에피소드당 {s['min_frames']}~{s['max_frames']} 프레임)"
        )
        self._log("[세션 요약]\n" + text)
        if s["num_episodes"] > 0:
            translated = tr(
                "파일: {path}\n에피소드: {num_episodes}개 (성공 {num_success} / "
                "실패 {num_fail} / 미표시 {num_unlabeled})\n총 프레임: {total_frames} "
                "(에피소드당 {min_frames}~{max_frames} 프레임)"
            ).format(**s)
            QMessageBox.information(self, tr("세션 요약"), translated)

    def _on_worker_finished(self) -> None:
        self._log("[세션 종료]")
        self.worker = None
        self.node_ok = True
        self._current_state = "idle"
        self.active_file_path = None  # file is no longer exclusively open; browser can read it directly
        self._set_controls_enabled(connected=False, gate_ok=False, recording=False, reset_wait=False)
        self.state_label.setText(tr("대기"))
        self._refresh_dataset_tree()
        # The worker released both cameras on its way out -- resume previews
        # for whatever's currently selected in the combo boxes.
        self._on_camera_selection_changed("agent")
        self._on_camera_selection_changed("wrist")
        self._set_phase(2)  # 수집 -> 정리

    def _on_start_teleop(self) -> None:
        if self.worker:
            self.worker.cmd_start_teleop()

    def _on_skip_reset(self) -> None:
        if self.worker:
            self.worker.cmd_skip_reset_wait()

    def _on_save(self, success: bool) -> None:
        if self.worker:
            self.worker.cmd_save_episode(success)

    def _on_discard(self) -> None:
        if self.worker:
            self.worker.cmd_discard_episode()

    def _on_go_home(self) -> None:
        if self.worker:
            self._log("[홈 이동 요청] 진행 중인 동작을 중단하고 홈으로 이동합니다...")
            self.worker.cmd_go_home()

    def _on_quit(self) -> None:
        if self.worker:
            self._log("[종료 요청] 홈 복귀 후 정리합니다...")
            self.quit_btn.setEnabled(False)
            self.worker.stop()

    # ---------------------------------------------------------- runme.sh
    def _run_runme_sh(self) -> None:
        """Best-effort startup tuning (GELLO USB latency timer, CPU
        governor) -- see scripts/runme.sh. Runs in the background so it
        never blocks the window from opening; a failure or a cancelled
        pkexec prompt is just logged, not fatal (e.g. GELLO may not be
        plugged in yet). Uses pkexec inside the script (not sudo) so this
        works with no controlling terminal -- the desktop icon launches
        with Terminal=false.
        """
        script = Path(RUNME_SCRIPT)
        if not script.exists():
            self._log(f"[RUNME] {script} 없음 -- 건너뜀")
            return
        proc = QProcess(self)
        proc.setProgram("bash")
        proc.setArguments([str(script)])
        proc.setWorkingDirectory(str(script.resolve().parent.parent))
        proc.readyReadStandardOutput.connect(self._on_runme_stdout)
        proc.readyReadStandardError.connect(self._on_runme_stderr)
        proc.finished.connect(self._on_runme_finished)
        self.runme_process = proc
        self._log("[RUNME] scripts/runme.sh 자동 실행 중 (USB latency / CPU governor)...")
        proc.start()

    def _on_runme_stdout(self) -> None:
        if self.runme_process is None:
            return
        data = bytes(self.runme_process.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(f"[RUNME] {line}")

    def _on_runme_stderr(self) -> None:
        if self.runme_process is None:
            return
        data = bytes(self.runme_process.readAllStandardError()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(f"[RUNME][stderr] {line}")

    def _on_runme_finished(self, exit_code: int, exit_status) -> None:  # noqa: ANN001
        if exit_code == 0:
            self._log("[RUNME] 완료")
        else:
            self._log(
                f"[RUNME] 경고 있음 (exit code {exit_code}) -- 위 로그 확인, "
                "필요하면 터미널에서 ./scripts/runme.sh 직접 실행"
            )

    # --------------------------------------------------------- robot node
    def _on_start_node(self) -> None:
        if self.node_process is not None and self.node_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, tr("이미 실행 중"), tr("로봇 노드가 이미 실행 중입니다."))
            return
        ip = self.robot_ip_edit.text().strip() or "172.16.0.2"
        proc = QProcess(self)
        proc.setProgram(PYLIBFRANKA_PYTHON)
        proc.setArguments([LAUNCH_NODES_SCRIPT, "--robot", "fr3", "--robot-ip", ip])
        proc.setWorkingDirectory(str(Path(LAUNCH_NODES_SCRIPT).resolve().parent.parent))
        proc.readyReadStandardOutput.connect(self._on_node_stdout)
        proc.readyReadStandardError.connect(self._on_node_stderr)
        proc.started.connect(self._on_node_started)
        proc.finished.connect(self._on_node_finished)
        proc.errorOccurred.connect(self._on_node_error)
        self.node_process = proc
        self._log(f"[NODE] 시작: {PYLIBFRANKA_PYTHON} {LAUNCH_NODES_SCRIPT} --robot fr3 --robot-ip {ip}")
        self.node_proc_label.setText(tr("시작 중..."))
        self.node_proc_label.setStyleSheet("color: #f39c12;")
        proc.start()

    def _on_restart_node(self) -> None:
        if self.node_process is not None and self.node_process.state() != QProcess.ProcessState.NotRunning:
            self._log("[NODE] 재시작 요청 -- 기존 프로세스 종료 중...")
            self._node_restart_pending = True
            self._on_stop_node()  # _on_node_finished() restarts it once it actually exits
        else:
            self._on_start_node()

    def _on_stop_node(self) -> None:
        if self.node_process is None or self.node_process.state() == QProcess.ProcessState.NotRunning:
            return
        self.node_process.terminate()
        if not self.node_process.waitForFinished(3000):
            self._log("[NODE] 정상 종료 실패 -- 강제 종료")
            self.node_process.kill()
            self.node_process.waitForFinished(2000)

    def _on_node_stdout(self) -> None:
        if self.node_process is None:
            return
        data = bytes(self.node_process.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(f"[NODE] {line}")

    def _on_node_stderr(self) -> None:
        if self.node_process is None:
            return
        data = bytes(self.node_process.readAllStandardError()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(f"[NODE][stderr] {line}")

    def _on_node_started(self) -> None:
        pid = self.node_process.processId() if self.node_process else "?"
        self.node_proc_label.setText(tr("실행 중 (PID {pid})").format(pid=pid))
        self.node_proc_label.setStyleSheet("color: #2ecc71;")

    def _on_node_error(self, error) -> None:  # noqa: ANN001 - QProcess.ProcessError
        self._log(f"[NODE] 프로세스 오류: {error}")

    def _on_node_finished(self, exit_code: int, exit_status) -> None:  # noqa: ANN001
        self._log(f"[NODE] 종료됨 (exit code {exit_code})")
        self.node_proc_label.setText(tr("중지됨"))
        self.node_proc_label.setStyleSheet("color: #888;")
        if self._node_restart_pending:
            self._node_restart_pending = False
            self._on_start_node()

    # ------------------------------------------------------- LeRobot convert
    # --------------------------------------------------- 용량 최적화 (재압축)
    def _selected_hdf5_paths(self) -> list:
        """The .hdf5 files implied by the tree selection (episode rows resolve
        to their parent file). Falls back to every file under the data root."""
        paths = []
        for item in self.dataset_tree.selectedItems():
            node = item if item.parent() is None else item.parent()
            p = node.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(p, str) and p.endswith(".hdf5") and p not in paths:
                paths.append(p)
        if not paths:
            root = Path(self.root_edit.text().strip() or str(Path.home()))
            paths = [str(p) for p in sorted(root.glob("**/*_demo.hdf5"))]
        return paths

    def _on_repack(self) -> None:
        if self.repack_process is not None and self.repack_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, tr("이미 실행 중"), tr("재압축이 이미 진행 중입니다. 로그를 확인하세요."))
            return
        if self.worker is not None:
            QMessageBox.warning(
                self, tr("수집 중"),
                tr("수집 중에는 재압축할 수 없습니다. 먼저 세션을 종료하세요."),
            )
            return
        paths = self._selected_hdf5_paths()
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"), tr("재압축할 .hdf5 파일이 없습니다."))
            return
        dlg = RepackDialog(self, paths)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        paths = dlg.selected()
        if not paths:
            QMessageBox.information(
                self, tr("선택 없음"), tr("재압축할 파일을 하나 이상 선택하세요.")
            )
            return
        total = sum(Path(p).stat().st_size for p in paths if Path(p).exists())
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([REPACK_SCRIPT, *paths])
        proc.setWorkingDirectory(str(Path(REPACK_SCRIPT).resolve().parent.parent))
        proc.readyReadStandardOutput.connect(self._on_repack_stdout)
        proc.readyReadStandardError.connect(self._on_repack_stdout)
        proc.finished.connect(self._on_repack_finished)
        self.repack_process = proc
        self._log(f"[재압축] 시작: {len(paths)}개 파일, {total/1e6:.0f} MB")
        self.repack_btn.setEnabled(False)
        proc.start()

    def _on_repack_stdout(self) -> None:
        if self.repack_process is None:
            return
        data = bytes(self.repack_process.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(f"[재압축] {line}")

    def _on_repack_finished(self, code: int, _status) -> None:
        self._log(f"[재압축] 종료 (exit={code})")
        self.repack_btn.setEnabled(True)
        self.repack_process = None
        self._refresh_dataset_tree()

    def _open_lerobot_convert(self) -> None:
        if self.convert_process is not None and self.convert_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, tr("이미 실행 중"), tr("LeRobot 변환/업로드가 이미 진행 중입니다. 로그를 확인하세요."))
            return
        dlg = LerobotConvertDialog(self, self.root_edit.text())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        args = dlg.build_args()
        if args is None:
            return

        script = str(Path(__file__).resolve().parent.parent / "scripts" / "convert_libero_to_lerobot.py")
        proc = QProcess(self)
        # Reuse this GUI's own interpreter -- unlike launch_nodes.py (which
        # needs the separate pylibfranka venv), this script only needs
        # `lerobot`/`h5py`, already available in whatever venv is running
        # this GUI right now.
        proc.setProgram(sys.executable)
        proc.setArguments([script, *args])
        proc.setWorkingDirectory(str(Path(script).resolve().parent.parent))
        proc.readyReadStandardOutput.connect(self._on_convert_stdout)
        proc.readyReadStandardError.connect(self._on_convert_stderr)
        proc.finished.connect(self._on_convert_finished)
        proc.errorOccurred.connect(self._on_convert_error)
        self.convert_process = proc
        self._log(f"[LeRobot] 시작: {sys.executable} {script} " + " ".join(args))
        self.lerobot_convert_btn.setEnabled(False)
        proc.start()

    def _on_convert_stdout(self) -> None:
        if self.convert_process is None:
            return
        data = bytes(self.convert_process.readAllStandardOutput()).decode(errors="replace")
        for line in clean_stream_lines(data, self._convert_stdout_state):
            self._log(f"[LeRobot] {line}")

    def _on_convert_stderr(self) -> None:
        if self.convert_process is None:
            return
        data = bytes(self.convert_process.readAllStandardError()).decode(errors="replace")
        for line in clean_stream_lines(data, self._convert_stderr_state):
            self._log(f"[LeRobot][stderr] {line}")

    def _on_convert_error(self, error) -> None:  # noqa: ANN001 - QProcess.ProcessError
        self._log(f"[LeRobot] 프로세스 오류: {error}")

    def _on_convert_finished(self, exit_code: int, exit_status) -> None:  # noqa: ANN001
        self._log(f"[LeRobot] 종료됨 (exit code {exit_code})")
        self.lerobot_convert_btn.setEnabled(True)
        if exit_code == 0:
            QMessageBox.information(self, tr("완료"), tr("LeRobot 변환/업로드가 완료되었습니다. 로그를 확인하세요."))
        else:
            QMessageBox.warning(self, tr("변환 실패"), tr("exit code {code} -- 로그를 확인하세요.").format(code=exit_code))

    # -------------------------------------------------------- HDF5 upload
    def _open_hdf5_upload(self) -> None:
        if (
            self.hdf5_upload_process is not None
            and self.hdf5_upload_process.state() != QProcess.ProcessState.NotRunning
        ):
            QMessageBox.information(self, tr("이미 실행 중"), tr("업로드가 이미 진행 중입니다. 로그를 확인하세요."))
            return

        # Prefill from whatever's selected in the dataset tree (file row or
        # an episode row -- either way, its parent file) so the common case
        # (upload the file I was just looking at) needs no typing.
        default_file = ""
        items = self.dataset_tree.selectedItems()
        if items:
            item = items[0]
            parent = item.parent()
            raw = parent.data(0, Qt.ItemDataRole.UserRole) if parent is not None else item.data(0, Qt.ItemDataRole.UserRole)
            default_file = str(raw)

        dlg = HdfUploadDialog(self, default_file)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        args = dlg.build_args()
        if args is None:
            return

        script = str(Path(__file__).resolve().parent.parent / "scripts" / "upload_to_hub.py")
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([script, *args])
        proc.setWorkingDirectory(str(Path(script).resolve().parent.parent))
        proc.readyReadStandardOutput.connect(self._on_hdf5_upload_stdout)
        proc.readyReadStandardError.connect(self._on_hdf5_upload_stderr)
        proc.finished.connect(self._on_hdf5_upload_finished)
        proc.errorOccurred.connect(self._on_hdf5_upload_error)
        self.hdf5_upload_process = proc
        self._log(f"[HDF5 업로드] 시작: {sys.executable} {script} " + " ".join(args))
        self.hdf5_upload_btn.setEnabled(False)
        proc.start()

    def _on_hdf5_upload_stdout(self) -> None:
        if self.hdf5_upload_process is None:
            return
        data = bytes(self.hdf5_upload_process.readAllStandardOutput()).decode(errors="replace")
        for line in clean_stream_lines(data, self._hdf5_stdout_state):
            self._log(f"[HDF5 업로드] {line}")

    def _on_hdf5_upload_stderr(self) -> None:
        if self.hdf5_upload_process is None:
            return
        data = bytes(self.hdf5_upload_process.readAllStandardError()).decode(errors="replace")
        for line in clean_stream_lines(data, self._hdf5_stderr_state):
            self._log(f"[HDF5 업로드][stderr] {line}")

    def _on_hdf5_upload_error(self, error) -> None:  # noqa: ANN001 - QProcess.ProcessError
        self._log(f"[HDF5 업로드] 프로세스 오류: {error}")

    def _on_hdf5_upload_finished(self, exit_code: int, exit_status) -> None:  # noqa: ANN001
        self._log(f"[HDF5 업로드] 종료됨 (exit code {exit_code})")
        self.hdf5_upload_btn.setEnabled(True)
        if exit_code == 0:
            QMessageBox.information(self, tr("업로드 완료"), tr("HDF5 업로드가 완료되었습니다. 로그를 확인하세요."))
        else:
            QMessageBox.warning(self, tr("업로드 실패"), tr("exit code {code} -- 로그를 확인하세요.").format(code=exit_code))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._stop_previews()
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        self._on_stop_node()
        if self.convert_process is not None and self.convert_process.state() != QProcess.ProcessState.NotRunning:
            self.convert_process.terminate()
            if not self.convert_process.waitForFinished(3000):
                self.convert_process.kill()
                self.convert_process.waitForFinished(2000)
        if (
            self.hdf5_upload_process is not None
            and self.hdf5_upload_process.state() != QProcess.ProcessState.NotRunning
        ):
            self.hdf5_upload_process.terminate()
            if not self.hdf5_upload_process.waitForFinished(3000):
                self.hdf5_upload_process.kill()
                self.hdf5_upload_process.waitForFinished(2000)
        if self.runme_process is not None and self.runme_process.state() != QProcess.ProcessState.NotRunning:
            self.runme_process.terminate()
            if not self.runme_process.waitForFinished(3000):
                self.runme_process.kill()
                self.runme_process.waitForFinished(2000)
        # Repack replaces a file only after verifying it, so a kill mid-run
        # leaves the original intact -- but give it a chance to exit cleanly
        # and remove its temp file first.
        if self.repack_process is not None and self.repack_process.state() != QProcess.ProcessState.NotRunning:
            self.repack_process.terminate()
            if not self.repack_process.waitForFinished(5000):
                self.repack_process.kill()
                self.repack_process.waitForFinished(2000)
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:  # noqa: BLE001
                pass
        event.accept()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        """One-handed shortcuts for solo data collection (hands stay on the
        GELLO leader, not the mouse). Same key means different things
        depending on state, mirroring the corresponding button:

            gate       + Space          -> 텔레옵 시작 (start teleop)
            recording  + Space          -> 저장 (성공)
            recording  + Esc            -> 저장 (실패)
            recording  + Delete/Backspace -> 폐기 (discard)
            reset_wait + Enter/Return   -> 리셋 대기 건너뛰기 (skip reset wait)

        Installed app-wide (not just this window) so it fires regardless
        of which widget currently has focus. Skipped while a modal dialog
        (QMessageBox etc.) is open so Esc still closes those normally.
        """
        if (
            event.type() == QEvent.Type.KeyPress
            and self.worker is not None
            and QApplication.activeModalWidget() is None
        ):
            key = event.key()
            if key == Qt.Key.Key_Space:
                if self._current_state == "gate":
                    self._on_start_teleop()
                    return True
                if self._current_state == "recording":
                    self._on_save(True)
                    return True
            elif key == Qt.Key.Key_Escape:
                if self._current_state == "recording":
                    self._on_save(False)
                    return True
            elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self._current_state == "recording":
                    self._on_discard()
                    return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._current_state == "reset_wait":
                    self._on_skip_reset()
                    return True
        return super().eventFilter(obj, event)


def main() -> None:
    app = QApplication(sys.argv)
    window = LiberoCollectorWindow()

    # availableGeometry는 GNOME 상단바/독을 이미 제외한 값. 1080p(+독 ~72px)에서
    # 약 1848x1048 → 아래 계산으로 ~1790x1000, 이전 상한(1500x950)보다 화면을
    # 넉넉히 사용. 더 작은 화면에서는 비율로 자연 축소.
    screen = app.primaryScreen().availableGeometry()
    width = min(1820, int(screen.width() * 0.97))
    height = min(1030, int(screen.height() * 0.96))
    window.resize(width, height)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
