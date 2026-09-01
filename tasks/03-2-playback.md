refactor(workspace): 트림·재생 상태를 PlaybackState로 분리 (Phase 3-2)

## 먼저 읽을 것

`apps/workspace/models.py` 의 `ProcessRegistry` 가 Phase 3-1 에서 만든 본입니다.
같은 모양으로 만드세요.

## 옮길 것 (이것만, 정확히)

    _trim_key  _trim_n  _trim_n_pending  _trim_frames  _trim_loader
    _trim_timer  _trim_series  _trim_tab_index
    _play_key  _play_timer  _play_frames  _play_loader
    _layout_playing

이름이 비슷하지만 **Qt 위젯이라 옮기면 안 되는 것**:
`trim_plots  trim_summary  trim_count  trim_pos  trim_warn
 play_caption  play_pos`
확실하지 않으면 `WorkspaceWindow.__init__` 이나 빌더에서 그 이름에 무엇이
대입되는지 보세요. `Q...` 로 시작하는 클래스가 대입되면 위젯입니다.

## 방법

1. `apps/workspace/models.py` 에 `PlaybackState` 를 추가합니다 (새 파일을
   만들지 말고 같은 파일에 붙입니다).
2. `WorkspaceWindow.__init__` 에서 `self.playback = PlaybackState()` 를 만들고
   위 13개의 초기화 줄을 지웁니다.
3. 모든 참조를 `self.playback.<이름>` 으로 바꿉니다. 앞의 밑줄은 뗍니다
   (`_trim_key` -> `playback.trim_key`).
4. `apps/workspace/builders/trim_tab.py` 가 같은 이름을 만집니다. 반드시
   grep 으로 전부 찾아 고치세요:

       grep -rn '_trim_\|_play_\|_layout_playing' apps/ tests/

## 주의: QTimer

`_trim_timer`, `_play_timer` 는 QTimer 입니다. dataclass 필드에 담는 것은
괜찮지만, 만들 때 부모를 주는 코드(`QTimer(self)`)는 그대로 두세요 --
부모를 잃으면 창이 닫힐 때 정리되지 않습니다. 즉 **만드는 자리는 그대로,
담아 두는 자리만 모델로** 옮깁니다.

## 하지 말 것

- 동작을 바꾸지 마세요. 재생 속도, 트림 계산, 타이머 주기 전부 그대로입니다.
- 메서드는 옮기지 마세요 (Phase 4).
- Qt 위젯을 모델에 넣지 마세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

19개 전부 통과해야 합니다. `test_grid_replay`, `test_h5view`,
`test_plan_edit_replay` 가 이 영역을 직접 건드립니다.

    grep -c '_trim_key\|_play_key' apps/collect_workspace.py   # 0 이어야 함

## 보고

옮긴 항목, 위젯이라 제외한 항목, `collect_workspace.py` 줄 수 변화, grep
결과를 한 문단으로.
