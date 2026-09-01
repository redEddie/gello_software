"""Depth and point-cloud operations for WorkspaceWindow."""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSlot
from PyQt6.QtWidgets import QComboBox

import numpy as np

from apps.dialogs._image_utils import _depth_colormap
from gello.gui.gui_widgets import DepthCloudWorker
from gello.gui.i18n import tr


class DepthOps:
    """Depth map and point-cloud tab operations."""

    def __init__(self, win) -> None:
        self.win = win

    def depth_role_combo(self) -> QComboBox:
        return (self.win.depth_cam_combo if self.win.cameras.depth_consumer == "depth"
                else self.win.cloud_cam_combo)

    def depth_views(self) -> list:
        return [self.win.cloud_view, self.win.depth_view]

    def start_cloud(self) -> None:
        if self.win.worker is not None:
            for v in self.depth_views():
                v.clear_frame(tr("수집 세션 중에는 사용할 수 없습니다 — "
                                 "세션 종료 후 다시 여세요"))
            return
        role = self.depth_role_combo().currentData() or "agent"
        combo = self.win.agent_combo if role == "agent" else self.win.wrist_combo
        serial = self.win.camera_ops.combo_serial(combo)
        if not serial:
            for v in self.depth_views():
                v.clear_frame(
                    tr("Configure 에서 {r} 카메라를 선택하세요").format(r=role))
            return
        if self.win.cameras.cloud_worker is not None:
            if serial == self.win.cameras.cloud_serial and self.win.cameras.cloud_worker.isRunning():
                return                      # 같은 카메라 -- 탭만 바뀐 것
            # 다른 카메라거나, 오류로 죽은 워커가 남아 있는 경우(죽은 워커를
            # '살아있다'고 믿으면 탭을 다시 들어와도 스트림이 영영 안 선다)
            self.stop_cloud(restore_previews=False)
        # depth 파이프라인은 RGB 미리보기와 같은 장치를 두 번 열 수 없다.
        # OR-누적: 카메라 전환 재시작 때(미리보기 이미 내려간 상태) 복원
        # 약속을 잊지 않게 한다. 플래그는 실제 복원 때 리셋된다.
        self.win._cloud_previews_were_on = (self.win._cloud_previews_were_on
                                        or bool(self.win.agent_preview
                                                or self.win.wrist_preview))
        self.win.camera_ops.stop_previews_async()
        msg = tr("depth 스트림 여는 중... ({s})").format(s=serial)
        self.win.cloud_status.setText(msg)
        self.win.depth_status.setText(msg)
        self.win.camera_ops.ensure_camera_node()
        w = DepthCloudWorker(role, serial, mode=self.win.cameras.depth_consumer or "cloud")
        w.cloud_ready.connect(self.on_cloud)
        w.depth_ready.connect(self.on_depth_img)
        w.error.connect(self.on_depth_error)
        w.start()
        self.win.cameras.cloud_worker = w
        self.win.cameras.cloud_serial = serial

    @pyqtSlot(str)
    def on_depth_error(self, m: str) -> None:
        text = tr("depth 오류: {m}").format(m=m)
        self.win.cloud_status.setText(text)
        self.win.depth_status.setText(text)

    def on_cloud_cam_changed(self, *_args) -> None:
        if self.win.cameras.cloud_worker is None:      # 탭이 닫혀 있으면 다음 진입 때 반영
            return
        self.stop_cloud(restore_previews=False)  # 복원 약속(플래그)은 유지된다
        for v in self.depth_views():
            v.clear_frame(tr("카메라 전환 중..."))
        self.start_cloud()

    def stop_cloud(self, restore_previews: bool = True) -> None:
        w = self.win.cameras.cloud_worker
        if w is None:
            return
        self.win.cameras.cloud_worker = None
        self.win.cameras.cloud_serial = ""
        w.stop()
        w.wait(3000)
        self.win.cloud_status.setText(tr("depth 스트림 종료"))
        self.win.depth_status.setText(tr("depth 스트림 종료"))
        if restore_previews and self.win._cloud_previews_were_on \
                and self.win.worker is None:
            self.win._cloud_previews_were_on = False
            # 파이프라인이 놓이는 데 잠깐 걸린다 -- 바로 열으면 busy.
            QTimer.singleShot(700, lambda: (
                self.win.camera_ops.restart_previews() if self.win.worker is None
                and self.win.cameras.cloud_worker is None else None))

    @pyqtSlot(object, object)
    def on_cloud(self, pts, rgb) -> None:
        self.win.cameras.cloud_pts, self.win.cameras.cloud_rgb = pts, rgb
        if self.win.cameras.depth_consumer == "cloud":     # 보이는 탭만 렌더
            self.render_cloud()
            self.win.cloud_status.setText(
                tr("점 {n:,}개 · 회전/기울임 슬라이더로 시점 변경").format(n=len(pts)))

    @pyqtSlot(object)
    def on_depth_img(self, z) -> None:
        self.win.cameras.depth_img = z
        if self.win.cameras.depth_consumer == "depth":
            self.render_depth()

    def depth_uv(self, pos) -> "tuple | None":
        """depth_view 위젯 좌표 -> depth 이미지 픽셀 좌표 (밖이면 None).

        VideoView 는 KeepAspectRatio + 중앙 정렬이라 스케일과 여백을
        되짚어야 한다.
        """
        z = self.win.cameras.depth_img
        if z is None:
            return None
        h, w = z.shape[:2]
        lw = max(1, self.win.depth_view.width())
        lh = max(1, self.win.depth_view.height())
        s = min(lw / w, lh / h)
        u = int((pos.x() - (lw - w * s) / 2) / s)
        v = int((pos.y() - (lh - h * s) / 2) / s)
        if 0 <= u < w and 0 <= v < h:
            return (u, v)
        return None

    def render_depth(self) -> None:
        """depth(m) → JET 컬러맵 + 척도 바 + 커서 지점 실거리."""
        z = self.win.cameras.depth_img
        if z is None:
            return
        import cv2

        zmax = self.win.depth_range_slider.value() / 100.0
        self.win.depth_range_label.setText(f"{zmax:.1f} m")
        frame = _depth_colormap(z, zmax)
        cursor_txt = ""
        if self.win.cameras.depth_cursor is not None:
            u, v = self.win.cameras.depth_cursor
            if not (0 <= u < z.shape[1] and 0 <= v < z.shape[0]):
                self.win.cameras.depth_cursor = None   # 프레임 크기가 바뀐 뒤 남은 커서
        if self.win.cameras.depth_cursor is not None:
            u, v = self.win.cameras.depth_cursor
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
        self.win.depth_view.set_frame(frame)
        n_ok = int(((z > 0.05) & (z <= zmax)).sum())
        self.win.depth_status.setText(
            tr("유효 픽셀 {p}% · 범위 0.05~{m:.1f} m{c} · 기록 여부는 Settings "
               "스키마(#17)").format(p=round(100 * n_ok / z.size), m=zmax,
                                     c=cursor_txt))

    def render_cloud(self) -> None:
        """포인트클라우드 → 고정 시점 직교 투영 이미지 (numpy 래스터라이즈)."""
        pts, rgb = self.win.cameras.cloud_pts, self.win.cameras.cloud_rgb
        if pts is None or len(pts) == 0:
            return
        yaw = np.deg2rad(self.win.cloud_yaw.value())
        pitch = np.deg2rad(self.win.cloud_pitch.value())
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
        self.win.cloud_view.set_frame(canvas)
