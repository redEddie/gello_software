refactor(gui): 다이얼로그 5개를 gello/gui/dialogs/ 로 분리 (5-2)

5-1 이 본입니다. 같은 방식으로 하세요 (파일 이동, 재수출 껍데기 금지).

## 옮길 것

    gello/gui/dialogs/hf_account.py
        HfAccountDialog + hf_account, hf_stored_accounts,
        hf_switch_account, hf_add_account
    gello/gui/dialogs/schema.py        DatasetSchemaDialog
    gello/gui/dialogs/convert.py       LerobotConvertDialog
    gello/gui/dialogs/upload.py        HdfUploadDialog
    gello/gui/dialogs/repack.py        RepackDialog

`gello/gui/dialogs/__init__.py` 를 만들고 다섯 개를 `__all__` 과 함께
재수출하세요 (`apps/dialogs/__init__.py` 와 같은 모양). 패키지 __init__ 의
재수출은 껍데기가 아니라 공개 API 선언이라 괜찮습니다 -- 금지한 것은
`gui_widgets.py` 에 옛 이름을 남겨두는 쪽입니다.

## 주의

- HF 계정 함수 4개는 `HfAccountDialog` 와 같은 파일에 둡니다. 계정 전환은
  다이얼로그와 함수가 같은 규약(토큰 파일 위치, 전환 절차)을 공유하므로
  떨어뜨리면 한쪽만 고치는 사고가 납니다.
- `LerobotConvertDialog`(211줄), `HdfUploadDialog`(185줄) 은 사용자가 업로드
  인자를 정하는 곳입니다. 기본값과 체크박스 초기 상태를 바꾸지 마세요 --
  잘못 올라간 데이터셋은 되돌리기 어렵습니다.
- `RepackDialog`, `HdfUploadDialog` 는 `REPACK_SCRIPT` 를 씁니다. 상수는
  아직 `gui_widgets.py` 에 두고 거기서 임포트하세요 (마지막 조각에서 정리).

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

20개 통과. `test_hub_upload_state` 가 업로드 다이얼로그를 직접 만듭니다.

## 보고

5-1 과 같은 형식으로.
