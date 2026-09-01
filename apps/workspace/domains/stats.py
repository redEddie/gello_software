"""Statistics and analysis operations for WorkspaceWindow."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from PyQt6.QtCore import Qt
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
from gello.gui.i18n import tr
from gello.scene.scene_format import iter_scene_files


class StatsOps:
    """Session counters and dataset analysis for the workspace."""

    def __init__(self, win) -> None:
        self.win = win

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
        try:
            usage = shutil.disk_usage(self.win.root_edit.text().strip() or str(Path.home()))
            self.win.disk_label.setText(f"{usage.free / 1e9:.1f} GB / {usage.total / 1e9:.0f} GB")
        except OSError:
            self.win.disk_label.setText("-")

    def on_summary(self, summary) -> None:
        # 해제는 여기서 하지 않는다 -- 정상 종료에만 오는 신호다. 실제 해제는
        # 모든 종료 경로에서 오는 finished(_on_worker_finished)가 맡는다.
        self.win.log(f"[세션 요약] {summary}")

    def refresh_analysis(self, force: bool = False) -> None:
        """Rescans every .hdf5's actions. Only a few KB per episode, so this is
        rebuilt from disk rather than cached -- a cache would go stale the
        moment a session records another take."""
        # Dataset 페이지의 폰더 선택을 따른다 (수집 경로 하드코딩 제거) --
        # scene 파일도 함께 스캔한다.
        root = self.win.dataset_ops.dataset_root()
        files = hdf5_files(root) + [str(p) for p in iter_scene_files(root)]
        if not files:
            self.win.analysis_summary.setText(
                tr("{r} 에 *_demo.hdf5 / scene_*.hdf5 가 없습니다.").format(r=root))
            return
        t0 = time.monotonic()
        self.win.session.stats = scan_dataset(files)
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
        doc = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "curation-metrics.md"
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
