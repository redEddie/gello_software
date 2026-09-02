"""큐레이션 편집(삭제·트림)이 있었다는 사실을 파일에 남기는 카운터.

구현은 h5py 그룹의 attr 를 올리는 두 줄이라 씬 전용이 아니다. 씬 쪽과
트림 쪽이 함께 쓰는데 씬에 두면 gello/data -> gello/scene 화살표가 생겨
순환이 됐다 (2026-09-02).

왜 세는가: 삭제 후 renumber 는 uid 를 재사용하므로, 변환기의 이어붙이기가
쓰는 "이미 올린 uid 는 스킵" 대조가 편집 이후에는 다른 에피소드를 가리킬
수 있다. 그래서 편집이 있었던 파일은 resume 을 거부하고 전체 재빌드만
허용해야 한다. 변환기가 변환 시점의 값을 사이드카에 적어 두고, resume
때 파일의 현재 값과 다르면 중단한다. 단조 증가만 하고 리셋되지 않는다.
"""
from __future__ import annotations

import time

import h5py


def mark_scene_edited(meta: h5py.Group) -> None:
    """편집 카운터를 하나 올리고 시각을 남긴다."""
    meta.attrs["edit_count"] = int(meta.attrs.get("edit_count", 0)) + 1
    meta.attrs["edited"] = time.strftime("%Y-%m-%dT%H:%M:%S")
