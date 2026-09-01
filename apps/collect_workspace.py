"""Workspace-style GUI for collecting LIBERO-format demos via GELLO teleop.

Run inside lerobot-venv::

    (pylibfranka-venv) python scripts/launch/launch_nodes.py --robot fr3   # terminal 1
    (lerobot-venv)     python apps/collect_workspace.py          # terminal 2

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
import re
import webbrowser

# Must run before numpy/cv2/h5py are imported -- see gello/gui_widgets.py for
# why this GUI caps the BLAS/OpenCV thread pools at 1.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import json
import shutil
import sys
import tempfile
import traceback
import time
from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import (QEvent, QProcess, QSize, Qt, QThread, QTimer,
                          pyqtSignal, pyqtSlot)
from PyQt6.QtGui import QAction, QActionGroup, QFont, QIcon, QTextCursor
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
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
    QInputDialog,
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
    QSpinBox,
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
from gello.data.dataset_schema import (  # noqa: E402
    OBS_AGENTVIEW_RGB,
    load_schema_config,
    save_schema_config,
)
from gello.data.dataset_sync import plan_sync  # noqa: E402
from gello.data.episode_stats import (  # noqa: E402
    STILL_VEL,
    TASK_DEV_LIMIT,
    hdf5_files,
    load_series,
    scan_dataset,
    summarize,
)
from gello.data.episode_trim import plan_trim, suggest_trim, tail_speed, trim_tail  # noqa: E402
from gello.gui.plot_widgets import BarStrip, Histogram, SeriesPlot  # noqa: E402
from gello.gui.gui_widgets import (  # noqa: E402
    TODO_MARK,
    repo_id_error,
    PLAYBACK_FPS,
    REPACK_SCRIPT,
    CameraPreviewWorker,
    DatasetSchemaDialog,
    DepthCloudWorker,
    GalleryLoadWorker,
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
    np_to_pixmap,
)
from gello.gui.grid_overlay import (  # noqa: E402
    DEFAULT_CORNERS,
    active_corners,
    draw_grid,
    grid_segments,
    load_grid_store,
    save_grid_store,
)
from gello.gui.i18n import tr  # noqa: E402
from gello.data.libero_format import (  # noqa: E402
    default_crop_params,
    describe_episode,
    hdf5_repack_status,
    load_crop_params,
    renumber_episodes,
    resize_rgb,
    save_crop_params,
)
from gello.data.hub_upload_state import changed_files  # noqa: E402
from gello.scene.collection_plan import (  # noqa: E402
    PLANS_DIR,
    check_scene_against_plan,
    list_plans,
    load_plan,
)
from gello.gui.libero_gui_worker import GATE_RAD, CollectionWorker, WorkerConfig  # noqa: E402
from gello.scene.props import load_props, props_by_id  # noqa: E402
from gello.scene.scene_diversity import AXES, recommend_detailed  # noqa: E402
from gello.scene.skill_stats import (  # noqa: E402
    collected_skill_counts,
    format_skill_counts,
    rank_instructions,
)
from gello.scene.scene_rules import check  # noqa: E402
from gello.robots.franka_fr3 import FR3_RESET_POSES  # noqa: E402
from gello.scene.scene_format import (  # noqa: E402
    INSTRUCTION_ID_RE,
    SCENE_ID_RE,
    SceneMetadata,
    count_by_slot,
    delete_scene_episodes,
    describe_scene,
    iter_scene_files,
    next_scene_id,
    read_scene_metadata,
    scene_filename,
)
from gello.gui.scene_gallery import invalidate_scene_thumbs  # noqa: E402
from gello.core.station import load_station  # noqa: E402

LOG_DIR = Path.home() / "libero_gui_logs"
# 로봇 IP, ZMQ 주소, 카메라 스트림 포맷, 크롭 초기값은 전부 여기서 온다.
# GELLO_STATION 으로 고르고, 파일은 configs/stations/<이름>.yaml.
STATION = load_station()
PYLIBFRANKA_PYTHON = STATION.node.python_path
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "launch" / "launch_nodes.py")
CONVERT_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "convert" / "convert_libero_to_lerobot.py")
UPLOAD_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "convert" / "upload_to_hub.py")
REPLAY_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "analyze" / "replay_episode.py")
# 새 수집(scene 체계)의 Hub 저장소 기본값 (2026-08-18 결정). 변환본은
# -lerobot, 원본 HDF5 는 접미사 없이. legacy repo(fr3-pick-place*, 728개)는
# 재사용하지 않는다 -- 그쪽에 전체 처리를 돌리면 삭제 게이트가 뜬다.
DEFAULT_REPOS = {
    "repo_id": "knu-physical-ai/fr3-tabletop-lerobot",
    "hdf5_repo_id": "knu-physical-ai/fr3-tabletop",
}
# recents 에 남아 있어도 기본값으로 되살리지 않을 옛 저장소들.
LEGACY_REPOS = {
    "knu-physical-ai/fr3-pick-place-lerobot", "knu-physical-ai/fr3-pick-place",
}
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


def _depth_colormap(z: np.ndarray, zmax: float, zmin: float = 0.05) -> np.ndarray:
    """depth(m) → JET 컬러맵(가까움=빨강) + 오른쪽 척도 바.

    라이브 Depth 탭과 HDF5 뷰어의 depth 미리보기가 같은 매핑을 쓰게 하는
    단일 지점. 무측정/범위 밖은 검정.
    """
    import cv2

    valid = (z > zmin) & (z <= zmax)
    norm = np.zeros(z.shape, np.uint8)
    norm[valid] = (255 * (1.0 - z[valid] / zmax)).astype(np.uint8)
    rgb = cv2.cvtColor(cv2.applyColorMap(norm, cv2.COLORMAP_JET),
                       cv2.COLOR_BGR2RGB)
    rgb[~valid] = 0
    return _draw_depth_scale(rgb, zmax)


def _draw_depth_scale(rgb: np.ndarray, zmax: float) -> np.ndarray:
    """오른쪽 세로 컬러바 + 거리 눈금(m). 너무 작은 이미지에는 그리지 않는다."""
    import cv2

    h, w = rgb.shape[:2]
    if w < 240 or h < 160:
        return rgb
    bar_h, bar_w = int(h * 0.72), 18
    x0, y0 = w - bar_w - 10, (h - bar_h) // 2
    t = np.linspace(0.0, 1.0, bar_h, dtype=np.float32)     # 0=위=가까움
    col = (255 * (1.0 - t)).astype(np.uint8).reshape(-1, 1)
    bar = cv2.cvtColor(cv2.applyColorMap(np.repeat(col, bar_w, 1),
                                         cv2.COLORMAP_JET), cv2.COLOR_BGR2RGB)
    rgb[y0:y0 + bar_h, x0:x0 + bar_w] = bar
    cv2.rectangle(rgb, (x0 - 1, y0 - 1), (x0 + bar_w, y0 + bar_h),
                  (255, 255, 255), 1)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y0 + int(frac * (bar_h - 1))
        cv2.line(rgb, (x0 - 5, y), (x0 - 1, y), (255, 255, 255), 1)
        label = f"{frac * zmax:.2f}m"
        # 검정 외곽선 + 흰 글자 -- 어느 배경에서든 읽히게
        for color, thick in (((0, 0, 0), 3), ((255, 255, 255), 1)):
            cv2.putText(rgb, label, (x0 - 62, y + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, thick,
                        cv2.LINE_AA)
    return rgb


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
CHECK_CAMERAS = str(Path(__file__).resolve().parent.parent / "scripts" / "check" / "check_cameras.py")
RESET_PROTECTION = str(Path(__file__).resolve().parent.parent / "scripts" / "check" / "gello_reset_protection.py")


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
    "reset_wait": "Enter: 리셋 완료 — 계속   Esc: 직전 에피소드 판정 뒤집기",
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
        # 업로드 대상은 업로드 장부(gello/hub_upload_state.py)가 고른다:
        # 지난 업로드 성공 이후 (크기, mtime)이 바뀐 파일 + 기록 없는 파일
        # + 이번에 재압축될 파일. 예전의 "재압축한 파일만" 방식은 attr 만
        # 고친 파일(라벨 교정, 삭제·재번호)을 빠뜨렸다 -- 실제로 문법 교정분
        # 5개가 Hub에 안 올라간 사고가 있었다 (2026-08-25 교체). 변경 없는
        # 파일을 올려도 Hub이 해시로 전송은 건너뛰지만 그 판정에 파일 전체를
        # 읽는 시간이 들어서, 자동 선택이 그 시간을 없앤다. 장부가 모르는
        # 밖의 변화(Hub 쪽 삭제 등)를 위해 체크 해제 = 전체 강제 업로드
        # 탈출구를 남긴다. 파일마다 '왜 올라가는지'는 이 체크박스 툴팁과
        # 시작 로그, Hub 커밋 메시지 꼬리표 세 곳에 보인다.
        sel0 = self._hdf5_upload_selection(hdf5_repo)
        self.hdf5_only_new_check = QCheckBox(
            tr("  ↳ 변경된 파일만 자동 선택 ({n}개) — 해제하면 전체 강제 업로드")
            .format(n=len(sel0)))
        self.hdf5_only_new_check.setToolTip(
            "\n".join(f"{Path(x).name}: {r}" for x, r in sel0)
            or tr("지난 업로드 이후 바뀐 파일이 없습니다."))
        self.hdf5_only_new_check.setChecked(True)
        self.hdf5_only_new_check.setEnabled(False)  # hdf5_check 켜야 활성화
        self.hdf5_check.toggled.connect(self.hdf5_only_new_check.setEnabled)
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

        # 삭제 보호 게이트: Hub 에서 사라질 에피소드가 있으면(rebuild 로
        # 교체 시 실제 삭제) 이해했다는 체크 없이는 시작 버튼이 열리지
        # 않는다. legacy 파일을 old_data/ 로 치워 둔 상태에서 옛 repo 를
        # 대상으로 돌리면 수백 개가 조용히 사라지는 사고의 마지막 잠금이다.
        self.shrink_ack = None
        if plan.get("shrunk"):
            self.shrink_ack = QCheckBox(tr(
                "Hub에서 에피소드 {n}개가 삭제되는 것을 확인했습니다 "
                "(로컬에 없는 에피소드는 재빌드 후 Hub에서 사라집니다)")
                .format(n=plan["shrunk"]))
            self.shrink_ack.setStyleSheet("color:#e74c3c; font-weight:bold;")
            layout.addWidget(self.shrink_ack)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText(tr("시작하고 퇴근"))
        self._action_ok = action != "blocked"
        self._update_ok_enabled()
        if self.shrink_ack is not None:
            self.shrink_ack.toggled.connect(self._update_ok_enabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_ok_enabled(self, *_args) -> None:
        ok = self._action_ok
        if self.shrink_ack is not None and not self.shrink_ack.isChecked():
            ok = False
        self._ok.setEnabled(ok)

    def _on_account(self) -> None:
        HfAccountDialog(self).exec()
        text, color = hf_account()
        self.acct_label.setText(text)
        self.acct_label.setStyleSheet(f"color:{color}; font-weight:bold;")


    def _hdf5_upload_selection(self, repo: str) -> list:
        """업로드 대상 [(경로 str, 사유 str)] -- 장부 기준 변경/신규 파일에
        이번 실행에서 재압축될 파일을 합친다 (재압축은 mtime 을 바꾸므로
        다음 판정에는 어차피 걸리지만, 같은 실행 안에서 놓치지 않게)."""
        sel = {str(x): r for x, r in changed_files(repo, self.plan["paths"])}
        if getattr(self, "repack_check", None) is None or \
                self.repack_check.isChecked():
            for x in self._repack_todo:
                sel.setdefault(str(x), tr("재압축 — 이번 실행에서 다시 압축됨"))
        return [(x, sel[x]) for x in map(str, self.plan["paths"]) if x in sel]
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
            if self.hdf5_only_new_check.isChecked():
                # repo 를 다이얼로그에서 바꿨을 수 있으니 여기서 다시 판정한다.
                sel = self._hdf5_upload_selection(hdf5_repo)
                if sel:
                    steps.append({
                        "name": tr("HDF5 원본 업로드 (변경분 {n}개)")
                        .format(n=len(sel)),
                        "detail": "; ".join(
                            f"{Path(x).name}: {r}" for x, r in sel),
                        "program": sys.executable,
                        "args": [UPLOAD_SCRIPT, *[x for x, _ in sel],
                                 "--repo-id", hdf5_repo, "--no-private"]})
                else:
                    # 프로세스 없는 정보용 단계 -- '왜 안 올라갔는지'가
                    # 로그와 요약에 남는다.
                    steps.append({"name": tr("HDF5 원본 업로드 — 생략"),
                                  "note": tr("지난 업로드 이후 바뀐 파일이 "
                                             "없습니다 (장부 기준).")})
            else:
                steps.append({"name": tr("HDF5 원본 업로드 (전체 강제)"),
                              "program": sys.executable,
                              "args": [UPLOAD_SCRIPT, *paths, "--repo-id",
                                       hdf5_repo, "--no-private"]})
        return steps


def _relax_min_widths(root: QWidget) -> None:
    """좌측 패널은 가로 스크롤이 없으므로 자식들이 패널 폭에 맞춰 줄어들 수
    있어야 한다. 버튼·체크박스·라디오는 텍스트 전체 폭을 최소로 고집하는
    기본 정책이라 좁은 패널에서 페이지를 잘리게 만든다 -- 수평 최소를 풀어
    좁아지면 글자가 생략되는 쪽을 택한다 (2026-08-13 사용자 결정: 200px
    수준까지 축소 허용, ... 요약 표시 허용). '...' 찾아보기처럼 명시적으로
    고정폭을 준 위젯은 건드리지 않는다."""
    for w in root.findChildren(QWidget):
        if isinstance(w, (QPushButton, QCheckBox, QRadioButton)):
            if w.maximumWidth() >= 16777215:  # 명시 고정폭은 존중
                sp = w.sizePolicy()
                sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                w.setSizePolicy(sp)
    # 폼의 '라벨+입력 나란히' 배치도 최소 폭을 만든다 -- 좁아지면 입력칸이
    # 라벨 아래로 내려가게 해서 폭 하한을 더 낮춘다.
    for f in root.findChildren(QFormLayout):
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # 긴 안내문 라벨이 wordWrap 없이 폭을 강제하는 경우가 페이지마다 하나씩
    # 숨어 있다(업로드 큐 안내문 등). 일괄 줄바꿈 -- 단, 수평 Ignored 정책
    # 라벨(SceneInfoView 의 격자처럼 일부러 줄바꿈을 막은 것)은 제외.
    for lb in root.findChildren(QLabel):
        if lb.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Ignored:
            lb.setWordWrap(True)
            # wordWrap 만으로는 QFormLayout 이 높이를 한 줄치로 줘서 두 줄째가
            # 잘린다(오른쪽 패널 WIDE_FIELDS 에서 이미 확인된 Qt 동작).
            # heightForWidth 를 켜야 접힌 만큼 세로가 확보된다.
            sp = lb.sizePolicy()
            sp.setHeightForWidth(True)
            sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
            lb.setSizePolicy(sp)


def _shrinkable_combo(c: QComboBox) -> None:
    """항목 텍스트(카메라 이름, scene 설명 등)가 길어도 콤보가 패널 폭에 맞춰
    줄어들 수 있게 한다. 기본 정책은 가장 긴 항목만큼 최소 폭을 요구해서,
    좁은 좌측 패널에서 페이지 전체가 오른쪽으로 잘려 나갔다 (가로 스크롤을
    쓰지 않는다는 원칙과 충돌). 펼친 목록은 전체 텍스트를 그대로 보여준다."""
    c.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(6)


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


class PlanJsonDialog(QDialog):
    """수집 계획 원문(JSON) 편집 — 저장하려면 load_plan 검증을 통과해야 한다.

    기본 편집기는 폼 방식의 PlanEditDialog 다. 이것은 note 추가처럼 폼이
    다루지 않는 필드를 만질 때 쓰는 고급 진입로로만 남아 있다.
    """

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(tr("수집 계획 JSON 편집 — {n}").format(n=self._path.name))
        self.setMinimumSize(680, 480)
        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "저장하면 규칙 검증(scene 내 ID 유일, 따옴표 금지, target>0)을 "
            "통과해야 반영됩니다. 동사 집합(§4) 밖 문장은 경고만 합니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(
            "font-family: 'DejaVu Sans Mono', monospace; font-size: 12px;")
        try:
            self.editor.setPlainText(self._path.read_text(encoding="utf-8"))
        except OSError as e:
            self.editor.setPlainText("")
            QMessageBox.warning(self, tr("읽기 실패"), str(e))
        col.addWidget(self.editor, 1)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#e74c3c;")
        col.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

    def _save(self) -> None:
        import tempfile

        text = self.editor.toPlainText()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as tf:
                tf.write(text)
                tmp = Path(tf.name)
            plan = load_plan(tmp)
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            self.error_label.setText(f"{type(e).__name__}: {e}")
            return
        self._path.write_text(text, encoding="utf-8")
        self.warnings = plan.warnings
        super().accept()


class PlanEditDialog(QDialog):
    """수집 계획 편집 — scene 별로 (문장, 목표)만 표에서 고친다.

    나머지는 자동이다: 기존 행은 파일의 instruction_id 를 그대로 유지하고
    (수집된 에피소드와의 연결이 ID 에 걸려 있다), 새 행은 저장 시점에 그
    scene 의 다음 빈 번호를 받는다. 행을 지워도 남은 행의 ID 는 바뀌지
    않고, 지운 ID 번호도 재사용하지 않는다 -- 같은 번호가 다른 문장으로
    되살아나면 이미 수집된 데이터와 어긋난다. note 같은 부가 필드는 그대로
    보존하며, 저장은 여전히 load_plan 검증을 통과해야 반영된다.
    """

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.warnings: list = []
        self.setWindowTitle(tr("수집 계획 편집 — {n}").format(n=self._path.name))
        self.setMinimumSize(720, 480)
        self._cur_sid: "str | None" = None

        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "문장과 목표 개수만 고치면 됩니다. ID 는 자동입니다 — 기존 행은 "
            "번호를 유지하고, 새 행은 저장할 때 다음 번호를 받습니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)

        srow = QHBoxLayout()
        srow.addWidget(QLabel(tr("Scene")))
        self.scene_combo = QComboBox()
        self.scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        srow.addWidget(self.scene_combo, 1)
        add_scene_btn = QPushButton(tr("scene 추가"))
        add_scene_btn.clicked.connect(self._on_add_scene)
        srow.addWidget(add_scene_btn)
        del_scene_btn = QPushButton(tr("scene 삭제"))
        del_scene_btn.setToolTip(tr(
            "이 scene 을 계획에서 뺍니다. 이미 수집한 파일은 지워지지 않지만 "
            "계획 대조가 사라집니다."))
        del_scene_btn.clicked.connect(self._on_del_scene)
        srow.addWidget(del_scene_btn)
        json_btn = QPushButton(tr("JSON 직접 편집..."))
        json_btn.setToolTip(tr("note 등 폼이 다루지 않는 필드를 고칠 때 씁니다."))
        json_btn.clicked.connect(self._on_raw_edit)
        srow.addWidget(json_btn)
        col.addLayout(srow)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("ID"), tr("문장 (instruction)"), tr("목표")])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        col.addWidget(self.tree, 1)

        rrow = QHBoxLayout()
        add_btn = QPushButton(tr("행 추가"))
        add_btn.clicked.connect(lambda: self._add_row(
            {"id": None, "instr": "", "target": 10}))
        rrow.addWidget(add_btn)
        del_btn = QPushButton(tr("선택 행 삭제"))
        del_btn.clicked.connect(self._on_del_row)
        rrow.addWidget(del_btn)
        up_btn = QPushButton("▲")
        up_btn.setMaximumWidth(36)
        up_btn.setToolTip(tr("선택 행을 위로 (표시 순서만 -- ID 는 안 바뀝니다)"))
        up_btn.clicked.connect(lambda: self._move_row(-1))
        rrow.addWidget(up_btn)
        down_btn = QPushButton("▼")
        down_btn.setMaximumWidth(36)
        down_btn.clicked.connect(lambda: self._move_row(+1))
        rrow.addWidget(down_btn)
        compact_btn = QPushButton(tr("번호 정리"))
        compact_btn.setToolTip(tr(
            "이 scene 의 ID 를 표 순서대로 I000..I{N-1} 로 다시 매깁니다.\n"
            "에피소드가 하나도 수집되지 않은 scene 에서만 가능합니다 --\n"
            "수집된 scene 의 ID 는 데이터와의 연결 고리라 재부여하지 않습니다."))
        compact_btn.clicked.connect(self._on_compact_ids)
        rrow.addWidget(compact_btn)
        rrow.addStretch(1)
        col.addLayout(rrow)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#e74c3c;")
        col.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._load_file()

    def _load_file(self) -> None:
        """파일 -> 작업본 -> 표. raw 편집 뒤에도 이걸로 되돌아온다.

        작업본은 scene ID -> [{"id": I000|None, "instr", "target"}] 이고,
        표는 scene 전환 때마다 여기서 다시 그린다.
        """
        try:
            self._raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("읽기 실패"), str(e))
            self._raw = {"plan_version": 1, "scenes": []}
        if not isinstance(self._raw.get("scenes"), list):
            self._raw["scenes"] = []
        self._work = {}
        self._scene_order = []
        for sc in self._raw["scenes"]:
            sid = sc.get("scene_id")
            if not isinstance(sid, str):
                continue
            self._scene_order.append(sid)
            self._work[sid] = [
                {"id": sl.get("instruction_id"),
                 "instr": str(sl.get("instruction", "")),
                 "target": int(sl.get("target") or 1)}
                for sl in sc.get("slots", []) if isinstance(sl, dict)]
        self._cur_sid = None
        self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        for sid in self._scene_order:
            self.scene_combo.addItem(sid)
        self.scene_combo.blockSignals(False)
        self.tree.clear()
        if self._scene_order:
            self._cur_sid = self._scene_order[0]
            self.scene_combo.setCurrentIndex(0)
            self._load_rows(self._cur_sid)

    # ---- 표 <-> 작업본 ----
    def _add_row(self, row: dict) -> None:
        it = QTreeWidgetItem([row["id"] or tr("(자동)"), "", ""])
        it.setData(0, Qt.ItemDataRole.UserRole, row["id"])
        self.tree.addTopLevelItem(it)
        instr = QLineEdit(row["instr"])
        instr.setPlaceholderText(tr("예) pick up the blue cup and place it on the blue bowl"))
        instr.textChanged.connect(self._check_dups)   # 중복은 저장 전에 보이게
        self.tree.setItemWidget(it, 1, instr)
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(max(1, row["target"]))
        self.tree.setItemWidget(it, 2, spin)

    def _collect_rows(self) -> list:
        rows = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            rows.append({"id": it.data(0, Qt.ItemDataRole.UserRole),
                         "instr": self.tree.itemWidget(it, 1).text().strip(),
                         "target": self.tree.itemWidget(it, 2).value()})
        return rows

    def _load_rows(self, sid: str) -> None:
        self.tree.clear()
        for row in self._work.get(sid, []):
            self._add_row(row)

    def _stash_current(self) -> None:
        if self._cur_sid is not None:
            self._work[self._cur_sid] = self._collect_rows()

    def _on_scene_changed(self, *_args) -> None:
        self._stash_current()
        self._cur_sid = self.scene_combo.currentText() or None
        if self._cur_sid is not None:
            self._load_rows(self._cur_sid)

    def _on_del_row(self) -> None:
        for it in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        self._check_dups()

    def _move_row(self, delta: int) -> None:
        idx = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        j = idx + delta
        if idx < 0 or not 0 <= j < self.tree.topLevelItemCount():
            return
        rows = self._collect_rows()
        rows[idx], rows[j] = rows[j], rows[idx]
        self.tree.clear()
        for r in rows:
            self._add_row(r)
        self.tree.setCurrentItem(self.tree.topLevelItem(j))

    def _check_dups(self, *_args) -> None:
        instrs = [r["instr"] for r in self._collect_rows() if r["instr"]]
        dup = sorted({s for s in instrs if instrs.count(s) > 1})
        if dup:
            self.error_label.setText(tr(
                "같은 문장이 여러 행에 있습니다: {s}").format(s=dup[0][:60]))
        elif self.error_label.text().startswith(tr("같은 문장이")):
            self.error_label.setText("")

    def _scene_has_episodes(self, sid: str) -> "bool | None":
        """이 scene 의 수집 파일에 에피소드가 있는가. None = 확인 불가(잠금 등)."""
        try:
            from gello.scene.scene_format import list_scene_episodes, scene_filename

            root = Path(Recents().most_recent("data_root",
                                              str(Path.home() / "libero_datasets")))
            path = root / scene_filename(sid)
            if not path.exists():
                return False
            return len(list_scene_episodes(path)) > 0
        except Exception:  # noqa: BLE001 -- 세션이 쥔 파일 등
            return None

    def _on_compact_ids(self) -> None:
        """빈 scene 한정 ID 압축 (2026-08-24 결정: 데이터가 없으면 번호를
        다시 매겨도 안전하고, 있으면 일관성을 위해 건드리지 않는다)."""
        sid = self._cur_sid
        if sid is None:
            return
        has = self._scene_has_episodes(sid)
        if has is None:
            QMessageBox.warning(self, tr("번호 정리 불가"), tr(
                "{s} 의 수집 파일을 확인할 수 없습니다 (수집 세션이 사용 중일 수 "
                "있음). 세션 종료 후 다시 시도하세요.").format(s=sid))
            return
        if has:
            QMessageBox.warning(self, tr("번호 정리 불가"), tr(
                "{s} 에는 이미 수집된 에피소드가 있습니다. ID 는 데이터와의 "
                "연결 고리라 재부여하지 않습니다 (지운 번호 재사용 금지 규칙)."
            ).format(s=sid))
            return
        rows = self._collect_rows()
        for i, r in enumerate(rows):
            r["id"] = f"I{i:03d}"
        self._work[sid] = rows
        self._load_rows(sid)
        self.error_label.setText("")
        QMessageBox.information(self, tr("번호 정리"), tr(
            "{s} 의 ID 를 I000..I{n:03d} 로 다시 매겼습니다. 저장을 눌러야 "
            "반영됩니다.").format(s=sid, n=len(rows) - 1))

    def _on_del_scene(self) -> None:
        sid = self._cur_sid
        if sid is None:
            return
        ans = QMessageBox.question(
            self, tr("scene 삭제"),
            tr("{s} 를 계획에서 뺄까요? (수집 파일은 그대로 남습니다)")
            .format(s=sid))
        if ans != QMessageBox.StandardButton.Yes:
            return
        i = self._scene_order.index(sid)
        self._scene_order.remove(sid)
        self._work.pop(sid, None)
        self._cur_sid = None
        self.scene_combo.blockSignals(True)
        self.scene_combo.removeItem(i)
        self.scene_combo.blockSignals(False)
        self.tree.clear()
        if self._scene_order:
            self._cur_sid = self.scene_combo.currentText() or None
            if self._cur_sid:
                self._load_rows(self._cur_sid)

    def _on_add_scene(self) -> None:
        used = [int(m.group(1)) for sid in self._scene_order
                if (m := SCENE_ID_RE.match(sid))]
        sid = f"S{(max(used) + 1) if used else 0:03d}"
        self._scene_order.append(sid)
        self._work[sid] = []
        self.scene_combo.addItem(sid)
        self.scene_combo.setCurrentIndex(self.scene_combo.count() - 1)

    def _on_raw_edit(self) -> None:
        dlg = PlanJsonDialog(self, self._path)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # 파일이 정본이므로 폼을 파일 기준으로 다시 세운다 (표의 미저장
        # 수정은 버려진다 -- raw 편집이 이미 파일을 바꿨다)
        self.warnings = list(getattr(dlg, "warnings", []))
        self._load_file()

    # ---- 저장 ----
    def _save(self) -> None:
        import tempfile

        self._stash_current()
        raw = json.loads(json.dumps(self._raw))     # 부가 필드 보존용 사본
        raw.setdefault("plan_version", 1)
        by_id = {s.get("scene_id"): s for s in raw["scenes"]}
        # scene 목록은 폼이 정본 -- 폼에서 지운 scene 은 파일에서도 빠진다
        raw["scenes"] = []
        for sid in self._scene_order:
            sc = by_id.get(sid)
            if sc is None:
                sc = {"scene_id": sid, "slots": []}
            raw["scenes"].append(sc)
            old = {sl.get("instruction_id"): sl
                   for sl in sc.get("slots", []) if isinstance(sl, dict)}
            rows = self._work.get(sid, [])
            # 지워진 ID 도 사용된 번호로 친다 -- 번호 재사용 금지
            used = {int(m.group(1)) for iid in
                    list(old) + [r["id"] for r in rows if r["id"]]
                    if (m := INSTRUCTION_ID_RE.match(iid or ""))}
            slots = []
            for r in rows:
                if not r["id"]:
                    n = max(used, default=-1) + 1
                    used.add(n)
                    r["id"] = f"I{n:03d}"
                sl = dict(old.get(r["id"], {}))
                sl["instruction_id"] = r["id"]
                sl["instruction"] = r["instr"]
                sl["target"] = r["target"]
                slots.append(sl)
            sc["slots"] = slots
        text = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as tf:
                tf.write(text)
                tmp = Path(tf.name)
            plan = load_plan(tmp)
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            self.error_label.setText(f"{type(e).__name__}: {e}")
            return
        self._path.write_text(text, encoding="utf-8")
        self.warnings = plan.warnings
        super().accept()


class Hdf5TreeDialog(QDialog):
    """HDF5 내부 구조 뷰어 — myHDF5(h5web)처럼 트리 + attrs + 미리보기.

    구조(이름·shape·dtype·압축·attrs)는 열 때 한 번 읽고 파일을 바로
    닫는다 — 뷰어가 파일을 쥔 채로 있으면 수집/재압축과 부딪힌다. 값·이미지
    미리보기만 항목을 클릭할 때 잠깐 다시 연다.
    """

    PREVIEW_ELEMS = 120     # 이 개수 이하의 수치 데이터셋은 값을 그대로 보여준다

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(tr("HDF5 구조 — {n}").format(n=self._path.name))
        self.resize(960, 620)
        col = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("이름"), tr("정보")])
        self.tree.setColumnWidth(0, 300)
        self.tree.currentItemChanged.connect(self._on_select)
        split.addWidget(self.tree)
        right = QWidget()
        rcol = QVBoxLayout(right)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(200)
        rcol.addWidget(self.preview)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setStyleSheet(
            "font-family: 'DejaVu Sans Mono', monospace; font-size: 12px;")
        rcol.addWidget(self.detail, 1)
        split.addWidget(right)
        split.setSizes([420, 540])
        col.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        try:
            with h5py.File(self._path, "r") as f:
                self._populate(f, self.tree.invisibleRootItem())
        except BlockingIOError:
            self.detail.setPlainText(tr(
                "파일이 사용 중입니다 (수집 세션/재압축). 끝난 뒤 다시 여세요."))
        except OSError as e:
            self.detail.setPlainText(tr("파일을 열지 못했습니다: {e}").format(e=e))
        self.tree.expandToDepth(0)

    def _populate(self, node, parent_item) -> None:
        # metadata 를 맨 위로 -- 파일을 여는 사람이 먼저 찾는 것이 scene
        # 정의다 (HDF5 그룹은 순서가 없어 뷰어가 정렬을 정한다).
        for key in sorted(node, key=lambda k: (k != "metadata", k)):
            obj = node[key]
            if isinstance(obj, h5py.Group):
                it = QTreeWidgetItem(
                    [key, tr("그룹 · 항목 {n} · attrs {a}")
                     .format(n=len(obj), a=len(obj.attrs))])
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
                it.setData(0, Qt.ItemDataRole.UserRole, obj.name)
                parent_item.addChild(it)
                self._add_attr_items(obj, it)
                self._populate(obj, it)
            else:
                shape = " × ".join(str(s) for s in obj.shape) or tr("스칼라")
                info = f"{shape} · {obj.dtype}"
                if obj.compression:
                    info += f" · {obj.compression}"
                it = QTreeWidgetItem([key, info])
                it.setData(0, Qt.ItemDataRole.UserRole, obj.name)
                parent_item.addChild(it)
                self._add_attr_items(obj, it)

    def _add_attr_items(self, obj, parent_item) -> None:
        """attrs 를 트리에 '@이름' 회색 항목으로 직접 보여준다 -- myHDF5 는
        오른쪽 패널에만 보여줘서 'scene_id 가 없다'는 오해가 실제로 있었다."""
        for k in sorted(obj.attrs):
            s = str(obj.attrs[k])
            it = QTreeWidgetItem(
                [f"@{k}", s[:80] + ("…" if len(s) > 80 else "")])
            # 회색은 '비활성/숨김'으로 읽힌다 (실사용 피드백) -- 기울임꼴만으로
            # 데이터셋과 구분하고 색은 그대로 둔다.
            f = it.font(0)
            f.setItalic(True)
            it.setFont(0, f)
            it.setFont(1, f)
            it.setData(0, Qt.ItemDataRole.UserRole, ("attr", obj.name, k))
            parent_item.addChild(it)

    def _on_select(self, item, _prev=None) -> None:
        self.preview.clear()
        if item is None:
            return
        h5path = item.data(0, Qt.ItemDataRole.UserRole)
        if not h5path:
            return
        if isinstance(h5path, tuple) and h5path[0] == "attr":
            _tag, owner, key = h5path
            try:
                with h5py.File(self._path, "r") as f:
                    v = f[owner].attrs[key]
                self.detail.setPlainText(
                    f"attr: {owner}/@{key}\n타입: {type(v).__name__}\n\n{v}")
            except Exception as e:  # noqa: BLE001
                self.detail.setPlainText(f"{type(e).__name__}: {e}")
            return
        try:
            with h5py.File(self._path, "r") as f:
                obj = f[h5path]
                lines = [f"경로: {h5path}"]
                if isinstance(obj, h5py.Dataset):
                    lines.append(f"shape: {tuple(obj.shape)}   dtype: {obj.dtype}")
                    lines.append(f"압축: {obj.compression or '-'}   "
                                 f"chunks: {obj.chunks or '-'}")
                    nbytes = obj.size * obj.dtype.itemsize
                    lines.append(f"크기(비압축): {nbytes / 1e6:.1f} MB")
                if len(obj.attrs):
                    lines.append("")
                    lines.append("── attrs " + "─" * 30)
                    for k in sorted(obj.attrs):
                        v = obj.attrs[k]
                        s = str(v)
                        lines.append(f"{k}: {s[:500]}" + ("…" if len(s) > 500 else ""))
                if isinstance(obj, h5py.Dataset):
                    arr = None
                    if obj.dtype == np.uint8 and obj.ndim == 4 and obj.shape[-1] == 3:
                        arr = obj[0]
                        lines.append("")
                        lines.append(tr("(첫 프레임 미리보기)"))
                    elif obj.dtype == np.uint8 and obj.ndim == 3 and obj.shape[-1] == 3:
                        arr = obj[...]
                        lines.append("")
                        lines.append(tr("(이미지 미리보기)"))
                    elif obj.dtype == np.uint16 and obj.ndim in (2, 3):
                        # depth (#17): mm -> m 변환 후 라이브 Depth 탭과 같은
                        # 컬러맵 + 척도 바
                        z = (obj[0] if obj.ndim == 3 else obj[...]) / 1000.0
                        valid = z > 0
                        zmax = float(np.percentile(z[valid], 98)) if valid.any() else 1.0
                        arr = _depth_colormap(z.astype(np.float32), zmax)
                        lines.append("")
                        lines.append(tr("(depth 첫 프레임 · 척도 ~{m:.2f}m)")
                                     .format(m=zmax))
                    elif obj.size and obj.size <= self.PREVIEW_ELEMS:
                        lines.append("")
                        lines.append("── 값 " + "─" * 32)
                        lines.append(np.array2string(
                            np.asarray(obj[...]), precision=4, threshold=200))
                    if arr is not None:
                        pix = np_to_pixmap(np.ascontiguousarray(arr))
                        self.preview.setPixmap(pix.scaled(
                            self.preview.width(), max(200, self.preview.height()),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))
                self.detail.setPlainText("\n".join(lines))
        except BlockingIOError:
            self.detail.setPlainText(tr("파일이 사용 중이라 값을 읽지 못했습니다."))
        except Exception as e:  # noqa: BLE001
            self.detail.setPlainText(f"{type(e).__name__}: {e}")


class _GridCanvas(QLabel):
    """격자 편집 캔버스 — 배경 이미지 위에서 꼭짓점 4개를 드래그한다.

    꼭짓점은 정규화 좌표(0..1)로 들고 있어 배경 해상도와 무관하다.
    드래그 중에는 외곽선·핸들만 갱신하고, 내부 3×3 선은 '변환' 버튼이
    다시 그린다 (full_grid 플래그).
    """

    changed = pyqtSignal()
    drag_started = pyqtSignal()     # 실행취소 스냅샷 시점
    HANDLE_PX = 22          # 위젯 픽셀 기준 잡기 반경

    def __init__(self, background: np.ndarray, corners: list) -> None:
        super().__init__()
        self._img = np.ascontiguousarray(background)
        self.corners = [list(c) for c in corners]
        self.full_grid = True
        self.crop_params: "dict | None" = None   # {"zoom","x","y"} -- agent 크롭
        self.show_crop = False
        self._drag: "int | None" = None
        self._fit = (1.0, 0, 0)     # scale, x-offset, y-offset (위젯 좌표계)
        self.setMinimumSize(480, 360)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(False)

    # ---- 렌더 ----
    def render_grid(self) -> None:
        import cv2

        h, w = self._img.shape[:2]
        if self.full_grid:
            out = draw_grid(self._img, self.corners, 80)
        else:
            out = self._img.copy()
        if self.show_crop and self.crop_params:
            out = self._crop_shade(out)
        pts = np.int32([[c[0] * w, c[1] * h] for c in self.corners])
        cv2.polylines(out, [pts.reshape(-1, 1, 2)], True, (80, 255, 140),
                      max(1, round(w / 320)), cv2.LINE_AA)
        r = max(4, round(w / 90))
        for i, (x, y) in enumerate(pts):
            cv2.circle(out, (int(x), int(y)), r, (255, 80, 80), -1, cv2.LINE_AA)
            cv2.putText(out, "1234"[i], (int(x) + r + 2, int(y) - r),
                        cv2.FONT_HERSHEY_SIMPLEX, w / 1200,
                        (255, 220, 220), 1, cv2.LINE_AA)
        pix = np_to_pixmap(out)
        avail_w, avail_h = max(1, self.width()), max(1, self.height())
        scale = min(avail_w / w, avail_h / h)
        sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
        self._fit = (scale, (avail_w - sw) // 2, (avail_h - sh) // 2)
        self.setPixmap(pix.scaled(sw, sh,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))

    def _crop_shade(self, img: np.ndarray) -> np.ndarray:
        """변환 파이프라인의 정사각 크롭 밖을 어둡게 -- 라이브 뷰의 크롭
        가이드(VideoView._decorate)와 같은 수식이라 보이는 영역이 일치한다."""
        import cv2

        h, w = img.shape[:2]
        side = min(w, h)
        z = float(self.crop_params.get("zoom", 1.0))
        if z > 1.0:
            side = max(16, round(side / z))
        sc = w / 640
        x0 = min(max((w - side) // 2
                     + round(self.crop_params.get("x", 0) * sc), 0), w - side)
        y0 = min(max((h - side) // 2
                     + round(self.crop_params.get("y", 0) * sc), 0), h - side)
        img[:y0] //= 2
        img[y0 + side:] //= 2
        img[y0:y0 + side, :x0] //= 2
        img[y0:y0 + side, x0 + side:] //= 2
        cv2.rectangle(img, (x0, y0), (x0 + side - 1, y0 + side - 1),
                      (255, 255, 255), 1)
        return img

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.render_grid()

    # ---- 좌표 변환/드래그 ----
    def _to_norm(self, pos) -> "tuple[float, float]":
        scale, ox, oy = self._fit
        h, w = self._img.shape[:2]
        return ((pos.x() - ox) / (w * scale), (pos.y() - oy) / (h * scale))

    def _pick(self, pos) -> "int | None":
        scale, ox, oy = self._fit
        h, w = self._img.shape[:2]
        best, best_d = None, self.HANDLE_PX
        for i, (cx, cy) in enumerate(self.corners):
            dx = cx * w * scale + ox - pos.x()
            dy = cy * h * scale + oy - pos.y()
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._drag = self._pick(event.position())
        if self._drag is not None:
            self.drag_started.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag is None:
            return
        x, y = self._to_norm(event.position())
        self.corners[self._drag] = [min(1.0, max(0.0, x)),
                                    min(1.0, max(0.0, y))]
        self.full_grid = False      # 내부선은 '변환'이 다시 계산한다
        self.render_grid()
        self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._drag = None


class GridEditorDialog(QDialog):
    """워크스페이스 3×3 격자 편집 — 드래그·정렬·변환·저장/불러오기.

    배경은 호출 시점의 agent 카메라 프레임(없으면 레이아웃 스틸/회색판).
    저장하면 workspace_grids.json 의 해당 이름에 기록되고 active 로 지정돼
    Live 오버레이가 바로 이 격자를 쓴다.
    """

    def __init__(self, parent, background: np.ndarray, store: dict,
                 crop_params: "dict | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("3×3 격자 편집"))
        self._store = store
        self._crop_params = crop_params
        corners = active_corners(store) or DEFAULT_CORNERS
        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "꼭짓점(1=좌상, 2=우상, 3=우하, 4=좌하)을 드래그해 작업면에 맞추고 "
            "'변환'으로 내부 3×3 선을 다시 그립니다. 정렬 버튼은 위/아래 두 "
            "꼭짓점의 높이를 맞춥니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)
        self.canvas = _GridCanvas(background, corners)
        col.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        for text, slot, tip in (
                ("위 정렬", lambda: self._align(0, 1, 1), "1·2번 꼭짓점을 같은 높이로"),
                ("아래 정렬", lambda: self._align(3, 2, 1), "4·3번 꼭짓점을 같은 높이로"),
                ("좌 정렬", lambda: self._align(0, 3, 0), "1·4번 꼭짓점을 같은 가로 위치로"),
                ("우 정렬", lambda: self._align(1, 2, 0), "2·3번 꼭짓점을 같은 가로 위치로"),
                ("변환 (3×3 다시 그리기)", self._transform,
                 "현재 꼭짓점으로 내부 격자선을 원근 계산해 그립니다"),
                ("실행취소", self._undo, "마지막 드래그/정렬 하나를 되돌립니다")):
            b = QPushButton(tr(text))
            b.setToolTip(tr(tip))
            b.clicked.connect(slot)
            row.addWidget(b)
        self.crop_check = QCheckBox(tr("크롭 가이드"))
        self.crop_check.setToolTip(tr(
            "LeRobot 변환 때 남는 정사각 영역 밖을 어둡게 표시합니다.\n"
            "격자(물체 배치)가 학습 화면 안에 들어오는지 확인용."))
        self.crop_check.setEnabled(crop_params is not None)
        self.crop_check.setChecked(crop_params is not None)
        self.crop_check.toggled.connect(self._on_crop_toggled)
        row.addWidget(self.crop_check)
        row.addStretch(1)
        col.addLayout(row)

        srow = QHBoxLayout()
        self.load_combo = QComboBox()
        for name in sorted(store["grids"]):
            self.load_combo.addItem(name)
        srow.addWidget(self.load_combo, 1)
        load_btn = QPushButton(tr("불러오기"))
        load_btn.clicked.connect(self._load_selected)
        srow.addWidget(load_btn)
        srow.addSpacing(16)
        self.name_edit = QLineEdit(store.get("active") or "default")
        self.name_edit.setPlaceholderText(tr("저장 이름"))
        srow.addWidget(self.name_edit, 1)
        save_btn = QPushButton(tr("저장"))
        save_btn.setToolTip(tr("이 이름으로 저장하고 active 격자로 지정합니다."))
        save_btn.clicked.connect(self._save)
        srow.addWidget(save_btn)
        col.addLayout(srow)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#2ecc71;")
        col.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._undo_stack: list = []
        self.canvas.changed.connect(lambda: self.status_label.setText(""))
        self.canvas.drag_started.connect(self._push_undo)
        self.canvas.crop_params = crop_params
        self.canvas.show_crop = self.crop_check.isChecked()
        self.canvas.render_grid()

    def _on_crop_toggled(self, on: bool) -> None:
        self.canvas.show_crop = bool(on)
        self.canvas.render_grid()

    def _push_undo(self) -> None:
        self._undo_stack.append([list(c) for c in self.canvas.corners])
        del self._undo_stack[:-20]      # 최근 20단계면 충분

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self.canvas.corners = self._undo_stack.pop()
        self.canvas.full_grid = True
        self.canvas.render_grid()

    def _align(self, i: int, j: int, axis: int) -> None:
        self._push_undo()
        c = self.canvas.corners
        v = (c[i][axis] + c[j][axis]) / 2
        c[i][axis] = c[j][axis] = v
        self.canvas.render_grid()

    def _transform(self) -> None:
        self.canvas.full_grid = True
        self.canvas.render_grid()

    def _load_selected(self) -> None:
        name = self.load_combo.currentText()
        corners = self._store["grids"].get(name)
        if not corners:
            return
        self.canvas.corners = [list(c) for c in corners]
        self.canvas.full_grid = True
        self.canvas.render_grid()
        self.name_edit.setText(name)
        self.status_label.setText(tr("{n} 불러옴").format(n=name))

    def _save(self) -> None:
        name = self.name_edit.text().strip() or "default"
        self._store["grids"][name] = [list(c) for c in self.canvas.corners]
        self._store["active"] = name
        save_grid_store(self._store)
        if self.load_combo.findText(name) < 0:
            self.load_combo.addItem(name)
        self.status_label.setText(tr("{n} 저장됨 (active)").format(n=name))


class RecommendWorker(QThread):
    """scene 추천 계산을 GUI 스레드 밖에서 수행한다."""

    # QThread 자체의 finished 시그널을 가리면 안 되므로(수명 관리가 그걸 쓴다)
    # 결과 시그널은 recs_ready 로 명명한다 -- 리포의 다른 QThread 들(loaded,
    # frame_ready, cloud_ready)과 같은 관례.
    recs_ready = pyqtSignal(list, object)   # (detailed 추천, 스킬 Counter)
    error = pyqtSignal(str)

    def __init__(self, existing: list, props: dict, k: int,
                 seed: int, scene_id: str,
                 data_root: "Path | None" = None) -> None:
        super().__init__()
        self._existing = existing
        self._props = props
        self._k = k
        self._seed = seed
        self._scene_id = scene_id
        self._data_root = data_root

    def run(self) -> None:
        try:
            # 스킬별 누적 수집량 -- 지시문 랭킹용. HDF5 IO 라 워커에서 센다.
            counts = collected_skill_counts(self._data_root)
            recs = recommend_detailed(self._existing, self._props, k=self._k,
                                      seed=self._seed,
                                      scene_id=self._scene_id)
            self.recs_ready.emit(recs, counts)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


class RecommendDialog(QDialog):
    """scene 다양성 추천안 3개 중 하나 고르기 + 문장 체크리스트 + 계획 등록."""

    def __init__(self, parent, existing: list, props: dict,
                 scene_id: str, plan_path: "Path | None" = None,
                 data_root: "Path | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("scene 추천 — 기존 {n}개 기준 (거리 버킷 + 커버리지)")
                            .format(n=len(existing)))
        self.setMinimumSize(620, 720)
        self._existing = existing
        self._props = props
        self._scene_id = scene_id
        self._plan_path = plan_path
        self._data_root = data_root
        self._skill_counts = None          # 워커가 채움 (Counter)
        self.picked = None                 # accept 시 SceneMetadata
        self.registered_plan_path: "Path | None" = None  # 등록 성공 시 경로
        self._recs: list = []
        self._radios: list = []
        self._sentence_checks: list[list[QCheckBox]] = []
        self._worker: RecommendWorker | None = None
        # 아직 도는 옛 워커들의 파이썬 참조. 참조를 버리면 GC 가 실행 중
        # QThread 를 파괴해 "Destroyed while thread is still running" 으로
        # 프로세스가 abort 한다 -- 결과는 워커 정체성 비교로 무시하고,
        # 참조는 스레드가 끝날 때(finished) 거둔다.
        self._stale_workers: list[RecommendWorker] = []

        col = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("seed")))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 9999)
        top.addWidget(self.seed_spin)
        self.again_btn = QPushButton(tr("다시 추천"))
        self.again_btn.clicked.connect(self._fill)
        top.addWidget(self.again_btn)
        self.status_label = QLabel("")
        top.addWidget(self.status_label, 1)
        top.addStretch(1)
        col.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards = QWidget()
        self._cards_col = QVBoxLayout(self._cards)
        scroll.setWidget(self._cards)
        col.addWidget(scroll, 1)

        if self._plan_path is not None:
            self._register_check = QCheckBox(
                tr("채택 시 선택한 문장을 계획 {n} 에 등록 (target=10)")
                .format(n=self._plan_path.name))
            self._register_check.setChecked(True)
            col.addWidget(self._register_check)
        else:
            self._register_check = None

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._fill()

    def _clear_cards(self) -> None:
        while self._cards_col.count():
            it = self._cards_col.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self._radios = []
        self._sentence_checks = []

    def _fill(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            # 강제 중단하지 않는다 (recommend() 는 인터럽트를 보지 않는다) --
            # 참조만 보관해 GC 파괴를 막고, 낡은 결과는 정체성 비교로 버린다.
            self._stale_workers.append(self._worker)
        self._clear_cards()
        self.again_btn.setEnabled(False)
        self.status_label.setText(tr("추천 계산 중..."))
        w = RecommendWorker(
            self._existing, self._props, k=3,
            seed=self.seed_spin.value(), scene_id=self._scene_id,
            data_root=self._data_root)
        self._worker = w
        w.recs_ready.connect(
            lambda recs, counts, w=w: self._on_recs_ready(w, recs, counts))
        w.error.connect(lambda msg, w=w: self._on_recs_error(w, msg))
        w.finished.connect(lambda w=w: self._reap(w))
        w.start()

    def _reap(self, w: RecommendWorker) -> None:
        """끝난 옛 워커의 참조 회수 (QThread 기본 finished 시그널 경유)."""
        if w in self._stale_workers and not w.isRunning():
            self._stale_workers.remove(w)

    def _wait_workers(self) -> None:
        """다이얼로그가 닫히기 전에 도는 워커를 기다린다 -- 다이얼로그 소멸과
        함께 워커가 GC 되면 실행 중 파괴로 abort 한다."""
        for w in [self._worker, *self._stale_workers]:
            if w is not None and w.isRunning():
                w.wait(10000)

    def done(self, r: int) -> None:  # accept/reject/close 공통 경유지
        self._wait_workers()
        super().done(r)

    def _on_recs_error(self, w: RecommendWorker, msg: str) -> None:
        if w is not self._worker:
            return                       # 낡은 워커의 결과 -- 무시
        self.status_label.setText(tr("오류: {m}").format(m=msg))
        self.again_btn.setEnabled(True)
        self._worker = None

    def _on_recs_ready(self, w: RecommendWorker, recs: list,
                       counts) -> None:
        if w is not self._worker:
            return                       # 낡은 워커의 결과 -- 무시
        self._worker = None
        self.again_btn.setEnabled(True)
        self.status_label.setText("")
        self._recs = recs
        self._skill_counts = counts
        group = QButtonGroup(self)
        for i, rec in enumerate(self._recs, 1):
            md = rec["md"]
            box = QGroupBox()
            bc = QVBoxLayout(box)
            rb = QRadioButton(tr("추천 {i} — {b} 변형 · 기존과의 최소 거리 {d}")
                              .format(i=i, b=rec["bucket"], d=rec["min_dist"]))
            group.addButton(rb)
            rb.setChecked(i == 1)
            self._radios.append(rb)
            bc.addWidget(rb)
            ax = rec.get("axes", {})
            ax_s = "  ".join(
                f"{a}={ax[a]:.2f}" if ax.get(a) is not None else f"{a}=--"
                for a in AXES)
            why = QLabel(tr("축별 최소 거리: {ax} · 커버리지 보강 축: {wk}")
                         .format(ax=ax_s, wk=rec.get("weak_axis", "?")))
            why.setStyleSheet("color:#888;")
            why.setWordWrap(True)
            bc.addWidget(why)
            view = SceneInfoView()
            view.setText(describe_scene(md))
            bc.addWidget(view)

            ranked = rank_instructions(md, self._props, counts or {})
            checks: list[QCheckBox] = []
            if ranked:
                bc.addWidget(QLabel(
                    tr("추천 문장 — 수집이 적은 스킬 우선 (채택 시 등록됨):")))
                for s, sk, n in ranked:
                    cb = QCheckBox(s)
                    cb.setChecked(True)
                    cb.setEnabled(self._plan_path is not None)
                    cb.setToolTip(tr("스킬 {sk} · 지금까지 {n} 에피소드 수집")
                                  .format(sk=sk, n=n))
                    checks.append(cb)
                    bc.addWidget(cb)
            else:
                note = QLabel(tr("(문법상 생성 가능한 문장이 없음)"))
                note.setStyleSheet("color:#888;")
                bc.addWidget(note)
            self._sentence_checks.append(checks)
            self._cards_col.addWidget(box)
        if counts:
            summary = QLabel(tr("스킬별 누적 수집 (적은 순): {s}")
                             .format(s=format_skill_counts(counts)))
            summary.setStyleSheet("color:#888;")
            summary.setWordWrap(True)
            self._cards_col.addWidget(summary)
        self._cards_col.addStretch(1)

    def _selected_sentences(self, idx: int) -> list[str]:
        return [cb.text() for cb in self._sentence_checks[idx] if cb.isChecked()]

    def _register_plan(self, md: SceneMetadata, sentences: list[str]) -> bool:
        """선택한 문장을 plan_path 의 scene+slots 로 등록. load_plan 검증 통과."""
        if self._plan_path is None or not sentences:
            return False
        path = self._plan_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("계획 읽기 실패"), str(e))
            return False
        raw.setdefault("plan_version", 1)
        if not isinstance(raw.get("scenes"), list):
            raw["scenes"] = []
        by_sid = {s.get("scene_id"): s for s in raw["scenes"]}
        scene = by_sid.get(md.scene_id)
        if scene is None:
            scene = {"scene_id": md.scene_id, "slots": []}
            raw["scenes"].append(scene)
        used = {
            int(m.group(1))
            for sl in scene.get("slots", [])
            if (m := INSTRUCTION_ID_RE.match(str(sl.get("instruction_id", ""))))
        }
        # 같은 문장이 이미 있으면 새 ID 로 또 쌓지 않는다 -- load_plan 은
        # "같은 ID·다른 문장"만 막으므로 여기서 문장 기준으로 걸러야 한다.
        existing_sents = {str(sl.get("instruction", "")).strip()
                          for sl in scene.get("slots", [])}
        new_slots = []
        n_dup = 0
        for sent in sentences:
            if sent.strip() in existing_sents:
                n_dup += 1
                continue
            existing_sents.add(sent.strip())
            n = max(used, default=-1) + 1
            used.add(n)
            new_slots.append({
                "instruction_id": f"I{n:03d}",
                "instruction": sent,
                "target": 10,
            })
        if not new_slots:
            QMessageBox.information(
                self, tr("계획 등록"),
                tr("선택한 문장이 모두 이미 등록되어 있습니다 (중복 {n}건 건너뜀).")
                .format(n=n_dup))
            return False
        scene.setdefault("slots", []).extend(new_slots)

        # 검증 게이트 -- 실패해도 temp 파일은 남기지 않는다.
        tmp: "Path | None" = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as tf:
                tf.write(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
                tmp = Path(tf.name)
            plan = load_plan(tmp)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self, tr("계획 등록 실패"),
                tr("load_plan 검증을 통과하지 못했습니다:\n{e}").format(e=e))
            return False
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        if plan.warnings:
            # 통일 문법 경고(§4)는 등록을 막지 않지만 버리지도 않는다 --
            # PlanEditDialog 저장 경로와 같은 규칙.
            QMessageBox.warning(self, tr("계획 경고"),
                                "\n".join(str(x) for x in plan.warnings))

        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        self._n_dup_skipped = n_dup
        self.registered_plan_path = path
        return True

    def _accept(self) -> None:
        idx = -1
        for i, rb in enumerate(self._radios):
            if rb.isChecked():
                idx = i
                break
        if idx < 0:
            return
        md = self._recs[idx]["md"]
        self.picked = md
        if self._register_check is not None and self._register_check.isChecked():
            sents = self._selected_sentences(idx)
            # 문장 수 × target 이 곧 수집량이다 -- 물체 5개 scene 은 문장이
            # 20개를 넘을 수 있어, 무심코 OK 한 번에 200 에피소드가 계획에
            # 얹히는 것을 총량 확인으로 막는다.
            if sents and QMessageBox.question(
                    self, tr("계획 등록"),
                    tr("{n}개 문장 × target 10 = 총 {t} 에피소드를 {sid} 에 "
                       "등록합니다. 진행할까요?")
                    .format(n=len(sents), t=len(sents) * 10, sid=md.scene_id),
            ) != QMessageBox.StandardButton.Yes:
                sents = []
            if sents and self._register_plan(md, sents):
                dup = getattr(self, "_n_dup_skipped", 0)
                QMessageBox.information(
                    self, tr("계획 등록 완료"),
                    tr("{n}개 문장을 {sid} 에 등록했습니다.{d}")
                    .format(n=len(sents) - dup, sid=md.scene_id,
                            d=tr(" (중복 {k}건 건너뜀)").format(k=dup) if dup else ""))
        super().accept()


class NewSceneDialog(QDialog):
    """새 scene 구성 — 소품 선택 + 3×3 존 배치 + 설명 + 규칙 lint."""

    def __init__(self, parent, scene_id: str,
                 data_root: "Path | None" = None,
                 plan_path: "Path | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("새 Scene 구성 — {sid}").format(sid=scene_id))
        self.setMinimumWidth(720)
        self._scene_id = scene_id
        self._data_root = data_root
        self._plan_path = plan_path
        self._placements: dict = {}
        self.metadata = None  # accept 시 SceneMetadata

        layout = QVBoxLayout(self)
        hrow = QHBoxLayout()
        hint = QLabel(tr(
            "① 포함할 물체를 체크  ② 목록에서 물체를 클릭해 선택  "
            "③ 오른쪽 격자 칸을 눌러 그 존에 배치  ([0,0]=왼쪽 위)"))
        hint.setWordWrap(True)
        hrow.addWidget(hint, 1)
        rec_btn = QPushButton(tr("추천 받기..."))
        rec_btn.setToolTip(tr(
            "기존 scene 들과 가장 다른 소품 조합·배치 3안을 추천받아\n"
            "체크·배치를 자동으로 채웁니다 (#33, 다양성 최대화)."))
        rec_btn.clicked.connect(self._on_recommend)
        hrow.addWidget(rec_btn)
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

    def _on_recommend(self) -> None:
        existing = []
        skipped = 0
        if self._data_root is not None:
            for p in iter_scene_files(self._data_root):
                try:
                    existing.append(read_scene_metadata(p))
                except Exception:  # noqa: BLE001 -- 세션이 쥔 파일 등
                    skipped += 1
        dlg = RecommendDialog(self, existing, props_by_id(), self._scene_id,
                              plan_path=self._plan_path,
                              data_root=self._data_root)
        if skipped:
            dlg.setWindowTitle(dlg.windowTitle()
                               + tr(" (읽지 못한 파일 {n}개 제외)").format(n=skipped))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.picked is not None:
            self._apply_recommendation(dlg.picked)

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

    def _accept(self) -> None:
        md = self._build()
        try:
            from gello.scene.props import active_prop_ids

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
        # 카메라 노드 (2026-08-25 3-프로세스 분리): RealSense 를 독점 소유하는
        # 별도 프로세스. GUI 미리보기·포인트클라우드·수집 worker 는 전부 이
        # 노드의 구독자다 -- GIL 기아·device busy·wedge 를 없앤 구조.
        self.camera_node_process: QProcess | None = None
        self._camera_node_spec = ""
        self._camera_node_crashes: list = []   # 비정상 종료 시각 (loop 방지)
        # 수동 종료 래치 (2026-08-26): VLA 배포 등 다른 프로그램이 카메라를
        # 직접 열어야 할 때 노드를 내려 두는 상태. 래치가 켜져 있으면
        # 새로고침/콤보 변경이 노드를 몰래 되살리지 않는다 -- 재시작 메뉴나
        # 세션 연결 안내를 통해서만 풀린다.
        self._camera_node_user_stopped = False
        self.replay_process: QProcess | None = None
        self._grid_store = load_grid_store()
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
        # Point Cloud 탭 전용 depth 워커 -- 탭이 보일 때만 산다 (안정성).
        self._cloud_worker: DepthCloudWorker | None = None
        self._cloud_pts = None
        self._cloud_rgb = None
        self._cloud_previews_were_on = False
        self._cloud_serial = ""            # 지금 depth 워커가 연 카메라
        self._depth_consumer = None        # "cloud" | "depth" | None
        self._depth_img = None
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
        # 세션 소유 scene 삭제 후 썸네일 무효화 대기 건수. bool 이 아니라 카운터 --
        # saver 는 삭제 1건마다 episode_list_changed 를 emit 하므로, 첫 emit 에서
        # 플래그를 소진하면 나머지 삭제(추가 renumber/uid 재배정)가 무효화를
        # 비껴간다.
        self._pending_scene_deletes = 0
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
        # 저장된 설정의 depth 플래그가 무시됐다면 여기서(로그 뷰가 생긴 뒤)
        # 보이는 로그로 알린다 -- from_json 의 warnings 는 stderr 로만 가서
        # 데스크톱 아이콘 실행에서는 소실된다 (아래 excepthook 주석과 같은 이유).
        for flag in getattr(self.schema, "ignored_depth_flags", []):
            self.log(f"[스키마] 저장된 {flag}=True 를 무시합니다 -- "
                     "카메라 드라이버가 depth 읽기를 지원하지 않습니다")
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

    # ------------------------------------------------------------ gallery
    def _build_gallery_tab(self) -> QWidget:
        """scene 에피소드 갤러리 (#31): 썸네일 그리드 + instruction 필터.

        더블클릭 = Playback 재생(기존 경로 재사용), 재판정 버튼 = Dataset
        페이지와 같은 코어(_relabel_episodes). 썸네일은 uid 기반 캐시라
        (에피소드 immutable) 첫 로드 이후에는 즉시 뜬다.
        """
        w = QWidget()
        col = QVBoxLayout(w)
        row = QHBoxLayout()
        self.gallery_scene_combo = QComboBox()
        _shrinkable_combo(self.gallery_scene_combo)
        self.gallery_scene_combo.currentIndexChanged.connect(self._refresh_gallery)
        row.addWidget(self.gallery_scene_combo, 2)
        self.gallery_filter_combo = QComboBox()
        _shrinkable_combo(self.gallery_filter_combo)
        self.gallery_filter_combo.currentIndexChanged.connect(self._apply_gallery_filter)
        row.addWidget(self.gallery_filter_combo, 2)
        b = QPushButton("↻")
        b.setToolTip(tr("scene 목록·썸네일 새로고침"))
        b.setMaximumWidth(32)
        b.clicked.connect(self._refresh_gallery_scenes)
        row.addWidget(b)
        self.gallery_relabel_btn = QPushButton(tr("선택 재판정"))
        self.gallery_relabel_btn.clicked.connect(self._on_gallery_relabel)
        row.addWidget(self.gallery_relabel_btn)
        self.gallery_replay_btn = QPushButton(tr("실로봇 재생"))
        self.gallery_replay_btn.setToolTip(tr(
            "선택한 에피소드의 관절 명령을 실로봇에 다시 보냅니다.\n"
            "로봇 노드가 켜져 있어야 하고, 로봇이 실제로 움직입니다."))
        self.gallery_replay_btn.clicked.connect(self._on_gallery_replay)
        row.addWidget(self.gallery_replay_btn)
        col.addLayout(row)
        self.gallery_list = QListWidget()
        self.gallery_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery_list.setMovement(QListWidget.Movement.Static)
        self.gallery_list.setIconSize(QSize(200, 150))
        self.gallery_list.setSpacing(8)
        self.gallery_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.gallery_list.itemActivated.connect(self._on_gallery_activated)
        col.addWidget(self.gallery_list, 1)
        self.gallery_status = QLabel(tr("scene 을 선택하세요"))
        self.gallery_status.setStyleSheet("color:#888;")
        self.gallery_status.setWordWrap(True)
        col.addWidget(self.gallery_status)
        self._gallery_loader = None
        self._gallery_episodes = []
        self._refresh_gallery_scenes()
        return w

    def _refresh_gallery_scenes(self) -> None:
        combo = self.gallery_scene_combo
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        try:
            for p in iter_scene_files(self._dataset_root()):
                combo.addItem(p.name, str(p))
        except Exception:  # noqa: BLE001
            pass
        idx = combo.findData(cur)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)
        self._refresh_gallery()

    def _refresh_gallery(self, *_args) -> None:
        path = self.gallery_scene_combo.currentData()
        self.gallery_list.clear()
        self._gallery_episodes = []
        if not path:
            self.gallery_status.setText(tr("표시할 scene 파일이 없습니다"))
            return
        if self.active_file_path is not None and Path(path) == self.active_file_path:
            # HDF5 잠금 -- 실패한 로드 대신 이유와 다음 행동을 말한다
            self.gallery_status.setText(tr(
                "수집 세션이 이 scene 파일을 사용 중입니다 — 세션을 종료하면 "
                "갤러리가 열립니다. (현황은 Collect 페이지 slot 패널에)"))
            return
        self.gallery_status.setText(tr("불러오는 중... (첫 로드는 썸네일 생성으로 수 초)"))
        if self._gallery_loader is not None:
            self._gallery_loader.wait()
        self._gallery_loader = GalleryLoadWorker(path)
        self._gallery_loader.loaded.connect(self._on_gallery_loaded)
        self._gallery_loader.failed.connect(
            lambda m: self.gallery_status.setText(tr("갤러리 로드 실패: {m}").format(m=m)))
        self._gallery_loader.start()

    @pyqtSlot(str, list, object)
    def _on_gallery_loaded(self, path, episodes, ref_thumb) -> None:
        if path != self.gallery_scene_combo.currentData():
            return  # 로드 중 scene 을 바꿨다
        self._gallery_episodes = episodes
        # instruction 필터 항목 재구성 (선택 유지)
        cur = self.gallery_filter_combo.currentData()
        self.gallery_filter_combo.blockSignals(True)
        self.gallery_filter_combo.clear()
        self.gallery_filter_combo.addItem(tr("(모든 instruction)"), None)
        for iid, instr in sorted({(e["instruction_id"], e["instruction"])
                                  for e in episodes}):
            self.gallery_filter_combo.addItem(f"{iid} · {instr[:44]}", iid)
        idx = self.gallery_filter_combo.findData(cur)
        self.gallery_filter_combo.setCurrentIndex(max(0, idx))
        self.gallery_filter_combo.blockSignals(False)
        self._ref_thumb = ref_thumb
        self._apply_gallery_filter()

    def _apply_gallery_filter(self, *_args) -> None:
        want = self.gallery_filter_combo.currentData()
        path = self.gallery_scene_combo.currentData()
        self.gallery_list.clear()
        if getattr(self, "_ref_thumb", None):
            it = QListWidgetItem(QIcon(self._ref_thumb), tr("기준 사진"))
            it.setData(Qt.ItemDataRole.UserRole, None)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.gallery_list.addItem(it)
        shown = 0
        for e in self._gallery_episodes:
            if want is not None and e["instruction_id"] != want:
                continue
            mark = {"success": "✓", "failed": "✗"}.get(e["quality_status"],
                                                       e["quality_status"][:4])
            it = QListWidgetItem(
                QIcon(e["thumb"]) if e["thumb"] else QIcon(),
                # E번호는 slot 로컬 (uid 의 마지막 조각) -- I000-E000, I003-E000 …
                f"{e['instruction_id']}-{e['episode_uid'].rsplit('-', 1)[-1]} {mark}")
            it.setData(Qt.ItemDataRole.UserRole, (path, e["name"]))
            it.setToolTip(f"{e['episode_uid']}\n{e['instruction']}\n"
                          f"{e['num_samples']}프레임 · {e['quality_status']}"
                          f" · {e.get('collector', '')}")
            self.gallery_list.addItem(it)
            shown += 1
        n_ok = sum(1 for e in self._gallery_episodes
                   if e["quality_status"] == "success")
        self.gallery_status.setText(
            tr("{s}개 표시 (전체 {n}개 · success {ok}개) — 더블클릭: 재생, "
               "선택 후 재판정 버튼: 성공↔실패").format(
                   s=shown, n=len(self._gallery_episodes), ok=n_ok))

    def _on_gallery_activated(self, item) -> None:
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self._play_episode(d[0], d[1])

    def _on_gallery_replay(self) -> None:
        if self._replay_running():      # 토글: 재생 중이면 중단 버튼이다
            self._on_replay_stop()
            return
        picks = [item.data(Qt.ItemDataRole.UserRole)
                 for item in self.gallery_list.selectedItems()]
        picks = [d for d in picks if d]
        if len(picks) != 1:
            QMessageBox.information(
                self, tr("선택 필요"),
                tr("실로봇 재생은 에피소드 하나만 선택하세요."))
            return
        self._replay_on_robot(picks[0][0], picks[0][1])

    def _on_gallery_relabel(self) -> None:
        by_file: dict = {}
        for item in self.gallery_list.selectedItems():
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                by_file.setdefault(Path(d[0]), []).append(d[1])
        if not by_file:
            QMessageBox.information(self, tr("선택 필요"),
                                    tr("재판정할 에피소드를 선택하세요."))
            return
        if self._relabel_episodes(by_file):
            self._refresh_gallery()
            self._refresh_dataset_tree()

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
        self.live_boxes = {}
        self._live_maximized: "str | None" = None
        for key, title in (("agent", "Agent (정면)"), ("wrist", "Wrist (손목)")):
            box = QGroupBox(tr(title))
            inner = QVBoxLayout(box)
            inner.setContentsMargins(4, 4, 4, 4)
            view = VideoView()
            view.setText(tr("카메라를 선택하세요"))
            view.set_crop_guide(**self._crop_params[key])
            view.setToolTip(tr("더블클릭: 이 카메라 최대화 / 복원"))
            view.setMinimumSize(60, 45)   # 최대화 시 반대쪽이 아주 작아질 수 있게
            view.installEventFilter(self)
            inner.addWidget(view)
            self.live_views[key] = view
            self.live_boxes[key] = box
            self.live_split.addWidget(box)
        self.live_split.setSizes([600, 600])
        live_col.addWidget(self.live_split, 1)
        self.square_guide_check = QCheckBox(tr("정사각 크롭 가이드"))
        self.square_guide_check.setChecked(True)
        self.square_guide_check.setToolTip(tr(
            "LeRobot 변환은 가운데 정사각만 남깁니다. 켜면 그 바깥이 어둡게 표시됩니다."))
        self.square_guide_check.toggled.connect(self._on_square_guide)
        grow = QHBoxLayout()
        grow.addWidget(QLabel(tr("보기")))
        # 한 카메라를 전체로 키우고 반대쪽을 왼쪽 아래 PiP 로 겹친다 --
        # 뷰 더블클릭으로도 토글된다.
        self.live_view_combo = QComboBox()
        self.live_view_combo.addItem(tr("나란히"), None)
        self.live_view_combo.addItem(tr("Agent 최대"), "agent")
        self.live_view_combo.addItem(tr("Wrist 최대"), "wrist")
        self.live_view_combo.currentIndexChanged.connect(
            lambda *_: self._set_live_maximized(self.live_view_combo.currentData()))
        grow.addWidget(self.live_view_combo)
        grow.addSpacing(16)
        grow.addWidget(self.square_guide_check)
        grow.addSpacing(16)
        # 3×3 워크스페이스 격자 -- 편집은 격자 편집 다이얼로그, 여기는 표시만.
        self.grid_live_check = QCheckBox(tr("3×3 격자"))
        self.grid_live_check.setChecked(bool(self._grid_store.get("live_on")))
        self.grid_live_check.setToolTip(tr(
            "저장된 워크스페이스 격자를 agent 라이브 화면에 겹쳐 보입니다.\n"
            "물체를 어느 칸(A1..C3)에 놓을지 확인하는 용도입니다."))
        self.grid_live_check.toggled.connect(self._on_grid_live_toggled)
        grow.addWidget(self.grid_live_check)
        self.grid_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.grid_alpha_slider.setRange(10, 100)
        self.grid_alpha_slider.setValue(int(self._grid_store.get("alpha", 60)))
        self.grid_alpha_slider.setMaximumWidth(140)
        self.grid_alpha_slider.valueChanged.connect(self._on_grid_alpha)
        self.grid_alpha_slider.sliderReleased.connect(self._on_grid_alpha_done)
        grow.addWidget(self.grid_alpha_slider)
        self.grid_alpha_label = QLabel(
            tr("{v}%").format(v=self.grid_alpha_slider.value()))
        self.grid_alpha_label.setStyleSheet("color:#888;")
        grow.addWidget(self.grid_alpha_label)
        grid_edit_btn = QPushButton(tr("격자 편집..."))
        grid_edit_btn.clicked.connect(self._on_edit_grid)
        grow.addWidget(grid_edit_btn)
        grow.addStretch(1)
        live.layout().addLayout(grow)
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
        self._gallery_tab_index = self.center_tabs.addTab(
            self._build_gallery_tab(), tr("Gallery"))
        self._cloud_tab_index = self.center_tabs.addTab(
            self._build_cloud_tab(), tr("Point Cloud"))
        self._depth_tab_index = self.center_tabs.addTab(
            self._build_depth_tab(), tr("Depth"))
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)

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
            _relax_min_widths(page)
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
        _shrinkable_combo(self.scene_combo)
        self.scene_combo.currentIndexChanged.connect(self._on_scene_selected)
        srow.addWidget(self.scene_combo, 1)
        self.scene_refresh_btn = QPushButton("↻")
        self.scene_refresh_btn.setToolTip(tr("scene 목록 새로고침"))
        self.scene_refresh_btn.setMaximumWidth(32)
        self.scene_refresh_btn.clicked.connect(self._refresh_scene_combo)
        srow.addWidget(self.scene_refresh_btn)
        sc_form.addRow(tr("Scene"), scene_row)
        self.scene_new_btn = QPushButton(tr("새 Scene 구성..."))
        self.scene_new_btn.clicked.connect(self._on_new_scene)
        sc_form.addRow(self.scene_new_btn)
        # 계획이 있으면 시작 문장을 여기서 고른다 -- 고르면 아래 문장·slot ID
        # 가 함께 채워진다 (세션 중 slot 패널의 계획 콤보와 같은 장치).
        self.start_plan_combo = QComboBox()
        _shrinkable_combo(self.start_plan_combo)
        self.start_plan_combo.currentIndexChanged.connect(self._on_start_plan_pick)
        sc_form.addRow(tr("계획 문장"), self.start_plan_combo)
        self.lang_edit = QLineEdit()
        self.lang_edit.setPlaceholderText(tr("예) pick up the blue cup and place it on the blue bowl"))
        self.lang_edit.setText(self._recents.most_recent("language", ""))
        # 문장을 바꾸면 slot ID 가 자동으로 따라온다 (아는 문장=재사용,
        # 새 문장=다음 빈 ID) -- ID-문장 갈라짐 방지.
        self.lang_edit.editingFinished.connect(self._on_start_sentence_edited)
        sc_form.addRow(tr("시작 문장"), self.lang_edit)
        # 수집 계획 (slot plan). 계획이 있으면 Collect 의 slot 패널이 계획
        # 기반 드롭다운 + 수집 카운트로 동작한다. 없어도 자유 입력은 그대로.
        self.plan_combo = QComboBox()
        _shrinkable_combo(self.plan_combo)
        self.plan_combo.addItem(tr("(계획 없음 — 자유 입력)"), None)
        for p in list_plans():
            self.plan_combo.addItem(p.name, str(p))
        last_plan = self._recents.most_recent("plan_file", "pilot.json")
        idx = self.plan_combo.findText(last_plan)
        if idx > 0:
            self.plan_combo.setCurrentIndex(idx)
        self.plan_combo.currentIndexChanged.connect(self._on_plan_selected)
        plan_row = QWidget()
        prow = QHBoxLayout(plan_row)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.plan_combo, 1)
        self.plan_edit_btn = QPushButton("✎")
        self.plan_edit_btn.setToolTip(tr("선택한 계획 파일 편집 (저장 시 규칙 검증)"))
        self.plan_edit_btn.setMaximumWidth(32)
        self.plan_edit_btn.clicked.connect(self._on_edit_plan)
        prow.addWidget(self.plan_edit_btn)
        plan_new_btn = QPushButton("+")
        plan_new_btn.setToolTip(tr("새 계획 파일 만들기 (이름을 정하면 빈 계획이 "
                                   "생기고 바로 편집이 열립니다)"))
        plan_new_btn.setMaximumWidth(32)
        plan_new_btn.clicked.connect(self._on_new_plan)
        prow.addWidget(plan_new_btn)
        plan_del_btn = QPushButton("🗑")
        plan_del_btn.setToolTip(tr("선택한 계획 파일 삭제 (git 이력에는 남습니다)"))
        plan_del_btn.setMaximumWidth(32)
        plan_del_btn.clicked.connect(self._on_delete_plan)
        prow.addWidget(plan_del_btn)
        sc_form.addRow(tr("수집 계획"), plan_row)
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
            _shrinkable_combo(c)
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
        self.resetwait_edit.setEnabled(False)
        self.resetwait_edit.setToolTip(tr(
            "더 이상 사용하지 않습니다 — 리셋 대기는 시간으로 끝나지 않고 "
            "'리셋 완료' 버튼(Enter)으로만 끝납니다."))
        sform.addRow(tr("리셋 대기(s) (미사용)"), self.resetwait_edit)
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
        self._refresh_start_plan_combo()
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
            if self.active_file_path is not None and path == self.active_file_path:
                # 세션이 파일을 쥐고 있다 -- 캐시 요약으로 대신한다
                counts = self._session_slot_counts()
                lines = [tr("{s} — 수집 세션 진행 중 (배치도는 오른쪽 패널에)")
                         .format(s=sid)]
                if counts:
                    lines.append("slot: " + "  ".join(
                        f"{iid} {c.get('usable', 0)}/{c.get('total', 0)}"
                        for iid, c in sorted(counts.items())))
                self.scene_info.setText("\n".join(lines))
                return
            md = read_scene_metadata(path)
            counts = count_by_slot(path)
            lines = [describe_scene(md)]
            if counts:
                lines.append("slot: " + "  ".join(
                    f"{iid} {c['usable']}/{c['total']}" for iid, c in sorted(counts.items())))
            plan = self._current_plan()
            if plan is not None and plan.slots_for(sid):
                lines.append(f"계획({plan.path.name}): " + "  ".join(
                    f"{s.instruction_id} {counts.get(s.instruction_id, {}).get('usable', 0)}"
                    f"/{s.target}" for s in plan.slots_for(sid)))
            self.scene_info.setText("\n".join(lines))
        except BlockingIOError:
            self.scene_info.setText(tr(
                "(다른 프로세스가 파일을 사용 중입니다 — 재압축/변환이 끝난 "
                "뒤 새로고침하세요)"))
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
        plan_data = self.plan_combo.currentData() if hasattr(self, "plan_combo") else None
        plan_path = Path(plan_data) if plan_data else None
        dlg = NewSceneDialog(self, sid, data_root=root, plan_path=plan_path)
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
        # 계획이 선택돼 있으면 시작 slot 은 계획의 (ID, 문장) 쌍이어야 한다 --
        # 자유 입력이 계획 밖 slot 을 만들던 구멍의 마지막 잠금. 새 문장은
        # ✎ 편집으로 계획에 추가하고, 자유 수집은 '(계획 없음)' 을 고른다.
        plan = self._current_plan()
        if plan is not None:
            psid = self._configure_scene_id()
            slots = plan.slots_for(psid) if psid else ()
            if not slots:
                return None, None, False, tr(
                    "계획({p})에 scene {s} 가 없습니다. ✎ 편집으로 scene 을 "
                    "추가하거나, 자유 수집이면 수집 계획을 '(계획 없음)' 으로 "
                    "바꾸세요.").format(p=plan.path.name, s=psid)
            if not any(s.instruction_id == iid and s.instruction == lang
                       for s in slots):
                return None, None, False, tr(
                    "시작 문장은 '계획 문장' 드롭다운에서 선택하세요. "
                    "({i}: {t!r} 는 계획에 없습니다 — 새 문장은 ✎ 편집으로 "
                    "계획에 먼저 추가)").format(i=iid, t=lang[:40])
        sid = self.scene_combo.currentData()
        if sid is None:
            if self._pending_scene_meta is None:
                return None, None, False, tr(
                    "'새 Scene 구성...'으로 배치를 먼저 정의하거나 기존 scene 을 고르세요.")
            return self._pending_scene_meta, None, False, None
        return None, sid, True, None

    # -------------------------------------------------- slot ID 자동 배정
    def _known_slots(self, scene_id=None, scene_path=None, episodes=None) -> dict:
        """**이 scene 의** instruction_id -> 문장 매핑.

        ID 는 scene 마다 독립이다(각 scene 의 첫 instruction 이 I000, 새
        문장마다 +1 -- 2026-08-13 결정). 그래서 참조 범위도 scene 하나:
        계획에서 그 scene 의 slot + 그 scene 파일에 기록된 에피소드.
        계획이 먼저다 -- 파일 쪽에 갈라짐 사고가 있어도 계획이 정본.

        세션 중에는 episodes(GUI 가 saver 에게서 받은 캐시)를 넘겨야 한다 --
        HDF5 파일 잠금 때문에 열려 있는 파일을 다시 읽을 수 없다.
        """
        m: dict = {}
        plan = self._current_plan()
        if plan is not None and scene_id is not None:
            for s in plan.slots_for(scene_id):
                m.setdefault(s.instruction_id, s.instruction)
        for ep in (episodes or []):
            if ep.get("instruction_id"):
                m.setdefault(ep["instruction_id"], ep.get("instruction", ""))
        if episodes is None and scene_path is not None and Path(scene_path).exists():
            try:
                from gello.scene.scene_format import list_scene_episodes

                for ep in list_scene_episodes(scene_path):
                    m.setdefault(ep["instruction_id"], ep["instruction"])
            except Exception:  # noqa: BLE001 - 다른 프로세스가 잠갔을 수 있다
                pass
        return m

    def _session_scene_id(self):
        if self.worker is None:
            return None
        cfg = self.worker.cfg
        if getattr(cfg, "scene_metadata", None) is not None:
            return cfg.scene_metadata.scene_id
        return getattr(cfg, "scene_id", None)

    def _session_slot_counts(self) -> dict:
        """세션 중 slot 카운트 -- 파일은 saver 가 h5py 로 잠그고 있으므로
        다시 열지 않고, saver 가 보내준 에피소드 목록으로 계산한다
        (count_by_slot 과 같은 정의: usable = quality_status success)."""
        counts: dict = {}
        for e in (self.active_episode_cache or []):
            iid = e.get("instruction_id")
            if not iid:
                continue
            c = counts.setdefault(iid, {"total": 0, "usable": 0})
            c["total"] += 1
            if e.get("quality_status") == "success":
                c["usable"] += 1
        return counts

    @staticmethod
    def _next_iid(known: dict) -> str:
        used = [int(i[1:]) for i in known if INSTRUCTION_ID_RE.match(i)]
        return f"I{(max(used) + 1) if used else 0:03d}"

    def _auto_assign_iid(self, instr: str, iid_edit, scene_id=None,
                         scene_path=None, episodes=None) -> None:
        """문장이 바뀌면 slot ID 를 자동으로 맞춘다 (**scene 안에서**).

        모든 scene 은 첫 instruction 이 I000 이고 새 문장마다 하나씩
        올라간다. 이 scene 에서 아는 문장 -> 그 ID 재사용, 처음 보는 문장
        -> 이 scene 의 다음 빈 ID. 다른 scene 의 ID 는 참조하지 않는다.
        자동 배정 후에도 손으로 고칠 수 있다.
        """
        instr = instr.strip()
        if not instr:
            return
        known = self._known_slots(scene_id, scene_path, episodes=episodes)
        for iid, s in known.items():
            if s == instr:
                if iid_edit.text().strip() != iid:
                    iid_edit.setText(iid)
                    self.log(f"[SLOT] 아는 문장 -- {iid} 재사용")
                return
        cur = iid_edit.text().strip()
        nxt = self._next_iid(known)
        if cur in known and known[cur] != instr:
            iid_edit.setText(nxt)
            self.log(f"[SLOT] 새 문장 -- {nxt} 자동 배정 ({cur} 는 이 scene 에서 사용 중)")
        elif not INSTRUCTION_ID_RE.match(cur) or cur not in known and cur != nxt:
            # 빈/이상한 값이거나, 이 scene 기준으로 뜬금없는 번호(예: 다른
            # scene 에서 넘어온 I003)면 이 scene 의 다음 번호로 정렬한다.
            iid_edit.setText(nxt)
            if cur and cur != nxt:
                self.log(f"[SLOT] 새 문장 -- {nxt} 자동 배정")

    def _configure_scene_id(self):
        """Configure 가 가리키는 scene ID -- 기존 선택이면 그것, 새 scene 이면
        구성해 둔 metadata 의 ID, 그것도 없으면 다음 발번 예정 ID."""
        sid = self.scene_combo.currentData()
        if sid is not None:
            return sid
        if self._pending_scene_meta is not None:
            return self._pending_scene_meta.scene_id
        try:
            return next_scene_id(Path(self.root_edit.text().strip() or "."))
        except Exception:  # noqa: BLE001
            return None

    def _selected_scene_path(self):
        """Configure 의 Scene 콤보가 가리키는 기존 scene 파일 (새 scene 이면 None)."""
        sid = self.scene_combo.currentData()
        if sid is None:
            return None
        return Path(self.root_edit.text().strip() or ".") / scene_filename(sid)

    def _on_start_sentence_edited(self) -> None:
        self._auto_assign_iid(self.lang_edit.text(), self.scene_iid_edit,
                              scene_id=self._configure_scene_id(),
                              scene_path=self._selected_scene_path())

    def _on_start_plan_pick(self, *_args) -> None:
        d = self.start_plan_combo.currentData()
        if d:
            self.scene_iid_edit.setText(d[0])
            self.lang_edit.setText(d[1])

    def _refresh_start_plan_combo(self) -> None:
        """Configure 의 계획 문장 드롭다운 = 계획 × 선택 scene.

        카운트는 scene 파일에서 온다 (계획 파일에는 카운트가 없다 -- 두 개의
        진실 금지). 세션이 파일을 쥐고 있으면 카운트만 생략된다.
        """
        if not hasattr(self, "start_plan_combo"):
            return
        combo = self.start_plan_combo
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        plan = self._current_plan()
        # 계획이 있으면 문장은 계획에서만 고른다 -- 자유 입력이 계획 밖
        # slot(문장-ID 갈라짐)을 실데이터에 만들었다. 새 문장은 ✎ 편집으로
        # 계획에 먼저 추가한다. 계획이 없을 때만 직접 입력을 연다.
        combo.addItem(tr("(계획에서 선택)") if plan is not None
                      else tr("(직접 입력)"), None)
        self.lang_edit.setReadOnly(plan is not None)
        self.scene_iid_edit.setReadOnly(plan is not None)
        for w in (self.lang_edit, self.scene_iid_edit):
            w.setStyleSheet("color:#888;" if plan is not None else "")
        sid = self._configure_scene_id()
        if plan is not None and sid is not None:
            counts: dict = {}
            if self._scene_session and sid == self._session_scene_id():
                # 세션이 파일을 쥐고 있다 -- saver 가 보내준 캐시로 센다
                counts = self._session_slot_counts()
            else:
                p = self._selected_scene_path()
                if p is not None and p.exists():
                    try:
                        counts = count_by_slot(p)
                    except Exception:  # noqa: BLE001 -- HDF5 잠금 등
                        counts = {}
            for s in plan.slots_for(sid):
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                combo.addItem(
                    f"{s.instruction_id} · {c}/{s.target} · {s.instruction}",
                    (s.instruction_id, s.instruction))
            if keep:
                for i in range(combo.count()):
                    if combo.itemData(i) == keep:
                        combo.setCurrentIndex(i)
                        break
        combo.blockSignals(False)

    def _on_slot_sentence_edited(self) -> None:
        # 세션 중에는 파일이 잠겨 있으므로 캐시로 (파일 인자 없이)
        self._auto_assign_iid(self.slot_instr_edit.text(), self.slot_iid_edit,
                              scene_id=self._session_scene_id(),
                              episodes=self.active_episode_cache)

    # -------------------------------------------------- 수집 계획 (slot plan)
    def _current_plan(self):
        """선택된 계획 파일. 작아서 캐시 없이 매번 읽는다 -- 파일을 고치고
        새로고침할 때 낡은 캐시가 남는 쪽이 더 나쁘다."""
        data = getattr(self, "plan_combo", None) and self.plan_combo.currentData()
        if not data:
            return None
        try:
            return load_plan(Path(data))
        except Exception as e:  # noqa: BLE001
            self.log(f"[계획] {Path(data).name} 로드 실패: {type(e).__name__}: {e}")
            return None

    def _refresh_plan_combo(self, select: "str | None" = None) -> None:
        """계획 파일 목록을 다시 읽는다. select 로 파일명을 주면 그걸 고른다."""
        keep = select or self.plan_combo.currentText()
        self.plan_combo.blockSignals(True)
        self.plan_combo.clear()
        self.plan_combo.addItem(tr("(계획 없음 — 자유 입력)"), None)
        for p in list_plans():
            self.plan_combo.addItem(p.name, str(p))
        idx = self.plan_combo.findText(keep)
        self.plan_combo.setCurrentIndex(max(0, idx))
        self.plan_combo.blockSignals(False)
        self._on_plan_selected()

    def _on_new_plan(self) -> None:
        name, ok = QInputDialog.getText(
            self, tr("새 수집 계획"),
            tr("계획 이름 (영문/숫자/-/_, 확장자 없이):"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            QMessageBox.warning(self, tr("이름 오류"),
                                tr("영문·숫자·-·_ 만 쓸 수 있습니다."))
            return
        path = PLANS_DIR / f"{name}.json"
        if path.exists():
            QMessageBox.warning(self, tr("이미 있음"),
                                tr("{n} 이 이미 있습니다. 드롭다운에서 "
                                   "선택하세요.").format(n=path.name))
            return
        PLANS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"plan_version": 1, "scenes": []},
                                   ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        self.log(f"[계획] 새 계획 생성: {path.name}")
        self._refresh_plan_combo(select=path.name)
        self._on_edit_plan()    # 빈 계획은 쓸모없으니 바로 편집으로

    def _on_delete_plan(self) -> None:
        data = self.plan_combo.currentData()
        if not data:
            QMessageBox.information(self, tr("계획 없음"),
                                    tr("삭제할 계획 파일을 먼저 선택하세요."))
            return
        p = Path(data)
        ans = QMessageBox.question(
            self, tr("계획 삭제"),
            tr("{n} 을(를) 삭제할까요?\n수집 파일에는 영향이 없고, git 이력"
               "에서 되살릴 수 있습니다.").format(n=p.name))
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            p.unlink()
        except OSError as e:
            QMessageBox.warning(self, tr("삭제 실패"), str(e))
            return
        self.log(f"[계획] 삭제: {p.name}")
        self._refresh_plan_combo(select="")

    def _on_edit_plan(self) -> None:
        data = self.plan_combo.currentData()
        if not data:
            QMessageBox.information(self, tr("계획 없음"),
                                    tr("편집할 계획 파일을 먼저 선택하세요."))
            return
        dlg = PlanEditDialog(self, Path(data))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for w in getattr(dlg, "warnings", []):
                self.log(f"[계획 경고] {w}")
            self.log(f"[계획] {Path(data).name} 저장됨")
            # 갱신된 목표/slot 이 화면에 반영되게
            self._on_plan_selected()

    def _on_plan_selected(self, *_args) -> None:
        plan = self._current_plan()
        if plan is not None:
            self._recents.add("plan_file", self.plan_combo.currentText())
            for w in plan.warnings:
                self.log(f"[계획 경고] {w}")
        self._refresh_slot_panel()
        self._on_scene_selected()

    def _scene_session_file(self):
        if not self._scene_session or self.active_file_path is None:
            return None
        return self.active_file_path

    def _refresh_slot_panel(self) -> None:
        """계획 slot 드롭다운 + 수집 카운트 + 계획-파일 불일치 경고 갱신.

        카운트는 계획 파일이 아니라 scene 파일에서 계산한다(두 개의 진실
        금지). 세션 중 에피소드가 저장될 때마다 다시 계산된다.
        """
        if not hasattr(self, "slot_plan_combo"):
            return
        combo = self.slot_plan_combo
        combo.blockSignals(True)
        combo.clear()
        plan = self._current_plan()
        # Configure 쪽과 같은 규칙: 계획이 있으면 드롭다운에서만 고른다.
        combo.addItem(tr("(계획에서 선택)") if plan is not None
                      else tr("(직접 입력)"), None)
        if hasattr(self, "slot_iid_edit"):
            self.slot_iid_edit.setReadOnly(plan is not None)
            self.slot_instr_edit.setReadOnly(plan is not None)
            for w in (self.slot_iid_edit, self.slot_instr_edit):
                w.setStyleSheet("color:#888;" if plan is not None else "")
        # 세션 중이므로 파일을 다시 열지 않는다(HDF5 잠금) -- scene ID 는
        # 워커 설정에서, 에피소드·카운트는 saver 가 보내준 캐시에서.
        sid = self._session_scene_id() if self._scene_session else None
        counts = self._session_slot_counts()
        episodes = list(self.active_episode_cache or [])
        warn: list = []
        if plan is not None and sid is not None:
            slots = plan.slots_for(sid)
            for s in slots:
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                combo.addItem(
                    f"{s.instruction_id} · {c}/{s.target} · {s.instruction}",
                    (s.instruction_id, s.instruction))
            if not slots:
                warn.append(tr("계획에 scene {s} 가 없습니다").format(s=sid))
            warn.extend(check_scene_against_plan(plan, sid, episodes))
        combo.blockSignals(False)
        self.slot_plan_warn.setText("\n".join(warn[:4]))

    def _on_slot_plan_pick(self, *_args) -> None:
        d = self.slot_plan_combo.currentData()
        if d:
            self.slot_iid_edit.setText(d[0])
            self.slot_instr_edit.setText(d[1])

    def _on_next_slot(self) -> None:
        """계획에서 목표(target)를 못 채운 첫 slot 을 골라 채워준다 (§6:
        채우지 못한 채 책상을 치우는 것이 재수집의 시작이다)."""
        plan = self._current_plan()
        sid = self._session_scene_id() if self._scene_session else None
        if plan is None or sid is None:
            self.log("[SLOT] 계획이 없거나 scene 세션이 아닙니다")
            return
        counts = self._session_slot_counts()
        for s in plan.slots_for(sid):
            c = counts.get(s.instruction_id, {}).get("usable", 0)
            if c < s.target:
                for i in range(self.slot_plan_combo.count()):
                    if self.slot_plan_combo.itemData(i) == (s.instruction_id, s.instruction):
                        self.slot_plan_combo.setCurrentIndex(i)
                        break
                self.slot_iid_edit.setText(s.instruction_id)
                self.slot_instr_edit.setText(s.instruction)
                self.log(f"[SLOT] 다음 미수집: {s.instruction_id} ({c}/{s.target}) {s.instruction}")
                return
        self.log("[SLOT] 이 scene 의 모든 slot 이 목표를 채웠습니다")

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
        # 계획이 있으면 계획의 (ID, 문장) 쌍만 적용 가능 -- 자유 입력이
        # 계획 밖 slot 을 만들던 구멍을 세션 중에도 막는다.
        plan = self._current_plan()
        sid = self._session_scene_id()
        if plan is not None and sid is not None:
            slots = plan.slots_for(sid)
            if slots and not any(s.instruction_id == iid
                                 and s.instruction == instr for s in slots):
                QMessageBox.warning(self, tr("slot 오류"), tr(
                    "계획에 없는 slot 입니다 ({i}). '계획 slot' 드롭다운에서 "
                    "고르세요 — 새 문장은 계획을 먼저 수정하세요.").format(i=iid))
                return
        self.worker.cmd_set_slot(instr, iid)
        self.slot_current_label.setText(f"{iid}: {instr}")
        # cmd_set_slot 은 워커 큐로 가서 다음 드레인에 반영된다 -- 오른쪽
        # 패널은 사용자가 누른 값으로 즉시 갱신한다 (워커 속성은 곧 같아진다).
        self.right_fields["ds_task"].setText(f"{iid}: {instr}")
        self.right_fields["ds_task"].setToolTip(f"{iid}: {instr}")
        self._recents.add("instruction_id", iid)
        self._recents.add("language", instr)
        # 계획과 어긋난 수동 입력은 막지 않되 즉시 보이게 한다 (ID-문장
        # 갈라짐이 실데이터에서 실제로 발생했다).
        plan = self._current_plan()
        sid = self._session_scene_id()
        if plan is not None and sid is not None:
            sentences = {s.instruction_id: s.instruction
                         for s in plan.slots_for(sid)}
            if iid in sentences and sentences[iid] != instr:
                self.log(f"[SLOT 경고] {iid} 문장이 계획({sid})과 다릅니다 -- "
                         f"계획: {sentences[iid]!r}")
            elif sentences and iid not in sentences:
                self.log(f"[SLOT 경고] 계획({sid})에 없는 slot {iid} 로 수집합니다")

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
        # 계획(수집 계획 파일)이 있으면 여기서 slot 을 고른다 -- 항목에 수집
        # 현황("2/10")이 붙고, 고르면 아래 ID·문장이 채워진다. 문장을 손으로
        # 칠 때 생기는 미묘한 갈라짐(실데이터에서 실제 발생)을 막는 장치.
        self.slot_plan_combo = QComboBox()
        _shrinkable_combo(self.slot_plan_combo)
        self.slot_plan_combo.currentIndexChanged.connect(self._on_slot_plan_pick)
        sfrm.addRow(tr("계획 slot"), self.slot_plan_combo)
        self.slot_next_btn = QPushButton(tr("다음 미수집 slot 제시"))
        self.slot_next_btn.clicked.connect(self._on_next_slot)
        sfrm.addRow(self.slot_next_btn)
        self.slot_iid_edit = QLineEdit()
        sfrm.addRow(tr("instruction ID"), self.slot_iid_edit)
        self.slot_instr_edit = QLineEdit()
        self.slot_instr_edit.editingFinished.connect(self._on_slot_sentence_edited)
        sfrm.addRow(tr("문장"), self.slot_instr_edit)
        self.slot_apply_btn = QPushButton(tr("slot 적용 (다음 에피소드부터)"))
        self.slot_apply_btn.clicked.connect(self._on_apply_slot)
        sfrm.addRow(self.slot_apply_btn)
        self.slot_plan_warn = QLabel("")
        self.slot_plan_warn.setWordWrap(True)
        self.slot_plan_warn.setStyleSheet("color:#e67e22;")
        sfrm.addRow(self.slot_plan_warn)
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
        self.skip_btn = QPushButton(tr("리셋 완료 — 계속 (Enter)"))
        self.skip_btn.setToolTip(tr(
            "물체를 제자리에 놓은 뒤 누르세요. 리셋 대기는 자동으로 끝나지 "
            "않습니다 -- 이 버튼(또는 Enter)을 눌러야 게이트로 넘어갑니다."))
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
        # 이 페이지 전용 폴더 선택 -- 수집 저장 경로(root_edit)와 독립적으로
        # 다른 폴더(예: old_data/)를 훑어볼 수 있다. 초기값은 수집 경로.
        dr = QHBoxLayout()
        self.dataset_root_edit = QLineEdit(
            self.root_edit.text() if hasattr(self, "root_edit")
            else str(Path.home() / "libero_datasets"))
        self.dataset_root_edit.editingFinished.connect(self._refresh_dataset_tree)
        dr.addWidget(self.dataset_root_edit, 1)
        dbrowse = QPushButton(tr("..."))
        dbrowse.setMaximumWidth(36)
        dbrowse.clicked.connect(self._browse_dataset_root)
        dr.addWidget(dbrowse)
        col.addLayout(dr)
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
                     (("HDF5 트리 뷰어", self._on_hdf5_tree,
                       "선택한 파일의 전체 내부 구조(그룹/데이터셋/attrs)를\n"
                       "트리로 탐색합니다. 데이터셋을 클릭하면 shape·dtype·압축과\n"
                       "이미지 미리보기/값 미리보기가 나옵니다 (myHDF5 스타일)."),
                      ("myHDF5 (웹)", self._on_myhdf5,
                       "브라우저에서 myhdf5.hdfgroup.org 를 엽니다.\n"
                       "파일을 창에 끌어다 놓으면 같은 구조를 웹에서 봅니다.")),
                     (("실패만 선택", self._on_select_failed,
                       "success=False 로 표시된 에피소드를 모두 선택합니다.\n"
                       "선택만 하고 지우지 않습니다."),
                      ("튀는 것만 선택", self._on_select_jerky,
                       "같은 (scene·문장) 그룹 평균과 ±{d} 넘게 차이 나는 에피소드를 모두 선택합니다.\n"
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

        relabel_btn = QPushButton(tr("선택 재판정 (성공↔실패)"))
        relabel_btn.setToolTip(tr(
            "scene 에피소드 전용. 선택한 에피소드의 quality_status 를 성공↔실패로 "
            "뒤집습니다.\nscene 체계에서 삭제를 대신하는 큐레이션 수단입니다 -- "
            "변환은 success 만 내보냅니다.\nbad_data 등 다른 상태는 건드리지 않습니다."))
        relabel_btn.clicked.connect(self._on_relabel_selected)
        col.addWidget(relabel_btn)

        # 재생 중에는 이 버튼 자체가 '■ 재생 중단' 으로 바뀐다 -- 별도 중단
        # 버튼은 화면 밖으로 밀려 안 보이는 일이 있었다.
        self.replay_btn = QPushButton(tr("선택 재생 (실로봇)"))
        self.replay_btn.setToolTip(tr(
            "기록된 관절 명령을 같은 주기로 다시 보내 에피소드를 실로봇에서 "
            "재현합니다.\n로봇 노드가 켜져 있어야 하고, 로봇이 실제로 "
            "움직입니다. 주변을 비우세요.\n재생 중에는 이 버튼이 '재생 중단'"
            "이 됩니다 (중단 시 로봇은 현재 포즈 유지)."))
        self.replay_btn.clicked.connect(self._on_replay_selected)
        col.addWidget(self.replay_btn)

        del_btn = QPushButton(tr("선택한 에피소드 삭제"))
        del_btn.setToolTip(tr(
            "선택한 에피소드를 .hdf5 에서 실제로 지웁니다 (실패·튀는 궤적 큐레이션).\n"
            "legacy/scene 모두 삭제 후 번호를 다시 매깁니다 (scene 은 slot E번호와 "
            "uid 도 재부여).\nHub 에 이미 올라간 에피소드면 전체 재빌드가 필요합니다."
            "\n되돌릴 수 없습니다. 수집 중이 아닌 "
            "파일이면 세션 없이도 삭제됩니다. 파일 통째 삭제는 Dataset 메뉴에."))
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
            if repo_id_error(v) is None and v not in LEGACY_REPOS:
                return v
        # 아무것도 없거나 legacy 뿐이면 새 수집 저장소 기본값
        return DEFAULT_REPOS.get(key, "")

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

        # 계획 진행률 -- scene×slot 전체가 한눈에. 카운트는 언제나 scene
        # 파일에서 센다 (세션이 쥔 파일만 캐시로 대신).
        plan_box = QGroupBox(tr("수집 계획 진행률"))
        pcol = QVBoxLayout(plan_box)
        self.plan_progress_tree = QTreeWidget()
        self.plan_progress_tree.setHeaderLabels(
            [tr("scene / slot"), tr("수집"), tr("목표"), tr("문장")])
        self.plan_progress_tree.setRootIsDecorated(True)
        self.plan_progress_tree.header().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.plan_progress_tree.setMinimumHeight(160)
        pcol.addWidget(self.plan_progress_tree)
        prow = QHBoxLayout()
        self.plan_progress_label = QLabel("")
        self.plan_progress_label.setStyleSheet("color:#888;")
        prow.addWidget(self.plan_progress_label, 1)
        pb = QPushButton(tr("새로고침"))
        pb.clicked.connect(self._refresh_plan_progress)
        prow.addWidget(pb)
        pcol.addLayout(prow)
        col.addWidget(plan_box)

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

    # -------------------------------------------------------- point cloud
    def _build_cloud_tab(self) -> QWidget:
        """agent 카메라의 depth 포인트클라우드 뷰 (탭이 보일 때만 스트림).

        depth 는 상시로 켜 두면 USB 대역·안정성을 잡아먹으므로, 이 탭에
        들어올 때 RGB 미리보기를 잠깐 내리고 depth 워커를 올린다. 탭을
        떠나면 반대로 되돌린다 (수집 세션과는 아예 공존 불가 -- 세션 중엔
        안내만 보여준다).
        """
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(4, 4, 4, 4)
        self.cloud_view = VideoView()
        self.cloud_view.setText(tr("탭에 들어오면 depth 스트림을 켭니다"))
        # 크롭 가이드는 학습 프레이밍용 -- 3D 뷰에는 의미가 없고 어둡게만 보인다
        self.cloud_view.set_square_guide(False)
        col.addWidget(self.cloud_view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("카메라")))
        self.cloud_cam_combo = QComboBox()
        self.cloud_cam_combo.addItem("Agent", "agent")
        self.cloud_cam_combo.addItem("Wrist", "wrist")
        self.cloud_cam_combo.setToolTip(tr(
            "포인트클라우드를 읽을 카메라. 탭이 열려 있으면 즉시 전환합니다."))
        self.cloud_cam_combo.currentIndexChanged.connect(self._on_cloud_cam_changed)
        row.addWidget(self.cloud_cam_combo)
        row.addSpacing(12)
        row.addWidget(QLabel(tr("회전")))
        self.cloud_yaw = QSlider(Qt.Orientation.Horizontal)
        self.cloud_yaw.setRange(-80, 80)
        self.cloud_yaw.setValue(25)
        self.cloud_yaw.valueChanged.connect(lambda *_: self._render_cloud())
        row.addWidget(self.cloud_yaw, 1)
        row.addWidget(QLabel(tr("기울임")))
        self.cloud_pitch = QSlider(Qt.Orientation.Horizontal)
        self.cloud_pitch.setRange(-80, 80)
        self.cloud_pitch.setValue(-30)
        self.cloud_pitch.valueChanged.connect(lambda *_: self._render_cloud())
        row.addWidget(self.cloud_pitch, 1)
        col.addLayout(row)
        self.cloud_status = QLabel("")
        self.cloud_status.setStyleSheet("color:#888;")
        col.addWidget(self.cloud_status)
        return w

    def _build_depth_tab(self) -> QWidget:
        """depth 컬러맵 라이브 뷰 -- Point Cloud 와 같은 워커·같은 수명주기.

        스키마의 depth 기록(#17)과 별개다: 여기는 수집 전에 depth 품질과
        범위를 눈으로 확인하는 뷰고, 기록 여부는 Settings 의 스키마
        체크박스가 정한다.
        """
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(4, 4, 4, 4)
        self.depth_view = VideoView()
        self.depth_view.setText(tr("탭에 들어오면 depth 스트림을 켭니다"))
        # depth 는 원본 해상도 그대로 기록/표시 -- 크롭 가이드 비적용
        self.depth_view.set_square_guide(False)
        # 마우스가 가리키는 지점의 실거리 표시 (eventFilter 에서 처리)
        self.depth_view.setMouseTracking(True)
        self.depth_view.installEventFilter(self)
        self._depth_cursor = None
        col.addWidget(self.depth_view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("카메라")))
        self.depth_cam_combo = QComboBox()
        self.depth_cam_combo.addItem("Agent", "agent")
        self.depth_cam_combo.addItem("Wrist", "wrist")
        self.depth_cam_combo.currentIndexChanged.connect(self._on_cloud_cam_changed)
        row.addWidget(self.depth_cam_combo)
        row.addSpacing(12)
        row.addWidget(QLabel(tr("최대 거리")))
        self.depth_range_slider = QSlider(Qt.Orientation.Horizontal)
        self.depth_range_slider.setRange(30, 300)      # 0.3 ~ 3.0 m
        self.depth_range_slider.setValue(120)
        self.depth_range_slider.valueChanged.connect(
            lambda *_: self._render_depth())
        row.addWidget(self.depth_range_slider, 1)
        self.depth_range_label = QLabel("1.2 m")
        self.depth_range_label.setStyleSheet("color:#888;")
        row.addWidget(self.depth_range_label)
        col.addLayout(row)
        self.depth_status = QLabel(tr(
            "가까움=빨강, 멂=파랑, 검정=측정 불가. 기록 여부는 Settings 의 "
            "스키마 체크박스(#17)가 정합니다."))
        self.depth_status.setStyleSheet("color:#888;")
        self.depth_status.setWordWrap(True)
        col.addWidget(self.depth_status)
        return w

    def _depth_role_combo(self) -> QComboBox:
        return (self.depth_cam_combo if self._depth_consumer == "depth"
                else self.cloud_cam_combo)

    def _depth_views(self) -> list:
        return [self.cloud_view, self.depth_view]

    def _start_cloud(self) -> None:
        if self.worker is not None:
            for v in self._depth_views():
                v.clear_frame(tr("수집 세션 중에는 사용할 수 없습니다 — "
                                 "세션 종료 후 다시 여세요"))
            return
        role = self._depth_role_combo().currentData() or "agent"
        combo = self.agent_combo if role == "agent" else self.wrist_combo
        serial = self._combo_serial(combo)
        if not serial:
            for v in self._depth_views():
                v.clear_frame(
                    tr("Configure 에서 {r} 카메라를 선택하세요").format(r=role))
            return
        if self._cloud_worker is not None:
            if serial == self._cloud_serial and self._cloud_worker.isRunning():
                return                      # 같은 카메라 -- 탭만 바뀐 것
            # 다른 카메라거나, 오류로 죽은 워커가 남아 있는 경우(죽은 워커를
            # '살아있다'고 믿으면 탭을 다시 들어와도 스트림이 영영 안 선다)
            self._stop_cloud(restore_previews=False)
        # depth 파이프라인은 RGB 미리보기와 같은 장치를 두 번 열 수 없다.
        # OR-누적: 카메라 전환 재시작 때(미리보기 이미 내려간 상태) 복원
        # 약속을 잊지 않게 한다. 플래그는 실제 복원 때 리셋된다.
        self._cloud_previews_were_on = (self._cloud_previews_were_on
                                        or bool(self.agent_preview
                                                or self.wrist_preview))
        self._stop_previews_async()
        msg = tr("depth 스트림 여는 중... ({s})").format(s=serial)
        self.cloud_status.setText(msg)
        self.depth_status.setText(msg)
        self._ensure_camera_node()
        w = DepthCloudWorker(role, serial, mode=self._depth_consumer or "cloud")
        w.cloud_ready.connect(self._on_cloud)
        w.depth_ready.connect(self._on_depth_img)
        w.error.connect(self._on_depth_error)
        w.start()
        self._cloud_worker = w
        self._cloud_serial = serial

    def _on_depth_error(self, m: str) -> None:
        text = tr("depth 오류: {m}").format(m=m)
        self.cloud_status.setText(text)
        self.depth_status.setText(text)

    def _on_cloud_cam_changed(self, *_args) -> None:
        if self._cloud_worker is None:      # 탭이 닫혀 있으면 다음 진입 때 반영
            return
        self._stop_cloud(restore_previews=False)  # 복원 약속(플래그)은 유지된다
        for v in self._depth_views():
            v.clear_frame(tr("카메라 전환 중..."))
        self._start_cloud()

    def _stop_cloud(self, restore_previews: bool = True) -> None:
        w = self._cloud_worker
        if w is None:
            return
        self._cloud_worker = None
        self._cloud_serial = ""
        w.stop()
        w.wait(3000)
        self.cloud_status.setText(tr("depth 스트림 종료"))
        self.depth_status.setText(tr("depth 스트림 종료"))
        if restore_previews and self._cloud_previews_were_on \
                and self.worker is None:
            self._cloud_previews_were_on = False
            # 파이프라인이 놓이는 데 잠깐 걸린다 -- 바로 열면 busy.
            QTimer.singleShot(700, lambda: (
                self._restart_previews() if self.worker is None
                and self._cloud_worker is None else None))

    @pyqtSlot(object, object)
    def _on_cloud(self, pts, rgb) -> None:
        self._cloud_pts, self._cloud_rgb = pts, rgb
        if self._depth_consumer == "cloud":     # 보이는 탭만 렌더
            self._render_cloud()
            self.cloud_status.setText(
                tr("점 {n:,}개 · 회전/기울임 슬라이더로 시점 변경").format(n=len(pts)))

    @pyqtSlot(object)
    def _on_depth_img(self, z) -> None:
        self._depth_img = z
        if self._depth_consumer == "depth":
            self._render_depth()

    def _depth_uv(self, pos) -> "tuple | None":
        """depth_view 위젯 좌표 -> depth 이미지 픽셀 좌표 (밖이면 None).

        VideoView 는 KeepAspectRatio + 중앙 정렬이라 스케일과 여백을
        되짚어야 한다.
        """
        z = self._depth_img
        if z is None:
            return None
        h, w = z.shape[:2]
        lw = max(1, self.depth_view.width())
        lh = max(1, self.depth_view.height())
        s = min(lw / w, lh / h)
        u = int((pos.x() - (lw - w * s) / 2) / s)
        v = int((pos.y() - (lh - h * s) / 2) / s)
        if 0 <= u < w and 0 <= v < h:
            return (u, v)
        return None

    def _render_depth(self) -> None:
        """depth(m) → JET 컬러맵 + 척도 바 + 커서 지점 실거리."""
        z = self._depth_img
        if z is None:
            return
        import cv2

        zmax = self.depth_range_slider.value() / 100.0
        self.depth_range_label.setText(f"{zmax:.1f} m")
        frame = _depth_colormap(z, zmax)
        cursor_txt = ""
        if self._depth_cursor is not None:
            u, v = self._depth_cursor
            if not (0 <= u < z.shape[1] and 0 <= v < z.shape[0]):
                self._depth_cursor = None   # 프레임 크기가 바뀐 뒤 남은 커서
        if self._depth_cursor is not None:
            u, v = self._depth_cursor
            zval = float(z[v, u])
            label = f"{zval:.3f} m" if zval > 0.001 else tr("무측정")
            cursor_txt = tr(" · 커서 ({u},{v}) = {d}").format(u=u, v=v, d=label)
            cv2.circle(frame, (u, v), 7, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.line(frame, (u - 11, v), (u + 11, v), (255, 255, 255), 1)
            cv2.line(frame, (u, v - 11), (u, v + 11), (255, 255, 255), 1)
            for color, thick in (((0, 0, 0), 3), ((255, 255, 255), 1)):
                cv2.putText(frame, label, (min(u + 12, z.shape[1] - 90), max(v - 10, 16)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, thick,
                            cv2.LINE_AA)
        self.depth_view.set_frame(frame)
        n_ok = int(((z > 0.05) & (z <= zmax)).sum())
        self.depth_status.setText(
            tr("유효 픽셀 {p}% · 범위 0.05~{m:.1f} m{c} · 기록 여부는 Settings "
               "스키마(#17)").format(p=round(100 * n_ok / z.size), m=zmax,
                                     c=cursor_txt))

    def _render_cloud(self) -> None:
        """포인트클라우드 → 고정 시점 직교 투영 이미지 (numpy 래스터라이즈)."""
        pts, rgb = self._cloud_pts, self._cloud_rgb
        if pts is None or len(pts) == 0:
            return
        yaw = np.deg2rad(self.cloud_yaw.value())
        pitch = np.deg2rad(self.cloud_pitch.value())
        cy, sy = np.cos(yaw), np.sin(yaw)
        cp, sp = np.cos(pitch), np.sin(pitch)
        ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
        p = (pts - pts.mean(axis=0)) @ (rx @ ry).T
        h_out, w_out = 480, 640
        # 로버스트 범위(2/98 퍼센타일)로 스케일 -- 튀는 점이 화면을 줄이지 않게
        lo = np.percentile(p[:, :2], 2, axis=0)
        hi = np.percentile(p[:, :2], 98, axis=0)
        span = np.maximum(hi - lo, 1e-6)
        s = 0.92 * min(w_out / span[0], h_out / span[1])
        u = ((p[:, 0] - (lo[0] + hi[0]) / 2) * s + w_out / 2).astype(np.int32)
        v = ((p[:, 1] - (lo[1] + hi[1]) / 2) * s + h_out / 2).astype(np.int32)
        keep = (u >= 0) & (u < w_out - 1) & (v >= 0) & (v < h_out - 1)
        u, v, z = u[keep], v[keep], p[keep, 2]
        c = rgb[keep]
        order = np.argsort(-z)              # 먼 점부터 -- 가까운 점이 덮어쓴다
        u, v, c = u[order], v[order], c[order]
        canvas = np.full((h_out, w_out, 3), 16, np.uint8)
        for du, dv in ((0, 0), (1, 0), (0, 1), (1, 1)):   # 2×2 점
            canvas[v + dv, u + du] = c
        self.cloud_view.set_frame(canvas)

    def _on_center_tab_changed(self, idx: int) -> None:
        """레이아웃 탭이 보이는 동안만 하단 로그를 접고 슬라이드쇼를 돌린다."""
        if idx == getattr(self, "_cloud_tab_index", -1):
            self._depth_consumer = "cloud"
        elif idx == getattr(self, "_depth_tab_index", -1):
            self._depth_consumer = "depth"
        else:
            self._depth_consumer = None
        if self._depth_consumer is not None:
            self._start_cloud()     # 이미 같은 카메라로 돌고 있으면 유지
            if self._cloud_worker is not None:
                # 보이는 탭 것만 계산하도록 워커 모드 전환 (사용자 요구:
                # depth 계산도 그 탭에 들어갔을 때만)
                self._cloud_worker.mode = self._depth_consumer
        elif self._cloud_worker is not None:
            self._stop_cloud()
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

        # 그룹(scene·문장) 필터 -- 편차는 이미 그룹 단위로 계산되지만, 후보
        # 목록도 한 그룹만 놓고 보아야 "이 작업 안에서 어떤 테이크가 튀나"가
        # 읽힌다 (파일 선택은 scene 단위까지만 좁혀 주었다).
        grow = QHBoxLayout()
        grow.addWidget(QLabel(tr("그룹")))
        self.group_combo = QComboBox()
        _shrinkable_combo(self.group_combo)
        self.group_combo.addItem(tr("(전체)"), None)
        self.group_combo.currentIndexChanged.connect(self._refresh_rank_list)
        grow.addWidget(self.group_combo, 1)
        fcol.addLayout(grow)

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
                tr("이 에피소드의 평균 |Δa| 에서 같은 (scene·문장) 그룹 평균을 뺀 값 (rad/frame).\n"
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
        cols = QLabel(tr("같은 (scene·문장) 그룹 평균과의 차 — ±{d} 밖이면 급함(빨강)/느림(파랑)")
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
        ws_grid_btn = QPushButton(tr("3×3 워크스페이스 격자 편집..."))
        ws_grid_btn.setToolTip(tr(
            "카메라에 비친 작업면의 꼭짓점 4개를 드래그해 3×3 격자를 만들고 "
            "저장합니다.\nLive 탭의 '3×3 격자' 체크박스로 겹쳐 볼 수 있습니다."))
        ws_grid_btn.clicked.connect(self._on_edit_grid)
        dform.addRow(ws_grid_btn)
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

        # 지금 수집 중인 scene 의 물체 배치(3×3)를 세션 내내 보여준다 --
        # 물체를 제자리에 되돌릴 때 Configure 로 오갈 필요가 없게.
        scene_box = QGroupBox(tr("Scene 배치 (수집 중)"))
        sv = QVBoxLayout(scene_box)
        sv.setContentsMargins(6, 6, 6, 6)
        self.right_scene_view = SceneInfoView()
        self.right_scene_view.setText(tr("(scene 세션 없음)"))
        sv.addWidget(self.right_scene_view)
        col.addWidget(scene_box)

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
        self.left_stack.setMinimumWidth(200)
        self.right_panel.setMinimumWidth(200)
        # 배치도가 붙으면서 패널이 창보다 길어질 수 있다 -- 세로 스크롤로
        # 감싼다 (가로는 원칙대로 없음, 내용이 접힌다).
        _relax_min_widths(self.right_panel)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(self.right_panel)
        right_scroll.setMinimumWidth(200)
        self.right_scroll = right_scroll
        self.center_tabs.setMinimumWidth(420)
        self.bottom_tabs.setMinimumHeight(90)
        self.upper_split.addWidget(self.left_stack)
        self.upper_split.addWidget(self.center_split)
        self.upper_split.addWidget(self.right_scroll)
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
        m.addAction(tr("튀는 것만 선택 (scene·문장 그룹 평균과 ±{d} 밖)")
                    .format(d=TASK_DEV_LIMIT),
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
        m.addAction(tr("카메라 노드 재시작"),
                    self._on_restart_camera_node)
        m.addAction(tr("카메라 노드 종료 (카메라 해제)"),
                    self._on_stop_camera_node_manual)

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
            lambda on: self.right_scroll.setVisible(on))
        m.addAction(self.act_toggle_right)

        m = mb.addMenu(tr("Tools"))
        m.addAction(tr("시스템 튜닝 실행 (runme.sh)"), self._run_runme)
        m.addAction(tr("카메라 점검 (USB 속도·프레임)"), self._on_check_cameras)
        m.addAction(tr("리더암 서보 보호 해제 (재부팅)"),
                    self._on_reset_leader_protection)
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
               "  리셋 대기 중   Enter        리셋 완료 — 계속\n\n"
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
            self._refresh_plan_progress()
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
        self._ensure_camera_node()

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
            return  # 세션 중 카메라 교체는 없다 -- 노드도 그대로 둔다
        self._ensure_camera_node()   # 선택이 바뀌면 노드를 새 구성으로 재시작
        self._restart_previews()

    def _on_toggle_previews(self) -> None:
        # 세션 중에도 켜고 끌 수 있다: 카메라 노드가 장치를 갖고 있고 이쪽은
        # 구독자일 뿐이라 worker 와 경합하지 않는다 (2026-09-01).
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
            w = CameraPreviewWorker(role, serial)
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
        # 기록 중에는 worker 가 보내는 프레임이 이긴다 -- 화면에 보이는 것이
        # 실제로 파일에 쓰이는 그림이어야 하기 때문이다. 그 외 단계(게이트·
        # 리셋 대기)에서는 worker 가 카메라를 아예 안 읽으므로 여기가 유일한
        # 공급원이고, 노드 속도 그대로 나온다.
        if self._current_state == "recording":
            return
        self._update_live_view(role, frame)
        if self.center_tabs.currentIndex() == self._layout_tab_index:
            self._layout_update_role(role)
        self._fps_count += 1

    def _update_live_view(self, role: str, frame) -> None:
        """라이브 프레임 공용 경로 -- 원본 캐시 + 표시 (겹침 없음)."""
        self._last_cam_frame[role] = frame      # 격자 없는 원본을 저장
        self.live_views[role].set_frame(self._with_grid(role, frame))

    def _set_live_maximized(self, role: "str | None") -> None:
        """좌우 배치는 유지하고 스플리터 비율만 바꾼다 -- 최대화한 쪽이
        ~88%, 반대쪽은 아주 작게. 겹침(PiP) 없음. 경계는 드래그로도 조절."""
        if role == self._live_maximized:
            return
        self._live_maximized = role
        total = max(self.live_split.width(), 800)
        if role is None:
            self.live_split.setSizes([total // 2, total // 2])
        else:
            big, small = int(total * 0.88), max(90, int(total * 0.12))
            self.live_split.setSizes([big, small] if role == "agent"
                                     else [small, big])
        idx = 0 if role is None else self.live_view_combo.findData(role)
        if idx >= 0 and self.live_view_combo.currentIndex() != idx:
            self.live_view_combo.blockSignals(True)
            self.live_view_combo.setCurrentIndex(idx)
            self.live_view_combo.blockSignals(False)

    def _with_grid(self, role: str, frame):
        """agent 라이브 화면에만 워크스페이스 3×3 격자를 덧그린다 (사본)."""
        if role != "agent" or not self.grid_live_check.isChecked():
            return frame
        corners = active_corners(self._grid_store)
        if not corners:
            return frame
        return draw_grid(frame, corners, self.grid_alpha_slider.value())

    def _on_grid_live_toggled(self, on: bool) -> None:
        self._grid_store["live_on"] = bool(on)
        save_grid_store(self._grid_store)
        if on and active_corners(self._grid_store) is None:
            self.log(tr("[격자] 저장된 격자가 없습니다 — '격자 편집...'에서 "
                        "만들어 저장하세요."))
        self._regrid_live()

    def _on_grid_alpha(self, val: int) -> None:
        # 드래그 중에는 화면만 갱신하고, 저장은 놓을 때 한 번(_on_grid_alpha_done).
        self.grid_alpha_label.setText(tr("{v}%").format(v=val))
        self._grid_store["alpha"] = int(val)
        self._regrid_live()

    def _on_grid_alpha_done(self) -> None:
        save_grid_store(self._grid_store)

    def _regrid_live(self) -> None:
        """마지막 프레임으로 agent 뷰를 다시 그린다 -- 멈춘 화면에서도
        체크박스/슬라이더가 즉시 반영되게."""
        frame = self._last_cam_frame.get("agent")
        if frame is not None:
            self.live_views["agent"].set_frame(self._with_grid("agent", frame))

    def _on_edit_grid(self) -> None:
        bg = self._last_cam_frame.get("agent")
        if bg is None:
            bg = self._layout_ref.get("agent")
        if bg is None:
            bg = np.full((480, 640, 3), 60, np.uint8)
            self.log(tr("[격자] 카메라 프레임이 없어 회색 배경에서 편집합니다 — "
                        "미리보기를 켜면 실제 화면 위에서 맞출 수 있습니다."))
        dlg = GridEditorDialog(self, bg, self._grid_store,
                               crop_params=dict(self._crop_params["agent"]))
        dlg.exec()
        self._grid_store = load_grid_store()    # 저장 결과를 다시 정본에서
        self._regrid_live()

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
        # 노드가 죽었거나 다른 구성으로 떠 있으면 여기서 맞춘다. worker 는
        # 장치를 직접 열지 않으므로(노드 구독) 이게 유일한 카메라 준비 단계다.
        if self._camera_node_user_stopped:
            # 수동 종료 상태에서 몰래 되살리면 외부 프로그램(VLA)이 쥔
            # 카메라를 노드가 빼앗으려 든다 -- 명시적 재시작을 요구한다.
            QMessageBox.warning(self, tr("카메라 노드 종료 상태"),
                                tr("카메라 노드가 수동으로 종료되어 있습니다 "
                                   "(외부 프로그램용 카메라 해제).\n"
                                   "Camera 메뉴 > 카메라 노드 재시작 후 다시 "
                                   "연결하세요."))
            return
        self._ensure_camera_node()
        try:
            ep_len = float(self.eplen_edit.text())
            reset_wait = float(self.resetwait_edit.text())
        except ValueError:
            QMessageBox.warning(self, tr("입력 오류"), tr("길이/대기는 숫자여야 합니다."))
            return

        # 미리보기는 이제 세션 내내 살려 둔다 (2026-09-01). 예전에는 여기서
        # 껐다 -- worker 가 카메라 장치를 직접 열던 시절, RealSense 파이프라인을
        # 두 번 열 수 없어서였다. 2026-08-25 3-프로세스 분리 이후로는 장치를
        # 카메라 노드가 독점하고 미리보기도 worker 도 그냥 ZMQ 구독자다
        # (PUB/SUB 는 팬아웃이라 경합이 없다). 끄면 오히려 게이트 중 화면이
        # 노드 속도(30 fps)에서 수집 루프 속도로 떨어지고, 게이지가 그 프레임
        # 뒤에 줄을 서서 같이 느려졌다.
        #
        # depth(포인트클라우드)는 사정이 다르다 -- 그건 여전히 장치를 직접
        # 여는 경로라 여기서 놓아야 한다.
        self._stop_cloud(restore_previews=False)
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
            # 오른쪽 배치도 -- 이어찍기는 metadata 가 파일에만 있으므로 워커가
            # 파일을 쥐기 전인 지금 읽어 둔다.
            md = scene_meta
            if md is None and scene_sid:
                try:
                    md = read_scene_metadata(
                        Path(cfg.data_root) / scene_filename(scene_sid))
                except Exception:  # noqa: BLE001
                    md = None
            self._set_right_scene(md, scene_sid)
        else:
            self._set_right_scene(None)

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
        for key in ("discard", "home"):
            self.tb_actions[key].setEnabled(running)
        for key in ("save", "savefail"):
            self.tb_actions[key].setEnabled(savable)
        self.tb_actions["connect"].setEnabled(not running)
        self.tb_actions["disconnect"].setEnabled(running)
        for b in (self.skip_btn, self.discard_btn, self.home_btn):
            b.setEnabled(running)
        if not running:
            self._gate_ok = None
        # Start(기록 시작)는 게이트 자세 조건까지 본다 -- 아래 헬퍼가 전담.
        self._update_start_controls(running)
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

    def _update_start_controls(self, running: "bool | None" = None) -> None:
        """Start Teleop 버튼/툴바는 게이트 상태에선 자세가 맞아야만 열린다.

        자동 정렬이 켜져 있어도 같다 -- 정렬은 리더가 범위(GATE_RAD) 안에
        들어와야 발동하므로, 그 전에 시작을 눌러도 워커가 거부만 한다.
        버튼을 잠가서 '왜 안 되는지'를 누르기 전에 보이게 한다.
        """
        if running is None:
            running = self.worker is not None
        # _gate_ok 는 게이트 진입 직후 None("아직 모름") 일 수 있다 -- setEnabled 는
        # bool 만 받으므로 여기서 확정한다.
        ok = bool(running and (self._current_state != "gate" or self._gate_ok))
        self.start_btn.setEnabled(ok)
        act = getattr(self, "tb_actions", {}).get("record")
        if act is not None:
            act.setEnabled(ok)

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
        if state == "gate" and self._current_state != "gate":
            # 새 게이트: 첫 gate_status 가 올 때까지 시작을 잠근다. None 은
            # '아직 모름' -- _on_gate 가 변화가 있을 때만 그리므로, 여기서
            # False 로 두면 첫 상태가 False 일 때 라벨이 안 갱신된다.
            self._gate_ok = None
        self._current_state = state
        self._update_start_controls()
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
            self._update_live_view(role, rgb)
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
        # 아래는 전부 all_ok 가 '바뀔 때만' 의미가 있는 일이다. 게이트는
        # 초당 45번 오는데, 매번 라벨 텍스트·스타일시트를 다시 쓰고 버튼
        # 활성 상태를 재계산하면 -- setStyleSheet 은 Qt 가 스타일을 통째로
        # 다시 파싱하게 만드는 호출이다 -- GUI 스레드가 그 뒤에 밀려 바가
        # 손을 늦게 따라온다. 워커는 45 Hz 로 멀쩡히 보내고 있었다 (실측
        # 0.7~2.6 ms/틱), 병목은 이쪽이었다 (2026-09-01).
        if all_ok != self._gate_ok:
            self._gate_ok = all_ok
            self.gate_label.setText(tr("자세 일치 — 시작 가능") if all_ok
                                    else tr("리더를 팔로워 자세에 맞추세요"))
            self.gate_label.setStyleSheet(
                "color:#2ecc71;" if all_ok else "color:#e67e22;")
            # Enter/버튼은 워커와 같은 조건에서만 열린다. 잠겨 있는 이유가
            # 보이도록 게이트 상태의 힌트도 all_ok 에 따라 바꾼다.
            self.match_btn.setEnabled(all_ok)
            self._update_start_controls()
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
        # 자동 진행이 없어졌으므로 카운트다운이 아니라 경과 시간이다.
        self.state_label.setText(
            tr("리셋 중 {s:.0f}s 경과 — 배치 후 Enter").format(s=seconds))

    @pyqtSlot(bool)
    def _on_node_status(self, ok) -> None:
        self.lights["node"].set("ok" if ok else "bad", tr("정상") if ok else tr("응답 없음"))
        self.right_fields["node"].setText(tr("정상") if ok else tr("응답 없음"))

    @pyqtSlot(str)
    def _on_fatal(self, msg) -> None:
        self.log(f"[치명적 오류] {msg}")
        # 서보 과토크 보호(overload 0x20 등 hardware error)로 죽은 세션은
        # GUI 재시작이 아니라 서보 Reboot 으로만 복구된다 -- 그 툴이 있는
        # 위치를 오류 대화상자에서 바로 알려준다 (#37B).
        if "hardware error" in msg:
            msg += tr("\n\n서보 보호모드가 걸렸습니다. 세션 종료 후 "
                      "Tools > 리더암 서보 보호 해제 (재부팅) 으로 복구하세요.")
        self._alert(tr("오류"), msg, QMessageBox.Icon.Critical)

    @pyqtSlot(int, str)
    def _on_connected(self, n_episodes, path) -> None:
        # 세션이 붙었다 = 노드가 살아 응답했다 (연결 검증이 노드 경유).
        self.lights["node"].set("ok", tr("정상"))
        self.right_fields["node"].setText(tr("정상"))
        # 연결되면 카메라 화면으로 따라간다. 버튼을 누른 시점이 아니라 여기인
        # 이유는, 연결이 미리보기 정리를 기다리거나 실패할 수 있기 때문이다 --
        # 그때 Live 로 옮겨두면 아무것도 안 나오는 탭을 보게 된다.
        self.center_tabs.setCurrentIndex(self._live_tab_index)
        # 기록 외 단계에서는 worker 가 카메라를 읽지 않으므로(게이지를 빠르게
        # 유지하기 위해 -- _emit_gate_status 참고) 미리보기가 그 구간의 유일한
        # 영상 공급원이다. 꺼져 있으면 자세를 맞추는 동안 화면이 빈다.
        if not (self.agent_preview or self.wrist_preview):
            self._restart_previews()
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
        # 직전 세션에서 삭제가 실패해 남았을 수 있는 대기 건수를 청산 --
        # 새 세션의 첫 목록 갱신이 엉뚱한 무효화를 하지 않게.
        self._pending_scene_deletes = 0
        if self._scene_session:
            # scene 파일이 실제로 만들어졌으니 보관해 둔 새 scene 구성은 소진.
            self._pending_scene_meta = None
            self._refresh_slot_panel()
        self._update_dataset_panel()
        self.log(f"[연결] 파일: {path} (기존 {n_episodes}개 에피소드)")
        self._refresh_dataset_tree()

    @pyqtSlot(list)
    def _on_episode_list(self, episodes) -> None:
        prev_n = len(self.active_episode_cache) if self.active_episode_cache else None
        self.active_episode_cache = episodes
        if self._pending_scene_deletes > 0 and self._scene_session:
            # 목록이 줄어든 emit 만 삭제 완료로 센다 -- 사이에 낀 저장/재판정
            # emit(개수 불변·증가)이 카운터를 잘못 소진하지 않게. 삭제 1건마다
            # renumber 로 uid 가 재배정되므로 매번 통째로 무효화한다.
            if prev_n is not None and len(episodes) < prev_n:
                self._pending_scene_deletes = max(
                    0, self._pending_scene_deletes - (prev_n - len(episodes)))
                try:
                    # 파일은 saver 가 잠그고 있다 -- 다시 열지 않고 세션 설정에서
                    # scene_id 를 얻는다 (_session_scene_id).
                    sid = self._session_scene_id()
                    n_thumbs = invalidate_scene_thumbs(sid) if sid else 0
                    if n_thumbs:
                        self.log(f"[썸네일] {sid}: {n_thumbs}개 캐시 무효화")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[썸네일 캐시 정리 실패] {e}")
        self._refresh_dataset_tree()
        if self._scene_session:
            # 저장/재판정마다 saver 가 새 목록을 보내온다 -- slot 카운트 갱신
            self._refresh_slot_panel()
            self._refresh_start_plan_combo()   # Configure 쪽 카운트도 동기화

    @pyqtSlot(dict)
    def _on_summary(self, summary) -> None:
        # 해제는 여기서 하지 않는다 -- 정상 종료에만 오는 신호다. 실제 해제는
        # 모든 종료 경로에서 오는 finished(_on_worker_finished)가 맡는다.
        self.log(f"[세션 요약] {summary}")

    def _set_right_scene(self, md, sid=None) -> None:
        """오른쪽 패널의 '수집 중 scene 배치도'를 갱신한다."""
        if not hasattr(self, "right_scene_view"):
            return
        if md is not None:
            self.right_scene_view.setText(describe_scene(md))
        elif sid:
            self.right_scene_view.setText(
                tr("{s} — 배치 정보를 읽지 못했습니다").format(s=sid))
        else:
            self.right_scene_view.setText(tr("(scene 세션 없음)"))

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
        self._set_right_scene(None)
        self._set_running(False)
        self._refresh_dataset_tree()
        if was_scene:
            # 세션이 만든/키운 scene 파일이 목록·slot 현황에 반영되게.
            self._refresh_scene_combo()
        self._restart_previews()
        if self._depth_consumer is not None:
            # 세션 동안 Depth/Point Cloud 탭에 머물러 있었다면 스트림을 다시
            # 올린다 (세션 중엔 안내만 보였다). 미리보기가 뜨는 시간을 준다.
            QTimer.singleShot(600, lambda: (
                self._start_cloud() if self.worker is None
                and self._depth_consumer is not None else None))

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

    def _refresh_plan_progress(self) -> None:
        """Statistics 의 계획 진행률 표 -- 계획 × 실제 scene 파일 대조."""
        tree = getattr(self, "plan_progress_tree", None)
        if tree is None:
            return
        tree.clear()
        plan = self._current_plan()
        if plan is None:
            self.plan_progress_label.setText(
                tr("Configure 에서 수집 계획을 선택하세요."))
            return
        root = Path(self.root_edit.text().strip() or ".")
        done = total = 0
        skipped: list = []
        for sp in plan.scenes:
            path = root / scene_filename(sp.scene_id)
            counts: dict = {}
            note = ""
            if self._scene_session and sp.scene_id == self._session_scene_id():
                counts = self._session_slot_counts()
                note = tr(" (세션 중 — 캐시)")
            elif path.exists():
                try:
                    counts = count_by_slot(path)
                except Exception:  # noqa: BLE001 -- 잠금 등
                    note = tr(" (파일 사용 중)")
            else:
                # 파일이 없는(아직 안 찍었거나 지운) scene 은 표에 넣지
                # 않는다 -- 지운 파일의 slot 목록이 계속 보이는 것이
                # 혼란스럽다는 실사용 피드백. 개수는 아래 요약에 남긴다.
                skipped.append(sp.scene_id)
                continue
            s_done = s_total = 0
            top = QTreeWidgetItem([f"{sp.scene_id}{note}", "", "", ""])
            for s in sp.slots:
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                s_done += min(c, s.target)
                s_total += s.target
                it = QTreeWidgetItem(
                    [f"  {s.instruction_id}", str(c), str(s.target),
                     s.instruction])
                if c >= s.target:
                    for col_i in range(4):
                        it.setForeground(col_i, Qt.GlobalColor.darkGreen)
                top.addChild(it)
            top.setText(1, str(s_done))
            top.setText(2, str(s_total))
            done += s_done
            total += s_total
            tree.addTopLevelItem(top)
        tree.expandAll()
        pct = (100 * done // total) if total else 0
        text = tr("전체 {d}/{t} ({p}%) — {n}").format(
            d=done, t=total, p=pct, n=plan.path.name)
        if skipped:
            text += tr("  ·  파일 없는 scene {n}개 표시 안 함 ({s})").format(
                n=len(skipped), s=", ".join(skipped[:4]))
        self.plan_progress_label.setText(text)

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
            # 연결 시점 설정이 아니라 '지금' slot 을 보여준다 -- scene 세션은
            # Disconnect 없이 slot(문장·ID)을 바꾸므로(cmd_set_slot) 설정값만
            # 보여주면 전환 뒤에도 첫 문장이 그대로 남는다 (실사용 보고).
            cur_instr = getattr(self.worker, "_slot_instruction", None) \
                or cfg.language_instruction or cfg.task_name
            cur_iid = getattr(self.worker, "_slot_instruction_id", "") or cfg.instruction_id
            task_text = f"{cur_iid}: {cur_instr}" if (self._scene_session and cur_iid) \
                else cur_instr
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
                if "data" in h:
                    data = h["data"]
                    info = data.attrs.get("problem_info")
                    if info:
                        try:
                            task = json.loads(json.loads(info)["language_instruction"])
                        except Exception:  # noqa: BLE001
                            task = str(info)[:60]
                    names = sorted(data.keys(), key=lambda s: int(s.split("_")[1]))
                    container = data
                else:
                    # scene-v1: task 는 파일 단위 개념이 아니다 -- scene ID 로 표기
                    task = "scene " + str(h["metadata"].attrs.get("scene_id", "?"))
                    names = sorted((k for k in h.keys() if k.startswith("episode_")),
                                   key=lambda s: int(s.split("_")[1]))
                    container = h
                if names:
                    g = container[names[0]]
                    action = str(g.attrs.get("action_space", "-"))
                    conv = str(g.attrs.get("gripper_action_convention", ""))
                    gripper = {"01": "0/1 (obs와 동일)", "pm1": "-1/+1"}.get(conv, conv or "-")
                    rgb = g.get("obs", {}).get(OBS_AGENTVIEW_RGB)
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
        # Dataset 페이지의 폴더 선택을 따른다 (수집 경로 하드코딩 제거) --
        # scene 파일도 함께 스캔한다.
        root = self._dataset_root()
        files = hdf5_files(root) + [str(p) for p in iter_scene_files(root)]
        if not files:
            self.analysis_summary.setText(
                tr("{r} 에 *_demo.hdf5 / scene_*.hdf5 가 없습니다.").format(r=root))
            return
        t0 = time.monotonic()
        self._stats = scan_dataset(files)
        self._summary = summarize(self._stats)
        dt = time.monotonic() - t0
        s = self._summary
        self.analysis_summary.setText(
            tr("에피소드 {n}개 · {f:,}프레임 · 그룹(scene·문장) {t}개 · 길이 {a}~{b}프레임\n{v}").format(
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
        self._refresh_group_combo()
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
        out = [e for e in out if path is None or e.path == path]
        grp = self.group_combo.currentData() if hasattr(self, "group_combo") else None
        if grp is not None:
            out = [e for e in out if e.group == grp]
        return out

    def _refresh_group_combo(self) -> None:
        """Analysis 스캔 결과의 (scene·문장) 그룹으로 콤보를 채운다 -- 선택은
        가능하면 유지한다 (새로고침마다 (전체) 로 튀지 않게)."""
        if not hasattr(self, "group_combo"):
            return
        keep = self.group_combo.currentData()
        groups = sorted({e.group for e in self._stats})
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem(tr("(전체)"), None)
        for g in groups:
            n = sum(1 for e in self._stats if e.group == g)
            label = (f"{g[0]} · {g[1]}" if g[0] else g[1])
            self.group_combo.addItem(f"{label}  ({n})", g)
        idx = 0
        for i in range(self.group_combo.count()):
            if self.group_combo.itemData(i) == keep:
                idx = i
                break
        self.group_combo.setCurrentIndex(idx)
        self.group_combo.blockSignals(False)

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
                f"{e.seconds:.1f}s", e.group_label[:40]])
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
                tr("{d} · {n}프레임 ({s:.1f}s) · 평균 |Δa| {m:.5f} · 같은 (scene·문장) 그룹 평균과 "
                   "{v:+.4f}{mark} · 멈춤 {p:.0f}%\n{t}").format(
                       d=demo, n=stat.n_frames, s=stat.seconds, m=stat.mean_da,
                       v=stat.task_dev,
                       mark=" (급함)" if stat.task_dev > TASK_DEV_LIMIT else (
                           " (느림)" if stat.task_dev < -TASK_DEV_LIMIT else ""),
                       p=100 * stat.still_frac, t=stat.group_label))
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
    def _dataset_root(self) -> Path:
        """Dataset 페이지의 폴더 -- 전용 입력이 있으면 그것, 없으면 수집 경로.
        (빌드 순서상 어느 쪽도 아직 없을 수 있다 -- 기본 경로로 폴백.)"""
        edit = (getattr(self, "dataset_root_edit", None)
                or getattr(self, "root_edit", None))
        if edit is None:
            return Path.home() / "libero_datasets"
        return Path(edit.text().strip()).expanduser()

    def _browse_dataset_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("데이터 폴더"),
                                             self.dataset_root_edit.text())
        if d:
            self.dataset_root_edit.setText(d)
            self._refresh_dataset_tree()

    def _refresh_dataset_tree(self) -> None:
        self.dataset_tree.clear()
        root = self._dataset_root()
        if not root.is_dir():
            return
        # ---- scene 파일 (scene-v1). 재생·재판정 UI 는 #31 갤러리에서 --
        # 여기서는 목록·개수·quality 확인 + 삭제/트림 대상 선택용. 삭제는
        # legacy 와 같이 삭제 후 renumber -- _delete_episodes.
        for path in iter_scene_files(root):
            item = QTreeWidgetItem([path.name, "", "scene"])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.dataset_tree.addTopLevelItem(item)
            try:
                if (self.active_file_path is not None
                        and path == self.active_file_path
                        and self.active_episode_cache is not None):
                    episodes = self.active_episode_cache
                else:
                    from gello.scene.scene_format import list_scene_episodes

                    episodes = list_scene_episodes(path)
            except Exception as e:  # noqa: BLE001
                item.setText(1, f"({type(e).__name__})")
                continue
            for ep in episodes:
                label = f"  {ep['name']} · {ep.get('instruction_id', '')}"
                q = ep.get("quality_status") or (
                    "-" if ep.get("success") is None
                    else ("success" if ep["success"] else "failed"))
                child = QTreeWidgetItem([label, str(ep.get("num_samples", "")), q])
                child.setData(0, Qt.ItemDataRole.UserRole, ep["name"])
                child.setToolTip(0, ep.get("instruction", ""))
                item.addChild(child)
            item.setText(1, tr("{n}개").format(n=len(episodes)))
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
        if hasattr(self, "gallery_scene_combo"):
            self._refresh_gallery_scenes()
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

    def _on_hdf5_tree(self) -> None:
        path = self._selected_file()
        if path is None:
            QMessageBox.information(self, tr("선택 필요"),
                                    tr("트리로 볼 파일을 먼저 선택하세요."))
            return
        if self.active_file_path is not None and path == self.active_file_path:
            QMessageBox.information(self, tr("파일 사용 중"), tr(
                "수집 세션이 이 파일을 쥐고 있습니다 — 세션 종료 후 여세요."))
            return
        Hdf5TreeDialog(self, path).exec()

    def _on_myhdf5(self) -> None:
        webbrowser.open("https://myhdf5.hdfgroup.org/")
        path = self._selected_file()
        if path is not None:
            self.log(tr("[myHDF5] 브라우저 창에 파일을 끌어다 놓으세요: {p}")
                     .format(p=path))

    def _on_relabel_selected(self) -> None:
        """scene 에피소드의 quality_status 를 성공↔실패로 뒤집는다.

        scene 체계의 큐레이션 수단이다 (삭제 없음, 변환이 success 만 내보냄).
        소유권 규칙은 삭제와 동일: 세션이 파일을 쥐고 있으면 saver 스레드
        경유, 아니면 직접 쓴다 (SceneWriter.set_quality_status 와 같은 필드).
        success/failed 이외의 상태(bad_data 등)는 건드리지 않는다.
        """
        by_file: dict = {}
        for item in self.dataset_tree.selectedItems():
            if item.parent() is None:
                continue
            p = Path(item.parent().data(0, Qt.ItemDataRole.UserRole))
            by_file.setdefault(p, []).append(item.data(0, Qt.ItemDataRole.UserRole))
        by_file = {p: v for p, v in by_file.items() if p.name.startswith("scene_")}
        if not by_file:
            QMessageBox.information(
                self, tr("선택 필요"),
                tr("재판정할 scene 에피소드를 선택하세요 (legacy 파일은 세션 중 "
                   "판정 버튼을 사용)."))
            return
        if self._relabel_episodes(by_file):
            self._refresh_dataset_tree()

    def _replay_running(self) -> bool:
        return (self.replay_process is not None and
                self.replay_process.state() != QProcess.ProcessState.NotRunning)

    def _on_replay_selected(self) -> None:
        if self._replay_running():      # 토글: 재생 중이면 중단 버튼이다
            self._on_replay_stop()
            return
        picks = [(Path(i.parent().data(0, Qt.ItemDataRole.UserRole)),
                  i.data(0, Qt.ItemDataRole.UserRole))
                 for i in self.dataset_tree.selectedItems()
                 if i.parent() is not None]
        if len(picks) != 1:
            QMessageBox.information(
                self, tr("선택 필요"),
                tr("실로봇 재생은 에피소드 하나만 선택하세요."))
            return
        self._replay_on_robot(str(picks[0][0]), picks[0][1])

    def _replay_on_robot(self, path: str, demo: str) -> None:
        """Dataset 트리와 Gallery 가 공유하는 실로봇 재생 진입점.

        replay_episode.py 를 --yes 로 하위 프로세스 실행한다 (램프·틱당
        클램프 같은 안전장치는 스크립트 쪽에 있다). 로봇을 쥐는 것은 결국
        로봇 노드 하나이므로, 여기서는 GUI 세션과의 충돌만 막는다.
        """
        if self.worker is not None:
            QMessageBox.warning(self, tr("재생 불가"),
                                tr("수집 세션 중에는 실로봇 재생을 할 수 "
                                   "없습니다. 먼저 세션을 종료하세요."))
            return
        busy = self._busy_reason()
        if busy:
            QMessageBox.warning(self, tr("재생 불가"),
                                tr("{w} 이(가) 파일을 사용 중입니다. 끝난 뒤 "
                                   "다시 시도하세요.").format(w=busy))
            return
        if self.replay_process is not None and \
                self.replay_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, tr("이미 재생 중"),
                                    tr("이전 재생이 끝나기를 기다리거나 '재생 "
                                       "중단'을 누르세요."))
            return
        speed, ok = QInputDialog.getDouble(
            self, tr("실로봇 재생"),
            tr("재생 배속 (0.1~1.0, 첫 재생은 0.5 권장)"),
            0.5, 0.1, 1.0, 1)
        if not ok:
            return
        ans = QMessageBox.warning(
            self, tr("로봇이 움직입니다"),
            tr("{d} ({f}) 을(를) {s}배속으로 실로봇에서 재현합니다.\n\n"
               "· 로봇 노드가 켜져 있어야 합니다\n"
               "· 로봇이 시작 포즈로 이동한 뒤 바로 재생됩니다\n"
               "· 주변 공간을 비우고, 비상정지를 준비하세요\n\n"
               "시작할까요?").format(d=demo, f=Path(path).name, s=speed),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([REPLAY_SCRIPT, path, demo,
                           "--speed", f"{speed:g}", "--yes"])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self._pipe(proc, "[실로봇 재생]", "log"))
        proc.finished.connect(self._on_replay_finished)
        self.replay_process = proc
        self.log(f"[실로봇 재생] ▶ {Path(path).name} / {demo} ({speed:g}x)")
        proc.start()
        self._set_replay_ui(True)

    def _on_replay_stop(self) -> None:
        """재생 하위 프로세스를 끊는다. 로봇 노드의 레퍼런스 필터가 현재
        포즈를 유지하므로(Ctrl-C 와 동일) 팔이 낙하하지는 않는다."""
        proc = self.replay_process
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        self.log(tr("[실로봇 재생] 중단 요청 — 현재 포즈에서 정지합니다"))
        proc.terminate()
        if not proc.waitForFinished(2000):
            proc.kill()

    def _set_replay_ui(self, running: bool) -> None:
        """재생/중단 토글 -- 두 진입점(Dataset·Gallery) 버튼이 함께 바뀐다."""
        for b, idle_text in ((getattr(self, "replay_btn", None),
                              tr("선택 재생 (실로봇)")),
                             (getattr(self, "gallery_replay_btn", None),
                              tr("실로봇 재생"))):
            if b is None:
                continue
            b.setText(tr("■ 재생 중단") if running else idle_text)
            b.setStyleSheet(
                "background-color:#c0392b; color:white;" if running else "")

    def _on_replay_finished(self, code: int, _status) -> None:
        self.replay_process = None
        self._set_replay_ui(False)
        self.log(tr("[실로봇 재생] {r} (exit={c})").format(
            r=tr("완료") if code == 0 else tr("중단/실패 — 로그 확인"), c=code))

    def _relabel_episodes(self, by_file: dict) -> bool:
        """재판정 공용 코어 -- Dataset 트리와 Gallery 가 같은 것을 쓴다.

        세션이 파일을 쥐고 있으면 HDF5 를 다시 열지 않는다. 같은 프로세스에서
        쓰기 중인 파일을 재오픈하면 h5py 가 거부하므로, 대신 saver 가 이미 연
        파일 핸들을 재사용하도록 큐 명령으로 보낸다. 판정값은 saver 가 채워주는
        ``active_episode_cache`` 에서 읽는다.
        """
        busy = self._busy_reason()
        if busy:
            QMessageBox.warning(self, tr("재판정 불가"),
                                tr("{job}이(가) 진행 중입니다.").format(job=busy))
            return False
        flipped = skipped_state = skipped_cache = 0
        cache: dict[str, dict] = {}
        if self.active_file_path is not None and self.active_episode_cache is not None:
            cache = {e["name"]: e for e in self.active_episode_cache}
        for path, names in by_file.items():
            owned = self.active_file_path is not None and path == self.active_file_path
            try:
                if owned:
                    # 세션 소유 파일: h5py 재오픈 없이 캐시에서 읽고 saver 큐로 쓴다.
                    for name in names:
                        e = cache.get(name)
                        if e is None:
                            skipped_cache += 1
                            self.log(f"[재판정] {path.name} / {name}: 캐시에 없어 건너뜀")
                            continue
                        q = str(e.get("quality_status", ""))
                        if "quality_status" not in e:
                            # 캐시 요약에 quality_status 가 없으면 success 로 판단.
                            success = e.get("success")
                            if success is True:
                                q = "success"
                            elif success is False:
                                q = "failed"
                        if q not in ("success", "failed"):
                            skipped_state += 1
                            continue
                        new_ok = q != "success"
                        self.worker.cmd_set_episode_success(name, new_ok)
                        flipped += 1
                else:
                    # 비소유 파일. 호출 경로(_on_relabel_selected 의 scene 필터,
                    # scene 전용 Gallery)가 scene 파일만 넘기므로 legacy 분기는
                    # 두지 않는다 -- 도달 불가한 분기는 규약이 어긋난 채 썩는다.
                    with h5py.File(path, "a") as f:
                        for name in names:
                            q = str(f[name].attrs.get("quality_status", ""))
                            if q not in ("success", "failed"):
                                skipped_state += 1
                                continue
                            new_ok = q != "success"
                            f[name].attrs["quality_status"] = (
                                "success" if new_ok else "failed")
                            f[name].attrs["success"] = new_ok
                            flipped += 1
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, tr("재판정 실패"),
                                     f"{path.name}\n{type(e).__name__}: {e}")
                return False
        parts = [f"[재판정] {flipped}개 뒤집음"]
        if skipped_state:
            parts.append(f"{skipped_state}개 건너뜀 (success/failed 아님)")
        if skipped_cache:
            parts.append(f"{skipped_cache}개 건너뜀 (세션 캐시에 없음)")
        self.log(", ".join(parts))
        return True

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

    def _describe_delete_targets(self, by_file: dict):
        """삭제 확인창용: (행 목록, 성공 개수, Hub 안내문). 파일을 읽지 못하면
        (세션이 쥔 파일 등) 캐시로 대신하고, 그것도 없으면 이름만 나열한다."""
        from gello.scene.scene_format import list_scene_episodes

        rows: list = []
        n_success = 0
        tasks: set = set()
        uids: set = set()
        for path, names in by_file.items():
            eps: dict = {}
            try:
                if path.name.startswith("scene_"):
                    src = (self.active_episode_cache
                           if (self.active_file_path is not None
                               and path == self.active_file_path)
                           else list_scene_episodes(path)) or []
                    eps = {e["name"]: e for e in src}
                else:
                    with h5py.File(path, "r") as f:
                        data = f["data"]
                        for n in names:
                            if n in data:
                                g = data[n]
                                ok = g.attrs.get("success", True)
                                eps[n] = {"episode_uid": n, "instruction": "",
                                          "quality_status": "success" if ok else "failed",
                                          "num_samples": int(g.attrs.get("num_samples", 0))}
            except Exception:  # noqa: BLE001 -- 잠금 등: 이름만
                eps = {}
            for n in names:
                e = eps.get(n)
                if e is None:
                    rows.append(f"  {path.name} / {n}")
                    continue
                q = str(e.get("quality_status", "?"))
                if q == "success":
                    n_success += 1
                instr = str(e.get("instruction", ""))
                if instr:
                    tasks.add(instr)
                if path.name.startswith("scene_") and e.get("episode_uid"):
                    uids.add(str(e["episode_uid"]))
                rows.append(f"  {e.get('episode_uid', n)}  [{q}]  {e.get('num_samples', '?')}f"
                            + (f"  {instr[:40]}" if instr else ""))
        hub_note = ""
        try:
            repo = self.repo_id_for("repo_id")
        except Exception:  # noqa: BLE001
            repo = ""
        if repo and (tasks or uids) and not repo_id_error(repo):
            # 판정 단위는 에피소드(uid)다. Hub 의 meta/episode_uids.json 사이드카에
            # 지울 uid 가 있을 때만 "올라가 있다" 고 말한다. 사이드카가 없는 repo
            # (legacy 수집분만 있는 데이터셋)는 에피소드 단위 판정이 불가능하므로
            # 문장(task) 단위 일치를 '참고' 로만 표시한다 -- 같은 문장의 legacy
            # 에피소드가 있다고 이 에피소드가 올라간 것은 아니다 (실사용 혼란).
            try:
                from gello.data.dataset_sync import hub_episode_uids, hub_meta

                hub_uids, err = hub_episode_uids(repo)
                if err:
                    hub_note = ""
                elif hub_uids is not None:
                    hit = sorted(uids & hub_uids)
                    if hit:
                        hub_note = tr("Hub({r})에 이 에피소드 {k}개가 이미 올라가 "
                                      "있습니다 ({u}{more}) — 다음 전체 처리에서 "
                                      "'삭제됨' 으로 잡혀 재빌드(교체)가 필요합니다.")\
                            .format(r=repo, k=len(hit), u=", ".join(hit[:3]),
                                    more=" …" if len(hit) > 3 else "")
                    else:
                        hub_note = tr("Hub({r})에는 이 에피소드가 올라가 있지 않습니다 "
                                      "(uid 대조).").format(r=repo)
                else:
                    hub, _lens, err2 = hub_meta(repo)
                    if not err2:
                        same = [t for t in tasks if hub.get(t, 0) > 0]
                        if same:
                            hub_note = tr("참고: Hub({r})에는 uid 사이드카가 없어 에피소드 "
                                          "단위 확인이 안 됩니다. 같은 문장의 task {k}개가 "
                                          "있지만(legacy 수집분일 수 있음) 이 에피소드가 "
                                          "올라갔다는 뜻은 아닙니다.").format(r=repo, k=len(same))
            except Exception:  # noqa: BLE001 -- 오프라인 등: 안내 생략
                hub_note = ""
        return rows, n_success, hub_note

    def _delete_episodes(self, by_file: dict) -> bool:
        """공용 삭제 경로. Dataset 패널과 Analysis 순위표가 같은 것을 쓴다 --
        세션 소유 검사와 실행 중 작업 검사를 두 벌로 두면 반드시 갈라진다."""
        busy = self._busy_reason()
        if busy:
            QMessageBox.warning(self, tr("삭제 불가"),
                                tr("{job}이(가) 진행 중입니다. 끝난 뒤 삭제하세요.").format(job=busy))
            return False
        total = sum(len(v) for v in by_file.values())
        # 확인창: 무엇을 지우는지(uid·문장·판정·프레임) 목록으로 보여주고,
        # 성공분이 섞였으면 경고, Hub 에 이미 올라간 에피소드면 재빌드 안내.
        # "실패만 선택" 으로 고른 정상 경로에서는 경고가 뜨지 않는다 -- 손으로
        # 잘못 고른 성공분만 눈에 띄게 하는 것이 목적이다.
        rows, n_success, hub_note = self._describe_delete_targets(by_file)
        detail = "\n".join(rows[:30]) + ("\n  …" if len(rows) > 30 else "")
        notes = [tr("삭제 후 남은 에피소드는 번호가 다시 매겨집니다 (scene 은 slot E번호·uid 도).")]
        if hub_note:
            notes.append(hub_note)
        notes.append(tr("파일 크기는 줄지 않습니다 (재압축 필요). 되돌릴 수 없습니다."))
        title = tr("에피소드 삭제")
        body = tr("에피소드 {n}개를 삭제합니다.\n\n{d}\n\n{notes}").format(
            n=total, d=detail, notes="\n".join(notes))
        if n_success:
            body = tr("⚠ 성공(success) 에피소드 {k}개가 포함되어 있습니다 — "
                      "정말 의도한 선택인지 확인하세요.\n\n").format(k=n_success) + body
            if QMessageBox.warning(
                    self, title, body,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
        elif QMessageBox.question(self, title, body) != QMessageBox.StandardButton.Yes:
            return False

        for path, names in by_file.items():
            owned = self.active_file_path is not None and path == self.active_file_path
            is_scene = path.name.startswith("scene_")
            if owned:
                # 세션이 파일을 쥐고 있으면 saver 스레드가 유일한 통로다. 매 삭제
                # 뒤 번호가 다시 매겨지므로 뒤에서부터 지워야 앞 이름이 안 밀린다.
                for name in sorted(names, key=lambda s: int(s.split("_")[1]), reverse=True):
                    self.worker.cmd_delete_episode(name)
                self.log(f"[삭제] {path.name}: {len(names)}개 요청 (세션 경유)")
                if is_scene:
                    # saver 가 삭제를 1건 완료할 때마다 episode_list_changed ->
                    # _on_episode_list 가 카운터를 줄이며 썸네일을 지운다.
                    self._pending_scene_deletes += len(names)
                continue
            try:
                if is_scene:
                    delete_scene_episodes(path, names)
                    # renumber 로 uid 가 재배정되므로 해당 scene 의 썸네일 캐시를
                    # 전부 무효화한다. 삭제와 별도 try -- 썸네일 정리 실패가
                    # "삭제 실패" 로 오표기되면 안 된다 (삭제는 이미 성공했다).
                    try:
                        sid = read_scene_metadata(path).scene_id
                        n_thumbs = invalidate_scene_thumbs(sid)
                        if n_thumbs:
                            self.log(f"[썸네일] {path.name}: {n_thumbs}개 캐시 무효화")
                    except Exception as e:  # noqa: BLE001
                        self.log(f"[썸네일 캐시 정리 실패] {path.name}: {e}")
                else:
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
        self.log(f"[큐레이션] 같은 (scene·문장) 그룹 평균과 {TASK_DEV_LIMIT} 넘게 차이 나는 "
                 f"에피소드 {n}개를 선택했습니다." + ("" if n else " (없음)"))
        self.dataset_hint.setText(
            tr("튀는 에피소드 {n}개 선택됨 — 재생으로 확인한 뒤 '에피소드 삭제'로 지웁니다.")
            .format(n=n) if n else
            tr("같은 (scene·문장) 그룹 평균과 {d} 넘게 차이 나는 에피소드가 없습니다 "
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
        # legacy 는 번역된 '실패', scene 은 quality_status 원문('failed')이
        # 상태 컬럼에 실린다 -- 둘 다 잡아야 한다 (scene 실패가 선택되지
        # 않던 실사용 버그).
        fail_labels = {tr("실패"), "failed"}
        for i in range(self.dataset_tree.topLevelItemCount()):
            parent = self.dataset_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.text(2) in fail_labels:
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
        # 이 파일의 에피소드가 Hub 에 이미 있으면 다음 전체 처리가 '삭제됨' 으로
        # 잡아 재빌드(교체)를 요구한다 -- 지금 지우는 것이 리모트에 어떤 결과를
        # 낳는지 삭제 순간에 알린다 (오프라인이면 안내 생략).
        hub_line = tr("Hub 에 올린 사본은 지금은 그대로지만, 다음 전체 처리 때 "
                      "로컬 기준으로 재빌드되어 교체됩니다.")
        if path.name.startswith("scene_"):
            try:
                from gello.scene.scene_format import list_scene_episodes

                names = [e["name"] for e in list_scene_episodes(path)]
                _rows, _n_ok, note = self._describe_delete_targets({path: names})
                if note:
                    hub_line = note
            except Exception:  # noqa: BLE001 -- 잠금/오프라인: 기본 안내
                pass
        confirm = QMessageBox.warning(
            self, tr("파일 삭제"),
            tr("{f}\n\n에피소드 {n}개, {mb:.1f} MB 를 완전히 삭제합니다.\n"
               "되돌릴 수 없습니다.\n{h}").format(
                   f=path.name, n=st["episodes"], mb=st["size"] / 1e6, h=hub_line),
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
        if path.name.startswith("scene_"):
            # scene 파일은 legacy 구조 검사 대신 표준 뷰(격자 지도 + slot 현황).
            try:
                md = read_scene_metadata(path)
                counts = count_by_slot(path)
                text = describe_scene(md)
                if counts:
                    text += "\n\nslot 현황: " + "  ".join(
                        f"{iid} {c['usable']}/{c['total']}"
                        for iid, c in sorted(counts.items()))
                text += "\n\n" + tr("정밀 검사: python scripts/check/check_scene_file.py {p}").format(p=path)
            except Exception as e:  # noqa: BLE001
                text = f"{path.name}\n읽기 실패: {type(e).__name__}: {e}"
            self._alert(tr("Scene 구조"), text, icon=QMessageBox.Icon.Information)
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
        for st in steps:
            if st.get("detail"):
                self.log(f"  · [{st['name']}] {st['detail']}", "upload")
        self._run_next_pipeline_step()

    def _on_hdf5_auto(self) -> None:
        """재압축 -> 원본 HDF5 업로드."""
        if not self._pipeline_guard(tr("HDF5 자동 처리")):
            return
        data_root = self.root_edit.text().strip()
        paths = self._all_hdf5(data_root)
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"),
                                tr("{r} 에 *_demo.hdf5 / scene_*.hdf5 가 없습니다.").format(r=data_root))
            return
        repo = self._check_repo("hdf5_repo_id", tr("HDF5 재압축 + 업로드"))
        if repo is None:
            return
        todo = [x for x in paths if not hdf5_repack_status(x)["repacked"]]
        # 업로드 대상은 업로드 장부가 고른다: 지난 업로드 성공 이후 바뀐
        # 파일 + 기록 없는 파일 + 이번에 재압축될 파일. 예전 "재압축분만"
        # 방식은 attr 만 고친 파일을 빠뜨렸다 (2026-08-25 교체). 어떤 파일이
        # 왜 올라가는지 확인창에 그대로 보여준다.
        sel = {str(x): r for x, r in changed_files(repo, paths)}
        for x in todo:
            sel.setdefault(str(x), tr("재압축 — 이번 실행에서 다시 압축됨"))
        changed = [(str(x), sel[str(x)]) for x in paths if str(x) in sel]
        listing = "\n".join(f"  · {Path(x).name}: {r}" for x, r in changed) \
            or "  " + tr("(지난 업로드 이후 바뀐 파일 없음)")
        box = QMessageBox(QMessageBox.Icon.Question, tr("HDF5 재압축 + 업로드"),
                          tr("파일 {n}개 중 재압축 필요 {m}개, 업로드 대상 {c}개.\n"
                             "재압축 후 {r} 에 원본을 업로드합니다.\n\n"
                             "업로드 대상과 사유:\n{l}\n\n진행할까요?")
                          .format(n=len(paths), m=len(todo), c=len(changed),
                                  r=repo, l=listing),
                          QMessageBox.StandardButton.Yes
                          | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        only_new = QCheckBox(
            tr("변경된 파일만 자동 선택 ({c}개) — 해제하면 전체 강제 업로드")
            .format(c=len(changed)))
        only_new.setChecked(True)
        box.setCheckBox(only_new)
        if box.exec() != QMessageBox.StandardButton.Yes:
            self.log("[HDF5 자동] 취소했습니다.", "upload")
            return
        steps = []
        if todo:
            steps.append({"name": tr("재압축"), "program": sys.executable,
                          "args": [REPACK_SCRIPT, *todo]})
        if only_new.isChecked():
            if changed:
                steps.append({"name": tr("HDF5 원본 업로드 (변경분 {n}개)")
                              .format(n=len(changed)),
                              "detail": "; ".join(
                                  f"{Path(x).name}: {r}" for x, r in changed),
                              "program": sys.executable,
                              "args": [UPLOAD_SCRIPT, *[x for x, _ in changed],
                                       "--repo-id", repo, "--no-private"]})
            else:
                steps.append({"name": tr("HDF5 원본 업로드 — 생략"),
                              "note": tr("지난 업로드 이후 바뀐 파일이 "
                                         "없습니다 (장부 기준).")})
        else:
            steps.append({"name": tr("HDF5 원본 업로드 (전체 강제)"),
                          "program": sys.executable,
                          "args": [UPLOAD_SCRIPT, *[str(x) for x in paths],
                                   "--repo-id", repo, "--no-private"]})
        if not any("program" in st for st in steps):
            self.log("[HDF5 자동] 할 일이 없습니다 — 재압축 대상도, "
                     "변경된 파일도 없습니다.", "upload")
            return
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
        paths = self._all_hdf5(data_root)
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"),
                                tr("{r} 에 *_demo.hdf5 / scene_*.hdf5 가 없습니다.").format(r=data_root))
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
        paths = self._all_hdf5(data_root)
        if not paths:
            QMessageBox.warning(self, tr("파일 없음"),
                                tr("{r} 에 *_demo.hdf5 / scene_*.hdf5 가 없습니다.").format(r=data_root))
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
                "paths": self._all_hdf5(data_root)}
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
        for st in steps:
            if st.get("detail"):
                self.log(f"  · [{st['name']}] {st['detail']}", "upload")
        self._run_next_pipeline_step()

    def _run_next_pipeline_step(self) -> None:
        if not self._pipeline_steps:
            self._finish_pipeline(True)
            return
        step = self._pipeline_steps[0]
        if "program" not in step:
            # 정보용 단계 (예: 'HDF5 원본 업로드 — 생략') -- 프로세스 없이
            # 사유만 로그·요약에 남기고 넘어간다. 자동 선택이 파일을 하나도
            # 안 고른 날, '왜 안 올라갔는지'가 보이게 하는 장치 (2026-08-25).
            self._pipeline_steps.pop(0)
            self._pipeline_results.append((step["name"], 0, 0.0))
            self.log(f"\n[전체 처리] · {step['name']} — "
                     f"{step.get('note', '')}", "upload")
            self._run_next_pipeline_step()
            return
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
        """Runs scripts/check/check_cameras.py into the Validation tab.

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

    def _on_reset_leader_protection(self) -> None:
        """scripts/check/gello_reset_protection.py 실행 -- 과토크 보호모드 해제 (#37B).

        서보의 overload(0x20) 래치는 Reboot 으로만 풀린다. 스크립트가 리더암
        시리얼 포트를 직접 여므로, 세션(worker)이 포트를 잡고 있는 동안은
        실행하지 않는다 -- wall 스레드와 같은 버스를 두고 싸우는 경로 자체를
        막는다. 재부팅 후 재설정은 필요 없다: operating mode 는 EEPROM 이라
        살아남고, 다음 세션의 wall.start() 가 나머지를 다시 세팅한다.
        """
        p = getattr(self, "_reset_protection_process", None)
        if p is not None and p.state() != QProcess.ProcessState.NotRunning:
            self.log("[리더암] 보호 해제가 이미 실행 중입니다.")
            return
        if self.worker is not None:
            self.log("[리더암] 세션이 리더암 포트를 잡고 있어 실행할 수 없습니다 -- "
                     "Robot > 세션 종료 후 다시 시도하세요.")
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([RESET_PROTECTION])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: [self.log(f"[리더암] {ln}")
                     for ln in self._proc_text(proc).splitlines() if ln.strip()])
        proc.finished.connect(lambda c, _s: self.log(
            {0: "[리더암] 보호 해제 완료 -- 새 세션을 시작할 수 있습니다.",
             1: "[리더암] 일부 서보가 복구되지 않았습니다 -- 5V 전원을 껐다 켜고 "
                "관절이 물리적으로 걸려 있지 않은지 확인하세요.",
             2: "[리더암] 리더암 포트를 열지 못했습니다 -- 연결/전원을 확인하세요."}
            .get(c, f"[리더암] 보호 해제 종료 (exit={c})")))
        self._reset_protection_process = proc
        self.log("=== 리더암 서보 보호 해제 ===")
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
    # ------------------------------------------------- 카메라 노드 (별도 프로세스)
    def _camera_node_specs(self) -> list:
        specs = []
        for role, combo in (("agent", self.agent_combo),
                            ("wrist", self.wrist_combo)):
            serial = self._combo_serial(combo)
            if serial:
                specs.append(f"{role}:{serial}")
        return specs

    def _on_restart_camera_node(self) -> None:
        self._camera_node_user_stopped = False
        self._ensure_camera_node(restart=True)

    def _on_stop_camera_node_manual(self) -> None:
        """카메라를 완전히 놓는다 -- VLA 배포 등 외부 프로그램이 장치를
        직접 열 수 있게. 미리보기·depth 뷰도 함께 내린다 (구독자만 남으면
        에러만 5초마다 찍는다)."""
        if self.worker is not None:
            QMessageBox.warning(self, tr("세션 진행 중"),
                                tr("수집 세션이 카메라를 쓰고 있습니다. "
                                   "세션 종료 후 노드를 내리세요."))
            return
        self._camera_node_user_stopped = True
        self._stop_cloud(restore_previews=False)
        self._stop_previews_async()
        self._stop_camera_node()
        for role in ("agent", "wrist"):
            self.live_views[role].clear_frame(tr("카메라 노드 종료됨"))
        self.lights["camera"].set("off", tr("노드 종료"))
        self.log("[카메라노드] 수동 종료 — 카메라가 해제되어 다른 프로그램"
                 "(VLA 정책 클라이언트 등)이 열 수 있습니다. 다시 쓰려면 "
                 "Camera 메뉴 > 카메라 노드 재시작.")

    def _ensure_camera_node(self, restart: bool = False) -> None:
        """카메라 노드 프로세스를 현재 콤보 선택과 일치하게 유지한다.

        이미 같은 구성으로 떠 있으면 아무것도 하지 않는다 -- 노드의 가치는
        "카메라를 한 번 열고 계속 스트리밍"에 있으므로 불필요한 재시작이
        가장 나쁘다. 선택이 바뀌었거나 죽었을 때만 (재)시작한다. 수동 종료
        래치가 켜져 있으면 건드리지 않는다 (외부 프로그램이 카메라를 쓰는
        중일 수 있다 -- restart=True 도 래치를 풀지 않는다, 그건
        _on_restart_camera_node 만 한다)."""
        if self._camera_node_user_stopped:
            return
        specs = self._camera_node_specs()
        key = ",".join(specs)
        running = (self.camera_node_process is not None and
                   self.camera_node_process.state()
                   != QProcess.ProcessState.NotRunning)
        if running and not restart and key == self._camera_node_spec:
            return
        if running:
            self._stop_camera_node()
        if not specs:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments(["-m", "gello.comm.camera_node", "--die-with-parent"]
                          + [a for sp in specs for a in ("--cam", sp)])
        proc.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_camera_node_output)
        proc.finished.connect(self._on_camera_node_finished)
        self.camera_node_process = proc
        self._camera_node_spec = key
        self.log(f"[카메라노드] 시작: {key}")
        proc.start()

    def _on_camera_node_output(self) -> None:
        if self.camera_node_process is None:
            return
        for line in self._proc_text(self.camera_node_process).splitlines():
            if line.strip():
                self.log(f"[카메라노드] {line.rstrip()}")

    def _on_camera_node_finished(self, code: int, _status) -> None:
        proc = self.sender()
        if proc is not self.camera_node_process:
            # _stop_camera_node() 나 _ensure(재시작) 가 이미 손을 뗀 프로세스
            # -- 의도된 종료라 조용히 보낸다.
            self.log(f"[카메라노드] 종료 (exit={code})")
            return
        # 비정상 종료 -- 자동 재시작한다. 단 crash-loop(예: 포트 충돌로
        # 뜨자마자 죽는 상태)이면 로그만 가득 채우므로 60초 내 3회를 넘으면
        # 멈추고 수동(카메라 메뉴)으로 넘긴다.
        self.camera_node_process = None
        self._camera_node_spec = ""
        now = time.monotonic()
        self._camera_node_crashes = [
            t for t in self._camera_node_crashes if now - t < 60.0] + [now]
        if len(self._camera_node_crashes) > 3:
            self.log(f"[카메라노드] 비정상 종료 (exit={code}) — 60초 내 "
                     f"{len(self._camera_node_crashes)}회째, 자동 재시작을 "
                     "멈춥니다. Camera 메뉴 > 카메라 노드 재시작으로 수동 "
                     "시작하세요.")
            return
        self.log(f"[카메라노드] 비정상 종료 (exit={code}) — 2초 후 자동 재시작")
        QTimer.singleShot(2000, self._ensure_camera_node)

    def _stop_camera_node(self) -> None:
        proc = self.camera_node_process
        self.camera_node_process = None
        self._camera_node_spec = ""
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        proc.terminate()
        if not proc.waitForFinished(3000):
            proc.kill()
            proc.waitForFinished(2000)

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
        proc.finished.connect(self._on_node_finished)
        self.node_process = proc
        self.log("[노드] 시작합니다...")
        # 인디케이터는 세션 중 장애 신호(node_status)로만 갱신되고 있어서,
        # 노드가 잘 떠 있어도 '노드 -' 로 남았다 (실화면에서 확인된 혼란).
        # GUI 가 켠 시점/종료 시점에도 갱신한다.
        self.lights["node"].set("busy", tr("시작 중"))
        self.right_fields["node"].setText(tr("시작 중"))
        proc.start()

    def _on_node_finished(self, code: int, _status) -> None:
        self.log(f"[노드] 종료 (exit={code})")
        self.lights["node"].set("off", "-")
        self.right_fields["node"].setText("-")

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
    @staticmethod
    def _all_hdf5(data_root) -> list:
        """변환·업로드 대상 파일 전부: legacy 정렬 + scene 정렬.

        legacy 를 앞에 두는 순서는 plan_sync 의 길이 지문(접두 비교)과
        일치해야 하므로 dataset_sync._ordered_paths 와 같은 규칙이다.
        """
        root = Path(str(data_root))
        return ([str(p) for p in sorted(root.glob("*_demo.hdf5"))]
                + [str(p) for p in sorted(root.glob("scene_*.hdf5"))])

    def _hdf5_candidates(self) -> list:
        return self._all_hdf5(self.root_edit.text().strip() or str(Path.home()))

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
        if event.type() == QEvent.Type.MouseButtonDblClick:
            # 라이브 뷰 더블클릭 = 그 카메라 최대화/복원 토글
            for r, v in getattr(self, "live_views", {}).items():
                if obj is v:
                    self._set_live_maximized(
                        None if self._live_maximized == r else r)
                    return True
        if obj is getattr(self, "depth_view", None):
            # Depth 뷰 위에서 마우스가 가리키는 지점의 실거리 표시.
            # 마우스 이벤트만 소비하고 나머지(키 입력 등)는 아래 공용 단축키
            # 처리로 흘려보낸다 -- 무조건 return 하면 이 뷰에 포커스가 있는
            # 동안 Space/Esc 단축키가 죽는다.
            if (event.type() == QEvent.Type.MouseMove
                    and self._depth_img is not None):
                self._depth_cursor = self._depth_uv(event.position())
                self._render_depth()
                return False
            if event.type() == QEvent.Type.Leave \
                    and self._depth_cursor is not None:
                self._depth_cursor = None
                self._render_depth()
                return False
        if (
            event.type() == QEvent.Type.KeyPress
            and self.worker is not None
            and QApplication.activeModalWidget() is None
        ):
            key = event.key()
            state = self._current_state
            if key == Qt.Key.Key_Space:
                if state == "gate":
                    # 버튼과 같은 조건: 자세가 맞아야 시작 (워커도 거부하지만
                    # 이유를 먼저 보여준다).
                    if self._gate_ok:
                        self._cmd("cmd_start_teleop")
                    else:
                        self.log("[GATE] 아직 자세가 맞지 않아 시작할 수 없습니다 "
                                 "-- 리더를 팔로워 자세에 맞추세요.")
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
        self._stop_cloud(restore_previews=False)
        self._stop_previews_blocking()
        self._stop_camera_node()
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
