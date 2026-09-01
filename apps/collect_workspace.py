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
from PyQt6.QtCore import (QEvent, QProcess, Qt, QTimer,
                          pyqtSlot)
from PyQt6.QtGui import QIcon, QTextCursor
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTreeWidgetItem,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.data.dataset_schema import (  # noqa: E402
    OBS_AGENTVIEW_RGB,
    load_schema_config,
    save_schema_config,
)
from gello.gui.dialogs import DatasetSchemaDialog, hf_account  # noqa: E402
from gello.gui.gui_widgets import (  # noqa: E402
    PLAYBACK_FPS,
    Recents,
)
from gello.gui.workers import CameraPreviewWorker, GalleryLoadWorker  # noqa: E402
from gello.gui.text_utils import clean_stream_lines, is_progress_line, repo_id_error  # noqa: E402
from apps.workspace.constants import LOG_DIR, LAYOUT_DIR, LAYOUT_ZIP  # noqa: E402
from apps.workspace.domains import CameraOps, CollectionOps, DatasetOps, DepthOps, PlaybackOps, SceneOps, StatsOps, UploadOps  # noqa: E402
from apps.workspace.models import (  # noqa: E402
    CameraState,
    PlaybackState,
    ProcessRegistry,
    SessionState,
    _new_stats,
)
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
from apps.dialogs.grid_editor_dialog import GridEditorDialog  # noqa: E402
from apps.dialogs.hdf5_tree_dialog import Hdf5TreeDialog  # noqa: E402
from gello.gui.grid_overlay import (  # noqa: E402
    draw_alignment_grid,
    load_grid_store,
    save_grid_store,
)
from gello.gui.i18n import tr  # noqa: E402
from gello.data.libero_format import (  # noqa: E402
    default_crop_params,
    hdf5_repack_status,
    resize_rgb,
    save_crop_params,
)
from gello.gui.libero_gui_worker import CollectionWorker  # noqa: E402
from gello.scene.scene_rules import check  # noqa: E402
from gello.scene.scene_format import (  # noqa: E402
    count_by_slot,
    iter_scene_files,
    scene_filename,
)
from gello.core.station import load_station  # noqa: E402

