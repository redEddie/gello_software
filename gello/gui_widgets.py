"""Widgets and dialogs shared by the collector GUIs.

Split out of the old wizard GUI (experiments/collect_libero_gui.py, replaced
by experiments/collect_workspace.py in 62cad92) so the workspace UI could
reuse them without importing a module that also defined a whole competing
main window. Nothing here knows about the window it lives in -- these are the
pieces that were already independent of the 3-phase wizard: the video view,
the episode loader, the camera preview thread, and the four dialogs
(schema / LeRobot convert / HDF5 upload / repack).
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
from PyQt6.QtCore import QEvent, QProcess, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
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
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
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
    DEAD_SPACE_RATIO,
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
# Episodes are recorded at 20 Hz, so playing them back at 20 fps shows the
# motion at the speed it actually happened -- which is the point of reviewing.
PLAYBACK_FPS = 20
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


# 미개발 표시. collect_workspace 가 gui_widgets 를 import 하므로 아래쪽 모듈인
# 여기에 두고 위에서 가져다 쓴다 -- 반대로 두면 순환 import 가 된다.
TODO_MARK = "미개발"

_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def repo_id_error(repo_id: str) -> "str | None":
    """Why `repo_id` is not a usable Hub id, or None if it is.

    Checked before anything is stored or run because the failure it prevents is
    slow and confusing: an id with a bad namespace passes every local step and
    dies at the very end with `403 ... rights to create a dataset under the
    namespace "r"`, after (in one real run) 15.6 minutes of repacking. And a
    typo that reaches Recents becomes the default for the automatic buttons,
    so the same failure repeats without anyone retyping it.
    """
    if not repo_id:
        return "Repo ID를 입력하세요."
    if "/" not in repo_id:
        return f"'{repo_id}' 에 네임스페이스가 없습니다. <조직 또는 사용자>/<이름> 형식이어야 합니다."
    if repo_id.count("/") > 1:
        return f"'{repo_id}' 에 '/' 가 너무 많습니다. <네임스페이스>/<이름> 하나뿐이어야 합니다."
    if not _REPO_ID_RE.match(repo_id):
        return f"'{repo_id}' 는 사용할 수 없는 형식입니다 (영문/숫자로 시작, 나머지는 영문·숫자·. _ - )."
    ns = repo_id.split("/")[0]
    if len(ns) < 2:
        return f"네임스페이스 '{ns}' 가 너무 짧습니다 — 오타로 보입니다."
    return None


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

def hf_stored_accounts() -> list[dict]:
    """Every locally stored HF token, with the account it actually belongs to.

    huggingface_hub keeps several tokens at once (``~/.cache/huggingface/
    stored_tokens``) and one of them is active. The stored *name* is the
    token's display name, not the account -- two profiles can belong to the
    same person, which is exactly what this machine had. So each token is
    resolved to its real username here; picking between "franka" and
    "oauth-gibeom25" tells you nothing on its own.

    Never raises: the switcher must still open when the network is down, just
    with '확인 실패' next to the entries it could not resolve.
    """
    try:
        from huggingface_hub import HfApi, get_token
        from huggingface_hub.utils._auth import get_stored_tokens
    except ImportError:
        return []
    try:
        stored = get_stored_tokens()
        active = get_token()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for name, token in stored.items():
        entry = {"profile": name, "active": token == active, "user": None, "orgs": [], "error": ""}
        try:
            info = HfApi(token=token).whoami()
            entry["user"] = info.get("name")
            entry["orgs"] = [o["name"] for o in info.get("orgs", [])
                             if isinstance(o, dict) and "name" in o]
        except Exception as e:  # noqa: BLE001
            entry["error"] = type(e).__name__
        out.append(entry)
    return out


def hf_switch_account(profile: str) -> str:
    """Makes ``profile`` the active token. Returns the resulting username.

    Subprocesses pick this up because they read the token file at startup, so
    an upload launched after the switch uploads as the new account -- one
    already running does not.
    """
    from huggingface_hub import HfApi, auth_switch

    auth_switch(profile)
    return HfApi().whoami().get("name", "?")


def hf_add_account(token: str) -> tuple[str, str]:
    """Stores a new token and makes it active. Returns (profile, username)."""
    from huggingface_hub._login import _validate_and_save_token

    return _validate_and_save_token(token.strip(), add_to_git_credential=False)


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


def is_progress_line(line: str) -> bool:
    """tqdm 진행률 줄인가. 로그에 쌓지 않고 한 줄을 갱신하는 데 쓴다."""
    return bool(_PROGRESS_RE.search(line))


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


class VideoView(QLabel):
    """Shows frames at their own aspect ratio without letting them drive layout.

    Two things go wrong with a plain QLabel here. Its sizeHint *is* its
    pixmap's size, so scaling each frame to the label's current size inside a
    layout is a feedback loop -- the pixmap grows the label, the bigger label
    grows the next pixmap. An Ignored size policy breaks it: the layout decides
    the box, the frame fits inside it.

    The other is a fixed 4:3 minimum, which wastes width on the 256x256 square
    frames the collector records (LIBERO/OpenVLA convention) -- the minimum is
    square here, and KeepAspectRatio handles a 640x480 "원본 해상도 유지" file
    just as well.

    Keeping the source frame also means dragging the splitter rescales what is
    on screen, instead of leaving a stale pixmap until the next tick -- which
    never comes while paused.
    """

    def __init__(self) -> None:
        super().__init__()
        self._frame = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet("background-color: #111; color: #666;")

    def set_frame(self, arr) -> None:
        self._frame = arr
        self._rescale()

    def clear_frame(self, text: str = "") -> None:
        self._frame = None
        self.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX -- 다음 영상 전까지 해제
        self.setPixmap(QPixmap())
        if text:
            self.setText(text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._frame is None:
            return
        h, w = self._frame.shape[:2]
        # Height is what's scarce (two views stacked), so the frame's width is
        # decided by it. Shrinking the label to exactly that width means the
        # leftover is normal panel background rather than a black letterbox
        # around a small square -- which is what a 256x256 source in a wide box
        # looks like. Guarded because setMaximumWidth relayouts and re-enters
        # here; the height it depends on is set by the splitter, not by this
        # width, so it settles after one pass.
        want = max(1, round(self.height() * w / h))
        if self.maximumWidth() != want:
            self.setMaximumWidth(want)
        self.setPixmap(np_to_pixmap(self._frame).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))


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


class EpisodeLoadWorker(QThread):
    """Reads one episode's two image streams into RAM, off the UI thread.

    Playback could read frame-by-frame instead -- gzip images cost ~1.7 ms per
    frame, 3% of a 20 Hz tick -- but that puts an h5py read inside the timer
    callback, where a stall from a cold page cache or a competing repack shows
    up directly as a dropped frame. A whole episode is ~47 MB for both cameras
    and loads in ~0.4 s, so it is bought once here and played from memory,
    which also keeps the file handle open for the shortest possible time (the
    collection session may want it back).
    """

    loaded = pyqtSignal(str, str, object, object)  # path, demo, agent, wrist
    failed = pyqtSignal(str)

    def __init__(self, path: str, demo: str) -> None:
        super().__init__()
        self.path = path
        self.demo = demo

    def run(self) -> None:
        try:
            with h5py.File(self.path, "r") as f:
                obs = f["data"][self.demo]["obs"]
                agent = obs["agentview_rgb"][:] if "agentview_rgb" in obs else None
                wrist = obs["eye_in_hand_rgb"][:] if "eye_in_hand_rgb" in obs else None
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        if agent is None and wrist is None:
            self.failed.emit(tr("이 에피소드에는 이미지가 없습니다."))
            return
        self.loaded.emit(self.path, self.demo, agent, wrist)


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


class HfAccountDialog(QDialog):
    """Switch between stored HF accounts, or add one by pasting a token.

    This machine is shared, and an upload goes out as whoever's token happens
    to be active -- which is not something to discover from the commit history
    afterwards. Switching is one click here instead of `hf auth login` in a
    terminal, and adding a second person is pasting their token once.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Hugging Face 계정"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self._switched_to = None

        layout.addWidget(QLabel(tr(
            "업로드는 여기서 선택된 계정으로 나갑니다. 이 PC는 공용이므로 "
            "올리기 전에 확인하세요.")))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([tr("계정"), tr("소속 org"), tr("저장된 이름"), tr("상태")])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 160)
        self.tree.setColumnWidth(1, 170)
        self.tree.setColumnWidth(2, 150)
        self.tree.setMinimumHeight(140)
        layout.addWidget(self.tree)

        row = QHBoxLayout()
        self.switch_btn = QPushButton(tr("선택한 계정으로 전환"))
        self.switch_btn.clicked.connect(self._on_switch)
        row.addWidget(self.switch_btn)
        refresh = QPushButton(tr("새로고침"))
        refresh.clicked.connect(self._reload)
        row.addWidget(refresh)
        row.addStretch()
        layout.addLayout(row)

        add_box = QGroupBox(tr("계정 추가"))
        acol = QVBoxLayout(add_box)
        acol.addWidget(QLabel(tr(
            "huggingface.co > Settings > Access Tokens 에서 write 권한 토큰을 "
            "만들어 붙여넣으세요. 추가하면 바로 그 계정으로 전환됩니다.")))
        trow = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("hf_****************")
        trow.addWidget(self.token_edit, 1)
        add_btn = QPushButton(tr("추가하고 전환"))
        add_btn.clicked.connect(self._on_add)
        trow.addWidget(add_btn)
        acol.addLayout(trow)
        layout.addWidget(add_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self.tree.clear()
        accounts = hf_stored_accounts()
        if not accounts:
            self.status.setText(tr("저장된 토큰이 없습니다. 아래에서 토큰을 추가하세요."))
            self.switch_btn.setEnabled(False)
            return
        self.switch_btn.setEnabled(True)
        same = len({a["user"] for a in accounts if a["user"]}) == 1 and len(accounts) > 1
        for a in accounts:
            item = QTreeWidgetItem([
                a["user"] or "?",
                ", ".join(a["orgs"]) or "-",
                a["profile"],
                tr("● 사용 중") if a["active"] else (a["error"] or ""),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, a["profile"])
            if a["active"]:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.darkGreen)
            self.tree.addTopLevelItem(item)
            if a["active"]:
                self.tree.setCurrentItem(item)
        self.status.setText(tr(
            "저장된 토큰이 모두 같은 계정({u})입니다 -- 다른 사람으로 바꾸려면 "
            "그 사람의 토큰을 추가해야 합니다.").format(u=accounts[0]["user"])
            if same else "")

    def _on_switch(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        profile = items[0].data(0, Qt.ItemDataRole.UserRole)
        try:
            user = hf_switch_account(profile)
        except Exception as e:  # noqa: BLE001
            self.status.setText(tr("전환 실패: {e}").format(e=f"{type(e).__name__}: {e}"))
            return
        self._switched_to = user
        self.status.setText(tr("이제 {u} 계정으로 업로드합니다. 이미 실행 중인 업로드는 "
                               "바뀌지 않습니다.").format(u=user))
        self._reload()

    def _on_add(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            return
        try:
            profile, user = hf_add_account(token)
        except Exception as e:  # noqa: BLE001
            self.status.setText(tr("토큰 추가 실패: {e}").format(e=f"{type(e).__name__}: {e}"))
            return
        self.token_edit.clear()
        self._switched_to = user
        self.status.setText(tr("{u} 추가 완료 (저장된 이름 {p}). 이제 이 계정으로 "
                               "업로드합니다.").format(u=user, p=profile))
        self._reload()

    def switched_to(self) -> "str | None":
        return self._switched_to


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
    # QComboBox itemData 는 int 는 왕복하지만 Python None 은 왕복하지 않는다.
    _IMAGE_SIZE_NATIVE = -1

    _EXTRA_FIELDS = [
        ("save_joint_velocities", "Joint velocities (관절 속도) -- 제어루프에서 이미 계산됨, 추가 비용 없음"),
        ("save_timestamp", "Timestamp (프레임별 wall-clock 시각) -- 프레임 간격 검증용"),
    ]
    def __init__(self, parent: QWidget, cfg: DatasetSchemaConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("데이터셋 구조 사용자 설정"))
        layout = QVBoxLayout(self)

        # Action 쪽은 이 다이얼로그에서 고를 수 없다. 액션 공간·그리퍼 규약·열
        # 이름이 파일마다 갈리면 한 데이터셋 안에서 조용히 호환되지 않는 파일이
        # 생기고, 그걸 잡아주는 장치가 지금 없다(issue #12). 값 자체는
        # DatasetSchemaConfig 의 FIXED_* 로 박혀 있고, 여기서는 무엇으로
        # 고정돼 있는지만 보여준다.
        fixed = QGroupBox(tr("Action 구조 (고정 -- 변경 불가)"))
        fixed_layout = QVBoxLayout(fixed)
        fixed_note = QLabel(tr(
            "Action Space: joint_absolute — 관절 절대각 7 + 그리퍼\n"
            "그리퍼: 0=open / 1=close 이진값 "
            "(Observation 의 gripper_states 는 0~1 연속값)\n"
            "열 이름: joint1.pos .. joint7.pos, gripper.pos "
            "— Observation 과 동일"))
        fixed_note.setWordWrap(True)
        fixed_note.setStyleSheet("color:#888;")
        fixed_layout.addWidget(fixed_note)
        layout.addWidget(fixed)

        self.field_checks: dict[str, QCheckBox] = {}

        obs_box = QGroupBox(tr("저장할 Observation 필드"))
        obs_layout = QVBoxLayout(obs_box)

        # None 은 QComboBox itemData 로 왕복하지 않아 센티널로 저장한다.
        image_size_row = QHBoxLayout()
        image_size_row.addWidget(QLabel(tr("이미지 해상도:")))
        self.image_size_combo = QComboBox()
        self.image_size_combo.addItem(tr("256x256, 정사각 크롭"), 256)
        self.image_size_combo.addItem(tr("480x480, 정사각 크롭"), 480)
        self.image_size_combo.addItem(
            tr("640x480, 크롭 없음 (카메라 원본)"), self._IMAGE_SIZE_NATIVE)
        target = cfg.image_size if cfg.image_size is not None else self._IMAGE_SIZE_NATIVE
        idx = self.image_size_combo.findData(target)
        if idx >= 0:
            self.image_size_combo.setCurrentIndex(idx)
        image_size_row.addWidget(self.image_size_combo, 1)
        obs_layout.addLayout(image_size_row)
        size_note = QLabel(tr(
            "에피소드 200프레임 기준 저장 시간 1.0 / 3.6 / 5.0초, "
            "파일 79 / 277 / 370MB"))
        size_note.setStyleSheet("color:#888;")
        size_note.setWordWrap(True)
        obs_layout.addWidget(size_note)

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

        self._editable_widgets = [obs_box, extra_box]

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

    def _current_config(self) -> DatasetSchemaConfig:
        """The config implied by the dialog's current widget state --
        regardless of whether OK has been clicked yet. Shared by
        result_config() (on accept) and _show_preview() (live, before
        committing to anything).
        """
        # Observation 쪽만 위젯에서 읽는다. Action 쪽은 dataclass 기본값이
        # 곧 고정값이므로 아무것도 넘기지 않는 것이 그대로 고정을 뜻한다.
        kwargs = {attr: cb.isChecked() for attr, cb in self.field_checks.items()}
        raw = self.image_size_combo.currentData()
        kwargs["image_size"] = None if raw == self._IMAGE_SIZE_NATIVE else raw
        return DatasetSchemaConfig(**kwargs)

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

        # 모드가 맨 위에 온다. 아래 항목 중 무엇이 보이고 무엇이 쓰이는지를
        # 이 선택이 결정하므로, 다 읽은 뒤에 고르게 두면 순서가 거꾸로다.
        # Conversion and upload are separate jobs: conversion is minutes of
        # AV1 encoding, upload is seconds. Bundling them meant "I already
        # converted, just upload it" had no answer -- re-running re-encoded
        # everything. Two explicit modes instead of one ambiguous checkbox.
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

        # Which account a --push actually uploads as. This machine is shared,
        # so "whose token is cached right now" is not something to assume.
        acct_text, acct_color = hf_account()
        acct_row = QHBoxLayout()
        self.hf_account_label = QLabel(acct_text)
        self.hf_account_label.setStyleSheet(f"color: {acct_color}; font-weight: bold;")
        self.hf_account_label.setWordWrap(True)
        acct_row.addWidget(self.hf_account_label, 1)
        switch_btn = QPushButton(tr("계정 전환..."))
        switch_btn.clicked.connect(self._open_account_dialog)
        acct_row.addWidget(switch_btn)
        up_col.addLayout(acct_row)

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


    def _open_account_dialog(self) -> None:
        dlg = HfAccountDialog(self)
        dlg.exec()
        text, color = hf_account()
        self.hf_account_label.setText(text)
        self.hf_account_label.setStyleSheet(f"color: {color}; font-weight: bold;")

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
        err = repo_id_error(repo_id)
        if err:
            QMessageBox.warning(self, tr("Repo ID 오류"), tr(err))
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

    def __init__(self, parent: QWidget, start_dir: str = "") -> None:
        """``start_dir`` is where 찾아보기 opens -- NOT a preselected file.

        It used to be a "default file" that callers filled with the data-root
        *directory*. The field then held a directory, and 'Repo 안 파일 이름'
        derived from it, so an upload went to the Hub under the folder's name
        (`libero_datasets`, 0.81 GB). Worse, that name later collided with the
        folder a multi-file upload wanted to create, and the Hub rejected the
        commit with 'Invalid file change'. Starting empty removes the whole
        class: nothing is ever uploaded under a name the operator didn't pick.
        """
        super().__init__(parent)
        self.setWindowTitle(tr("HDF5 원본 업로드"))
        self._start_dir = start_dir
        default_file = ""
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("업로드할 .hdf5 파일 (여러 개 선택 가능, 이미 큐레이션 끝난 파일):")))
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit(default_file)
        self.file_edit.textChanged.connect(lambda: self._on_files_changed())
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
        self.path_in_repo_label = QLabel(tr("Repo 안 파일 이름:"))
        grid.addWidget(self.path_in_repo_label, 1, 0)
        grid.addWidget(ex_name, 1, 2)
        self.path_in_repo_edit = QLineEdit(Path(default_file).name if default_file else "")
        self.path_in_repo_edit.setPlaceholderText(
            tr("비워두면 로컬 파일 이름 그대로")
        )
        grid.addWidget(self.path_in_repo_edit, 1, 1)
        layout.addLayout(grid)

        self.file_count_label = QLabel("")
        self.file_count_label.setStyleSheet("color: #888;")
        layout.addWidget(self.file_count_label)

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
        acct_row = QHBoxLayout()
        self.hf_account_label = QLabel(acct_text)
        self.hf_account_label.setStyleSheet(f"color: {acct_color}; font-weight: bold;")
        self.hf_account_label.setWordWrap(True)
        acct_row.addWidget(self.hf_account_label, 1)
        switch_btn = QPushButton(tr("계정 전환..."))
        switch_btn.clicked.connect(self._open_account_dialog)
        acct_row.addWidget(switch_btn)
        layout.addLayout(acct_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("업로드 시작"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._on_files_changed()


    def _open_account_dialog(self) -> None:
        dlg = HfAccountDialog(self)
        dlg.exec()
        text, color = hf_account()
        self.hf_account_label.setText(text)
        self.hf_account_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _on_delete_existing_toggled(self, on: bool) -> None:
        self.old_path_label.setEnabled(on)
        self.old_path_in_repo_edit.setEnabled(on)

    def _browse_file(self) -> None:
        first = self.file_edit.text().split()[0] if self.file_edit.text().strip() else ""
        start = str(Path(first).parent) if first else (self._start_dir or str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("업로드할 .hdf5 파일 (여러 개 선택 가능)"), start, "HDF5 (*.hdf5)")
        if not paths:
            return
        self.file_edit.setText(" ".join(paths))
        self._on_files_changed()

    def _on_files_changed(self) -> None:
        """Keeps the repo-name field honest about what it will do.

        With one file it renames; with several it can only be a folder, since
        one name for many uploads would leave just the last one. Saying so here
        is cheaper than discovering it on the Hub afterwards.
        """
        files = self.file_edit.text().split()
        multi = len(files) > 1
        if multi:
            self.path_in_repo_label.setText(tr("Repo 안 폴더:"))
            self.path_in_repo_edit.setPlaceholderText(
                tr("비워두면 repo 최상위. 파일 이름은 각자 그대로 유지됩니다"))
            if self.path_in_repo_edit.text().strip() in {Path(f).name for f in files}:
                self.path_in_repo_edit.clear()
            self.file_count_label.setText(
                tr("파일 {n}개 선택됨").format(n=len(files)))
        else:
            self.path_in_repo_label.setText(tr("Repo 안 파일 이름:"))
            self.path_in_repo_edit.setPlaceholderText(tr("비워두면 로컬 파일 이름 그대로"))
            if files and not self.path_in_repo_edit.text().strip():
                # 디렉터리에서 이름을 따오지 않는다 -- 그렇게 만들어진 게
                # Hub의 `libero_datasets` 파일이다.
                if Path(files[0]).is_file():
                    self.path_in_repo_edit.setText(Path(files[0]).name)
            self.file_count_label.setText("")
        # 여러 개일 때 '기존 파일 삭제'의 다른 이름 지정은 의미가 없다.
        self.old_path_label.setVisible(not multi)
        self.old_path_in_repo_edit.setVisible(not multi)

    def build_args(self) -> "list[str] | None":
        """Returns the script's argv (sans program name), or None (with a
        warning dialog already shown) if required fields are missing."""
        files = self.file_edit.text().split()
        if not files:
            QMessageBox.warning(self, tr("파일 필요"), tr(".hdf5 파일을 하나 이상 선택하세요."))
            return None
        repo_id = self.repo_id_edit.currentText().strip()
        err = repo_id_error(repo_id)
        if err:
            QMessageBox.warning(self, tr("Repo ID 오류"), tr(err))
            return None
        self._recents.add("hdf5_repo_id", repo_id)
        args = [*files, "--repo-id", repo_id]
        path_in_repo = self.path_in_repo_edit.text().strip()
        if path_in_repo:
            args += ["--path-in-repo", path_in_repo]
        args.append("--private" if self.private_check.isChecked() else "--no-private")
        if self.delete_existing_check.isChecked():
            args.append("--delete-existing")
            old_path_in_repo = self.old_path_in_repo_edit.text().strip()
            if old_path_in_repo and len(files) == 1:
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
            # 왜 다시 필요한지를 근거별로 모은다. 추가와 삭제는 원인이 다르고
            # (lzf가 섞임 / 지운 자리가 남음) 둘 다 동시에 성립할 수 있다.
            reasons = []
            if st["mixed"]:
                reasons.append(tr("{n}개 추가됨").format(n=st["new_since"] or "?"))
            if st["deleted_since"]:
                reasons.append(tr("{n}개 삭제됨").format(n=st["deleted_since"]))
            if st["dead_ratio"] >= DEAD_SPACE_RATIO:
                # 삭제분이 차지하던 자리. 개수 비교만으로는 '두 개 지우고 두 개
                # 더 찍은' 흔한 경우를 놓치므로, 이쪽이 결정적인 근거다.
                reasons.append(tr("빈 공간 {mb:,.0f} MB ({p:.0f}%)").format(
                    mb=st["dead_bytes"] / 1e6, p=st["dead_ratio"] * 100))
            if reasons:
                history = ", ".join(reasons) + tr(" — 다시 필요")
            elif st["marker"]:
                history = st["marker"]
            elif st["repacked"]:
                history = tr("완료 (gzip 감지)")
            else:
                history = tr("안 됨")
            item = QTreeWidgetItem([
                Path(path).name,
                f"{st['size']/1e6:,.1f} MB",
                str(st["episodes"]),
                st["compression"] or ("?" if st["error"] else "없음"),
                history,
            ])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            todo = not st["repacked"] and not st["error"]
            item.setCheckState(0, Qt.CheckState.Checked if todo else Qt.CheckState.Unchecked)
            if st["error"]:
                item.setText(4, st["error"])
                item.setDisabled(True)
            elif reasons:
                for c in range(5):
                    item.setForeground(c, Qt.GlobalColor.darkYellow)
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


