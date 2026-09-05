"""Workspace-style GUI for collecting LIBERO-format demos via GELLO teleop.

Run inside lerobot-venv::

    (pylibfranka-venv) python scripts/launch/launch_nodes.py --robot fr3   # terminal 1
    (lerobot-venv)     python apps/collect_launcher.py                     # terminal 2

데스크톱 아이콘은 collect_launcher.py 를 실행한다 -- 런처 마법사(이어서
하기/새 데이터세트 + 하드웨어 선택)가 먼저 뜨고, 그 결과가 recents/env 에
반영된 뒤 이 워크스페이스가 열린다. 이 파일을 직접 실행하면 마법사 없이
바로 열린다 (직전 recents 값 사용).

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

Widgets and dialogs come from gello/gui/{widgets,dialogs,workers}.py and
apps/workspace/shared/, split out so the collector and the old wizard could
share them without one importing the other's window.
"""

from __future__ import annotations

import os

# Must run before numpy/cv2/h5py are imported. The GUI caps the BLAS/OpenCV
# thread pools at 1 so camera preview threads do not fight the control loop.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import sys
import traceback
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QEvent, QProcess, Qt, QTimer
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.data.dataset_schema import (  # noqa: E402
    SCHEMA_VERSION,
    load_schema_config,
    save_schema_config,
)
from gello.scene.dataset_meta import load_identity  # noqa: E402
from gello.gui.dialogs import DatasetSchemaDialog, hf_account  # noqa: E402
from gello.gui.constants import PLAYBACK_FPS  # noqa: E402
from gello.gui.fonts import ensure_font
from gello.gui.wheel_guard import install_wheel_guard  # noqa: E402
from gello.gui.widgets import Recents  # noqa: E402
from gello.gui.workers import CameraPreviewWorker  # noqa: E402
from gello.gui.text_utils import clean_stream_lines, is_progress_line, repo_id_error  # noqa: E402
from apps.workspace.constants import LOG_DIR  # noqa: E402
from apps.workspace.features.camera import CameraOps, DepthOps  # noqa: E402
from apps.workspace.features.collection import CollectionOps  # noqa: E402
from apps.workspace.features.dataset import DatasetOps  # noqa: E402
from apps.workspace.features.gallery import GalleryOps  # noqa: E402
from apps.workspace.features.playback import PlaybackOps  # noqa: E402
from apps.workspace.features.scene import LayoutRefOps, SceneOps, ScenePlanningOps  # noqa: E402
from apps.workspace.features.stats import StatsOps  # noqa: E402
from apps.workspace.features.system import SystemOps  # noqa: E402
from apps.workspace.features.upload import UploadOps  # noqa: E402
from apps.workspace.models import (  # noqa: E402
    CameraState,
    PlaybackState,
    ProcessRegistry,
    SessionState,
)
from apps.workspace.shared.camera_node_proc import adopt_node  # noqa: E402
from apps.workspace.shared.robot_node_proc import (  # noqa: E402
    adopt_node as adopt_robot_node,
)
from apps.workspace.shell import (  # noqa: E402
    build_bottom,
    build_center,
    build_layout,
    build_left,
    build_menu,
    build_right,
    build_statusbar,
    build_toolbar,
)
from apps.workspace.features.scene.dialogs.grid_editor_dialog import GridEditorDialog  # noqa: E402
from apps.workspace.features.dataset.hdf5_tree_dialog import Hdf5TreeDialog  # noqa: E402
from gello.gui.grid_overlay import (  # noqa: E402
    load_grid_store,
    save_grid_store,
)
from gello.gui.i18n import tr  # noqa: E402
from gello.data.crop import (  # noqa: E402
    default_crop_params,
    save_crop_params,
)
from gello.collect.worker import CollectionWorker  # noqa: E402
from gello.config.station import load_station  # noqa: E402

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

# Panels named in the UI spec that this build does not implement yet. They are
# shown, disabled and greyed, rather than omitted: a missing tab reads as "this
# tool cannot do that", while a greyed one says "not built yet" -- and leaving
# the shape visible is what makes the gap reviewable instead of forgotten.

# 큐레이션 기준값은 전부 gello/episode_stats.py 에 있다 (TASK_DEV_LIMIT /
# STILL_VEL). 여기서 다시 정의하지 않는 이유는, 화면에 찍히는 수와
# 판정에 쓰이는 수가 갈라지면 조작자가 둘 중 뭘 믿어야 할지 알 수 없기 때문이다.


