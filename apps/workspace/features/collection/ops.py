"""Collection control for WorkspaceWindow: connect, record, save, judge, gate, reset."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QTimer, pyqtSlot
from PyQt6.QtWidgets import QMessageBox

from gello.gui.i18n import tr
from gello.collect.worker import CollectionWorker, GATE_RAD, WorkerConfig
from gello.scene.scene_format import read_scene_metadata, scene_filename
from apps.workspace.models import _new_stats


class CollectionOps:
    """Collection control: connect, record, save, judge, gate, reset."""

    def __init__(self, win) -> None:
        self.win = win

    # ------------------------------------------------------------------ control
    def cmd(self, name: str, *args) -> None:
        if self.win.worker is None:
            self.win.log("[제어] 아직 연결되지 않았습니다.")
            return
        getattr(self.win.worker, name)(*args)

    def save(self, success: bool) -> None:
        """Episode end -- the success flag is remembered for stats."""
        if self.win.worker is None:
            self.win.log("[제어] 아직 연결되지 않았습니다.")
            return
        self.win.session.pending_success = success
        self.win.worker.cmd_save_episode(success)

    def toggle_last_verdict(self) -> None:
        """Flips the success flag of the episode that was just saved."""
        if self.win.worker is None or self.win.session.no_dataset_session:
            return
        if self.win.session.last_saved_name is None:
            # 저장이 아직 백그라운드에서 돌고 있어 이름을 모른다. 의사만 적어
            # 두고 episode_saved가 오면 그때 반영한다.
            self.win.session.pending_verdict_toggle = not self.win.session.pending_verdict_toggle
            self.win.log("[판정] 저장이 끝나면 직전 에피소드 판정을 뒤집습니다."
                     if self.win.session.pending_verdict_toggle else "[판정] 뒤집기를 취소했습니다.")
            self.win._refresh_verdict_label()
            return
        self.win.session.last_saved_success = not self.win.session.last_saved_success
        self.win.worker.cmd_set_episode_success(self.win.session.last_saved_name, self.win.session.last_saved_success)
        self.win.stats_ops.bump("success", 1 if self.win.session.last_saved_success else -1)
        self.win.stats_ops.bump("failed", -1 if self.win.session.last_saved_success else 1)
        self.win._refresh_verdict_label()
        self.win.stats_ops.refresh_stats()

    # --------------------------------------------------------------- session UI
    def set_running(self, running: bool) -> None:
        savable = running and not self.win.session.no_dataset_session
        for key in ("discard", "home"):
            self.win.tb_actions[key].setEnabled(running)
        for key in ("save", "savefail"):
            self.win.tb_actions[key].setEnabled(savable)
        self.win.tb_actions["connect"].setEnabled(not running)
        self.win.tb_actions["disconnect"].setEnabled(running)
        for b in (self.win.skip_btn, self.win.discard_btn, self.win.home_btn,
                  # 정렬 버튼은 세션 중이면 항상 열린다 -- 자세 오차가
                  # 커도 사람이 직접 요청하면 걸 수 있어야 한다 (2026-09-01).
                  self.win.match_btn):
            b.setEnabled(running)
        if not running:
            self.win.session.gate_ok = None
        # Start(기록 시작)는 게이트 자세 조건까지 본다 -- 아래 헬퍼가 전담.
        self.update_start_controls(running)
        for b in (self.win.save_ok_btn, self.win.save_ng_btn):
            b.setEnabled(savable)
        self.win.no_dataset_check.setEnabled(not running)
        self.win.task_box.setEnabled(not running and not self.win.no_dataset_check.isChecked())
        for w in (self.win.lang_edit, self.win.root_edit, self.win.agent_combo,
                  self.win.wrist_combo, self.win.layout_agent_combo,
                  self.win.layout_wrist_combo, self.win.reset_pose_combo,
                  self.win.grip_combo, self.win.eplen_edit, self.win.resetwait_edit,
                  self.win.wall_check, self.win.match_check):
            w.setEnabled(not running)
        # 크롭 정렬은 에피소드 attrs 에 Connect 시점 스냅샷으로 찍히므로,
        # 세션 중에 움직이면 가이드와 기록이 어긋난다. 잠근다.
        for w in self.win._crop_widgets:
            w.setEnabled(not running)
        self.win.camera_ops.update_preview_btn()
        # scene 세션에서만 slot 전환 패널 노출
        self.win.slot_box.setVisible(running and self.win.session.scene_session)
        self.win.lights["robot"].set("ok" if running else "off",
                                 tr("연결됨") if running else tr("끊김"))
        self.win.right_fields["robot"].setText(tr("연결됨") if running else tr("끊김"))

    def update_start_controls(self, running: "bool | None" = None) -> None:
        """Start Teleop 버튼/툴바는 게이트 상태에선 자세가 맞아야만 열린다.

        자동 정렬이 켜져 있어도 같다 -- 정렬은 리더가 범위(GATE_RAD) 안에
        들어와야 발동하므로, 그 전에 시작을 눌러도 워커가 거부만 한다.
        버튼을 잠가서 '왜 안 되는지'를 누르기 전에 보이게 한다.
        """
        if running is None:
            running = self.win.worker is not None
        # _gate_ok 는 게이트 진입 직후 None("아직 모름") 일 수 있다 -- setEnabled 는
        # bool 만 받으므로 여기서 확정한다.
        ok = bool(running and (self.win.session.current_state != "gate" or self.win.session.gate_ok))
        self.win.start_btn.setEnabled(ok)
        act = getattr(self.win, "tb_actions", {}).get("record")
        if act is not None:
            act.setEnabled(ok)

    # ------------------------------------------------------------------ connect
    def on_connect(self) -> None:
        if self.win.worker is not None:
            self.win.log("[연결] 이미 세션이 실행 중입니다.")
            return
        no_dataset = self.win.no_dataset_check.isChecked()
        scene_on = not no_dataset  # scene-v1 이 유일한 수집 방식 (legacy 제거)
        lang = self.win.lang_edit.text().strip()
        # scene 설정 검증은 _scene_config_from_ui 가, 파일 생성/이어찍기 판정은
        # SceneWriter 가 한다 (파일명은 scene_id 에서 나오므로 이름 중복 검사
        # 자체가 없다).
        scene_meta = None
        scene_sid = None
        scene_resume = False
        if scene_on:
            scene_meta, scene_sid, scene_resume, err = self.win.scene_ops.scene_config_from_ui()
            if err is not None:
                QMessageBox.warning(self.win, tr("Scene 설정"), err)
                return
            task = scene_meta.scene_id if scene_meta is not None else scene_sid
        else:
            # 연습 모드: writer 에 닿지 않지만 WorkerConfig 라벨용 이름은 필요.
            task = "practice"
        resume = False  # legacy 이어찍기 제거 -- scene 은 scene_resume 이 담당
        agent, wrist = self.win.camera_ops.combo_serial(self.win.agent_combo), self.win.camera_ops.combo_serial(self.win.wrist_combo)
        if not agent or not wrist:
            QMessageBox.warning(self.win, tr("칩로라 선택 필요"),
                                tr("Agent / Wrist 칩로라를 모두 선택하세요."))
            return
        if agent == wrist:
            QMessageBox.warning(self.win, tr("칩로라 중복"),
                                tr("Agent와 Wrist에 같은 칩로라가 선택되었습니다."))
            return
        # 노드가 죽었거나 다른 구성으로 떠 있으면 여기서 맞춘다. worker 는
        # 장치를 직접 열지 않으므로(노드 구독) 이게 유일한 칩로라 준비 단계다.
        if self.win.cameras.camera_node_user_stopped:
            # 수동 종료 상태에서 몰래 되살리면 외부 프로그램(VLA)이 쥔
            # 칩로라를 노드가 빼앗으려 든다 -- 명시적 재시작을 요구한다.
            QMessageBox.warning(self.win, tr("칩로라 노드 종료 상태"),
                                tr("칩로라 노드가 수동으로 종료되어 있습니다 "
                                   "(외부 프로그램용 칩로라 해제).\n"
                                   "Camera 메뉴 > 칩로라 노드 재시작 후 다시 "
                                   "연결하세요."))
            return
        self.win.camera_ops.ensure_camera_node()
        try:
            ep_len = float(self.win.eplen_edit.text())
            reset_wait = float(self.win.resetwait_edit.text())
        except ValueError:
            QMessageBox.warning(self.win, tr("입력 오류"), tr("길이/대기는 숫자여야 합니다."))
            return

        # 미리보기는 이제 세션 나이 살아 둔다 (2026-09-01). 예전에는 여기서
        # 껐다 -- worker 가 칩로라 장치를 직접 열고 있었고, RealSense 파이프라인을
        # 두 번 열 수 없어서였다. 2026-08-25 3-프로세스 분리 이후로는 장치를
        # 칩로라 노드가 독점하고 미리보기도 worker 도 그냥 ZMQ 구독자다
        # (PUB/SUB 는 팬아웃이라 경쟁이 없다). 끄면 오히려 게이트 중 화면이
        # 노드 속도(30 fps)에서 수집 루프 속도로 떨어지고, 게이지가 그 프레임
        # 뒤에 줄을 서서 같이 느려졌다.
        #
        # depth(포인트클라우드)는 사정이 다르다 -- 그건 여전히 장치를 직접
        # 여는 경로라 여기서 놓아야 한다.
        self.win.depth_ops.stop_cloud(restore_previews=False)
        if self.win.camera_ops.previews_busy():
            if self.win._connect_wait_since is None:
                self.win._connect_wait_since = time.monotonic()
                self.win.log("[칩로라] 미리보기 정리를 기다리는 중 -- 정리되면 자동으로 연결합니다.")
            waited = time.monotonic() - self.win._connect_wait_since
            if waited < 12.0:
                self.win.tb_actions["connect"].setEnabled(False)
                self.win.stats_ops.connect_progress(waited)
                QTimer.singleShot(200, self.win.collection.on_connect)
                return
            self.win._connect_wait_since = None
            self.win.tb_actions["connect"].setEnabled(True)
            self.win.statusBar().clearMessage()
            self.win._alert(tr("칩로라 해제 지연"),
                        tr("미리보기가 칩로라를 12초 넘게 붙잡고 있습니다.\n\n"
                           "Camera 메뉴 > 미리보기 중지 후 다시 시도하세요. 계속되면 "
                           "USB 케이블을 다시 꽂아야 합니다 -- 손목 D405는 USB 2 링크라 "
                           "접촉이 나쁘면 이렇게 됩니다."))
            return
        self.win._connect_wait_since = None
        self.win.statusBar().clearMessage()
        cfg = WorkerConfig(
            task_name=task,
            language_instruction=lang or task.replace("_", " "),
            data_root=self.win.root_edit.text().strip(),
            grip=self.win.grip_combo.currentText(),
            reset_pose=self.win.reset_pose_combo.currentText(),
            max_episode_seconds=ep_len,
            reset_wait_seconds=reset_wait,
            enable_wall=self.win.wall_check.isChecked(),
            auto_match_pose=self.win.match_check.isChecked(),
            resume=resume,
            no_dataset=no_dataset,
            scene_metadata=scene_meta,
            scene_id=scene_sid,
            scene_resume=scene_resume,
            instruction_id=(self.win.scene_iid_edit.text().strip() if scene_on else ""),
            collector=(self.win.collector_edit.text().strip() if scene_on else ""),
            agent_camera_serial=agent,
            wrist_camera_serial=wrist,
            schema=self.win.schema,
            # 스냅샷(깊은 복사): 세션 중 슬라이더가 잠기긴 하지만, 기록될 값이
            # GUI 상태와 얽혀 있지 않아야 한다.
            crop_params={r: dict(v) for r, v in self.win.cameras.crop_params.items()},
        )
        for key, value in (("language", lang),
                           ("data_root", cfg.data_root),
                           ("agent_serial", agent), ("wrist_serial", wrist),
                           ("collector", cfg.collector),
                           ("instruction_id", cfg.instruction_id)):
            if value:
                self.win._recents.add(key, value)
        # scene 세션 표시 + Collect 페이지 slot 패널 초기값
        self.win.session.scene_session = scene_on
        if scene_on:
            self.win.slot_iid_edit.setText(cfg.instruction_id)
            self.win.slot_instr_edit.setText(cfg.language_instruction)
            self.win.slot_current_label.setText(
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
            self.win.scene_ops.set_right_scene(md, scene_sid)
        else:
            self.win.scene_ops.set_right_scene(None)

        w = CollectionWorker(cfg)
        self.win.session.no_dataset_session = no_dataset
        # The right panel's serials were only filled by _restart_previews, so
        # they blanked out for the whole session -- exactly when knowing which
        # camera is which matters most.
        self.win.right_fields["cam_agent"].setText(agent)
        self.win.right_fields["cam_wrist"].setText(wrist)
        self.win.lights["camera"].set("ok", tr("세션"))
        self.win.ep_progress.setMaximum(max(1, int(ep_len * cfg.fps)))
        self.win._connect_worker(w)
        self.set_running(True)
        self.win._set_activity("collect")
        if no_dataset:
            self.win.log("[연결] 연습 모드 — 파일을 만들지 않습니다. 저장은 버려집니다.")
        else:
            self.win.log(f"[연결] 세션 시작: task={task!r}")
        w.start()

    def on_disconnect(self) -> None:
        if self.win.worker is None:
            return
        self.win.log("[연결] 세션 종료를 요청했습니다...")
        self.win.worker.cmd_quit()

    # ----------------------------------------------------------------- worker slots
    def on_state(self, state: str) -> None:
        if state == "recording" and self.win.session.current_state != "recording":
            # 표시는 에피소드 단위다. 새 기록이 시작되면 항상 성공에서 출발한다.
            # 직전 에피소드 판정은 이 시점부터 더 이상 뒤집을 수 없다 -- 리셋
            # 구간이 끝났고, 이제 '직전'이 무엇인지 헷갈릴 수 있다.
            self.win.session.last_saved_name = None
            self.win.session.pending_verdict_toggle = False
            self.win.verdict_label.setText("")
        if state == "gate" and self.win.session.current_state != "gate":
            # 새 게이트: 첫 gate_status 가 올 때까지 시작을 잠근다. None 은
            # '아직 모름' -- _on_gate 가 변화가 있을 때만 그리므로, 여기서
            # False 로 두면 첫 상태가 False 일 때 라벨이 안 갱신된다.
            self.win.session.gate_ok = None
        self.win.session.current_state = state
        self.update_start_controls()
        self.win.state_label.setText(self.win.STATE_LABELS.get(state, state))
        self.win.shortcut_hint.setText(self.win.SHORTCUT_HINTS.get(state, ""))
        self.win.right_fields["state"].setText(state)
        recording = "기록" in state or "record" in state.lower()
        self.win.lights["recording"].set("bad" if recording else "off",
                                     tr("기록 중") if recording else tr("대기"))
        self.win.right_fields["recording"].setText(tr("기록 중") if recording else tr("대기"))

    def on_gate(self, leader, follower, all_ok) -> None:
        if leader is None or follower is None:
            return
        d = np.asarray(leader, dtype=float) - np.asarray(follower, dtype=float)
        for i, bar in enumerate(self.win.delta_bars):
            if i < len(d):
                bar.update_delta(float(d[i]), GATE_RAD)
        # 아래는 전부 all_ok 가 '바뀔 때만' 의미가 있는 일이다. 게이트는
        # 초당 45번 오는데, 매번 라벨 텍스트·스타일시트를 다시 쓰고 버튼
        # 활성 상태를 재계산하면 -- setStyleSheet 은 Qt 가 스타일을 통째로
        # 다시 파싱하게 만드는 호출이다 -- GUI 스레드가 그 뒤에 밀려 바가
        # 손을 늦게 따라온다. 워커는 45 Hz 로 멀쩡히 본내고 있었다 (실측
        # 0.7~2.6 ms/틱), 병목은 이쪽이었다 (2026-09-01).
        if all_ok != self.win.session.gate_ok:
            self.win.session.gate_ok = all_ok
            self.win.gate_label.setText(tr("자세 일치 — 시작 가능") if all_ok
                                    else tr("리더를 팔로워 자세에 맞추세요"))
            self.win.gate_label.setStyleSheet(
                "color:#2ecc71;" if all_ok else "color:#e67e22;")
            # 정렬 버튼은 자세와 무관하게 열린다 (2026-09-01) -- 아래
            # _set_running 이 세션 단위로 켜고 끈다. 잠기는 것은 '텔레옵
            # 시작' 쪽뿐이다.
            self.update_start_controls()
            if self.win.session.current_state == "gate":
                self.win.shortcut_hint.setText(
                    "Space: 텔레옵 시작   Enter: 자동 정렬 다시" if all_ok
                    else "Space: 텔레옵 시작   Enter: 자동 정렬 (오차 커도 가능)")

    def on_pose_match(self, err, done) -> None:
        self.win.gate_label.setText(
            tr("자동 정렬 완료") if done else tr("자동 정렬 중... 오차 {e:.3f} rad").format(e=err))

    def on_progress(self, n_frames, seconds) -> None:
        self.win.ep_progress.setValue(n_frames)
        self.win.right_fields["frames"].setText(f"{n_frames} ({seconds:.1f}s)")

    def on_saved(self, name, n_frames) -> None:
        self.win.stats_ops.bump("saved")
        self.win.stats_ops.bump("frames", n_frames)
        if self.win.session.pending_success is not None:
            self.win.stats_ops.bump("success" if self.win.session.pending_success else "failed")
            self.win.session.pending_success = None
        self.win.session.last_saved_name = name
        if self.win.session.pending_success is not None:
            self.win.session.last_saved_success = self.win.session.pending_success
        if self.win.session.pending_verdict_toggle:
            # 저장 전에 눌러 둔 뒤집기를 이제 반영한다.
            self.win.session.pending_verdict_toggle = False
            self.win.session.last_saved_success = not self.win.session.last_saved_success
            self.win.worker.cmd_set_episode_success(name, self.win.session.last_saved_success)
        self.win._refresh_verdict_label()
        self.win.log(f"[저장] {name} ({n_frames} frames)")
        self.win.right_fields["episode"].setText(name)
        self.win.dataset_ops.update_dataset_panel()
        self.win.stats_ops.refresh_stats()

    def on_save_status(self, text: str) -> None:
        """Background-save progress. Empty string means idle."""
        self.win.save_status_label.setText(text)
        self.win.save_status_label.setStyleSheet(
            "color:#f39c12;" if text else "color:#888;")

    def on_countdown(self, seconds) -> None:
        # 자동 진행이 없어졌으므로 카운트다운이 아니라 경과 시간이다.
        self.win.state_label.setText(
            tr("리셋 중 {s:.0f}s 경과 — 배치 후 Enter").format(s=seconds))

    def on_fatal(self, msg) -> None:
        self.win.log(f"[치명적 오류] {msg}")
        # 서보 과토크 보호(overload 0x20 등 hardware error)로 죽은 세션은
        # GUI 재시작이 아니라 서보 Reboot 으로만 복구된다 -- 그 툴이 있는
        # 위치를 오류 대화상자에서 바로 알려준다 (#37B).
        if "hardware error" in msg:
            msg += tr("\n\n서보 보호모드가 걸렸습니다. 세션 종료 후 "
                      "Tools > 리더암 서보 보호 해제 (재부팅) 으로 복구하세요.")
        self.win._alert(tr("오류"), msg, QMessageBox.Icon.Critical)

    def on_connected(self, n_episodes, path) -> None:
        # 세션이 붙었다 = 노드가 살아 응답했다 (연결 검증이 노드 경유).
        self.win.lights["node"].set("ok", tr("정상"))
        self.win.right_fields["node"].setText(tr("정상"))
        # 연결되면 카메라 화면으로 따라간다. 버튼을 누른 시점이 아니라 여기인
        # 이유는, 연결이 미리보기 정리를 기다리거나 실패할 수 있기 때문이다 --
        # 그때 Live 로 옮겨두면 아무것도 안 나오는 탭을 보게 된다.
        self.win.center_tabs.setCurrentIndex(self.win._live_tab_index)
        # 기록 외 단계에서는 worker 가 카메라를 읽지 않으므로(게이지를 빠르게
        # 유지하기 위해 -- _emit_gate_status 참고) 미리보기가 그 구간의 유일한
        # 영상 공급원이다. 꺼져 있으면 자세를 맞추는 동안 화면이 빈다.
        if not (self.win.agent_preview or self.win.wrist_preview):
            self.win.camera_ops.restart_previews()
        # 이번 task 카운터는 여기서 0 으로 돌아간다(누적은 그대로). 연습 모드도
        # 마찬가지다 -- NullTaskWriter 도 저장을 받아 넘기므로 카운터는 움직인다.
        self.win.session.counters = _new_stats()
        if self.win.session.no_dataset_session:
            # NullTaskWriter has no real path; claiming one here would make the
            # dataset tree think a file is locked by this session.
            self.win.dataset_ops.update_dataset_panel()
            self.win.log("[연결] 연습 모드로 연결되었습니다.")
            return
        self.win.session.active_file_path = Path(path)
        self.win.session.episodes_at_connect = int(n_episodes)
        # 직전 세션에서 삭제가 실패해 남았을 수 있는 대기 건수를 청산 --
        # 새 세션의 첫 목록 갱신이 엉뚱한 무효화를 하지 않게.
        self.win._pending_scene_deletes = 0
        if self.win.session.scene_session:
            # scene 파일이 실제로 만들어졌으니 보관해 둔 새 scene 구성은 소진.
            self.win._pending_scene_meta = None
            self.win.scene_planning.refresh_slot_panel()
        self.win.dataset_ops.update_dataset_panel()
        self.win.log(f"[연결] 파일: {path} (기존 {n_episodes}개 에피소드)")
        self.win.dataset_ops.refresh_dataset_tree()

    @pyqtSlot()
    def on_worker_finished(self) -> None:
        """워커 run()이 어떤 경로로든 끝나면 세션을 해제한다.

        정상 종료(요약 후), 연결 실패 조기 return, 예외 -- 전부 여기로 온다.
        summary보다 늦게 도착하므로(둘 다 큐잉, run() 안에서 summary가 먼저
        emit) 로그 순서도 자연스럽다.
        """
        if self.win.worker is not self.sender():
            # 이미 다른 세션이 시작된 뒤 도착한 옛 워커의 신호 -- 무시.
            return
        self.win.worker = None
        self.win.session.no_dataset_session = False
        self.win.session.active_file_path = None
        self.win.session.active_episode_cache = None
        was_scene = self.win.session.scene_session
        self.win.session.scene_session = False
        self.win.scene_ops.set_right_scene(None)
        self.win.collection.set_running(False)
        self.win.dataset_ops.refresh_dataset_tree()
        if was_scene:
            # 세션이 만든/키운 scene 파일이 목록·slot 현황에 반영되게.
            self.win.scene_ops.refresh_scene_combo()
        self.win.camera_ops.restart_previews()
        if self.win.cameras.depth_consumer is not None:
            # 세션 동안 Depth/Point Cloud 탭에 머물러 있었다면 스트림을 다시
            # 올린다 (세션 중엔 안난만 보였다). 미리보기가 뜨는 시간을 준다.
            QTimer.singleShot(600, lambda: (
                self.win.depth_ops.start_cloud() if self.win.worker is None
                and self.win.cameras.depth_consumer is not None else None))
