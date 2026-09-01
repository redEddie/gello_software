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
import traceback
import time
from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import (QEvent, QProcess, Qt, QThread, QTimer,
                          pyqtSlot)
from PyQt6.QtGui import QIcon, QTextCursor
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QTreeWidgetItem,
    QVBoxLayout,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.data.dataset_schema import (  # noqa: E402
    OBS_AGENTVIEW_RGB,
    load_schema_config,
    save_schema_config,
)
from gello.data.dataset_sync import plan_sync  # noqa: E402
from gello.data.episode_stats import (  # noqa: E402
    TASK_DEV_LIMIT,
    hdf5_files,
    load_series,
    scan_dataset,
    summarize,
)
from gello.data.episode_trim import plan_trim, suggest_trim, trim_tail  # noqa: E402
from gello.gui.gui_widgets import (  # noqa: E402
    repo_id_error,
    PLAYBACK_FPS,
    REPACK_SCRIPT,
    CameraPreviewWorker,
    DatasetSchemaDialog,
    DepthCloudWorker,
    GalleryLoadWorker,
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
from apps.workspace.constants import LOG_DIR  # noqa: E402
from apps.workspace.models import CameraState, PlaybackState, ProcessRegistry  # noqa: E402
from apps.workspace.builders import (  # noqa: E402
    build_bottom,
    build_center,
    build_layout,
    build_left,
    build_menu,
    build_right,
    build_statusbar,
    build_toolbar,
)
from apps.dialogs._image_utils import _depth_colormap  # noqa: E402
from apps.dialogs.grid_editor_dialog import GridEditorDialog  # noqa: E402
from apps.dialogs.hdf5_tree_dialog import Hdf5TreeDialog  # noqa: E402
from apps.dialogs.new_scene_dialog import NewSceneDialog  # noqa: E402
from apps.dialogs.pipeline_dialog import PipelineDialog  # noqa: E402
from apps.dialogs.plan_edit_dialog import PlanEditDialog  # noqa: E402
from gello.gui.grid_overlay import (  # noqa: E402
    draw_alignment_grid,
    active_corners,
    draw_grid,
    load_grid_store,
    save_grid_store,
)
from gello.gui.i18n import tr  # noqa: E402
from gello.data.libero_format import (  # noqa: E402
    default_crop_params,
    describe_episode,
    hdf5_repack_status,
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
from gello.scene.scene_rules import check  # noqa: E402
from gello.scene.scene_format import (  # noqa: E402
    INSTRUCTION_ID_RE,
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
CHECK_CAMERAS = str(Path(__file__).resolve().parent.parent / "scripts" / "check" / "check_cameras.py")
RESET_PROTECTION = str(Path(__file__).resolve().parent.parent / "scripts" / "check" / "gello_reset_protection.py")
# LIBERO 초기 배치 참조 이미지. 리모트에는 zip 만 올라가고(3.9MB), 풀린
# png 들은 .gitignore 의 *.png 에 걸린다. GUI 가 뜰 때 zip 이 바뀌었으면 다시
# 푼다 -- _ensure_layout_refs().
LAYOUT_ZIP = Path(__file__).resolve().parent.parent / "assets" / "libero_init_layouts.zip"
LAYOUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "libero_init_layouts"


def _new_stats() -> dict:
    """수집 카운터 한 벌. 이번 task 용과 누적용이 같은 모양이라 같은 곳에서 만든다."""
    return {"saved": 0, "success": 0, "failed": 0, "discarded": 0,
            "frames": 0, "t0": time.monotonic()}


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""

# Panels named in the UI spec that this build does not implement yet. They are
# shown, disabled and greyed, rather than omitted: a missing tab reads as "this
# tool cannot do that", while a greyed one says "not built yet" -- and leaving
# the shape visible is what makes the gap reviewable instead of forgotten.
# TODO_MARK 는 gello/gui_widgets.py 에서 가져온다 (순환 import 방지).

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




class WorkspaceWindow(QMainWindow):
    def __init__(self, log_path: Path | None) -> None:
        super().__init__()
        self.setWindowTitle(tr("FR3 GELLO 데이터 수집 워크스페이스"))
        self.resize(1780, 1020)

        self.worker: CollectionWorker | None = None
        self.procs = ProcessRegistry()
        self.playback = PlaybackState()
        self.cameras = CameraState()
        self.cameras.grid_store = load_grid_store()
        self._stats: list = []
        self._summary: dict = {}
        self._progress_line: dict = {}

        self.active_file_path: Path | None = None
        self.active_episode_cache: list | None = None
        self.agent_preview: CameraPreviewWorker | None = None
        self.wrist_preview: CameraPreviewWorker | None = None
        self._cloud_previews_were_on = False

        # 두 벌을 든다. _session 은 Connect 마다 0 으로 돌아가므로 "지금 찍고 있는
        # task 를 몇 개 모았나"이고, _cumulative 는 GUI 를 켠 뒤 전체다. 예전에는
        # _session 하나뿐이었고 그것이 Connect 때 리셋되지 않아서, task 를 바꿔
        # 연결하면 이전 task 의 개수가 그대로 따라왔다 -- 게다가 상태바가
        # max(목록 길이, 연결시점 + saved) 를 쓰는 탓에 정확한 목록이 도착핵도
        # 부풀려진 값이 이겨서, 빈 task 가 "에피소드 10개"로 보였다.
        self._session = _new_stats()
        self._cumulative = _new_stats()
        self._pending_success: bool | None = None
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
        self._connect_wait_since = None
        self._episodes_at_connect = 0
        self._recents = Recents()
        self._log_file = None
        if log_path is not None:
            self._log_file = open(log_path, "a", buffering=1)  # noqa: SIM115

        self.schema = load_schema_config()

        build_bottom(self)          # log view exists before anything logs
        # 저장된 설정의 depth 플래그가 무시됐다면 여기서(로그 뷰가 생긴 뒤)
        # 보이는 로그로 알린다 -- from_json 의 warnings 는 stderr 로만 가서
        # 데스크톱 아이콘 실행에서는 소실된다 (아래 excepthook 주석과 같은 이유).
        for flag in getattr(self.schema, "ignored_depth_flags", []):
            self.log(f"[스키마] 저장된 {flag}=True 를 무시합니다 -- "
                     "카메라 드라이버가 depth 읽기를 지원하지 않습니다")
        build_center(self)
        build_left(self)
        build_right(self)
        build_layout(self)
        build_toolbar(self)
        build_menu(self)
        build_statusbar(self)

        self.cameras.fps_timer = QTimer(self)
        self.cameras.fps_timer.timeout.connect(self._tick_fps)
        self.cameras.fps_timer.start(1000)

        self.playback.play_timer = QTimer(self)
        self.playback.play_timer.setInterval(int(1000 / PLAYBACK_FPS))
        self.playback.play_timer.timeout.connect(self._on_play_tick)

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
    # --------------------------------------------------------------- left
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
        dlg = NewSceneDialog(self, sid, data_root=root, plan_path=plan_path,
                             station_name=STATION.name)
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

    def _warn_ignored_legacy(self, plan: dict) -> None:
        """legacy(*_demo.hdf5)는 업로드 대상이 아니다 -- 남아 있으면 알린다.

        조용히 빼면 "왜 이 파일은 안 올라갔지" 를 나중에 데이터로 추적해야
        한다. 계획에서 빠졌다는 사실은 계획을 세우는 그 자리에서 말한다
        (issue #15).
        """
        names = plan.get("ignored_legacy") or []
        if names:
            self.log(f"[동기화] legacy 파일 {len(names)}개는 업로드 대상이 "
                     f"아닙니다 (scene 포맷만 배포): {', '.join(names[:5])}"
                     + (" ..." if len(names) > 5 else ""))

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
        self.center_tabs.setCurrentIndex(self.playback.trim_tab_index)

    # ------------------------------------------------------------------ Trim
    def _show_trim_for(self, path: str, demo: str) -> None:
        """Dataset 트리와 Analysis 순위표가 공유하는 트림 진입점."""
        if not path or not demo:
            return
        if self.active_file_path is not None and Path(path) == self.active_file_path:
            self.trim_summary.setText(tr("수집 중인 파일은 편집할 수 없습니다."))
            return
        self.playback.trim_key = (path, demo)
        self.playback.trim_n_pending = 0
        try:
            series = load_series(path, demo)
        except Exception as e:  # noqa: BLE001
            self.trim_summary.setText(tr("불러오기 실패: {e}").format(e=e))
            return
        self.playback.trim_series = series
        self.playback.trim_n = int(series["n"])
        for plot, dims in self.trim_plots.values():
            plot.set_data(series, dims)
        self.playback.trim_frames = {"agent": None, "wrist": None}
        for v in self.trim_views.values():
            v.clear_frame(tr("영상 불러오는 중..."))
        if self.playback.trim_loader is not None:
            self.playback.trim_loader.wait()
        self.playback.trim_loader = EpisodeLoadWorker(path, demo)
        self.playback.trim_loader.loaded.connect(self._on_trim_loaded)
        self.playback.trim_loader.failed.connect(
            lambda m: [v.clear_frame(tr("영상 없음")) for v in self.trim_views.values()])
        self.playback.trim_loader.start()
        self._trim_update()

    @pyqtSlot(str, str, object, object)
    def _on_trim_loaded(self, path, demo, agent, wrist) -> None:
        if self.playback.trim_key != (path, demo):
            return
        self.playback.trim_frames = {"agent": agent, "wrist": wrist}
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_pending(self) -> int:
        return self.playback.trim_n_pending

    def _trim_keep(self) -> int:
        return max(0, self.playback.trim_n - self._trim_pending())

    def _trim_add(self, n: int) -> None:
        """+/- 를 누른 만큼 옮긴다. 0 아래로는 못 간다 -- 원본보다 길어질 수 없다."""
        if self.playback.trim_key is None:
            return
        self.playback.trim_n_pending = max(0, self.playback.trim_n_pending + n)
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_reset(self) -> None:
        """정정 -- 고른 것을 통째로 0으로. 한 단계씩 물리는 것보다, 잘못 짚었을 때
        처음부터 다시 보는 쪽이 실제 흐름에 맞는다."""
        if self.playback.trim_key is None:
            return
        self.playback.trim_n_pending = 0
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_suggest(self) -> None:
        if self.playback.trim_key is None:
            return
        n = suggest_trim(*self.playback.trim_key)
        self.playback.trim_n_pending = n
        self.log(f"[트림] 추천 {n}프레임" + ("" if n else " (이미 조용하게 끝납니다)"))
        self._trim_update()
        self._trim_seek(self._trim_keep() - 1)

    def _trim_seek(self, i: int) -> None:
        n = self.playback.trim_n
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
            arr = self.playback.trim_frames.get(role)
            if arr is None or len(arr) == 0:
                continue
            v.set_frame(arr[min(i, len(arr) - 1)])
        mark = tr(" ← 잘린 뒤 마지막") if i == keep - 1 else (
            tr("  (잘려나갈 구간)") if i >= keep else "")
        self.trim_pos.setText(f"{i + 1}/{self.playback.trim_n}{mark}")
        for plot, _ in self.trim_plots.values():
            plot.set_cursor(i)

    def _on_trim_scrub(self, i: int) -> None:
        self._trim_show_frame(i)

    def _on_trim_play(self) -> None:
        """잘린 뒤 구간만 훑는다 -- 확인하려는 것이 '새 끝'이기 때문이다."""
        if self.playback.trim_key is None:
            return
        keep = self._trim_keep()
        self._trim_seek(max(0, keep - 40))
        if self.playback.trim_timer is None:
            self.playback.trim_timer = QTimer(self)
            self.playback.trim_timer.setInterval(50)
            self.playback.trim_timer.timeout.connect(self._trim_tick)
        self.playback.trim_timer.start()
        self.trim_play_btn.setText(tr("정지"))

    def _trim_tick(self) -> None:
        i = self.trim_slider.value() + 1
        if i >= self._trim_keep():
            self.playback.trim_timer.stop()
            self.trim_play_btn.setText(tr("재생"))
            return
        self._trim_seek(i)

    def _trim_update(self) -> None:
        """Recomputes every label, guard and shading from the pending count."""
        has = self.playback.trim_key is not None
        self.trim_play_btn.setEnabled(has and self.playback.trim_frames.get("agent") is not None)
        self.trim_slider.setEnabled(has)
        self.trim_reset_btn.setEnabled(bool(self.playback.trim_n_pending))
        if not has:
            self.trim_count.setText(tr("에피소드를 고르세요"))
            self.trim_apply_btn.setEnabled(False)
            self.trim_warn.setText("")
            for plot, _ in self.trim_plots.values():
                plot.set_cut(None)
            return
        path, demo = self.playback.trim_key
        n_trim, keep = self._trim_pending(), self._trim_keep()
        plan = plan_trim(path, [demo], max(n_trim, 1))[0]
        self.trim_summary.setText(
            tr("{d} · {n}프레임 ({s:.1f}s) · 마지막 그리퍼 동작 −{g}프레임").format(
                d=demo, n=self.playback.trim_n, s=self.playback.trim_n / 20.0,
                g=plan.gripper_tail if plan.gripper_tail is not None else "?"))
        self.trim_count.setText(
            tr("{a} → {b} 프레임   (−{n})").format(a=self.playback.trim_n, b=keep, n=n_trim)
            if n_trim else tr("{a} 프레임 — 자를 구간 없음").format(a=self.playback.trim_n))
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
        if self.playback.trim_key is None or not self._trim_pending():
            return
        path, demo = self.playback.trim_key
        n_trim, keep = self._trim_pending(), self._trim_keep()
        if QMessageBox.question(
                self, tr("끝 다듬기 확정"),
                tr("{f}\n{d}\n\n{a} → {b} 프레임 (뒤에서 {n}개 삭제)\n\n"
                   "되돌릴 수 없습니다. 진행할까요?").format(
                       f=Path(path).name, d=demo, a=self.playback.trim_n, b=keep, n=n_trim),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            new_n = trim_tail(path, demo, n_trim)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr("다듬기 실패"), f"{type(e).__name__}: {e}")
            self.log(f"[트림 실패] {Path(path).name} {demo}: {type(e).__name__}: {e}")
            return
        self.log(f"[트림] {Path(path).name} {demo}: {self.playback.trim_n} → {new_n}프레임 "
                 f"(−{n_trim})")
        self._refresh_dataset_tree()
        self._refresh_analysis(force=True)
        self._show_trim_for(path, demo)

    # ------------------------------------------------- layout check tab
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
        self.cameras.layout_refilter()

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
        self.playback.layout_playing = not self.playback.layout_playing
        self.layout_play_btn.setText(
            tr("일시정지") if self.playback.layout_playing else tr("재생"))
        if self.playback.layout_playing and \
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
                self.cameras.layout_ref.pop(role, None)
                continue
            self.cameras.layout_ref[role] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            self.layout_strip_views[f"{role}_ref"].set_frame(self.cameras.layout_ref[role])
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
        ref = self.cameras.layout_ref.get(role)
        if ref is None:
            return
        frame = self.cameras.last_cam_frame.get(role)
        if frame is None:
            self.layout_overlay_views[role].clear_frame(
                tr("카메라 없음 — Configure 에서 미리보기를 켜세요"))
            self.layout_strip_views[f"{role}_live"].clear_frame(tr("카메라 없음"))
            return
        p = self.cameras.crop_params[role]
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
            shown = draw_alignment_grid(shown)
        self.layout_overlay_views[role].set_frame(shown)

    # -------------------------------------------------------- point cloud
    def _depth_role_combo(self) -> QComboBox:
        return (self.depth_cam_combo if self.cameras.depth_consumer == "depth"
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
        if self.cameras.cloud_worker is not None:
            if serial == self.cameras.cloud_serial and self.cameras.cloud_worker.isRunning():
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
        w = DepthCloudWorker(role, serial, mode=self.cameras.depth_consumer or "cloud")
        w.cloud_ready.connect(self._on_cloud)
        w.depth_ready.connect(self._on_depth_img)
        w.error.connect(self._on_depth_error)
        w.start()
        self.cameras.cloud_worker = w
        self.cameras.cloud_serial = serial

    def _on_depth_error(self, m: str) -> None:
        text = tr("depth 오류: {m}").format(m=m)
        self.cloud_status.setText(text)
        self.depth_status.setText(text)

    def _on_cloud_cam_changed(self, *_args) -> None:
        if self.cameras.cloud_worker is None:      # 탭이 닫혀 있으면 다음 진입 때 반영
            return
        self._stop_cloud(restore_previews=False)  # 복원 약속(플래그)은 유지된다
        for v in self._depth_views():
            v.clear_frame(tr("카메라 전환 중..."))
        self._start_cloud()

    def _stop_cloud(self, restore_previews: bool = True) -> None:
        w = self.cameras.cloud_worker
        if w is None:
            return
        self.cameras.cloud_worker = None
        self.cameras.cloud_serial = ""
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
                and self.cameras.cloud_worker is None else None))

    @pyqtSlot(object, object)
    def _on_cloud(self, pts, rgb) -> None:
        self.cameras.cloud_pts, self.cameras.cloud_rgb = pts, rgb
        if self.cameras.depth_consumer == "cloud":     # 보이는 탭만 렌더
            self._render_cloud()
            self.cloud_status.setText(
                tr("점 {n:,}개 · 회전/기울임 슬라이더로 시점 변경").format(n=len(pts)))

    @pyqtSlot(object)
    def _on_depth_img(self, z) -> None:
        self.cameras.depth_img = z
        if self.cameras.depth_consumer == "depth":
            self._render_depth()

    def _depth_uv(self, pos) -> "tuple | None":
        """depth_view 위젯 좌표 -> depth 이미지 픽셀 좌표 (밖이면 None).

        VideoView 는 KeepAspectRatio + 중앙 정렬이라 스케일과 여백을
        되짚어야 한다.
        """
        z = self.cameras.depth_img
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
        z = self.cameras.depth_img
        if z is None:
            return
        import cv2

        zmax = self.depth_range_slider.value() / 100.0
        self.depth_range_label.setText(f"{zmax:.1f} m")
        frame = _depth_colormap(z, zmax)
        cursor_txt = ""
        if self.cameras.depth_cursor is not None:
            u, v = self.cameras.depth_cursor
            if not (0 <= u < z.shape[1] and 0 <= v < z.shape[0]):
                self.cameras.depth_cursor = None   # 프레임 크기가 바뀐 뒤 남은 커서
        if self.cameras.depth_cursor is not None:
            u, v = self.cameras.depth_cursor
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
        pts, rgb = self.cameras.cloud_pts, self.cameras.cloud_rgb
        if pts is None or len(pts) == 0:
            return
        yaw = np.deg2rad(self.cameras.cloud_yaw.value())
        pitch = np.deg2rad(self.cameras.cloud_pitch.value())
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
            self.cameras.depth_consumer = "cloud"
        elif idx == getattr(self, "_depth_tab_index", -1):
            self.cameras.depth_consumer = "depth"
        else:
            self.cameras.depth_consumer = None
        if self.cameras.depth_consumer is not None:
            self._start_cloud()     # 이미 같은 카메라로 돌고 있으면 유지
            if self.cameras.cloud_worker is not None:
                # 보이는 탭 것만 계산하도록 워커 모드 전환 (사용자 요구:
                # depth 계산도 그 탭에 들어갔을 때만)
                self.cameras.cloud_worker.mode = self.cameras.depth_consumer
        elif self.cameras.cloud_worker is not None:
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
            if self.playback.layout_playing:
                self._layout_timer.start()
            if self.layout_blink_check.isChecked():
                self._layout_blink_timer.start()
        else:
            self._layout_timer.stop()
            self._layout_blink_timer.stop()

    def _refresh_crop_labels(self) -> None:
        p = self.cameras.crop_params
        self.crop_agent_zoom_label.setText(
            tr("Agent 줌 {z:.2f}x").format(z=p["agent"]["zoom"]))
        self.crop_agent_x_label.setText(
            tr("Agent x {v:+d}px").format(v=p["agent"]["x"]))
        self.crop_agent_y_label.setText(
            tr("Agent y {v:+d}px").format(v=p["agent"]["y"]))
        self.crop_wrist_x_label.setText(
            tr("Wrist x {v:+d}px").format(v=p["wrist"]["x"]))

    def _crop_changed(self) -> None:
        p = self.cameras.crop_params
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

    # -------------------------------------------------------------- right
    # ------------------------------------------------------------- bottom
    # ------------------------------------------------------------- layout
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
        # 멈춘 치메라의 마지막 프레임을 "현재"로 계속 겹쳐 보이지 않게 한다.
        cams = self.cameras
        if cams.last_cam_frame:
            cams.last_cam_frame.clear()
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
        # 기록 중에는 worker 가 별내는 프레임이 이긴다 -- 화면에 보이는 것이
        # 실제로 파일에 쓰이는 그림이어야 하기 때문이다. 그 외 단계(게이트·
        # 리셋 대기)에서는 worker 가 치메라를 아예 안 읽으므로 여기가 유일한
        # 공급원이고, 노드 속도 그대로 나온다.
        if self._current_state == "recording":
            return
        cams = self.cameras
        self._update_live_view(role, frame, cams=cams)
        if self.center_tabs.currentIndex() == self._layout_tab_index:
            self._layout_update_role(role)
        cams.fps_count += 1

    def _update_live_view(self, role: str, frame, cams=None) -> None:
        """라이브 프레임 공용 경로 -- 원본 캐시 + 표시 (겹침 없음)."""
        if cams is None:
            cams = self.cameras
        cams.last_cam_frame[role] = frame      # 격자 없는 원본을 저장
        self.live_views[role].set_frame(self._with_grid(role, frame))

    def _set_live_maximized(self, role: "str | None") -> None:
        """좌우 배치는 유지하고 스플리터 비율만 바꾼다 -- 최대화한 쪽이
        ~88%, 반대쪽은 아주 작게. 겹침(PiP) 없음. 경계는 드래그로도 조절."""
        if role == self.cameras.live_maximized:
            return
        self.cameras.live_maximized = role
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
        corners = active_corners(self.cameras.grid_store)
        if not corners:
            return frame
        return draw_grid(frame, corners, self.grid_alpha_slider.value())

    def _on_grid_live_toggled(self, on: bool) -> None:
        self.cameras.grid_store["live_on"] = bool(on)
        save_grid_store(self.cameras.grid_store)
        if on and active_corners(self.cameras.grid_store) is None:
            self.log(tr("[격자] 저장된 격자가 없습니다 — '격자 편집...'에서 "
                        "만들어 저장하세요."))
        self._regrid_live()

    def _on_grid_alpha(self, val: int) -> None:
        # 드래그 중에는 화면만 갱신하고, 저장은 놓을 때 한 번(_on_grid_alpha_done).
        self.grid_alpha_label.setText(tr("{v}%").format(v=val))
        self.cameras.grid_store["alpha"] = int(val)
        self._regrid_live()

    def _on_grid_alpha_done(self) -> None:
        save_grid_store(self.cameras.grid_store)

    def _regrid_live(self) -> None:
        """마지막 프레임으로 agent 뷰를 다시 그린다 -- 멈춘 화면에서도
        체크박스/슬라이더가 즉시 반영되게."""
        frame = self.cameras.last_cam_frame.get("agent")
        if frame is not None:
            self.live_views["agent"].set_frame(self._with_grid("agent", frame))

    def _on_edit_grid(self) -> None:
        bg = self.cameras.last_cam_frame.get("agent")
        if bg is None:
            bg = self.cameras.layout_ref.get("agent")
        if bg is None:
            bg = np.full((480, 640, 3), 60, np.uint8)
            self.log(tr("[격자] 카메라 프레임이 없어 회색 배경에서 편집합니다 — "
                        "미리보기를 켜면 실제 화면 위에서 맞출 수 있습니다."))
        dlg = GridEditorDialog(self, bg, self.cameras.grid_store,
                               crop_params=dict(self.cameras.crop_params["agent"]),
                               save_callback=save_grid_store)
        dlg.exec()
        self.cameras.grid_store = load_grid_store()    # 저장 결과를 다시 정본에서
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
        if self.cameras.camera_node_user_stopped:
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
            crop_params={r: dict(v) for r, v in self.cameras.crop_params.items()},
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
        for b in (self.skip_btn, self.discard_btn, self.home_btn,
                  # 정렬 버튼은 세션 중이면 항상 열려 있다 -- 자세 오차가
                  # 커도 사람이 직접 요청하면 걸 수 있어야 한다 (2026-09-01).
                  self.match_btn):
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
        cams = self.cameras
        for role, rgb in (("agent", agent_rgb), ("wrist", wrist_rgb)):
            if rgb is None:
                continue
            self._update_live_view(role, rgb, cams=cams)
            if layout_on:
                self._layout_update_role(role)
        cams.fps_count += 1
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
            # 정렬 버튼은 자세와 무관하게 열려 있다 (2026-09-01) -- 아래
            # _set_running 이 세션 단위로 켜고 끈다. 잠기는 것은 '텔레옵
            # 시작' 쪽뿐이다.
            self._update_start_controls()
            if self._current_state == "gate":
                self.shortcut_hint.setText(
                    "Space: 텔레옵 시작   Enter: 자동 정렬 다시" if all_ok
                    else "Space: 텔레옵 시작   Enter: 자동 정렬 (오차 커도 가능)")

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
        if self.cameras.depth_consumer is not None:
            # 세션 동안 Depth/Point Cloud 탭에 머물러 있었다면 스트림을 다시
            # 올린다 (세션 중엔 안내만 보였다). 미리보기가 뜨는 시간을 준다.
            QTimer.singleShot(600, lambda: (
                self._start_cloud() if self.worker is None
                and self.cameras.depth_consumer is not None else None))

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
        cams = self.cameras
        cams.fps_value = cams.fps_count
        cams.fps_count = 0
        self.right_fields["fps"].setText(f"{cams.fps_value:.0f}")
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
            f"{cams.fps_value:.0f} fps   |   {count}   |   {self.root_edit.text()}")
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
        for proc, label in ((self.procs.repack_process, tr("재압축")),
                            (self.procs.convert_process, tr("LeRobot 변환")),
                            (self.procs.upload_process, tr("HDF5 업로드"))):
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
        return (self.procs.replay_process is not None and
                self.procs.replay_process.state() != QProcess.ProcessState.NotRunning)

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
        if self.procs.replay_process is not None and \
                self.procs.replay_process.state() != QProcess.ProcessState.NotRunning:
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
        self.procs.replay_process = proc
        self.log(f"[실로봇 재생] ▶ {Path(path).name} / {demo} ({speed:g}x)")
        proc.start()
        self._set_replay_ui(True)

    def _on_replay_stop(self) -> None:
        """재생 하위 프로세스를 끊는다. 로봇 노드의 레퍼런스 필터가 현재
        포즈를 유지하므로(Ctrl-C 와 동일) 팔이 낙하하지는 않는다."""
        proc = self.procs.replay_process
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
        self.procs.replay_process = None
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
        if self.playback.play_key == (path, demo):
            self.center_tabs.setCurrentIndex(1)
            return
        if self.active_file_path is not None and Path(path) == self.active_file_path:
            self.play_caption.setText(tr("수집 중인 파일은 재생할 수 없습니다."))
            return
        self._stop_playback()
        self.playback.play_key = (path, demo)
        self.play_caption.setText(tr("불러오는 중... {d}").format(d=demo))
        self.center_tabs.setCurrentIndex(1)
        if self.playback.play_loader is not None:
            self.playback.play_loader.wait()
        self.playback.play_loader = EpisodeLoadWorker(path, demo)
        self.playback.play_loader.loaded.connect(self._on_episode_loaded)
        self.playback.play_loader.failed.connect(
            lambda m: self.play_caption.setText(tr("재생 실패: {m}").format(m=m)))
        self.playback.play_loader.start()

    @pyqtSlot(str, str, object, object)
    def _on_episode_loaded(self, path, demo, agent, wrist) -> None:
        if self.playback.play_key != (path, demo):
            return
        self.playback.play_frames = {"agent": agent, "wrist": wrist}
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
        self.playback.play_timer.start()

    def _stop_playback(self) -> None:
        self.playback.play_timer.stop()
        self.playback.play_frames = {"agent": None, "wrist": None}
        self.playback.play_key = None
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
        self.playback.play_timer.setInterval(interval)

    def _on_speed_changed(self) -> None:
        self._apply_speed()
        self._refresh_play_caption()

    def _refresh_play_caption(self) -> None:
        if not self.playback.play_key:
            return
        path, demo = self.playback.play_key
        n = self.play_slider.maximum() + 1
        speed = self._speed()
        eff = PLAYBACK_FPS * speed
        self.play_caption.setText(
            f"{Path(path).name} · {demo} · {n} frames · "
            + (tr("{s:g}배속 ({f:g} fps)").format(s=speed, f=eff) if speed != 1
               else tr("{f:g} fps (실제 속도)").format(f=eff)))

    def _on_play_toggle(self) -> None:
        if self.playback.play_timer.isActive():
            self.playback.play_timer.stop()
            self.play_btn.setText(tr("재생"))
        else:
            self.playback.play_timer.start()
            self.play_btn.setText(tr("일시정지"))

    def _on_play_tick(self) -> None:
        n = self.play_slider.maximum() + 1
        if n > 1:
            self.play_slider.setValue((self.play_slider.value() + 1) % n)

    def _show_frame(self, i: int) -> None:
        for key, view in self.play_views.items():
            frames = self.playback.play_frames.get(key)
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
        if self.procs.pipeline_steps:
            QMessageBox.information(self, tr("이미 실행 중"),
                                    tr("{w}이(가) 이미 진행 중입니다. 로그를 확인하세요.")
                                    .format(w=what))
            return False
        return True

    def _start_pipeline(self, steps: list, tag: str) -> None:
        self.procs.pipeline_steps = steps
        self.procs.pipeline_results = []
        self.procs.pipeline_t0 = time.monotonic()
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
        self._warn_ignored_legacy(plan)
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
        if self.procs.pipeline_steps:
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
        dlg = PipelineDialog(
            self, data_root, plan, repo,
            self.repo_id_for("hdf5_repo_id"),
            self._recents.most_recent(
                "lerobot_root", str(Path.home() / "lerobot_upload")),
            scripts={"repack": REPACK_SCRIPT,
                     "convert": CONVERT_SCRIPT,
                     "upload": UPLOAD_SCRIPT})
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
        self.procs.pipeline_steps = steps
        self.procs.pipeline_results = []
        self.procs.pipeline_t0 = time.monotonic()
        self.log(f"[전체 처리] {len(steps)}단계 시작 — "
                 + " → ".join(s["name"] for s in steps), "upload")
        for st in steps:
            if st.get("detail"):
                self.log(f"  · [{st['name']}] {st['detail']}", "upload")
        self._run_next_pipeline_step()

    def _run_next_pipeline_step(self) -> None:
        if not self.procs.pipeline_steps:
            self._finish_pipeline(True)
            return
        step = self.procs.pipeline_steps[0]
        if "program" not in step:
            # 정보용 단계 (예: 'HDF5 원본 업로드 — 생략') -- 프로세스 없이
            # 사유만 로그·요약에 남기고 넘어간다. 자동 선택이 파일을 하나도
            # 안 고른 날, '왜 안 올라갔는지'가 보이게 하는 장치 (2026-08-25).
            self.procs.pipeline_steps.pop(0)
            self.procs.pipeline_results.append((step["name"], 0, 0.0))
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
        self.procs.pipeline_proc = proc
        self.procs.pipeline_step_t0 = time.monotonic()
        self.log(f"\n[전체 처리] ▶ {step['name']} 시작", "upload")
        self.statusBar().showMessage(tr("전체 처리: {n}").format(n=step["name"]))
        proc.start()

    def _on_pipeline_step_finished(self, code: int, _status) -> None:
        step = self.procs.pipeline_steps.pop(0)
        dt = time.monotonic() - self.procs.pipeline_step_t0
        self.procs.pipeline_results.append((step["name"], code, dt))
        self.log(f"[전체 처리] {'✔' if code == 0 else '✖'} {step['name']} "
                 f"종료 (exit={code}, {dt / 60:.1f}분)", "upload")
        self.procs.pipeline_proc = None
        if code != 0:
            # 뒤 단계가 앞 결과에 의존하므로(변환 -> 업로드) 잘못된 것을 올리지
            # 않는다. 아침에 로그만 보면 어디서 멈췄는지 알 수 있게 남긴다.
            self._finish_pipeline(False)
            return
        self._run_next_pipeline_step()

    def _finish_pipeline(self, ok: bool) -> None:
        remaining = [s["name"] for s in self.procs.pipeline_steps]
        self.procs.pipeline_steps = []
        total = time.monotonic() - self.procs.pipeline_t0
        lines = ["", "=" * 56,
                 tr("전체 처리 요약 — {r} (총 {m:.1f}분)").format(
                     r=tr("완료") if ok else tr("중단됨"), m=total / 60)]
        for name, code, dt in self.procs.pipeline_results:
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
        p = self.procs.reset_protection_process
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
        self.procs.reset_protection_process = proc
        self.log("=== 리더암 서보 보호 해제 ===")
        proc.start()

    def _run_runme(self) -> None:
        # runme.sh 는 pkexec 로 관리자 비밀번호 창을 띄운다. 사람이 없는
        # 자리(인수 테스트, 밤샘 리팩토링 러너)에서 그 창이 뜨면 답할 사람이
        # 없어 그대로 멈춘다 -- 실제로 2026-09-01 에 테스트 실행 중 창이 떴다.
        # 시작 시 자동 실행이라 눌러야만 뜨는 것도 아니고, 튜닝이 어긋나
        # 있을 때만이라 기계 상태에 따라 떴다 안 떴다 한다.
        if os.environ.get("GELLO_NO_PRIVILEGED"):
            self.log("[튜닝] GELLO_NO_PRIVILEGED -- 관리자 권한 작업을 건너뜁니다. "
                     "필요하면 사람이 scripts/runme.sh 를 직접 실행하세요.")
            return
        if self.procs.runme_process is not None and \
                self.procs.runme_process.state() != QProcess.ProcessState.NotRunning:
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
        self.procs.runme_process = proc
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
        self.procs.runme_process = None

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
        self.cameras.camera_node_user_stopped = False
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
        self.cameras.camera_node_user_stopped = True
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
        if self.cameras.camera_node_user_stopped:
            return
        specs = self.cameras.camera_node_specs()
        key = ",".join(specs)
        running = (self.procs.camera_node_process is not None and
                   self.procs.camera_node_process.state()
                   != QProcess.ProcessState.NotRunning)
        if running and not restart and key == self.cameras.camera_node_spec:
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
        self.procs.camera_node_process = proc
        self.cameras.camera_node_spec = key
        self.log(f"[카메라노드] 시작: {key}")
        proc.start()

    def _on_camera_node_output(self) -> None:
        if self.procs.camera_node_process is None:
            return
        for line in self._proc_text(self.procs.camera_node_process).splitlines():
            if line.strip():
                self.log(f"[카메라노드] {line.rstrip()}")

    def _on_camera_node_finished(self, code: int, _status) -> None:
        proc = self.sender()
        if proc is not self.procs.camera_node_process:
            # _stop_camera_node() 나 _ensure(재시작) 가 이미 손을 뗀 프로세스
            # -- 의도된 종료라 조용히 보낸다.
            self.log(f"[카메라노드] 종료 (exit={code})")
            return
        # 비정상 종료 -- 자동 재시작한다. 단 crash-loop(예: 포트 충돌로
        # 뜨자마자 죽는 상태)이면 로그만 가득 채우므로 60초 내 3회를 넘으면
        # 멈추고 수동(카메라 메뉴)으로 넘긴다.
        self.procs.camera_node_process = None
        self.cameras.camera_node_spec = ""
        now = time.monotonic()
        self.cameras.camera_node_crashes = [
            t for t in self.cameras.camera_node_crashes if now - t < 60.0] + [now]
        if len(self.cameras.camera_node_crashes) > 3:
            self.log(f"[카메라노드] 비정상 종료 (exit={code}) — 60초 내 "
                     f"{len(self.cameras.camera_node_crashes)}회째, 자동 재시작을 "
                     "멈춥니다. Camera 메뉴 > 카메라 노드 재시작으로 수동 "
                     "시작하세요.")
            return
        self.log(f"[카메라노드] 비정상 종료 (exit={code}) — 2초 후 자동 재시작")
        QTimer.singleShot(2000, self._ensure_camera_node)

    def _stop_camera_node(self) -> None:
        proc = self.procs.camera_node_process
        self.procs.camera_node_process = None
        self.cameras.camera_node_spec = ""
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        proc.terminate()
        if not proc.waitForFinished(3000):
            proc.kill()
            proc.waitForFinished(2000)

    def _on_start_node(self) -> None:
        if self.procs.node_process is not None and \
                self.procs.node_process.state() != QProcess.ProcessState.NotRunning:
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
        self.procs.node_process = proc
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
        if self.procs.node_process is None:
            return
        data = self._proc_text(self.procs.node_process)
        for line in data.splitlines():
            if line.strip():
                self.log(f"[노드] {line}")

    def _on_stop_node(self) -> None:
        if self.procs.node_process is None or \
                self.procs.node_process.state() == QProcess.ProcessState.NotRunning:
            return
        self.procs.node_process.terminate()
        if not self.procs.node_process.waitForFinished(3000):
            self.procs.node_process.kill()
            self.procs.node_process.waitForFinished(2000)
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
        self.procs.repack_process = proc
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
        cams = self.cameras
        state = cams.stream_states.setdefault(prefix, {})
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
        self.procs.convert_process = proc
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
        self.procs.upload_process = proc
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
                        None if self.cameras.live_maximized == r else r)
                    return True
        if obj is getattr(self, "depth_view", None):
            # Depth 뷰 위에서 마우스가 가리키는 지점의 실거리 표시.
            # 마우스 이벤트만 소비하고 나머지(키 입력 등)는 아래 공용 단축키
            # 처리로 흘려보낸다 -- 무조건 return 하면 이 뷰에 포커스가 있는
            # 동안 Space/Esc 단축키가 죽는다.
            if (event.type() == QEvent.Type.MouseMove
                    and self.cameras.depth_img is not None):
                self.cameras.depth_cursor = self._depth_uv(event.position())
                self._render_depth()
                return False
            if event.type() == QEvent.Type.Leave \
                    and self.cameras.depth_cursor is not None:
                self.cameras.depth_cursor = None
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
                    # 자동 정렬 재시도. 오차 조건은 없다 -- wall 이 관절별로
                    # 보호한다 (2026-09-01).
                    self._cmd("cmd_auto_match_pose")
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.playback.play_timer.stop()
        if self.playback.play_loader is not None:
            self.playback.play_loader.wait(3000)
        self._stop_cloud(restore_previews=False)
        self._stop_previews_blocking()
        self._stop_camera_node()
        if self.worker is not None and self.worker.isRunning():
            self.worker.cmd_quit()
            self.worker.wait(5000)
        self._on_stop_node()
        for proc in (self.procs.repack_process, self.procs.convert_process,
                     self.procs.upload_process, self.procs.runme_process,
                     self.procs.pipeline_proc):
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
