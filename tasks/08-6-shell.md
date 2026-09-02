refactor(workspace): 남은 것을 shell/ 과 공용으로 정리한다 (8-6, 마지막)

`tasks/_공통.md` 를 먼저 읽으세요. 8-1~8-5 가 끝난 뒤에 합니다.

## 이번 조각의 목적

기능이 전부 `features/` 로 갔으니, 남은 것은 **창 골격**과 **여러 기능이
함께 쓰는 것** 둘뿐이어야 합니다. 그것이 이름으로 드러나게 합니다.

## 만들 것

    apps/workspace/shell/__init__.py        재수출 + __all__
    apps/workspace/shell/layout.py          builders/layout.py
                                            (center/left/right/bottom -- 창 골격)
    apps/workspace/shell/toolbar.py         builders/toolbar.py
                                            (toolbar/menu/statusbar)
    apps/workspace/shell/configure_page.py  pages/configure.py
    apps/workspace/shell/settings_page.py   pages/settings.py

    apps/workspace/shared/__init__.py       재수출 + __all__
    apps/workspace/shared/widgets.py        apps/dialogs/_widgets.py
    apps/workspace/shared/image_utils.py    apps/dialogs/_image_utils.py
    apps/workspace/shared/sizing.py         apps/workspace/sizing.py

`configure.py` 와 `settings.py` 가 `shell/` 인 이유: scene 콤보도 로봇 설정도
카메라도 함께 있는 **앱 수준 설정 페이지**라 한 기능이 아닙니다.

## 이름에서 밑줄을 떼세요

`_widgets` -> `widgets`, `_image_utils` -> `image_utils`. 패키지 밖에서
임포트하는 것에 밑줄이 있으면 이름이 거짓말을 합니다 (`gui_widgets` 분해
때 같은 이유로 뗐습니다).

## 다 끝난 뒤 확인

    apps/workspace/builders/   비었으면 지운다
    apps/workspace/pages/      비었으면 지운다
    apps/workspace/domains/    이미 8-5 에서 비었어야 한다
    apps/dialogs/              비었으면 지운다

비어서 지웠는지, 남은 것이 있으면 무엇이 왜 남았는지 보고에 적으세요.
**억지로 지우지 마세요.**

## 마지막으로 CLAUDE.md

`## Layout = dependency direction` 절의 `apps/` 설명을 새 구조에 맞게
고치세요. 한두 줄이면 됩니다:

    apps/workspace/features/<기능>/   한 기능의 도메인·화면·대화상자
    apps/workspace/shell/             창 골격과 앱 수준 설정 페이지
    apps/workspace/shared/            여러 기능이 함께 쓰는 위젯·헬퍼

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 통과. 그리고 임포트 순서에 따라 깨지지 않는지 각각 확인하세요:

    for m in apps.collect_workspace apps.workspace.shell apps.workspace.shared \
             apps.workspace.features.scene apps.workspace.features.dataset; do
      QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
    import sys; sys.path.insert(0,'.')
    from PyQt6.QtWidgets import QApplication; QApplication([])
    import $m" && echo "OK $m" || echo "FAIL $m"
    done

`pages` 를 먼저 임포트하면 죽는 순환이 실제로 있었습니다 (2026-09-02).

## 보고

최종 폴더 구성(폴더별 파일 수와 줄 수), 지운 폴더, 남긴 것과 그 이유.
