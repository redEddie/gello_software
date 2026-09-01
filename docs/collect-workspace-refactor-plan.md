# collect_workspace.py 분해 계획 (2026-09-01)

## 현재 상황

- `apps/collect_workspace.py`는 4,955줄, `WorkspaceWindow` 클래스에 메서드 229개.
- Plan 1단계(대화상자 추출) 완료: 7개 dialog + `_widgets.py` + `_image_utils.py`를 `apps/dialogs/`로 이동.
- Plan 2단계(Builder 분리) 완료: 21개 `_build_*` / `_page_*` 메서드를 `apps/workspace/builders/`와 `apps/workspace/pages/`로 이동, `collect_workspace.py`는 -1,772줄.
- Plan 2단계 후속 정리:
  - 빌더/페이지 함수 이름을 공개형 `build_*`으로 통일, `builders/__init__.py`와 `pages/__init__.py`에 `__all__` 추가.
  - 공유 상수(`LOG_DIR`, `ACTIVITIES`, `WIDE_FIELDS`, `PLAYBACK_SPEEDS`)를 `apps/workspace/constants.py`로 분리.
  - UI sizing 헬퍼(`relax_min_widths`, `shrinkable_combo`, `grid_overlay`)를 `apps/workspace/builders/sizing.py`로 분리.
  - `test_app_structure.py`가 `apps/` 전체의 클래스 중복 / 이름 해석 / 화살표 방향 / 순환 임포트 / `__all__` 계약을 검증.
  - `tests/gui/run_all.sh`는 총 18개 테스트를 일괄 실행.

## 목표

`WorkspaceWindow`의 책임을 기능 단위 모듈로 분리하여:

- 단일 클래스의 메서드 수를 100개 이하로 줄인다.
- "파일/디렉터리만 봐도 의존 방향이 보인다"는 불변식을 유지한다.
- UI 조립, 페이지 정의, 상태 관리, 하드웨어 제어를 분리한다.

## 불변식

분해 전체에서 지켜야 할 규칙:

1. 한 클래스는 한 모듈에만 정의한다. `test_app_structure.py` 의 AST 중복 검사가 `apps/` 전체를 본다.
2. `apps/` -> `gello/` 의존은 허용, `gello/` -> `apps/` 의존은 금지.
3. 순환 임포트 금지. `test_app_structure.py` 가 `apps/` 전체의 순환과 화살표 역류를 검사한다.
4. 상태는 가능한 한 순수 데이터 클래스/객체로 묶고, 상태 변경은 모델의 메서드를 통해서만 한다. UI 코드가 모델의 필드에 직접 대입하지 않는다.
5. 하드웨어/프로세스 제어(로봇, 카메라 노드, QProcess)는 WorkspaceWindow가 마지막으로 소유한다.

## Phase 2 - Builder 분리 (완료)

기준: 아무도 되부르지 않는 잎사귀부터 뺀다. `_build_*`와 `_page_*`는 외부에서 호출되는 진입점이 `__init__` 1개뿐이라 가장 먼저 분리한다.

파일 구조:

```
apps/workspace/
  __init__.py
  constants.py    # LOG_DIR, ACTIVITIES, WIDE_FIELDS, PLAYBACK_SPEEDS
  builders/
    __init__.py   # __all__ + build_* 재수출
    sizing.py     # relax_min_widths, shrinkable_combo, grid_overlay
    toolbar.py    # build_toolbar, build_menu, build_statusbar
    layout.py     # build_layout, build_center, build_left, build_right, build_bottom
    gallery_tab.py  # build_gallery_tab
    trim_tab.py     # build_trim_tab
    layout_tab.py   # build_layout_tab
    cloud_tab.py    # build_cloud_tab
    depth_tab.py    # build_depth_tab
    analysis_tab.py # build_analysis_tab
  pages/
    __init__.py   # PAGE_BUILDERS dict, __all__
    configure.py  # build_configure
    collect.py    # build_collect
    dataset.py    # build_dataset
    upload.py     # build_upload
    stats.py      # build_stats
    layout.py     # build_layout_page (빌더 layout.py와 이름 충돌 방지)
    settings.py   # build_settings
```

총 21개 함수:

- `build_gallery_tab`, `build_center`, `build_left`, `build_right`, `build_bottom`, `build_layout`
- `build_toolbar`, `build_menu`, `build_statusbar`
- `build_trim_tab`, `build_layout_tab`, `build_cloud_tab`, `build_depth_tab`, `build_analysis_tab`
- `build_configure`, `build_collect`, `build_dataset`, `build_upload`, `build_stats`, `build_layout_page`, `build_settings`

방식:

