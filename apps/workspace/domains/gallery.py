"""Gallery tab: scene episode thumbnails, filter, and activation."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QListWidgetItem

from gello.gui.i18n import tr
from gello.gui.workers import GalleryLoadWorker
from gello.scene.scene_format import iter_scene_files


class GalleryOps:
    """Gallery tab: scene episode thumbnails, filter, and activation."""

    def __init__(self, win) -> None:
        self.win = win

    def refresh_gallery_scenes(self) -> None:
        combo = self.win.gallery_scene_combo
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        try:
            for p in iter_scene_files(self.win.dataset_ops.dataset_root()):
                combo.addItem(p.name, str(p))
        except Exception:  # noqa: BLE001
            pass
        idx = combo.findData(cur)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)
        self.refresh_gallery()

    def refresh_gallery(self, *_args) -> None:
        path = self.win.gallery_scene_combo.currentData()
        self.win.gallery_list.clear()
        self.win._gallery_episodes = []
        if not path:
            self.win.gallery_status.setText(tr("표시할 scene 파일이 없습니다"))
            return
        if self.win.session.active_file_path is not None and Path(path) == self.win.session.active_file_path:
            # HDF5 잠금 -- 실패한 로드 대신 이유와 다음 행동을 말한다
            self.win.gallery_status.setText(tr(
                "수집 세션이 이 scene 파일을 사용 중입니다 — 세션을 종료하면 "
                "갤러리가 열립니다. (현황은 Collect 페이지 slot 패널에)"))
            return
        self.win.gallery_status.setText(tr("불러오는 중... (첫 로드는 썸네일 생성으로 수 초)"))
        if self.win._gallery_loader is not None:
            self.win._gallery_loader.wait()
        self.win._gallery_loader = GalleryLoadWorker(path)
        self.win._gallery_loader.loaded.connect(self.on_gallery_loaded)
        self.win._gallery_loader.failed.connect(
            lambda m: self.win.gallery_status.setText(tr("갤러리 로드 실패: {m}").format(m=m)))
        self.win._gallery_loader.start()

    def on_gallery_loaded(self, path, episodes, ref_thumb) -> None:
        if path != self.win.gallery_scene_combo.currentData():
            return  # 로드 중 scene 을 바꿨다
        self.win._gallery_episodes = episodes
        # instruction 필터 항목 재구성 (선택 유지)
        cur = self.win.gallery_filter_combo.currentData()
        self.win.gallery_filter_combo.blockSignals(True)
        self.win.gallery_filter_combo.clear()
        self.win.gallery_filter_combo.addItem(tr("(모든 instruction)"), None)
        for iid, instr in sorted({(e["instruction_id"], e["instruction"])
                                  for e in episodes}):
            self.win.gallery_filter_combo.addItem(f"{iid} · {instr[:44]}", iid)
        idx = self.win.gallery_filter_combo.findData(cur)
        self.win.gallery_filter_combo.setCurrentIndex(max(0, idx))
        self.win.gallery_filter_combo.blockSignals(False)
        self._ref_thumb = ref_thumb
        self.apply_gallery_filter()

    def apply_gallery_filter(self, *_args) -> None:
        want = self.win.gallery_filter_combo.currentData()
        path = self.win.gallery_scene_combo.currentData()
        self.win.gallery_list.clear()
        if getattr(self, "_ref_thumb", None):
            it = QListWidgetItem(QIcon(self._ref_thumb), tr("기준 사진"))
            it.setData(Qt.ItemDataRole.UserRole, None)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.win.gallery_list.addItem(it)
        shown = 0
        for e in self.win._gallery_episodes:
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
            self.win.gallery_list.addItem(it)
            shown += 1
        n_ok = sum(1 for e in self.win._gallery_episodes
                   if e["quality_status"] == "success")
        self.win.gallery_status.setText(
            tr("{s}개 표시 (전체 {n}개 · success {ok}개) — 더블클릭: 재생, "
               "선택 후 재판정 버튼: 성공↔실패").format(
                   s=shown, n=len(self.win._gallery_episodes), ok=n_ok))

    def on_gallery_activated(self, item) -> None:
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            self.win.playback_ops.play_episode(d[0], d[1])
