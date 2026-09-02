"""Layout reference image unpacking, slideshow, and overlay blending."""

from __future__ import annotations

import shutil

import numpy as np

from apps.workspace.constants import LAYOUT_DIR, LAYOUT_ZIP
from gello.data.crop import resize_rgb
from gello.gui.grid_overlay import draw_alignment_grid
from gello.gui.i18n import tr


class LayoutRefOps:
    """LIBERO layout reference slideshow and camera overlay."""

    def __init__(self, win) -> None:
        self.win = win

    def on_grid_alpha(self, val):
        self.win._on_grid_alpha(val)

    def on_grid_alpha_done(self):
        self.win._on_grid_alpha_done()

    def on_edit_grid(self):
        self.win._on_edit_grid()

    def ensure_layout_refs(self) -> bool:
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
            self.win.log(f"[레이아웃] 참조 이미지 압축 해제: {LAYOUT_DIR}")
            return True
        except Exception as e:  # noqa: BLE001
            self.win.log(f"[레이아웃] 압축 해제 실패: {type(e).__name__}: {e}")
            return False

    def layout_reload(self) -> None:
        """Scans the extracted tree and fills the suite filter."""
        if not self.ensure_layout_refs():
            self.win.layout_name_label.setText(
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
        self.win._layout_all_entries = entries
        cur = self.win.layout_suite_combo.currentText()
        self.win.layout_suite_combo.blockSignals(True)
        self.win.layout_suite_combo.clear()
        self.win.layout_suite_combo.addItem(tr("(전체)"), None)
        for s in suites:
            self.win.layout_suite_combo.addItem(s, s)
        i = self.win.layout_suite_combo.findText(cur)
        self.win.layout_suite_combo.setCurrentIndex(max(0, i))
        self.win.layout_suite_combo.blockSignals(False)
        self.layout_refilter()

    def layout_refilter(self) -> None:
        suite = self.win.layout_suite_combo.currentData()
        all_entries = getattr(self.win, "_layout_all_entries", [])
        self.win._layout_entries = [e for e in all_entries
                                    if suite is None or e[0] == suite]
        self.win._layout_idx = 0
        self.layout_show()

    def layout_step(self, delta: int, user: bool = True) -> None:
        if not self.win._layout_entries:
            return
        self.win._layout_idx = (self.win._layout_idx + delta) % len(self.win._layout_entries)
        if user and self.win._layout_timer.isActive():
            self.win._layout_timer.start()      # 수동 이동 시 타이머 리셋
        self.layout_show()

    def layout_toggle_play(self) -> None:
        self.win.playback.layout_playing = not self.win.playback.layout_playing
        self.win.layout_play_btn.setText(
            tr("일시정지") if self.win.playback.layout_playing else tr("재생"))
        if self.win.playback.layout_playing and \
                self.win.center_tabs.currentIndex() == self.win._layout_tab_index:
            self.win._layout_timer.start()
        else:
            self.win._layout_timer.stop()

    def layout_apply_interval(self) -> None:
        sec = self.win.layout_interval_combo.currentData() or 5
        self.win._layout_timer.setInterval(int(sec) * 1000)

    def layout_show(self) -> None:
        if not self.win._layout_entries:
            for v in self.win.layout_overlay_views.values():
                v.clear_frame(tr("참조 이미지 없음"))
            for v in self.win.layout_strip_views.values():
                v.clear_frame("")
            self.win.layout_name_label.setText("")
            return
        import cv2
        suite, name, ap, wp = self.win._layout_entries[self.win._layout_idx]
        self.win.layout_name_label.setText(
            f"{suite} · {name}  ({self.win._layout_idx + 1}/{len(self.win._layout_entries)})")
        for role, path in (("agent", ap), ("wrist", wp)):
            bgr = cv2.imread(path)
            if bgr is None:
                self.win.cameras.layout_ref.pop(role, None)
                continue
            self.win.cameras.layout_ref[role] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            self.win.layout_strip_views[f"{role}_ref"].set_frame(self.win.cameras.layout_ref[role])
            self.layout_update_role(role)

    def layout_alpha_changed(self, val: int) -> None:
        self.win.layout_alpha_label.setText(tr("스틸 {v}%").format(v=val))
        for role in ("agent", "wrist"):
            self.layout_update_role(role)

    def layout_blink_toggled(self, on: bool) -> None:
        """번갈아 보기 -- 겹침 대신 카메라와 스틸을 0.5초씩 교대로 보여준다."""
        self.win.layout_alpha_slider.setEnabled(not on)
        self.win._layout_blink_state = False
        if on and self.win.center_tabs.currentIndex() == self.win._layout_tab_index:
            self.win._layout_blink_timer.start()
        else:
            self.win._layout_blink_timer.stop()
        for role in ("agent", "wrist"):
            self.layout_update_role(role)

    def layout_blink_tick(self) -> None:
        self.win._layout_blink_state = not self.win._layout_blink_state
        for role in ("agent", "wrist"):
            self.layout_update_role(role)

    def layout_update_role(self, role: str) -> None:
        """Re-blends one side. Called on slideshow advance, on every camera
        frame while the tab is visible, and when the alpha slider moves.

        카메라가 바닥, LIBERO 스틸이 그 위에 슬라이더만큼의 불투명도로 올라간다.
        카메라 프레임이 없으면 참조를 단독으로 보여주는 대신 그렇다고 말한다 --
        참조 단독은 "겹침이 안 되고 있다"는 사실을 숨긴다.
        """
        ref = self.win.cameras.layout_ref.get(role)
        if ref is None:
            return
        frame = self.win.cameras.last_cam_frame.get(role)
        if frame is None:
            self.win.layout_overlay_views[role].clear_frame(
                tr("카메라 없음 — Configure 에서 미리보기를 켜세요"))
            self.win.layout_strip_views[f"{role}_live"].clear_frame(tr("카메라 없음"))
            return
        p = self.win.cameras.crop_params[role]
        live = resize_rgb(frame, ref.shape[0], zoom=p["zoom"],
                          x_shift=p["x"], y_shift=p["y"])
        self.win.layout_strip_views[f"{role}_live"].set_frame(live)
        if self.win.layout_blink_check.isChecked():
            # 교대 모드: 위치 차이가 겹침보다 눈에 잘 띈다 (운동 시차 효과).
            shown = ref if self.win._layout_blink_state else live
        else:
            a = self.win.layout_alpha_slider.value()
            shown = ((live.astype(np.uint16) * (100 - a)
                      + ref.astype(np.uint16) * a) // 100).astype(np.uint8)
        if self.win.layout_grid_check.isChecked():
            shown = draw_alignment_grid(shown)
        self.win.layout_overlay_views[role].set_frame(shown)

    def layout_rerender(self) -> None:
        for role in ("agent", "wrist"):
            self.layout_update_role(role)

    def layout_blink_interval_changed(self, ms: int) -> None:
        self.win.layout_blink_label.setText(tr("전환 {s:.2f}초").format(s=ms / 1000))
        self.win._layout_blink_timer.setInterval(int(ms))