- 빌더 함수는 `WorkspaceWindow` 인스턴스(`win`)를 인자로 받아 위젯을 만들고 `win`에 할당한다. 예: `build_toolbar(win)`.
- 페이지도 동일하게 모듈 함수 `build_<name>(win) -> QWidget`로 분리한다. 페이지를 `QWidget` 서브클래스로 만들지 않는다 -- 그러면 창과 페이지가 서로를 참조하게 된다.
- 분리 즉시 `WorkspaceWindow`에서 원본 메서드를 삭제하고, `apps/workspace/builders/__init__.py`와 `apps/workspace/pages/__init__.py`에서 편의 임포트를 제공한다.
- `build_left`는 `pages.PAGE_BUILDERS[key](win)`만 사용하며, 페이지가 모두 분리된 뒤 `getattr(win, "_page_...")` fallback은 제거한다.

검증:

- [x] `python -m py_compile apps/collect_workspace.py` 및 새 모듈 전체
- [x] `bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python` 18/18 통과
- [x] 옮긴 이름의 잔여 참조 확인: `grep -cw <이름> apps/collect_workspace.py` 0

## Phase 3 - Data Model 분리

기준: 빌더가 빠져야 진짜 상태가 보인다. `WorkspaceWindow`에 흩어진 상태 변수를 순수 데이터 객체로 묶는다.

파일: `apps/workspace/models.py`

대상 상태:

- 세션 식별: `_session_id`, `_session_scene_id`, `_session_slot_counts`, `_next_iid`, `_auto_assign_iid`
- 설정/인벤토리: `_known_slots`, `_current_plan`, `_scene_session_file`
- 저장소: `_recents_valid_repo`
- UI 상태: `_current_page`, `_current_task`, `_last_*`, `_activity_*`
- 카메라 / 노드 상태: `_camera_*`, `_node_*`, `_depth_*`

방식:

- `WorkspaceModel` 또는 `SessionState` dataclass를 만들고, `WorkspaceWindow.__init__`에서 인스턴스를 생성한다.
- 메서드는 처음에는 `WorkspaceWindow`에서 `self._model.xxx()` 형태로 호출하도록 옮기고, 나중에 콜백/시그널로 완전 분리한다.
- 상태 변경은 모델의 메서드를 통해서만 한다. UI 코드가 모델의 필드에 직접 대입하지 않는다.

검증:

- [ ] `py_compile`
- [ ] `bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python` 18/18 통과
- [ ] 옮긴 이름의 잔여 참조 확인

## Phase 4 - Domain 분리

기준: 상태가 정리돼야 화살표가 한쪽이 된다. 수집 제어, 트림, 업로드 등 하드웨어/프로세스 제어 로직을 분리한다.

파일 구조:

```
apps/workspace/
  domains/
    __init__.py
    collection.py  # 녹화/성공/실패/폐기, gate, reset, worker 신호 연결
    trim.py        # 재생/트림, HDF5 tree
    upload.py      # 업로드 파이프라인 실행
    replay.py      # 에피소드 재생
    teleop.py      # teleop wall, 자세 정렬 (#44 케이블 풀기는 아직 없음)
    camera.py      # 프리뷰, 카메라 노드, 포인트 클라우드
    gallery.py     # 갤러리 탭
```

방식:

- 각 domain 클래스는 `WorkspaceModel`을 읽고, `WorkspaceWindow`에 시그널/콜백으로만 결과를 돌려준다.
- 하드웨어/프로세스 제어는 domain이 실행하지만, 실제 QProcess/robot handle은 WorkspaceWindow가 소유한다(불변식 5).

검증:

- [ ] `py_compile`
- [ ] `bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python` 18/18 통과
- [ ] 화면 검증: offscreen 또는 실제 GUI에서 전체 처리/새 씬 대화상자 열어보기

## 검증 체크리스트 (모든 phase 공통)

- [ ] `python -m py_compile apps/collect_workspace.py` 및 새 모듈 전체
- [ ] `bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python` 18/18 통과 (개별 테스트를 골라 돌리지 않는다 -- 2026-09-01 회귀 2건이 그 구멍으로 샜다)
- [ ] 옮긴 이름의 잔여 참조 확인: `grep -cw <이름> <원본파일>` (매 단계 고아 임포트가 남았다)
- [ ] 화면 검증: offscreen 또는 실제 GUI에서 전체 처리/새 씬 대화상자 열어보기

## 단기계획 (다음에 바로 시작할 것)

**Phase 3 - Data Model 분리**를 진행한다.

1. `apps/workspace/models.py` 생성.
2. `WorkspaceWindow.__init__`의 상태 변수를 그룹별로 `WorkspaceModel` 속성으로 이동.
3. 상태를 읽기/쓰기하는 메서드를 `WorkspaceModel`로 옮기고, `WorkspaceWindow`에서는 `self._model.xxx()` 형태로 호출.
4. `py_compile` + `bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python` 18/18 통과.
5. 상태 그룹 하나당 커밋 하나로 나눈다: 세션 / 저장소 / 카메라·노드 / UI 상태. 한 커밋에 229개 메서드를 다 손대면 무엇이 무엇을 깨뜨렸는지 추적할 수 없다.
