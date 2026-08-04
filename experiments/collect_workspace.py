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

import shutil
import sys
import time
from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import QEvent, QProcess, Qt, QThread, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QActionGroup, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
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
from gello.gui_widgets import (  # noqa: E402
    PLAYBACK_FPS,
    REPACK_SCRIPT,
    CameraPreviewWorker,
    DatasetSchemaDialog,
    DeltaBar,
    EpisodeLoadWorker,
    HdfUploadDialog,
    LerobotConvertDialog,
    Recents,
    RepackDialog,
    VideoView,
    clean_stream_lines,
    hf_account,
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
    "reset_wait": "Enter: 대기 건너뛰고 바로 진행",
    "gate": "Space: 텔레옵 시작   Enter: 자동 정렬 다시",
    "recording": "Space: 저장(성공)   Esc: 저장(실패)   Del: 폐기",
}


def mark_todo(widget: QWidget, note: str = "") -> QWidget:
    widget.setEnabled(False)
    widget.setStyleSheet(TODO_STYLE)
    widget.setToolTip(f"{TODO_MARK}: " + (note or tr("아직 구현되지 않은 기능입니다.")))
    return widget


def _dot(state: str, text: str) -> str:
    return f'<span style="color:{_DOT[state]};">●</span> {text}'


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
        self._convert_out_state: dict = {}
        self._upload_out_state: dict = {}

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
        play_col.addLayout(row)
        self.play_caption = QLabel(tr("Dataset 패널에서 에피소드를 고르면 여기서 재생됩니다."))
        self.play_caption.setStyleSheet("color:#888;")
        play_col.addWidget(self.play_caption)
        self.center_tabs.addTab(play, tr("Playback"))
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
        self.save_ng_btn = QPushButton(tr("저장 (실패)"))
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
        self.dataset_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.dataset_tree.itemSelectionChanged.connect(self._on_dataset_selection)
        col.addWidget(self.dataset_tree, 1)
        row = QHBoxLayout()
        for text, slot in ((tr("새로고침"), self._refresh_dataset_tree),
                           (tr("에피소드 삭제"), self._on_delete_selected),
                           (tr("파일 삭제"), self._on_delete_file),
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
        col.addStretch()
        return w

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
            ("Recording", (("recording", "기록"), ("episode", "에피소드"),
                           ("frames", "프레임"), ("file", "파일"))),
        ):
            box = QGroupBox(tr(title))
            form = QFormLayout(box)
            for key, label in keys:
                lab = QLabel("-")
                lab.setWordWrap(True)
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
        add("save", tr("✔ Save"), lambda: self._save(True), tr("성공으로 저장"))
        add("savefail", tr("✖ Save (fail)"), lambda: self._save(False))
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
        m.addAction(tr("에피소드 삭제"), self._on_delete_selected)
        m.addAction(tr("파일 삭제"), self._on_delete_file)
        m.addAction(tr("구조 확인..."), self._on_show_structure)
        m.addSeparator()
        m.addAction(tr("용량 최적화 (재압축)..."), self._on_repack)
        m.addAction(tr("LeRobot 변환/업로드..."), self._on_lerobot)
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
        m.addAction(tr("미리보기 중지"), self._stop_previews)

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
        m.addAction(tr("데이터셋 스키마..."), self._on_schema)
        m.addAction(tr("언어 전환"), self._toggle_language)

        m = mb.addMenu(tr("Help"))
        m.addAction(tr("단축키..."), lambda: QMessageBox.information(
            self, tr("단축키"),
            tr("양손이 GELLO 리더 위에 있으므로 마우스 없이 조작합니다.\n"
               "같은 키가 상태에 따라 다르게 동작합니다.\n\n"
               "  자세 정렬 중   Space        텔레옵 시작\n"
               "  기록 중        Space        저장 (성공)\n"
               "  기록 중        Esc          저장 (실패)\n"
               "  기록 중        Delete       폐기\n"
               "  자세 정렬 중   Enter        자동 정렬 다시 (대략 맞춘 뒤에만)\n"
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
        elif key == "dataset":
            self._refresh_dataset_tree()
        elif key == "upload":
            text, color = hf_account()
            self.hf_label.setText(text)
            self.hf_label.setStyleSheet(f"color:{color}; font-weight:bold;")

    # -------------------------------------------------------------- utils
    def log(self, msg: str, view: str = "log") -> None:
        target = {"log": self.log_view, "upload": self.upload_view,
                  "validation": self.validation_view}[view]
        target.appendPlainText(msg)
        if self._log_file is not None:
            self._log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def _cmd(self, name: str, *args) -> None:
        if self.worker is None:
            self.log("[제어] 아직 연결되지 않았습니다.")
            return
        getattr(self.worker, name)(*args)

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
        self._stop_previews()
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

    def _stop_previews(self, timeout_ms: int = 7000) -> list:
        """Stops both preview threads. Returns the roles that did NOT stop.

        A RealSense pipeline cannot be opened twice, so the session can only
        have the cameras once these threads have run their `finally:
        cam.disconnect()`. stop() is just a flag the loop checks between
        reads, and the wrist D405's marginal USB 2 link can sit inside
        read_latest for a second or more (librealsense's own frame timeout is
        5 s), so the wait has to be generous.

        The previous 2 s wait ignored its own return value and cleared the
        handle regardless, so a thread that was still holding the device was
        forgotten -- and the session's connect then failed with librealsense's
        "Failed to open RealSenseCamera(...)", which names the camera but not
        the reason. Report the truth instead and let the caller refuse to
        connect.
        """
        stuck = []
        for role in ("agent", "wrist"):
            w = getattr(self, f"{role}_preview", None)
            if w is None:
                continue
            w.stop()
            if w.wait(timeout_ms):
                setattr(self, f"{role}_preview", None)
            else:
                # Keep the handle: it still owns the device, and dropping the
                # reference would only lose the ability to try again.
                stuck.append(role)
                self.log(f"[카메라] {role} 미리보기 스레드가 {timeout_ms / 1000:.0f}초 안에 "
                         f"종료되지 않았습니다 (카메라를 아직 붙잡고 있음).")
        if not stuck:
            # librealsense needs a moment to actually release the USB device
            # after disconnect() returns; connecting immediately can still hit
            # a busy device.
            QThread.msleep(300)
        self.lights["camera"].set("off" if not stuck else "bad",
                                  "-" if not stuck else tr("해제 실패"))
        return stuck

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

        # The worker opens both cameras itself, and a RealSense pipeline cannot
        # be opened twice -- so bail out here with the real reason rather than
        # letting connect fail later with "Failed to open RealSenseCamera(...)".
        stuck = self._stop_previews()
        if stuck:
            QMessageBox.warning(
                self, tr("카메라 해제 실패"),
                tr("{roles} 미리보기가 카메라를 아직 붙잡고 있어 연결할 수 없습니다.\n\n"
                   "몇 초 뒤 다시 시도하거나, Camera 메뉴 > 미리보기 중지 후 연결하세요. "
                   "계속되면 카메라를 뽑았다 꽂아야 합니다 (손목 D405는 USB 2 링크라 "
                   "가끔 늦게 놓습니다).").format(roles=", ".join(stuck)))
            return
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
        self.log(f"[저장] {name} ({n_frames} frames)")
        self.right_fields["episode"].setText(name)
        self._refresh_stats()

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
        QMessageBox.critical(self, tr("오류"), msg)

    @pyqtSlot(int, str)
    def _on_connected(self, n_episodes, path) -> None:
        if self._no_dataset_session:
            # NullTaskWriter has no real path; claiming one here would make the
            # dataset tree think a file is locked by this session.
            self.right_fields["file"].setText(tr("(기록 안 함)"))
            self.log("[연결] 연습 모드로 연결되었습니다.")
            return
        self.active_file_path = Path(path)
        self.right_fields["file"].setText(Path(path).name)
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
        self.sb_right.setText(
            f"{self._fps_value:.0f} fps   |   "
            f"{tr('저장')} {self._session['saved']}   |   {self.root_edit.text()}")

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
        items = self.dataset_tree.selectedItems()
        if not items or items[0].parent() is None:
            QMessageBox.information(self, tr("선택 필요"), tr("삭제할 에피소드를 선택하세요."))
            return
        name = items[0].data(0, Qt.ItemDataRole.UserRole)
        path = self._selected_file()
        if path is None:
            return
        busy = self._busy_reason()
        if busy:
            QMessageBox.warning(self, tr("삭제 불가"),
                                tr("{job}이(가) 진행 중입니다. 끝난 뒤 삭제하세요.").format(job=busy))
            return
        owned = self.active_file_path is not None and path == self.active_file_path
        if QMessageBox.question(
                self, tr("에피소드 삭제"),
                tr("{f}\n{n}을(를) 삭제할까요?\n\n남은 에피소드는 번호가 다시 매겨집니다. "
                   "파일 크기는 줄지 않습니다 (재압축 필요).").format(f=path.name, n=name)
        ) != QMessageBox.StandardButton.Yes:
            return
        if owned:
            self.worker.cmd_delete_episode(name)
            return
        try:
            with h5py.File(path, "a") as f:
                data = f["data"]
                if name not in data:
                    raise KeyError(name)
                del data[name]
                renumber_episodes(data)
            self.log(f"[삭제] {path.name}: {name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr("삭제 실패"), f"{type(e).__name__}: {e}")
            self.log(f"[삭제 실패] {path.name}: {type(e).__name__}: {e}")
        self._refresh_dataset_tree()

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
        confirm = QMessageBox.warning(
            self, tr("파일 삭제"),
            tr("{f}\n\n에피소드 {n}개, {mb:.1f} MB 를 통째로 지웁니다.\n"
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
        if item is None or item.parent() is None:
            return
        path = item.parent().data(0, Qt.ItemDataRole.UserRole)
        demo = item.data(0, Qt.ItemDataRole.UserRole)
        if not path or not demo or self._play_key == (path, demo):
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
        self.play_caption.setText(f"{Path(path).name} · {demo} · {n} frames @ {PLAYBACK_FPS} fps")
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

    # ------------------------------------------------------------- node
    def _on_start_node(self) -> None:
        if self.node_process is not None and \
                self.node_process.state() != QProcess.ProcessState.NotRunning:
            self.log("[노드] 이미 실행 중입니다.")
            return
        proc = QProcess(self)
        proc.setProgram(PYLIBFRANKA_PYTHON)
        proc.setArguments([LAUNCH_NODES_SCRIPT, "--robot", "fr3"])
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
        state = self._convert_out_state if view == "upload" else self._upload_out_state
        for line in clean_stream_lines(data, state):
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
                if state == "recording" and not self._no_dataset_session:
                    self._save(False)
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
        self._stop_previews()
        if self.worker is not None and self.worker.isRunning():
            self.worker.cmd_quit()
            self.worker.wait(5000)
        self._on_stop_node()
        for proc in (self.repack_process, self.convert_process, self.upload_process):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
                    proc.waitForFinished(2000)
        if self._log_file is not None:
            self._log_file.close()
        super().closeEvent(event)


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"workspace_{time.strftime('%Y%m%d_%H%M%S')}.log"
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = WorkspaceWindow(log_path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
