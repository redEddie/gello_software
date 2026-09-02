refactor(workspace): 남은 작은 것들을 제 도메인으로 (6-4)

`tasks/_공통.md` 를 먼저 읽으세요. 이번 조각은 작은 이동 여러 개입니다.

## 옮길 것

    DatasetOps 로   _update_dataset_panel(93)  _on_no_dataset_toggled(13)
    CollectionOps 로 _on_connected(34)  _on_worker_finished(29)
    StatsOps 로     _log_progress(21)  connect_progress(5)

`grep -n '    def ' apps/collect_workspace.py` 로 실제 이름을 확인하세요.

## 창에 남겨야 하는 것 -- 옮기지 마세요

    __init__  eventFilter  closeEvent  _connect_worker  _set_activity
    _on_center_tab_changed  log  _alert  _proc_text  _current_task_label

이것들은 조립·수명주기이거나 창이 가진 공용 유틸입니다. **`collect_workspace.py`
를 0줄로 만드는 것이 목표가 아닙니다** -- 조립 269줄 + 공용 유틸 196줄,
합쳐 400~500줄이 남는 것이 정상입니다.

## 주의

- `_on_connected` 와 `_on_worker_finished` 는 worker 수명주기에 걸립니다.
  `self.win.worker` 는 창이 소유하므로 **worker 를 옮기지 마세요.**
  연결하는 코드(`_connect_worker`)도 창에 남깁니다.
- `_update_dataset_panel(93줄)` 은 이번 조각에서 가장 큽니다. 이것만 먼저
  옮기고 나머지가 애매하면, 옮긴 것만 두고 **남긴 것을 보고에 적으세요.**

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 통과. 끝나면 `wc -l apps/collect_workspace.py` 를 보고에 적으세요.
