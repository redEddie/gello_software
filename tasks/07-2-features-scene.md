refactor(workspace): scene 기능을 한 폴더로 모은다 (7-2)

`tasks/_공통.md` 를 먼저 읽으세요. 7-1 이 끝난 뒤에 합니다.

## 왜

지금 scene 기능은 네 폴더에 흩어져 있습니다 (dialogs, builders, pages,
domains). 하나를 고치려면 네 곳을 열어야 합니다. 계층별로 나눈 폴더를
**기능별로** 바꾸는 첫 시도입니다 -- 두 기능(scene, upload)만 해보고
나머지는 그 결과를 보고 정합니다.

## 만들 것

    apps/workspace/features/__init__.py          (빈 패키지 설명만)
    apps/workspace/features/scene/__init__.py    공개 이름 재수출 + __all__
    apps/workspace/features/scene/ops.py         domains/scene/ops.py
    apps/workspace/features/scene/planning.py    domains/scene/planning.py
    apps/workspace/features/scene/layout_ref.py  domains/scene/layout_ref.py
    apps/workspace/features/scene/layout_page.py pages/layout.py
    apps/workspace/features/scene/layout_tab.py  builders/layout_tab.py
    apps/workspace/features/scene/dialogs/       apps/dialogs 에서 5개:
        new_scene_dialog.py  recommend_dialog.py  grid_editor_dialog.py
        plan_edit_dialog.py  plan_json_dialog.py

`git mv` 를 쓰세요 -- 이력이 이어져야 나중에 왜 옮겼는지 추적됩니다.

## 옮기지 말 것

- `apps/workspace/pages/configure.py` -- scene 콤보도 있지만 로봇·노드·
  카메라 설정이 함께 있는 **설정 페이지**입니다. 한 기능이 아닙니다.
- `apps/workspace/builders/layout.py` -- 이름이 비슷하지만 창 골격
  (center/left/right/bottom) 입니다. scene 과 무관합니다.
- `gello/gui/` 아래 것 (`scene_gallery.py`, `grid_overlay.py`) -- 계층이
  다릅니다. `apps/` 아래로 내리면 `gello -> apps` 역류가 됩니다.
- `apps/dialogs/_widgets.py`, `_image_utils.py` -- 여러 기능이 함께 씁니다.

## 호출부

`apps/dialogs/__init__.py` 가 옮기는 5개를 재수출합니다. 그 목록에서 빼고,
쓰는 쪽이 새 경로에서 직접 가져오게 하세요. 테스트도 3개가 직접 임포트합니다:

    grep -rn 'new_scene_dialog\|recommend_dialog\|grid_editor_dialog\|plan_edit_dialog\|plan_json_dialog\|pages.layout\|builders.layout_tab' apps/ tests/ --include='*.py'

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 전부 통과. `test_app_structure` 가 `apps/` 전체의 순환·중복 정의·
고아 임포트를 봅니다. `test_layer_rules` 가 `gello -> apps` 역류를 봅니다.

## 보고

옮긴 파일 수와 줄 수, 고친 임포트 파일 수, 그리고 **옮길지 망설인 것**을
적으세요.
