"""Statistics and analysis operations for WorkspaceWindow."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QTreeWidgetItem,
    QVBoxLayout,
)

from gello.data.episode_stats import (
    TASK_DEV_LIMIT,
    hdf5_files,
    load_series,
    scan_dataset,
    summarize,
)
from gello.data.collection_history import (
    SessionRecord,
    append_session,
    history_path,
    leaderboard,
    load_sessions,
    now_iso,
)
from gello.gui.i18n import tr
from gello.scene.scene_format import iter_scene_files

#: 저장 경로 여유가 이보다 적으면 상태바가 주황(주의) / 빨강(위험)이 된다.
#: 에피소드 하나가 60MB 안팎이라 10GB 면 한 세션이 안 들어간다.
DISK_WARN_GB = 50.0
DISK_CRITICAL_GB = 10.0

#: 데이터가 바뀐 뒤 자동 재분석까지 기다리는 시간. 삭제 한 번이 신호 여러
#: 개를 내므로 그것들을 한 번으로 뭉치는 것이 목적이다.
ANALYSIS_DEBOUNCE_MS = 2000

#: 최근 세션 목록에 보여줄 줄 수. 그 아래는 순위표가 요약한다.
HISTORY_ROWS = 30


def _bold_row(item) -> None:
    """트리 한 줄 전체를 굵게. 항목은 위젯이 아니라 칸마다 글꼴을 들고 있어
    (fonts.set_bold 처럼) 위젯 하나를 고칠 수가 없다."""
    font = item.font(0)
    font.setBold(True)
    for c in range(item.columnCount()):
        item.setFont(c, font)


class StatsOps:
    """Session counters and dataset analysis for the workspace."""

    def __init__(self, win) -> None:
        self.win = win

    def connect_progress(self, waited: float) -> None:
        self.win.statusBar().showMessage(
            tr("카메라 정리 중... {s:.0f}초 (정리되면 자동으로 연결합니다)").format(s=waited),
            1000)
        self.win.lights["camera"].set("busy", tr("정리 중"))

    def log_progress(self, msg: str, view: str) -> None:
        """Progress that overwrites its own last line instead of stacking.

        A 1.3 GB upload prints a bar every second; appended, that buries every
        other message in the tab and makes the log useless exactly while a long
        job is running. Replacing the previous progress line keeps one live line
        and leaves the surrounding log readable.

        Deliberately not written to the log file -- the file is what gets read
        after a crash, and hundreds of superseded percentages help nobody there.
        """
        target = self.win._view(view)
        if self.win._progress_line.get(view):
            cursor = target.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(msg)
        else:
            target.appendPlainText(msg)
            self.win._progress_line[view] = True

    def bump(self, key: str, n: int = 1) -> None:
        """카운터 하나를 이번 task 와 누적 양쪽에 올린다.

        두 dict 를 따로 건드리면 반드시 한쪽만 올리는 자리가 생긴다 -- 판정
        뒤집기처럼 -1 도 있는 경로가 섞여 있어서 더 그렇다.
        """
        self.win.session.counters[key] += n
        self.win.session.cumulative[key] += n

    def refresh_stats(self) -> None:
        for stats, labels in ((self.win.session.counters, self.win.stats_labels),
                              (self.win.session.cumulative, self.win.stats_total_labels)):
            elapsed = time.monotonic() - stats["t0"]
            for key in ("saved", "success", "failed", "discarded", "frames"):
                labels[key].setText(str(stats[key]))
            labels["elapsed"].setText(f"{elapsed / 60:.1f} min")
            # 30초 미만에서는 분당 환산이 의미 없는 큰 수로 튄다.
            rate = stats["saved"] / (elapsed / 60) if elapsed > 30 else 0.0
            labels["rate"].setText(f"{rate:.2f}")
        # 어느 task 의 숫자인지 헤더에 박아 둔다. task 를 여러 개 도는 동안
        # 왼쪽 열이 무엇을 세고 있는지가 패널만 보고 답이 되어야 한다.
        task = self.win._current_task_label(limit=20)
        self.win.stats_task_header.setText(task or tr("이번 task"))
        self.win.stats_task_header.setToolTip(self.win._current_task_label())

    # ------------------------------------------------------------ 디스크
    def refresh_disk(self) -> None:
        """저장 경로의 남은 용량을 상태바에 적는다.

        Statistics 의 '디스크' 상자에서 옮겨 왔다 (2026-09-06 사용자 요청) --
        화면을 옮겨야 보이는 값이라 정작 수집 중에는 아무도 안 봤고, 다 찬
        것을 아는 시점이 저장에 실패한 뒤였다. 상태바는 늘 떠 있다.

        문턱은 이 데이터셋의 실제 크기에서 나왔다: 에피소드 하나가 20Hz
        20초 × 256² RGB 두 대 ≈ 60MB 라, 10GB 면 한 세션(150여 개)이 안
        들어가고 20GB 면 아슬아슬하다.
        """
        try:
            usage = shutil.disk_usage(
                self.win.root_edit.text().strip() or str(Path.home()))
        except OSError:
            self.win.sb_disk.setText(tr("여유 -"))
            self.win.sb_disk.setStyleSheet("color:#888;")
            return
        free_gb = usage.free / 1e9
        self.win.sb_disk.setText(tr("여유 {f:.0f} GB / {t:.0f} GB").format(
            f=free_gb, t=usage.total / 1e9))
        color = ("#e74c3c" if free_gb < DISK_CRITICAL_GB
                 else "#f39c12" if free_gb < DISK_WARN_GB else "#888")
        self.win.sb_disk.setStyleSheet(
            f"color:{color};" + (" font-weight:bold;" if free_gb < DISK_CRITICAL_GB else ""))

    # -------------------------------------------------------- 수집 이력
    def record_session(self) -> None:
        """방금 끝난 연결 세션을 이력 파일에 한 줄로 남긴다.

        Disconnect 마다 부른다. 아무것도 저장하지 않은 세션은 남기지 않는다
        -- 연결만 했다 끊은 것은 수집이 아니고, 그런 줄이 섞이면 세션 수로
        나누는 값이 전부 흐려진다.

        시간은 ``counters["t0"]`` 에서 온다. 그 값은 on_connected 에서
        0 으로 돌아가므로 정확히 이번 연결 구간이고, 그 구간이 곧 조작자가
        자리에 앉아 있던 시간이다 (리셋·재배치 포함).
        """
        st = self.win.session.counters
        if not st["saved"] or self.win.session.history_written:
            return
        self.win.session.history_written = True
        rec = SessionRecord(
            run=self.win.run_id,
            started=self.win.session.started_iso or now_iso(),
            ended=now_iso(),
            collector=self.win.collector_edit.text().strip(),
            # 이 세션이 **실제로 쓴** 폴더. Dataset 화면의 폴더 선택
            # (dataset_root) 은 구경하러 다른 데를 볼 수 있어서, 그것으로
            # 이름을 붙이면 엉뚱한 데이터셋의 순위에 줄이 들어간다.
            dataset=self._session_dataset(),
            # 아직 worker 가 살아 있는 동안 불린다 (on_worker_finished 의
            # 맨 앞) -- 그래야 이 세션이 무슨 scene 이었는지 알 수 있다.
            scene=str(self.win.scene_ops.session_scene_id() or ""),
            station=getattr(self.win, "station_name", ""),
            saved=int(st["saved"]), success=int(st["success"]),
            failed=int(st["failed"]), discarded=int(st["discarded"]),
            frames=int(st["frames"]),
            seconds=max(0.0, time.monotonic() - st["t0"]),
        )
        try:
            append_session(rec)
        except OSError as e:
            # 이력을 못 남기는 것이 수집을 막을 이유는 못 된다 -- 로그로만.
            self.win.log(f"[이력] 저장 실패: {e}")
            return
        self.win.log(tr("[이력] {n}개 / {m:.0f}분 (분당 {r:.2f}) — {p}").format(
            n=rec.saved, m=rec.seconds / 60, r=rec.per_minute, p=history_path()))
        self.refresh_history()

    def _session_dataset(self) -> str:
        """순위표와 이력이 말하는 "이 데이터셋"의 이름 = 수집 저장 경로의
        폴더명. 기록 중이면 그 파일이 놓인 폴더가 더 정확하다 -- 세션 도중에
        저장 경로 칸을 건드려도 이미 쓰고 있는 파일은 안 옮겨간다."""
        path = self.win.session.active_file_path
        if path is not None:
            return Path(path).parent.name
        return Path(self.win.root_edit.text().strip() or ".").name

    def refresh_history(self) -> None:
        """이력 파일을 읽어 순위표와 최근 세션 목록을 채운다.

        순위표는 **지금 데이터셋**만 센다. task 가 다르면 에피소드 하나에
        드는 시간이 다르므로, 다른 데이터셋의 속도와 한 줄에 세우면 비교가
        아니라 착시가 된다 (collection_history.leaderboard 참고).
        """
        if not hasattr(self.win, "board_tree"):
            return
        rows = load_sessions()
        dataset = self._session_dataset()
        self.win.board_tree.clear()
        me = self.win.collector_edit.text().strip()
        board = leaderboard(rows, dataset=dataset)
        for rank, pace in enumerate(board, start=1):
            item = QTreeWidgetItem([
                f"{rank}. {pace.collector}",
                f"{pace.per_minute:.2f}",
                str(pace.saved),
                f"{100 * pace.success_rate:.0f}%",
                f"{pace.seconds / 3600:.1f}h",
            ])
            item.setToolTip(0, tr("세션 {s}개 · 마지막 {l}").format(
                s=pace.sessions, l=pace.last or "-"))
            # 내 줄만 굵게. 순위표를 보는 이유가 "나는 어디쯤인가"라서,
            # 줄이 늘어나면 그 답을 찾는 것부터 일이 된다.
            if me and pace.collector == me:
                _bold_row(item)
            self.win.board_tree.addTopLevelItem(item)
        self.win.board_hint.setText(
            tr("{d} · 수집자 {n}명 · 세션 {s}개").format(
                d=dataset or "-", n=len(board), s=sum(p.sessions for p in board))
            if board else
            tr("{d} 의 이력이 아직 없습니다 — 세션을 끝내면(Disconnect) 한 줄이 쌓입니다.")
            .format(d=dataset or "-"))

        self.win.history_tree.clear()
        for rec in reversed(rows[-HISTORY_ROWS:]):
            here = rec.run == self.win.run_id
            item = QTreeWidgetItem([
                (tr("이번 실행") if here else rec.started[5:16].replace("T", " ")),
                rec.collector or "-",
                str(rec.saved),
                f"{rec.seconds / 60:.0f}m",
                f"{rec.per_minute:.2f}",
            ])
            item.setToolTip(0, f"{rec.dataset} · {rec.scene or '-'} · {rec.started}")
            if here:
                _bold_row(item)
            self.win.history_tree.addTopLevelItem(item)

    def on_summary(self, summary) -> None:
        # 해제는 여기서 하지 않는다 -- 정상 종료에만 오는 신호다. 실제 해제는
        # 모든 종료 경로에서 오는 finished(collection.on_worker_finished)가 맡는다.
        self.win.log(f"[세션 요약] {summary}")

    # ------------------------------------------------------------- 재분석
    def mark_stats_stale(self) -> None:
        """데이터가 바뀌었다고 표시하고, 곧 다시 분석하도록 예약한다.

        저장·삭제·재판정·트림·폴더 변경이 전부 여기로 온다. 예약이 한 번에
        몰리지 않게 단발 타이머를 다시 걸어 뭉갠다 -- 실패만 골라 20개를
        지우면 신호가 20번 오는데, 그때마다 전체 스캔을 돌 이유가 없다.
        """
        self.win.session.stats_stale = True
        timer = getattr(self.win, "analysis_timer", None)
        if timer is not None:
            timer.start(ANALYSIS_DEBOUNCE_MS)

    def auto_refresh_analysis(self) -> None:
        """조작자가 [다시 분석] 을 누르지 않아도 최신값이 보이게 한다
        (2026-09-06 사용자 요청).

        두 가지를 지킨다:

        * **세션 중에는 안 돈다.** 스캔은 파일을 읽기로 여는데 기록 중인
          파일은 saver 가 쥐고 있어서, 열리지 않으면 ``scan_dataset`` 이
          그 파일을 조용히 건너뛴다 -- 지금 찍고 있는 것만 빠진 통계가
          가장 나쁘다. 세션이 끝나면 그 자리에서 다시 돈다.
        * **보고 있지 않으면 안 돈다.** 결과가 보이는 화면은 Statistics 와
          Dataset(Analysis 탭) 뿐이다. Configure 에서 도는 스캔은 아무도
          못 보면서 디스크만 훑는다.
        """
        if self.win.worker is not None:
            return
        if self.win._activity not in ("stats", "dataset"):
            return
        self.refresh_analysis()

    def refresh_analysis(self, force: bool = False) -> None:
        """Rescans every .hdf5's actions. Only a few KB per episode, so this is
        rebuilt from disk rather than cached -- a cache would go stale the
        moment a session records another take.

        ``force`` 는 조작자가 직접 [다시 분석] 을 누른 경우다. 그 밖에는
        마지막 스캔 뒤에 데이터가 바뀌었을 때만 돈다 -- 화면을 오갈 때마다
        같은 스캔을 되풀이하면 트리를 고를 때마다 몇백 ms 씩 멎는다.
        """
        if not force and self.win.session.stats and not self.win.session.stats_stale:
            return
        # Dataset 페이지의 폰더 선택을 따른다 (수집 경로 하드코딩 제거) --
        # scene 파일도 함께 스캔한다.
        root = self.win.dataset_ops.dataset_root()
        files = hdf5_files(root) + [str(p) for p in iter_scene_files(root)]
        if not files:
            self.win.session.stats_stale = False
            self.win.analysis_summary.setText(
                tr("{r} 에 *_demo.hdf5 / scene_*.hdf5 가 없습니다.").format(r=root))
            return
        t0 = time.monotonic()
        self.win.session.stats = scan_dataset(files)
        self.win.session.stats_stale = False
        self.win._summary = summarize(self.win.session.stats)
        dt = time.monotonic() - t0
        s = self.win._summary
        self.win.analysis_summary.setText(
            tr("에피소드 {n}개 · {f:,}프레임 · 그룹(scene·문장) {t}개 · 길이 {a}~{b}프레임\n{v}").format(
                n=s["n"], f=s["frames"], t=s["tasks"],
                a=s["len_min"], b=s["len_max"], v=s["verdict"]))
        self.win.log(f"[분석] {len(files)}개 파일 / {s['n']}개 에피소드 ({dt:.2f}s) — {s['verdict']}")

        self.win.dim_bars.set_rows(
            [(f"joint{i + 1}", float(s["per_dim_sigma"][i]), "") for i in range(7)])
        means = [e.mean_da for e in self.win.session.stats]
        self.win.da_hist.set_values(means, [(s["p50"], tr("중앙값")), (s["p99"], "p99")])

        lens = [e.seconds for e in self.win.session.stats]
        self.win.len_min_spin.blockSignals(True)
        self.win.len_max_spin.blockSignals(True)
        self.win.len_min_spin.setRange(0, int(max(lens) * 10) + 5)
        self.win.len_max_spin.setRange(0, int(max(lens) * 10) + 5)
        self.win.len_min_spin.setValue(0)
        self.win.len_max_spin.setValue(int(max(lens) * 10) + 5)
        self.win.len_min_spin.blockSignals(False)
        self.win.len_max_spin.blockSignals(False)
        self.refresh_group_combo()
        self.refresh_rank_list()

    def filtered_stats(self) -> list:
        lo = self.win.len_min_spin.value() / 10.0
        hi = self.win.len_max_spin.value() / 10.0
        if lo > hi:
            lo, hi = hi, lo
        self.win.len_label.setText(f"{lo:.1f}~{hi:.1f}s")
        # 선택 출처는 Dataset 트리 하나뿐이다. 파일 행을 고륾면 그 파일만,
        # 에피소드 행을 고륾면 그 부모 파일만 남긴다.
        path = None
        sel = self.win.dataset_tree.selectedItems() if hasattr(self.win, "dataset_tree") else []
        if sel:
            node = sel[0] if sel[0].parent() is None else sel[0].parent()
            v = node.data(0, Qt.ItemDataRole.UserRole)
            path = v if isinstance(v, str) and v.endswith(".hdf5") else None
        out = [e for e in self.win.session.stats if lo <= e.seconds <= hi]
        out = [e for e in out if path is None or e.path == path]
        grp = self.win.group_combo.currentData() if hasattr(self.win, "group_combo") else None
        if grp is not None:
            out = [e for e in out if e.group == grp]
        return out

    def refresh_group_combo(self) -> None:
        """Analysis 스캔 결과의 (scene·문장) 그룹으로 콤보를 채운다 -- 선택은
        가능하면 유지한다 (새로고침마다 (전체) 로 튀지 않게)."""
        if not hasattr(self.win, "group_combo"):
            return
        keep = self.win.group_combo.currentData()
        groups = sorted({e.group for e in self.win.session.stats})
        self.win.group_combo.blockSignals(True)
        self.win.group_combo.clear()
        self.win.group_combo.addItem(tr("(전체)"), None)
        for g in groups:
            n = sum(1 for e in self.win.session.stats if e.group == g)
            label = (f"{g[0]} · {g[1]}" if g[0] else g[1])
            self.win.group_combo.addItem(f"{label}  ({n})", g)
        idx = 0
        for i in range(self.win.group_combo.count()):
            if self.win.group_combo.itemData(i) == keep:
                idx = i
                break
        self.win.group_combo.setCurrentIndex(idx)
        self.win.group_combo.blockSignals(False)

    def refresh_rank_list(self) -> None:
        if not self.win.session.stats:
            return
        key = self.win.rank_combo.currentData()
        rows = self.filtered_stats()
        score = {
            "fast": lambda e: -e.task_dev,
            "slow": lambda e: e.task_dev,
            "still": lambda e: -e.still_frac,
            "short": lambda e: e.n_frames,
            "long": lambda e: -e.n_frames,
        }[key]
        rows = sorted(rows, key=score)[:60]
        self.win.rank_tree.clear()
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
            self.win.rank_tree.addTopLevelItem(item)
        self.win.stats_hint.setText(
            tr("{n}개 중 상위 {m}개 표시").format(n=len(self.filtered_stats()), m=len(rows)))

    def show_analysis_for(self, path: str, demo: str) -> None:
        """Dataset 트리와 순위표가 공유하는 곡선 표시 경로."""
        if not path or not demo:
            return
        try:
            series = load_series(path, demo)
        except Exception as e:  # noqa: BLE001
            self.win.log(f"[분석] 시계열 로드 실패: {type(e).__name__}: {e}")
            return
        for plot, dims in self.win.series_plots.values():
            plot.set_data(series, dims)
            plot.set_cursor(None)
        stat = next((e for e in self.win.session.stats if e.key == (path, demo)), None)
        if stat is not None:
            self.win.analysis_summary.setText(
                tr("{d} · {n}프레임 ({s:.1f}s) · 평균 |Δa| {m:.5f} · 같은 (scene·문장) 그룹 평균과 "
                   "{v:+.4f}{mark} · 멈춤 {p:.0f}%\n{t}").format(
                       d=demo, n=stat.n_frames, s=stat.seconds, m=stat.mean_da,
                       v=stat.task_dev,
                       mark=" (급함)" if stat.task_dev > TASK_DEV_LIMIT else (
                           " (느림)" if stat.task_dev < -TASK_DEV_LIMIT else ""),
                       p=100 * stat.still_frac, t=stat.group_label))
            self.win.da_hist.set_values(
                [e.mean_da for e in self.win.session.stats],
                [(self.win._summary["p50"], tr("중앙값")), (stat.mean_da, tr("이 에피소드"))])

    def on_rank_delete(self) -> None:
        """Hands the selection to the same delete path the Dataset panel uses --
        including its session-ownership and busy checks."""
        picks = [i.data(0, Qt.ItemDataRole.UserRole) for i in self.win.rank_tree.selectedItems()]
        if not picks:
            QMessageBox.information(self.win, tr("선택 필요"),
                                    tr("삭제할 에피소드를 선택하세요 (Ctrl/Shift로 여러 개)."))
            return
        by_file: dict = {}
        for path, demo in picks:
            by_file.setdefault(Path(path), []).append(demo)
        if self.win.dataset_ops.delete_episodes(by_file):
            self.refresh_analysis()

    def on_metric_help(self) -> None:
        """Shows docs/curation-metrics.md rather than a copy of it.

        The thresholds in that file are the ones episode_stats.py actually
        uses; a second prose copy inside the GUI would be the version that
        goes stale first, and the operator would have no way to tell which of
        the two was lying.
        """
        doc = Path(__file__).resolve().parents[4] / "docs" / "curation-metrics.md"
        try:
            body = doc.read_text(encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self.win, tr("지표 설명"),
                                tr("{p} 를 읽을 수 없습니다: {e}").format(p=doc, e=e))
            return
        dlg = QDialog(self.win)
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
