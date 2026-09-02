refactor(workspace): playback 과 gallery 를 features/ 로 (8-2)

`tasks/_공통.md` 를 먼저 읽으세요. 8-1 이 본입니다.

## 만들 것

    features/playback/__init__.py   재수출 + __all__
    features/playback/ops.py        domains/playback.py
    features/playback/trim_tab.py   builders/trim_tab.py

    features/gallery/__init__.py    재수출 + __all__
    features/gallery/ops.py         domains/gallery.py
    features/gallery/tab.py         builders/gallery_tab.py

두 기능을 한 커밋에 넣습니다 -- 둘 다 작고 서로 무관합니다.

## 주의

- `builders/__init__.py` 가 `build_trim_tab` 과 `build_gallery_tab` 을
  재수출합니다. `__all__` 에서 빼고, 부르는 쪽(`builders/layout.py` 의
  `build_center`)이 새 경로에서 직접 가져오게 하세요.
- `GalleryLoadWorker`, `EpisodeLoadWorker` 는 `gello/gui/workers.py` 에
  있습니다. 옮기지 마세요 -- `gello/` 계층입니다.
- 썸네일 캐시 `gello/gui/scene_gallery.py` 도 그대로 둡니다.
- QTimer 를 만들 때 부모는 창입니다 (`QTimer(self.win)`).

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python
