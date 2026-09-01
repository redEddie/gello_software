"""Playback, trim, replay, and HDF5 structure operations for WorkspaceWindow."""

from __future__ import annotations

import sys
from pathlib import Path

import h5py
from PyQt6.QtCore import QProcess, Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from gello.data.episode_stats import load_series
from gello.data.episode_trim import plan_trim, suggest_trim, trim_tail
from gello.gui.gui_widgets import PLAYBACK_FPS, EpisodeLoadWorker
from gello.gui.i18n import tr
from gello.data.libero_format import describe_episode, hdf5_repack_status
from gello.gui.scene_gallery import invalidate_scene_thumbs
from gello.scene.scene_format import count_by_slot, describe_scene, read_scene_metadata

from apps.workspace.constants import REPLAY_SCRIPT


class PlaybackOps:
    """Playback, trim, robot replay, and HDF5 structure inspection."""

    def __init__(self, win) -> None:
        self.win = win

    # ------------------------------------------------------------------ open
    def on_open_trim(self) -> None:
        items = [i for i in self.win.dataset_tree.selectedItems() if i.parent() is not None]
        if not items:
            QMessageBox.information(self.win, tr("선택 필요"),
                                    tr("에피소드를 하나 선택하세요 (파일이 아니라)."))
            return
        it = items[0]
        path = it.parent().data(0, Qt.ItemDataRole.UserRole)
        self.show_trim_for(path, it.data(0, Qt.ItemDataRole.UserRole))
        self.win.center_tabs.setCurrentIndex(self.win.playback.trim_tab_index)

    # ------------------------------------------------------------------ Trim
    def show_trim_for(self, path: str, demo: str) -> None:
        """Dataset 트리와 Analysis 순위표가 공유하는 트림 진입점."""
        if not path or not demo:
            return
        if self.win.session.active_file_path is not None and Path(path) == self.win.session.active_file_path:
            self.win.trim_summary.setText(tr("수집 중인 파일은 편집할 수 없습니다."))
            return
        self.win.playback.trim_key = (path, demo)
        self.win.playback.trim_n_pending = 0
        try:
            series = load_series(path, demo)
        except Exception as e:  # noqa: BLE001
            self.win.trim_summary.setText(tr("불러오기 실패: {e}").format(e=e))
            return
        self.win.playback.trim_series = series
        self.win.playback.trim_n = int(series["n"])
        for plot, dims in self.win.trim_plots.values():
            plot.set_data(series, dims)
        self.win.playback.trim_frames = {"agent": None, "wrist": None}
        for v in self.win.trim_views.values():
            v.clear_frame(tr("영상 불러오는 중..."))
        if self.win.playback.trim_loader is not None:
            self.win.playback.trim_loader.wait()
        self.win.playback.trim_loader = EpisodeLoadWorker(path, demo)
        self.win.playback.trim_loader.loaded.connect(self.on_trim_loaded)
        self.win.playback.trim_loader.failed.connect(
            lambda m: [v.clear_frame(tr("영상 없음")) for v in self.win.trim_views.values()])
        self.win.playback.trim_loader.start()
        self.trim_update()

    @pyqtSlot(str, str, object, object)
    def on_trim_loaded(self, path, demo, agent, wrist) -> None:
        if self.win.playback.trim_key != (path, demo):
            return
        self.win.playback.trim_frames = {"agent": agent, "wrist": wrist}
        self.trim_update()
        self.trim_seek(self.trim_keep() - 1)

    def trim_pending(self) -> int:
        return self.win.playback.trim_n_pending

    def trim_keep(self) -> int:
        return max(0, self.win.playback.trim_n - self.trim_pending())

    def trim_add(self, n: int) -> None:
        """+/- 를 누른 만큼 옮긴다. 0 아래로는 못 간다 -- 원본보다 길어질 수 없다."""
        if self.win.playback.trim_key is None:
            return
        self.win.playback.trim_n_pending = max(0, self.win.playback.trim_n_pending + n)
        self.trim_update()
        self.trim_seek(self.trim_keep() - 1)

    def trim_reset(self) -> None:
        """정정 -- 고른 것을 통째로 0으로. 한 단계씩 물리는 것보다, 잘못 짚었을 때
        처음부터 다시 보는 쪽이 실제 흐름에 맞는다."""
        if self.win.playback.trim_key is None:
            return
        self.win.playback.trim_n_pending = 0
        self.trim_update()
        self.trim_seek(self.trim_keep() - 1)

    def trim_suggest(self) -> None:
        if self.win.playback.trim_key is None:
            return
        n = suggest_trim(*self.win.playback.trim_key)
        self.win.playback.trim_n_pending = n
        self.win.log(f"[트림] 추천 {n}프레임" + ("" if n else " (이미 조용하게 끝납니다)"))
        self.trim_update()
        self.trim_seek(self.trim_keep() - 1)

    def trim_seek(self, i: int) -> None:
        n = self.win.playback.trim_n
        if n <= 0:
            return
        i = max(0, min(n - 1, i))
        self.win.trim_slider.blockSignals(True)
        self.win.trim_slider.setRange(0, n - 1)
        self.win.trim_slider.setValue(i)
        self.win.trim_slider.blockSignals(False)
        self.trim_show_frame(i)

    def trim_show_frame(self, i: int) -> None:
        keep = self.trim_keep()
        for role, v in self.win.trim_views.items():
            arr = self.win.playback.trim_frames.get(role)
            if arr is None or len(arr) == 0:
                continue
            v.set_frame(arr[min(i, len(arr) - 1)])
        mark = tr(" ← 잘린 뒤 마지막") if i == keep - 1 else (
            tr("  (잘려나갈 구간)") if i >= keep else "")
        self.win.trim_pos.setText(f"{i + 1}/{self.win.playback.trim_n}{mark}")
        for plot, _ in self.win.trim_plots.values():
            plot.set_cursor(i)

    def on_trim_scrub(self, i: int) -> None:
        self.trim_show_frame(i)

    def on_trim_play(self) -> None:
        """잘린 뒤 구간만 훑는다 -- 확인하려는 것이 '새 끝'이기 때문이다."""
        if self.win.playback.trim_key is None:
            return
        keep = self.trim_keep()
        self.trim_seek(max(0, keep - 40))
        if self.win.playback.trim_timer is None:
            self.win.playback.trim_timer = QTimer(self.win)
            self.win.playback.trim_timer.setInterval(50)
            self.win.playback.trim_timer.timeout.connect(self.trim_tick)
        self.win.playback.trim_timer.start()
        self.win.trim_play_btn.setText(tr("정지"))

    def trim_tick(self) -> None:
        i = self.win.trim_slider.value() + 1
        if i >= self.trim_keep():
            self.win.playback.trim_timer.stop()
            self.win.trim_play_btn.setText(tr("재생"))
            return
        self.trim_seek(i)

    def trim_update(self) -> None:
        """Recomputes every label, guard and shading from the pending count."""
        has = self.win.playback.trim_key is not None
        self.win.trim_play_btn.setEnabled(has and self.win.playback.trim_frames.get("agent") is not None)
        self.win.trim_slider.setEnabled(has)
        self.win.trim_reset_btn.setEnabled(bool(self.win.playback.trim_n_pending))
        if not has:
            self.win.trim_count.setText(tr("에피소드를 고르세요"))
            self.win.trim_apply_btn.setEnabled(False)
            self.win.trim_warn.setText("")
            for plot, _ in self.win.trim_plots.values():
                plot.set_cut(None)
            return
        path, demo = self.win.playback.trim_key
        n_trim, keep = self.trim_pending(), self.trim_keep()
        plan = plan_trim(path, [demo], max(n_trim, 1))[0]
        self.win.trim_summary.setText(
            tr("{d} · {n}프레임 ({s:.1f}s) · 마지막 그리퍼 동작 −{g}프레임").format(
                d=demo, n=self.win.playback.trim_n, s=self.win.playback.trim_n / 20.0,
                g=plan.gripper_tail if plan.gripper_tail is not None else "?"))
        self.win.trim_count.setText(
            tr("{a} → {b} 프레임   (−{n})").format(a=self.win.playback.trim_n, b=keep, n=n_trim)
            if n_trim else tr("{a} 프레임 — 자를 구간 없음").format(a=self.win.playback.trim_n))
        for plot, _ in self.win.trim_plots.values():
            plot.set_cut(keep if n_trim else None)
        blocked = plan_trim(path, [demo], n_trim)[0].blocked if n_trim else None
        self.win.trim_apply_btn.setEnabled(bool(n_trim) and not blocked)
        if blocked:
            self.win.trim_warn.setText(tr("⚠ {b}").format(b=blocked))
        elif plan.already:
            self.win.trim_warn.setText(tr("이미 다듬은 이력: {a}").format(a=plan.already))
        else:
            self.win.trim_warn.setText("")

    def trim_apply(self) -> None:
        if self.win.playback.trim_key is None or not self.trim_pending():
            return
        path, demo = self.win.playback.trim_key
        n_trim, keep = self.trim_pending(), self.trim_keep()
        if QMessageBox.question(
                self.win, tr("끝 다듬기 확정"),
                tr("{f}\n{d}\n\n{a} → {b} 프레임 (뒤에서 {n}개 삭제)\n\n"
                   "되돌릴 수 없습니다. 진행할까요?").format(
                       f=Path(path).name, d=demo, a=self.win.playback.trim_n, b=keep, n=n_trim),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            new_n = trim_tail(path, demo, n_trim)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self.win, tr("다듬기 실패"), f"{type(e).__name__}: {e}")
            self.win.log(f"[트림 실패] {Path(path).name} {demo}: {type(e).__name__}: {e}")
            return
        self.win.log(f"[트림] {Path(path).name} {demo}: {self.win.playback.trim_n} → {new_n}프레임 "
                 f"(−{n_trim})")
        self.win.dataset_ops.refresh_dataset_tree()
        self.win.stats_ops.refresh_analysis(force=True)
        self.show_trim_for(path, demo)

    # -------------------------------------------------------------- playback
    def play_episode(self, path: str, demo: str) -> None:
        """Dataset 트리와 Analysis 순위표가 공유하는 재생 진입점."""
        if self.win.playback.play_key == (path, demo):
            self.win.center_tabs.setCurrentIndex(1)
            return
        if self.win.session.active_file_path is not None and Path(path) == self.win.session.active_file_path:
            self.win.play_caption.setText(tr("수집 중인 파일은 재생할 수 없습니다."))
            return
        self.stop_playback()
        self.win.playback.play_key = (path, demo)
        self.win.play_caption.setText(tr("불러오는 중... {d}").format(d=demo))
        self.win.center_tabs.setCurrentIndex(1)
        if self.win.playback.play_loader is not None:
            self.win.playback.play_loader.wait()
        self.win.playback.play_loader = EpisodeLoadWorker(path, demo)
        self.win.playback.play_loader.loaded.connect(self.on_episode_loaded)
        self.win.playback.play_loader.failed.connect(
            lambda m: self.win.play_caption.setText(tr("재생 실패: {m}").format(m=m)))
        self.win.playback.play_loader.start()

    @pyqtSlot(str, str, object, object)
    def on_episode_loaded(self, path, demo, agent, wrist) -> None:
        if self.win.playback.play_key != (path, demo):
            return
        self.win.playback.play_frames = {"agent": agent, "wrist": wrist}
        n = len(agent) if agent is not None else len(wrist)
        self.win.play_slider.blockSignals(True)
        self.win.play_slider.setRange(0, max(0, n - 1))
        self.win.play_slider.setValue(0)
        self.win.play_slider.blockSignals(False)
        self.win.play_slider.setEnabled(True)
        self.win.play_btn.setEnabled(True)
        self.win.play_btn.setText(tr("일시정지"))
        self.apply_speed()
        self.refresh_play_caption()
        self.show_frame(0)
        self.win.playback.play_timer.start()

    def stop_playback(self) -> None:
        self.win.playback.play_timer.stop()
        self.win.playback.play_frames = {"agent": None, "wrist": None}
        self.win.playback.play_key = None
        self.win.play_btn.setEnabled(False)
        self.win.play_btn.setText(tr("재생"))
        self.win.play_slider.setEnabled(False)
        self.win.play_pos.setText("-/-")
        for v in self.win.play_views.values():
            v.clear_frame(tr("에피소드를 선택하세요"))

    def speed(self) -> float:
        data = self.win.speed_combo.currentData()
        return float(data) if data else 1.0

    def apply_speed(self) -> None:
        interval = max(1, int(round(1000.0 / (PLAYBACK_FPS * self.speed()))))
        self.win.playback.play_timer.setInterval(interval)

    def on_speed_changed(self) -> None:
        self.apply_speed()
        self.refresh_play_caption()

    def refresh_play_caption(self) -> None:
        if not self.win.playback.play_key:
            return
        path, demo = self.win.playback.play_key
        n = self.win.play_slider.maximum() + 1
        speed = self.speed()
        eff = PLAYBACK_FPS * speed
        self.win.play_caption.setText(
            f"{Path(path).name} · {demo} · {n} frames · "
            + (tr("{s:g}배속 ({f:g} fps)").format(s=speed, f=eff) if speed != 1
               else tr("{f:g} fps (실제 속도)").format(f=eff)))

    def on_play_toggle(self) -> None:
        if self.win.playback.play_timer.isActive():
            self.win.playback.play_timer.stop()
            self.win.play_btn.setText(tr("재생"))
        else:
            self.win.playback.play_timer.start()
            self.win.play_btn.setText(tr("일시정지"))

    def on_play_tick(self) -> None:
        n = self.win.play_slider.maximum() + 1
        if n > 1:
            self.win.play_slider.setValue((self.win.play_slider.value() + 1) % n)

    def show_frame(self, i: int) -> None:
        for key, view in self.win.play_views.items():
            frames = self.win.playback.play_frames.get(key)
            if frames is not None and i < len(frames):
                view.set_frame(frames[i])
        self.win.play_pos.setText(f"{i + 1}/{self.win.play_slider.maximum() + 1}")

    # -------------------------------------------------------------- analysis
    def on_rank_play(self) -> None:
        items = self.win.rank_tree.selectedItems()
        if not items:
            return
        path, demo = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.play_episode(path, demo)

    # --------------------------------------------------------------- gallery
    def on_gallery_replay(self) -> None:
        if self.replay_running():      # 토글: 재생 중이면 중단 버튼이다
            self.on_replay_stop()
            return
        picks = [item.data(Qt.ItemDataRole.UserRole)
                 for item in self.win.gallery_list.selectedItems()]
        picks = [d for d in picks if d]
        if len(picks) != 1:
            QMessageBox.information(
                self.win, tr("선택 필요"),
                tr("실로봇 재생은 에피소드 하나만 선택하세요."))
            return
        self.replay_on_robot(picks[0][0], picks[0][1])

    # ---------------------------------------------------------------- replay
    def replay_running(self) -> bool:
        return (self.win.procs.replay_process is not None and
                self.win.procs.replay_process.state() != QProcess.ProcessState.NotRunning)

    def on_replay_selected(self) -> None:
        if self.replay_running():      # 토글: 재생 중이면 중단 버튼이다
            self.on_replay_stop()
            return
        picks = [(Path(i.parent().data(0, Qt.ItemDataRole.UserRole)),
                  i.data(0, Qt.ItemDataRole.UserRole))
                 for i in self.win.dataset_tree.selectedItems()
                 if i.parent() is not None]
        if len(picks) != 1:
            QMessageBox.information(
                self.win, tr("선택 필요"),
                tr("실로봇 재생은 에피소드 하나만 선택하세요."))
            return
        self.replay_on_robot(str(picks[0][0]), picks[0][1])

    def replay_on_robot(self, path: str, demo: str) -> None:
        """Dataset 트리와 Gallery 가 공유하는 실로봇 재생 진입점.

        replay_episode.py 를 --yes 로 하위 프로세스 실행한다 (램프·틱당
        클램프 같은 안전장치는 스크립트 쪽에 있다). 로봇을 쥐는 것은 결국
        로봇 노드 하나이므로, 여기서는 GUI 세션과의 충돌만 막는다.
        """
        if self.win.worker is not None:
            QMessageBox.warning(self.win, tr("재생 불가"),
                                tr("수집 세션 중에는 실로봇 재생을 할 수 "
                                   "없습니다. 먼저 세션을 종료하세요."))
            return
        busy = self.win.dataset_ops.busy_reason()
        if busy:
            QMessageBox.warning(self.win, tr("재생 불가"),
                                tr("{w} 이(가) 파일을 사용 중입니다. 끝난 뒤 "
                                   "다시 시도하세요.").format(w=busy))
            return
        if self.win.procs.replay_process is not None and \
                self.win.procs.replay_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self.win, tr("이미 재생 중"),
                                    tr("이전 재생이 끝나기를 기다리거나 '재생 "
                                       "중단'을 누르세요."))
            return
        speed, ok = QInputDialog.getDouble(
            self.win, tr("실로봇 재생"),
            tr("재생 배속 (0.1~1.0, 첫 재생은 0.5 권장)"),
            0.5, 0.1, 1.0, 1)
        if not ok:
            return
        ans = QMessageBox.warning(
            self.win, tr("로봇이 움직입니다"),
            tr("{d} ({f}) 을(를) {s}배속으로 실로봇에서 재현합니다.\n\n"
               "· 로봇 노드가 켜져 있어야 합니다\n"
               "· 로봇이 시작 포즈로 이동한 뒤 바로 재생됩니다\n"
               "· 주변 공간을 비우고, 비상정지를 준비하세요\n\n"
               "시작할까요?").format(d=demo, f=Path(path).name, s=speed),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        proc = QProcess(self.win)
        proc.setProgram(sys.executable)
        proc.setArguments([REPLAY_SCRIPT, path, demo,
                           "--speed", f"{speed:g}", "--yes"])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self.win._pipe(proc, "[실로봇 재생]", "log"))
        proc.finished.connect(self.on_replay_finished)
        self.win.procs.replay_process = proc
        self.win.log(f"[실로봇 재생] ▶ {Path(path).name} / {demo} ({speed:g}x)")
        proc.start()
        self.set_replay_ui(True)

    def on_replay_stop(self) -> None:
        """재생 하위 프로세스를 끊는다. 로봇 노드의 레퍼런스 필터가 현재
        포즈를 유지하므로(Ctrl-C 와 동일) 팔이 낙하하지는 않는다."""
        proc = self.win.procs.replay_process
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        self.win.log(tr("[실로봇 재생] 중단 요청 — 현재 포즈에서 정지합니다"))
        proc.terminate()
        if not proc.waitForFinished(2000):
            proc.kill()

    def set_replay_ui(self, running: bool) -> None:
        """재생/중단 토글 -- 두 진입점(Dataset·Gallery) 버튼이 함께 바뀐다."""
        for b, idle_text in ((getattr(self.win, "replay_btn", None),
                              tr("선택 재생 (실로봇)")),
                             (getattr(self.win, "gallery_replay_btn", None),
                              tr("실로봇 재생"))):
            if b is None:
                continue
            b.setText(tr("■ 재생 중단") if running else idle_text)
            b.setStyleSheet(
                "background-color:#c0392b; color:white;" if running else "")

    def on_replay_finished(self, code: int, _status) -> None:
        self.win.procs.replay_process = None
        self.set_replay_ui(False)
        self.win.log(tr("[실로봇 재생] {r} (exit={c})").format(
            r=tr("완료") if code == 0 else tr("중단/실패 — 로그 확인"), c=code))

    # ---------------------------------------------------------------- session
    @pyqtSlot(list)
    def on_episode_list(self, episodes) -> None:
        prev_n = len(self.win.session.active_episode_cache) if self.win.session.active_episode_cache else None
        self.win.session.active_episode_cache = episodes
        if self.win._pending_scene_deletes > 0 and self.win.session.scene_session:
            # 목록이 줄어든 emit 만 삭제 완료로 센다 -- 사이에 낀 저장/재판정
            # emit(개수 불변·증가)이 카운터를 잘못 소진하지 않게. 삭제 1걧마다
            # renumber 로 uid 가 재배정되므로 매번 통째로 무효화한다.
            if prev_n is not None and len(episodes) < prev_n:
                self.win._pending_scene_deletes = max(
                    0, self.win._pending_scene_deletes - (prev_n - len(episodes)))
                try:
                    # 파일은 saver 가 잠그고 있다 -- 다시 열지 않고 세션 설정에서
                    # scene_id 를 얻는다 (_session_scene_id).
                    sid = self.win.scene_ops.session_scene_id()
                    n_thumbs = invalidate_scene_thumbs(sid) if sid else 0
                    if n_thumbs:
                        self.win.log(f"[썸네일] {sid}: {n_thumbs}개 캐시 무효화")
                except Exception as e:  # noqa: BLE001
                    self.win.log(f"[썸네일 캐시 정리 실패] {e}")
        self.win.dataset_ops.refresh_dataset_tree()
        if self.win.session.scene_session:
            # 저장/재판정마다 saver 가 새 목록을 보내온다 -- slot 카운트 갱신
            self.win.scene_ops.refresh_slot_panel()
            self.win.scene_ops.refresh_start_plan_combo()   # Configure 쪽 카운트도 동기화

    # -------------------------------------------------------------- hdf5 view
    def on_show_structure(self) -> None:
        path = self.win.dataset_ops.selected_file()
        if path is None:
            QMessageBox.information(self.win, tr("선택 필요"), tr("파일을 선택하세요."))
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
            self.win._alert(tr("Scene 구조"), text, icon=QMessageBox.Icon.Information)
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
        self.win.log("\n".join(lines), view="validation")
        self.win.bottom_tabs.setCurrentWidget(self.win.validation_view)
