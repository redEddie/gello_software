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

import json
import sys
import time
from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import QEvent, QProcess, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402
from gello.robots.franka_fr3 import FR3_RESET_POSES  # noqa: E402

# launch_nodes.py needs pylibfranka, which only exists in this separate venv
# (this GUI itself runs in lerobot-venv -- see module docstring). Spawned as
# a subprocess rather than imported, same as running it by hand in a second
# terminal.
PYLIBFRANKA_PYTHON = str(Path.home() / "pylibfranka-venv" / "bin" / "python")
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parent / "launch_nodes.py")

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
                    frame = cam.read()
                except Exception as e:  # noqa: BLE001
                    if not self._running:
                        break
                    self.error.emit(f"{type(e).__name__}: {e}")
                    break
                if self._running:
                    self.frame_ready.emit(frame)
        finally:
            try:
                cam.disconnect()
            except Exception:  # noqa: BLE001
                pass


class LiberoCollectorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FR3 + GELLO -- LIBERO 데이터 수집기")
        self.worker: CollectionWorker | None = None
        self.episodes_this_session = 0
        self.node_ok = True
        self.active_file_path: Path | None = None
        self.active_episode_cache: list[dict] | None = None  # None = not loaded yet
        self.agent_preview_worker: CameraPreviewWorker | None = None
        self.wrist_preview_worker: CameraPreviewWorker | None = None
        self.node_process: QProcess | None = None
        self._node_restart_pending = False
        self._current_state = "idle"

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

        # Left column: robot-node control, session setup, camera preview, log.
        left_col = QVBoxLayout()
        left_col.addWidget(self._build_node_box())
        left_col.addWidget(self._build_config_box())
        left_col.addWidget(self._build_video_box())
        left_col.addWidget(self.log_view)

        # Right column: pose-match gate, status, dataset browser -- lets a
        # maximized window show setup+camera and monitoring+data side by
        # side instead of one long vertical stack.
        right_col = QVBoxLayout()
        right_col.addWidget(self._build_gate_box())
        right_col.addWidget(self._build_status_box())
        right_col.addWidget(self._build_dataset_box())

        columns = QHBoxLayout()
        columns.addLayout(left_col, 1)
        columns.addLayout(right_col, 1)

        scroll_content = QWidget()
        scroll_root = QVBoxLayout(scroll_content)
        scroll_root.addLayout(columns)

        # The two columns can still exceed a laptop screen's height; a
        # scroll area lets the window itself stay within the screen while
        # the (taller) content scrolls, instead of the window manager
        # clamping/clipping the window off-screen.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_content)

        # 제어 row is deliberately OUTSIDE the scroll area, pinned to the
        # bottom of the window so it stays visible in one row regardless of
        # scroll position or window resizing.
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(scroll, 1)
        central_layout.addWidget(self._build_control_box())
        self.setCentralWidget(central)

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

    # ------------------------------------------------------------ UI build
    def _build_node_box(self) -> QGroupBox:
        box = QGroupBox("로봇 노드 (launch_nodes.py, pylibfranka-venv)")
        layout = QHBoxLayout(box)

        layout.addWidget(QLabel("로봇 IP:"))
        self.robot_ip_edit = QLineEdit("172.16.0.2")
        self.robot_ip_edit.setFixedWidth(120)
        layout.addWidget(self.robot_ip_edit)

        self.node_start_btn = QPushButton("노드 시작")
        self.node_start_btn.setStyleSheet("background-color: #2ecc71;")
        self.node_start_btn.clicked.connect(self._on_start_node)
        layout.addWidget(self.node_start_btn)

        self.node_restart_btn = QPushButton("노드 재시작")
        self.node_restart_btn.setStyleSheet("background-color: #f39c12;")
        self.node_restart_btn.clicked.connect(self._on_restart_node)
        layout.addWidget(self.node_restart_btn)

        self.node_stop_btn = QPushButton("노드 중지")
        self.node_stop_btn.clicked.connect(self._on_stop_node)
        layout.addWidget(self.node_stop_btn)

        self.node_proc_label = QLabel("중지됨")
        self.node_proc_label.setStyleSheet("color: #888;")
        layout.addWidget(self.node_proc_label)
        layout.addStretch()
        return box

    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox("세션 설정 (연결 전에만 수정 가능)")
        grid = QGridLayout(box)

        grid.addWidget(QLabel("Task 이름:"), 0, 0)
        self.task_combo = QComboBox()
        self.task_combo.setEditable(True)
        self.task_combo.lineEdit().setPlaceholderText("예: pick up the red block")
        self.task_combo.setToolTip("저장 경로에 이미 있는 task를 선택하면 이어서 수집 가능 (--resume 자동 체크)")
        self.task_combo.activated.connect(self._on_task_selected)
        grid.addWidget(self.task_combo, 0, 1, 1, 3)

        grid.addWidget(QLabel("언어 지시문:"), 1, 0)
        self.lang_edit = QLineEdit()
        self.lang_edit.setPlaceholderText("비워두면 Task 이름과 동일하게 사용")
        grid.addWidget(self.lang_edit, 1, 1, 1, 3)

        grid.addWidget(QLabel("데이터 저장 경로:"), 2, 0)
        self.root_edit = QLineEdit(str(Path.home() / "libero_datasets"))
        grid.addWidget(self.root_edit, 2, 1, 1, 2)
        browse_btn = QPushButton("찾아보기...")
        browse_btn.clicked.connect(self._browse_root)
        grid.addWidget(browse_btn, 2, 3)

        grid.addWidget(QLabel("Reset pose:"), 3, 0)
        self.reset_pose_combo = QComboBox()
        self.reset_pose_combo.addItems(sorted(FR3_RESET_POSES.keys()))
        self.reset_pose_combo.setCurrentText("libero")
        grid.addWidget(self.reset_pose_combo, 3, 1)

        grid.addWidget(QLabel("Grip:"), 3, 2)
        self.grip_combo = QComboBox()
        self.grip_combo.addItems(["right", "left"])
        grid.addWidget(self.grip_combo, 3, 3)

        self.wall_check = QCheckBox("GELLO 조인트 한계 벽(wall) 사용")
        self.wall_check.setChecked(True)
        grid.addWidget(self.wall_check, 4, 0, 1, 2)

        self.resume_check = QCheckBox("기존 데이터셋에 이어붙이기 (--resume)")
        grid.addWidget(self.resume_check, 4, 2, 1, 2)

        grid.addWidget(QLabel("에피소드 최대 길이(초):"), 5, 0)
        self.max_seconds_edit = QLineEdit("20")
        grid.addWidget(self.max_seconds_edit, 5, 1)

        grid.addWidget(QLabel("리셋 대기(초):"), 5, 2)
        self.reset_wait_edit = QLineEdit("10")
        grid.addWidget(self.reset_wait_edit, 5, 3)

        grid.addWidget(QLabel("Agentview 카메라:"), 6, 0)
        self.agent_cam_combo = QComboBox()
        self.agent_cam_combo.setEditable(True)
        self.agent_cam_combo.setToolTip("연결된 RealSense 목록에서 선택하거나 시리얼번호를 직접 입력")
        self.agent_cam_combo.currentIndexChanged.connect(lambda _: self._on_camera_selection_changed("agent"))
        self.agent_cam_combo.lineEdit().editingFinished.connect(
            lambda: self._on_camera_selection_changed("agent")
        )
        grid.addWidget(self.agent_cam_combo, 6, 1)

        grid.addWidget(QLabel("Wrist 카메라:"), 6, 2)
        self.wrist_cam_combo = QComboBox()
        self.wrist_cam_combo.setEditable(True)
        self.wrist_cam_combo.setToolTip("연결된 RealSense 목록에서 선택하거나 시리얼번호를 직접 입력")
        self.wrist_cam_combo.currentIndexChanged.connect(lambda _: self._on_camera_selection_changed("wrist"))
        self.wrist_cam_combo.lineEdit().editingFinished.connect(
            lambda: self._on_camera_selection_changed("wrist")
        )
        grid.addWidget(self.wrist_cam_combo, 6, 3)

        self.cam_refresh_btn = QPushButton("카메라 목록 새로고침")
        self.cam_refresh_btn.clicked.connect(self._refresh_cameras)
        grid.addWidget(self.cam_refresh_btn, 7, 0, 1, 2)

        self.camera_hint = QLabel("")
        self.camera_hint.setStyleSheet("color: #888;")
        grid.addWidget(self.camera_hint, 7, 2, 1, 2)

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
            self.camera_hint.setText(f"카메라 목록 조회 실패: {type(e).__name__}: {e}")
            self._log(f"[카메라] 목록을 가져오지 못했습니다: {type(e).__name__}: {e}")
            return

        for combo in (self.agent_cam_combo, self.wrist_cam_combo):
            prev = self._camera_serial_from_combo(combo)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(선택 안함)", "")
            for c in cams:
                combo.addItem(f"{c.get('name', '?')} ({c['id']})", c["id"])
            idx = combo.findData(prev) if prev else 0
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(prev)
            combo.blockSignals(False)

        if cams:
            self.camera_hint.setText(f"{len(cams)}대 감지됨")
        else:
            self.camera_hint.setText("감지된 RealSense 카메라가 없습니다 (시리얼번호 직접 입력 가능)")
        self._log(f"[카메라] {len(cams)}대 감지됨: " + ", ".join(f"{c.get('name', '?')}={c['id']}" for c in cams))

        # blockSignals() suppressed currentIndexChanged during repopulation --
        # sync the preview to whatever ended up selected.
        self._on_camera_selection_changed("agent")
        self._on_camera_selection_changed("wrist")

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
            view.setText("선택 안함 -- 대기 중")
            return

        view.setPixmap(QPixmap())
        view.setText(f"{serial} 미리보기 연결 중...")
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
        view.setText(f"미리보기 실패: {msg}")
        self._log(f"[카메라 미리보기 실패] {role}: {msg}")

    def _build_video_box(self) -> QGroupBox:
        box = QGroupBox("카메라")
        layout = QHBoxLayout(box)
        self.agent_view = QLabel("선택 안함 -- 대기 중")
        self.agent_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.agent_view.setMinimumSize(400, 300)
        self.agent_view.setStyleSheet("background-color: #111; color: #888;")
        self.wrist_view = QLabel("선택 안함 -- 대기 중")
        self.wrist_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wrist_view.setMinimumSize(400, 300)
        self.wrist_view.setStyleSheet("background-color: #111; color: #888;")
        col_a = QVBoxLayout()
        col_a.addWidget(QLabel("Agentview (외부 카메라)"))
        col_a.addWidget(self.agent_view)
        col_w = QVBoxLayout()
        col_w.addWidget(QLabel("Eye-in-hand (손목 카메라)"))
        col_w.addWidget(self.wrist_view)
        layout.addLayout(col_a)
        layout.addLayout(col_w)
        return box

    def _build_gate_box(self) -> QGroupBox:
        box = QGroupBox(f"자세 매칭 (leader vs follower, 게이트 {GATE_RAD} rad)")
        layout = QVBoxLayout(box)
        self.delta_bars = [DeltaBar(name) for name in JOINT_LABELS]
        for bar in self.delta_bars:
            layout.addWidget(bar)
        self.gate_summary = QLabel("로봇 연결 후 표시됩니다")
        layout.addWidget(self.gate_summary)
        return box

    def _build_control_box(self) -> QGroupBox:
        box = QGroupBox("제어")
        layout = QHBoxLayout(box)

        self.connect_btn = QPushButton("로봇 연결")
        self.connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self.connect_btn)

        self.start_teleop_btn = QPushButton("텔레옵 시작 (Space)")
        self.start_teleop_btn.clicked.connect(self._on_start_teleop)
        layout.addWidget(self.start_teleop_btn)

        self.skip_reset_btn = QPushButton("리셋 대기 건너뛰기")
        self.skip_reset_btn.clicked.connect(self._on_skip_reset)
        layout.addWidget(self.skip_reset_btn)

        self.save_success_btn = QPushButton("저장 (성공) (Space)")
        self.save_success_btn.setStyleSheet("background-color: #2ecc71;")
        self.save_success_btn.clicked.connect(lambda: self._on_save(True))
        layout.addWidget(self.save_success_btn)

        self.save_fail_btn = QPushButton("저장 (실패) (Esc)")
        self.save_fail_btn.setStyleSheet("background-color: #f1c40f;")
        self.save_fail_btn.clicked.connect(lambda: self._on_save(False))
        layout.addWidget(self.save_fail_btn)

        self.discard_btn = QPushButton("폐기")
        self.discard_btn.clicked.connect(self._on_discard)
        layout.addWidget(self.discard_btn)

        self.go_home_btn = QPushButton("홈으로 이동")
        self.go_home_btn.setStyleSheet("background-color: #3498db; color: white;")
        self.go_home_btn.clicked.connect(self._on_go_home)
        layout.addWidget(self.go_home_btn)

        self.quit_btn = QPushButton("세션 종료")
        self.quit_btn.setStyleSheet("background-color: #e74c3c; color: white;")
        self.quit_btn.clicked.connect(self._on_quit)
        layout.addWidget(self.quit_btn)

        return box

    def _build_status_box(self) -> QGroupBox:
        box = QGroupBox("상태")
        layout = QHBoxLayout(box)
        self.state_label = QLabel("대기")
        self.state_label.setStyleSheet("font-weight: bold; font-size: 14pt;")
        self.node_label = QLabel("노드: -")
        self.episode_label = QLabel("에피소드: 0")
        self.progress_label = QLabel("")
        for w in (self.state_label, self.node_label, self.episode_label, self.progress_label):
            layout.addWidget(w)
        layout.addStretch()
        note = QLabel(
            "⚠ 이 화면의 버튼은 소프트웨어 텔레옵 루프만 멈춥니다. "
            "실제 비상정지는 로봇 하드웨어 E-stop을 사용하세요."
        )
        note.setStyleSheet("color: #e67e22;")
        layout.addWidget(note)
        return box

    def _build_dataset_box(self) -> QGroupBox:
        box = QGroupBox("데이터셋 탐색기 (저장 경로 아래의 *_demo.hdf5)")
        layout = QVBoxLayout(box)

        self.dataset_tree = QTreeWidget()
        self.dataset_tree.setColumnCount(3)
        self.dataset_tree.setHeaderLabels(["파일 / 에피소드", "프레임수", "결과"])
        self.dataset_tree.setMaximumHeight(220)
        layout.addWidget(self.dataset_tree)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("새로고침")
        refresh_btn.clicked.connect(self._refresh_dataset_tree)
        btn_row.addWidget(refresh_btn)
        delete_btn = QPushButton("선택 삭제")
        delete_btn.setStyleSheet("background-color: #e74c3c; color: white;")
        delete_btn.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()
        hint = QLabel(
            "HDF5는 삭제해도 파일 용량이 즉시 줄지 않습니다 (디스크 공간까지 "
            "회수하려면 h5repack 필요)."
        )
        hint.setStyleSheet("color: #888;")
        btn_row.addWidget(hint)
        layout.addLayout(btn_row)
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
        d = QFileDialog.getExistingDirectory(self, "데이터 저장 경로", self.root_edit.text())
        if d:
            self.root_edit.setText(d)
            self._refresh_dataset_tree()

    def _refresh_tasks(self) -> None:
        """Re-scans the data root for ``<task>_demo.hdf5`` files and repopulates
        the Task 이름 combo, so a previous task can be picked instead of
        retyped. Filenames use ``LiberoTaskWriter``'s exact slugification
        (spaces -> underscores), reversed here to recover the task name --
        so re-selecting an entry and connecting targets the same file.
        """
        prev = self.task_combo.currentText()
        root = Path(self.root_edit.text()).expanduser()
        tasks = []
        if root.is_dir():
            for path in sorted(root.glob("**/*_demo.hdf5")):
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
        instruction from the existing file and checks --resume, since
        picking an existing task almost always means continuing it.
        """
        task = self.task_combo.itemText(index)
        root = Path(self.root_edit.text()).expanduser()
        safe_name = task.strip().replace(" ", "_")
        path = root / f"{safe_name}_demo.hdf5"
        if not path.exists():
            return
        self.resume_check.setChecked(True)
        try:
            with h5py.File(path, "r") as f:
                info = json.loads(f["data"].attrs["problem_info"])
            lang = info.get("language_instruction", "")
            if len(lang) >= 2 and lang.startswith('"') and lang.endswith('"'):
                lang = lang[1:-1]
            self.lang_edit.setText(lang)
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
        for path in sorted(root.glob("**/*_demo.hdf5")):
            file_item = QTreeWidgetItem([path.name, "", ""])
            file_item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.dataset_tree.addTopLevelItem(file_item)

            if self.active_file_path is not None and path.samefile(self.active_file_path):
                if self.active_episode_cache is None:
                    # Connected, but the worker's first episode_list_changed
                    # hasn't arrived yet -- don't claim 0 episodes.
                    file_item.setText(1, "불러오는 중...")
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
                    file_item.setText(1, f"(읽기 실패: {e})")
                    continue

            for ep in episodes:
                success = ep["success"]
                result = "-" if success is None else ("성공" if success else "실패")
                child = QTreeWidgetItem(["  " + ep["name"], str(ep["num_samples"]), result])
                child.setData(0, Qt.ItemDataRole.UserRole, ep["name"])
                file_item.addChild(child)
            file_item.setText(1, f"{len(episodes)}개 에피소드")
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
                    self, "삭제 불가", "현재 세션이 사용 중인 파일입니다. 먼저 '세션 종료'를 누르세요."
                )
                return
            reply = QMessageBox.question(
                self,
                "파일 전체 삭제",
                f"{file_path.name}\n이 작업의 데이터 전체(모든 에피소드)가 영구 삭제됩니다. 계속할까요?",
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
            self, "에피소드 삭제", f"{file_path.name} / {demo_name} 를 삭제할까요?"
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.active_file_path is not None and file_path.samefile(self.active_file_path):
            if self.worker is None:
                QMessageBox.warning(self, "삭제 불가", "세션이 종료되었습니다. 새로고침 후 다시 시도하세요.")
                return
            self.worker.cmd_delete_episode(demo_name)  # worker deletes + re-emits episode_list_changed
        else:
            with h5py.File(file_path, "a") as f:
                del f["data"][demo_name]
            self._log(f"[삭제] {file_path.name} / {demo_name}")
            self._refresh_dataset_tree()

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
            self.max_seconds_edit,
            self.reset_wait_edit,
            self.agent_cam_combo,
            self.wrist_cam_combo,
            self.cam_refresh_btn,
        ):
            w.setEnabled(not connected)
        self.start_teleop_btn.setEnabled(connected and not recording and self.node_ok)
        self.skip_reset_btn.setEnabled(connected and reset_wait and self.node_ok)
        self.save_success_btn.setEnabled(connected and recording and self.node_ok)
        self.save_fail_btn.setEnabled(connected and recording and self.node_ok)
        self.discard_btn.setEnabled(connected and recording and self.node_ok)
        self.go_home_btn.setEnabled(connected and self.node_ok)
        self.quit_btn.setEnabled(connected)

    # -------------------------------------------------------------- actions
    def _on_connect(self) -> None:
        task = self.task_combo.currentText().strip()
        if not task:
            QMessageBox.warning(self, "Task 이름 필요", "Task 이름을 입력하세요.")
            return
        lang = self.lang_edit.text().strip() or task
        try:
            max_seconds = float(self.max_seconds_edit.text())
            reset_wait = float(self.reset_wait_edit.text())
        except ValueError:
            QMessageBox.warning(self, "입력 오류", "에피소드 길이/리셋 대기는 숫자여야 합니다.")
            return

        agent_serial = self._camera_serial_from_combo(self.agent_cam_combo)
        wrist_serial = self._camera_serial_from_combo(self.wrist_cam_combo)
        if not agent_serial or not wrist_serial:
            QMessageBox.warning(self, "카메라 선택 필요", "Agentview / Wrist 카메라를 모두 선택하세요.")
            return
        if agent_serial == wrist_serial:
            QMessageBox.warning(self, "카메라 중복", "Agentview와 Wrist에 같은 카메라가 선택되었습니다.")
            return

        # Release the preview pipelines before the session opens the same
        # serials -- RealSense can't have two pipelines on one device at once.
        self._log("[카메라] 미리보기 정지 중...")
        if not self._stop_previews():
            QMessageBox.critical(
                self,
                "카메라 해제 실패",
                "미리보기 카메라를 제때 해제하지 못했습니다. 잠시 후 다시 시도하세요.",
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
            resume=self.resume_check.isChecked(),
            agent_camera_serial=agent_serial,
            wrist_camera_serial=wrist_serial,
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
        self.episode_label.setText(f"에피소드: {start_count}")
        self.active_file_path = Path(file_path)
        self._log(f"[연결 완료] 기존 {start_count}개 에피소드 ({file_path})")
        self._set_controls_enabled(connected=True, gate_ok=False, recording=False, reset_wait=False)

    @pyqtSlot(list)
    def _on_episode_list_changed(self, episodes: list) -> None:
        self.active_episode_cache = episodes
        self._refresh_dataset_tree()

    @pyqtSlot(str)
    def _on_state_changed(self, state: str) -> None:
        self._current_state = state
        self.state_label.setText(STATE_LABELS_KO.get(state, state))
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
            self.gate_summary.setText("모든 조인트 일치 -- '텔레옵 시작'을 누르세요")
            self.gate_summary.setStyleSheet("color: #2ecc71; font-weight: bold;")
        else:
            worst = int(np.argmax(np.abs(deltas)))
            self.gate_summary.setText(
                f"{JOINT_LABELS[worst]} 조인트를 맞춰주세요 (차이 {deltas[worst]:+.3f} rad)"
            )
            self.gate_summary.setStyleSheet("color: #e74c3c;")

    @pyqtSlot(int, float)
    def _on_episode_progress(self, n_frames, seconds) -> None:
        self.progress_label.setText(f"기록 중: {n_frames} 프레임 ({seconds:.1f}s)")

    @pyqtSlot(str, int)
    def _on_episode_saved(self, demo_name, n_frames) -> None:
        self.episodes_this_session += 1
        self.episode_label.setText(f"에피소드: {self.episodes_this_session}")
        self._log(f"[저장] {demo_name} ({n_frames} 프레임)")

    @pyqtSlot(int)
    def _on_episode_discarded(self, n_frames) -> None:
        self._log(f"[폐기] {n_frames} 프레임")

    @pyqtSlot(float)
    def _on_reset_countdown(self, remaining) -> None:
        self.progress_label.setText(f"환경 리셋 대기: {remaining:.0f}s (건너뛰려면 버튼 클릭)")

    @pyqtSlot(bool)
    def _on_node_status(self, ok) -> None:
        self.node_ok = ok
        self.node_label.setText("노드: 정상" if ok else "노드: 응답 없음 (launch_nodes 재시작 필요)")
        self.node_label.setStyleSheet("color: #2ecc71;" if ok else "color: #e74c3c; font-weight: bold;")
        self._set_controls_enabled(
            connected=True,
            gate_ok=(self.state_label.text() == STATE_LABELS_KO["gate"]),
            recording=(self.state_label.text() == STATE_LABELS_KO["recording"]),
            reset_wait=(self.state_label.text() == STATE_LABELS_KO["reset_wait"]),
        )

    @pyqtSlot(str)
    def _on_fatal_error(self, msg) -> None:
        self._log(f"[오류] {msg}")
        QMessageBox.critical(self, "오류", msg)

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
            QMessageBox.information(self, "세션 요약", text)

    def _on_worker_finished(self) -> None:
        self._log("[세션 종료]")
        self.worker = None
        self.node_ok = True
        self._current_state = "idle"
        self.active_file_path = None  # file is no longer exclusively open; browser can read it directly
        self._set_controls_enabled(connected=False, gate_ok=False, recording=False, reset_wait=False)
        self.state_label.setText("대기")
        self._refresh_dataset_tree()
        # The worker released both cameras on its way out -- resume previews
        # for whatever's currently selected in the combo boxes.
        self._on_camera_selection_changed("agent")
        self._on_camera_selection_changed("wrist")

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

    # --------------------------------------------------------- robot node
    def _on_start_node(self) -> None:
        if self.node_process is not None and self.node_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "이미 실행 중", "로봇 노드가 이미 실행 중입니다.")
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
        self.node_proc_label.setText("시작 중...")
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
        self.node_proc_label.setText(f"실행 중 (PID {pid})")
        self.node_proc_label.setStyleSheet("color: #2ecc71;")

    def _on_node_error(self, error) -> None:  # noqa: ANN001 - QProcess.ProcessError
        self._log(f"[NODE] 프로세스 오류: {error}")

    def _on_node_finished(self, exit_code: int, exit_status) -> None:  # noqa: ANN001
        self._log(f"[NODE] 종료됨 (exit code {exit_code})")
        self.node_proc_label.setText("중지됨")
        self.node_proc_label.setStyleSheet("color: #888;")
        if self._node_restart_pending:
            self._node_restart_pending = False
            self._on_start_node()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._stop_previews()
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        self._on_stop_node()
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

            gate       + Space -> 텔레옵 시작 (start teleop)
            recording  + Space -> 저장 (성공)
            recording  + Esc   -> 저장 (실패)

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
        return super().eventFilter(obj, event)


def main() -> None:
    app = QApplication(sys.argv)
    window = LiberoCollectorWindow()

    screen = app.primaryScreen().availableGeometry()
    width = min(1500, int(screen.width() * 0.95))
    height = min(950, int(screen.height() * 0.92))
    window.resize(width, height)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
