refactor(gui): 남은 위젯과 상수를 정리해 gui_widgets.py 를 비운다 (5-3)

5-1, 5-2 가 본입니다.

## 옮길 것

    gello/gui/widgets/video_view.py   VideoView + np_to_pixmap
    gello/gui/widgets/recents.py      Recents + RECENTS_PATH
    gello/gui/widgets/delta_bar.py    DeltaBar

`gello/gui/widgets/__init__.py` 에 `__all__` 과 함께 재수출.

남은 상수(`TODO_MARK`, `TODO_STYLE`, `PLAYBACK_FPS`, `REPACK_SCRIPT` 등)는
`gello/gui/constants.py` 로 모으세요. **`apps/workspace/constants.py` 로
옮기지 마세요** -- `gello/` 가 `apps/` 를 임포트하면 계층이 뒤집힙니다
(CLAUDE.md 의 불변식: `apps/ -> gello/` 는 되고 반대는 안 됩니다).

다 옮기고 나면 `gello/gui/gui_widgets.py` 에 남는 것이 있는지 보세요.
비었으면 파일을 지우고, 남은 것이 있으면 그것이 무엇이고 왜 어디에도
속하지 않는지 보고에 적으세요.

## 주의: RECENTS_PATH

`tests/gui/test_hub_upload_state.py` 가 실제 GUI 기억 파일을 오염시키지 않으려고
`gw.RECENTS_PATH` 를 임시 경로로 갈아끼웁니다 (2026-08-26 사고 재발 방지).
`Recents` 가 이 값을 **모듈 전역에서 읽어야** 그 수법이 계속 통합니다 --
클래스 안에 기본값으로 굳히지 마세요. 테스트의 갈아끼우는 줄도 새 경로에
맞게 고치세요.

## 주의: DeltaBar

`DeltaBar.update_delta` 는 색이 바뀔 때만 `setStyleSheet` 를 부릅니다.
그렇게 하기 전에는 초당 350번 스타일을 다시 파싱해 오차 게이지가 눈에 띄게
느렸습니다 (2026-08-31). 캐시(`self._color`) 를 없애지 마세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

20개 통과.

## 보고

5-1 과 같은 형식으로. `gui_widgets.py` 를 지웠는지, 남겼다면 무엇이
남았는지 반드시 적으세요.
