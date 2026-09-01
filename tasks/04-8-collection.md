refactor(workspace): 수집 제어를 CollectionOps로 분리 (Phase 4-8, 마지막)

`tasks/_공통.md` 를 먼저 읽으세요. 앞의 일곱 조각이 본입니다.

## 이 조각이 가장 위험합니다

수집 제어는 밖의 메서드를 **48번** 부릅니다 (다음으로 많은 데이터셋이 12번).
녹화·저장·판정·게이트·리셋이 전부 여기 있고, 여기가 깨지면 데이터 수집
자체가 막힙니다. 그러니 **가장 보수적으로**: 자리만 옮기고 그 외에는
아무것도 바꾸지 마세요.

## 만들 것

`apps/workspace/domains/collection.py` 의 `CollectionOps`.
창에서는 `self.collection = CollectionOps(self)`.

## 옮길 것

    _on_connect  _on_disconnect  _cmd  _save  _set_running
    _on_state  _on_gate  _on_countdown  _on_saved  _on_save_status
    _on_pose_match  _on_fatal  _on_log  _on_progress
    _set_activity 는 옮기지 마세요 (좌측 패널 전환이라 UI 입니다)

`grep -n '    def ' apps/collect_workspace.py` 로 남은 것을 보고, 수집 동선에
해당하는 것만 고르세요. 확신이 서지 않으면 **남겨 두고 보고에 적으세요** --
남겨 두는 쪽이 언제나 안전합니다.

## worker 를 어떻게 할 것인가

`self.worker` 는 ZMQ 수집 스레드 핸들이고 29개 메서드가 걸려 있습니다.
**창에 그대로 두세요** (`self.win.worker`). 도메인으로 옮기지 마세요 --
창이 소유하고 창이 닫을 때 정리하는 것이 지금의 계약이고, 그것까지 이번에
바꾸면 실패했을 때 원인이 둘로 갈립니다.

worker 의 시그널 연결(`self.worker.state.connect(...)` 등)은 옮긴 슬롯을
가리키도록 `self.collection.on_state` 처럼 고치되, **연결하는 코드 자체는
창에 남겨 두세요.**

## 주의: 단축키

`eventFilter` 는 Space/Enter/Esc/Delete 를 상태에 따라 다르게 처리하고,
양손이 리더암 위에 있는 조작자에게는 이것이 유일한 조작 수단입니다.
**`eventFilter` 는 창에 남겨 두고**, 그 안에서 `self.collection.<메서드>()`
를 부르게만 하세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

20개 전부 통과해야 합니다. 특히 `test_gate_reset` 과 `test_phase4a` 는 게이트
진입, 오차 게이지, 리셋 대기, 판정 뒤집기를 직접 확인합니다. 둘 중 하나라도
깨지면 수집 동선이 깨진 것이므로 되돌려야 합니다.

`test_ui_surface` 가 툴바 9개 버튼(Connect/Record/Save/Discard 등)이 그대로
있는지 봅니다 -- 이 버튼들이 전부 이번에 옮기는 메서드를 부릅니다.

## 보고

옮긴 메서드, **확신이 서지 않아 남긴 메서드**, `collect_workspace.py` 줄 수,
그리고 worker 연결을 어디에 남겼는지 적으세요.