# 로봇 IP, ZMQ 주소, 카메라 스트림 포맷, 크롭 초기값은 전부 여기서 온다.
# GELLO_STATION 으로 고르고, 파일은 configs/stations/<이름>.yaml.
STATION = load_station()
PYLIBFRANKA_PYTHON = STATION.node.python_path
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "launch" / "launch_nodes.py")
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
        self.session = SessionState()
        self._summary: dict = {}
        self._progress_line: dict = {}

        self.agent_preview: CameraPreviewWorker | None = None
        self.wrist_preview: CameraPreviewWorker | None = None
        self._cloud_previews_were_on = False

        # 세션 소유 scene 삭제 후 썸네일 무효화 대기 건수. bool 이 아니라 카운터 --
        # saver 는 삭제 1건마다 episode_list_changed 를 emit 하므로, 첫 emit 에서
        # 플래그를 소진하면 나머지 삭제(추가 renumber/uid 재배정)가 무효화를
        # 비껴간다.
        self._pending_scene_deletes = 0
        self._dying_previews: list = []
        # 확정 전까지의 트림 상태. 누른 만큼 오르내리는 정수 하나면 충분하다 --
        # +/- 가 양쪽으로 있으므로 되돌리기용 이력을 따로 들 이유가 없다.
        self._connect_wait_since = None
        self._recents = Recents()
        self._log_file = None
        if log_path is not None:
            self._log_file = open(log_path, "a", buffering=1)  # noqa: SIM115

        self.schema = load_schema_config()
        # 상태 라벨 상수를 인스턴스로 노출 -- CollectionOps 가 self.win 으로 읽는다.
        self.STATE_LABELS = STATE_LABELS
        self.SHORTCUT_HINTS = SHORTCUT_HINTS

        self.upload = UploadOps(self)
        self.playback_ops = PlaybackOps(self)
        self.scene_ops = SceneOps(self)
        self.camera_ops = CameraOps(self)
        self.depth_ops = DepthOps(self)
        self.dataset_ops = DatasetOps(self)
        self.stats_ops = StatsOps(self)
        self.collection = CollectionOps(self)

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
        self.cameras.fps_timer.timeout.connect(self.camera_ops.tick_fps)
        self.cameras.fps_timer.start(1000)

        self.playback.play_timer = QTimer(self)
        self.playback.play_timer.setInterval(int(1000 / PLAYBACK_FPS))
        self.playback.play_timer.timeout.connect(self.playback_ops.on_play_tick)

        # App-wide, not window-scoped: the operator's hands are on the leader,
        # so whichever widget happens to hold focus must not swallow the keys.
        QApplication.instance().installEventFilter(self)

        self._set_activity("configure")
        self.collection.set_running(False)
        self.camera_ops.refresh_cameras()
        self.dataset_ops.refresh_dataset_tree()
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
            for p in iter_scene_files(self.dataset_ops.dataset_root()):
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
        if self.session.active_file_path is not None and Path(path) == self.session.active_file_path:
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
            self.playback_ops.play_episode(d[0], d[1])


    # ------------------------------------------------------------- center
    # --------------------------------------------------------------- left
    # ------------------------------------------------------- scene 수집 UI




    # -------------------------------------------------- slot ID 자동 배정











    # -------------------------------------------------- 수집 계획 (slot plan)











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


    def _on_center_tab_changed(self, idx: int) -> None:
        """레이아웃 탭이 보이는 동안만 하단 로그를 접고 슬라이드쇼를 돌린다."""
        if idx == getattr(self, "_cloud_tab_index", -1):
            self.cameras.depth_consumer = "cloud"
        elif idx == getattr(self, "_depth_tab_index", -1):
            self.cameras.depth_consumer = "depth"
        else:
            self.cameras.depth_consumer = None
        if self.cameras.depth_consumer is not None:
            self.depth_ops.start_cloud()     # 이미 같은 카메라로 돌고 있으면 유지
            if self.cameras.cloud_worker is not None:
                # 보이는 탭 것만 계산하도록 워커 모드 전환 (사용자 요구:
                # depth 계산도 그 탭에 들어갔을 때만)
                self.cameras.cloud_worker.mode = self.cameras.depth_consumer
        elif self.cameras.cloud_worker is not None:
            self.depth_ops.stop_cloud()
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
            self.stats_ops.refresh_stats()
            self._refresh_plan_progress()
            if not self.session.stats:
                self.stats_ops.refresh_analysis()
        elif key == "dataset":
            self.dataset_ops.refresh_dataset_tree()
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

    def _refresh_verdict_label(self) -> None:
        if self.session.last_saved_name is None:
            self.verdict_label.setText(
                tr("판정 뒤집기 예약됨 (Esc로 취소)") if self.session.pending_verdict_toggle else "")
            self.verdict_label.setStyleSheet("color:#f39c12;")
            return
        ok = self.session.last_saved_success
        self.verdict_label.setText(
            tr("직전 {n}: {v}   —   Esc로 뒤집기").format(
                n=self.session.last_saved_name, v=tr("성공") if ok else tr("실패")))
        self.verdict_label.setStyleSheet(
            "color:#2ecc71; font-weight:bold;" if ok else "color:#e74c3c; font-weight:bold;")

    # legacy '기존 task 이어찍기' 드롭다운(_refresh_resume_combo /
    # _on_resume_selected / _show_resume_info)은 legacy 수집 UI 제거와 함께
    # 삭제됐다 (2026-08-13). scene 이어찍기는 Scene 콤보가 담당한다.


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
    def _on_square_guide(self, on: bool) -> None:
        for v in list(self.live_views.values()) + list(self.play_views.values()) \
                + list(getattr(self, "trim_views", {}).values()):
            v.set_square_guide(on)

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

    def _on_grid_alpha(self, val: int) -> None:
        # 드래그 중에는 화면만 갱신하고, 저장은 놓을 때 한 번(_on_grid_alpha_done).
        self.grid_alpha_label.setText(tr("{v}%").format(v=val))
        self.cameras.grid_store["alpha"] = int(val)
        self.camera_ops.regrid_live()

    def _on_grid_alpha_done(self) -> None:
        save_grid_store(self.cameras.grid_store)

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
        self.camera_ops.regrid_live()

    def _connect_worker(self, w: CollectionWorker) -> None:
        """Connect worker signals and start it. Stays on the window so the
        worker lifecycle remains the window's responsibility."""
        w.state_changed.connect(self.collection.on_state)
        w.frames_ready.connect(self.camera_ops.on_frames)
        w.gate_status.connect(self.collection.on_gate)
        w.pose_match_status.connect(self.collection.on_pose_match)
        w.episode_progress.connect(self.collection.on_progress)
        w.episode_saved.connect(self.collection.on_saved)
        w.episode_discarded.connect(self._on_discarded)
        w.reset_countdown.connect(self.collection.on_countdown)
        w.log_message.connect(self.log)
        w.node_status.connect(self._on_node_status)
        w.fatal_error.connect(self.collection.on_fatal)
        w.connected.connect(self._on_connected)
        w.episode_list_changed.connect(self.playback_ops.on_episode_list)
        w.session_summary.connect(self.stats_ops.on_summary)
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
        w.saver.episode_saved.connect(self.collection.on_saved)
        w.saver.episode_list_changed.connect(self.playback_ops.on_episode_list)
        w.saver.log_message.connect(self.log)
        w.saver.save_status.connect(self.collection.on_save_status)
        self.worker = w

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
            self.camera_ops.restart_previews()
        # 이번 task 카운터는 여기서 0 으로 돌아간다(누적은 그대로). 연습 모드도
        # 마찬가지다 -- NullTaskWriter 도 저장을 받아 넘기므로 카운터는 움직인다.
        self.session.counters = _new_stats()
        if self.session.no_dataset_session:
            # NullTaskWriter has no real path; claiming one here would make the
            # dataset tree think a file is locked by this session.
            self._update_dataset_panel()
            self.log("[연결] 연습 모드로 연결되었습니다.")
            return
        self.session.active_file_path = Path(path)
        self.session.episodes_at_connect = int(n_episodes)
        # 직전 세션에서 삭제가 실패해 남았을 수 있는 대기 건수를 청산 --
        # 새 세션의 첫 목록 갱신이 엉뚱한 무효화를 하지 않게.
        self._pending_scene_deletes = 0
        if self.session.scene_session:
            # scene 파일이 실제로 만들어졌으니 보관해 둔 새 scene 구성은 소진.
            self._pending_scene_meta = None
            self.scene_ops.refresh_slot_panel()
        self._update_dataset_panel()
        self.log(f"[연결] 파일: {path} (기존 {n_episodes}개 에피소드)")
        self.dataset_ops.refresh_dataset_tree()

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
        self.session.no_dataset_session = False
        self.session.active_file_path = None
        self.session.active_episode_cache = None
        was_scene = self.session.scene_session
        self.session.scene_session = False
        self.scene_ops.set_right_scene(None)
        self.collection.set_running(False)
        self.dataset_ops.refresh_dataset_tree()
        if was_scene:
            # 세션이 만든/키운 scene 파일이 목록·slot 현황에 반영되게.
            self.scene_ops.refresh_scene_combo()
        self.camera_ops.restart_previews()
        if self.cameras.depth_consumer is not None:
            # 세션 동안 Depth/Point Cloud 탭에 머물러 있었다면 스트림을 다시
            # 올린다 (세션 중엔 안내만 보였다). 미리보기가 뜨는 시간을 준다.
            QTimer.singleShot(600, lambda: (
                self.depth_ops.start_cloud() if self.worker is None
                and self.cameras.depth_consumer is not None else None))

    def _current_task_label(self, limit: int = 0) -> str:
        """수집 중인 task 이름. 연결 전이거나 연습 모드면 빈 문자열.

        ``limit`` 을 주면 그 길이로 줄이되 **뒤쪽**을 자른다. LIBERO task 이름은
        ``put_the_black_bowl_on_the_plate...`` 처럼 길고 앞부분이 서로 다르므로,
        Qt 가 오른쪽 정렬 라벨에서 하듯 앞을 잘라내면 어느 task 인지 알 수 없다.
        """
        if self.worker is None or self.session.no_dataset_session:
            return ""
        name = getattr(self.worker.cfg, "task_name", "") or ""
        if limit and len(name) > limit:
            return name[: limit - 1] + "…"
        return name

    def _refresh_plan_progress(self) -> None:
        """Statistics 의 계획 진행률 표 -- 계획 × 실제 scene 파일 대조."""
        tree = getattr(self, "plan_progress_tree", None)
        if tree is None:
            return
        tree.clear()
        plan = self.scene_ops.current_plan()
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
            if self.session.scene_session and sp.scene_id == self.scene_ops.session_scene_id():
                counts = self.scene_ops.session_slot_counts()
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
            name = (tr("(기록 안 함)") if self.session.no_dataset_session
                    else Path(str(self.session.active_file_path or "-")).name)
            f["ds_file"].setText(soft_wrap(name))
            f["ds_file"].setToolTip(name)
            # 연결 시점 설정이 아니라 '지금' slot 을 보여준다 -- scene 세션은
            # Disconnect 없이 slot(문장·ID)을 바꾸므로(cmd_set_slot) 설정값만
            # 보여주면 전환 뒤에도 첫 문장이 그대로 남는다 (실사용 보고).
            cur_instr = getattr(self.worker, "_slot_instruction", None) \
                or cfg.language_instruction or cfg.task_name
            cur_iid = getattr(self.worker, "_slot_instruction_id", "") or cfg.instruction_id
            task_text = f"{cur_iid}: {cur_instr}" if (self.session.scene_session and cur_iid) \
                else cur_instr
            f["ds_task"].setText(task_text)
            f["ds_task"].setToolTip(task_text)
            # 저장은 백그라운드라 episode_list_changed가 몇 초 늦게 온다. 그걸
            # 기다리면 방금 저장한 것이 한동안 안 세어져 "지금 몇 개째인지"를
            # 알 수 없다. 연결 시점 개수 + 이번 세션 저장 수로 즉시 계산하고,
            # 목록이 도착하면 그 값이 더 정확하므로 그쪽을 쓴다.
            listed = len(self.session.active_episode_cache or [])
            counted = self.session.episodes_at_connect + self.session.counters["saved"]
            total = max(listed, counted)
            f["ds_episodes"].setText(
                tr("{t}개  (이번 +{s})").format(t=total, s=self.session.counters["saved"]))
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




    def _on_hdf5_tree(self) -> None:
        path = self.dataset_ops.selected_file()
        if path is None:
            QMessageBox.information(self, tr("선택 필요"),
                                    tr("트리로 볼 파일을 먼저 선택하세요."))
            return
        if self.session.active_file_path is not None and path == self.session.active_file_path:
            QMessageBox.information(self, tr("파일 사용 중"), tr(
                "수집 세션이 이 파일을 쥐고 있습니다 — 세션 종료 후 여세요."))
            return
        Hdf5TreeDialog(self, path).exec()

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

    @staticmethod



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
                    self.camera_ops.set_live_maximized(
                        None if self.cameras.live_maximized == r else r)
                    return True
        if obj is getattr(self, "depth_view", None):
            # Depth 뷰 위에서 마우스가 가리키는 지점의 실거리 표시.
            # 마우스 이벤트만 소비하고 나머지(키 입력 등)는 아래 공용 단축키
            # 처리로 흘려보낸다 -- 무조건 return 하면 이 뷰에 포커스가 있는
            # 동안 Space/Esc 단축키가 죽는다.
            if (event.type() == QEvent.Type.MouseMove
                    and self.cameras.depth_img is not None):
                self.cameras.depth_cursor = self.depth_ops.depth_uv(event.position())
                self.depth_ops.render_depth()
                return False
            if event.type() == QEvent.Type.Leave \
                    and self.cameras.depth_cursor is not None:
                self.cameras.depth_cursor = None
                self.depth_ops.render_depth()
                return False
        if (
            event.type() == QEvent.Type.KeyPress
            and self.worker is not None
            and QApplication.activeModalWidget() is None
        ):
            key = event.key()
            state = self.session.current_state
            if key == Qt.Key.Key_Space:
                if state == "gate":
                    # 버튼과 같은 조건: 자세가 맞아야 시작 (워커도 거부하지만
                    # 이유를 먼저 보여준다).
                    if self.session.gate_ok:
                        self.collection.cmd("cmd_start_teleop")
                    else:
                        self.log("[GATE] 아직 자세가 맞지 않아 시작할 수 없습니다 "
                                 "-- 리더를 팔로워 자세에 맞추세요.")
                    return True
                if state == "recording" and not self.session.no_dataset_session:
                    self.collection.save(True)
                    return True
            elif key == Qt.Key.Key_Escape:
                # 기록 중이면 '실패로 끝내기', 리셋 대기 중이면 방금 것의 판정
                # 번복. 버튼을 누르는 행위 자체가 "이 에피소드는 끝났다"는
                # 판단이므로, 두 키 모두 에피소드를 끝낸다. 성공이었는지는
                # 팔이 홈으로 가는 동안 다시 보고 정하는 게 자연스럽다.
                if state == "recording" and not self.session.no_dataset_session:
                    self.collection.save(False)
                    return True
                if state in ("reset_wait", "homing") and not self.session.no_dataset_session:
                    self.collection.toggle_last_verdict()
                    return True
            elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if state == "recording":
                    self.collection.cmd("cmd_discard_episode")
                    return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if state == "reset_wait":
                    self.collection.cmd("cmd_skip_reset_wait")
                    return True
                if state == "gate":
                    # 자동 정렬 재시도. 오차 조건은 없다 -- wall 이 관절별로
                    # 보호한다 (2026-09-01).
                    self.collection.cmd("cmd_auto_match_pose")
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.playback.play_timer.stop()
        if self.playback.play_loader is not None:
            self.playback.play_loader.wait(3000)
        self.depth_ops.stop_cloud(restore_previews=False)
        self.camera_ops.stop_previews_blocking()
        self.camera_ops.stop_camera_node()
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