# The worker's state names, and what the operator can do from each. Both the
# 진행 label and the shortcut hint read from these, so the hint can never drift
# out of sync with what eventFilter() actually accepts.
# Status 는 영어다 (i18n.py 의 언어 계층). 0.5초 곁눈질로 읽는 것이라
# 글이 아니라 기호로 인식되고, 외국인 연구원도 화면만 보고 상태를 안다.
# **무엇을 해야 하는지**는 아래 SHORTCUT_HINTS 가 한국어로 말한다 -- 그쪽은
# 읽고 이해하는 안내라서 대부분의 사용자인 한국인의 모국어로 둔다.
STATE_LABELS = {
    "connecting": "CONNECTING",
    "idle": "IDLE",
    "homing": "HOMING",
    "reset_wait": "RESET WAIT",
    "gate": "GATE",
    "approach": "APPROACH",
    "recording": "RECORDING",
}
SHORTCUT_HINTS = {
    "reset_wait": "물체를 제자리에 놓으세요.   Enter: 계속   Esc: 직전 판정 뒤집기",
    "gate": "리더를 팔로워 자세에 맞추세요.   Space: 텔레옵 시작   Enter: 자동 정렬",
    "recording": "Space: 성공으로 끝내기   Esc: 실패로 끝내기   Del: 폐기",
}




