"""Workspace-style GUI for collecting LIBERO-format demos via GELLO teleop.

Run inside lerobot-venv::

    (pylibfranka-venv) python experiments/launch_nodes.py --robot fr3   # terminal 1
    (lerobot-venv)     python experiments/collect_workspace.py          # terminal 2

Replaces the 3-phase wizard (준비 -> 수집 -> 정리). The wizard assumed the
phases are visited in order and left once, but in practice an operator moves
between them constantly -- tweak a camera, record two episodes, delete a bad
one, adjust the task string, record again -- and every move swapped the whole
screen, including the camera. This is a single workspace instead: an activity
bar picks what the LEFT panel shows, and nothing else moves.

The invariant that drives the layout: **the camera view is the center of the
window and never goes away.** Switching activities, opening dialogs, starting
or stopping a recording -- none of them touch the center. It is the one thing
the operator's hands depend on while they are on the GELLO leader.

Panel map (all splitters, all user-resizable):

    menu bar
    toolbar          connect / record / stop / save / discard / upload
    ┌──────┬──────────┬───────────────────────┬──────────────┐
    │ act. │ left     │ CENTER  Live/Playback │ right        │
    │ bar  │ (stacked)│ (camera, never swaps) │ (status)     │
    ├──────┴──────────┴───────────────────────┴──────────────┤
    │ bottom tabs: Log / Upload / Validation                 │
    ├────────────────────────────────────────────────────────┤
    │ status bar: robot / camera / recording / fps / episode │
    └────────────────────────────────────────────────────────┘

Widgets and dialogs come from gello/gui_widgets.py, which was split out of the
old wizard so both could share them without one importing the other's window.
"""

from __future__ import annotations

import os

# Must run before numpy/cv2/h5py are imported -- see gello/gui_widgets.py for
# why this GUI caps the BLAS/OpenCV thread pools at 1.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import json
import shutil
import sys
import traceback
import time
from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import QEvent, QProcess, Qt, QThread, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QActionGroup, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.dataset_schema import load_schema_config, save_schema_config  # noqa: E402
from gello.dataset_sync import plan_sync  # noqa: E402
from gello.episode_stats import (  # noqa: E402
    hdf5_files,
    load_series,
    scan_dataset,
    summarize,
)
from gello.plot_widgets import BarStrip, Histogram, SeriesPlot  # noqa: E402
from gello.gui_widgets import (  # noqa: E402
    PLAYBACK_FPS,
    REPACK_SCRIPT,
    CameraPreviewWorker,
    DatasetSchemaDialog,
    DeltaBar,
    EpisodeLoadWorker,
    HdfUploadDialog,
    HfAccountDialog,
    LerobotConvertDialog,
    Recents,
    RepackDialog,
    VideoView,
    clean_stream_lines,
    hf_account,
    is_progress_line,
)
from gello.i18n import get_language, set_language, tr  # noqa: E402
from gello.libero_format import (  # noqa: E402
    describe_episode,
    hdf5_repack_status,
    renumber_episodes,
)
from gello.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402
from gello.robots.franka_fr3 import FR3_RESET_POSES  # noqa: E402

LOG_DIR = Path.home() / "libero_gui_logs"
PYLIBFRANKA_PYTHON = str(Path.home() / "pylibfranka-venv" / "bin" / "python")
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parent / "launch_nodes.py")
CONVERT_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "convert_libero_to_lerobot.py")
UPLOAD_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "upload_to_hub.py")
RUNME_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "runme.sh")
CHECK_CAMERAS = str(Path(__file__).resolve().parent.parent / "scripts" / "check_cameras.py")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""

# Activity bar entries: (key, icon, title, tooltip). Icons are emoji rather
# than a theme lookup -- an icon theme that is missing on this machine would
# leave the strip blank, and the strip is the only navigation there is.
ACTIVITIES = (
    ("configure", "⚙", "Configure", "로봇·카메라·태스크 설정"),
    ("collect", "🎮", "Collect", "수집 제어와 현재 상태"),
    ("dataset", "📂", "Dataset", "에피소드 목록·재생·삭제"),
    ("upload", "☁", "Upload", "재압축·LeRobot 변환·업로드"),
    ("stats", "📊", "Statistics", "세션 통계"),
    ("settings", "🛠", "Settings", "언어·스키마"),
)

_DOT = {"ok": "#2ecc71", "busy": "#f39c12", "off": "#7f8c8d", "bad": "#e74c3c"}

# Panels named in the UI spec that this build does not implement yet. They are
# shown, disabled and greyed, rather than omitted: a missing tab reads as "this
# tool cannot do that", while a greyed one says "not built yet" -- and leaving
# the shape visible is what makes the gap reviewable instead of forgotten.
TODO_STYLE = "color:#6b6b6b; font-style:italic;"
TODO_MARK = "미개발"

# 오른쪽 패널에서 값이 길어 좌우 배치로는 읽기 어려운 항목들.
WIDE_FIELDS = {"ds_file", "ds_task"}

# 0.5배는 접촉 순간을 한 프레임씩 보려고, 2~3배는 긴 에피소드를 훑으려고 쓴다.
# 3배면 60Hz라 프레임을 건너뛰지 않고도 타이머만으로 낼 수 있다.
PLAYBACK_SPEEDS = (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0))

# task 안에서 2σ. 전역 절대값이 아닌 이유는 gello/episode_stats.py 참고 --
# 전역 순위는 품질이 아니라 task의 속도를 잰다.
JERKY_Z = 2.0


def soft_wrap(text: str) -> str:
    """Lets a long filename wrap.

    QLabel only breaks at whitespace, and ``pick_up_the_blue_cup_..._demo.hdf5``
    has none -- so word wrap did nothing and the name sat on one clipped line.
    A zero-width space after each separator gives it legal break points without
    changing the visible characters or what a copy-paste yields... except that
    the copy would carry U+200B, so this is only ever applied to display text
    whose real value is also in the tooltip.
    """
    for sep in ("_", "-", "."):
        text = text.replace(sep, sep + "​")
    return text

# The worker's state names, and what the operator can do from each. Both the
# 진행 label and the shortcut hint read from these, so the hint can never drift
# out of sync with what eventFilter() actually accepts.
STATE_LABELS = {
    "connecting": "연결 중...",
    "idle": "대기",
    "homing": "홈 복귀 중",
    "reset_wait": "리셋 대기 — 물체를 다시 놓으세요",
    "gate": "자세 정렬 — 리더를 팔로워에 맞추세요",
    "approach": "접근 중",
    "recording": "기록 중",
}
SHORTCUT_HINTS = {
    "reset_wait": "Enter: 대기 건너뛰기   Esc: 직전 에피소드 판정 뒤집기",
    "gate": "Space: 텔레옵 시작   Enter: 자동 정렬 다시",
    "recording": "Space: 성공으로 끝내기   Esc: 실패로 끝내기   Del: 폐기",
}


def mark_todo(widget: QWidget, note: str = "") -> QWidget:
    widget.setEnabled(False)
    widget.setStyleSheet(TODO_STYLE)
    widget.setToolTip(f"{TODO_MARK}: " + (note or tr("아직 구현되지 않은 기능입니다.")))
    return widget


def _dot(state: str, text: str) -> str:
    return f'<span style="color:{_DOT[state]};">●</span> {text}'


