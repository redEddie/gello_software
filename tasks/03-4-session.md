refactor(workspace): 세션·에피소드 상태를 SessionState로 분리 (Phase 3-4)

## 이 조각이 가장 위험합니다

앞의 셋은 탭 하나, 프로세스 하나에 갇혀 있었지만 이것은 **수집의 심장**입니다.
`worker`(29개 메서드), `active_file_path`(15개), `active_episode_cache`(11개)가
녹화·저장·판정·통계에 전부 걸쳐 있습니다. 여기가 깨지면 수집 자체가 막힙니다.
그러니 앞의 셋보다 더 보수적으로: **이름만 옮기고 그 외에는 아무것도 바꾸지
마세요.**

## 먼저 읽을 것

`apps/workspace/models.py` 의 `ProcessRegistry`(3-1), `PlaybackState`(3-2),
`CameraState`(3-3). 같은 모양으로 만드세요.

## 옮길 것 (이것만, 정확히)

    _session  _scene_session  _no_dataset_session  _episodes_at_connect
    _stats  _cumulative
    active_file_path  active_episode_cache
    _last_saved_name  _last_saved_success  _pending_verdict_toggle
    _pending_success  _current_state  _gate_ok

**`worker` 는 옮기지 마세요.** ZMQ 스레드 핸들이라 창이 소유하는 것이 맞고
(불변식 5), 29개 메서드가 걸려 있어 이번 조각과 같이 건드리면 실패했을 때
원인이 둘로 갈립니다. 다음 조각에서 따로 다룹니다.

이름이 비슷하지만 **Qt 위젯이라 옮기면 안 되는 것**:
`stats_task_header  ep_progress  analysis_summary  _summary  scene_info`

## 방법

1. `apps/workspace/models.py` 에 `SessionState` 를 추가합니다.
2. `WorkspaceWindow.__init__` 에서 `self.session = SessionState()` 를 만들고
   초기화 줄을 지웁니다.
3. 모든 참조를 `self.session.<이름>` 으로 바꿉니다. 앞의 밑줄은 뗍니다.
   `active_file_path` 처럼 원래 밑줄이 없던 것은 그대로 둡니다.
4. 빌더와 페이지가 `_stats`, `_scene_session`, `active_file_path`,
   `active_episode_cache` 를 직접 만집니다. 전부 grep 으로:

       grep -rn '_scene_session\|active_file_path\|active_episode_cache\|_stats\b' apps/ tests/

   `pages/stats.py`, `pages/configure.py`, `pages/upload.py` 를 꼭 확인하세요.

## 주의: `_stats` 와 `_cumulative` 는 같은 모양의 dict

`_new_stats()` 가 만드는 dict 한 벌입니다. 둘 다 `SessionState` 필드로 옮기되
`field(default_factory=_new_stats)` 처럼 **각각 따로** 만들어야 합니다.
하나를 두 필드가 공유하면 이번 task 통계가 누적 통계를 오염시킵니다.

## 하지 말 것

- 동작을 바꾸지 마세요. 성공/실패 판정, 리셋 구간 뒤집기, 카운터 증가 시점
  전부 그대로입니다.
- 메서드는 옮기지 마세요 (Phase 4).
- `worker` 를 건드리지 마세요.
- Qt 위젯을 모델에 넣지 마세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

19개 전부 통과해야 합니다. `test_gate_reset`, `test_phase4a`,
`test_stats_group`, `test_relabel` 이 이 영역을 직접 건드립니다 -- 이 넷 중
하나라도 깨지면 수집 동선이 깨진 것이므로 되돌려야 합니다.

## 보고

옮긴 항목, 위젯이라 제외한 항목, `_stats`/`_cumulative` 를 어떻게 각각
따로 만들었는지, 줄 수 변화를 한 문단으로. 그리고 이번 작업에서 **확신이
서지 않아 그대로 둔 것**이 있으면 반드시 적으세요 -- 아침에 사람이 봅니다.
