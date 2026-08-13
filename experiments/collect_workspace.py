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
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
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
    STILL_VEL,
    TASK_DEV_LIMIT,
    hdf5_files,
    load_series,
    scan_dataset,
    summarize,
)
from gello.episode_trim import plan_trim, suggest_trim, tail_speed, trim_tail  # noqa: E402
from gello.plot_widgets import BarStrip, Histogram, SeriesPlot  # noqa: E402
from gello.gui_widgets import (  # noqa: E402
    TODO_MARK,
    repo_id_error,
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
from gello.i18n import tr  # noqa: E402
from gello.libero_format import (  # noqa: E402
    default_crop_params,
    describe_episode,
    hdf5_repack_status,
    load_crop_params,
    renumber_episodes,
    resize_rgb,
    save_crop_params,
)
from gello.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402
from gello.props import load_props  # noqa: E402
from gello.robots.franka_fr3 import FR3_RESET_POSES  # noqa: E402
from gello.scene_format import (  # noqa: E402
    INSTRUCTION_ID_RE,
    SceneMetadata,
    count_by_slot,
    describe_scene,
    iter_scene_files,
    next_scene_id,
    read_scene_metadata,
    scene_filename,
)
from gello.station import load_station  # noqa: E402

LOG_DIR = Path.home() / "libero_gui_logs"
# 로봇 IP, ZMQ 주소, 카메라 스트림 포맷, 크롭 초기값은 전부 여기서 온다.
# GELLO_STATION 으로 고르고, 파일은 configs/stations/<이름>.yaml.
STATION = load_station()
PYLIBFRANKA_PYTHON = STATION.node.python_path
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parent / "launch_nodes.py")
CONVERT_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "convert_libero_to_lerobot.py")
UPLOAD_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "upload_to_hub.py")
RUNME_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "runme.sh")
# LIBERO 초기 배치 참조 이미지. 리모트에는 zip 만 올라가고(3.9MB), 풀린
# png 들은 .gitignore 의 *.png 에 걸린다. GUI 가 뜰 때 zip 이 바뀌었으면 다시
# 푼다 -- _ensure_layout_refs().
LAYOUT_ZIP = Path(__file__).resolve().parent.parent / "assets" / "libero_init_layouts.zip"
LAYOUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "libero_init_layouts"


def _new_stats() -> dict:
    """수집 카운터 한 벌. 이번 task 용과 누적용이 같은 모양이라 같은 곳에서 만든다."""
    return {"saved": 0, "success": 0, "failed": 0, "discarded": 0,
            "frames": 0, "t0": time.monotonic()}


def _grid_overlay(img):
    """1/8 간격 격자를 절반 밝기로 덧그린다 -- 수평/중앙 확인용. 사본에만."""
    out = img.copy()
    h, w = out.shape[:2]
    for i in range(1, 8):
        y, x = h * i // 8, w * i // 8
        c = 255 if i == 4 else 190        # 중앙선만 조금 더 밝게
        out[y, :] = out[y, :] // 2 + c // 2
        out[:, x] = out[:, x] // 2 + c // 2
    return out
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
    ("layout", "🎯", "Layout", "LIBERO 초기 배치와 카메라 비교"),
    ("settings", "🛠", "Settings", "언어·스키마"),
)

_DOT = {"ok": "#2ecc71", "busy": "#f39c12", "off": "#7f8c8d", "bad": "#e74c3c"}

# Panels named in the UI spec that this build does not implement yet. They are
# shown, disabled and greyed, rather than omitted: a missing tab reads as "this
# tool cannot do that", while a greyed one says "not built yet" -- and leaving
# the shape visible is what makes the gap reviewable instead of forgotten.
TODO_STYLE = "color:#6b6b6b; font-style:italic;"
# TODO_MARK 는 gello/gui_widgets.py 에서 가져온다 (순환 import 방지).

# 오른쪽 패널에서 값이 길어 좌우 배치로는 읽기 어려운 항목들.
WIDE_FIELDS = {"ds_file", "ds_task"}

# 0.5배는 접촉 순간을 한 프레임씩 보려고, 2~3배는 긴 에피소드를 훑으려고 쓴다.
# 3배면 60Hz라 프레임을 건너뛰지 않고도 타이머만으로 낼 수 있다.
PLAYBACK_SPEEDS = (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0))

# 큐레이션 기준값은 전부 gello/episode_stats.py 에 있다 (TASK_DEV_LIMIT /
# STILL_VEL). 여기서 다시 정의하지 않는 이유는, 화면에 찍히는 수와
# 판정에 쓰이는 수가 갈라지면 조작자가 둘 중 뭘 믿어야 할지 알 수 없기 때문이다.


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
            if plan["shrunk"]:
                head.setText(tr(
                    "이미 올라간 task에서 에피소드 {n}개가 삭제되었습니다. LeRobot은 게시된 "
                    "에피소드를 지울 수 없으므로, 전체를 다시 만들어 Hub을 교체해야 합니다 "
                    "(오래 걸립니다).").format(n=plan["shrunk"]))
            else:
                head.setText(tr(
                    "task {n}개의 에피소드 이력이 Hub와 어긋나 있습니다 (길이 지문 "
                    "불일치 — 지우고 다시 찍은 흔적). 이어붙이면 엉뚱한 에피소드가 "
                    "붙으므로, 전체를 다시 만들어 Hub을 교체해야 합니다.").format(
                        n=plan.get("mismatch", 0)))
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
        # 기본값은 언제나 전체 재빌드다. 큐레이션이 이미 올라간 에피소드를 지우는
        # 일이 잦은데, --resume 은 append 만 하므로 지운 에피소드의 청크가 Hub에
        # 남는다 -- 선언된 개수는 줄었는데 파일은 남은 상태가 된다. 빠른 쪽을
        # 기본으로 두면 그 상태가 기본이 된다.
        self.mode_rebuild = QRadioButton(
            tr("전체 재빌드 — 처음부터 만들어 Hub 교체 (삭제 반영, 권장)"))
        self.mode_resume = QRadioButton(
            tr("이어붙이기 — 새 에피소드만 추가 (빠르지만 지운 에피소드가 Hub에 남음)"))
        self.mode_rebuild.setChecked(True)
        self.mode_resume.setChecked(False)
        self.mode_resume.setEnabled(action in ("resume", "up_to_date"))
        mcol.addWidget(self.mode_rebuild)
        mcol.addWidget(self.mode_resume)
        resume_note = QLabel(tr(
            "이어붙이기는 에피소드를 하나도 지우지 않았을 때만 안전합니다."))
        resume_note.setStyleSheet("color:#e67e22;")
        resume_note.setWordWrap(True)
        mcol.addWidget(resume_note)
        layout.addWidget(mode)

        opts = QGroupBox(tr("함께 할 일"))
        ocol = QVBoxLayout(opts)
        # 다이얼로그를 연 시점의 판정으로 고정한다 -- steps() 는 재압축 단계가
        # 돌기 *전에* 호출되므로 그때 다시 판정해도 같지만, 두 곳이 따로 계산
        # 하면 언젠가 어긋난다.
        self._repack_todo = [p for p in plan["paths"]
                             if not hdf5_repack_status(p)["repacked"]]
        n_repack = len(self._repack_todo)
        self.repack_check = QCheckBox(
            tr("재압축 — 필요한 파일 {n}개").format(n=n_repack))
        self.repack_check.setChecked(n_repack > 0)
        self.repack_check.setEnabled(n_repack > 0)
        ocol.addWidget(self.repack_check)
        self.hdf5_check = QCheckBox(tr("원본 HDF5도 Hub에 업로드 (9GB 기준 약 15분)"))
        ocol.addWidget(self.hdf5_check)
        # 업로드 쪽은 재압축 마커 같은 로컬 기록이 없어 "Hub과 다른 파일"을
        # 스스로 고르지 못한다. 대신 이번에 재압축 대상인 파일(= 지난 재압축
        # 이후 새로 찍었거나 편집한 파일)만 올리는 선택지를 준다. 변경 없는
        # 파일은 올려도 Hub이 해시로 전송을 건너뛰지만, 그 판정에만 전체
        # 파일을 다시 읽는 시간이 든다 -- 이 체크박스는 그 시간을 없앤다.
        self.hdf5_only_new_check = QCheckBox(
            tr("  ↳ 이번에 재압축한 파일만 ({n}개) — 나머지는 Hub에 이미 있음")
            .format(n=n_repack))
        self.hdf5_only_new_check.setChecked(n_repack > 0)
        self.hdf5_only_new_check.setEnabled(False)  # hdf5_check 켜야 활성화
        self.hdf5_check.toggled.connect(
            lambda on: self.hdf5_only_new_check.setEnabled(on and n_repack > 0))
        ocol.addWidget(self.hdf5_only_new_check)
        # --only-success 체크박스는 없앴다. 이 팀 규약은 "실패는 푸시 전에
        # 파일에서 삭제"라 필터링 업로드를 쓸 일이 없고, 실수로 켜면 로컬
        # (실패 포함)과 Hub(성공만)의 에피소드 시퀀스가 어긋나 길이 지문
        # 검증(dataset_sync)과 resume 스킵 산술이 둘 다 깨진다. CLI 플래그는
        # 수동 용도로 convert_libero_to_lerobot.py 에 남아 있다.
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
        if self.repack_check.isChecked() and self._repack_todo:
            steps.append({"name": tr("재압축"), "program": sys.executable,
                          "args": [REPACK_SCRIPT, *self._repack_todo]})
        convert = [CONVERT_SCRIPT, *paths, "--repo-id", lerobot_repo, "--root", root]
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
            only_new = (self.hdf5_only_new_check.isChecked()
                        and bool(self._repack_todo))
            upload_paths = self._repack_todo if only_new else paths
            steps.append({"name": tr("HDF5 원본 업로드")
                          + (tr(" (재압축분 {n}개)").format(n=len(upload_paths))
                             if only_new else ""),
                          "program": sys.executable,
                          "args": [UPLOAD_SCRIPT, *upload_paths, "--repo-id",
                                   hdf5_repo, "--no-private"]})
        return steps


class SceneInfoView(QWidget):
    """describe_scene 출력 표시용 — 좁은 패널에서도 잘리지 않는 반응형.

    일반 문장 줄(objects, 빈 존, 설명)은 줄바꿈으로 접고, 격자 줄(│┌…)만
    고정폭 폰트의 비줄바꿈 라벨에 넣는다. 격자 라벨은 수평 크기 정책을
    Ignored 로 두어 패널 폭을 강제하지 않는다 -- 패널이 격자보다 좁으면
    격자 오른쪽이 살짝 잘릴 뿐, 다른 입력은 전부 접근 가능하게 남는다.
    """

    _GRID_CHARS = set("│┌┬┐├┼┤└┴┘─")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setStyleSheet("color:#888; font-size: 11px;")
        self._grid = QLabel("")
        # 'monospace' 별칭은 한국어 로케일에서 CJK 모노 폰트로 풀리는데, 그
        # 폰트는 격자 선문자(│─┌)를 2칸 폭으로 그려 격자가 어긋난다.
        self._grid.setStyleSheet(
            "font-family: 'DejaVu Sans Mono', 'Liberation Mono', monospace; "
            "color:#888; font-size: 10px;")
        self._grid.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        col.addWidget(self._text)
        col.addWidget(self._grid)

    def setText(self, text: str) -> None:
        grid_lines = [ln for ln in text.splitlines()
                      if set(ln) & self._GRID_CHARS]
        text_lines = [ln for ln in text.splitlines()
                      if not (set(ln) & self._GRID_CHARS)]
        self._text.setText("\n".join(text_lines))
        self._grid.setText("\n".join(grid_lines))
        self._grid.setVisible(bool(grid_lines))

    def text(self) -> str:
        return "\n".join(x for x in (self._text.text(), self._grid.text()) if x)


