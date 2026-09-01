refactor(workspace): 트림·재생을 PlaybackOps로 분리 (Phase 4-2)

`tasks/_공통.md` 를 먼저 읽으세요. `apps/workspace/domains/upload.py`(4-1)가
본입니다 -- 같은 모양으로 만드세요.

## 만들 것

`apps/workspace/domains/playback.py` 의 `PlaybackOps`.
창에서는 `self.playback_ops = PlaybackOps(self)`.

**이름 주의**: `self.playback` 은 이미 `PlaybackState`(데이터)입니다.
도메인은 `self.playback_ops` 로, 겹치지 않게 하세요.

## 옮길 것

재생·트림·HDF5 뷰어에 해당하는 메서드 약 29개, 350줄:

    _on_play_tick  _on_open_trim  _trim_*  _play_*  _apply_speed
    _on_episode_loaded  _on_episode_list  _on_replay*  _on_h5*
    _on_show_structure

`grep -n '    def .*\(trim\|play\|replay\|h5\)' apps/collect_workspace.py` 로
목록을 확정하세요. 위 목록은 정규식으로 뽑은 것이라 빠진 것이 있을 수
있고, 반대로 `_on_hdf5_upload` 처럼 업로드 쪽인데 걸리는 것도 있습니다 --
그건 이미 4-1 에서 나갔으니 건드리지 마세요.

## 주의

- 상태는 이미 `self.win.playback.*` 에 있습니다 (`trim_key`, `play_frames` 등).
  새로 만들지 마세요.
- `replay_process` 는 `self.win.procs.replay_process` 입니다.
- QTimer 를 만들 때 부모는 창이어야 합니다: `QTimer(self.win)`.
  도메인을 부모로 주면 창이 닫힐 때 정리되지 않습니다.
- `builders/trim_tab.py` 가 `win._trim_*` / `win._on_*` 를 연결합니다.
  전부 `win.playback_ops.*` 로 고치세요.
