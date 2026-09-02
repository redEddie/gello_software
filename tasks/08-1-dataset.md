refactor(workspace): dataset 기능을 features/dataset/ 으로 (8-1)

`tasks/_공통.md` 를 먼저 읽으세요. `features/scene/`(7-2)와 `features/upload/`(7-3)이
본입니다 -- 같은 모양으로, `git mv` 로.

## 만들 것

    apps/workspace/features/dataset/__init__.py           재수출 + __all__
    apps/workspace/features/dataset/ops.py                domains/dataset.py
    apps/workspace/features/dataset/page.py               pages/dataset.py
    apps/workspace/features/dataset/hdf5_tree_dialog.py   apps/dialogs/hdf5_tree_dialog.py

## 주의

- `Hdf5TreeDialog` 은 Dataset 메뉴의 "구조 확인" 입니다. 쓰는 곳:
  `apps/collect_workspace.py`(임포트 + 호출), `apps/dialogs/__init__.py`(재수출),
  `tests/gui/test_h5view.py`(`cw.Hdf5TreeDialog` 로 참조).
  `apps/dialogs/__init__.py` 의 재수출 목록에서 빼고, 쓰는 쪽이 새 경로에서
  직접 가져오게 하세요. 테스트는 `cw.` 경유 대신 새 경로를 직접 임포트하도록
  고치세요.
- `hdf5_tree_dialog.py` 는 `apps/dialogs/_image_utils.py` 를 씁니다.
  **`_image_utils.py` 는 옮기지 마세요** -- `domains/depth.py` 도 함께 쓰는
  공용 헬퍼입니다. 새 자리에서 `apps.dialogs._image_utils` 를 그대로 임포트하세요.
- 삭제 확인 문구를 한 글자도 바꾸지 마세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 통과. `test_app_structure` 가 순환·고아 임포트를 봅니다.
