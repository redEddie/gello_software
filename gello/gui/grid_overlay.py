"""워크스페이스 3×3 격자 오버레이.

scene 수집 때 물체를 어느 칸(A1..C3)에 놓을지 화면에서 확인하는 보조선이다.
격자는 카메라에 비친 작업면의 사각형(꼭짓점 4개)으로 정의하고, 내부 선은
원근 변환으로 계산한다 -- 사영 변환은 직선을 직선으로 보내므로 선분의
양 끝점만 변환하면 된다.

꼭짓점은 이미지 크기에 대한 비율(0..1)로 저장한다. 라이브 뷰(640×480)와
레이아웃 스틸(224×224)처럼 해상도가 달라도 같은 격자가 같은 곳에 그려진다.
순서는 tl, tr, br, bl (시계 방향).

저장 파일은 **스테이션마다 하나**다 (2026-09-04 결정 -- 격자는 카메라
자세에 종속된 물리 셋업 값이라 스테이션이 다르면 섞이면 안 된다):

    ~/libero_gui_logs/grids/<station>.json
    {"active": 이름, "live_on": bool, "alpha": 10..100, "grids": {이름: 꼭짓점}}

스테이션은 GELLO_STATION / load_station() 이 정한다. 첫 실행 때 옛 전역
파일(~/libero_gui_logs/workspace_grids.json)이 있으면 그 스테이션 파일로
1회 복사한다.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

GRID_DIR = Path.home() / "libero_gui_logs" / "grids"
LEGACY_GRID_STORE_PATH = Path.home() / "libero_gui_logs" / "workspace_grids.json"
# 화면 가운데쯤의 사다리꼴 -- 위가 멀고 아래가 가까운 일반적인 카메라 각도.
DEFAULT_CORNERS = [[0.30, 0.30], [0.70, 0.30], [0.80, 0.80], [0.20, 0.80]]
GRID_COLOR = (80, 255, 140)   # RGB


def grid_store_path(station: "str | None" = None) -> Path:
    """스테이션의 격자 저장 파일. station=None 이면 현재 스테이션."""
    if station is None:
        from gello.config.station import load_station

        station = load_station().name
    return GRID_DIR / f"{station}.json"


def load_grid_store(path: "Path | None" = None) -> dict:
    # 마이그레이션은 path 를 지정하지 않은 호출(= 현재 스테이션 파일)에서만
    # 한다. 호출자가 준 경로에까지 옛 파일을 복사하면 "빈 저장소를 달라"고
    # 넘긴 경로에 남의 격자가 들어온다 -- 읽기 함수가 지정된 곳에 쓰는 셈이다.
    migrate = path is None
    path = Path(path) if path is not None else grid_store_path()
    if (migrate and not path.exists()
            and path != LEGACY_GRID_STORE_PATH
            and LEGACY_GRID_STORE_PATH.exists()):
        # 옛 전역 저장소 -> 이 스테이션 파일로 1회 마이그레이션
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LEGACY_GRID_STORE_PATH, path)
        except OSError:
            pass
    try:
        d = json.loads(path.read_text())
        if not isinstance(d.get("grids"), dict):
            raise ValueError
    except (OSError, ValueError):
        d = {}
    d.setdefault("grids", {})
    d.setdefault("active", None)
    d.setdefault("live_on", False)
    d.setdefault("alpha", 60)
    return d


def save_grid_store(store: dict, path: "Path | None" = None) -> None:
    path = Path(path) if path is not None else grid_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2))


def active_corners(store: dict) -> "list | None":
    return store["grids"].get(store.get("active") or "")


# 매 프레임(라이브 20~30Hz) 호출되므로 호모그래피 계산은 캐시한다 --
# 꼭짓점은 편집할 때만 바뀐다. 키 몇 개면 충분해 크기만 느슨하게 막는다.
_SEG_CACHE: dict = {}


def grid_segments(corners, w: int, h: int, n: int = 3) -> list:
    """픽셀 선분 [((x1,y1),(x2,y2)), ...] -- 세로 n+1개 + 가로 n+1개."""
    key = (tuple(tuple(c) for c in corners), w, h, n)
    hit = _SEG_CACHE.get(key)
    if hit is not None:
        return hit
    import cv2

    src = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    dst = np.float32([[c[0] * w, c[1] * h] for c in corners])
    m = cv2.getPerspectiveTransform(src, dst)
    pts = []
    for i in range(n + 1):
        t = i / n
        pts += [(t, 0.0), (t, 1.0), (0.0, t), (1.0, t)]
    mapped = cv2.perspectiveTransform(
        np.float32(pts).reshape(-1, 1, 2), m).reshape(-1, 2)
    segs = []
    for i in range(n + 1):
        a, b, c, d = mapped[4 * i: 4 * i + 4]
        segs.append((tuple(np.int32(a)), tuple(np.int32(b))))   # 세로선
        segs.append((tuple(np.int32(c)), tuple(np.int32(d))))   # 가로선
    if len(_SEG_CACHE) > 64:
        _SEG_CACHE.clear()
    _SEG_CACHE[key] = segs
    return segs


def draw_grid(img: np.ndarray, corners, alpha_pct: int,
              color=GRID_COLOR, n: int = 3) -> np.ndarray:
    """격자를 alpha_pct% 불투명도로 덧그린 사본을 돌려준다."""
    import cv2

    h, w = img.shape[:2]
    overlay = np.ascontiguousarray(img)
    lines = overlay.copy()
    thick = max(1, round(w / 320))
    for a, b in grid_segments(corners, w, h, n):
        cv2.line(lines, a, b, color, thick, cv2.LINE_AA)
    a = max(0, min(100, int(alpha_pct))) / 100.0
    return cv2.addWeighted(lines, a, overlay, 1.0 - a, 0.0)


def draw_alignment_grid(img: np.ndarray) -> np.ndarray:
    """1/8 간격 격자를 절반 밝기로 덧그린다 -- 수평/중앙 확인용. 사본에만.

    draw_grid() 가 그리는 것은 조작자가 네 모서리로 정의한 작업 격자이고,
    이쪽은 카메라가 삐뚤어졌는지 보려고 화면에 고정으로 얹는 보조선이다.
    """
    out = img.copy()
    h, w = out.shape[:2]
    for i in range(1, 8):
        y, x = h * i // 8, w * i // 8
        c = 255 if i == 4 else 190        # 중앙선만 조금 더 밝게
        out[y, :] = out[y, :] // 2 + c // 2
        out[:, x] = out[:, x] // 2 + c // 2
    return out
