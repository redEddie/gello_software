# collect_workspace.py 분해 계획 (2026-09-01)

## 현재 상황

- `apps/collect_workspace.py`는 6,700줄, `WorkspaceWindow` 클래스에 메서드 251개.
- Plan 1단계(대화상자 추출) 완료: 7개 dialog + `_widgets.py` + `_image_utils.py`를 `apps/dialogs/`로 이동, `collect_workspace.py`는 -1,689줄.
- Plan 1단계 후속 정리(Claude 검증 반영):
  - `pipeline_dialog.py` / `new_scene_dialog.py`의 중복 정의(`StatusLight`, `SceneInfoView`, `PlanJsonDialog`, `_relax_min_widths`, `_shrinkable_combo`) 제거.
  - `tests/gui/test_hub_upload_state.py`에 `scripts=` 인자 추가.
  - `tests/gui/test_dialog_modules.py` 신규: `apps/dialogs` 패키지의 클래스 중복/이름 해결/순환 임포트/`__all__` 계약 검증.
  - `tests/gui/run_all.sh`에 `test_dialog_modules` 추가.

## 목표

`WorkspaceWindow`의 책임을 기능 단위 모듈로 분리하여:

- 단일 클래스의 메서드 수를 100개 이하로 줄인다.
- "파일/디렉터리만 봐도 의존 방향이 보인다"는 불변식을 유지한다.
- UI 조립, 페이지 정의, 상태 관리, 하드웨어 제어를 분리한다.

## 불변식

분해 전체에서 지켜야 할 규칙:

1. 한 클래스는 한 모듈에만 정의한다. `test_dialog_modules.py`의 AST 중복 검사를 WorkspaceWindow 분해에도 확장한다.
2. `apps/` -> `gello/` 의존은 허용, `gello/` -> `apps/` 의존은 금지.
3. 순환 임포트 금지. `apps/dialogs`에서 시작한 `test_dialog_modules` 검증을 `apps/workspace`로 확장한다.
4. 상태는 가능한 한 순수 데이터 클래스/객체로 묶고, UI 메서드는 상태를 읽기만 한다.
5. 하드웨어/프로세스 제어(로봇, 칩 인자 camera node, QProcess)는 WorkspaceWindow가 마지막으로 소유한다.

## Phase 2 - Builder 분리

기준: 아무도 되부르지 않는 잎사귀부터 뺀다. `_build_*`와 `_page_*`는 외부에서 호출되는 진입점이 `__init__` 1개뿐이라 가장 먼저 분리한다.

파일 구조:

```
apps/workspace/
  __init__.py
  builders/
    __init__.py
    toolbar.py      # _build_toolbar, _build_menu, _build_statusbar
    layout.py       # _build_layout, _build_center, _build_left, _build_right, _build_bottom
    gallery_tab.py  # _build_gallery_tab
    trim_tab.py     # _build_trim_tab
    layout_tab.py   # _build_layout_tab
    cloud_tab.py    # _build_cloud_tab
    depth_tab.py    # _build_depth_tab
    analysis_tab.py # _build_analysis_tab
  pages/
    __init__.py
    configure.py    # _page_configure + scene combo / slot panel helpers
    collect.py      # _page_collect + activity / view helpers
    dataset.py      # _page_dataset + dataset tree helpers
    upload.py       # _page_upload + repo edit helpers
    stats.py        # _page_stats + progress / rank helpers
    layout.py       # _page_layout + crop helpers
    settings.py     # _page_settings + camera / schema helpers
```

총 21개 메서드:

- `_build_gallery_tab`, `_build_center`, `_build_left`, `_build_right`, `_build_bottom`, `_build_layout`
- `_build_toolbar`, `_build_menu`, `_build_statusbar`
- `_build_trim_tab`, `_build_layout_tab`, `_build_cloud_tab`, `_build_depth_tab`, `_build_analysis_tab`
- `_page_configure`, `_page_collect`, `_page_dataset`, `_page_upload`, `_page_stats`, `_page_layout`, `_page_settings`

방식:

- 빌더 함수는 `WorkspaceWindow` 인스턴스(`win`)를 인자로 받아 위젯을 만들고 `win`에 할당한다. 예: `build_toolbar(win)`.
- 페이지 클래스는 `QWidget`을 상속받고, 초기에는 `win._xxx` 직접 접근을 허용한다. 점진적으로 콜백/시그널로 교체한다.
- 분리 즉시 `WorkspaceWindow`에서 원본 메서드를 삭제하고, `apps/workspace/builders/__init__.py`에서 편의 임포트를 제공한다.

