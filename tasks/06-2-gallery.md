refactor(workspace): 갤러리를 GalleryOps로 분리 (6-2)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

`apps/workspace/domains/gallery.py` 의 `GalleryOps`.
창에서는 `self.gallery = GalleryOps(self)`.

## 옮길 것 (약 4~6개, 73줄)

    _apply_gallery_filter  _refresh_gallery  _refresh_gallery_scenes
    _on_gallery_loaded  _on_gallery_activated

`grep -n '    def .*gallery' apps/collect_workspace.py` 로 확정.
`builders/gallery_tab.py` 가 이 메서드들을 연결하므로 같이 고치세요.

## 주의

- `GalleryLoadWorker` 는 `gello/gui/workers.py` 에 있습니다. 그대로 쓰세요.
- 썸네일 캐시는 `gello/gui/scene_gallery.py` 입니다. 옮기지 마세요.
- `_refresh_gallery_scenes` 는 scene 목록을 읽습니다. scene 도메인과
  겹쳐 보이면 **갤러리 쪽에 두고** 보고에 적으세요 -- 갤러리 탭이
  유일한 사용처입니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python
