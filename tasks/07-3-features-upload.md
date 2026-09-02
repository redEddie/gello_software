refactor(workspace): upload 기능을 한 폴더로 모은다 (7-3)

`tasks/_공통.md` 를 먼저 읽으세요. 7-2 가 본입니다 -- 같은 모양으로.

## 만들 것

    apps/workspace/features/upload/__init__.py         재수출 + __all__
    apps/workspace/features/upload/ops.py              domains/upload.py
    apps/workspace/features/upload/page.py             pages/upload.py
    apps/workspace/features/upload/pipeline_dialog.py  apps/dialogs/pipeline_dialog.py

`git mv` 를 쓰세요.

## 옮기지 말 것

- `gello/gui/dialogs/{convert,upload,repack}.py` -- `gello/` 계층입니다.
  `apps/features/` 아래로 내리면 계층이 뒤집힙니다.
- `gello/scene/dataset_sync.py`, `gello/data/hub_upload_state.py` -- 라이브러리입니다.

## 주의: PipelineDialog

- `scripts=` 를 필수 인자로 받습니다. 스크립트 경로 상수는
  `apps/workspace/constants.py` 에 있고, 그건 그대로 둡니다.
- `tests/gui/test_hub_upload_state.py` 가 이 대화상자를 직접 만들어
  업로드 대상 선택 로직(장부)을 검사합니다. **그 테스트의 assert 를 고치지
  말고 임포트 경로만** 고치세요.
- 업로드 기본값과 체크박스 초기 상태를 바꾸지 마세요. 잘못 올라간
  데이터셋은 되돌리기 어렵습니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 통과.

## 보고

7-2 와 같은 형식으로. 그리고 이제 `apps/workspace/` 밑에 남은 폴더가
무엇이고 각각 몇 줄인지 적으세요 -- 다음 판단에 쓰겠습니다.
