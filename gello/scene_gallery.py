"""scene 에피소드 갤러리의 썸네일 캐시 (#31).

에피소드 첫 agentview 프레임을 ~/libero_gui_logs/thumbs/<episode_uid>.jpg
로 캐시한다. episode_uid 는 전역 유일 + 에피소드는 immutable(프레임 불변)
이라 한 번 만든 썸네일은 절대 낡지 않는다 -- 갱신 검사 없이 "없으면
만든다"로 충분하다. quality/instruction 처럼 바뀔 수 있는 표시는 이미지가
아니라 list_scene_episodes 로 매번 읽는다.

564MB scene 파일에서도 에피소드당 첫 프레임 하나만 읽으므로 싸다.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from gello.scene_format import EPISODE_GROUP_RE, list_scene_episodes

THUMBS_DIR = Path.home() / "libero_gui_logs" / "thumbs"
THUMB_WIDTH = 240


def thumb_path(episode_uid: str, thumbs_dir: Path = THUMBS_DIR) -> Path:
    return Path(thumbs_dir) / f"{episode_uid}.jpg"


def _write_thumb(img: np.ndarray, out: Path) -> None:
    import cv2

    h, w = img.shape[:2]
    scale = THUMB_WIDTH / float(w)
    small = cv2.resize(img, (THUMB_WIDTH, max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(small, cv2.COLOR_RGB2BGR))


def build_gallery(scene_path: Path, thumbs_dir: Path = THUMBS_DIR) -> list[dict]:
    """에피소드 요약 + 썸네일 경로 목록. 없는 썸네일만 만든다.

    반환: list_scene_episodes 의 dict 에 "thumb"(str|None) 를 더한 목록.
    이미지가 없는 에피소드(스키마가 agentview 를 껐던 경우)는 thumb=None.
    """
    scene_path = Path(scene_path)
    episodes = list_scene_episodes(scene_path)
    missing = [ep for ep in episodes
               if not thumb_path(ep["episode_uid"], thumbs_dir).exists()]
    if missing:
        with h5py.File(scene_path, "r") as f:
            for ep in missing:
                grp = f.get(ep["name"])
                if grp is None or not EPISODE_GROUP_RE.match(ep["name"]):
                    continue
                rgb = grp.get("obs", {}).get("agentview_rgb")
                if rgb is None or rgb.shape[0] == 0:
                    continue
                _write_thumb(rgb[0], thumb_path(ep["episode_uid"], thumbs_dir))
    for ep in episodes:
        p = thumb_path(ep["episode_uid"], thumbs_dir)
        ep["thumb"] = str(p) if p.exists() else None
    return episodes


def reference_thumb(scene_path: Path, scene_id: str,
                    thumbs_dir: Path = THUMBS_DIR) -> str | None:
    """scene 기준 사진의 썸네일 (갤러리 첫 칸·scene 카드 대표 이미지).

    기준 사진은 set_reference_image 로 바뀔 수 있어(재촬영 허용) 캐시를
    쓰지 않고 매번 만든다 -- 한 장이라 싸다.
    """
    from gello.scene_format import read_reference_image

    img = read_reference_image(Path(scene_path))
    if img is None:
        return None
    out = Path(thumbs_dir) / f"{scene_id}__reference.jpg"
    _write_thumb(img, out)
    return str(out)
