"""Square-crop / resize helpers for LIBERO-format camera images.

Crop parameters are station-specific and persist across GUI restarts in
``~/libero_gui_logs/crop_params.<station>.json``. The historical values in
``EYE_IN_HAND_CROP_X_SHIFT`` and ``LEGACY_CROP_PARAMS_PATH`` are kept only to
reproduce framing for old files that predate per-station crop params.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from gello.config.station import load_station

IMAGE_SIZE = 256  # matches OpenVLA's LIBERO regeneration convention

# 손목 카메라(D405)의 정사각 크롭을 오른쪽으로 미는 양. 640x480 원본 기준 px.
#
# D405 의 RGB 는 좌측 이미저에서 나온다(베이스라인 18.2mm). 모듈 중심이 그리퍼
# 축에 정렬돼 있으므로 광축은 축보다 9.1mm 왼쪽이고, 그리퍼 축은 화면 중앙보다
# 오른쪽에 찍힌다. 수집된 247개 첫 프레임에서 손가락 패드 두 개의 중점을 재면
# 중앙에서 +16.6px(256 기준, σ1.9) = 640 기준 +31px. 물리 검산: fx·d/Z =
# 337 × 9.1mm / 31px → Z ≈ 9.8cm, 손가락 패드까지의 실거리와 일치한다.
#
# 시차 때문에 한 깊이에서만 정확히 가운 때가 된다. 파지 평면(손가락 깊이)을
# 기준으로 잡았다 -- 조작 대상이 놓이는 깊이라서다. 더 먼 배경(탁자)은 이보다
# 덜 밀리지만(예: 25cm 에서 ~12px) 조작에는 영향이 없다.
#
# 이 값은 **역사적 상수**이지 현재 설정이 아니다. attrs["crop_params"] 가 없는
# 옛 파일이 실제로 찍힌 값이라, 변환할 때의 fallback 으로만 쓴다
# (scripts/convert/convert_libero_to_lerobot.py). 스테이션 설정을 고쳐도 여기는 31 로
# 남아야 옛 파일의 프레이밍이 재현된다. 지금 수집에 쓰이는 값은
# default_crop_params() 를 본다.
EYE_IN_HAND_CROP_X_SHIFT = 31

# dataset_schema.json / recent_inputs.json 과 같은 자리. GUI 재시작 간 유지용
# 환경설정이고, 에피소드의 진실은 각 demo attrs["crop_params"] 쪽이다.
#
# **스테이션마다 따로** 둔다. 크롭은 카메라가 어디에 어떻게 달렸는지의 결과라
# 스테이션이 바뀌면 통째로 달라진다. 예전에는 전역 파일 하나였고, 그 탓에
# 스테이션을 바꿔도 이전 스테이션에서 맞춘 값이 그대로 이겨서 -- yaml 에 새 값을
# 적어 두어도 -- 조용히 옛 프레이밍으로 찍혔다.
CROP_PARAMS_DIR = Path.home() / "libero_gui_logs"
# 스테이션이 하나뿐이던 시절의 파일. 기본 스테이션에 한해 한 번 물려받는다.
LEGACY_CROP_PARAMS_PATH = CROP_PARAMS_DIR / "crop_params.json"


def default_crop_params() -> dict:
    """Per-camera square-crop alignment, adjusted in the GUI's Layout page and
    stamped into each episode's attrs (``crop_params``) so the conversion can
    reproduce exactly the framing the operator saw.

    ``x``/``y`` move the crop window (px at 640 source width; +x right,
    +y down). ``zoom`` divides the crop side (1.0 = full square, 2.0 = half).

    값은 스테이션 설정(configs/stations/<이름>.yaml 의 ``crop``)에서 온다 --
    카메라가 어디에 어떻게 달렸는지의 결과라 스테이션마다 다르다. 여기서
    주는 것은 초기값이고, GUI 에서 조정한 값은 crop_params.json 이 이긴다."""
    return load_station().crop_params()


def crop_params_path(station: str | None = None) -> Path:
    """이 스테이션의 크롭 설정 파일 경로."""
    name = station or load_station().name
    return CROP_PARAMS_DIR / f"crop_params.{name}.json"


def load_crop_params(path: Path | None = None, station: str | None = None) -> dict:
    """Never raises -- missing/corrupt file just means the station's defaults.

    ``path`` 를 직접 주면 그것만 읽는다(테스트용). 아니면 스테이션별 파일을
    읽고, 그게 없고 기본 스테이션이면 옛 전역 파일을 한 번 물려받는다.
    """
    from gello.config.station import DEFAULT_STATION

    base = default_crop_params()
    if path is None:
        name = station or load_station().name
        path = crop_params_path(name)
        # 다른 스테이션은 옛 파일을 물려받지 않는다 -- 물려받으면 그게 바로
        # 이 분리로 없애려는 버그다.
        if not Path(path).exists() and name == DEFAULT_STATION:
            path = LEGACY_CROP_PARAMS_PATH
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for role, defaults in base.items():
            got = data.get(role)
            if not isinstance(got, dict):
                continue
            for key, dflt in defaults.items():
                v = got.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    defaults[key] = type(dflt)(v)
    except (OSError, ValueError, TypeError):
        pass
    return base


def save_crop_params(params: dict, path: Path | None = None,
                     station: str | None = None) -> None:
    try:
        p = Path(path) if path is not None else crop_params_path(station)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(params, indent=1), encoding="utf-8")
    except OSError:
        pass


def square_crop(img: np.ndarray, zoom: float = 1.0, x_shift: int = 0,
                y_shift: int = 0) -> np.ndarray:
    """Square window into ``img``: shifted, optionally zoomed, always clamped.

    Shifts are in pixels *at 640 source width* (scaled for other widths), so
    the same numbers mean the same framing at every capture resolution."""
    h, w = img.shape[:2]
    s = min(h, w)
    if zoom > 1.0:
        s = max(16, round(s / zoom))
    sc = w / 640
    x0 = (w - s) // 2 + round(x_shift * sc)
    y0 = (h - s) // 2 + round(y_shift * sc)
    x0 = min(max(x0, 0), w - s)
    y0 = min(max(y0, 0), h - s)
    return img[y0 : y0 + s, x0 : x0 + s]


def resize_rgb(img: np.ndarray, size: int = IMAGE_SIZE, zoom: float = 1.0,
               x_shift: int = 0, y_shift: int = 0) -> np.ndarray:
    """Resize an (H, W, 3) uint8 RGB image to (size, size, 3), square-cropped
    first (see ``square_crop``). Defaults keep the historical center crop."""
    import cv2

    cropped = square_crop(img, zoom=zoom, x_shift=x_shift, y_shift=y_shift)
    # INTER_AREA 는 축소용이다. zoom 이 크면 확대가 되는데 그때는 LINEAR.
    interp = cv2.INTER_AREA if cropped.shape[0] >= size else cv2.INTER_LINEAR
    return cv2.resize(cropped, (size, size), interpolation=interp)
