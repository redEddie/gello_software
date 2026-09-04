"""Camera preview and camera-node operations for WorkspaceWindow."""

from __future__ import annotations

import sys
import time

from PyQt6 import sip
from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import QComboBox, QMessageBox

from gello.gui.workers import CameraPreviewWorker
from gello.gui.grid_overlay import active_corners, draw_grid, save_grid_store
from apps.workspace.constants import WT_ROOT
from gello.gui.i18n import tr


class CameraOps:
    """Camera preview and camera-node operations."""

    def __init__(self, win) -> None:
        self.win = win

    # ------------------------------------------------------------------ list
    def refresh_cameras(self) -> None:
        try:
            from lerobot.cameras.realsense import RealSenseCamera

            cams = RealSenseCamera.find_cameras()
        except Exception as e:  # noqa: BLE001
            self.set_camera_hint(tr("카메라 목록 조회 실패: {e}").format(e=e))
            self.win.log(f"[카메라] 목록 조회 실패: {type(e).__name__}: {e}")
            return
        entries = []
        for c in cams:
            serial = str(c.get("serial_number") or c.get("id") or "")
            name = str(c.get("name") or "RealSense")
            if serial:
                entries.append((serial, f"{name} ({serial})"))
        for combo, remembered in ((self.win.agent_combo, "agent_serial"),
                                  (self.win.wrist_combo, "wrist_serial")):
            cur = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("(선택 안함)"), "")
            for serial, label in entries:
                combo.addItem(label, serial)
            want = cur or self.win._recents.most_recent(remembered, "")
            if want:
                for i in range(combo.count()):
                    if combo.itemData(i) == want or combo.itemText(i) == want:
                        combo.setCurrentIndex(i)
                        break
            combo.blockSignals(False)
        self.mirror_camera_combos(rebuild=True)
        self.set_camera_hint(tr("{n}대 감지됨").format(n=len(entries)))
        self.win.log(f"[카메라] {len(entries)}대 감지: {[s for s, _ in entries]}")
        self.ensure_camera_node()

    def set_camera_hint(self, text: str) -> None:
        self.win.camera_hint.setText(text)
        if hasattr(self.win, "layout_camera_hint"):
            self.win.layout_camera_hint.setText(text)

    def mirror_camera_combos(self, rebuild: bool = False) -> None:
        """Configure 콤보(원본) -> Layout 콤보(미러) 복사. ``rebuild`` 면 항목
        목록까지 새로 채운다 (refresh_cameras 뒤)."""
        if not hasattr(self.win, "layout_agent_combo"):
            return
        for src, dst in ((self.win.agent_combo, self.win.layout_agent_combo),
                         (self.win.wrist_combo, self.win.layout_wrist_combo)):
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

    def on_layout_camera_changed(self) -> None:
        """Layout 콤보에서 고른 것을 원본으로 밀어넣는다. 원본 시그널이
        on_camera_changed 를 태워 미리보기 재시작까지 이어진다."""
        for src, dst in ((self.win.layout_agent_combo, self.win.agent_combo),
                         (self.win.layout_wrist_combo, self.win.wrist_combo)):
            if src.currentText() == dst.currentText():
                continue
            i = src.currentIndex()
            if i >= 0 and src.itemText(i) == src.currentText():
                dst.setCurrentIndex(i)
            else:
                dst.setCurrentText(src.currentText())

    def combo_serial(self, combo: QComboBox) -> str:
        data = combo.currentData()
        if data:
            return str(data)
        text = combo.currentText().strip()
        return "" if text.startswith("(") else text

    def on_camera_changed(self) -> None:
        self.mirror_camera_combos()
        if self.win.worker is not None:
            return  # 세션 중 카메라 교체는 없다 -- 노드도 그대로 둔다
        self.ensure_camera_node()   # 선택이 바뀌면 노드를 새 구성으로 재시작
        self.restart_previews()

    # --------------------------------------------------------------- preview
    def on_toggle_previews(self) -> None:
        # 세션 중에도 켜고 끌 수 있다: 카메라 노드가 장치를 갖고 있고 이쪽은
        # 구독자일 뿐이라 worker 와 경합하지 않는다 (2026-09-01).
        if self.win.agent_preview or self.win.wrist_preview:
            self.stop_previews_async()
            for role in ("agent", "wrist"):
                self.win.live_views[role].clear_frame(tr("미리보기 중단됨"))
        else:
            self.restart_previews()

    def update_preview_btn(self) -> None:
        if not hasattr(self.win, "preview_btn"):
            return
        on = bool(self.win.agent_preview or self.win.wrist_preview)
        for btn in (self.win.preview_btn,
                    getattr(self.win, "layout_preview_btn", None)):
            if btn is not None:
                btn.setText(tr("미리보기 중단") if on else tr("미리보기 시작"))
                btn.setEnabled(self.win.worker is None)

    def restart_previews(self) -> None:
        self.stop_previews_async()
        for role, combo in (("agent", self.win.agent_combo),
                            ("wrist", self.win.wrist_combo)):
            serial = self.combo_serial(combo)
            if not serial:
                self.win.live_views[role].clear_frame(tr("카메라를 선택하세요"))
                self.win.right_fields[f"cam_{role}"].setText("-")
                continue
            w = CameraPreviewWorker(role, serial)
            w.frame_ready.connect(lambda f, r=role: self.on_preview_frame(r, f))
            w.error.connect(lambda m, r=role: self.on_preview_error(r, m))
            w.start()
            setattr(self.win, f"{role}_preview", w)
            self.win.right_fields[f"cam_{role}"].setText(serial)
        self.win.lights["camera"].set(
            "ok" if (self.win.agent_preview or self.win.wrist_preview) else "off",
            tr("미리보기") if (self.win.agent_preview or self.win.wrist_preview) else "-")
        self.update_preview_btn()

    def previews_busy(self) -> bool:
        """Prunes finished previews, skipping any whose C++ side is already gone.

        `sip.isdeleted` must come first and cannot be dropped: a QThread that
        Qt has destroyed leaves its Python wrapper behind, and *any* call on
        it -- isRunning() included -- raises "wrapped C/C++ object ... has been
        deleted". That was this list's normal end state, so the exception fired
        on the next stop/restart and again from closeEvent.
        """
        self.win._dying_previews = [w for w in self.win._dying_previews
                                    if not sip.isdeleted(w) and w.isRunning()]
        return bool(self.win._dying_previews)

    def release_preview(self, role: str) -> None:
        """Asks one preview thread to stop, and cuts it off from the UI now.

        Disconnecting before waiting is the important half. A thread that is
        slow to notice the stop flag (the wrist D405 can sit inside a read for
        a second) used to keep emitting frames into the GUI thread the whole
        time, and if a restart replaced the handle while it was still alive the
        old one was orphaned but still connected -- so every retry added
        another 30 fps of scaling work to the UI thread. Once disconnected an
        orphan is harmless: it only still owns the camera, which is what
        previews_busy() reports.
        """
        w = getattr(self.win, f"{role}_preview", None)
        if w is None:
            return
        for sig in (w.frame_ready, w.error):
            try:
                sig.disconnect()
            except TypeError:
                pass  # already disconnected
        w.stop()
        setattr(self.win, f"{role}_preview", None)
        if w.isRunning():
            # No deleteLater: this list is the owner. Having both meant Qt
            # could free the thread while the list still held the wrapper,
            # which is exactly what previews_busy() then tripped over. The
            # entry is dropped once the thread reports finished, and the last
            # Python reference goes with it.
            self.win._dying_previews.append(w)

    def stop_previews_async(self) -> None:
        """Non-blocking stop. The GUI thread never waits on a camera here --
        that wait was up to 7 s per thread and read as a hang."""
        for role in ("agent", "wrist"):
            self.release_preview(role)
        self.win.lights["camera"].set(
            "busy" if self.previews_busy() else "off",
            tr("정리 중") if self.previews_busy() else "-")
        self.update_preview_btn()
        # 멈춘 카메라의 마지막 프레임을 "현재"로 계속 겹쳐 보이지 않게 한다.
        cams = self.win.cameras
        if cams.last_cam_frame:
            cams.last_cam_frame.clear()
            if self.win.center_tabs.currentIndex() == self.win._layout_tab_index:
                for role in ("agent", "wrist"):
                    self.win.layout_ref.layout_update_role(role)

    def stop_previews_blocking(self, timeout_ms: int = 4000) -> None:
        """Only for shutdown: wait so the cameras are released before exit.

        Blocking is acceptable here and nowhere else -- the window is closing,
        so there is no interaction left to make unresponsive.
        """
        self.stop_previews_async()
        for w in self.win._dying_previews:
            if not sip.isdeleted(w):
                w.wait(timeout_ms)
        self.win._dying_previews = []

    # -------------------------------------------------------------------- ui
    def on_preview_frame(self, role: str, frame) -> None:
        # 기록 중에는 worker 가 별내는 프레임이 이긴다 -- 화면에 보이는 것이
        # 실제로 파일에 쓰이는 그림이어야 하기 때문이다. 그 외 단계(게이트·
        # 리셋 대기)에서는 worker 가 카메라를 아예 안 읽으므로 여기가 유일한
        # 공급원이고, 노드 속도 그대로 나온다.
        win = self.win
        cams = win.cameras
        if win.session.current_state == "recording":
            return
        self.update_live_view(role, frame, cams=cams)
        if win.center_tabs.currentIndex() == win._layout_tab_index:
            win.layout_ref.layout_update_role(role)
        cams.fps_count += 1

    def update_live_view(self, role: str, frame, cams=None) -> None:
        """라이브 프레임 공용 경로 -- 원본 캐시 + 표시 (겹침 없음)."""
        if cams is None:
            cams = self.win.cameras
        cams.last_cam_frame[role] = frame      # 격자 없는 원본을 저장
        self.win.live_views[role].set_frame(self.with_grid(role, frame))

    def set_live_maximized(self, role: "str | None") -> None:
        """좌우 배치는 유지하고 스플리터 비율만 바꾼다 -- 최대화한 쪽이
        ~88%, 반대쪽은 아주 작게. 겹침(PiP) 없음. 경계는 드래그로도 조절."""
        if role == self.win.cameras.live_maximized:
            return
        self.win.cameras.live_maximized = role
        total = max(self.win.live_split.width(), 800)
        if role is None:
            self.win.live_split.setSizes([total // 2, total // 2])
        else:
            big, small = int(total * 0.88), max(90, int(total * 0.12))
            self.win.live_split.setSizes([big, small] if role == "agent"
                                         else [small, big])
        idx = 0 if role is None else self.win.live_view_combo.findData(role)
        if idx >= 0 and self.win.live_view_combo.currentIndex() != idx:
            self.win.live_view_combo.blockSignals(True)
            self.win.live_view_combo.setCurrentIndex(idx)
            self.win.live_view_combo.blockSignals(False)

    def with_grid(self, role: str, frame):
        """agent 라이브 화면에만 워크스페이스 3×3 격자를 덧그린다 (사본)."""
        if role != "agent" or not self.win.grid_live_check.isChecked():
            return frame
        corners = active_corners(self.win.cameras.grid_store)
        if not corners:
            return frame
        return draw_grid(frame, corners, self.win.grid_alpha_slider.value())

    def on_grid_live_toggled(self, on: bool) -> None:
        self.win.cameras.grid_store["live_on"] = bool(on)
        save_grid_store(self.win.cameras.grid_store)
        if on and active_corners(self.win.cameras.grid_store) is None:
            self.win.log(tr("[격자] 저장된 격자가 없습니다 — '격자 편집...'에서 "
                            "만들어 저장하세요."))
        self.regrid_live()

    def regrid_live(self) -> None:
        """마지막 프레임으로 agent 뷰를 다시 그린다 -- 멈춘 화면에서도
        체크박스/슬라이더가 즉시 반영되게."""
        frame = self.win.cameras.last_cam_frame.get("agent")
        if frame is not None:
            self.win.live_views["agent"].set_frame(
                self.with_grid("agent", frame))

    def on_preview_error(self, role: str, msg: str) -> None:
        self.win.live_views[role].clear_frame(tr("미리보기 실패"))
        self.win.log(f"[카메라 미리보기 실패] {role}: {msg}")
        self.win.lights["camera"].set("bad", tr("오류"))

    # ----------------------------------------------------------------- worker
    def on_frames(self, agent_rgb, wrist_rgb) -> None:
        win = self.win
        cams = win.cameras
        layout_on = win.center_tabs.currentIndex() == win._layout_tab_index
        for role, rgb in (("agent", agent_rgb), ("wrist", wrist_rgb)):
            if rgb is None:
                continue
            self.update_live_view(role, rgb, cams=cams)
            if layout_on:
                win.layout_ref.layout_update_role(role)
        cams.fps_count += 1

    def tick_fps(self) -> None:
        win = self.win
        cams = win.cameras
        cams.fps_value = cams.fps_count
        cams.fps_count = 0
        win.right_fields["fps"].setText(f"{cams.fps_value:.0f}")
        if win.worker is not None and not win.session.no_dataset_session:
            # max(): 저장이 백그라운드라 episode_list_changed 가 몇 초 늦게 온다.
            # 그 사이를 연결시점 + 이번 task 저장수로 메운다. _session 이 Connect
            # 마다 리셋되므로 두 값 모두 지금 task 의 것이다.
            total = max(len(win.session.active_episode_cache or []),
                        win.session.episodes_at_connect + win.session.counters["saved"])
            count = tr("{k}: 에피소드 {t}개 (이번 +{s})").format(
                k=win._current_task_label(limit=32), t=total, s=win.session.counters["saved"])
        else:
            count = tr("저장 {s}").format(s=win.session.cumulative["saved"])
        win.sb_right.setText(
            f"{cams.fps_value:.0f} fps   |   {count}   |   {win.root_edit.text()}")

    # ------------------------------------------------------------------ node
    def camera_node_specs(self) -> list:
        specs = []
        for role, combo in (("agent", self.win.agent_combo),
                            ("wrist", self.win.wrist_combo)):
            serial = self.combo_serial(combo)
            if serial:
                specs.append(f"{role}:{serial}")
        return specs

    def on_restart_camera_node(self) -> None:
        self.win.cameras.camera_node_user_stopped = False
        self.ensure_camera_node(restart=True)

    def on_stop_camera_node_manual(self) -> None:
        """카메라를 완전히 놓는다 -- VLA 배포 등 외부 프로그램이 장치를
        직접 열 수 있게. 미리보기·depth 뷰도 함께 내린다 (구독자만 남으면
        에러만 5초마다 찍는다)."""
        if self.win.worker is not None:
            QMessageBox.warning(self.win, tr("세션 진행 중"),
                                tr("수집 세션이 카메라를 쓰고 있습니다. "
                                   "세션 종료 후 노드를 내리세요."))
            return
        self.win.cameras.camera_node_user_stopped = True
        self.win.depth_ops.stop_cloud(restore_previews=False)
        self.stop_previews_async()
        self.stop_camera_node()
        for role in ("agent", "wrist"):
            self.win.live_views[role].clear_frame(tr("카메라 노드 종료됨"))
        self.win.lights["camera"].set("off", tr("노드 종료"))
        self.win.log("[카메라노드] 수동 종료 — 카메라가 해제되어 다른 프로그램"
                     "(VLA 정책 클라이언트 등)이 열 수 있습니다. 다시 쓰려면 "
                     "Process 메뉴 > 카메라 노드 재시작.")

    def ensure_camera_node(self, restart: bool = False) -> None:
        """카메라 노드 프로세스를 현재 콤보 선택과 일치하게 유지한다.

        이미 같은 구성으로 떠 있으면 아무것도 하지 않는다 -- 노드의 가치는
        "카메라를 한 번 열고 계속 스트리밍"에 있으므로 불필요한 재시작이
        가장 나쁘다. 선택이 바뀌었거나 죽었을 때만 (재)시작한다. 수동 종료
        래치가 켜져 있으면 건드리지 않는다 (외부 프로그램이 카메라를 쓰는
        중일 수 있다 -- restart=True 도 래치를 풀지 않는다, 그건
        on_restart_camera_node 만 한다)."""
        if self.win.cameras.camera_node_user_stopped:
            return
        specs = self.camera_node_specs()
        key = ",".join(specs)
        running = (self.win.procs.camera_node_process is not None and
                   self.win.procs.camera_node_process.state()
                   != QProcess.ProcessState.NotRunning)
        if running and not restart and key == self.win.cameras.camera_node_spec:
            return
        if running:
            self.stop_camera_node()
        if not specs:
            return
        proc = QProcess(self.win)
        proc.setProgram(sys.executable)
        proc.setArguments(["-m", "gello.comm.camera_node", "--die-with-parent"]
                          + [a for sp in specs for a in ("--cam", sp)])
        # 이 프로세스는 GUI 의 sys.path 를 물려받지 않는다 -- 저장소 루트에서
        # 띄워야 `python -m gello.comm.camera_node` 가 gello 를 찾는다.
        proc.setWorkingDirectory(str(WT_ROOT))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self.on_camera_node_output)
        proc.finished.connect(self.on_camera_node_finished)
        self.win.procs.camera_node_process = proc
        self.win.cameras.camera_node_spec = key
        self.win.log(f"[카메라노드] 시작: {key}")
        proc.start()

    def on_camera_node_output(self) -> None:
        if self.win.procs.camera_node_process is None:
            return
        for line in self.win._proc_text(self.win.procs.camera_node_process).splitlines():
            if line.strip():
                self.win.log(f"[카메라노드] {line.rstrip()}")

    def on_camera_node_finished(self, code: int, _status) -> None:
        proc = self.win.sender()
        if proc is not self.win.procs.camera_node_process:
            # stop_camera_node() 나 ensure(재시작) 가 이미 손을 뗀 프로세스
            # -- 의도된 종료라 조용히 본낸다.
            self.win.log(f"[카메라노드] 종료 (exit={code})")
            return
        # 비정상 종료 -- 자동 재시작한다. 단 crash-loop(예: 포트 충돌로
        # 뜨자마자 죽는 상태)이면 로그만 가득 채우므로 60초 내 3회를 넘으면
        # 멈추고 수동(카메라 메뉴)으로 넘긴다.
        self.win.procs.camera_node_process = None
        self.win.cameras.camera_node_spec = ""
        now = time.monotonic()
        self.win.cameras.camera_node_crashes = [
            t for t in self.win.cameras.camera_node_crashes if now - t < 60.0] + [now]
        if len(self.win.cameras.camera_node_crashes) > 3:
            self.win.log(f"[카메라노드] 비정상 종료 (exit={code}) — 60초 내 "
                         f"{len(self.win.cameras.camera_node_crashes)}회째, 자동 재시작을 "
                         "멈춥니다. Process 메뉴 > 카메라 노드 재시작으로 수동 "
                         "시작하세요.")
            return
        self.win.log(f"[카메라노드] 비정상 종료 (exit={code}) — 2초 후 자동 재시작")
        QTimer.singleShot(2000, self.ensure_camera_node)

    def stop_camera_node(self) -> None:
        proc = self.win.procs.camera_node_process
        self.win.procs.camera_node_process = None
        self.win.cameras.camera_node_spec = ""
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        proc.terminate()
        if not proc.waitForFinished(3000):
            proc.kill()
            proc.waitForFinished(2000)