검증:

- `py_compile`
- `PYTHONPATH="" python tests/gui/test_plan_form.py` (WorkspaceWindow 인스턴스 생성)
- offscreen GUI smoke test

## Phase 3 - Data Model 분리

기준: 빌더가 빠져야 진짜 상태가 보인다. `WorkspaceWindow`에 흩어진 상태 변수 257개를 순수 데이터 객체로 묶는다.

파일: `apps/workspace/models.py`

대상 상태:

- 세션 식별: `_session_id`, `_session_scene_id`, `_session_slot_counts`, `_next_iid`, `_auto_assign_iid`
- 설정/인벤토리: `_known_slots`, `_current_plan`, `_scene_session_file`, `_apply_session_config`
- 저장소: `_recents_valid_repo`, `repo_id_for`, `_check_repo`
- UI 상태: `_current_page`, `_current_task`, `_last_*`, `_activity_*`
- 칩 인서 camera / 노드 상태: `_camera_*`, `_node_*`, `_depth_*`

방식:

- `WorkspaceModel` 또는 `SessionState` dataclass를 만들고, `WorkspaceWindow.__init__`에서 인스턴스를 생성한다.
- 메서드는 처음에는 `WorkspaceWindow`에서 `self._model.xxx()` 형태로 호출하도록 옮기고, 나중에 콜백/시그널로 완전 분리한다.
- UI 메서드가 `self._model`을 직접 읽는 것은 허용하되, UI 메서드가 상태를 쓰지는 않는다.

검증:

- `py_compile`
- `tests/gui/test_plan_form.py`, `test_right_scene.py`
- `test_dialog_modules.py` 패턴을 `apps/workspace`로 확장한 테스트 초안 추가

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
    teleop.py      # teleop wall, 자세 정렬, 180도 자동 해제
    camera.py      # 프리뷰, 칩 인에 camera node, 포인트클우드
    gallery.py     # 갤러리 탭
```

방식:

- 각 domain 클래스는 `WorkspaceModel`을 읽고, `WorkspaceWindow`에 시그널/콜백으로만 결과를 돌려준다.
- 하드웨어/프로세스 제어는 domain이 실행하지만, 실제 QProcess/robot handle은 WorkspaceWindow가 소유한다(불변식 5).

검증:

- `py_compile`
- `tests/gui/run_all.sh` 전체 통과
- 화면 검증: offscreen 또는 실제 GUI에서 전체 처리/새 씬 대화상자 열어보기

## 검증 체크리스트 (모든 phase 공통)

- [ ] `python -m py_compile apps/collect_workspace.py` 및 새 모듈 전체
- [ ] `PYTHONPATH="" python tests/gui/test_dialog_modules.py` (구조 계약)
- [ ] `PYTHONPATH="" python tests/gui/test_plan_form.py`
- [ ] `PYTHONPATH="" python tests/gui/test_grid_replay.py`
- [ ] `PYTHONPATH="" python tests/gui/test_scene_edit.py`
- [ ] `tests/gui/run_all.sh` 17/17 통과
- [ ] 화면 검증: offscreen 또는 실제 GUI에서 전체 처리/새 씬 대화상자 열어보기

## 단기계획 (다음에 바로 시작할 것)

**Phase 2 - Builder 분리**만 먼저 진행한다.

1. `apps/workspace/` 패키지 생성, `builders/`와 `pages/` 서브패키지 생성.
2. `toolbar.py`/`layout.py`/`gallery_tab.py`/`trim_tab.py`/`layout_tab.py`/`cloud_tab.py`/`depth_tab.py`/`analysis_tab.py`로 `_build_*` 14개 이동.
3. `pages/configure.py`/`collect.py`/`dataset.py`/`upload.py`/`stats.py`/`layout.py`/`settings.py`로 `_page_*` 7개 이동.
4. `collect_workspace.py`에서 이동한 21개 메서드 삭제, `apps/workspace/builders/__init__.py`에서 편의 임포트.
5. `test_dialog_modules.py`를 `apps/workspace`로 확장할 초안 테스트 추가.
6. `py_compile` + `test_plan_form.py` + `test_grid_replay.py` + `test_scene_edit.py` + offscreen GUI smoke test.
7. 커밋.

Phase 3(Data Model)은 Phase 2 커밋 후 진행한다.