class PipelineDialog(QDialog):
    """Decide once, then walk away: compares Hub against the curated files and
    proposes the run that makes them match.

    The decision it exists to surface is which of two very different runs is
    correct. Appending is cheap but only valid while no already-pushed task has
    lost episodes; once one has, the Hub copy holds takes the operator deleted
    and nothing short of a rebuild removes them (LeRobot has no episode delete).
    Choosing wrong is invisible afterwards -- the dataset simply contains bad
    demonstrations -- so the comparison is done for the operator and the
    recommendation is pre-selected, but the button is theirs to press.
    """

    def __init__(self, parent: QWidget, data_root: str, plan: dict,
                 lerobot_repo: str, hdf5_repo: str, lerobot_root: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("전체 처리 (재압축 → 변환 → 업로드)"))
        self.setMinimumWidth(820)
        self.plan = plan
        self.data_root = data_root
        layout = QVBoxLayout(self)
        self._recents = Recents()

        head = QLabel()
        head.setWordWrap(True)
        action = plan["action"]
        if action == "blocked":
            head.setText(tr("Hub 상태를 읽지 못했습니다: {e}\n\n확실하지 않은 채로 올리지 "
                            "않습니다. 네트워크나 계정을 확인한 뒤 다시 여세요.").format(
                                e=plan["error"]))
            head.setStyleSheet("color:#e74c3c; font-weight:bold;")
        elif action == "up_to_date":
            head.setText(tr("Hub이 이미 로컬과 같습니다 ({n}개). 변환/업로드할 것이 "
                            "없습니다.").format(n=plan["local_total"]))
            head.setStyleSheet("color:#27ae60; font-weight:bold;")
        elif action == "rebuild":
            head.setText(tr(
                "이미 올라간 task에서 에피소드 {n}개가 삭제되었습니다. LeRobot은 게시된 "
                "에피소드를 지울 수 없으므로, 전체를 다시 만들어 Hub을 교체해야 합니다 "
                "(오래 걸립니다).").format(n=plan["shrunk"]))
            head.setStyleSheet("color:#e67e22; font-weight:bold;")
        else:
            head.setText(tr("새 에피소드 {n}개를 이어붙이면 됩니다 (Hub {h} → {l}). "
                            "이미 올라간 task에서 삭제된 것은 없습니다.").format(
                                n=plan["added"], h=plan["hub_total"], l=plan["local_total"]))
            head.setStyleSheet("color:#27ae60; font-weight:bold;")
        layout.addWidget(head)

        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels([tr("task"), tr("Hub"), tr("로컬"), tr("비고")])
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 420)
        tree.setMinimumHeight(200)
        for r in plan["rows"]:
            item = QTreeWidgetItem([r["task"], str(r["hub"]), str(r["local"]), r["note"]])
            if r["delta"] < 0:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.red)
            elif r["delta"] > 0:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.darkGreen)
            elif "편집" in r["note"]:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.darkYellow)
            tree.addTopLevelItem(item)
        layout.addWidget(tree)

        if plan["ambiguous"]:
            warn = QLabel(tr(
                "개수는 같지만 재압축 이후 편집된 흔적이 있는 task가 있습니다: {t}\n"
                "지우고 다시 찍었다면 이어붙이기로는 옛 에피소드가 Hub에 남습니다. "
                "확실하지 않으면 '전체 재빌드'를 고르세요.").format(
                    t=", ".join(x[:40] for x in plan["ambiguous"])))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#e67e22;")
            layout.addWidget(warn)

        mode = QGroupBox(tr("LeRobot 처리 방식"))
        mcol = QVBoxLayout(mode)
        self.mode_resume = QRadioButton(tr("이어붙이기 — 새 에피소드만 변환/업로드 (빠름)"))
        self.mode_rebuild = QRadioButton(tr("전체 재빌드 — 처음부터 만들어 Hub 교체 (삭제 반영, 느림)"))
        self.mode_resume.setChecked(action == "resume")
        self.mode_rebuild.setChecked(action == "rebuild")
        self.mode_resume.setEnabled(action in ("resume", "up_to_date"))
        mcol.addWidget(self.mode_resume)
        mcol.addWidget(self.mode_rebuild)
        layout.addWidget(mode)

        opts = QGroupBox(tr("함께 할 일"))
        ocol = QVBoxLayout(opts)
        n_repack = sum(1 for p in plan["paths"] if not hdf5_repack_status(p)["repacked"])
        self.repack_check = QCheckBox(
            tr("재압축 — 필요한 파일 {n}개").format(n=n_repack))
        self.repack_check.setChecked(n_repack > 0)
        self.repack_check.setEnabled(n_repack > 0)
        ocol.addWidget(self.repack_check)
        self.hdf5_check = QCheckBox(tr("원본 HDF5도 Hub에 업로드 (9GB 기준 약 15분)"))
        ocol.addWidget(self.hdf5_check)
        self.only_success_check = QCheckBox(tr("성공한 에피소드만 변환 (--only-success)"))
        ocol.addWidget(self.only_success_check)
        layout.addWidget(opts)

        grid = QGridLayout()
        grid.addWidget(QLabel(tr("LeRobot Repo ID:")), 0, 0)
        self.lerobot_repo_edit = QLineEdit(lerobot_repo)
        grid.addWidget(self.lerobot_repo_edit, 0, 1)
        grid.addWidget(QLabel(tr("HDF5 Repo ID:")), 1, 0)
        self.hdf5_repo_edit = QLineEdit(hdf5_repo)
        grid.addWidget(self.hdf5_repo_edit, 1, 1)
        grid.addWidget(QLabel(tr("로컬 변환 폴더:")), 2, 0)
        self.root_edit = QLineEdit(lerobot_root)
        grid.addWidget(self.root_edit, 2, 1)
        layout.addLayout(grid)

        note = QLabel(tr(
            "시작할 때 로컬 변환 폴더를 비웁니다. 그래야 이어붙이기가 Hub의 현재 상태를 "
            "기준으로 삼습니다 — 그 폴더의 내용은 HDF5에서 언제든 다시 만들 수 있습니다."))
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        layout.addWidget(note)

        acct_row = QHBoxLayout()
        acct_text, acct_color = hf_account()
        self.acct_label = QLabel(acct_text)
        self.acct_label.setStyleSheet(f"color:{acct_color}; font-weight:bold;")
        self.acct_label.setWordWrap(True)
        acct_row.addWidget(self.acct_label, 1)
        acct_btn = QPushButton(tr("계정 전환..."))
        acct_btn.clicked.connect(self._on_account)
        acct_row.addWidget(acct_btn)
        layout.addLayout(acct_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText(tr("시작하고 퇴근"))
        self._ok.setEnabled(action != "blocked")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_account(self) -> None:
        HfAccountDialog(self).exec()
        text, color = hf_account()
        self.acct_label.setText(text)
        self.acct_label.setStyleSheet(f"color:{color}; font-weight:bold;")

    def steps(self) -> list:
        """The ordered subprocess steps this run will execute."""
        rebuild = self.mode_rebuild.isChecked()
        lerobot_repo = self.lerobot_repo_edit.text().strip()
        hdf5_repo = self.hdf5_repo_edit.text().strip()
        root = self.root_edit.text().strip()
        paths = self.plan["paths"]
        self._recents.add("repo_id", lerobot_repo)
        self._recents.add("hdf5_repo_id", hdf5_repo)
        self._recents.add("lerobot_root", root)

        steps = []
        if self.repack_check.isChecked():
            todo = [p for p in paths if not hdf5_repack_status(p)["repacked"]]
            if todo:
                steps.append({"name": tr("재압축"), "program": sys.executable,
                              "args": [REPACK_SCRIPT, *todo]})
        convert = [CONVERT_SCRIPT, *paths, "--repo-id", lerobot_repo, "--root", root]
        if self.only_success_check.isChecked():
            convert.append("--only-success")
        if not rebuild:
            convert.append("--resume")
        steps.append({"name": tr("LeRobot 변환") + ("" if not rebuild else tr(" (전체 재빌드)")),
                      "program": sys.executable, "args": convert, "clear_root": root})
        push = [CONVERT_SCRIPT, "--repo-id", lerobot_repo, "--root", root,
                "--push-only", "--no-private"]
        if rebuild:
            push.append("--replace")
        steps.append({"name": tr("LeRobot 업로드"), "program": sys.executable, "args": push})
        if self.hdf5_check.isChecked():
            steps.append({"name": tr("HDF5 원본 업로드"), "program": sys.executable,
                          "args": [UPLOAD_SCRIPT, *paths, "--repo-id", hdf5_repo,
                                   "--no-private"]})
        return steps


class StatusLight(QLabel):
    """One status-bar indicator: a colored dot plus a short label."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label
        self.set("off", "-")

    def set(self, state: str, text: str) -> None:
        self.setText(_dot(state, f"{self._label} {text}"))


class WorkspaceWindow(QMainWindow):
    def __init__(self, log_path: Path | None) -> None:
        super().__init__()
        self.setWindowTitle(tr("FR3 GELLO 데이터 수집 워크스페이스"))
        self.resize(1780, 1020)

        self.worker: CollectionWorker | None = None
        self.node_process: QProcess | None = None
        self.repack_process: QProcess | None = None
        self.convert_process: QProcess | None = None
        self.upload_process: QProcess | None = None
        self.runme_process: QProcess | None = None
        self._pipeline_steps: list = []
        self._pipeline_results: list = []
        self._pipeline_proc: QProcess | None = None
        self._pipeline_t0 = 0.0
        self._pipeline_step_t0 = 0.0
        self._stats: list = []
        self._summary: dict = {}
        self._stream_states: dict = {}
        self._progress_line: dict = {}

        self.active_file_path: Path | None = None
        self.active_episode_cache: list | None = None
        self.agent_preview: CameraPreviewWorker | None = None
        self.wrist_preview: CameraPreviewWorker | None = None
        self._play_loader: EpisodeLoadWorker | None = None
        self._play_frames: dict = {"agent": None, "wrist": None}
        self._play_key = None

        self._session = {"saved": 0, "success": 0, "failed": 0, "discarded": 0,
                         "frames": 0, "t0": time.monotonic()}
        self._fps_count = 0
        self._fps_value = 0.0
        self._pending_success: bool | None = None
        self._no_dataset_session = False
        self._current_state = "idle"
        self._gate_ok = False
        self._last_saved_name = None
        self._last_saved_success = True
        self._pending_verdict_toggle = False
        self._dying_previews: list = []
        self._connect_wait_since = None
        self._episodes_at_connect = 0
        self._recents = Recents()
        self._log_file = None
        if log_path is not None:
            self._log_file = open(log_path, "a", buffering=1)  # noqa: SIM115

        self.schema = load_schema_config()

        self._build_bottom()          # log view exists before anything logs
        self._build_center()
        self._build_left()
        self._build_right()
        self._build_layout()
        self._build_toolbar()
        self._build_menu()
        self._build_statusbar()

        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._tick_fps)
        self._fps_timer.start(1000)

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(int(1000 / PLAYBACK_FPS))
        self._play_timer.timeout.connect(self._on_play_tick)

        # App-wide, not window-scoped: the operator's hands are on the leader,
        # so whichever widget happens to hold focus must not swallow the keys.
        QApplication.instance().installEventFilter(self)

        self._set_activity("configure")
        self._set_running(False)
        self._refresh_cameras()
        self._refresh_dataset_tree()
        if log_path is not None:
            self.log(f"[로그] 이 세션 로그: {log_path}")
        self.log("[준비] 로봇 노드를 먼저 띄운 뒤 Connect 를 누르세요.")
        QTimer.singleShot(0, self._startup_tuning)

    # ------------------------------------------------------------- center
    def _build_center(self) -> None:
        """Camera views. This widget is created once and never replaced --
        every other panel changes around it."""
        self.center_tabs = QTabWidget()
        self.center_tabs.setDocumentMode(True)

        live = QWidget()
        live_col = QVBoxLayout(live)
        live_col.setContentsMargins(4, 4, 4, 4)
        self.live_split = QSplitter(Qt.Orientation.Horizontal)
        self.live_views = {}
        for key, title in (("agent", "Agent (정면)"), ("wrist", "Wrist (손목)")):
            box = QGroupBox(tr(title))
            inner = QVBoxLayout(box)
            inner.setContentsMargins(4, 4, 4, 4)
            view = VideoView()
            view.setText(tr("카메라를 선택하세요"))
            inner.addWidget(view)
            self.live_views[key] = view
            self.live_split.addWidget(box)
        self.live_split.setSizes([600, 600])
        live_col.addWidget(self.live_split, 1)
        self.center_tabs.addTab(live, tr("Live"))

        play = QWidget()
        play_col = QVBoxLayout(play)
        play_col.setContentsMargins(4, 4, 4, 4)
        self.play_split = QSplitter(Qt.Orientation.Horizontal)
        self.play_views = {}
        for key, title in (("agent", "Agent (정면)"), ("wrist", "Wrist (손목)")):
            box = QGroupBox(tr(title))
            inner = QVBoxLayout(box)
            inner.setContentsMargins(4, 4, 4, 4)
            view = VideoView()
            view.setText(tr("에피소드를 선택하세요"))
            inner.addWidget(view)
            self.play_views[key] = view
            self.play_split.addWidget(box)
        self.play_split.setSizes([600, 600])
        play_col.addWidget(self.play_split, 1)

        row = QHBoxLayout()
        self.play_btn = QPushButton(tr("재생"))
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._on_play_toggle)
        row.addWidget(self.play_btn)
        self.play_slider = QSlider(Qt.Orientation.Horizontal)
        self.play_slider.setEnabled(False)
        self.play_slider.valueChanged.connect(self._show_frame)
        row.addWidget(self.play_slider, 1)
        self.play_pos = QLabel("-/-")
        self.play_pos.setMinimumWidth(80)
        self.play_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.play_pos)
        # 배속. 3배까지는 타이머 주기만 줄이면 되고(20 -> 60Hz) 프레임을 건너뛸
        # 필요가 없어서, 빠르게 훑을 때도 놓치는 프레임이 없다.
        row.addWidget(QLabel(tr("배속")))
        self.speed_combo = QComboBox()
        for label, mult in PLAYBACK_SPEEDS:
            self.speed_combo.addItem(label, mult)
        self.speed_combo.setCurrentIndex(
            [m for _l, m in PLAYBACK_SPEEDS].index(1.0))
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self.speed_combo.setMaximumWidth(80)
        row.addWidget(self.speed_combo)
        play_col.addLayout(row)
        self.play_caption = QLabel(tr("Dataset 패널에서 에피소드를 고르면 여기서 재생됩니다."))
        self.play_caption.setStyleSheet("color:#888;")
        play_col.addWidget(self.play_caption)
        self.center_tabs.addTab(play, tr("Playback"))
        self.center_tabs.addTab(self._build_analysis_tab(), tr("Analysis"))
        for title, why in ((tr("Depth"), tr("깊이 스트림을 아직 수집하지 않습니다.")),
                           (tr("Point Cloud"), tr("포인트클라우드 렌더러가 없습니다."))):
            ph = QLabel(tr("{t} — {m}\n\n{w}").format(t=title, m=TODO_MARK, w=why))
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet(TODO_STYLE)
            idx = self.center_tabs.addTab(ph, f"{title} ({TODO_MARK})")
            self.center_tabs.setTabEnabled(idx, False)

    # --------------------------------------------------------------- left
    def _build_left(self) -> None:
        self.left_stack = QStackedWidget()
        self.left_pages = {}
        for key, _icon, title, _tip in ACTIVITIES:
            page = getattr(self, f"_page_{key}")()
            wrapper = QWidget()
            col = QVBoxLayout(wrapper)
            col.setContentsMargins(6, 6, 6, 6)
            head = QLabel(title.upper())
            f = head.font()
            f.setPointSize(max(8, f.pointSize() - 1))
            f.setBold(True)
            head.setFont(f)
            head.setStyleSheet("color:#888; letter-spacing:1px;")
            col.addWidget(head)
            col.addWidget(page, 1)
            self.left_pages[key] = self.left_stack.count()
            self.left_stack.addWidget(wrapper)

    def _page_configure(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)

        node = QGroupBox(tr("로봇 노드"))
        nrow = QVBoxLayout(node)
        self.node_start_btn = QPushButton(tr("노드 시작"))
        self.node_start_btn.clicked.connect(self._on_start_node)
        self.node_stop_btn = QPushButton(tr("노드 종료"))
        self.node_stop_btn.clicked.connect(self._on_stop_node)
        nrow.addWidget(self.node_start_btn)
        nrow.addWidget(self.node_stop_btn)
        col.addWidget(node)

        mode = QGroupBox(tr("모드"))
        mcol = QVBoxLayout(mode)
        self.no_dataset_check = QCheckBox(tr("데이터셋 없이 조작만 (연습 / 씬 세팅)"))
        self.no_dataset_check.setToolTip(tr(
            "파일을 전혀 만들지 않고 텔레옵만 합니다. 자세 게이트·카메라·프레임 "
            "카운터는 그대로 동작하고, 저장을 눌러도 버려집니다."))
        self.no_dataset_check.toggled.connect(self._on_no_dataset_toggled)
        mcol.addWidget(self.no_dataset_check)
        self.mode_hint = QLabel("")
        self.mode_hint.setStyleSheet("color:#888;")
        self.mode_hint.setWordWrap(True)
        mcol.addWidget(self.mode_hint)
        col.addWidget(mode)

        task = QGroupBox(tr("태스크"))
        self.task_box = task
        form = QFormLayout(task)
        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText(tr("예) pick_up_the_blue_cup_and_place_it_on_the_blue_bowl"))
        self.task_edit.setText(self._recents.most_recent("task", ""))
        form.addRow(tr("Task 이름"), self.task_edit)
        self.lang_edit = QLineEdit()
        self.lang_edit.setPlaceholderText(tr("예) pick up the blue cup and place it on the blue bowl"))
        self.lang_edit.setText(self._recents.most_recent("language", ""))
        form.addRow(tr("Language"), self.lang_edit)
        root_row = QWidget()
        rl = QHBoxLayout(root_row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.root_edit = QLineEdit(self._recents.most_recent(
            "data_root", str(Path.home() / "libero_datasets")))
        rl.addWidget(self.root_edit, 1)
        browse = QPushButton(tr("..."))
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse_root)
        rl.addWidget(browse)
        form.addRow(tr("저장 경로"), root_row)
        col.addWidget(task)

        cam = QGroupBox(tr("카메라"))
        cform = QFormLayout(cam)
        self.agent_combo = QComboBox()
        self.wrist_combo = QComboBox()
        for c in (self.agent_combo, self.wrist_combo):
            c.setEditable(True)
            c.currentTextChanged.connect(self._on_camera_changed)
        cform.addRow(tr("Agent"), self.agent_combo)
        cform.addRow(tr("Wrist"), self.wrist_combo)
        refresh = QPushButton(tr("카메라 새로고침"))
        refresh.clicked.connect(self._refresh_cameras)
        cform.addRow(refresh)
        self.camera_hint = QLabel("")
        self.camera_hint.setStyleSheet("color:#888;")
        self.camera_hint.setWordWrap(True)
        cform.addRow(self.camera_hint)
        col.addWidget(cam)

        sess = QGroupBox(tr("세션"))
        sform = QFormLayout(sess)
        self.reset_pose_combo = QComboBox()
        self.reset_pose_combo.addItems(sorted(FR3_RESET_POSES))
        if "libero" in FR3_RESET_POSES:
            self.reset_pose_combo.setCurrentText("libero")
        sform.addRow(tr("Reset pose"), self.reset_pose_combo)
        self.grip_combo = QComboBox()
        self.grip_combo.addItems(["right", "left"])
        sform.addRow(tr("Grip"), self.grip_combo)
        self.eplen_edit = QLineEdit("20")
        sform.addRow(tr("에피소드 길이(s)"), self.eplen_edit)
        self.resetwait_edit = QLineEdit("10")
        sform.addRow(tr("리셋 대기(s)"), self.resetwait_edit)
        self.resume_check = QCheckBox(tr("기존 파일에 이어서 수집"))
        self.resume_check.setChecked(True)
        sform.addRow(self.resume_check)
        self.wall_check = QCheckBox(tr("관절 한계 벽 사용"))
        self.wall_check.setChecked(True)
        sform.addRow(self.wall_check)
        self.match_check = QCheckBox(tr("에피소드마다 리더를 리셋 포즈로 정렬"))
        self.match_check.setChecked(True)
        sform.addRow(self.match_check)
        col.addWidget(sess)
        col.addStretch()
        return w

    def _on_no_dataset_toggled(self, on: bool) -> None:
        """No file is written, so the task/path fields have nothing to name."""
        self.task_box.setEnabled(not on)
        self.mode_hint.setText(tr(
            "연습 모드: 파일을 만들지 않습니다. 저장을 눌러도 버려집니다."
        ) if on else "")
        self.mode_hint.setStyleSheet("color:#e67e22;" if on else "color:#888;")
        for key in ("save", "savefail"):
            if key in getattr(self, "tb_actions", {}):
                self.tb_actions[key].setEnabled(not on and self.worker is not None)
        for b in (getattr(self, "save_ok_btn", None), getattr(self, "save_ng_btn", None)):
            if b is not None:
                b.setEnabled(not on and self.worker is not None)

    def _page_collect(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)

        gate = QGroupBox(tr("리더 자세 게이트"))
        gcol = QVBoxLayout(gate)
        self.delta_bars = []
        for i in range(8):
            bar = DeltaBar(f"J{i + 1}" if i < 7 else tr("그리퍼"))
            gcol.addWidget(bar)
            self.delta_bars.append(bar)
        self.gate_label = QLabel(tr("연결 대기 중"))
        self.gate_label.setStyleSheet("color:#888;")
        gcol.addWidget(self.gate_label)
        col.addWidget(gate)

        ctl = QGroupBox(tr("제어"))
        ccol = QVBoxLayout(ctl)
        self.start_btn = QPushButton(tr("Start Teleop (기록 시작)"))
        self.start_btn.clicked.connect(lambda: self._cmd("cmd_start_teleop"))
        # 자동 정렬은 한 번 시간 초과되면 끝이었고, 다시 걸 방법이 없어 남은 길은
        # 손으로 맞추는 것뿐이었다. 워커는 재요청을 받을 수 있으므로 버튼과 Enter
        # 둘 다 연결한다. all_ok 전에는 잠근다 -- 리더 모터로 끌어당기는 동작이라
        # 크게 어긋난 상태에서 걸면 모터에 무리가 간다(워커도 같은 조건을 재검사).
        self.match_btn = QPushButton(tr("자동 정렬 다시 (Enter)"))
        self.match_btn.setEnabled(False)
        self.match_btn.clicked.connect(lambda: self._cmd("cmd_auto_match_pose"))
        self.skip_btn = QPushButton(tr("리셋 대기 건너뛰기"))
        self.skip_btn.clicked.connect(lambda: self._cmd("cmd_skip_reset_wait"))
        self.save_ok_btn = QPushButton(tr("저장 (성공)"))
        self.save_ok_btn.setStyleSheet("background-color:#2ecc71; color:white; font-weight:bold;")
        self.save_ok_btn.clicked.connect(lambda: self._save(True))
        # 두 버튼 모두 에피소드를 끝낸다. 판정을 되돌리는 건 리셋 구간의
        # Esc(_toggle_last_verdict)이고, 여기서는 끝내는 순간의 첫 판단만 한다.
        self.save_ng_btn = QPushButton(tr("실패로 끝내기 (Esc)"))
        self.save_ng_btn.clicked.connect(lambda: self._save(False))
        self.discard_btn = QPushButton(tr("버리기"))
        self.discard_btn.setStyleSheet("background-color:#e74c3c; color:white;")
        self.discard_btn.clicked.connect(lambda: self._cmd("cmd_discard_episode"))
        self.home_btn = QPushButton(tr("홈으로"))
        self.home_btn.clicked.connect(lambda: self._cmd("cmd_go_home"))
        for b in (self.start_btn, self.match_btn, self.skip_btn, self.save_ok_btn,
                  self.save_ng_btn, self.discard_btn, self.home_btn):
            ccol.addWidget(b)
        col.addWidget(ctl)

        prog = QGroupBox(tr("진행"))
        pcol = QVBoxLayout(prog)
        self.ep_progress = QProgressBar()
        self.ep_progress.setFormat("%v / %m frames")
        pcol.addWidget(self.ep_progress)
        self.state_label = QLabel(tr("대기"))
        self.state_label.setFont(QFont("", 11, QFont.Weight.Bold))
        pcol.addWidget(self.state_label)
        self.save_status_label = QLabel("")
        self.save_status_label.setStyleSheet("color:#888;")
        pcol.addWidget(self.save_status_label)
        self.verdict_label = QLabel("")
        self.verdict_label.setWordWrap(True)
        pcol.addWidget(self.verdict_label)
        self.shortcut_hint = QLabel("")
        self.shortcut_hint.setStyleSheet(
            "color:#2ecc71; font-family:monospace; font-weight:bold;")
        self.shortcut_hint.setWordWrap(True)
        pcol.addWidget(self.shortcut_hint)
        col.addWidget(prog)
        col.addStretch()
        return w

    def _page_dataset(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        search = QLineEdit()
        search.setPlaceholderText(f"{tr('에피소드 검색')} ({TODO_MARK})")
        mark_todo(search, tr("검색/필터는 아직 없습니다."))
        col.addWidget(search)
        self.dataset_tree = QTreeWidget()
        self.dataset_tree.setColumnCount(3)
        self.dataset_tree.setHeaderLabels([tr("파일 / 에피소드"), tr("프레임"), tr("결과")])
        self.dataset_tree.setColumnWidth(0, 300)
        # 큐레이션은 실패 여러 개를 한 번에 지우는 작업이다.
        self.dataset_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.dataset_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.dataset_tree.itemSelectionChanged.connect(self._on_dataset_selection)
        col.addWidget(self.dataset_tree, 1)
        row = QHBoxLayout()
        # 파일 삭제는 여기 없다. 에피소드 삭제 바로 옆에 두었더니 실제로 오클릭이
        # 났고, 한 번에 태스크 하나가 통째로 날아간다. 되돌릴 수 없는 조작은
        # 한 단계 더 들어가야 닿도록 Dataset 메뉴에만 둔다.
        for text, slot in ((tr("새로고침"), self._refresh_dataset_tree),
                           (tr("실패만 선택"), self._on_select_failed),
                           (tr("튀는 것만 선택"), self._on_select_jerky),
                           (tr("에피소드 삭제"), self._on_delete_selected),
                           (tr("구조 확인"), self._on_show_structure)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        col.addLayout(row)
        self.dataset_hint = QLabel(tr(
            "에피소드를 고르면 Playback 탭에서 재생됩니다. 삭제는 수집 중이 아닌 "
            "파일이면 세션 없이도 됩니다."))
        self.dataset_hint.setStyleSheet("color:#888;")
        self.dataset_hint.setWordWrap(True)
        col.addWidget(self.dataset_hint)
        return w

    def _page_upload(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        acct_text, acct_color = hf_account()
        self.hf_label = QLabel(acct_text)
        self.hf_label.setStyleSheet(f"color:{acct_color}; font-weight:bold;")
        self.hf_label.setWordWrap(True)
        col.addWidget(self.hf_label)
        # 이 PC는 공용이라 '누구로 올라가는가'가 매번 다를 수 있다. 확인과 전환을
        # 업로드 버튼 바로 위에 둔다 -- 올린 뒤 커밋 기록에서 알게 되면 늦는다.
        acct_btn = QPushButton(tr("계정 확인 / 전환..."))
        acct_btn.clicked.connect(self._on_hf_accounts)
        col.addWidget(acct_btn)
        pipe_btn = QPushButton(tr("전체 처리 (재압축 → 변환 → 업로드)"))
        pipe_btn.setStyleSheet("background-color:#2ecc71; color:white; font-weight:bold; padding:8px;")
        pipe_btn.setToolTip(tr("Hub과 로컬을 대조해 필요한 것만 순서대로 실행합니다. "
                               "확인 창에서 시작을 누르면 끝까지 무인으로 진행합니다."))
        pipe_btn.clicked.connect(self._on_pipeline)
        col.addWidget(pipe_btn)
        for text, slot, style in (
            (tr("용량 최적화 (재압축)"), self._on_repack, "background-color:#9b59b6; color:white;"),
            (tr("HDF5 업로드..."), self._on_hdf5_upload, ""),
            (tr("LeRobot 변환/업로드..."), self._on_lerobot, ""),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if style:
                b.setStyleSheet(style)
            col.addWidget(b)
        note = QLabel(tr(
            "변환과 업로드는 분리되어 있습니다. Hub에 올라간 에피소드는 지울 수 "
            "없으므로, 변환 결과를 확인한 뒤 업로드하세요."))
        note.setStyleSheet("color:#888;")
        note.setWordWrap(True)
        col.addWidget(note)
        qbox = QGroupBox(f"{tr('업로드 큐 / 이력')} ({TODO_MARK})")
        qcol = QVBoxLayout(qbox)
        qcol.addWidget(QLabel(tr("업로드는 현재 한 번에 하나씩, 로그 탭으로만 확인합니다.")))
        mark_todo(qbox, tr("큐잉과 이력 보관은 아직 없습니다."))
        col.addWidget(qbox)
        col.addStretch()
        return w

    def _page_stats(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        self.stats_labels = {}
        box = QGroupBox(tr("이번 세션"))
        form = QFormLayout(box)
        for key, label in (("saved", "저장된 에피소드"), ("success", "성공"),
                           ("failed", "실패"), ("discarded", "버림"),
                           ("frames", "총 프레임"), ("elapsed", "경과 시간"),
                           ("rate", "분당 에피소드")):
            lab = QLabel("-")
            lab.setFont(QFont("", 10, QFont.Weight.Bold))
            form.addRow(tr(label), lab)
            self.stats_labels[key] = lab
        col.addWidget(box)
        self.disk_box = QGroupBox(tr("디스크"))
        dform = QFormLayout(self.disk_box)
        self.disk_label = QLabel("-")
        dform.addRow(tr("저장 경로 여유"), self.disk_label)
        col.addWidget(self.disk_box)

        # 파일 목록은 여기 없다. Dataset 패널의 트리가 이미 파일과 에피소드를
        # 모두 들고 있어서, 같은 목록을 두 군데 두면 어느 쪽 선택이 분석에
        # 반영되는지가 매번 헷갈린다. 선택은 Dataset 하나로 모은다.
        motion = QGroupBox(tr("움직임 분석"))
        mcol = QVBoxLayout(motion)
        self.stats_hint = QLabel(tr("Dataset 패널에서 파일이나 에피소드를 고르면 "
                                    "Analysis 탭에 반영됩니다."))
        self.stats_hint.setStyleSheet("color:#888;")
        self.stats_hint.setWordWrap(True)
        mcol.addWidget(self.stats_hint)
        rescan = QPushButton(tr("다시 분석"))
        rescan.clicked.connect(lambda: self._refresh_analysis(force=True))
        mcol.addWidget(rescan)
        col.addWidget(motion)
        col.addStretch()
        return w

    # ------------------------------------------------------------- 분석 탭
    def _build_analysis_tab(self) -> QWidget:
        """Center-tab analysis: the curve view plus the curation list.

        It lives in the center, next to Live/Playback, because judging a take
        means looking at its curves and its video together -- putting the plots
        in a side panel would have made them too narrow to read.
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 4, 4, 4)
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lcol = QVBoxLayout(left)
        lcol.setContentsMargins(0, 0, 0, 0)
        self.analysis_summary = QLabel(tr("Statistics 패널에서 '다시 분석'을 누르세요."))
        self.analysis_summary.setWordWrap(True)
        self.analysis_summary.setStyleSheet("font-weight:bold;")
        lcol.addWidget(self.analysis_summary)

        self.plot_grid = QGridLayout()
        self.series_plots = {}
        # LeRobot 뷰어와 같은 묶음: 인접 관절끼리 스케일이 비슷해 같은 축에 얹힌다.
        for i, (title, dims) in enumerate((
            ("joint1.pos, joint2.pos", [(0, "joint1.pos"), (1, "joint2.pos")]),
            ("joint4.pos, joint5.pos", [(3, "joint4.pos"), (4, "joint5.pos")]),
            ("joint6.pos, joint7.pos", [(5, "joint6.pos"), (6, "joint7.pos")]),
            ("joint3.pos", [(2, "joint3.pos")]),
            ("gripper.pos", [(7, "gripper.pos")]),
        )):
            plot = SeriesPlot(title)
            self.series_plots[title] = (plot, dims)
            self.plot_grid.addWidget(plot, i // 2, i % 2)
        lcol.addLayout(self.plot_grid, 1)
        legend = QLabel(tr("실선 observation.state   ┄ 파선 observation.commanded_state"
                           "   ┈ 점선 action"))
        legend.setStyleSheet("color:#888;")
        lcol.addWidget(legend)
        split.addWidget(left)

        right = QWidget()
        rcol = QVBoxLayout(right)
        rcol.setContentsMargins(0, 0, 0, 0)

        self.dim_bars = BarStrip()
        dim_box = QGroupBox(tr("차원별 σ(Δa) — 전체 평균"))
        QVBoxLayout(dim_box).addWidget(self.dim_bars)
        rcol.addWidget(dim_box)

        self.da_hist = Histogram(tr("에피소드 평균 |Δa| 분포"))
        rcol.addWidget(self.da_hist)

        filt = QGroupBox(tr("큐레이션 후보"))
        fcol = QVBoxLayout(filt)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("기준")))
        self.rank_combo = QComboBox()
        # 전역 평균은 품질이 아니라 task의 속도를 재므로 기본이 아니다 -- 실측에서
        # 전역 상위 10개가 거의 한 task였다.
        for label, key in (("task 내 z-score (권장)", "z"), ("스파이크 횟수", "spikes"),
                           ("전역 평균 |Δa|", "mean"), ("길이 (짧은 순)", "short"),
                           ("움직임 적은 순", "still")):
            self.rank_combo.addItem(tr(label), key)
        self.rank_combo.currentIndexChanged.connect(self._refresh_rank_list)
        row.addWidget(self.rank_combo, 1)
        fcol.addLayout(row)

        len_row = QHBoxLayout()
        len_row.addWidget(QLabel(tr("길이(초)")))
        self.len_min_spin = QSlider(Qt.Orientation.Horizontal)
        self.len_max_spin = QSlider(Qt.Orientation.Horizontal)
        for s in (self.len_min_spin, self.len_max_spin):
            s.setRange(0, 300)
            s.valueChanged.connect(self._refresh_rank_list)
        self.len_min_spin.setValue(0)
        self.len_max_spin.setValue(300)
        len_row.addWidget(self.len_min_spin, 1)
        len_row.addWidget(self.len_max_spin, 1)
        self.len_label = QLabel("-")
        self.len_label.setMinimumWidth(96)
        len_row.addWidget(self.len_label)
        fcol.addLayout(len_row)

        self.rank_tree = QTreeWidget()
        self.rank_tree.setColumnCount(5)
        self.rank_tree.setHeaderLabels([tr("에피소드"), tr("점수"), tr("길이"),
                                        tr("스파이크"), tr("task")])
        self.rank_tree.setRootIsDecorated(False)
        self.rank_tree.setColumnWidth(0, 150)
        self.rank_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rank_tree.itemSelectionChanged.connect(self._on_rank_selected)
        self.rank_tree.setMinimumHeight(220)
        fcol.addWidget(self.rank_tree, 1)

        btns = QHBoxLayout()
        for text, slot in ((tr("재생해서 확인"), self._on_rank_play),
                           (tr("선택 삭제"), self._on_rank_delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        fcol.addLayout(btns)
        warn = QLabel(tr("자동으로 지우지 않습니다. 목록에서 고른 것만 삭제합니다."))
        warn.setStyleSheet("color:#888;")
        warn.setWordWrap(True)
        fcol.addWidget(warn)
        rcol.addWidget(filt, 1)
        split.addWidget(right)
        split.setSizes([700, 430])
        outer.addWidget(split)
        return page

    def _page_settings(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        lang = QPushButton(tr("언어 전환 (한국어 / English)"))
        lang.clicked.connect(self._toggle_language)
        col.addWidget(lang)
        schema = QPushButton(tr("데이터셋 스키마..."))
        schema.clicked.connect(self._on_schema)
        col.addWidget(schema)
        self.schema_label = QLabel("")
        self.schema_label.setStyleSheet("color:#888;")
        self.schema_label.setWordWrap(True)
        col.addWidget(self.schema_label)
        self._refresh_schema_label()
        col.addStretch()
        return w

    # -------------------------------------------------------------- right
    def _build_right(self) -> None:
        self.right_panel = QWidget()
        col = QVBoxLayout(self.right_panel)
        col.setContentsMargins(6, 6, 6, 6)

        self.right_fields = {}
        for title, keys in (
            ("Robot", (("robot", "연결"), ("node", "노드"), ("state", "상태"))),
            ("Camera", (("cam_agent", "Agent"), ("cam_wrist", "Wrist"), ("fps", "FPS"))),
            ("Recording", (("recording", "기록"), ("episode", "마지막 에피소드"),
                           ("frames", "프레임"))),
            # 파일과 스키마가 한 칸에 같이 있어야 "지금 어디에, 어떤 형식으로
            # 쌓이는가"가 한눈에 잡힌다. 세션 중에는 그 세션의 값이, 아닐 때는
            # 트리에서 고른 파일의 값이 뜬다.
            ("Dataset", (("ds_file", "파일"), ("ds_task", "태스크"),
                         ("ds_episodes", "에피소드"), ("ds_action", "액션 공간"),
                         ("ds_gripper", "그리퍼 규약"), ("ds_image", "이미지"),
                         ("ds_fps", "FPS"), ("ds_repack", "재압축"))),
        ):
            box = QGroupBox(tr(title))
            form = QFormLayout(box)
            form.setVerticalSpacing(6)
            for key, label in keys:
                lab = QLabel("-")
                lab.setWordWrap(True)
                if key in WIDE_FIELDS:
                    # 파일명과 자연어 지시문만 길다. 라벨-값을 좌우로 놓으면 값이
                    # 150px 남짓에 갇혀 서너 줄로 접히는데, 정작 수집 중 가장
                    # 자주 확인하는 두 줄이다. 이 둘만 캡션을 위에 올리고 값이
                    # 패널 폭을 다 쓰게 한다.
                    cap = QLabel(tr(label))
                    cap.setStyleSheet("color:#888; font-size:11px;")
                    lab.setStyleSheet("padding: 2px 0 6px 0;")
                    lab.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse)
                    # QLabel은 wordWrap을 켜도 sizePolicy의 heightForWidth가
                    # 꺼져 있어 레이아웃이 높이를 한 줄치로만 준다 -- 두 줄짜리
                    # 지시문이 잘려서 뒤가 안 보였다. 켜 줘야 접힌 만큼 높이가
                    # 확보된다.
                    sp = lab.sizePolicy()
                    sp.setHeightForWidth(True)
                    sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
                    lab.setSizePolicy(sp)
                    form.addRow(cap)
                    form.addRow(lab)
                else:
                    form.addRow(tr(label), lab)
                self.right_fields[key] = lab
            col.addWidget(box)

        sysbox = QGroupBox(f"System ({TODO_MARK})")
        sform = QFormLayout(sysbox)
        for label in ("CPU", "GPU", "Memory"):
            sform.addRow(label, QLabel("-"))
        mark_todo(sysbox, tr("시스템 사용률 표시는 아직 없습니다. 디스크는 Statistics에 있습니다."))
        col.addWidget(sysbox)
        col.addStretch()

    # ------------------------------------------------------------- bottom
    def _build_bottom(self) -> None:
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setDocumentMode(True)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.bottom_tabs.addTab(self.log_view, tr("Log"))
        self.upload_view = QPlainTextEdit()
        self.upload_view.setReadOnly(True)
        self.upload_view.setMaximumBlockCount(4000)
        self.bottom_tabs.addTab(self.upload_view, tr("Upload"))
        self.validation_view = QPlainTextEdit()
        self.validation_view.setReadOnly(True)
        self.bottom_tabs.addTab(self.validation_view, tr("Validation"))
        for title, why in (
            (tr("ROS2"), tr("이 스택은 ROS2가 아니라 pylibfranka로 직접 구동합니다.")),
            (tr("Terminal"), tr("임베디드 셸은 아직 없습니다. 로그 탭을 쓰세요.")),
        ):
            ph = QPlainTextEdit(f"{title} — {TODO_MARK}\n\n{why}")
            ph.setReadOnly(True)
            ph.setStyleSheet(TODO_STYLE)
            idx = self.bottom_tabs.addTab(ph, f"{title} ({TODO_MARK})")
            self.bottom_tabs.setTabEnabled(idx, False)

    # ------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        self.activity_bar = QToolBar()
        self.activity_bar.setOrientation(Qt.Orientation.Vertical)
        self.activity_bar.setMovable(False)
        self.activity_bar.setIconSize(self.activity_bar.iconSize())
        self.activity_bar.setStyleSheet(
            "QToolBar{background:#2b2b2b; border:none; spacing:2px; padding:4px;}"
            "QToolButton{color:#bbb; font-size:20px; padding:8px; border:none;}"
            "QToolButton:hover{background:#3a3a3a;}"
            "QToolButton:checked{background:#3a3a3a; color:#fff;"
            " border-left:2px solid #2ecc71;}"
        )
        self._activity_group = QActionGroup(self)
        self._activity_group.setExclusive(True)
        self._activity_actions = {}
        for key, icon, title, tip in ACTIVITIES:
            act = QAction(icon, self)
            act.setCheckable(True)
            act.setToolTip(f"{title} — {tr(tip)}")
            act.triggered.connect(lambda _c, k=key: self._set_activity(k))
            self._activity_group.addAction(act)
            self.activity_bar.addAction(act)
            self._activity_actions[key] = act

        # 로그는 중앙 열 안에, 카메라 바로 아래에만 둔다. 창 전체 폭으로 깔면
        # 왼쪽/오른쪽 패널이 로그 높이만큼 잘려서, 정작 세로로 긴 것들(에피소드
        # 트리, 상태 목록)이 먼저 손해를 본다. VS Code의 사이드바가 전체 높이를
        # 쓰고 패널이 에디터 아래에만 오는 것과 같은 이유다.
        self.center_split = QSplitter(Qt.Orientation.Vertical)
        self.center_split.addWidget(self.center_tabs)
        self.center_split.addWidget(self.bottom_tabs)
        self.center_split.setStretchFactor(0, 1)
        self.center_split.setStretchFactor(1, 0)
        self.center_split.setSizes([720, 220])
        self.center_split.setChildrenCollapsible(False)

        self.upper_split = QSplitter(Qt.Orientation.Horizontal)
        self.left_stack.setMinimumWidth(240)
        self.right_panel.setMinimumWidth(200)
        self.center_tabs.setMinimumWidth(420)
        self.bottom_tabs.setMinimumHeight(90)
        self.upper_split.addWidget(self.left_stack)
        self.upper_split.addWidget(self.center_split)
        self.upper_split.addWidget(self.right_panel)
        # Only the center grows when the window does: the two side panels hold
        # text at a readable width, the camera is the thing worth more pixels.
        self.upper_split.setStretchFactor(0, 0)
        self.upper_split.setStretchFactor(1, 1)
        self.upper_split.setStretchFactor(2, 0)
        self.upper_split.setSizes([320, 1120, 300])
        self.upper_split.setChildrenCollapsible(False)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.activity_bar)
        row.addWidget(self.upper_split, 1)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> None:
        tb = QToolBar(tr("주요 작업"))
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.tb_actions = {}

        def add(key: str, text: str, slot, tip: str = "") -> QAction:
            act = QAction(text, self)
            act.setToolTip(tip or text)
            act.triggered.connect(slot)
            tb.addAction(act)
            self.tb_actions[key] = act
            return act

        add("connect", tr("▶ Connect"), self._on_connect, tr("로봇에 연결하고 세션 시작"))
        add("disconnect", tr("■ Disconnect"), self._on_disconnect, tr("세션 종료"))
        tb.addSeparator()
        add("record", tr("● Record"), lambda: self._cmd("cmd_start_teleop"), tr("기록 시작"))
        # _save, not _cmd -- the success flag has to be recorded for the stats
        # panel, and a toolbar button that counts differently from the side
        # panel button next to it is a bug waiting to be blamed on the stats.
        add("save", tr("✔ Save"), lambda: self._save(True), tr("성공으로 끝내기"))
        add("savefail", tr("✖ Save (fail)"), lambda: self._save(False),
            tr("실패로 끝내기 (Esc). 판정은 리셋 구간에서 Esc로 뒤집을 수 있습니다"))
        add("discard", tr("🗑 Discard"), lambda: self._cmd("cmd_discard_episode"))
        tb.addSeparator()
        add("home", tr("⌂ Home"), lambda: self._cmd("cmd_go_home"))
        add("refresh_cam", tr("⟳ Camera"), self._refresh_cameras)
        tb.addSeparator()
        add("upload", tr("☁ Upload"), lambda: self._set_activity("upload"))

    def _build_menu(self) -> None:
        mb = self.menuBar()

        m = mb.addMenu(tr("File"))
        m.addAction(tr("데이터 저장 경로 열기..."), self._browse_root)
        m.addAction(tr("로그 폴더 열기"), lambda: self.log(f"[로그] {LOG_DIR}"))
        m.addSeparator()
        m.addAction(tr("종료"), self.close)

        m = mb.addMenu(tr("Dataset"))
        m.addAction(tr("새로고침"), self._refresh_dataset_tree)
        m.addAction(tr("실패만 선택"), self._on_select_failed)
        m.addAction(tr("튀는 것만 선택 (task 내 z ≥ 2)"), self._on_select_jerky)
        m.addAction(tr("에피소드 삭제"), self._on_delete_selected)
        m.addAction(tr("파일 삭제"), self._on_delete_file)
        m.addAction(tr("구조 확인..."), self._on_show_structure)
        m.addSeparator()
        m.addAction(tr("용량 최적화 (재압축)..."), self._on_repack)
        m.addAction(tr("LeRobot 변환/업로드..."), self._on_lerobot)
        m.addSeparator()
        m.addAction(tr("전체 처리 (재압축 → 변환 → 업로드)..."), self._on_pipeline)
        m.addAction(tr("HDF5 업로드..."), self._on_hdf5_upload)

        m = mb.addMenu(tr("Robot"))
        m.addAction(tr("노드 시작"), self._on_start_node)
        m.addAction(tr("노드 종료"), self._on_stop_node)
        m.addSeparator()
        m.addAction(tr("연결"), self._on_connect)
        m.addAction(tr("세션 종료"), self._on_disconnect)
        m.addAction(tr("홈으로"), lambda: self._cmd("cmd_go_home"))

        m = mb.addMenu(tr("Camera"))
        m.addAction(tr("새로고침"), self._refresh_cameras)
        m.addAction(tr("미리보기 중지"), self._stop_previews_async)

        m = mb.addMenu(tr("View"))
        for key, _icon, title, _tip in ACTIVITIES:
            m.addAction(title, lambda _c=False, k=key: self._set_activity(k))
        m.addSeparator()
        self.act_toggle_bottom = QAction(tr("하단 패널"), self, checkable=True, checked=True)
        self.act_toggle_bottom.triggered.connect(
            lambda on: self.bottom_tabs.setVisible(on))
        m.addAction(self.act_toggle_bottom)
        self.act_toggle_right = QAction(tr("오른쪽 패널"), self, checkable=True, checked=True)
        self.act_toggle_right.triggered.connect(
            lambda on: self.right_panel.setVisible(on))
        m.addAction(self.act_toggle_right)

        m = mb.addMenu(tr("Tools"))
        m.addAction(tr("시스템 튜닝 실행 (runme.sh)"), self._run_runme)
        m.addAction(tr("카메라 점검 (USB 속도·프레임)"), self._on_check_cameras)
        m.addAction(tr("Hugging Face 계정..."), self._on_hf_accounts)
        m.addSeparator()
        m.addAction(tr("데이터셋 스키마..."), self._on_schema)
        m.addAction(tr("언어 전환"), self._toggle_language)

        m = mb.addMenu(tr("Help"))
        m.addAction(tr("단축키..."), lambda: QMessageBox.information(
            self, tr("단축키"),
            tr("양손이 GELLO 리더 위에 있으므로 마우스 없이 조작합니다.\n"
               "같은 키가 상태에 따라 다르게 동작합니다.\n\n"
               "  자세 정렬 중   Space        텔레옵 시작\n"
               "  기록 중        Space        성공으로 끝내기\n"
               "  기록 중        Esc          실패로 끝내기\n"
               "  기록 중        Delete       폐기\n"
               "  자세 정렬 중   Enter        자동 정렬 다시 (대략 맞춘 뒤에만)\n"
               "  리셋 대기 중   Esc          직전 에피소드 판정 뒤집기\n"
               "  리셋 대기 중   Enter        대기 건너뛰기\n\n"
               "지금 쓸 수 있는 키는 Collect 패널 아래에 초록색으로 표시됩니다.")))
        m.addSeparator()
        m.addAction(tr("정보"), lambda: QMessageBox.information(
            self, tr("정보"),
            tr("FR3 GELLO 데이터 수집 워크스페이스\n\n"
               "카메라는 항상 중앙에 유지됩니다. 왼쪽 아이콘 바로 패널만 바꾸세요.")))

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lights = {}
        for key, label in (("robot", "Robot"), ("camera", "Camera"),
                           ("recording", "Recording"), ("node", "Node")):
            light = StatusLight(label)
            sb.addWidget(light)
            self.lights[key] = light
        self.sb_right = QLabel("")
        sb.addPermanentWidget(self.sb_right)

    # ---------------------------------------------------------- activity
    def _set_activity(self, key: str) -> None:
        """Switch the LEFT panel only. The center camera is untouched -- that
        is the whole point of this layout, so nothing here may touch it."""
        self.left_stack.setCurrentIndex(self.left_pages[key])
        act = self._activity_actions.get(key)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        if key == "stats":
            self._refresh_stats()
            if not self._stats:
                self._refresh_analysis()
        elif key == "dataset":
            self._refresh_dataset_tree()
        elif key == "upload":
            text, color = hf_account()
            self.hf_label.setText(text)
            self.hf_label.setStyleSheet(f"color:{color}; font-weight:bold;")

    # -------------------------------------------------------------- utils
    def _view(self, view: str) -> QPlainTextEdit:
        return {"log": self.log_view, "upload": self.upload_view,
                "validation": self.validation_view}[view]

    def log(self, msg: str, view: str = "log") -> None:
        self._progress_line.pop(view, None)  # 다음 진행률은 새 줄에서 시작
        target = self._view(view)
        target.appendPlainText(msg)
        if self._log_file is not None:
            self._log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def _log_progress(self, msg: str, view: str) -> None:
        """Progress that overwrites its own last line instead of stacking.

        A 1.3 GB upload prints a bar every second; appended, that buries every
        other message in the tab and makes the log useless exactly while a long
        job is running. Replacing the previous progress line keeps one live line
        and leaves the surrounding log readable.

        Deliberately not written to the log file -- the file is what gets read
        after a crash, and hundreds of superseded percentages help nobody there.
        """
        target = self._view(view)
        if self._progress_line.get(view):
            cursor = target.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(msg)
        else:
            target.appendPlainText(msg)
            self._progress_line[view] = True

    def _cmd(self, name: str, *args) -> None:
        if self.worker is None:
            self.log("[제어] 아직 연결되지 않았습니다.")
            return
        getattr(self.worker, name)(*args)

    def _toggle_last_verdict(self) -> None:
        """Flips the success flag of the episode that was just saved.

        Pressing save *is* the judgement that the take is over; whether it
        succeeded is a separate question, and one the operator can answer
        better a few seconds later while the arm homes than in the instant
        they let go. The re-label goes through the saver queue, so a toggle
        sent while that episode is still being written lands after it.
        """
        if self.worker is None or self._no_dataset_session:
            return
        if self._last_saved_name is None:
            # 저장이 아직 백그라운드에서 돌고 있어 이름을 모른다. 의사만 적어
            # 두고 episode_saved가 오면 그때 반영한다.
            self._pending_verdict_toggle = not self._pending_verdict_toggle
            self.log("[판정] 저장이 끝나면 직전 에피소드 판정을 뒤집습니다."
                     if self._pending_verdict_toggle else "[판정] 뒤집기를 취소했습니다.")
            self._refresh_verdict_label()
            return
        self._last_saved_success = not self._last_saved_success
        self.worker.cmd_set_episode_success(self._last_saved_name, self._last_saved_success)
        self._session["success"] += 1 if self._last_saved_success else -1
        self._session["failed"] += -1 if self._last_saved_success else 1
        self._refresh_verdict_label()
        self._refresh_stats()

    def _refresh_verdict_label(self) -> None:
        if self._last_saved_name is None:
            self.verdict_label.setText(
                tr("판정 뒤집기 예약됨 (Esc로 취소)") if self._pending_verdict_toggle else "")
            self.verdict_label.setStyleSheet("color:#f39c12;")
            return
        ok = self._last_saved_success
        self.verdict_label.setText(
            tr("직전 {n}: {v}   —   Esc로 뒤집기").format(
                n=self._last_saved_name, v=tr("성공") if ok else tr("실패")))
        self.verdict_label.setStyleSheet(
            "color:#2ecc71; font-weight:bold;" if ok else "color:#e74c3c; font-weight:bold;")

    def _save(self, success: bool) -> None:
        """episode_saved carries only (name, n_frames), so the success flag has
        to be remembered here -- the worker never sends it back."""
        if self.worker is None:
            self.log("[제어] 아직 연결되지 않았습니다.")
            return
        self._pending_success = success
        self.worker.cmd_save_episode(success)

    def _browse_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("데이터 저장 경로"), self.root_edit.text())
        if d:
            self.root_edit.setText(d)
            self._refresh_dataset_tree()

    def _toggle_language(self) -> None:
        set_language("en" if get_language() == "ko" else "ko")
        self.log(f"[설정] 언어: {get_language()} (일부 문구는 재시작 후 반영)")

    def _refresh_schema_label(self) -> None:
        self.schema_label.setText(
            tr("action space: {a}").format(a=getattr(self.schema, "action_space", "?")))

    def _on_schema(self) -> None:
        dlg = DatasetSchemaDialog(self, self.schema)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.schema = dlg.result_config()
            save_schema_config(self.schema)
            self._refresh_schema_label()
            self.log("[설정] 데이터셋 스키마를 저장했습니다.")

    # ------------------------------------------------------------ cameras
    def _refresh_cameras(self) -> None:
        try:
            from lerobot.cameras.realsense import RealSenseCamera

            cams = RealSenseCamera.find_cameras()
        except Exception as e:  # noqa: BLE001
            self.camera_hint.setText(tr("카메라 목록 조회 실패: {e}").format(e=e))
            self.log(f"[카메라] 목록 조회 실패: {type(e).__name__}: {e}")
            return
        entries = []
        for c in cams:
            serial = str(c.get("serial_number") or c.get("id") or "")
            name = str(c.get("name") or "RealSense")
            if serial:
                entries.append((serial, f"{name} ({serial})"))
        for combo, remembered in ((self.agent_combo, "agent_serial"),
                                  (self.wrist_combo, "wrist_serial")):
            cur = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("(선택 안함)"), "")
            for serial, label in entries:
                combo.addItem(label, serial)
            want = cur or self._recents.most_recent(remembered, "")
            if want:
                for i in range(combo.count()):
                    if combo.itemData(i) == want or combo.itemText(i) == want:
                        combo.setCurrentIndex(i)
                        break
            combo.blockSignals(False)
        self.camera_hint.setText(tr("{n}대 감지됨").format(n=len(entries)))
        self.log(f"[카메라] {len(entries)}대 감지: {[s for s, _ in entries]}")

    def _combo_serial(self, combo: QComboBox) -> str:
        data = combo.currentData()
        if data:
            return str(data)
        text = combo.currentText().strip()
        return "" if text.startswith("(") else text

    def _on_camera_changed(self) -> None:
        if self.worker is not None:
            return  # the session owns the cameras; previews must stay off
        self._restart_previews()

    def _restart_previews(self) -> None:
        self._stop_previews_async()
        for role, combo in (("agent", self.agent_combo), ("wrist", self.wrist_combo)):
            serial = self._combo_serial(combo)
            if not serial:
                self.live_views[role].clear_frame(tr("카메라를 선택하세요"))
                self.right_fields[f"cam_{role}"].setText("-")
                continue
            w = CameraPreviewWorker(serial)
            w.frame_ready.connect(lambda f, r=role: self._on_preview_frame(r, f))
            w.error.connect(lambda m, r=role: self._on_preview_error(r, m))
            w.start()
            setattr(self, f"{role}_preview", w)
            self.right_fields[f"cam_{role}"].setText(serial)
        self.lights["camera"].set("ok" if (self.agent_preview or self.wrist_preview) else "off",
                                  tr("미리보기") if (self.agent_preview or self.wrist_preview) else "-")

    def _alert(self, title: str, text: str, icon=None) -> None:
        """Non-modal notice.

        A modal QMessageBox runs its own event loop, so anything the app does
        while it is up runs *nested inside* it -- and if that work blocks, the
        dialog itself stops responding and cannot even be dismissed. That is
        what happened when a fatal camera error and a session teardown landed
        together. Non-modal has neither problem: the dialog is always
        closeable, and the window behind it keeps drawing.
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(icon if icon is not None else QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()
        box.raise_()

    def connect_progress(self, waited: float) -> None:
        self.statusBar().showMessage(
            tr("카메라 정리 중... {s:.0f}초 (정리되면 자동으로 연결합니다)").format(s=waited),
            1000)
        self.lights["camera"].set("busy", tr("정리 중"))

    def _previews_busy(self) -> bool:
        self._dying_previews = [w for w in self._dying_previews if w.isRunning()]
        return bool(self._dying_previews)

    def _release_preview(self, role: str) -> None:
        """Asks one preview thread to stop, and cuts it off from the UI now.

        Disconnecting before waiting is the important half. A thread that is
        slow to notice the stop flag (the wrist D405 can sit inside a read for
        a second) used to keep emitting frames into the GUI thread the whole
        time, and if a restart replaced the handle while it was still alive the
        old one was orphaned but still connected -- so every retry added
        another 30 fps of scaling work to the UI thread. Once disconnected an
        orphan is harmless: it only still owns the camera, which is what
        _previews_busy() reports.
        """
        w = getattr(self, f"{role}_preview", None)
        if w is None:
            return
        for sig in (w.frame_ready, w.error):
            try:
                sig.disconnect()
            except TypeError:
                pass  # already disconnected
        w.stop()
        setattr(self, f"{role}_preview", None)
        if w.isRunning():
            self._dying_previews.append(w)
            w.finished.connect(w.deleteLater)

    def _stop_previews_async(self) -> None:
        """Non-blocking stop. The GUI thread never waits on a camera here --
        that wait was up to 7 s per thread and read as a hang."""
        for role in ("agent", "wrist"):
            self._release_preview(role)
        self.lights["camera"].set("busy" if self._previews_busy() else "off",
                                  tr("정리 중") if self._previews_busy() else "-")

    def _stop_previews_blocking(self, timeout_ms: int = 4000) -> None:
        """Only for shutdown: wait so the cameras are released before exit.

        Blocking is acceptable here and nowhere else -- the window is closing,
        so there is no interaction left to make unresponsive.
        """
        self._stop_previews_async()
        for w in self._dying_previews:
            w.wait(timeout_ms)
        self._dying_previews = []

    def _on_preview_frame(self, role: str, frame) -> None:
        self.live_views[role].set_frame(frame)
        self._fps_count += 1

    def _on_preview_error(self, role: str, msg: str) -> None:
        self.live_views[role].clear_frame(tr("미리보기 실패"))
        self.log(f"[카메라 미리보기 실패] {role}: {msg}")
        self.lights["camera"].set("bad", tr("오류"))

    # ----------------------------------------------------------- session
    def _on_connect(self) -> None:
        if self.worker is not None:
            self.log("[연결] 이미 세션이 실행 중입니다.")
            return
        no_dataset = self.no_dataset_check.isChecked()
        task = self.task_edit.text().strip()
        lang = self.lang_edit.text().strip()
        if not task and not no_dataset:
            QMessageBox.warning(self, tr("Task 이름 필요"), tr("Task 이름을 입력하세요."))
            return
        if no_dataset:
            # Never reaches a writer, but WorkerConfig requires the fields and
            # a blank task_name would show up as an empty label everywhere.
            task = task or "practice"
        agent, wrist = self._combo_serial(self.agent_combo), self._combo_serial(self.wrist_combo)
        if not agent or not wrist:
            QMessageBox.warning(self, tr("카메라 선택 필요"),
                                tr("Agent / Wrist 카메라를 모두 선택하세요."))
            return
        if agent == wrist:
            QMessageBox.warning(self, tr("카메라 중복"),
                                tr("Agent와 Wrist에 같은 카메라가 선택되었습니다."))
            return
        try:
            ep_len = float(self.eplen_edit.text())
            reset_wait = float(self.resetwait_edit.text())
        except ValueError:
            QMessageBox.warning(self, tr("입력 오류"), tr("길이/대기는 숫자여야 합니다."))
            return

        # The worker opens both cameras itself and a RealSense pipeline cannot
        # be opened twice, so the previews have to let go first. That release
        # can take a second or two on a flaky link -- waited for inline it just
        # looked like the app had died. Ask them to stop, then keep the UI
        # alive and retry on a timer until they are actually gone.
        self._stop_previews_async()
        if self._previews_busy():
            if self._connect_wait_since is None:
                self._connect_wait_since = time.monotonic()
                self.log("[카메라] 미리보기 정리를 기다리는 중 — 정리되면 자동으로 연결합니다.")
            waited = time.monotonic() - self._connect_wait_since
            if waited < 12.0:
                self.tb_actions["connect"].setEnabled(False)
                self.connect_progress(waited)
                QTimer.singleShot(200, self._on_connect)
                return
            self._connect_wait_since = None
            self.tb_actions["connect"].setEnabled(True)
            self.statusBar().clearMessage()
            self._alert(tr("카메라 해제 지연"),
                        tr("미리보기가 카메라를 12초 넘게 붙잡고 있습니다.\n\n"
                           "Camera 메뉴 > 미리보기 중지 후 다시 시도하세요. 계속되면 "
                           "USB 케이블을 다시 꽂아야 합니다 -- 손목 D405는 USB 2 링크라 "
                           "접촉이 나쁘면 이렇게 됩니다."))
            return
        self._connect_wait_since = None
        self.statusBar().clearMessage()
        cfg = WorkerConfig(
            task_name=task,
            language_instruction=lang or task.replace("_", " "),
            data_root=self.root_edit.text().strip(),
            grip=self.grip_combo.currentText(),
            reset_pose=self.reset_pose_combo.currentText(),
            max_episode_seconds=ep_len,
            reset_wait_seconds=reset_wait,
            enable_wall=self.wall_check.isChecked(),
            auto_match_pose=self.match_check.isChecked(),
            resume=self.resume_check.isChecked(),
            no_dataset=no_dataset,
            agent_camera_serial=agent,
            wrist_camera_serial=wrist,
            schema=self.schema,
        )
        for key, value in (("task", task), ("language", lang),
                           ("data_root", cfg.data_root),
                           ("agent_serial", agent), ("wrist_serial", wrist)):
            if value:
                self._recents.add(key, value)

        w = CollectionWorker(cfg)
        w.state_changed.connect(self._on_state)
        w.frames_ready.connect(self._on_frames)
        w.gate_status.connect(self._on_gate)
        w.pose_match_status.connect(self._on_pose_match)
        w.episode_progress.connect(self._on_progress)
        w.episode_saved.connect(self._on_saved)
        w.episode_discarded.connect(self._on_discarded)
        w.reset_countdown.connect(self._on_countdown)
        w.log_message.connect(self.log)
        w.node_status.connect(self._on_node_status)
        w.fatal_error.connect(self._on_fatal)
        w.connected.connect(self._on_connected)
        w.episode_list_changed.connect(self._on_episode_list)
        w.session_summary.connect(self._on_summary)
        # 저장은 CollectionWorker가 아니라 그 안의 EpisodeSaver 스레드가 알린다
        # (h5py 접근을 한 스레드로 직렬화하려고 분리해 둔 것). 워커 쪽 시그널만
        # 연결해 두면 episode_saved/episode_list_changed가 영원히 오지 않아,
        # 에피소드 수·세션 통계·실패 표시 해제·탐색기 목록이 전부 멈춘 채로
        # 있는다 -- 실제로 10개를 저장한 세션 로그에 [저장] 줄이 한 줄도 없었다.
        w.saver.episode_saved.connect(self._on_saved)
        w.saver.episode_list_changed.connect(self._on_episode_list)
        w.saver.log_message.connect(self.log)
        w.saver.save_status.connect(self._on_save_status)
        self.worker = w
        self._no_dataset_session = no_dataset
        # The right panel's serials were only filled by _restart_previews, so
        # they blanked out for the whole session -- exactly when knowing which
        # camera is which matters most.
        self.right_fields["cam_agent"].setText(agent)
        self.right_fields["cam_wrist"].setText(wrist)
        self.lights["camera"].set("ok", tr("세션"))
        self.ep_progress.setMaximum(max(1, int(ep_len * cfg.fps)))
        self._set_running(True)
        self._set_activity("collect")
        if no_dataset:
            self.log("[연결] 연습 모드 — 파일을 만들지 않습니다. 저장은 버려집니다.")
        else:
            self.log(f"[연결] 세션 시작: task={task!r}")
        w.start()

    def _on_disconnect(self) -> None:
        if self.worker is None:
            return
        self.log("[연결] 세션 종료를 요청했습니다...")
        self.worker.cmd_quit()

    def _set_running(self, running: bool) -> None:
        savable = running and not self._no_dataset_session
        for key in ("record", "discard", "home"):
            self.tb_actions[key].setEnabled(running)
        for key in ("save", "savefail"):
            self.tb_actions[key].setEnabled(savable)
        self.tb_actions["connect"].setEnabled(not running)
        self.tb_actions["disconnect"].setEnabled(running)
        for b in (self.start_btn, self.skip_btn, self.discard_btn, self.home_btn):
            b.setEnabled(running)
        for b in (self.save_ok_btn, self.save_ng_btn):
            b.setEnabled(savable)
        self.no_dataset_check.setEnabled(not running)
        self.task_box.setEnabled(not running and not self.no_dataset_check.isChecked())
        for w in (self.lang_edit, self.root_edit, self.agent_combo,
                  self.wrist_combo, self.reset_pose_combo, self.grip_combo,
                  self.eplen_edit, self.resetwait_edit, self.resume_check,
                  self.wall_check, self.match_check):
            w.setEnabled(not running)
        self.lights["robot"].set("ok" if running else "off",
                                 tr("연결됨") if running else tr("끊김"))
        self.right_fields["robot"].setText(tr("연결됨") if running else tr("끊김"))

    # ------------------------------------------------------ worker slots
    @pyqtSlot(str)
    def _on_state(self, state: str) -> None:
        if state == "recording" and self._current_state != "recording":
            # 표시는 에피소드 단위다. 새 기록이 시작되면 항상 성공에서 출발한다.
            # 직전 에피소드 판정은 이 시점부터 더 이상 뒤집을 수 없다 -- 리셋
            # 구간이 끝났고, 이제 '직전'이 무엇인지 헷갈릴 수 있다.
            self._last_saved_name = None
            self._pending_verdict_toggle = False
            self.verdict_label.setText("")
        self._current_state = state
        self.state_label.setText(STATE_LABELS.get(state, state))
        self.shortcut_hint.setText(SHORTCUT_HINTS.get(state, ""))
        self.right_fields["state"].setText(state)
        recording = "기록" in state or "record" in state.lower()
        self.lights["recording"].set("bad" if recording else "off",
                                     tr("기록 중") if recording else tr("대기"))
        self.right_fields["recording"].setText(tr("기록 중") if recording else tr("대기"))

    @pyqtSlot(object, object)
    def _on_frames(self, agent_rgb, wrist_rgb) -> None:
        if agent_rgb is not None:
            self.live_views["agent"].set_frame(agent_rgb)
        if wrist_rgb is not None:
            self.live_views["wrist"].set_frame(wrist_rgb)
        self._fps_count += 1

    @pyqtSlot(object, object, bool)
    def _on_gate(self, leader, follower, all_ok) -> None:
        if leader is None or follower is None:
            return
        d = np.asarray(leader, dtype=float) - np.asarray(follower, dtype=float)
        for i, bar in enumerate(self.delta_bars):
            if i < len(d):
                bar.update_delta(float(d[i]), GATE_RAD)
        self.gate_label.setText(tr("자세 일치 — 시작 가능") if all_ok
                                else tr("리더를 팔로워 자세에 맞추세요"))
        self.gate_label.setStyleSheet("color:#2ecc71;" if all_ok else "color:#e67e22;")
        # Enter/버튼은 워커와 같은 조건에서만 열린다. 잠겨 있는 이유가 보이도록
        # 게이트 상태의 힌트도 all_ok에 따라 바꾼다.
        self._gate_ok = all_ok
        self.match_btn.setEnabled(all_ok)
        if self._current_state == "gate":
            self.shortcut_hint.setText(
                "Space: 텔레옵 시작   Enter: 자동 정렬 다시" if all_ok
                else "Space: 텔레옵 시작   (Enter: 자세를 더 맞춰야 자동 정렬 가능)")

    @pyqtSlot(float, bool)
    def _on_pose_match(self, err, done) -> None:
        self.gate_label.setText(
            tr("자동 정렬 완료") if done else tr("자동 정렬 중... 오차 {e:.3f} rad").format(e=err))

    @pyqtSlot(int, float)
    def _on_progress(self, n_frames, seconds) -> None:
        self.ep_progress.setValue(n_frames)
        self.right_fields["frames"].setText(f"{n_frames} ({seconds:.1f}s)")

    @pyqtSlot(str, int)
    def _on_saved(self, name, n_frames) -> None:
        self._session["saved"] += 1
        self._session["frames"] += n_frames
        if self._pending_success is not None:
            self._session["success" if self._pending_success else "failed"] += 1
            self._pending_success = None
        self._last_saved_name = name
        if self._pending_success is not None:
            self._last_saved_success = self._pending_success
        if self._pending_verdict_toggle:
            # 저장 전에 눌러 둔 뒤집기를 이제 반영한다.
            self._pending_verdict_toggle = False
            self._last_saved_success = not self._last_saved_success
            self.worker.cmd_set_episode_success(name, self._last_saved_success)
        self._refresh_verdict_label()
        self.log(f"[저장] {name} ({n_frames} frames)")
        self.right_fields["episode"].setText(name)
        self._update_dataset_panel()
        self._refresh_stats()

    @pyqtSlot(str)
    def _on_save_status(self, text: str) -> None:
        """Background-save progress. Empty string means idle."""
        self.save_status_label.setText(text)
        self.save_status_label.setStyleSheet(
            "color:#f39c12;" if text else "color:#888;")

    @pyqtSlot(int)
    def _on_discarded(self, n_frames) -> None:
        self._session["discarded"] += 1
        self.log(f"[버림] {n_frames} frames")
        self._refresh_stats()

    @pyqtSlot(float)
    def _on_countdown(self, seconds) -> None:
        self.state_label.setText(tr("리셋 대기 {s:.0f}s").format(s=seconds))

    @pyqtSlot(bool)
    def _on_node_status(self, ok) -> None:
        self.lights["node"].set("ok" if ok else "bad", tr("정상") if ok else tr("응답 없음"))
        self.right_fields["node"].setText(tr("정상") if ok else tr("응답 없음"))

    @pyqtSlot(str)
    def _on_fatal(self, msg) -> None:
        self.log(f"[치명적 오류] {msg}")
        self._alert(tr("오류"), msg, QMessageBox.Icon.Critical)

    @pyqtSlot(int, str)
    def _on_connected(self, n_episodes, path) -> None:
        if self._no_dataset_session:
            # NullTaskWriter has no real path; claiming one here would make the
            # dataset tree think a file is locked by this session.
            self._update_dataset_panel()
            self.log("[연결] 연습 모드로 연결되었습니다.")
            return
        self.active_file_path = Path(path)
        self._episodes_at_connect = int(n_episodes)
        self._update_dataset_panel()
        self.log(f"[연결] 파일: {path} (기존 {n_episodes}개 에피소드)")
        self._refresh_dataset_tree()

    @pyqtSlot(list)
    def _on_episode_list(self, episodes) -> None:
        self.active_episode_cache = episodes
        self._refresh_dataset_tree()

    @pyqtSlot(dict)
    def _on_summary(self, summary) -> None:
        self.log(f"[세션 요약] {summary}")
        self.worker = None
        self._no_dataset_session = False
        self.active_file_path = None
        self.active_episode_cache = None
        self._set_running(False)
        self._refresh_dataset_tree()
        self._restart_previews()

    # -------------------------------------------------------------- stats
    def _tick_fps(self) -> None:
        self._fps_value = self._fps_count
        self._fps_count = 0
        self.right_fields["fps"].setText(f"{self._fps_value:.0f}")
        if self.worker is not None and not self._no_dataset_session:
            total = max(len(self.active_episode_cache or []),
                        self._episodes_at_connect + self._session["saved"])
            count = tr("에피소드 {t}개 (이번 세션 +{s})").format(
                t=total, s=self._session["saved"])
        else:
            count = tr("저장 {s}").format(s=self._session["saved"])
        self.sb_right.setText(
            f"{self._fps_value:.0f} fps   |   {count}   |   {self.root_edit.text()}")

    def _refresh_stats(self) -> None:
        s = self._session
        elapsed = time.monotonic() - s["t0"]
        self.stats_labels["saved"].setText(str(s["saved"]))
        self.stats_labels["success"].setText(str(s["success"]))
        self.stats_labels["failed"].setText(str(s["failed"]))
        self.stats_labels["discarded"].setText(str(s["discarded"]))
        self.stats_labels["frames"].setText(str(s["frames"]))
        self.stats_labels["elapsed"].setText(f"{elapsed / 60:.1f} min")
        rate = s["saved"] / (elapsed / 60) if elapsed > 30 else 0.0
        self.stats_labels["rate"].setText(f"{rate:.2f}")
        try:
            usage = shutil.disk_usage(self.root_edit.text().strip() or str(Path.home()))
            self.disk_label.setText(f"{usage.free / 1e9:.1f} GB / {usage.total / 1e9:.0f} GB")
        except OSError:
            self.disk_label.setText("-")

    def _update_dataset_panel(self, path: "Path | None" = None) -> None:
        """Fills the right panel's Dataset box.

        During a session it describes what this session is writing (from the
        config, since the file's own attrs only exist after the first save).
        Otherwise it describes whichever file is selected in the tree, read
        straight off disk -- so 'what format is this old file?' is answerable
        without opening the schema dialog or connecting anything.
        """
        f = self.right_fields
        if self.worker is not None:
            cfg = self.worker.cfg
            name = (tr("(기록 안 함)") if self._no_dataset_session
                    else Path(str(self.active_file_path or "-")).name)
            f["ds_file"].setText(soft_wrap(name))
            f["ds_file"].setToolTip(name)
            task_text = cfg.language_instruction or cfg.task_name
            f["ds_task"].setText(task_text)
            f["ds_task"].setToolTip(task_text)
            # 저장은 백그라운드라 episode_list_changed가 몇 초 늦게 온다. 그걸
            # 기다리면 방금 저장한 것이 한동안 안 세어져 "지금 몇 개째인지"를
            # 알 수 없다. 연결 시점 개수 + 이번 세션 저장 수로 즉시 계산하고,
            # 목록이 도착하면 그 값이 더 정확하므로 그쪽을 쓴다.
            listed = len(self.active_episode_cache or [])
            counted = self._episodes_at_connect + self._session["saved"]
            total = max(listed, counted)
            f["ds_episodes"].setText(
                tr("{t}개  (이번 세션 +{s})").format(t=total, s=self._session["saved"]))
            f["ds_action"].setText(cfg.schema.action_space)
            f["ds_gripper"].setText(
                "0/1 (obs와 동일)" if cfg.schema.gripper_action_match_obs else "-1/+1")
            f["ds_image"].setText(f"{cfg.schema.image_size}²" if cfg.schema.image_size
                                  else tr("원본 해상도"))
            f["ds_fps"].setText(str(cfg.fps))
            f["ds_repack"].setText("-")
            return

        if path is None or not Path(path).exists():
            for k in ("ds_file", "ds_task", "ds_episodes", "ds_action",
                      "ds_gripper", "ds_image", "ds_fps", "ds_repack"):
                f[k].setText("-")
            return

        path = Path(path)
        st = hdf5_repack_status(path)
        f["ds_file"].setText(soft_wrap(path.name))
        f["ds_file"].setToolTip(str(path))
        f["ds_episodes"].setText(f"{st['episodes']}  ({st['size'] / 1e6:.0f} MB)")
        f["ds_repack"].setText(
            tr("혼합 — 다시 필요") if st["mixed"]
            else (st["marker"] or (tr("완료") if st["repacked"] else tr("안 됨"))))
        task = action = gripper = image = "-"
        try:
            with h5py.File(path, "r") as h:
                data = h["data"]
                info = data.attrs.get("problem_info")
                if info:
                    try:
                        task = json.loads(json.loads(info)["language_instruction"])
                    except Exception:  # noqa: BLE001
                        task = str(info)[:60]
                names = sorted(data.keys(), key=lambda s: int(s.split("_")[1]))
                if names:
                    g = data[names[0]]
                    action = str(g.attrs.get("action_space", "-"))
                    conv = str(g.attrs.get("gripper_action_convention", ""))
                    gripper = {"01": "0/1 (obs와 동일)", "pm1": "-1/+1"}.get(conv, conv or "-")
                    rgb = g.get("obs", {}).get("agentview_rgb")
                    if rgb is not None and rgb.ndim == 4:
                        image = f"{rgb.shape[1]}×{rgb.shape[2]}"
        except Exception as e:  # noqa: BLE001
            task = f"({type(e).__name__})"
        f["ds_task"].setText(task)
        f["ds_task"].setToolTip(task)
        f["ds_action"].setText(action)
        f["ds_gripper"].setText(gripper)
        f["ds_image"].setText(image)
        f["ds_fps"].setText("-")

    # --------------------------------------------------------------- 분석
    def _refresh_analysis(self, force: bool = False) -> None:
        """Rescans every .hdf5's actions. Only a few KB per episode, so this is
        rebuilt from disk rather than cached -- a cache would go stale the
        moment a session records another take."""
        root = self.root_edit.text().strip()
        files = hdf5_files(root)
        if not files:
            self.analysis_summary.setText(tr("{r} 에 *_demo.hdf5 가 없습니다.").format(r=root))
            return
        t0 = time.monotonic()
        self._stats = scan_dataset(files)
        self._summary = summarize(self._stats)
        dt = time.monotonic() - t0
        s = self._summary
        self.analysis_summary.setText(
            tr("에피소드 {n}개 · {f:,}프레임 · task {t}개 · 길이 {a}~{b}프레임\n{v}").format(
                n=s["n"], f=s["frames"], t=s["tasks"],
                a=s["len_min"], b=s["len_max"], v=s["verdict"]))
        self.log(f"[분석] {len(files)}개 파일 / {s['n']}개 에피소드 ({dt:.2f}s) — {s['verdict']}")

        self.dim_bars.set_rows(
            [(f"joint{i + 1}", float(s["per_dim_sigma"][i]), "") for i in range(7)])
        means = [e.mean_da for e in self._stats]
        self.da_hist.set_values(means, [(s["p50"], tr("중앙값")), (s["p99"], "p99")])

        lens = [e.seconds for e in self._stats]
        self.len_min_spin.blockSignals(True)
        self.len_max_spin.blockSignals(True)
        self.len_min_spin.setRange(0, int(max(lens) * 10) + 5)
        self.len_max_spin.setRange(0, int(max(lens) * 10) + 5)
        self.len_min_spin.setValue(0)
        self.len_max_spin.setValue(int(max(lens) * 10) + 5)
        self.len_min_spin.blockSignals(False)
        self.len_max_spin.blockSignals(False)
        self._refresh_rank_list()

    def _filtered_stats(self) -> list:
        lo = self.len_min_spin.value() / 10.0
        hi = self.len_max_spin.value() / 10.0
        if lo > hi:
            lo, hi = hi, lo
        self.len_label.setText(f"{lo:.1f}~{hi:.1f}s")
        # 선택 출처는 Dataset 트리 하나뿐이다. 파일 행을 고르면 그 파일만,
        # 에피소드 행을 고르면 그 부모 파일만 남긴다.
        path = None
        sel = self.dataset_tree.selectedItems() if hasattr(self, "dataset_tree") else []
        if sel:
            node = sel[0] if sel[0].parent() is None else sel[0].parent()
            v = node.data(0, Qt.ItemDataRole.UserRole)
            path = v if isinstance(v, str) and v.endswith(".hdf5") else None
        out = [e for e in self._stats if lo <= e.seconds <= hi]
        return [e for e in out if path is None or e.path == path]

    def _refresh_rank_list(self) -> None:
        if not self._stats:
            return
        key = self.rank_combo.currentData()
        rows = self._filtered_stats()
        score, fmt = {
            "z": (lambda e: -e.z_in_task, lambda e: f"z {e.z_in_task:+.2f}"),
            "spikes": (lambda e: -e.spikes, lambda e: f"{e.spikes}회"),
            "mean": (lambda e: -e.mean_da, lambda e: f"{e.mean_da:.5f}"),
            "short": (lambda e: e.n_frames, lambda e: f"{e.seconds:.1f}s"),
            "still": (lambda e: e.mean_da, lambda e: f"{e.mean_da:.5f}"),
        }[key]
        rows = sorted(rows, key=score)[:60]
        self.rank_tree.clear()
        for e in rows:
            item = QTreeWidgetItem([
                f"{Path(e.path).stem[:22]} · {e.demo}", fmt(e),
                f"{e.seconds:.1f}s", str(e.spikes), e.task[:34]])
            item.setData(0, Qt.ItemDataRole.UserRole, (e.path, e.demo))
            if e.success is False:
                item.setForeground(0, Qt.GlobalColor.red)
            self.rank_tree.addTopLevelItem(item)
        self.stats_hint.setText(
            tr("{n}개 중 상위 {m}개 표시").format(n=len(self._filtered_stats()), m=len(rows)))

    def _on_rank_selected(self) -> None:
        """Selecting a row draws its curves -- the point of the panel is that a
        number never decides on its own whether a take is bad."""
        items = self.rank_tree.selectedItems()
        if not items:
            return
        path, demo = items[0].data(0, Qt.ItemDataRole.UserRole)
        self._show_analysis_for(path, demo)

    def _show_analysis_for(self, path: str, demo: str) -> None:
        """Dataset 트리와 순위표가 공유하는 곡선 표시 경로."""
        if not path or not demo:
            return
        try:
            series = load_series(path, demo)
        except Exception as e:  # noqa: BLE001
            self.log(f"[분석] 시계열 로드 실패: {type(e).__name__}: {e}")
            return
        for plot, dims in self.series_plots.values():
            plot.set_data(series, dims)
            plot.set_cursor(None)
        stat = next((e for e in self._stats if e.key == (path, demo)), None)
        if stat is not None:
            self.analysis_summary.setText(
                tr("{d} · {n}프레임 ({s:.1f}s) · 평균 |Δa| {m:.5f} · task 내 z {z:+.2f} "
                   "· 스파이크 {k}회\n{t}").format(
                       d=demo, n=stat.n_frames, s=stat.seconds, m=stat.mean_da,
                       z=stat.z_in_task, k=stat.spikes, t=stat.task))
            self.da_hist.set_values(
                [e.mean_da for e in self._stats],
                [(self._summary["p50"], tr("중앙값")), (stat.mean_da, tr("이 에피소드"))])

    def _on_rank_play(self) -> None:
        items = self.rank_tree.selectedItems()
        if not items:
            return
        path, demo = items[0].data(0, Qt.ItemDataRole.UserRole)
        self._play_episode(path, demo)

    def _on_rank_delete(self) -> None:
        """Hands the selection to the same delete path the Dataset panel uses --
        including its session-ownership and busy checks."""
        picks = [i.data(0, Qt.ItemDataRole.UserRole) for i in self.rank_tree.selectedItems()]
        if not picks:
            QMessageBox.information(self, tr("선택 필요"),
                                    tr("삭제할 에피소드를 선택하세요 (Ctrl/Shift로 여러 개)."))
            return
        by_file: dict = {}
        for path, demo in picks:
            by_file.setdefault(Path(path), []).append(demo)
        if self._delete_episodes(by_file):
            self._refresh_analysis()

    # ------------------------------------------------------------ dataset
    def _refresh_dataset_tree(self) -> None:
        self.dataset_tree.clear()
        root = Path(self.root_edit.text().strip()).expanduser()
        if not root.is_dir():
            return
        for path in sorted(root.glob("*_demo.hdf5")):
            item = QTreeWidgetItem([path.name, "", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.dataset_tree.addTopLevelItem(item)
            if self.active_file_path is not None and path == self.active_file_path:
                if self.active_episode_cache is None:
                    item.setText(1, tr("불러오는 중..."))
                    continue
                episodes = self.active_episode_cache
            else:
                try:
                    with h5py.File(path, "r") as f:
                        data = f["data"]
                        episodes = [{"name": n,
                                     "num_samples": int(data[n].attrs.get("num_samples", 0)),
                                     "success": (None if data[n].attrs.get("success") is None
                                                 else bool(data[n].attrs.get("success")))}
                                    for n in data]
                        episodes.sort(key=lambda d: int(d["name"].split("_")[1]))
                except OSError as e:
                    item.setText(1, f"({e})")
                    continue
            for ep in episodes:
                res = "-" if ep["success"] is None else (tr("성공") if ep["success"] else tr("실패"))
                child = QTreeWidgetItem(["  " + ep["name"], str(ep["num_samples"]), res])
                child.setData(0, Qt.ItemDataRole.UserRole, ep["name"])
                item.addChild(child)
            item.setText(1, tr("{n}개").format(n=len(episodes)))
        self.dataset_tree.expandAll()
        self._update_dataset_panel(self._selected_file())

    def _selected_file(self) -> Path | None:
        items = self.dataset_tree.selectedItems()
        if not items:
            return None
        node = items[0] if items[0].parent() is None else items[0].parent()
        p = node.data(0, Qt.ItemDataRole.UserRole)
        return Path(p) if isinstance(p, str) else None

    def _busy_reason(self) -> str:
        """Anything that may currently hold an .hdf5 open, by name."""
        for proc, label in ((self.repack_process, tr("재압축")),
                            (self.convert_process, tr("LeRobot 변환")),
                            (self.upload_process, tr("HDF5 업로드"))):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                return label
        return ""

    def _on_delete_selected(self) -> None:
        """Deletes the selected episode.

        Two paths, because who owns the file decides who may touch it. h5py is
        not thread-safe, so while a session has the file open, every
        file-touching call goes through that session's saver thread -- deleting
        behind its back would corrupt the file it is still writing into. When
        no session owns the file, nothing else has it open and this window can
        do it directly, which is the common case: curating yesterday's takes
        should not require connecting a robot first.
        """
        # 파일별로 묶는다. 여러 개를 지울 때 이름 하나씩 지우고 매번 번호를 다시
        # 매기면 두 번째부터는 이미 밀린 이름을 지우게 된다 -- 한 파일 안에서
        # 전부 지운 뒤 renumber는 마지막에 한 번만.
        by_file: dict = {}
        for item in self.dataset_tree.selectedItems():
            if item.parent() is None:
                continue
            p = item.parent().data(0, Qt.ItemDataRole.UserRole)
            by_file.setdefault(Path(p), []).append(item.data(0, Qt.ItemDataRole.UserRole))
        if not by_file:
            QMessageBox.information(self, tr("선택 필요"),
                                    tr("삭제할 에피소드를 선택하세요 (Ctrl/Shift로 여러 개)."))
            return
        if self._delete_episodes(by_file):
            self._refresh_dataset_tree()

    def _delete_episodes(self, by_file: dict) -> bool:
        """공용 삭제 경로. Dataset 패널과 Analysis 순위표가 같은 것을 쓴다 --
        세션 소유 검사와 실행 중 작업 검사를 두 벌로 두면 반드시 갈라진다."""
        busy = self._busy_reason()
        if busy:
            QMessageBox.warning(self, tr("삭제 불가"),
                                tr("{job}이(가) 진행 중입니다. 끝난 뒤 삭제하세요.").format(job=busy))
            return False

        total = sum(len(v) for v in by_file.values())
        detail = "\n".join(f"  {p.name}: {len(v)}개" for p, v in by_file.items())
        if QMessageBox.question(
                self, tr("에피소드 삭제"),
                tr("에피소드 {n}개를 삭제합니다.\n\n{d}\n\n남은 에피소드는 번호가 다시 "
                   "매겨집니다. 파일 크기는 줄지 않습니다 (재압축 필요).").format(
                       n=total, d=detail)
        ) != QMessageBox.StandardButton.Yes:
            return False

        for path, names in by_file.items():
            owned = self.active_file_path is not None and path == self.active_file_path
            if owned:
                # 세션이 파일을 쥐고 있으면 saver 스레드가 유일한 통로다. 매 삭제
                # 뒤 번호가 다시 매겨지므로 뒤에서부터 지워야 앞 이름이 안 밀린다.
                for name in sorted(names, key=lambda s: int(s.split("_")[1]), reverse=True):
                    self.worker.cmd_delete_episode(name)
                self.log(f"[삭제] {path.name}: {len(names)}개 요청 (세션 경유)")
                continue
            try:
                with h5py.File(path, "a") as f:
                    data = f["data"]
                    missing = [n for n in names if n not in data]
                    if missing:
                        raise KeyError(", ".join(missing))
                    for name in names:
                        del data[name]
                    renumber_episodes(data)
                self.log(f"[삭제] {path.name}: {len(names)}개 ({', '.join(sorted(names))})")
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, tr("삭제 실패"), f"{path.name}\n{type(e).__name__}: {e}")
                self.log(f"[삭제 실패] {path.name}: {type(e).__name__}: {e}")
        return True

    def _on_select_jerky(self) -> None:
        """Selects the episodes that stand out *within their own task*.

        z >= 2 rather than a fixed |Δa|: measured over 273 episodes the spread
        is narrow (p99/p50 = 1.67) and the global ranking mostly reflects how
        fast a task is, not how bad a take is -- a global cut would have
        selected one task wholesale. Two sigma inside a task is a claim that
        survives a task being naturally quick or slow.

        Nothing is deleted here. The selection lands in the same tree the
        operator deletes from, so they can play the takes first.
        """
        if not self._stats:
            self._refresh_analysis()
        if not self._stats:
            return
        flagged = {(e.path, e.demo) for e in self._stats if e.z_in_task >= JERKY_Z}
        self.dataset_tree.clearSelection()
        n = 0
        for i in range(self.dataset_tree.topLevelItemCount()):
            parent = self.dataset_tree.topLevelItem(i)
            path = parent.data(0, Qt.ItemDataRole.UserRole)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if (path, child.data(0, Qt.ItemDataRole.UserRole)) in flagged:
                    child.setSelected(True)
                    n += 1
        self.log(f"[큐레이션] task 내 z ≥ {JERKY_Z} 인 에피소드 {n}개를 선택했습니다."
                 + ("" if n else " (없음)"))
        self.dataset_hint.setText(
            tr("튀는 에피소드 {n}개 선택됨 — 재생으로 확인한 뒤 '에피소드 삭제'로 지웁니다.")
            .format(n=n) if n else
            tr("task 내 z ≥ {z} 인 에피소드가 없습니다 (이 데이터셋은 균일합니다).")
            .format(z=JERKY_Z))

    def _on_select_failed(self) -> None:
        """Selects every episode marked failed, across all files.

        This is the other half of marking-instead-of-discarding: failures pile
        up during collection on purpose, and curation is where they go. Without
        this the operator would ctrl-click them one at a time down a tree of a
        hundred rows.
        """
        self.dataset_tree.clearSelection()
        n = 0
        for i in range(self.dataset_tree.topLevelItemCount()):
            parent = self.dataset_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.text(2) == tr("실패"):
                    child.setSelected(True)
                    n += 1
        self.log(f"[큐레이션] 실패로 표시된 에피소드 {n}개를 선택했습니다."
                 + ("" if n else " (없음)"))
        self.dataset_hint.setText(
            tr("실패 {n}개 선택됨 — '에피소드 삭제'로 한 번에 지웁니다.").format(n=n)
            if n else tr("실패로 표시된 에피소드가 없습니다."))

    def _on_delete_file(self) -> None:
        """Deletes a whole <task>_demo.hdf5. Never offered for the file a
        session is writing into -- that one is closed by ending the session."""
        path = self._selected_file()
        if path is None:
            QMessageBox.information(self, tr("선택 필요"), tr("삭제할 파일을 선택하세요."))
            return
        if self.active_file_path is not None and path == self.active_file_path:
            QMessageBox.warning(self, tr("삭제 불가"),
                                tr("지금 수집 중인 파일입니다. 먼저 세션을 종료하세요."))
            return
        busy = self._busy_reason()
        if busy:
            QMessageBox.warning(self, tr("삭제 불가"),
                                tr("{job}이(가) 진행 중입니다. 끝난 뒤 삭제하세요.").format(job=busy))
            return
        st = hdf5_repack_status(path)
        # 진짜 삭제한다. 오클릭 대책은 되돌리기가 아니라 닿기 어렵게 두는 것
        # (이 항목은 Dataset 메뉴에만 있다) -- 반쯤 지워진 채 디스크만 차지하는
        # 휴지통은 결국 아무도 비우지 않는다.
        confirm = QMessageBox.warning(
            self, tr("파일 삭제"),
            tr("{f}\n\n에피소드 {n}개, {mb:.1f} MB 를 완전히 삭제합니다.\n"
               "되돌릴 수 없습니다. Hub에 올린 사본은 영향받지 않습니다.").format(
                   f=path.name, n=st["episodes"], mb=st["size"] / 1e6),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
            self.log(f"[파일 삭제] {path.name} ({st['episodes']}개 에피소드, "
                     f"{st['size'] / 1e6:.1f} MB)")
        except OSError as e:
            QMessageBox.critical(self, tr("삭제 실패"), str(e))
            self.log(f"[파일 삭제 실패] {path.name}: {e}")
        self._refresh_dataset_tree()

    def _on_show_structure(self) -> None:
        path = self._selected_file()
        if path is None:
            QMessageBox.information(self, tr("선택 필요"), tr("파일을 선택하세요."))
            return
        st = hdf5_repack_status(path)
        lines = [f"{path.name}",
                 f"  에피소드 {st['episodes']}개, {st['size'] / 1e6:.1f} MB",
                 f"  이미지 압축: {st['compression']} (혼합={st['mixed']})",
                 f"  재압축 이력: {st['marker'] or '-'}"]
        try:
            with h5py.File(path, "r") as f:
                names = sorted(f["data"], key=lambda s: int(s.split("_")[1]))
                if names:
                    lines.append("  " + describe_episode(f["data"][names[0]]).replace("\n", "\n  "))
        except Exception as e:  # noqa: BLE001
            lines.append(f"  (구조 읽기 실패: {e})")
        self.log("\n".join(lines), view="validation")
        self.bottom_tabs.setCurrentWidget(self.validation_view)

    # ----------------------------------------------------------- playback
    def _on_dataset_selection(self) -> None:
        items = self.dataset_tree.selectedItems()
        item = items[0] if items else None
        # 파일 행을 골라도 오른쪽 Dataset 칸은 갱신된다 -- 재생은 에피소드 행에서만.
        self._update_dataset_panel(self._selected_file())
        if self._stats:
            self._refresh_rank_list()
            if item is not None and item.parent() is not None:
                self._show_analysis_for(
                    item.parent().data(0, Qt.ItemDataRole.UserRole),
                    item.data(0, Qt.ItemDataRole.UserRole))
        if item is None or item.parent() is None:
            return
        path = item.parent().data(0, Qt.ItemDataRole.UserRole)
        demo = item.data(0, Qt.ItemDataRole.UserRole)
        if not path or not demo:
            return
        self._play_episode(path, demo)

    def _play_episode(self, path: str, demo: str) -> None:
        """Dataset 트리와 Analysis 순위표가 공유하는 재생 진입점."""
        if self._play_key == (path, demo):
            self.center_tabs.setCurrentIndex(1)
            return
        if self.active_file_path is not None and Path(path) == self.active_file_path:
            self.play_caption.setText(tr("수집 중인 파일은 재생할 수 없습니다."))
            return
        self._stop_playback()
        self._play_key = (path, demo)
        self.play_caption.setText(tr("불러오는 중... {d}").format(d=demo))
        self.center_tabs.setCurrentIndex(1)
        if self._play_loader is not None:
            self._play_loader.wait()
        self._play_loader = EpisodeLoadWorker(path, demo)
        self._play_loader.loaded.connect(self._on_episode_loaded)
        self._play_loader.failed.connect(
            lambda m: self.play_caption.setText(tr("재생 실패: {m}").format(m=m)))
        self._play_loader.start()

    @pyqtSlot(str, str, object, object)
    def _on_episode_loaded(self, path, demo, agent, wrist) -> None:
        if self._play_key != (path, demo):
            return
        self._play_frames = {"agent": agent, "wrist": wrist}
        n = len(agent) if agent is not None else len(wrist)
        self.play_slider.blockSignals(True)
        self.play_slider.setRange(0, max(0, n - 1))
        self.play_slider.setValue(0)
        self.play_slider.blockSignals(False)
        self.play_slider.setEnabled(True)
        self.play_btn.setEnabled(True)
        self.play_btn.setText(tr("일시정지"))
        self._apply_speed()
        self._refresh_play_caption()
        self._show_frame(0)
        self._play_timer.start()

    def _stop_playback(self) -> None:
        self._play_timer.stop()
        self._play_frames = {"agent": None, "wrist": None}
        self._play_key = None
        self.play_btn.setEnabled(False)
        self.play_btn.setText(tr("재생"))
        self.play_slider.setEnabled(False)
        self.play_pos.setText("-/-")
        for v in self.play_views.values():
            v.clear_frame(tr("에피소드를 선택하세요"))

    def _speed(self) -> float:
        data = self.speed_combo.currentData()
        return float(data) if data else 1.0

    def _apply_speed(self) -> None:
        interval = max(1, int(round(1000.0 / (PLAYBACK_FPS * self._speed()))))
        self._play_timer.setInterval(interval)

    def _on_speed_changed(self) -> None:
        self._apply_speed()
        self._refresh_play_caption()

    def _refresh_play_caption(self) -> None:
        if not self._play_key:
            return
        path, demo = self._play_key
        n = self.play_slider.maximum() + 1
        speed = self._speed()
        eff = PLAYBACK_FPS * speed
        self.play_caption.setText(
            f"{Path(path).name} · {demo} · {n} frames · "
            + (tr("{s:g}배속 ({f:g} fps)").format(s=speed, f=eff) if speed != 1
               else tr("{f:g} fps (실제 속도)").format(f=eff)))

    def _on_play_toggle(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self.play_btn.setText(tr("재생"))
        else:
            self._play_timer.start()
            self.play_btn.setText(tr("일시정지"))

    def _on_play_tick(self) -> None:
        n = self.play_slider.maximum() + 1
        if n > 1:
            self.play_slider.setValue((self.play_slider.value() + 1) % n)

    def _show_frame(self, i: int) -> None:
        for key, view in self.play_views.items():
            frames = self._play_frames.get(key)
            if frames is not None and i < len(frames):
                view.set_frame(frames[i])
        self.play_pos.setText(f"{i + 1}/{self.play_slider.maximum() + 1}")

    # ---------------------------------------------------------- 시스템 튜닝
    @staticmethod
    def check_tuning() -> list:
        """What scripts/runme.sh would change, read without touching anything.

        Both settings reset on reboot and on replugging the GELLO, and both
        are invisible until they bite: a 16 ms FTDI latency timer drops the
        Dynamixel sync-read from ~340 Hz to ~55 Hz, and the powersave governor
        produces the latency spikes that end an FR3 session with
        communication_constraints_violation.

        Checking here rather than just running the script means the pkexec
        password prompt only ever appears when something actually needs
        changing -- a prompt on every launch trains people to dismiss it.
        """
        issues = []
        ports = sorted(Path("/dev/serial/by-id").glob("*FTDI*")) \
            if Path("/dev/serial/by-id").is_dir() else []
        if not ports:
            issues.append(("gello", "GELLO(FTDI)를 찾지 못했습니다 -- USB 연결 확인"))
        else:
            tty = Path(os.path.realpath(ports[0])).name
            lat = Path(f"/sys/bus/usb-serial/devices/{tty}/latency_timer")
            try:
                value = lat.read_text().strip()
                if value != "1":
                    issues.append(("latency",
                                   f"FTDI latency_timer={value} (1이어야 함, {tty})"))
            except OSError:
                issues.append(("latency", f"{lat} 를 읽을 수 없습니다"))
        govs = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"))
        if govs:
            perf = sum(1 for g in govs if _read(g) == "performance")
            if perf != len(govs):
                issues.append(("governor",
                               f"CPU governor performance {perf}/{len(govs)} 코어"))
        return issues

    def _startup_tuning(self) -> None:
        issues = self.check_tuning()
        if not issues:
            self.log("[튜닝] FTDI latency_timer=1, CPU governor=performance — 이미 적용됨.")
            return
        self.log("[튜닝] 조정이 필요합니다:")
        for _key, text in issues:
            self.log(f"  - {text}")
        if any(k == "gello" for k, _ in issues):
            # 케이블 문제는 pkexec로 해결되지 않는다. 스크립트를 띄워봐야
            # 비밀번호만 묻고 같은 경고를 낼 뿐이다.
            self.log("[튜닝] GELLO가 연결되면 Tools > 시스템 튜닝 실행 을 눌러주세요.")
            return
        self.log("[튜닝] scripts/runme.sh 를 실행합니다 (관리자 비밀번호 창이 뜹니다).")
        self._run_runme()

    # ------------------------------------------------------------ 전체 처리
    def _on_pipeline(self) -> None:
        if self.worker is not None:
            QMessageBox.warning(self, tr("수집 중"),
                                tr("수집 중에는 실행할 수 없습니다. 먼저 세션을 종료하세요."))
            return
        if self._pipeline_steps:
            QMessageBox.information(self, tr("이미 실행 중"),
                                    tr("전체 처리가 이미 진행 중입니다. 로그를 확인하세요."))
            return
        data_root = self.root_edit.text().strip()
        repo = self._recents.most_recent("repo_id", "")
        self.log("[전체 처리] Hub 상태를 확인하는 중...", "upload")
        self.bottom_tabs.setCurrentWidget(self.upload_view)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = plan_sync(data_root, repo) if repo else {
                "action": "blocked", "error": "LeRobot Repo ID가 없습니다 (먼저 한 번 지정하세요)",
                "rows": [], "added": 0, "shrunk": 0, "ambiguous": [],
                "local_total": 0, "hub_total": 0,
                "paths": sorted(str(p) for p in Path(data_root).glob("*_demo.hdf5"))}
        finally:
            QApplication.restoreOverrideCursor()
        dlg = PipelineDialog(self, data_root, plan, repo,
                             self._recents.most_recent("hdf5_repo_id", ""),
                             self._recents.most_recent(
                                 "lerobot_root", str(Path.home() / "lerobot_upload")))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.log("[전체 처리] 취소했습니다.", "upload")
            return
        steps = dlg.steps()
        if not steps:
            self.log("[전체 처리] 할 일이 없습니다.", "upload")
            return
        self._pipeline_steps = steps
        self._pipeline_results = []
        self._pipeline_t0 = time.monotonic()
        self.log(f"[전체 처리] {len(steps)}단계 시작 — "
                 + " → ".join(s["name"] for s in steps), "upload")
        self._run_next_pipeline_step()

    def _run_next_pipeline_step(self) -> None:
        if not self._pipeline_steps:
            self._finish_pipeline(True)
            return
        step = self._pipeline_steps[0]
        if step.get("clear_root"):
            # 이어붙이기는 로컬 메타가 있으면 그걸 기준으로 삼는다. 비워야 Hub의
            # 현재 상태를 받아오고, 재빌드는 애초에 빈 폴더가 필요하다.
            root = Path(step["clear_root"])
            if root.exists():
                try:
                    shutil.rmtree(root)
                    self.log(f"[전체 처리] 로컬 변환 폴더를 비웠습니다: {root}", "upload")
                except OSError as e:
                    self.log(f"[전체 처리] 폴더를 비우지 못했습니다: {e}", "upload")
                    self._finish_pipeline(False)
                    return
        proc = QProcess(self)
        proc.setProgram(step["program"])
        proc.setArguments(step["args"])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        prefix = f"[{step['name']}]"
        proc.readyReadStandardOutput.connect(lambda: self._pipe(proc, prefix, "upload"))
        proc.finished.connect(self._on_pipeline_step_finished)
        self._pipeline_proc = proc
        self._pipeline_step_t0 = time.monotonic()
        self.log(f"\n[전체 처리] ▶ {step['name']} 시작", "upload")
        self.statusBar().showMessage(tr("전체 처리: {n}").format(n=step["name"]))
        proc.start()

    def _on_pipeline_step_finished(self, code: int, _status) -> None:
        step = self._pipeline_steps.pop(0)
        dt = time.monotonic() - self._pipeline_step_t0
        self._pipeline_results.append((step["name"], code, dt))
        self.log(f"[전체 처리] {'✔' if code == 0 else '✖'} {step['name']} "
                 f"종료 (exit={code}, {dt / 60:.1f}분)", "upload")
        self._pipeline_proc = None
        if code != 0:
            # 뒤 단계가 앞 결과에 의존하므로(변환 -> 업로드) 잘못된 것을 올리지
            # 않는다. 아침에 로그만 보면 어디서 멈췄는지 알 수 있게 남긴다.
            self._finish_pipeline(False)
            return
        self._run_next_pipeline_step()

    def _finish_pipeline(self, ok: bool) -> None:
        remaining = [s["name"] for s in self._pipeline_steps]
        self._pipeline_steps = []
        total = time.monotonic() - self._pipeline_t0
        lines = ["", "=" * 56,
                 tr("전체 처리 요약 — {r} (총 {m:.1f}분)").format(
                     r=tr("완료") if ok else tr("중단됨"), m=total / 60)]
        for name, code, dt in self._pipeline_results:
            lines.append(f"  {'✔' if code == 0 else '✖'} {name:24s} "
                         f"exit={code}  {dt / 60:.1f}분")
        for name in remaining:
            lines.append(f"  · {name:24s} " + tr("실행 안 함"))
        lines.append("=" * 56)
        for ln in lines:
            self.log(ln, "upload")
        self.statusBar().showMessage(
            tr("전체 처리 완료") if ok else tr("전체 처리 중단 — 로그 확인"), 0)
        if not ok:
            self._alert(tr("전체 처리 중단"),
                        tr("한 단계가 실패해 이후 단계를 실행하지 않았습니다.\n"
                           "Upload 탭과 로그 파일에 자세한 내용이 있습니다."))

    def _on_hf_accounts(self) -> None:
        dlg = HfAccountDialog(self)
        dlg.exec()
        text, color = hf_account()
        self.hf_label.setText(text)
        self.hf_label.setStyleSheet(f"color:{color}; font-weight:bold;")
        if dlg.switched_to():
            self.log(f"[HF] 이제 {dlg.switched_to()} 계정으로 업로드합니다.")

    def _on_check_cameras(self) -> None:
        """Runs scripts/check_cameras.py into the Validation tab.

        No --stream: the previews (or a session) usually hold the cameras, and
        the link-speed half is exactly the part that stays readable anyway.
        """
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([CHECK_CAMERAS])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: [self.log(ln, "validation") for ln in
                     bytes(proc.readAllStandardOutput()).decode(errors="replace").splitlines()])
        proc.finished.connect(lambda c, _s: self.log(
            {0: "카메라 점검: 모두 정상", 1: "카메라 점검: 문제 발견",
             2: "카메라 점검: 일부 확인 못 함"}.get(c, f"카메라 점검 종료 (exit={c})"),
            "validation"))
        self._camera_check_process = proc
        self.bottom_tabs.setCurrentWidget(self.validation_view)
        self.log("=== 카메라 점검 ===", "validation")
        proc.start()

    def _run_runme(self) -> None:
        if self.runme_process is not None and \
                self.runme_process.state() != QProcess.ProcessState.NotRunning:
            self.log("[튜닝] 이미 실행 중입니다.")
            return
        proc = QProcess(self)
        proc.setProgram("/usr/bin/env")
        proc.setArguments(["bash", RUNME_SCRIPT])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: [self.log(f"[튜닝] {ln}") for ln in
                     bytes(proc.readAllStandardOutput()).decode(errors="replace").splitlines()
                     if ln.strip()])
        proc.finished.connect(self._on_runme_finished)
        self.runme_process = proc
        proc.start()

    def _on_runme_finished(self, code: int, _status) -> None:
        left = self.check_tuning()
        if code == 0 and not left:
            self.log("[튜닝] 완료 — 모두 적용되었습니다.")
        else:
            self.log(f"[튜닝] 종료 (exit={code}). 남은 항목: "
                     + (", ".join(t for _k, t in left) if left else "없음"))
            if left:
                self.log("[튜닝] 취소했거나 실패했습니다. Tools > 시스템 튜닝 실행 으로 다시 할 수 있습니다.")
        self.runme_process = None

    # ------------------------------------------------------------- node
    def _on_start_node(self) -> None:
        if self.node_process is not None and \
                self.node_process.state() != QProcess.ProcessState.NotRunning:
            self.log("[노드] 이미 실행 중입니다.")
            return
        proc = QProcess(self)
        proc.setProgram(PYLIBFRANKA_PYTHON)
        # --die-with-parent: closeEvent가 노드를 정리하지만 그건 정상 종료일
        # 때뿐이다. GUI가 갑자기 죽으면 노드가 FCI 연결을 쥔 채 남아 다음 실행이
        # 노드를 못 띄운다. 커널이 대신 정리하게 한다.
        proc.setArguments([LAUNCH_NODES_SCRIPT, "--robot", "fr3", "--die-with-parent"])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_node_output)
        proc.finished.connect(lambda c, _s: self.log(f"[노드] 종료 (exit={c})"))
        self.node_process = proc
        self.log("[노드] 시작합니다...")
        proc.start()

    def _on_node_output(self) -> None:
        if self.node_process is None:
            return
        data = bytes(self.node_process.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.log(f"[노드] {line}")

    def _on_stop_node(self) -> None:
        if self.node_process is None or \
                self.node_process.state() == QProcess.ProcessState.NotRunning:
            return
        self.node_process.terminate()
        if not self.node_process.waitForFinished(3000):
            self.node_process.kill()
            self.node_process.waitForFinished(2000)
        self.log("[노드] 종료했습니다.")

    # ----------------------------------------------------------- upload
    def _hdf5_candidates(self) -> list:
        root = Path(self.root_edit.text().strip() or str(Path.home()))
        return [str(p) for p in sorted(root.glob("*_demo.hdf5"))]

    def _on_repack(self) -> None:
        if self.worker is not None:
            QMessageBox.warning(self, tr("수집 중"),
                                tr("수집 중에는 재압축할 수 없습니다. 먼저 세션을 종료하세요."))
            return
        paths = self._hdf5_candidates()
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"), tr("재압축할 .hdf5 파일이 없습니다."))
            return
        dlg = RepackDialog(self, paths)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dlg.selected()
        if not selected:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([REPACK_SCRIPT, *selected])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self._pipe(proc, "[재압축]", "upload"))
        proc.finished.connect(lambda c, _s: (self.log(f"[재압축] 종료 (exit={c})", "upload"),
                                             self._refresh_dataset_tree()))
        self.repack_process = proc
        self.bottom_tabs.setCurrentWidget(self.upload_view)
        self.log(f"[재압축] 시작: {len(selected)}개 파일", "upload")
        proc.start()

    def _pipe(self, proc: QProcess, prefix: str, view: str) -> None:
        data = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        state = self._stream_states.setdefault(prefix, {})
        # 진행률은 1초마다 받아 한 줄을 덮어쓴다. 3초로 줄여도 1.3GB 업로드가
        # 몇 분이면 수십 줄이 쌓여, 그 사이 지나간 다른 로그를 밀어낸다.
        for line in clean_stream_lines(data, state, every_s=1.0):
            if is_progress_line(line):
                self._log_progress(f"{prefix} {line}", view)
            else:
                self.log(f"{prefix} {line}", view)

    def _on_lerobot(self) -> None:
        dlg = LerobotConvertDialog(self, self.root_edit.text().strip())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        args = dlg.build_args()
        if args is None:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([CONVERT_SCRIPT, *args])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._pipe(proc, "[LeRobot]", "upload"))
        proc.finished.connect(lambda c, _s: self.log(
            f"[LeRobot] 종료 (exit={c})" + ("" if c == 0 else " -- 실패, 위 로그를 확인하세요"),
            "upload"))
        self.convert_process = proc
        self.bottom_tabs.setCurrentWidget(self.upload_view)
        self.log(f"[LeRobot] 시작: {' '.join(args)}", "upload")
        proc.start()

    def _on_hdf5_upload(self) -> None:
        # 두 번째 인자는 '찾아보기'가 열릴 폴더다. 파일이 아니다.
        dlg = HdfUploadDialog(self, self.root_edit.text().strip())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        args = dlg.build_args()
        if args is None:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([UPLOAD_SCRIPT, *args])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._pipe(proc, "[HDF5 업로드]", "upload"))
        proc.finished.connect(lambda c, _s: self.log(
            f"[HDF5 업로드] 종료 (exit={c})" + ("" if c == 0 else " -- 실패, 위 로그를 확인하세요"),
            "upload"))
        self.upload_process = proc
        self.bottom_tabs.setCurrentWidget(self.upload_view)
        self.log(f"[HDF5 업로드] 시작: {' '.join(args)}", "upload")
        proc.start()

    # --------------------------------------------------------- shortcuts
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        """One-handed shortcuts for solo collection: both hands are on the
        GELLO leader, not the mouse. The same key means different things per
        state, mirroring the button that is live at that moment:

            gate       + Space            -> 텔레옵 시작
            recording  + Space            -> 저장 (성공)
            recording  + Esc              -> 저장 (실패)
            recording  + Delete/Backspace -> 폐기
            reset_wait + Enter/Return     -> 리셋 대기 건너뛰기

        Installed app-wide rather than on this window, so it fires no matter
        which widget has focus -- the operator is not managing GUI focus while
        teleoperating. Skipped while a modal dialog is open so Esc still
        closes dialogs normally.
        """
        if (
            event.type() == QEvent.Type.KeyPress
            and self.worker is not None
            and QApplication.activeModalWidget() is None
        ):
            key = event.key()
            state = self._current_state
            if key == Qt.Key.Key_Space:
                if state == "gate":
                    self._cmd("cmd_start_teleop")
                    return True
                if state == "recording" and not self._no_dataset_session:
                    self._save(True)
                    return True
            elif key == Qt.Key.Key_Escape:
                # 기록 중이면 '실패로 끝내기', 리셋 대기 중이면 방금 것의 판정
                # 번복. 버튼을 누르는 행위 자체가 "이 에피소드는 끝났다"는
                # 판단이므로, 두 키 모두 에피소드를 끝낸다. 성공이었는지는
                # 팔이 홈으로 가는 동안 다시 보고 정하는 게 자연스럽다.
                if state == "recording" and not self._no_dataset_session:
                    self._save(False)
                    return True
                if state in ("reset_wait", "homing") and not self._no_dataset_session:
                    self._toggle_last_verdict()
                    return True
            elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if state == "recording":
                    self._cmd("cmd_discard_episode")
                    return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if state == "reset_wait":
                    self._cmd("cmd_skip_reset_wait")
                    return True
                if state == "gate":
                    # 자동 정렬 재시도. 시간 초과로 꺼진 뒤 손으로 대충 맞춰놓고
                    # 나머지를 다시 기계에 맡기는 흐름이 자연스럽다.
                    if self._gate_ok:
                        self._cmd("cmd_auto_match_pose")
                    else:
                        self.log("[자동정렬] 먼저 리더를 대략 맞춰주세요 "
                                 "(자동 정렬은 큰 오차에서 걸면 모터에 무리).")
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._play_timer.stop()
        if self._play_loader is not None:
            self._play_loader.wait(3000)
        self._stop_previews_blocking()
        if self.worker is not None and self.worker.isRunning():
            self.worker.cmd_quit()
            self.worker.wait(5000)
        self._on_stop_node()
        for proc in (self.repack_process, self.convert_process,
                     self.upload_process, self.runme_process,
                     self._pipeline_proc):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
                    proc.waitForFinished(2000)
        if self._log_file is not None:
            self._log_file.close()
        super().closeEvent(event)


def _install_excepthook(log_path: Path, window_ref: dict) -> None:
    """Logs unhandled exceptions instead of letting PyQt kill the process.

    PyQt calls qFatal() -- i.e. abort() -- when a Python exception escapes a
    slot, so the window vanishes with no message: the traceback goes to stderr,
    and stderr goes nowhere when the app is launched from a desktop icon. That
    is the 'GUI가 이유도 안 보여주고 꺼진다'. An installed excepthook takes
    precedence over that abort, so the app survives a non-fatal slot error and,
    either way, the traceback lands in the session log where it can be read
    afterwards.
    """
    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(log_path, "a", buffering=1) as f:
                f.write(f"\n[{time.strftime('%H:%M:%S')}] [예외] 처리되지 않은 오류\n{text}\n")
        except OSError:
            pass
        print(text, file=sys.stderr, flush=True)
        win = window_ref.get("win")
        if win is not None:
            try:
                win.log(f"[예외] {exc_type.__name__}: {exc} — 자세한 내용은 로그 파일에")
                win._alert(tr("내부 오류"),
                           tr("{t}: {e}\n\n작업은 계속할 수 있지만, 이 상태가 이상하면 "
                              "저장 후 다시 시작하세요.\n로그: {p}").format(
                                  t=exc_type.__name__, e=exc, p=log_path),
                           QMessageBox.Icon.Critical)
            except Exception:  # noqa: BLE001
                pass

    sys.excepthook = hook


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"workspace_{time.strftime('%Y%m%d_%H%M%S')}.log"
    window_ref: dict = {}
    _install_excepthook(log_path, window_ref)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = WorkspaceWindow(log_path)
    window_ref["win"] = win
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