class WorkspaceWindow(QMainWindow):
    def __init__(self, log_path: Path | None,
                 camera_node=None, camera_node_spec: str = "",
                 robot_node=None,
                 schema_version: str = SCHEMA_VERSION) -> None:
        super().__init__()
        self.setWindowTitle(tr("FR3 GELLO 데이터 수집 워크스페이스"))
        self.resize(1780, 1020)

        # 이 세션이 기록할 스키마 버전. 런처가 데이터셋을 보고 정해 주고
        # 여기서는 고정으로 쓴다 -- 새 scene 파일에 그대로 찍힌다. 기록기가
        # 내용을 보고 추측하면 토크를 못 주는 장비에서 어긋난다 (2026-09-05).
        self.schema_version = schema_version
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
        self.scene_planning = ScenePlanningOps(self)
        self.layout_ref = LayoutRefOps(self)
        self.camera_ops = CameraOps(self)
        self.depth_ops = DepthOps(self)
        self.dataset_ops = DatasetOps(self)
        self.gallery_ops = GalleryOps(self)
        self.stats_ops = StatsOps(self)
        self.system = SystemOps(self)
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

        # 마법사가 미리보기용으로 이미 띄운 노드가 있으면 그것을 이어서 쓴다.
        # refresh_cameras() 가 ensure_camera_node() 를 부르는데, 여기서 먼저
        # 등록해 두면 같은 구성이라 그냥 넘어간다 -- 안 그러면 카메라를 두 번
        # 열려다 포트 6021 충돌로 죽는다.
        if camera_node is not None:
            adopt_node(camera_node, self)
            camera_node.readyReadStandardOutput.connect(
                self.camera_ops.on_camera_node_output)
            camera_node.finished.connect(self.camera_ops.on_camera_node_finished)
            self.procs.camera_node_process = camera_node
            self.cameras.camera_node_spec = camera_node_spec
            self.log(f"[카메라노드] 마법사에서 이어받음: {camera_node_spec}")

        # 마법사의 데이터세트 버전 [확인] 이 로봇 노드를 띄웠으면 그것도
        # 이어받는다. FCI 는 클라이언트 하나만 받으므로, 안 이어받으면
        # Process 메뉴의 '노드 시작' 이 조용히 실패한다.
        if robot_node is not None:
            adopt_robot_node(robot_node, self)
            robot_node.readyReadStandardOutput.connect(self.system.on_node_output)
            robot_node.finished.connect(self.system.on_node_finished)
            self.procs.node_process = robot_node
            self.lights["node"].set("busy", tr("시작 중"))
            self.log("[노드] 마법사에서 이어받음")

        self._set_activity("configure")
        self.collection.set_running(False)
        self.camera_ops.refresh_cameras()
        # 데이터셋 트리는 여기서 만들지 않는다. 시작 화면은 Configure 인데
        # 트리는 Dataset 패널에만 보이고, 만드는 데 데이터셋의 scene 파일을
        # 전부 열어야 한다 (16개 568ms 실측, 2026-09-05). _set_activity 가
        # 패널에 들어올 때 만들고 on_connected 도 다시 만드므로, 시작 시점의
        # 이 호출은 아무도 안 보는 것을 위해 창 뜨는 것을 늦추기만 했다.
        if log_path is not None:
            self.log(f"[로그] 이 세션 로그: {log_path}")
        self.log("[준비] 로봇 노드를 먼저 띄운 뒤 Connect 를 누르세요.")
        QTimer.singleShot(0, self.system.startup_tuning)

    # ------------------------------------------------------------- center
    # --------------------------------------------------------------- left
    # ------------------------------------------------------- scene 수집 UI




    # -------------------------------------------------- slot ID 자동 배정











    # -------------------------------------------------- 수집 계획 (slot plan)











    def _recents_valid_repo(self, key: str) -> str:
        """Most recent stored id that actually parses -- a bad one is skipped.

        Recents keeps whatever was last typed, so once a typo lands there it
        becomes the default forever. Falling back to the newest *valid* entry
        means a bad run does not poison the next one.
        """
        # 데이터셋 귀속 기본값이 우선이다: dataset-identity.json 의 hf_repo 가
        # 이 데이터셋의 정본 저장소 (recents 는 마지막 수동 입력일 뿐).
        try:
            ident = load_identity(Path(self.root_edit.text().strip()))
        except Exception:  # noqa: BLE001
            ident = None
        if ident is not None and ident.hf_repo:
            repo = ident.hf_repo if key == "hdf5_repo_id" else f"{ident.hf_repo}-lerobot"
            if repo_id_error(repo) is None:
                return repo
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
                self.layout_ref.layout_reload()
            else:
                self.layout_ref.layout_show()
            self.layout_ref.layout_apply_interval()
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
        self.layout_ref.layout_rerender()

    def _crop_reset(self) -> None:
        d = default_crop_params()
        self.crop_agent_zoom.setValue(round(d["agent"]["zoom"] * 100))
        self.crop_agent_x.setValue(d["agent"]["x"])
        self.crop_agent_y.setValue(d["agent"]["y"])
        self.crop_wrist_x.setValue(d["wrist"]["x"])

    def _set_activity(self, key: str) -> None:
        """Switch the LEFT panel only. The center camera is untouched -- that
        is the whole point of this layout, so nothing here may touch it."""
        self.left_stack.setCurrentIndex(self.left_pages[key])
        act = self._activity_actions.get(key)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        if key == "stats":
            self.stats_ops.refresh_stats()
            if not self.session.stats:
                self.stats_ops.refresh_analysis()
        elif key == "collect":
            # 진행률 트리는 Collect "진행" 상자에 있다 (Statistics 에서 이동)
            self.scene_planning.refresh_plan_progress()
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
        w.episode_discarded.connect(self.collection.on_discarded)
        w.reset_countdown.connect(self.collection.on_countdown)
        w.log_message.connect(self.log)
        w.node_status.connect(self.collection.on_node_status)
        w.fatal_error.connect(self.collection.on_fatal)
        w.connected.connect(self.collection.on_connected)
        w.episode_list_changed.connect(self.playback_ops.on_episode_list)
        w.session_summary.connect(self.stats_ops.on_summary)
        # 세션 해제(버튼 복구, worker=None)는 session_summary가 아니라 finished에
        # 걸어야 한다. summary는 run()의 finally에서만 나오는데, 연결 실패는 그
        # 전에 조기 return이라 summary가 영영 오지 않는다 -- 그 상태에서는 GUI가
        # '연결됨'에 갇혀 재시도하려면 앱을 닫는 수밖에 없었다. finished는 Qt가
        # run()이 어떤 경로로 끝나든 반드시 쏜다.
        w.finished.connect(lambda _w=w: self.collection.on_worker_finished(_w))
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
                self.stats_ops.log_progress(f"{prefix} {line}", view)
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
            # 키를 누르고 있는 것은 결정을 여러 번 내리는 것이 아니다. 이
            # 기계는 500ms 뒤부터 33Hz 로 반복분을 보내는데(xset q), 같은 키가
            # 상태마다 뜻이 달라서(Space: gate=시작 / recording=저장) 그 반복이
            # 상태 경계를 넘으면 방금 시작한 에피소드를 즉시 끝낸다.
            # 2026-09-05 에 그렇게 2프레임짜리가 저장됐다: 자동정렬 직후라
            # approach 램프가 400ms 로 짧아져, 시작용 Space 의 첫 반복(500ms)이
            # 기록 100ms 지점에 떨어졌다.
            #
            # isAutoRepeat 는 최초 KeyPress 에는 False 라, 눌러 둔 시간과
            # 무관하게 동작은 정확히 한 번이 된다. 사람이 초당 33번 누를 수는
            # 없으므로 정상 조작에서 이 줄이 발동할 일은 없다.
            and not event.isAutoRepeat()
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
        self.system.on_stop_node()
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


def main(app: "QApplication | None" = None, camera_node=None,
         camera_node_spec: str = "", robot_node=None,
         schema_version: str = SCHEMA_VERSION) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"workspace_{time.strftime('%Y%m%d_%H%M%S')}.log"
    window_ref: dict = {}
    _install_excepthook(log_path, window_ref)
    if app is None:
        # 런처를 거쳐 왔으면 app 은 이미 글꼴까지 끝난 상태로 넘어온다.
        # 이 가지는 마법사를 건너뛰고 이 파일을 직접 실행한 경우다.
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        ensure_font(app)
        app._wheel_guard = install_wheel_guard(app)
    win = WorkspaceWindow(log_path, camera_node=camera_node,
                          camera_node_spec=camera_node_spec,
                          robot_node=robot_node,
                          schema_version=schema_version)
    window_ref["win"] = win
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