class NewSceneDialog(QDialog):
    """새 scene 구성 — 소품 선택 + 3×3 존 배치 + 설명.

    입력 검증은 SceneMetadata.validate 가 전담한다 (가드레일: UI 는 얇게).
    기준 사진은 여기서 찍지 않는다 — 첫 에피소드 기록이 시작될 때 agentview
    프레임이 자동 캡처된다 (libero_gui_worker 의 enqueue_set_reference).

    사용법: 왼쪽 목록에서 물체를 체크해 포함시키고, 물체를 클릭해 선택한
    상태로 오른쪽 3×3 격자 칸을 누르면 그 존에 배치된다. [0,0]=왼쪽 위.
    """

    def __init__(self, parent, scene_id: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("새 Scene 구성 — {sid}").format(sid=scene_id))
        self.setMinimumWidth(720)
        self._scene_id = scene_id
        self._placements: dict = {}
        self.metadata = None  # accept 시 SceneMetadata

        layout = QVBoxLayout(self)
        hint = QLabel(tr(
            "① 포함할 물체를 체크  ② 목록에서 물체를 클릭해 선택  "
            "③ 오른쪽 격자 칸을 눌러 그 존에 배치  ([0,0]=왼쪽 위)"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

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
            station=STATION.name,
        )

    def _refresh(self, *_args) -> None:
        checked = set(self._checked_ids())
        self._placements = {k: v for k, v in self._placements.items()
                            if k in checked}
        for (r, c), b in self.zone_buttons.items():
            here = [o.replace("OBJ-", "") for o, z in self._placements.items()
                    if z == [r, c]]
            b.setText("\n".join(here))
        try:
            self.preview.setText(describe_scene(self._build()))
        except Exception:  # noqa: BLE001 - 미완성 구성의 미리보기는 없어도 된다
            self.preview.setText("")

    def _accept(self) -> None:
        md = self._build()
        try:
            from gello.props import active_prop_ids

            md.validate(known_prop_ids=active_prop_ids())
        except ValueError as e:
            QMessageBox.warning(self, tr("Scene 구성 오류"), str(e))
            return
        self.metadata = md
        super().accept()


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

        # 두 벌을 든다. _session 은 Connect 마다 0 으로 돌아가므로 "지금 찍고 있는
        # task 를 몇 개 모았나"이고, _cumulative 는 GUI 를 켠 뒤 전체다. 예전에는
        # _session 하나뿐이었고 그것이 Connect 때 리셋되지 않아서, task 를 바꿔
        # 연결하면 이전 task 의 개수가 그대로 따라왔다 -- 게다가 상태바가
        # max(목록 길이, 연결시점 + saved) 를 쓰는 탓에 정확한 목록이 도착해도
        # 부풀려진 값이 이겨서, 빈 task 가 "에피소드 10개"로 보였다.
        self._session = _new_stats()
        self._cumulative = _new_stats()
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
        # 확정 전까지의 트림 상태. 누른 만큼 오르내리는 정수 하나면 충분하다 --
        # +/- 가 양쪽으로 있으므로 되돌리기용 이력을 따로 들 이유가 없다.
        self._trim_key: tuple | None = None
        self._trim_n_pending: int = 0
        self._trim_frames: dict = {"agent": None, "wrist": None}
        self._trim_n: int = 0
        self._trim_loader = None
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
        # 카메라별 크롭 정렬 -- 뷰 가이드·레이아웃 겹침·수집·변환이 전부 이
        # 값을 쓴다. 파일(~/libero_gui_logs/crop_params.json)에서 복원하고,
        # Layout 페이지 슬라이더가 바꾸면 저장한다.
        self._crop_params = load_crop_params()
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
            view.set_crop_guide(**self._crop_params[key])
            inner.addWidget(view)
            self.live_views[key] = view
            self.live_split.addWidget(box)
        self.live_split.setSizes([600, 600])
        live_col.addWidget(self.live_split, 1)
        self.square_guide_check = QCheckBox(tr("정사각 크롭 가이드"))
        self.square_guide_check.setChecked(True)
        self.square_guide_check.setToolTip(tr(
            "LeRobot 변환은 가운데 정사각만 남깁니다. 켜면 그 바깥이 어둡게 표시됩니다."))
        self.square_guide_check.toggled.connect(self._on_square_guide)
        live.layout().addWidget(self.square_guide_check)
        self._live_tab_index = self.center_tabs.addTab(live, tr("Live"))

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
            view.set_crop_guide(**self._crop_params[key])
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
        self._trim_tab_index = self.center_tabs.addTab(self._build_trim_tab(), tr("Trim"))
        self._layout_tab_index = self.center_tabs.addTab(
            self._build_layout_tab(), tr("레이아웃"))
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)
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
            # 페이지가 창보다 길어지면(예: Configure 의 scene 그룹) 세로
            # 스크롤. 가로 스크롤은 쓰지 않는다 -- 내용이 패널 폭에 맞게
            # 접히는 것이 원칙이다 (긴 한 줄 표시는 SceneInfoView 처럼 줄바꿈
            # 또는 Ignored 정책으로 해결).
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            col.addWidget(scroll, 1)
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

        # ---- scene-v1 이 유일한 수집 방식이다 (2026-08-13, legacy 수집 UI
        # 제거). 파일 하나 = 책상 배치(scene) 하나, instruction 은 에피소드마다
        # 기록되고 수집 중에 바꿀 수 있다. legacy *_demo.hdf5 는 더 이상 새로
        # 만들지 않지만 변환·업로드·재생 등 데이터 관리 기능은 그대로 남는다.
        scene = QGroupBox(tr("Scene 수집 (scene-v1)"))
        self.task_box = scene  # 연습 모드 토글이 잠그는 그룹 (기존 이름 유지)
        sc_form = QFormLayout(scene)
        scene_row = QWidget()
        srow = QHBoxLayout(scene_row)
        srow.setContentsMargins(0, 0, 0, 0)
        self.scene_combo = QComboBox()
        self.scene_combo.currentIndexChanged.connect(self._on_scene_selected)
        srow.addWidget(self.scene_combo, 1)
        self.scene_refresh_btn = QPushButton(tr("새로고침"))
        self.scene_refresh_btn.setMaximumWidth(80)
        self.scene_refresh_btn.clicked.connect(self._refresh_scene_combo)
        srow.addWidget(self.scene_refresh_btn)
        sc_form.addRow(tr("Scene"), scene_row)
        self.scene_new_btn = QPushButton(tr("새 Scene 구성..."))
        self.scene_new_btn.clicked.connect(self._on_new_scene)
        sc_form.addRow(self.scene_new_btn)
        self.lang_edit = QLineEdit()
        self.lang_edit.setPlaceholderText(tr("예) pick up the blue cup and place it on the blue bowl"))
        self.lang_edit.setText(self._recents.most_recent("language", ""))
        sc_form.addRow(tr("시작 문장"), self.lang_edit)
        self.scene_iid_edit = QLineEdit(self._recents.most_recent("instruction_id", "I000"))
        self.scene_iid_edit.setToolTip(tr("시작 slot 의 instruction ID (예: I000). "
                                          "수집 중 Collect 페이지에서 바꿀 수 있습니다."))
        sc_form.addRow(tr("시작 slot ID"), self.scene_iid_edit)
        self.collector_edit = QLineEdit(self._recents.most_recent("collector", ""))
        self.collector_edit.setPlaceholderText(tr("수집자 식별자 (필수 attr, 예: gibeom)"))
        sc_form.addRow(tr("수집자"), self.collector_edit)
        root_row = QWidget()
        rl = QHBoxLayout(root_row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.root_edit = QLineEdit(self._recents.most_recent(
            "data_root", str(Path.home() / "libero_datasets")))
        self.root_edit.editingFinished.connect(self._refresh_scene_combo)
        rl.addWidget(self.root_edit, 1)
        browse = QPushButton(tr("..."))
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse_root)
        rl.addWidget(browse)
        sc_form.addRow(tr("저장 경로"), root_row)
        self.scene_info = SceneInfoView()
        sc_form.addRow(self.scene_info)
        self._pending_scene_meta = None
        self._scene_session = False
        col.addWidget(scene)

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
        self.preview_btn = QPushButton(tr("미리보기 시작"))
        self.preview_btn.clicked.connect(self._on_toggle_previews)
        cform.addRow(self.preview_btn)
        self.camera_hint = QLabel("")
        self.camera_hint.setStyleSheet("color:#888;")
        self.camera_hint.setWordWrap(True)
        cform.addRow(self.camera_hint)
        col.addWidget(cam)

        # "세션"이 아니라 "수집 설정": 여기 있는 것은 전부 Connect 시점에
        # 적용되는 수집 방식이다. 연습 모드도 그중 하나라 별도 "모드" 그룹을
        # 두지 않고 여기에 둔다.
        sess = QGroupBox(tr("수집 설정"))
        sform = QFormLayout(sess)
        self.no_dataset_check = QCheckBox(tr("데이터셋 없이 조작만 (연습 / 씬 세팅)"))
        self.no_dataset_check.setToolTip(tr(
            "파일을 전혀 만들지 않고 텔레옵만 합니다. 자세 게이트·카메라·프레임 "
            "카운터는 그대로 동작하고, 저장을 눌러도 버려집니다."))
        self.no_dataset_check.toggled.connect(self._on_no_dataset_toggled)
        sform.addRow(self.no_dataset_check)
        self.mode_hint = QLabel("")
        self.mode_hint.setStyleSheet("color:#888;")
        self.mode_hint.setWordWrap(True)
        sform.addRow(self.mode_hint)
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
        self.wall_check = QCheckBox(tr("관절 한계 벽 사용"))
        self.wall_check.setChecked(True)
        sform.addRow(self.wall_check)
        self.match_check = QCheckBox(tr("에피소드마다 리더를 리셋 포즈로 정렬"))
        self.match_check.setChecked(True)
        sform.addRow(self.match_check)
        col.addWidget(sess)
        col.addStretch()
        self._refresh_scene_combo()
        return w

    # ------------------------------------------------------- scene 수집 UI
    def _refresh_scene_combo(self) -> None:
        """저장 경로의 scene_*.hdf5 목록. 파일명이 아니라 내부 metadata 로
        표시한다 (경로 역산 금지)."""
        self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        root = Path(self.root_edit.text().strip() or ".")
        try:
            sid_next = next_scene_id(root)
        except Exception:  # noqa: BLE001
            sid_next = "S???"
        self.scene_combo.addItem(tr("— 새 Scene ({sid}) —").format(sid=sid_next), None)
        try:
            for p in iter_scene_files(root):
                try:
                    md = read_scene_metadata(p)
                except Exception as e:  # noqa: BLE001
                    self.scene_combo.addItem(f"{p.name} (읽기 실패: {type(e).__name__})", None)
                    continue
                label = f"{md.scene_id} · 물체 {len(md.objects)}개"
                if md.description:
                    label += f" · {md.description[:28]}"
                self.scene_combo.addItem(label, md.scene_id)
        except Exception:  # noqa: BLE001
            pass
        self.scene_combo.blockSignals(False)
        self._on_scene_selected()

    def _on_scene_selected(self, *_args) -> None:
        sid = self.scene_combo.currentData()
        self.scene_new_btn.setEnabled(sid is None)
        if sid is None:
            if self._pending_scene_meta is not None:
                self.scene_info.setText(
                    describe_scene(self._pending_scene_meta)
                    + "\n" + tr("(연결하면 이 구성으로 새 scene 파일이 만들어집니다)"))
            else:
                self.scene_info.setText(
                    tr("'새 Scene 구성...'으로 물체 배치를 정의하세요."))
            return
        root = Path(self.root_edit.text().strip() or ".")
        try:
            path = root / scene_filename(sid)
            md = read_scene_metadata(path)
            counts = count_by_slot(path)
            lines = [describe_scene(md)]
            if counts:
                lines.append("slot: " + "  ".join(
                    f"{iid} {c['usable']}/{c['total']}" for iid, c in sorted(counts.items())))
            self.scene_info.setText("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            self.scene_info.setText(f"(scene 정보 읽기 실패: {type(e).__name__}: {e})")

    def _on_new_scene(self) -> None:
        root = Path(self.root_edit.text().strip() or ".")
        try:
            sid = next_scene_id(root)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("경로 오류"),
                                tr("저장 경로를 확인하세요: {e}").format(e=e))
            return
        dlg = NewSceneDialog(self, sid)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.metadata is not None:
            self._pending_scene_meta = dlg.metadata
            self._on_scene_selected()

    def _scene_config_from_ui(self):
        """Connect 시점의 scene 설정 검증. (meta, scene_id, resume, error) --
        error 가 None 이 아니면 연결을 중단하고 그 메시지를 보여준다."""
        lang = self.lang_edit.text().strip()
        iid = self.scene_iid_edit.text().strip()
        collector = self.collector_edit.text().strip()
        if not lang:
            return None, None, False, tr("시작 instruction 문장을 Language 칸에 입력하세요.")
        if lang.startswith('"') and lang.endswith('"'):
            return None, None, False, tr("instruction 은 따옴표 없는 순수 문장이어야 합니다.")
        if not INSTRUCTION_ID_RE.match(iid):
            return None, None, False, tr("시작 slot ID 형식이 틀렸습니다 (예: I000).")
        if not collector:
            return None, None, False, tr("수집자 식별자를 입력하세요 (에피소드 필수 attr).")
        sid = self.scene_combo.currentData()
        if sid is None:
            if self._pending_scene_meta is None:
                return None, None, False, tr(
                    "'새 Scene 구성...'으로 배치를 먼저 정의하거나 기존 scene 을 고르세요.")
            return self._pending_scene_meta, None, False, None
        return None, sid, True, None

    def _on_apply_slot(self) -> None:
        """scene 세션 중 slot 전환 -- worker 의 cmd_set_slot 호출만 한다."""
        if self.worker is None or not self._scene_session:
            return
        iid = self.slot_iid_edit.text().strip()
        instr = self.slot_instr_edit.text().strip()
        if not INSTRUCTION_ID_RE.match(iid):
            QMessageBox.warning(self, tr("slot 오류"),
                                tr("instruction ID 형식이 틀렸습니다 (예: I000)."))
            return
        if not instr or (instr.startswith('"') and instr.endswith('"')):
            QMessageBox.warning(self, tr("slot 오류"),
                                tr("따옴표 없는 순수 문장을 입력하세요."))
            return
        self.worker.cmd_set_slot(instr, iid)
        self.slot_current_label.setText(f"{iid}: {instr}")
        self._recents.add("instruction_id", iid)
        self._recents.add("language", instr)

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

        # scene 세션 전용: 다음 에피소드부터 수행할 slot(instruction) 전환.
        # 진행 중 에피소드에는 영향이 없다 (worker 가 기록 시작 시점에 캡처).
        slot = QGroupBox(tr("Scene slot — 현재 instruction"))
        self.slot_box = slot
        sfrm = QFormLayout(slot)
        self.slot_current_label = QLabel("")
        self.slot_current_label.setWordWrap(True)
        sfrm.addRow(tr("현재"), self.slot_current_label)
        self.slot_iid_edit = QLineEdit()
        sfrm.addRow(tr("instruction ID"), self.slot_iid_edit)
        self.slot_instr_edit = QLineEdit()
        sfrm.addRow(tr("문장"), self.slot_instr_edit)
        self.slot_apply_btn = QPushButton(tr("slot 적용 (다음 에피소드부터)"))
        self.slot_apply_btn.clicked.connect(self._on_apply_slot)
        sfrm.addRow(self.slot_apply_btn)
        slot.setVisible(False)
        col.addWidget(slot)

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
        # 파일 삭제는 여기 없다. 에피소드 삭제 바로 옆에 두었더니 실제로 오클릭이
        # 났고, 한 번에 태스크 하나가 통째로 날아간다. 되돌릴 수 없는 조작은
        # 한 단계 더 들어가야 닿도록 Dataset 메뉴에만 둔다.
        #
        # 두 줄로 나누고 삭제만 떼어놓는 이유는 폭이 아니라 종류다. 위 네 개는
        # 읽거나 고르기만 하고, 아래 하나만 파일을 바꾼다 -- 한 줄에 다섯 개가
        # 나란히 있으면 그 차이가 라벨 글자에만 남는다.
        for pair in ((("새로고침", self._refresh_dataset_tree,
                       "데이터 폴더를 다시 읽어 목록을 새로 그립니다."),
                      ("구조 확인", self._on_show_structure,
                       "선택한 *파일*의 에피소드 수·용량·이미지 압축·재압축 이력과\n"
                       "첫 에피소드의 데이터 구조를 보여줍니다.")),
                     (("실패만 선택", self._on_select_failed,
                       "success=False 로 표시된 에피소드를 모두 선택합니다.\n"
                       "선택만 하고 지우지 않습니다."),
                      ("튀는 것만 선택", self._on_select_jerky,
                       "같은 task 평균과 ±{d} 넘게 차이 나는 에피소드를 모두 선택합니다.\n"
                       "선택만 하고 지우지 않습니다. (Analysis 탭과 같은 기준)"))):
            row = QHBoxLayout()
            for text, slot, tip in pair:
                b = QPushButton(tr(text))
                b.setToolTip(tr(tip).format(d=TASK_DEV_LIMIT))
                b.clicked.connect(slot)
                row.addWidget(b)
            col.addLayout(row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("color:#444;")
        col.addWidget(line)

        trim_btn = QPushButton(tr("끝 다듬기 (Trim 탭에서)"))
        trim_btn.setToolTip(tr(
            "선택한 에피소드를 Trim 탭에서 엽니다.\n"
            "저장 키를 누를 때 흔들린 마지막 몇 프레임을 잘라냅니다."))
        trim_btn.clicked.connect(self._on_open_trim)
        col.addWidget(trim_btn)

        del_btn = QPushButton(tr("선택한 에피소드 삭제"))
        del_btn.setToolTip(tr(
            "위에서 선택한 에피소드를 .hdf5 에서 실제로 지우고 번호를 다시 매깁니다.\n"
            "되돌릴 수 없습니다. 수집 중이 아닌 파일이면 세션 없이도 삭제됩니다.\n"
            "파일 통째 삭제는 Dataset 메뉴에 있습니다."))
        del_btn.setStyleSheet("background-color:#c0392b; color:white; padding:6px;")
        del_btn.clicked.connect(self._on_delete_selected)
        col.addWidget(del_btn)

        # 빈 채로 시작한다. 고정 안내문은 매번 같은 말을 차지하기만 했고, 정작
        # 알아야 할 것("N개 선택됨")은 누른 뒤에만 생긴다.
        self.dataset_hint = QLabel("")
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
        acct_btn.setToolTip(tr("이 PC는 공용입니다. 지금 어떤 토큰으로 올라가는지 "
                               "확인하고, 다른 사람 계정으로 바꿉니다."))
        acct_btn.clicked.connect(self._on_hf_accounts)
        col.addWidget(acct_btn)

        # Repo ID 를 패널 밖으로 꺼내둔다. 다이얼로그 안에만 있을 때는 오타가
        # Recents 에 저장돼도 아무데도 보이지 않고, 자동 버튼이 그걸 그대로 다시
        # 쓴다 -- 실제로 'r/lerobot' 이 저장된 채 재압축 15.6분을 돌고 마지막
        # 업로드에서 403 으로 죽었다. 여기 있으면 누르기 전에 눈에 띈다.
        self.repo_edits = {}
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        for key, label, tip in (
            ("repo_id", tr("LeRobot repo"),
             tr("변환본이 올라갈 저장소. <조직 또는 사용자>/<이름>")),
            ("hdf5_repo_id", tr("HDF5 repo"),
             tr("원본 .hdf5 가 올라갈 저장소. 변환본과 별개입니다.")),
        ):
            e = QLineEdit(self._recents_valid_repo(key))
            e.setPlaceholderText(tr("<조직 또는 사용자>/<이름>"))
            e.setToolTip(tip)
            e.textChanged.connect(self._on_repo_edited)
            self.repo_edits[key] = e
            form.addRow(QLabel(label), e)
        col.addLayout(form)
        self.repo_warn = QLabel("")
        self.repo_warn.setStyleSheet("color:#e67e22;")
        self.repo_warn.setWordWrap(True)
        col.addWidget(self.repo_warn)
        self._on_repo_edited()

        # 세 묶음으로 나눈다. 위에서 아래로 갈수록 범위가 좁아진다 --
        # 전부 / 원본(HDF5)만 / 변환본(LeRobot)만. 묶음마다 첫 줄이 "자동"이고
        # 그 아래가 같은 일을 쪼갠 수동 단계라, 어느 버튼이 어느 버튼을 포함하는지
        # 위치만 봐도 읽힌다.
        pipe_btn = self._upload_button(
            col, tr("전체 처리 (재압축 → 변환 → 업로드)"),
            tr("Hub과 로컬을 대조해 필요한 것만 순서대로 실행합니다.\n"
               "재압축 → LeRobot 변환 → LeRobot 업로드까지 한 번에.\n"
               "확인 창에서 시작을 누르면 끝까지 무인으로 진행합니다."),
            self._on_pipeline, primary=True, color="#2ecc71")

        col.addSpacing(14)
        hdf5_box = QGroupBox(tr("HDF5 원본"))
        hcol = QVBoxLayout(hdf5_box)
        hcol.setSpacing(6)
        self._upload_button(
            hcol, tr("재압축 + 업로드 (자동)"),
            tr("아래 두 단계를 순서대로 실행합니다.\n"
               "재압축이 필요한 파일만 골라 줄인 뒤, 원본 .hdf5 를 Hub에 올립니다."),
            self._on_hdf5_auto, primary=True, color="#9b59b6")
        self._upload_button(
            hcol, tr("용량 최적화 (재압축)"),
            tr("lzf 압축으로 .hdf5 크기를 줄입니다. 내용은 그대로입니다.\n"
               "이미 재압축된 파일은 건너뜁니다."),
            self._on_repack)
        self._upload_button(
            hcol, tr("원본 업로드..."),
            tr("큐레이션이 끝난 .hdf5 를 그대로 Hub에 올립니다.\n"
               "변환본(LeRobot)과는 별개의 저장소입니다."),
            self._on_hdf5_upload)
        col.addWidget(hdf5_box)

        col.addSpacing(14)
        lerobot_box = QGroupBox(tr("LeRobot 변환본"))
        lcol = QVBoxLayout(lerobot_box)
        lcol.setSpacing(6)
        self._upload_button(
            lcol, tr("변환 + 업로드 (자동)"),
            tr("전체를 처음부터 다시 만들어 Hub을 통째로 교체합니다.\n"
               "이어붙이기(resume)를 쓰지 않으므로, 큐레이션에서 지운 에피소드가 "
               "Hub에서도 사라집니다.\n실행 전에 항상 확인 창을 띄웁니다."),
            self._on_lerobot_auto, primary=True, color="#3498db")
        self._upload_button(
            lcol, tr("이어붙이기 (새 에피소드만)"),
            tr("Hub과 대조해 새로 추가된 에피소드만 변환해 이어붙입니다.\n"
               "5~10개씩 추가 수집한 날은 전체 재빌드 대신 이걸로 몇 분이면 "
               "끝납니다.\n에피소드를 삭제·편집한 흔적이 있으면 안전하게 거부하고 "
               "전체 재빌드를 안내합니다."),
            self._on_lerobot_resume, primary=True, color="#1abc9c")
        self._upload_button(
            lcol, tr("HDF5 골라서 변환만..."),
            tr("올리지 않고 로컬에만 변환합니다.\n"
               "결과를 눈으로 확인한 뒤 아래 버튼으로 올리세요."),
            self._on_lerobot)
        self._upload_button(
            lcol, tr("전체 task 다시 업로드..."),
            tr("이미 변환해둔 로컬 결과를 Hub에 통째로 교체 업로드합니다 "
               "(재변환 없음).\n로컬에 없는 원격 파일도 함께 지우므로, 큐레이션으로 "
               "삭제한 에피소드가 Hub에 남지 않습니다."),
            self._on_lerobot_reupload)
        col.addWidget(lerobot_box)

        col.addSpacing(10)
        note = QLabel(tr(
            "'변환 + 업로드 (자동)'은 전체를 새로 만들어 교체합니다 — 큐레이션으로 "
            "지운 에피소드를 Hub에서도 없애는 유일한 방법입니다. 추가만 한 날은 "
            "'이어붙이기'가 새 에피소드만 변환해서 훨씬 빠릅니다."))
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

    def _recents_valid_repo(self, key: str) -> str:
        """Most recent stored id that actually parses -- a bad one is skipped.

        Recents keeps whatever was last typed, so once a typo lands there it
        becomes the default forever. Falling back to the newest *valid* entry
        means a bad run does not poison the next one.
        """
        for v in self._recents.get(key):
            if repo_id_error(v) is None:
                return v
        return ""

    def repo_id_for(self, key: str) -> str:
        return self.repo_edits[key].text().strip()

    def _on_repo_edited(self) -> None:
        msgs = []
        for key, label in (("repo_id", "LeRobot"), ("hdf5_repo_id", "HDF5")):
            e = self.repo_edits[key]
            err = repo_id_error(e.text().strip())
            # 비어 있는 것은 경고하지 않는다 -- 쓰지 않는 저장소일 수 있고,
            # 실제로 필요할 때 각 버튼이 막는다.
            if err and e.text().strip():
                msgs.append(f"{label}: {err}")
            e.setStyleSheet("" if not err else "border:1px solid #e67e22;")
        self.repo_warn.setText("\n".join(msgs))

    def _check_repo(self, key: str, what: str) -> "str | None":
        """Returns the id, or None after telling the operator what is wrong."""
        repo = self.repo_id_for(key)
        err = repo_id_error(repo)
        if err:
            QMessageBox.warning(self, tr("Repo ID 오류"),
                                tr("{w} 을(를) 시작할 수 없습니다.\n\n{e}").format(w=what, e=err))
            return None
        self._recents.add(key, repo)
        return repo

    def _upload_button(self, layout, text: str, tip: str, slot,
                       primary: bool = False, color: str = "") -> QPushButton:
        """One Upload-panel button. `primary` marks the automatic one in a group.

        Only the group's automatic button is coloured. Colouring every button
        made the panel read as five equally urgent actions, when in fact each
        group is one recommended path plus the manual steps it is made of.
        """
        b = QPushButton(text)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        if primary:
            b.setStyleSheet(f"background-color:{color}; color:white; "
                            "font-weight:bold; padding:7px;")
        layout.addWidget(b)
        return b

    def _page_stats(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        # 두 열: 왼쪽은 지금 찍고 있는 task, 오른쪽은 GUI 를 켠 뒤 전체.
        # task 를 여러 개 도는 세션에서 "이 task 를 몇 개 모았나"와 "오늘 총
        # 몇 개인가"는 서로 다른 질문이고, 한 열만 두면 둘 중 하나를 못 본다.
        self.stats_labels = {}
        self.stats_total_labels = {}
        box = QGroupBox(tr("수집 현황"))
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)
        self.stats_task_header = QLabel(tr("이번 task"))
        for c, head in ((1, self.stats_task_header), (2, QLabel(tr("누적")))):
            head.setStyleSheet("color:#888;")
            head.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(head, 0, c)
        for row, (key, label) in enumerate((
                ("saved", "저장된 에피소드"), ("success", "성공"),
                ("failed", "실패"), ("discarded", "버림"),
                ("frames", "총 프레임"), ("elapsed", "경과 시간"),
                ("rate", "분당 에피소드")), start=1):
            grid.addWidget(QLabel(tr(label)), row, 0)
            for c, store in ((1, self.stats_labels), (2, self.stats_total_labels)):
                lab = QLabel("-")
                lab.setAlignment(Qt.AlignmentFlag.AlignRight)
                # 이번 task 쪽만 굵게. 수집 중에 눈이 가야 할 것은 이쪽이다.
                if c == 1:
                    lab.setFont(QFont("", 10, QFont.Weight.Bold))
                else:
                    lab.setStyleSheet("color:#888;")
                grid.addWidget(lab, row, c)
                store[key] = lab
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
    def _on_open_trim(self) -> None:
        items = [i for i in self.dataset_tree.selectedItems() if i.parent() is not None]
        if not items:
            QMessageBox.information(self, tr("선택 필요"),
                                    tr("에피소드를 하나 선택하세요 (파일이 아니라)."))
            return
        it = items[0]
        path = it.parent().data(0, Qt.ItemDataRole.UserRole)
        self._show_trim_for(path, it.data(0, Qt.ItemDataRole.UserRole))
        self.center_tabs.setCurrentIndex(self._trim_tab_index)

    # ------------------------------------------------------------------ Trim
    def _show_trim_for(self, path: str, demo: str) -> None:
        """Dataset 트리와 Analysis 순위표가 공유하는 트림 진입점."""
        if not path or not demo:
            return
        if self.active_file_path is not None and Path(path) == self.active_file_path:
            self.trim_summary.setText(tr("수집 중인 파일은 편집할 수 없습니다."))
            return
        self._trim_key = (path, demo)
        self._trim_n_pending = 0
        try:
            series = load_series(path, demo)
        except Exception as e:  # noqa: BLE001
            self.trim_summary.setText(tr("불러오기 실패: {e}").format(e=e))
            return
        self._trim_series = series
        self._trim_n = int(series["n"])
        for plot, dims in self.trim_plots.values():
            plot.set_data(series, dims)
        self._trim_frames = {"agent": None, "wrist": None}
        for v in self.trim_views.values():
            v.clear_frame(tr("영상 불러오는 중..."))
        if self._trim_loader is not None:
            self._trim_loader.wait()
        self._trim_loader = EpisodeLoadWorker(path, demo)
        self._trim_loader.loaded.connect(self._on_trim_loaded)
        self._trim_loader.failed.connect(
            lambda m: [v.clear_frame(tr("영상 없음")) for v in self.trim_views.values()])
        self._trim_loader.start()
        self._trim_update()

    @pyqtSlot(str, str, object, object)
    def _on_trim_loaded(self, path, demo, agent, wrist) -> None:
        if self._trim_key != (path, demo):
            return
        self._trim_frames = {"agent": agent, "wrist": wrist}
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_pending(self) -> int:
        return self._trim_n_pending

    def _trim_keep(self) -> int:
        return max(0, self._trim_n - self._trim_pending())

    def _trim_add(self, n: int) -> None:
        """+/- 를 누른 만큼 옮긴다. 0 아래로는 못 간다 -- 원본보다 길어질 수 없다."""
        if self._trim_key is None:
            return
        self._trim_n_pending = max(0, self._trim_n_pending + n)
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_reset(self) -> None:
        """정정 -- 고른 것을 통째로 0으로. 한 단계씩 물리는 것보다, 잘못 짚었을 때
        처음부터 다시 보는 쪽이 실제 흐름에 맞는다."""
        if self._trim_key is None:
            return
        self._trim_n_pending = 0
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_suggest(self) -> None:
        if self._trim_key is None:
            return
        n = suggest_trim(*self._trim_key)
        self._trim_n_pending = n
        self.log(f"[트림] 추천 {n}프레임" + ("" if n else " (이미 조용하게 끝납니다)"))
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_seek(self, i: int) -> None:
        n = self._trim_n
        if n <= 0:
            return
        i = max(0, min(n - 1, i))
        self.trim_slider.blockSignals(True)
        self.trim_slider.setRange(0, n - 1)
        self.trim_slider.setValue(i)
        self.trim_slider.blockSignals(False)
        self._trim_show_frame(i)

    def _trim_show_frame(self, i: int) -> None:
        keep = self._trim_keep()
        for role, v in self.trim_views.items():
            arr = self._trim_frames.get(role)
            if arr is None or len(arr) == 0:
                continue
            v.set_frame(arr[min(i, len(arr) - 1)])
        mark = tr(" ← 잘린 뒤 마지막") if i == keep - 1 else (
            tr("  (잘려나갈 구간)") if i >= keep else "")
        self.trim_pos.setText(f"{i + 1}/{self._trim_n}{mark}")
        for plot, _ in self.trim_plots.values():
            plot.set_cursor(i)

    def _on_trim_scrub(self, i: int) -> None:
        self._trim_show_frame(i)

    def _on_trim_play(self) -> None:
        """잘린 뒤 구간만 훑는다 -- 확인하려는 것이 '새 끝'이기 때문이다."""
        if self._trim_key is None:
            return
        keep = self._trim_keep()
        self._trim_seek(max(0, keep - 40))
        if not hasattr(self, "_trim_timer"):
            self._trim_timer = QTimer(self)
            self._trim_timer.setInterval(50)
            self._trim_timer.timeout.connect(self._trim_tick)
        self._trim_timer.start()
        self.trim_play_btn.setText(tr("정지"))

    def _trim_tick(self) -> None:
        i = self.trim_slider.value() + 1
        if i >= self._trim_keep():
            self._trim_timer.stop()
            self.trim_play_btn.setText(tr("재생"))
            return
        self._trim_seek(i)

    def _trim_update(self) -> None:
        """Recomputes every label, guard and shading from the pending count."""
        has = self._trim_key is not None
        self.trim_play_btn.setEnabled(has and self._trim_frames.get("agent") is not None)
        self.trim_slider.setEnabled(has)
        self.trim_reset_btn.setEnabled(bool(self._trim_n_pending))
        if not has:
            self.trim_count.setText(tr("에피소드를 고르세요"))
            self.trim_apply_btn.setEnabled(False)
            self.trim_warn.setText("")
            for plot, _ in self.trim_plots.values():
                plot.set_cut(None)
            return
        path, demo = self._trim_key
        n_trim, keep = self._trim_pending(), self._trim_keep()
        plan = plan_trim(path, [demo], max(n_trim, 1))[0]
        self.trim_summary.setText(
            tr("{d} · {n}프레임 ({s:.1f}s) · 마지막 그리퍼 동작 −{g}프레임").format(
                d=demo, n=self._trim_n, s=self._trim_n / 20.0,
                g=plan.gripper_tail if plan.gripper_tail is not None else "?"))
        self.trim_count.setText(
            tr("{a} → {b} 프레임   (−{n})").format(a=self._trim_n, b=keep, n=n_trim)
            if n_trim else tr("{a} 프레임 — 자를 구간 없음").format(a=self._trim_n))
        for plot, _ in self.trim_plots.values():
            plot.set_cut(keep if n_trim else None)
        blocked = plan_trim(path, [demo], n_trim)[0].blocked if n_trim else None
        self.trim_apply_btn.setEnabled(bool(n_trim) and not blocked)
        if blocked:
            self.trim_warn.setText(tr("⚠ {b}").format(b=blocked))
        elif plan.already:
            self.trim_warn.setText(tr("이미 다듬은 이력: {a}").format(a=plan.already))
        else:
            self.trim_warn.setText("")

    def _trim_apply(self) -> None:
        if self._trim_key is None or not self._trim_pending():
            return
        path, demo = self._trim_key
        n_trim, keep = self._trim_pending(), self._trim_keep()
        if QMessageBox.question(
                self, tr("끝 다듬기 확정"),
                tr("{f}\n{d}\n\n{a} → {b} 프레임 (뒤에서 {n}개 삭제)\n\n"
                   "되돌릴 수 없습니다. 진행할까요?").format(
                       f=Path(path).name, d=demo, a=self._trim_n, b=keep, n=n_trim),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            new_n = trim_tail(path, demo, n_trim)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr("다듬기 실패"), f"{type(e).__name__}: {e}")
            self.log(f"[트림 실패] {Path(path).name} {demo}: {type(e).__name__}: {e}")
            return
        self.log(f"[트림] {Path(path).name} {demo}: {self._trim_n} → {new_n}프레임 "
                 f"(−{n_trim})")
        self._refresh_dataset_tree()
        self._refresh_analysis(force=True)
        self._show_trim_for(path, demo)

    def _build_trim_tab(self) -> QWidget:
        """Analysis's layout, aimed at one question: where should this take end.

        The plots are the same five as Analysis -- the tail wobble is visible
        there as clearly as anywhere -- but the right column is the episode's
        own video instead of dataset-wide statistics, because the check that
        actually matters ("did I cut the release?") is a thing you look at, not
        a number. Nothing is written until 확정; every button before that only
        moves a pending count.
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 4, 4, 4)
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lcol = QVBoxLayout(left)
        lcol.setContentsMargins(0, 0, 0, 0)
        self.trim_summary = QLabel(tr("Dataset 트리나 Analysis 순위표에서 에피소드를 고르세요."))
        self.trim_summary.setWordWrap(True)
        self.trim_summary.setStyleSheet("font-weight:bold;")
        lcol.addWidget(self.trim_summary)

        grid = QGridLayout()
        self.trim_plots = {}
        for i, (title, dims) in enumerate((
            ("joint1.pos, joint2.pos", [(0, "joint1.pos"), (1, "joint2.pos")]),
            ("joint4.pos, joint5.pos", [(3, "joint4.pos"), (4, "joint5.pos")]),
            ("joint6.pos, joint7.pos", [(5, "joint6.pos"), (6, "joint7.pos")]),
            ("joint3.pos", [(2, "joint3.pos")]),
            ("gripper.pos", [(7, "gripper.pos")]),
        )):
            plot = SeriesPlot(title)
            self.trim_plots[title] = (plot, dims)
            grid.addWidget(plot, i // 2, i % 2)
        lcol.addLayout(grid, 1)
        legend = QLabel(tr("실선 observation.state   ┄ 파선 observation.commanded_state"
                           "   ┈ 점선 action     ▨ 빨간 음영 = 잘려나갈 구간"))
        legend.setStyleSheet("color:#888;")
        lcol.addWidget(legend)
        split.addWidget(left)

        right = QWidget()
        rcol = QVBoxLayout(right)
        rcol.setContentsMargins(0, 0, 0, 0)

        vids = QHBoxLayout()
        self.trim_views = {}
        for role, cap in (("agent", tr("agent")), ("wrist", tr("wrist"))):
            box = QVBoxLayout()
            v = VideoView()
            v.clear_frame(tr("에피소드를 선택하세요"))
            v.set_crop_guide(**self._crop_params[role])
            self.trim_views[role] = v
            box.addWidget(v, 1)
            lab = QLabel(cap)
            lab.setStyleSheet("color:#888;")
            lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(lab)
            vids.addLayout(box, 1)
        rcol.addLayout(vids, 1)

        # 슬라이더는 '지금 몇 번째 프레임을 보고 있나'다. 자를 지점을 정하는
        # 것과 별개로, 잘린 뒤 마지막 프레임이 어떤 장면인지 눈으로 확인해야
        # 하기 때문에 재생/스크럽을 그대로 둔다.
        srow = QHBoxLayout()
        self.trim_play_btn = QPushButton(tr("재생"))
        self.trim_play_btn.setEnabled(False)
        self.trim_play_btn.clicked.connect(self._on_trim_play)
        srow.addWidget(self.trim_play_btn)
        self.trim_slider = QSlider(Qt.Orientation.Horizontal)
        self.trim_slider.setEnabled(False)
        self.trim_slider.valueChanged.connect(self._on_trim_scrub)
        srow.addWidget(self.trim_slider, 1)
        self.trim_pos = QLabel("-/-")
        self.trim_pos.setMinimumWidth(72)
        srow.addWidget(self.trim_pos)
        rcol.addLayout(srow)

        box = QGroupBox(tr("끝 다듬기"))
        bcol = QVBoxLayout(box)
        self.trim_count = QLabel(tr("에피소드를 고르세요"))
        self.trim_count.setStyleSheet("font-size:15px; font-weight:bold;")
        self.trim_count.setWordWrap(True)
        bcol.addWidget(self.trim_count)

        # 누른 만큼 쌓이고, + 로 되물린다. -1..-20 을 늘어놓는 대신 네 개만 두면
        # 한 자리에서 오르내릴 수 있어 "몇 번 눌렀더라"를 셀 필요가 없다.
        step_row = QHBoxLayout()
        # 라벨의 부호는 *에피소드 길이* 기준이다: "−5" 는 5프레임 짧아진다는 뜻이라
        # 자를 양(pending)은 +5 만큼 는다. 둘을 같은 부호로 두면 −5 가 되돌리기가
        # 되어 버린다.
        for label, n in ((tr("−5"), 5), (tr("−1"), 1), (tr("+1"), -1), (tr("+5"), -5)):
            b = QPushButton(label)
            b.setToolTip(
                tr("누를 때마다 {n}프레임씩 더 자릅니다 (아직 파일은 그대로)")
                .format(n=n) if n > 0 else
                tr("누를 때마다 {n}프레임씩 되돌립니다 (원본 길이 이상으로는 안 갑니다)")
                .format(n=-n))
            b.clicked.connect(lambda _=False, k=n: self._trim_add(k))
            step_row.addWidget(b)
        bcol.addLayout(step_row)

        act_row = QHBoxLayout()
        sug = QPushButton(tr("추천"))
        sug.setToolTip(tr("끝에서부터 속도가 그 에피소드 중앙값 아래로 떨어지는 "
                          "지점까지를 제안합니다 (최대 15프레임)"))
        sug.clicked.connect(self._trim_suggest)
        act_row.addWidget(sug)
        self.trim_reset_btn = QPushButton(tr("정정"))
        self.trim_reset_btn.setToolTip(tr("고른 프레임 수를 0으로 되돌립니다. "
                                          "확정 전에는 파일이 바뀌지 않습니다."))
        self.trim_reset_btn.clicked.connect(self._trim_reset)
        act_row.addWidget(self.trim_reset_btn)
        self.trim_apply_btn = QPushButton(tr("확정 (파일에 적용)"))
        self.trim_apply_btn.setStyleSheet("background-color:#c0392b; color:white; padding:6px;")
        self.trim_apply_btn.setToolTip(tr("여기서부터 .hdf5 가 실제로 바뀝니다. "
                                          "되돌릴 수 없습니다."))
        self.trim_apply_btn.clicked.connect(self._trim_apply)
        act_row.addWidget(self.trim_apply_btn, 1)
        bcol.addLayout(act_row)

        self.trim_warn = QLabel("")
        self.trim_warn.setWordWrap(True)
        self.trim_warn.setStyleSheet("color:#e67e22;")
        bcol.addWidget(self.trim_warn)
        rcol.addWidget(box)
        split.addWidget(right)
        split.setSizes([640, 490])
        outer.addWidget(split)
        self._trim_update()
        return page

    # ------------------------------------------------- layout check tab
    def _build_layout_tab(self) -> QWidget:
        """LIBERO 초기 배치와 현재 카메라를 비교하는 탭.

        위: 참조 이미지와 카메라를 50%씩 섞은 겹침 뷰 (agent / wrist).
        아래: 참조·카메라 4장을 나란히. 로그 자리가 필요하므로 이 탭이
        보이는 동안은 하단 로그 패널을 접는다(_on_center_tab_changed).
        카메라 쪽은 변환 파이프라인과 같은 크롭(wrist 는 +31px)을 거치므로
        보이는 그대로가 학습 입력 프레이밍이다.
        """
        self._layout_entries: list = []      # (suite, name, agent_png, wrist_png)
        self._layout_idx = 0
        self._layout_playing = True
        self._layout_ref: dict = {}          # role -> (224,224,3) RGB
        self._last_cam_frame: dict = {}      # role -> 카메라 원본 (640x480)

        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(4, 4, 4, 4)

        # 컨트롤(suite·재생·간격·투명도·번갈아 보기)은 왼쪽 Layout 페이지에
        # 있다(_page_layout) -- 뷰를 보면서 조작할 수 있도록. 탭에는 지금 몇
        # 번째 스틸인지만 남긴다.
        self._layout_blink_state = False
        self.layout_name_label = QLabel("")
        self.layout_name_label.setStyleSheet("color:#888;")
        col.addWidget(self.layout_name_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.layout_overlay_views = {}
        for role, title in (("agent", "Agent 비교"), ("wrist", "Wrist 비교")):
            box = QGroupBox(tr(title))
            inner = QVBoxLayout(box)
            inner.setContentsMargins(4, 4, 4, 4)
            v = VideoView()
            v.setText(tr("참조 이미지 없음"))
            inner.addWidget(v)
            self.layout_overlay_views[role] = v
            split.addWidget(box)
        split.setSizes([600, 600])
        col.addWidget(split, 1)

        strip = QHBoxLayout()
        self.layout_strip_views = {}
        for key, cap in (("agent_ref", "LIBERO agent"), ("agent_live", tr("카메라 agent")),
                         ("wrist_ref", "LIBERO wrist"), ("wrist_live", tr("카메라 wrist"))):
            cell = QVBoxLayout()
            v = VideoView()
            v.setMinimumSize(120, 120)
            cell.addWidget(v, 1)
            lab = QLabel(cap)
            lab.setStyleSheet("color:#888;")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(lab)
            self.layout_strip_views[key] = v
            strip.addLayout(cell, 1)
        strip_w = QWidget()
        strip_w.setLayout(strip)
        strip_w.setMinimumHeight(150)
        strip_w.setMaximumHeight(220)
        col.addWidget(strip_w)

        self._layout_timer = QTimer(self)
        self._layout_timer.setInterval(5000)
        self._layout_timer.timeout.connect(lambda: self._layout_step(+1, user=False))
        self._layout_blink_timer = QTimer(self)
        self._layout_blink_timer.setInterval(500)
        self._layout_blink_timer.timeout.connect(self._layout_blink_tick)
        return w

    def _ensure_layout_refs(self) -> bool:
        """Unpacks assets/libero_init_layouts.zip next to itself.

        Only the zip is in the remote; the extracted pngs fall under
        .gitignore's *.png. A stamp of the zip's size+mtime decides whether to
        re-extract, so replacing the zip is all it takes to update the refs.
        """
        if not LAYOUT_ZIP.exists():
            return LAYOUT_DIR.exists()
        stamp = LAYOUT_DIR / ".zip_stamp"
        st = LAYOUT_ZIP.stat()
        want = f"{st.st_size}:{int(st.st_mtime)}"
        try:
            if stamp.exists() and stamp.read_text() == want:
                return True
            import zipfile
            if LAYOUT_DIR.exists():
                shutil.rmtree(LAYOUT_DIR)
            with zipfile.ZipFile(LAYOUT_ZIP) as z:
                z.extractall(LAYOUT_DIR)
            stamp.write_text(want)
            self.log(f"[레이아웃] 참조 이미지 압축 해제: {LAYOUT_DIR}")
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"[레이아웃] 압축 해제 실패: {type(e).__name__}: {e}")
            return False

    def _layout_reload(self) -> None:
        """Scans the extracted tree and fills the suite filter."""
        if not self._ensure_layout_refs():
            self.layout_name_label.setText(
                tr("assets/libero_init_layouts.zip 이 없습니다"))
            return
        entries = []
        suites = []
        for suite in sorted(p for p in LAYOUT_DIR.iterdir() if p.is_dir()):
            found = False
            for ap in sorted((suite / "agent").glob("*.png")):
                wp = suite / "wrist" / ap.name
                if wp.exists():
                    entries.append((suite.name, ap.stem, str(ap), str(wp)))
                    found = True
            if found:
                suites.append(suite.name)
        self._layout_all_entries = entries
        cur = self.layout_suite_combo.currentText()
        self.layout_suite_combo.blockSignals(True)
        self.layout_suite_combo.clear()
        self.layout_suite_combo.addItem(tr("(전체)"), None)
        for s in suites:
            self.layout_suite_combo.addItem(s, s)
        i = self.layout_suite_combo.findText(cur)
        self.layout_suite_combo.setCurrentIndex(max(0, i))
        self.layout_suite_combo.blockSignals(False)
        self._layout_refilter()

    def _layout_refilter(self) -> None:
        suite = self.layout_suite_combo.currentData()
        all_entries = getattr(self, "_layout_all_entries", [])
        self._layout_entries = [e for e in all_entries
                                if suite is None or e[0] == suite]
        self._layout_idx = 0
        self._layout_show()

    def _layout_step(self, delta: int, user: bool = True) -> None:
        if not self._layout_entries:
            return
        self._layout_idx = (self._layout_idx + delta) % len(self._layout_entries)
        if user and self._layout_timer.isActive():
            self._layout_timer.start()      # 수동 이동 시 타이머 리셋
        self._layout_show()

    def _layout_toggle_play(self) -> None:
        self._layout_playing = not self._layout_playing
        self.layout_play_btn.setText(
            tr("일시정지") if self._layout_playing else tr("재생"))
        if self._layout_playing and \
                self.center_tabs.currentIndex() == self._layout_tab_index:
            self._layout_timer.start()
        else:
            self._layout_timer.stop()

    def _layout_apply_interval(self) -> None:
        sec = self.layout_interval_combo.currentData() or 5
        self._layout_timer.setInterval(int(sec) * 1000)

    def _layout_show(self) -> None:
        if not self._layout_entries:
            for v in self.layout_overlay_views.values():
                v.clear_frame(tr("참조 이미지 없음"))
            for v in self.layout_strip_views.values():
                v.clear_frame("")
            self.layout_name_label.setText("")
            return
        import cv2
        suite, name, ap, wp = self._layout_entries[self._layout_idx]
        self.layout_name_label.setText(
            f"{suite} · {name}  ({self._layout_idx + 1}/{len(self._layout_entries)})")
        for role, path in (("agent", ap), ("wrist", wp)):
            bgr = cv2.imread(path)
            if bgr is None:
                self._layout_ref.pop(role, None)
                continue
            self._layout_ref[role] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            self.layout_strip_views[f"{role}_ref"].set_frame(self._layout_ref[role])
            self._layout_update_role(role)

    def _layout_alpha_changed(self, val: int) -> None:
        self.layout_alpha_label.setText(tr("스틸 {v}%").format(v=val))
        for role in ("agent", "wrist"):
            self._layout_update_role(role)

    def _layout_blink_toggled(self, on: bool) -> None:
        """번갈아 보기 -- 겹침 대신 카메라와 스틸을 0.5초씩 교대로 보여준다."""
        self.layout_alpha_slider.setEnabled(not on)
        self._layout_blink_state = False
        if on and self.center_tabs.currentIndex() == self._layout_tab_index:
            self._layout_blink_timer.start()
        else:
            self._layout_blink_timer.stop()
        for role in ("agent", "wrist"):
            self._layout_update_role(role)

    def _layout_blink_tick(self) -> None:
        self._layout_blink_state = not self._layout_blink_state
        for role in ("agent", "wrist"):
            self._layout_update_role(role)

    def _layout_update_role(self, role: str) -> None:
        """Re-blends one side. Called on slideshow advance, on every camera
        frame while the tab is visible, and when the alpha slider moves.

        카메라가 바닥, LIBERO 스틸이 그 위에 슬라이더만큼의 불투명도로 올라간다.
        카메라 프레임이 없으면 참조를 단독으로 보여주는 대신 그렇다고 말한다 --
        참조 단독은 "겹침이 안 되고 있다"는 사실을 숨긴다.
        """
        ref = self._layout_ref.get(role)
        if ref is None:
            return
        frame = self._last_cam_frame.get(role)
        if frame is None:
            self.layout_overlay_views[role].clear_frame(
                tr("카메라 없음 — Configure 에서 미리보기를 켜세요"))
            self.layout_strip_views[f"{role}_live"].clear_frame(tr("카메라 없음"))
            return
        p = self._crop_params[role]
        live = resize_rgb(frame, ref.shape[0], zoom=p["zoom"],
                          x_shift=p["x"], y_shift=p["y"])
        self.layout_strip_views[f"{role}_live"].set_frame(live)
        if self.layout_blink_check.isChecked():
            # 교대 모드: 위치 차이가 겹침보다 눈에 잘 띈다 (운동 시차 효과).
            shown = ref if self._layout_blink_state else live
        else:
            a = self.layout_alpha_slider.value()
            shown = ((live.astype(np.uint16) * (100 - a)
                      + ref.astype(np.uint16) * a) // 100).astype(np.uint8)
        if self.layout_grid_check.isChecked():
            shown = _grid_overlay(shown)
        self.layout_overlay_views[role].set_frame(shown)

    def _on_center_tab_changed(self, idx: int) -> None:
        """레이아웃 탭이 보이는 동안만 하단 로그를 접고 슬라이드쇼를 돌린다."""
        on = idx == self._layout_tab_index
        self.bottom_tabs.setVisible(not on)
        if on:
            self._set_activity("layout")     # 컨트롤이 왼쪽 페이지에 있다
            if not getattr(self, "_layout_all_entries", None):
                self._layout_reload()
            else:
                self._layout_show()
            self._layout_apply_interval()
            if self._layout_playing:
                self._layout_timer.start()
            if self.layout_blink_check.isChecked():
                self._layout_blink_timer.start()
        else:
            self._layout_timer.stop()
            self._layout_blink_timer.stop()

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
        # 정렬 키는 전부 아래 표에 칼럼으로도 나온다 -- 정렬 기준을 바꿔야만
        # 보이는 "점수" 칸이 있으면 지금 무슨 수를 보고 있는지 알 수 없다.
        for label, key in (("평균과 차이 큰 순 = 급한 순 (권장)", "fast"),
                           ("평균과 차이 작은 순 = 느린 순", "slow"),
                           ("멈춤 비율 높은 순", "still"),
                           ("길이 짧은 순", "short"),
                           ("길이 긴 순", "long")):
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
        self.rank_tree.setHeaderLabels([tr("에피소드"), tr("평균과 차이"), tr("멈춤%"),
                                        tr("길이"), tr("task")])
        self.rank_tree.setRootIsDecorated(False)
        self.rank_tree.setColumnWidth(0, 150)
        for c in range(1, 4):
            self.rank_tree.setColumnWidth(c, 76)
        for c, tip in enumerate((
                tr("파일 · 에피소드"),
                tr("이 에피소드의 평균 |Δa| 에서 같은 task 평균을 뺀 값 (rad/frame).\n"
                   "+ 는 그 작업의 보통 테이크보다 급하게, - 는 느리게 움직인 것.\n"
                   "±{d} 를 넘으면 빨강/파랑").format(d=TASK_DEV_LIMIT),
                tr("속도가 {v} rad/frame 미만이던 프레임 비율 — 망설임").format(v=STILL_VEL),
                tr("에피소드 길이 (초)"),
                tr("language instruction"))):
            self.rank_tree.headerItem().setToolTip(c, tip)
        self.rank_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rank_tree.itemSelectionChanged.connect(self._on_rank_selected)
        self.rank_tree.setMinimumHeight(220)
        fcol.addWidget(self.rank_tree, 1)
        # 판정선만 한 줄로 남긴다. 나머지 정의는 헤더 툴팁 -- 조작자가 코드를
        # 열지 않고도 "몇이면 이상한가"를 알아야 하지만, 그게 목록을 밀어내면
        # 정작 봐야 할 후보가 안 보인다.
        cols_row = QHBoxLayout()
        cols = QLabel(tr("같은 task 평균과의 차 — ±{d} 밖이면 급함(빨강)/느림(파랑)")
                      .format(d=TASK_DEV_LIMIT))
        cols.setStyleSheet("color:#888;")
        cols_row.addWidget(cols, 1)
        helpb = QPushButton("?")
        helpb.setFixedWidth(24)
        helpb.setToolTip(tr("칼럼 정의와 판정 기준 (docs/curation-metrics.md)"))
        helpb.clicked.connect(self._on_metric_help)
        cols_row.addWidget(helpb)
        fcol.addLayout(cols_row)

        btns = QHBoxLayout()
        for text, slot in ((tr("재생해서 확인"), self._on_rank_play),
                           (tr("선택 삭제"), self._on_rank_delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        fcol.addLayout(btns)
        rcol.addWidget(filt, 1)
        split.addWidget(right)
        split.setSizes([700, 430])
        outer.addWidget(split)
        return page

    def _page_layout(self) -> QWidget:
        """카메라 레이아웃 설정 -- 레이아웃 탭의 뷰를 보면서 조작하는 왼쪽 패널."""
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)

        open_btn = QPushButton(tr("레이아웃 탭 열기"))
        open_btn.clicked.connect(
            lambda: self.center_tabs.setCurrentIndex(self._layout_tab_index))
        col.addWidget(open_btn)

        # Configure 의 카메라 그룹 복제 -- 여기서 고르나 저기서 고르나 같다.
        # Configure 쪽 콤보가 원본이고 이쪽은 미러: 이쪽에서 바꾸면 원본으로
        # 밀어넣고(_on_layout_camera_changed), 원본이 바뀌면 여기로 복사한다
        # (_mirror_camera_combos). 미리보기 재시작은 원본의 시그널이 담당한다.
        cam = QGroupBox(tr("카메라"))
        cform = QFormLayout(cam)
        self.layout_agent_combo = QComboBox()
        self.layout_wrist_combo = QComboBox()
        for c in (self.layout_agent_combo, self.layout_wrist_combo):
            c.setEditable(True)
            c.currentTextChanged.connect(self._on_layout_camera_changed)
        cform.addRow(tr("Agent"), self.layout_agent_combo)
        cform.addRow(tr("Wrist"), self.layout_wrist_combo)
        refresh = QPushButton(tr("카메라 새로고침"))
        refresh.clicked.connect(self._refresh_cameras)
        cform.addRow(refresh)
        self.layout_preview_btn = QPushButton(tr("미리보기 시작"))
        self.layout_preview_btn.clicked.connect(self._on_toggle_previews)
        cform.addRow(self.layout_preview_btn)
        self.layout_camera_hint = QLabel("")
        self.layout_camera_hint.setStyleSheet("color:#888;")
        self.layout_camera_hint.setWordWrap(True)
        cform.addRow(self.layout_camera_hint)
        col.addWidget(cam)

        show = QGroupBox(tr("슬라이드쇼"))
        sform = QFormLayout(show)
        self.layout_suite_combo = QComboBox()
        self.layout_suite_combo.currentIndexChanged.connect(self._layout_refilter)
        sform.addRow(tr("Suite"), self.layout_suite_combo)
        nav = QWidget()
        nrow = QHBoxLayout(nav)
        nrow.setContentsMargins(0, 0, 0, 0)
        prev_btn = QPushButton("◀")
        prev_btn.clicked.connect(lambda: self._layout_step(-1))
        nrow.addWidget(prev_btn)
        self.layout_play_btn = QPushButton(tr("일시정지"))
        self.layout_play_btn.clicked.connect(self._layout_toggle_play)
        nrow.addWidget(self.layout_play_btn, 1)
        next_btn = QPushButton("▶")
        next_btn.clicked.connect(lambda: self._layout_step(+1))
        nrow.addWidget(next_btn)
        sform.addRow(nav)
        self.layout_interval_combo = QComboBox()
        for sec in (3, 5, 10):
            self.layout_interval_combo.addItem(tr("{s}초마다").format(s=sec), sec)
        self.layout_interval_combo.setCurrentIndex(1)
        self.layout_interval_combo.currentIndexChanged.connect(
            self._layout_apply_interval)
        sform.addRow(tr("전환 간격"), self.layout_interval_combo)
        col.addWidget(show)

        disp = QGroupBox(tr("표시"))
        dform = QFormLayout(disp)
        # 스틸(LIBERO)이 카메라 위. 0% = 카메라만, 100% = 스틸만.
        self.layout_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.layout_alpha_slider.setRange(0, 100)
        self.layout_alpha_slider.setValue(50)
        self.layout_alpha_slider.valueChanged.connect(self._layout_alpha_changed)
        self.layout_alpha_label = QLabel(tr("스틸 50%"))
        self.layout_alpha_label.setStyleSheet("color:#888;")
        dform.addRow(self.layout_alpha_label, self.layout_alpha_slider)
        self.layout_blink_check = QCheckBox(tr("카메라/스틸 번갈아 보기"))
        self.layout_blink_check.toggled.connect(self._layout_blink_toggled)
        dform.addRow(self.layout_blink_check)
        self.layout_blink_slider = QSlider(Qt.Orientation.Horizontal)
        self.layout_blink_slider.setRange(50, 500)      # ms
        self.layout_blink_slider.setValue(500)
        self.layout_blink_label = QLabel(tr("전환 0.50초"))
        self.layout_blink_label.setStyleSheet("color:#888;")
        self.layout_blink_slider.valueChanged.connect(
            self._layout_blink_interval_changed)
        dform.addRow(self.layout_blink_label, self.layout_blink_slider)
        self.layout_grid_check = QCheckBox(tr("격자 표시 (수평 확인)"))
        self.layout_grid_check.toggled.connect(
            lambda _on: self._layout_rerender())
        dform.addRow(self.layout_grid_check)
        col.addWidget(disp)

        # 크롭 정렬 -- 값은 640 폭 기준 px. 라이브 가이드·레이아웃 겹침·변환이
        # 같은 값을 쓰고, 에피소드마다 attrs["crop_params"] 로 저장된다.
        crop = QGroupBox(tr("크롭 정렬"))
        crform = QFormLayout(crop)
        p = self._crop_params

        def _slider(lo: int, hi: int, val: int) -> QSlider:
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(lo, hi)
            s.setValue(val)
            return s

        self.crop_agent_zoom = _slider(100, 200, round(p["agent"]["zoom"] * 100))
        self.crop_agent_zoom_label = QLabel("")
        crform.addRow(self.crop_agent_zoom_label, self.crop_agent_zoom)
        self.crop_agent_x = _slider(-80, 80, int(p["agent"]["x"]))
        self.crop_agent_x_label = QLabel("")
        crform.addRow(self.crop_agent_x_label, self.crop_agent_x)
        self.crop_agent_y = _slider(-100, 100, int(p["agent"]["y"]))
        self.crop_agent_y_label = QLabel("")
        crform.addRow(self.crop_agent_y_label, self.crop_agent_y)
        self.crop_wrist_x = _slider(-80, 80, int(p["wrist"]["x"]))
        self.crop_wrist_x_label = QLabel("")
        crform.addRow(self.crop_wrist_x_label, self.crop_wrist_x)
        for s in (self.crop_agent_zoom, self.crop_agent_x,
                  self.crop_agent_y, self.crop_wrist_x):
            s.valueChanged.connect(self._crop_changed)
        reset_btn = QPushButton(tr("기본값으로"))
        reset_btn.clicked.connect(self._crop_reset)
        crform.addRow(reset_btn)
        self._crop_widgets = [self.crop_agent_zoom, self.crop_agent_x,
                              self.crop_agent_y, self.crop_wrist_x, reset_btn]
        self._refresh_crop_labels()
        col.addWidget(crop)
        col.addStretch()
        return w

    def _refresh_crop_labels(self) -> None:
        p = self._crop_params
        self.crop_agent_zoom_label.setText(
            tr("Agent 줌 {z:.2f}x").format(z=p["agent"]["zoom"]))
        self.crop_agent_x_label.setText(
            tr("Agent x {v:+d}px").format(v=p["agent"]["x"]))
        self.crop_agent_y_label.setText(
            tr("Agent y {v:+d}px").format(v=p["agent"]["y"]))
        self.crop_wrist_x_label.setText(
            tr("Wrist x {v:+d}px").format(v=p["wrist"]["x"]))

    def _crop_changed(self) -> None:
        p = self._crop_params
        p["agent"]["zoom"] = self.crop_agent_zoom.value() / 100.0
        p["agent"]["x"] = self.crop_agent_x.value()
        p["agent"]["y"] = self.crop_agent_y.value()
        p["wrist"]["x"] = self.crop_wrist_x.value()
        self._refresh_crop_labels()
        save_crop_params(p)
        for views in (self.live_views, self.play_views,
                      getattr(self, "trim_views", {})):
            for role, v in views.items():
                v.set_crop_guide(**p[role])
        self._layout_rerender()

    def _crop_reset(self) -> None:
        d = default_crop_params()
        self.crop_agent_zoom.setValue(round(d["agent"]["zoom"] * 100))
        self.crop_agent_x.setValue(d["agent"]["x"])
        self.crop_agent_y.setValue(d["agent"]["y"])
        self.crop_wrist_x.setValue(d["wrist"]["x"])

    def _layout_rerender(self) -> None:
        for role in ("agent", "wrist"):
            self._layout_update_role(role)

    def _layout_blink_interval_changed(self, ms: int) -> None:
        self.layout_blink_label.setText(tr("전환 {s:.2f}초").format(s=ms / 1000))
        self._layout_blink_timer.setInterval(int(ms))

    def _page_settings(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        # 언어 전환은 반쪽만 동작한다. tr() 은 위젯을 만들 때 한 번 호출되고
        # 그 문자열이 박히므로, 전역 언어를 바꿔도 이미 만들어진 창은 그대로다
        # -- 전환 뒤 새로 여는 다이얼로그만 바뀌어서 한국어와 영어가 섞인다.
        # 제대로 하려면 모든 위젯에 retranslate 경로가 필요하다. 그때까지는
        # 반쯤 되는 채로 두는 것보다 미개발로 못 박아두는 쪽이 낫다.
        lang = QPushButton(f'{tr("언어 전환 (한국어 / English)")} ({TODO_MARK})')
        col.addWidget(mark_todo(lang, tr(
            "이미 열린 창은 다시 그려지지 않아 한국어와 영어가 섞입니다.")))
        layout_btn = QPushButton(tr("카메라 레이아웃 확인 (LIBERO 초기 배치와 비교)"))
        layout_btn.setToolTip(tr(
            "LIBERO 초기 배치 이미지와 현재 카메라를 50% 투명도로 겹쳐 보여줍니다."))
        layout_btn.clicked.connect(
            lambda: self.center_tabs.setCurrentIndex(self._layout_tab_index))
        col.addWidget(layout_btn)
        schema = QPushButton(tr("데이터셋 구조 사용자 설정..."))
        schema.setToolTip(tr("Action 구조는 고정입니다. Observation 필드만 고를 수 "
                             "있습니다."))
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
        m.addAction(tr("튀는 것만 선택 (task 평균과 ±0.0026 밖)"),
                    self._on_select_jerky)
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
        m.addAction(tr("데이터셋 구조 사용자 설정..."), self._on_schema)
        m.addAction(f'{tr("언어 전환")} ({TODO_MARK})').setEnabled(False)

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
        """Writes to the file even when the widget is gone.

        Signals outlive the window: QProcess.finished for the robot node
        arrives after closeEvent has torn the tabs down, and appending to a
        destroyed QPlainTextEdit raised "wrapped C/C++ object ... has been
        deleted" -- during shutdown, where an unhandled exception is most
        likely to lose the very message explaining why we are shutting down.
        The file is what matters at that point, so it is written first.
        """
        self._progress_line.pop(view, None)  # 다음 진행률은 새 줄에서 시작
        if self._log_file is not None:
            self._log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        target = self._view(view)
        if target is not None and not sip.isdeleted(target):
            target.appendPlainText(msg)

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
        self._bump("success", 1 if self._last_saved_success else -1)
        self._bump("failed", -1 if self._last_saved_success else 1)
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

    # legacy '기존 task 이어찍기' 드롭다운(_refresh_resume_combo /
    # _on_resume_selected / _show_resume_info)은 legacy 수집 UI 제거와 함께
    # 삭제됐다 (2026-08-13). scene 이어찍기는 Scene 콤보가 담당한다.

    def _apply_session_config(self, cfg: dict) -> list:
        """Puts a file's recorded session_config back into the widgets that
        produced it (see libero_gui_worker.py's record_session_config).

        Returns the labels of what was actually restored, so the hint can say
        what changed rather than claim more than it did -- older files were
        written before some of these keys existed.
        """
        done = []
        for key, combo, label in (("reset_pose", self.reset_pose_combo, tr("Reset pose")),
                                  ("grip", self.grip_combo, tr("Grip"))):
            val = cfg.get(key)
            if val is None:
                continue
            i = combo.findText(str(val))
            if i >= 0:
                combo.setCurrentIndex(i)
                done.append(label)
        for key, edit, label in (("max_episode_seconds", self.eplen_edit, tr("에피소드 길이")),
                                 ("reset_wait_seconds", self.resetwait_edit, tr("리셋 대기"))):
            val = cfg.get(key)
            if val is not None:
                edit.setText(str(int(val)))
                done.append(label)
        if cfg.get("enable_wall") is not None:
            self.wall_check.setChecked(bool(cfg["enable_wall"]))
            done.append(tr("관절 한계 벽"))
        return done

    def _refresh_schema_label(self) -> None:
        n = sum(1 for k in ("save_agentview_rgb", "save_eye_in_hand_rgb",
                            "save_joint_states", "save_gripper_states",
                            "save_ee_states", "save_ee_pos", "save_ee_ori",
                            "save_joint_velocities", "save_timestamp")
                if getattr(self.schema, k, False))
        self.schema_label.setText(
            tr("action: {a} (고정) · observation 필드 {n}개 선택됨").format(
                a=getattr(self.schema, "action_space", "?"), n=n))

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
            self._set_camera_hint(tr("카메라 목록 조회 실패: {e}").format(e=e))
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
        self._mirror_camera_combos(rebuild=True)
        self._set_camera_hint(tr("{n}대 감지됨").format(n=len(entries)))
        self.log(f"[카메라] {len(entries)}대 감지: {[s for s, _ in entries]}")

    def _set_camera_hint(self, text: str) -> None:
        self.camera_hint.setText(text)
        if hasattr(self, "layout_camera_hint"):
            self.layout_camera_hint.setText(text)

    def _mirror_camera_combos(self, rebuild: bool = False) -> None:
        """Configure 콤보(원본) -> Layout 콤보(미러) 복사. ``rebuild`` 면 항목
        목록까지 새로 채운다 (_refresh_cameras 뒤)."""
        if not hasattr(self, "layout_agent_combo"):
            return
        for src, dst in ((self.agent_combo, self.layout_agent_combo),
                         (self.wrist_combo, self.layout_wrist_combo)):
            dst.blockSignals(True)
            if rebuild:
                dst.clear()
                for i in range(src.count()):
                    dst.addItem(src.itemText(i), src.itemData(i))
            i = src.currentIndex()
            if i >= 0 and src.itemText(i) == src.currentText():
                dst.setCurrentIndex(i)
            else:
                dst.setCurrentText(src.currentText())
            dst.blockSignals(False)

    def _on_layout_camera_changed(self) -> None:
        """Layout 콤보에서 고른 것을 원본으로 밀어넣는다. 원본 시그널이
        _on_camera_changed 를 태워 미리보기 재시작까지 이어진다."""
        for src, dst in ((self.layout_agent_combo, self.agent_combo),
                         (self.layout_wrist_combo, self.wrist_combo)):
            if src.currentText() == dst.currentText():
                continue
            i = src.currentIndex()
            if i >= 0 and src.itemText(i) == src.currentText():
                dst.setCurrentIndex(i)
            else:
                dst.setCurrentText(src.currentText())

    def _combo_serial(self, combo: QComboBox) -> str:
        data = combo.currentData()
        if data:
            return str(data)
        text = combo.currentText().strip()
        return "" if text.startswith("(") else text

    def _on_square_guide(self, on: bool) -> None:
        for v in list(self.live_views.values()) + list(self.play_views.values()) \
                + list(getattr(self, "trim_views", {}).values()):
            v.set_square_guide(on)

    def _on_camera_changed(self) -> None:
        self._mirror_camera_combos()
        if self.worker is not None:
            return  # the session owns the cameras; previews must stay off
        self._restart_previews()

    def _on_toggle_previews(self) -> None:
        if self.worker is not None:
            return  # the session owns the cameras; previews must stay off
        if self.agent_preview or self.wrist_preview:
            self._stop_previews_async()
            for role in ("agent", "wrist"):
                self.live_views[role].clear_frame(tr("미리보기 중단됨"))
        else:
            self._restart_previews()

    def _update_preview_btn(self) -> None:
        if not hasattr(self, "preview_btn"):
            return
        on = bool(self.agent_preview or self.wrist_preview)
        for btn in (self.preview_btn,
                    getattr(self, "layout_preview_btn", None)):
            if btn is not None:
                btn.setText(tr("미리보기 중단") if on else tr("미리보기 시작"))
                btn.setEnabled(self.worker is None)

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
        self._update_preview_btn()

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
        """Prunes finished previews, skipping any whose C++ side is already gone.

        `sip.isdeleted` must come first and cannot be dropped: a QThread that
        Qt has destroyed leaves its Python wrapper behind, and *any* call on
        it -- isRunning() included -- raises "wrapped C/C++ object ... has been
        deleted". That was this list's normal end state, so the exception fired
        on the next stop/restart and again from closeEvent.
        """
        self._dying_previews = [w for w in self._dying_previews
                                if not sip.isdeleted(w) and w.isRunning()]
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
            # No deleteLater: this list is the owner. Having both meant Qt
            # could free the thread while the list still held the wrapper,
            # which is exactly what _previews_busy() then tripped over. The
            # entry is dropped once the thread reports finished, and the last
            # Python reference goes with it.
            self._dying_previews.append(w)

    def _stop_previews_async(self) -> None:
        """Non-blocking stop. The GUI thread never waits on a camera here --
        that wait was up to 7 s per thread and read as a hang."""
        for role in ("agent", "wrist"):
            self._release_preview(role)
        self.lights["camera"].set("busy" if self._previews_busy() else "off",
                                  tr("정리 중") if self._previews_busy() else "-")
        self._update_preview_btn()
        # 멈춘 카메라의 마지막 프레임을 "현재"로 계속 겹쳐 보이지 않게 한다.
        if self._last_cam_frame:
            self._last_cam_frame.clear()
            if self.center_tabs.currentIndex() == self._layout_tab_index:
                for role in ("agent", "wrist"):
                    self._layout_update_role(role)

    def _stop_previews_blocking(self, timeout_ms: int = 4000) -> None:
        """Only for shutdown: wait so the cameras are released before exit.

        Blocking is acceptable here and nowhere else -- the window is closing,
        so there is no interaction left to make unresponsive.
        """
        self._stop_previews_async()
        for w in self._dying_previews:
            if not sip.isdeleted(w):
                w.wait(timeout_ms)
        self._dying_previews = []

    def _on_preview_frame(self, role: str, frame) -> None:
        self.live_views[role].set_frame(frame)
        self._last_cam_frame[role] = frame
        if self.center_tabs.currentIndex() == self._layout_tab_index:
            self._layout_update_role(role)
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
        scene_on = not no_dataset  # scene-v1 이 유일한 수집 방식 (legacy 제거)
        lang = self.lang_edit.text().strip()
        # scene 설정 검증은 _scene_config_from_ui 가, 파일 생성/이어찍기 판정은
        # SceneWriter 가 한다 (파일명은 scene_id 에서 나오므로 이름 중복 검사
        # 자체가 없다).
        scene_meta = None
        scene_sid = None
        scene_resume = False
        if scene_on:
            scene_meta, scene_sid, scene_resume, err = self._scene_config_from_ui()
            if err is not None:
                QMessageBox.warning(self, tr("Scene 설정"), err)
                return
            task = scene_meta.scene_id if scene_meta is not None else scene_sid
        else:
            # 연습 모드: writer 에 닿지 않지만 WorkerConfig 라벨용 이름은 필요.
            task = "practice"
        resume = False  # legacy 이어찍기 제거 -- scene 은 scene_resume 이 담당
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
            resume=resume,
            no_dataset=no_dataset,
            scene_metadata=scene_meta,
            scene_id=scene_sid,
            scene_resume=scene_resume,
            instruction_id=(self.scene_iid_edit.text().strip() if scene_on else ""),
            collector=(self.collector_edit.text().strip() if scene_on else ""),
            agent_camera_serial=agent,
            wrist_camera_serial=wrist,
            schema=self.schema,
            # 스냅샷(깊은 복사): 세션 중 슬라이더가 잠기긴 하지만, 기록될 값이
            # GUI 상태와 얽혀 있지 않아야 한다.
            crop_params={r: dict(v) for r, v in self._crop_params.items()},
        )
        for key, value in (("language", lang),
                           ("data_root", cfg.data_root),
                           ("agent_serial", agent), ("wrist_serial", wrist),
                           ("collector", cfg.collector),
                           ("instruction_id", cfg.instruction_id)):
            if value:
                self._recents.add(key, value)
        # scene 세션 표시 + Collect 페이지 slot 패널 초기값
        self._scene_session = scene_on
        if scene_on:
            self.slot_iid_edit.setText(cfg.instruction_id)
            self.slot_instr_edit.setText(cfg.language_instruction)
            self.slot_current_label.setText(
                f"{cfg.instruction_id}: {cfg.language_instruction}")

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
        # 세션 해제(버튼 복구, worker=None)는 session_summary가 아니라 finished에
        # 걸어야 한다. summary는 run()의 finally에서만 나오는데, 연결 실패는 그
        # 전에 조기 return이라 summary가 영영 오지 않는다 -- 그 상태에서는 GUI가
        # '연결됨'에 갇혀 재시도하려면 앱을 닫는 수밖에 없었다. finished는 Qt가
        # run()이 어떤 경로로 끝나든 반드시 쏜다.
        w.finished.connect(self._on_worker_finished)
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
                  self.wrist_combo, self.layout_agent_combo,
                  self.layout_wrist_combo, self.reset_pose_combo,
                  self.grip_combo, self.eplen_edit, self.resetwait_edit,
                  self.wall_check, self.match_check):
            w.setEnabled(not running)
        # 크롭 정렬은 에피소드 attrs 에 Connect 시점 스냅샷으로 찍히므로,
        # 세션 중에 움직이면 가이드와 기록이 어긋난다. 잠근다.
        for w in self._crop_widgets:
            w.setEnabled(not running)
        self._update_preview_btn()
        # scene 세션에서만 slot 전환 패널 노출
        self.slot_box.setVisible(running and self._scene_session)
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
        layout_on = self.center_tabs.currentIndex() == self._layout_tab_index
        for role, rgb in (("agent", agent_rgb), ("wrist", wrist_rgb)):
            if rgb is None:
                continue
            self.live_views[role].set_frame(rgb)
            self._last_cam_frame[role] = rgb
            if layout_on:
                self._layout_update_role(role)
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
        self._bump("saved")
        self._bump("frames", n_frames)
        if self._pending_success is not None:
            self._bump("success" if self._pending_success else "failed")
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
        self._bump("discarded")
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
        # 연결되면 카메라 화면으로 따라간다. 버튼을 누른 시점이 아니라 여기인
        # 이유는, 연결이 미리보기 정리를 기다리거나 실패할 수 있기 때문이다 --
        # 그때 Live 로 옮겨두면 아무것도 안 나오는 탭을 보게 된다.
        self.center_tabs.setCurrentIndex(self._live_tab_index)
        # 이번 task 카운터는 여기서 0 으로 돌아간다(누적은 그대로). 연습 모드도
        # 마찬가지다 -- NullTaskWriter 도 저장을 받아 넘기므로 카운터는 움직인다.
        self._session = _new_stats()
        if self._no_dataset_session:
            # NullTaskWriter has no real path; claiming one here would make the
            # dataset tree think a file is locked by this session.
            self._update_dataset_panel()
            self.log("[연결] 연습 모드로 연결되었습니다.")
            return
        self.active_file_path = Path(path)
        self._episodes_at_connect = int(n_episodes)
        if self._scene_session:
            # scene 파일이 실제로 만들어졌으니 보관해 둔 새 scene 구성은 소진.
            self._pending_scene_meta = None
        self._update_dataset_panel()
        self.log(f"[연결] 파일: {path} (기존 {n_episodes}개 에피소드)")
        self._refresh_dataset_tree()

    @pyqtSlot(list)
    def _on_episode_list(self, episodes) -> None:
        self.active_episode_cache = episodes
        self._refresh_dataset_tree()

    @pyqtSlot(dict)
    def _on_summary(self, summary) -> None:
        # 해제는 여기서 하지 않는다 -- 정상 종료에만 오는 신호다. 실제 해제는
        # 모든 종료 경로에서 오는 finished(_on_worker_finished)가 맡는다.
        self.log(f"[세션 요약] {summary}")

    @pyqtSlot()
    def _on_worker_finished(self) -> None:
        """워커 run()이 어떤 경로로든 끝나면 세션을 해제한다.

        정상 종료(요약 후), 연결 실패 조기 return, 예외 -- 전부 여기로 온다.
        summary보다 늦게 도착하므로(둘 다 큐잉, run() 안에서 summary가 먼저
        emit) 로그 순서도 자연스럽다.
        """
        if self.worker is not self.sender():
            # 이미 다른 세션이 시작된 뒤 도착한 옛 워커의 신호 -- 무시.
            return
        self.worker = None
        self._no_dataset_session = False
        self.active_file_path = None
        self.active_episode_cache = None
        was_scene = self._scene_session
        self._scene_session = False
        self._set_running(False)
        self._refresh_dataset_tree()
        if was_scene:
            # 세션이 만든/키운 scene 파일이 목록·slot 현황에 반영되게.
            self._refresh_scene_combo()
        self._restart_previews()

    # -------------------------------------------------------------- stats
    def _bump(self, key: str, n: int = 1) -> None:
        """카운터 하나를 이번 task 와 누적 양쪽에 올린다.

        두 dict 를 따로 건드리면 반드시 한쪽만 올리는 자리가 생긴다 -- 판정
        뒤집기처럼 -1 도 있는 경로가 섞여 있어서 더 그렇다.
        """
        self._session[key] += n
        self._cumulative[key] += n

    def _current_task_label(self, limit: int = 0) -> str:
        """수집 중인 task 이름. 연결 전이거나 연습 모드면 빈 문자열.

        ``limit`` 을 주면 그 길이로 줄이되 **뒤쪽**을 자른다. LIBERO task 이름은
        ``put_the_black_bowl_on_the_plate...`` 처럼 길고 앞부분이 서로 다르므로,
        Qt 가 오른쪽 정렬 라벨에서 하듯 앞을 잘라내면 어느 task 인지 알 수 없다.
        """
        if self.worker is None or self._no_dataset_session:
            return ""
        name = getattr(self.worker.cfg, "task_name", "") or ""
        if limit and len(name) > limit:
            return name[: limit - 1] + "…"
        return name

    def _tick_fps(self) -> None:
        self._fps_value = self._fps_count
        self._fps_count = 0
        self.right_fields["fps"].setText(f"{self._fps_value:.0f}")
        if self.worker is not None and not self._no_dataset_session:
            # max(): 저장이 백그라운드라 episode_list_changed 가 몇 초 늦게 온다.
            # 그 사이를 연결시점 + 이번 task 저장수로 메운다. _session 이 Connect
            # 마다 리셋되므로 두 값 모두 지금 task 의 것이다.
            total = max(len(self.active_episode_cache or []),
                        self._episodes_at_connect + self._session["saved"])
            count = tr("{k}: 에피소드 {t}개 (이번 +{s})").format(
                k=self._current_task_label(limit=32), t=total, s=self._session["saved"])
        else:
            count = tr("저장 {s}").format(s=self._cumulative["saved"])
        self.sb_right.setText(
            f"{self._fps_value:.0f} fps   |   {count}   |   {self.root_edit.text()}")

    def _refresh_stats(self) -> None:
        for stats, labels in ((self._session, self.stats_labels),
                              (self._cumulative, self.stats_total_labels)):
            elapsed = time.monotonic() - stats["t0"]
            for key in ("saved", "success", "failed", "discarded", "frames"):
                labels[key].setText(str(stats[key]))
            labels["elapsed"].setText(f"{elapsed / 60:.1f} min")
            # 30초 미만에서는 분당 환산이 의미 없는 큰 수로 튄다.
            rate = stats["saved"] / (elapsed / 60) if elapsed > 30 else 0.0
            labels["rate"].setText(f"{rate:.2f}")
        # 어느 task 의 숫자인지 헤더에 박아 둔다. task 를 여러 개 도는 동안
        # 왼쪽 열이 무엇을 세고 있는지가 패널만 보고 답이 되어야 한다.
        task = self._current_task_label(limit=20)
        self.stats_task_header.setText(task or tr("이번 task"))
        self.stats_task_header.setToolTip(self._current_task_label())
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
                tr("{t}개  (이번 +{s})").format(t=total, s=self._session["saved"]))
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
        score = {
            "fast": lambda e: -e.task_dev,
            "slow": lambda e: e.task_dev,
            "still": lambda e: -e.still_frac,
            "short": lambda e: e.n_frames,
            "long": lambda e: -e.n_frames,
        }[key]
        rows = sorted(rows, key=score)[:60]
        self.rank_tree.clear()
        for e in rows:
            item = QTreeWidgetItem([
                f"{Path(e.path).stem[:22]} · {e.demo}",
                f"{e.task_dev:+.4f}", f"{100 * e.still_frac:.0f}%",
                f"{e.seconds:.1f}s", e.task[:34]])
            item.setData(0, Qt.ItemDataRole.UserRole, (e.path, e.demo))
            # 밴드 밖은 차이 칸만 물들인다 -- 행 전체를 칠하면 실패(빨강)와
            # 겹쳐서 둘 다 안 읽힌다.
            if e.task_dev > TASK_DEV_LIMIT:
                item.setForeground(1, Qt.GlobalColor.red)
            elif e.task_dev < -TASK_DEV_LIMIT:
                item.setForeground(1, Qt.GlobalColor.blue)
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
        self._show_trim_for(path, demo)

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
                tr("{d} · {n}프레임 ({s:.1f}s) · 평균 |Δa| {m:.5f} · 같은 task 평균과 "
                   "{v:+.4f}{mark} · 멈춤 {p:.0f}%\n{t}").format(
                       d=demo, n=stat.n_frames, s=stat.seconds, m=stat.mean_da,
                       v=stat.task_dev,
                       mark=" (급함)" if stat.task_dev > TASK_DEV_LIMIT else (
                           " (느림)" if stat.task_dev < -TASK_DEV_LIMIT else ""),
                       p=100 * stat.still_frac, t=stat.task))
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
        # 접은 채로 시작한다. 200줄 넘는 에피소드를 한 번에 펼쳐두면 정작 훑고
        # 싶은 task 목록이 화면 밖으로 밀린다. 필요한 파일만 열면 된다.
        self.dataset_tree.collapseAll()
        if hasattr(self, "scene_combo"):
            self._refresh_scene_combo()
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

    def _on_metric_help(self) -> None:
        """Shows docs/curation-metrics.md rather than a copy of it.

        The thresholds in that file are the ones episode_stats.py actually
        uses; a second prose copy inside the GUI would be the version that
        goes stale first, and the operator would have no way to tell which of
        the two was lying.
        """
        doc = Path(__file__).resolve().parent.parent / "docs" / "curation-metrics.md"
        try:
            body = doc.read_text(encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, tr("지표 설명"),
                                tr("{p} 를 읽을 수 없습니다: {e}").format(p=doc, e=e))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("지표 정의 — curation-metrics.md"))
        dlg.resize(900, 680)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setMarkdown(body)
        view.setOpenExternalLinks(True)
        lay.addWidget(view)
        path_lbl = QLabel(str(doc))
        path_lbl.setStyleSheet("color:#888;")
        path_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(path_lbl)
        btn = QPushButton(tr("닫기"))
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    def _on_select_jerky(self) -> None:
        """Selects the episodes that stand out *within their own task*.

        Both ends: rushing and dawdling are different mistakes but both are
        "not how this task is usually done". Compared within the task because
        mean_da is distance over time, so between tasks it ranks how far the
        arm must reach rather than how well it was driven.

        Nothing is deleted here. The selection lands in the same tree the
        operator deletes from, so they can play the takes first.
        """
        if not self._stats:
            self._refresh_analysis()
        if not self._stats:
            return
        flagged = {(e.path, e.demo) for e in self._stats if e.flagged}
        self.dataset_tree.clearSelection()
        n = 0
        for i in range(self.dataset_tree.topLevelItemCount()):
            parent = self.dataset_tree.topLevelItem(i)
            path = parent.data(0, Qt.ItemDataRole.UserRole)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if (path, child.data(0, Qt.ItemDataRole.UserRole)) in flagged:
                    child.setSelected(True)
                    # 접혀 있으면 "N개 선택됨"만 뜨고 무엇이 골렸는지 안 보인다.
                    parent.setExpanded(True)
                    n += 1
        self.log(f"[큐레이션] 같은 task 평균과 {TASK_DEV_LIMIT} 넘게 차이 나는 "
                 f"에피소드 {n}개를 선택했습니다." + ("" if n else " (없음)"))
        self.dataset_hint.setText(
            tr("튀는 에피소드 {n}개 선택됨 — 재생으로 확인한 뒤 '에피소드 삭제'로 지웁니다.")
            .format(n=n) if n else
            tr("같은 task 평균과 {d} 넘게 차이 나는 에피소드가 없습니다 "
               "(이 데이터셋은 균일합니다).").format(d=TASK_DEV_LIMIT))

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
                    parent.setExpanded(True)
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

    # ------------------------------------------------- 묶음 자동 실행 (HDF5/LeRobot)
    def _pipeline_guard(self, what: str) -> bool:
        """Shared preconditions for every automatic button."""
        if self.worker is not None:
            QMessageBox.warning(self, tr("수집 중"),
                                tr("수집 중에는 실행할 수 없습니다. 먼저 세션을 종료하세요."))
            return False
        if self._pipeline_steps:
            QMessageBox.information(self, tr("이미 실행 중"),
                                    tr("{w}이(가) 이미 진행 중입니다. 로그를 확인하세요.")
                                    .format(w=what))
            return False
        return True

    def _start_pipeline(self, steps: list, tag: str) -> None:
        self._pipeline_steps = steps
        self._pipeline_results = []
        self._pipeline_t0 = time.monotonic()
        self.bottom_tabs.setCurrentWidget(self.upload_view)
        self.log(f"[{tag}] {len(steps)}단계 시작 — "
                 + " → ".join(st["name"] for st in steps), "upload")
        self._run_next_pipeline_step()

    def _on_hdf5_auto(self) -> None:
        """재압축 -> 원본 HDF5 업로드."""
        if not self._pipeline_guard(tr("HDF5 자동 처리")):
            return
        data_root = self.root_edit.text().strip()
        paths = sorted(str(x) for x in Path(data_root).glob("*_demo.hdf5"))
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"),
                                tr("{r} 에 *_demo.hdf5 가 없습니다.").format(r=data_root))
            return
        repo = self._check_repo("hdf5_repo_id", tr("HDF5 재압축 + 업로드"))
        if repo is None:
            return
        todo = [x for x in paths if not hdf5_repack_status(x)["repacked"]]
        box = QMessageBox(QMessageBox.Icon.Question, tr("HDF5 재압축 + 업로드"),
                          tr("파일 {n}개 중 재압축이 필요한 것 {m}개.\n"
                             "재압축 후 {r} 에 원본을 업로드합니다.\n\n진행할까요?")
                          .format(n=len(paths), m=len(todo), r=repo),
                          QMessageBox.StandardButton.Yes
                          | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        # 업로드는 재압축과 달리 "Hub과 다른 파일"을 스스로 고르지 못한다
        # (로컬에 업로드 마커가 없다). 변경 없는 파일도 Hub이 해시 대조로
        # 전송은 건너뛰지만 그 대조에 파일 전체를 다시 읽는 시간이 들어서,
        # 몇 개만 새로 찍은 날은 이 체크박스가 수십 분을 아낀다.
        only_new = QCheckBox(
            tr("이번에 재압축한 파일만 업로드 ({m}개)").format(m=len(todo)))
        only_new.setChecked(bool(todo))
        only_new.setEnabled(bool(todo))
        box.setCheckBox(only_new)
        if box.exec() != QMessageBox.StandardButton.Yes:
            self.log("[HDF5 자동] 취소했습니다.", "upload")
            return
        upload_paths = todo if (only_new.isChecked() and todo) else paths
        steps = []
        if todo:
            steps.append({"name": tr("재압축"), "program": sys.executable,
                          "args": [REPACK_SCRIPT, *todo]})
        steps.append({"name": tr("HDF5 원본 업로드")
                      + (tr(" (재압축분 {n}개)").format(n=len(upload_paths))
                         if len(upload_paths) < len(paths) else ""),
                      "program": sys.executable,
                      "args": [UPLOAD_SCRIPT, *upload_paths, "--repo-id", repo,
                               "--no-private"]})
        self._start_pipeline(steps, tr("HDF5 자동"))

    def _on_lerobot_auto(self) -> None:
        """전체 재빌드 -> 교체 업로드. resume 경로는 여기에 없다.

        Curation deletes episodes from .hdf5 files that were already pushed,
        and --resume only ever appends: the deleted episodes' chunks stay on
        the Hub while the declared count drops. Rebuilding from scratch and
        pushing with --replace is the only combination that makes the Hub
        match what is actually on disk, so this button offers nothing else.
        """
        if not self._pipeline_guard(tr("LeRobot 자동 처리")):
            return
        data_root = self.root_edit.text().strip()
        paths = sorted(str(x) for x in Path(data_root).glob("*_demo.hdf5"))
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"),
                                tr("{r} 에 *_demo.hdf5 가 없습니다.").format(r=data_root))
            return
        repo = self._check_repo("repo_id", tr("LeRobot 변환 + 업로드"))
        if repo is None:
            return
        root = self._recents.most_recent("lerobot_root", str(Path.home() / "lerobot_upload"))
        # "task 15개" 만 보여주면 이어붙이기 확인창의 "새 에피소드 25개" 와
        # 단위가 달라 개수 오류처럼 읽힌다 -- 에피소드 합계를 함께 표기한다.
        n_ep = self._count_hdf5_episodes()
        ep_txt = tr(" (에피소드 {e}개)").format(e=n_ep) if n_ep is not None else ""
        if QMessageBox.question(
                self, tr("LeRobot 변환 + 업로드"),
                tr("task {n}개{ep}를 처음부터 다시 변환하고, {r} 을(를) 통째로 "
                   "교체합니다.\n\n"
                   "· 로컬 변환 폴더를 비웁니다: {o}\n"
                   "· 이어붙이기(resume)를 쓰지 않으므로 큐레이션에서 지운 "
                   "에피소드가 Hub에서도 사라집니다\n"
                   "· 전체 재변환이라 시간이 걸립니다\n\n진행할까요?")
                .format(n=len(paths), ep=ep_txt, r=repo, o=root),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            self.log("[LeRobot 자동] 취소했습니다.", "upload")
            return
        self._recents.add("repo_id", repo)
        self._recents.add("lerobot_root", root)
        steps = [
            {"name": tr("LeRobot 변환 (전체 재빌드)"), "program": sys.executable,
             "args": [CONVERT_SCRIPT, *paths, "--repo-id", repo, "--root", root],
             "clear_root": root},
            {"name": tr("LeRobot 교체 업로드"), "program": sys.executable,
             "args": [CONVERT_SCRIPT, "--repo-id", repo, "--root", root,
                      "--push-only", "--replace", "--no-private"]},
        ]
        self._start_pipeline(steps, tr("LeRobot 자동"))

    def _on_lerobot_resume(self) -> None:
        """이어붙이기 -- Hub과 대조해 새 에피소드만 변환·추가 업로드.

        --resume 이 안전한 조건(추가만 있음)을 plan_sync 로 먼저 검증하고,
        아니면 실행을 거부한다. 삭제/편집이 섞인 채 이어붙이면 지운 에피소드의
        청크가 Hub에 남거나(선언 개수만 줄어듦) 개수 대응이 깨진다 --
        convert_libero_to_lerobot.py 상단 docstring 2번 참고.
        """
        if not self._pipeline_guard(tr("LeRobot 이어붙이기")):
            return
        data_root = self.root_edit.text().strip()
        paths = sorted(str(x) for x in Path(data_root).glob("*_demo.hdf5"))
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"),
                                tr("{r} 에 *_demo.hdf5 가 없습니다.").format(r=data_root))
            return
        repo = self._check_repo("repo_id", tr("LeRobot 이어붙이기"))
        if repo is None:
            return
        plan = plan_sync(data_root, repo)  # 네트워크 -- Hub 개수 대조
        if plan["action"] == "blocked":
            QMessageBox.warning(self, tr("이어붙이기 불가"),
                                tr("Hub 상태를 읽지 못했습니다: {e}\n확실하지 않은 "
                                   "채로 올리지 않습니다.").format(e=plan["error"]))
            return
        if plan["action"] == "rebuild":
            if plan["shrunk"]:
                msg = tr("이미 올라간 task에서 에피소드 {n}개가 삭제되었습니다.\n"
                         "이어붙이기는 추가만 할 수 있어 지운 에피소드가 Hub에 "
                         "남습니다.").format(n=plan["shrunk"])
            else:
                msg = tr("에피소드 이력이 Hub와 어긋난 task가 있습니다 (길이 지문 "
                         "불일치).\n이어붙이면 엉뚱한 에피소드가 붙습니다.")
            QMessageBox.warning(self, tr("이어붙이기 불가"),
                                msg + tr("\n'변환 + 업로드 (자동)'으로 전체 "
                                         "재빌드하세요."))
            return
        # 여기 남는 ambiguous 는 전부 개수가 Hub와 같은 task 다 (append 대상이
        # 아님 -- 대상이면서 이력이 어긋난 경우는 위 rebuild 로 빠졌다). 이번
        # 실행이 그 task 에 아무것도 추가하지 않으므로, 위험을 확인했다는
        # 체크만 받고 나머지 task 의 이어붙이기는 허용한다.
        if plan["ambiguous"] and not self._confirm_ambiguous_idle(plan["ambiguous"]):
            self.log("[LeRobot 이어붙이기] 취소했습니다 (이력 미확인).", "upload")
            return
        if plan["action"] == "up_to_date":
            QMessageBox.information(
                self, tr("이어붙일 것 없음"),
                tr("Hub이 이미 로컬과 같습니다 ({n}개 에피소드).")
                .format(n=plan["local_total"]))
            return
        root = self._recents.most_recent("lerobot_root",
                                         str(Path.home() / "lerobot_upload"))
        if QMessageBox.question(
                self, tr("LeRobot 이어붙이기"),
                tr("새 에피소드 {n}개만 변환해 이어붙입니다 "
                   "(Hub {h} → {l}).\n\n"
                   "· 로컬 변환 폴더를 비우고 Hub의 현재 상태를 기준으로 "
                   "받습니다: {o}\n"
                   "· 이미 올라간 에피소드는 다시 변환하지 않습니다\n\n진행할까요?")
                .format(n=plan["added"], h=plan["hub_total"],
                        l=plan["local_total"], o=root),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            self.log("[LeRobot 이어붙이기] 취소했습니다.", "upload")
            return
        self._recents.add("repo_id", repo)
        self._recents.add("lerobot_root", root)
        steps = [
            {"name": tr("LeRobot 변환 (이어붙이기)"), "program": sys.executable,
             "args": [CONVERT_SCRIPT, *paths, "--repo-id", repo, "--root", root,
                      "--resume"],
             "clear_root": root},
            {"name": tr("LeRobot 추가 업로드"), "program": sys.executable,
             "args": [CONVERT_SCRIPT, "--repo-id", repo, "--root", root,
                      "--push-only", "--no-private"]},
        ]
        self._start_pipeline(steps, tr("LeRobot 이어붙이기"))

    def _confirm_ambiguous_idle(self, tasks: list) -> bool:
        """이력 검증을 통과 못했지만 append 대상도 아닌 task 확인창.

        진행해도 이 task 들에는 아무것도 추가되지 않지만, 지우고 다시 찍은
        것이라면 Hub 에 옛 에피소드가 남아 있을 수 있다 -- 그 정리는 전체
        재빌드만 할 수 있다. 실수로 Yes 를 누르지 못하도록 체크박스를 켜야
        진행 버튼이 활성화된다.
        """
        box = QMessageBox(
            QMessageBox.Icon.Warning, tr("이력 확인 필요"),
            tr("다음 task는 개수는 Hub와 같지만 이력 검증(에피소드 길이 지문)을 "
               "통과하지 못했습니다:\n{t}\n\n이번 이어붙이기에서 이 task에는 "
               "아무것도 추가되지 않습니다. 다만 지우고 다시 찍은 것이라면 Hub에 "
               "옛 에피소드가 남아 있을 수 있고, 그 정리는 '변환 + 업로드 "
               "(자동)' 전체 재빌드만 할 수 있습니다.").format(
                   t="\n".join(f"· {x[:60]}" for x in tasks)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            self)
        yes = box.button(QMessageBox.StandardButton.Yes)
        yes.setText(tr("나머지 task만 이어붙이기 진행"))
        yes.setEnabled(False)
        ack = QCheckBox(tr("위 내용을 확인했습니다"))
        ack.toggled.connect(yes.setEnabled)
        box.setCheckBox(ack)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _count_hdf5_episodes(self) -> "int | None":
        """Episodes currently in the .hdf5 files. Metadata only -- no images."""
        try:
            import h5py
            total = 0
            for f in hdf5_files(self.root_edit.text().strip()):
                with h5py.File(f, "r") as h:
                    total += len(h["data"].keys())
            return total
        except Exception:  # noqa: BLE001
            return None

    def _on_lerobot_reupload(self) -> None:
        """재변환 없이, 이미 만들어둔 로컬 결과로 Hub을 교체."""
        if not self._pipeline_guard(tr("LeRobot 재업로드")):
            return
        repo = self._check_repo("repo_id", tr("전체 task 다시 업로드"))
        if repo is None:
            return
        root = self._recents.most_recent("lerobot_root", str(Path.home() / "lerobot_upload"))
        info = Path(root) / "meta" / "info.json"
        if not info.exists():
            QMessageBox.warning(
                self, tr("변환 결과 없음"),
                tr("{o} 에 변환 결과가 없습니다 ({i} 없음).\n"
                   "'변환 + 업로드 (자동)' 또는 'HDF5 골라서 변환만...'을 먼저 "
                   "실행하세요.").format(o=root, i=info.name))
            return
        try:
            meta = json.loads(info.read_text())
            n_ep, n_fr = meta.get("total_episodes", "?"), meta.get("total_frames", "?")
        except Exception:  # noqa: BLE001
            n_ep = n_fr = "?"
        # 변환 결과와 현재 .hdf5 의 개수를 맞춰본다. 큐레이션으로 에피소드를
        # 지운 뒤 재변환을 잊으면, 이 버튼은 삭제 이전 결과를 그대로 Hub에
        # 올려 큐레이션을 통째로 되돌린다 -- 그리고 개수만 보고는 눈치채기
        # 어렵다. 지금 세는 값과 나란히 놓으면 그 자리에서 보인다.
        n_local = self._count_hdf5_episodes()
        stale = isinstance(n_ep, int) and n_local is not None and n_ep != n_local
        head = (tr("⚠ 변환 결과가 최신이 아닙니다 — 변환본 {e}개 vs 현재 HDF5 {l}개\n"
                   "   지금 올리면 큐레이션으로 지운 에피소드가 되살아납니다.\n"
                   "   '변환 + 업로드 (자동)'으로 다시 만드세요.\n\n").format(e=n_ep, l=n_local)
                if stale else "")
        if QMessageBox.question(
                self, tr("전체 task 다시 업로드"),
                head + tr("{o} 의 변환 결과를 {r} 에 통째로 올립니다.\n\n"
                          "· 변환본 에피소드 {e}개 / 프레임 {f}\n"
                          "· 현재 HDF5 에피소드 {l}개\n"
                          "· 재변환은 하지 않습니다\n"
                          "· 로컬에 없는 원격 파일은 지웁니다 (큐레이션 삭제 반영)\n\n"
                          "이 로컬 결과가 최신인지 확인하셨나요?")
                .format(o=root, r=repo, e=n_ep, f=n_fr,
                        l=n_local if n_local is not None else "?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            self.log("[LeRobot 재업로드] 취소했습니다.", "upload")
            return
        self._start_pipeline([
            {"name": tr("LeRobot 교체 업로드"), "program": sys.executable,
             "args": [CONVERT_SCRIPT, "--repo-id", repo, "--root", root,
                      "--push-only", "--replace", "--no-private"]}],
            tr("LeRobot 재업로드"))

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
        repo = self._check_repo("repo_id", tr("전체 처리"))
        if repo is None:
            return
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
                             self.repo_id_for("hdf5_repo_id"),
                             self._recents.most_recent(
                                 "lerobot_root", str(Path.home() / "lerobot_upload")))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.log("[전체 처리] 취소했습니다.", "upload")
            return
        # 다이얼로그 안에서 바꾼 값도 검사한다. HDF5 업로드는 파이프라인의 마지막
        # 단계라, 여기서 막지 않으면 앞 단계를 다 돌고 나서야 403 으로 죽는다.
        for edit, key, what in ((dlg.lerobot_repo_edit, "repo_id", "LeRobot"),
                                (dlg.hdf5_repo_edit, "hdf5_repo_id", "HDF5")):
            if key == "hdf5_repo_id" and not dlg.hdf5_check.isChecked():
                continue
            err = repo_id_error(edit.text().strip())
            if err:
                QMessageBox.warning(self, tr("Repo ID 오류"),
                                    tr("{w} Repo ID: {e}").format(w=what, e=err))
                self.log(f"[전체 처리] 중단 — {what} Repo ID: {err}", "upload")
                return
            self.repo_edits[key].setText(edit.text().strip())
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
            lambda: [self.log(ln, "validation")
                     for ln in self._proc_text(proc).splitlines()])
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
            lambda: [self.log(f"[튜닝] {ln}")
                     for ln in self._proc_text(proc).splitlines() if ln.strip()])
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
        # 주소를 명시적으로 넘긴다. 노드는 다른 venv 에서 도는 별도
        # 프로세스라 스테이션 설정을 자기가 다시 읽는데, GELLO_STATION 이
        # 전달되지 않거나 그 사이 파일이 바뀌면 GUI 가 붙을 곳과 노드가 여는
        # 곳이 조용히 어긋난다. 여기서 넘기면 둘은 항상 같은 값을 본다.
        proc.setArguments([
            LAUNCH_NODES_SCRIPT,
            "--robot", STATION.robot.kind,
            "--robot-ip", STATION.robot.ip,
            "--robot-port", str(STATION.node.port),
            "--hostname", STATION.node.host,
            "--die-with-parent",
        ])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_node_output)
        proc.finished.connect(lambda c, _s: self.log(f"[노드] 종료 (exit={c})"))
        self.node_process = proc
        self.log("[노드] 시작합니다...")
        proc.start()

    def _on_node_output(self) -> None:
        if self.node_process is None:
            return
        data = self._proc_text(self.node_process)
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

    @staticmethod
    def _proc_text(proc: QProcess) -> str:
        """Reads a child's stdout, or "" once Qt has destroyed the QProcess.

        readyReadStandardOutput can still be delivered after the window (the
        QProcess's parent) is torn down, and reading a destroyed QProcess
        raises "wrapped C/C++ object ... has been deleted" -- during shutdown,
        where it surfaces as a crash dialog instead of a clean exit.
        """
        if proc is None or sip.isdeleted(proc):
            return ""
        return bytes(proc.readAllStandardOutput()).decode(errors="replace")

    def _pipe(self, proc: QProcess, prefix: str, view: str) -> None:
        data = self._proc_text(proc)
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
