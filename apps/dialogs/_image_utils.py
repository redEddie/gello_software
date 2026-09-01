"""Image/depth helpers shared by workspace dialogs."""

from __future__ import annotations

import numpy as np


def _depth_colormap(z: np.ndarray, zmax: float, zmin: float = 0.05) -> np.ndarray:
    """depth(m) → JET 컬러맵(가까움=빨강) + 오른쪽 척도 바.

    라이브 Depth 탭과 HDF5 뷰어의 depth 미리보기가 같은 매핑을 쓰게 하는
    단일 지점. 무측정/범위 밖은 검정.
    """
    import cv2

    valid = (z > zmin) & (z <= zmax)
    norm = np.zeros(z.shape, np.uint8)
    norm[valid] = (255 * (1.0 - z[valid] / zmax)).astype(np.uint8)
    rgb = cv2.cvtColor(cv2.applyColorMap(norm, cv2.COLORMAP_JET),
                       cv2.COLOR_BGR2RGB)
    rgb[~valid] = 0
    return _draw_depth_scale(rgb, zmax)


def _draw_depth_scale(rgb: np.ndarray, zmax: float) -> np.ndarray:
    """오른쪽 세로 컬러바 + 거리 눈금(m). 너무 작은 이미지에는 그리지 않는다."""
    import cv2

    h, w = rgb.shape[:2]
    if w < 240 or h < 160:
        return rgb
    bar_h, bar_w = int(h * 0.72), 18
    x0, y0 = w - bar_w - 10, (h - bar_h) // 2
    t = np.linspace(0.0, 1.0, bar_h, dtype=np.float32)     # 0=위=가까움
    col = (255 * (1.0 - t)).astype(np.uint8).reshape(-1, 1)
    bar = cv2.cvtColor(cv2.applyColorMap(np.repeat(col, bar_w, 1),
                                         cv2.COLORMAP_JET), cv2.COLOR_BGR2RGB)
    rgb[y0:y0 + bar_h, x0:x0 + bar_w] = bar
    cv2.rectangle(rgb, (x0 - 1, y0 - 1), (x0 + bar_w, y0 + bar_h),
                  (255, 255, 255), 1)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y0 + int(frac * (bar_h - 1))
        cv2.line(rgb, (x0 - 5, y), (x0 - 1, y), (255, 255, 255), 1)
        label = f"{frac * zmax:.2f}m"
        # 검정 외곽선 + 흰 글자 -- 어느 배경에서든 읽히게
        for color, thick in (((0, 0, 0), 3), ((255, 255, 255), 1)):
            cv2.putText(rgb, label, (x0 - 62, y + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, thick,
                        cv2.LINE_AA)
    return rgb
