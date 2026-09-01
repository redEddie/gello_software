# 1000줄 넘는 파일 리팩토링 전략 (Phase 5 준비)

**작업 날짜:** 2026-09-02  
**원칙:** 이 문서는 측량과 판단만 담는다. 아래 분석을 바탕으로 Phase 5에서 실제 파일을 쪼갠다.  

## 1. 대상 파일

```bash
find . -name '*.py' -not -path './.git/*' -not -path './third_party/*' \
  | xargs wc -l | sort -rn | awk '$1>1000 && $2!="total"'
```

위 명령으로 찾은 대상은 **6개**다.

| 순위 | 파일 | 줄 수 | 최상위 클래스 수 | 최상위 함수 수 |
|------|------|------:|----------------:|---------------:|
| 1 | `apps/collect_workspace.py` | 1,564 | 1 | 4 |
| 2 | `gello/gui/gui_widgets.py` | 1,484 | 12 | 8 |
| 3 | `gello/gui/libero_gui_worker.py` | 1,427 | 3 | 0 |
| 4 | `gello/data/libero_format.py` | 1,367 | 3 | 23 |
| 5 | `scripts/convert/convert_libero_to_lerobot.py` | 1,131 | 0 | 21 |
| 6 | `gello/robots/joint_limit_wall.py` | 1,122 | 2 | 6 |

## 2. 참고: 방금 지나온 길

`apps/collect_workspace.py`는 2026-08-31 ~ 09-02 사이에 8,437줄에서 1,564줄로 줄었다. 순서는 다음과 같았다.

1. 독립 대화상자 7개 → `apps/dialogs/`
2. 빌더·페이지 21개 → `apps/workspace/builders/`, `apps/workspace/pages/` (모듈 함수 `build_x(win)`)
3. 상태 116개 → `apps/workspace/models.py` (dataclass 4개)
4. 도메인 8개 → `apps/workspace/domains/` (`win`을 든 클래스 `XOps`)

매 단계 **자리만 옮기고 동작은 그대로** 두었기 때문에 기계적으로 검증할 수 있었다. 검증은 `tests/gui/run_all.sh` 하나로 통일했다(20개).

이 과정에서 실제로 난 사고 세 가지는 전부 "테스트는 통과하는데 그 버튼만 죽는" 형태였다.

- 클래스를 복사하고 원본을 안 지워 한 이름에 정의가 둘이 됨
- 함수를 옮기고 호출부를 안 고침
- 옮긴 함수 바로 아래 붙어 있던 상수 두 개가 같이 지워짐 (Tools 메뉴 두 항목이 눌리는 순간 죽는 상태로 커밋될 뻔했다)

이에 해당하는 안전망:

- `tests/gui/test_app_structure.py` — 중복 정의, 잘못된 임포트 탐지
- `tests/gui/test_ui_surface.py` — 버튼/액션 이름과 연결 누락 탐지
- `tests/gui/test_domain_attrs.py` — `XOps` 클래스가 `win`에서 읽는 속성 이름 변경 탐지

Phase 5에서도 동일한 방식을 따를 것이다: 잎사귀부터, 한 조각 = 한 커밋, 위젯은 모델에 넣지 않기, 옮긴 뒤 원본에 남은 이름을 `grep`으로 세기.

---

## 3. 파일별 분석

### 3.1 `apps/collect_workspace.py`

#### 무엇을 하는 파일인가

FR3 GELLO 데이터 수집 워크스페이스의 진입점이다. Phase 4 분해 후 남은 `WorkspaceWindow` 클래스(1,311줄, 62개 메서드)는 여전히 "GUI 상태 허브" 역할을 한다: 활동(activity) 전환, 갤러리/레이아웃 탭, 로봇 워커 연결, 로그/프로세스 관리, 데이터셋/업로드 패널 상태를 한 클래스에서 조율한다. `apps/workspace/`의 builders/pages/domains/models가 이미 분리되어 있어 이 파일은 이제 "윈도우 껍데기 + 연결 코드"만 남았다고 보이지만, 1,500줄이 넘는 이유는 여전히 너무 많은 책임이 한 클래스에 붙어 있기 때문이다.

#### 왜 커졌는가

책임이 여러 개다. 특히 다음 그룹이 한 클래스에 있다.

- **gallery**: scene 파일 갤러리 로드/필터/표시
- **layout**: LIBERO 초기 레이아웃 참조 이미지 오버레이/슬라이드쇼
- **center/crop**: 중앙 탭 전환, 크롭 슬라이더
- **worker**: `CollectionWorker` 연결/상태 수신
- **process**: `runme.sh`, `launch_nodes.py`, `check_cameras.py` 등 외부 프로세스 관리
- **tuning/reset**: 리더 보호 리셋, 칩 조정
- **dataset/upload**: 저장소 입력, 업로드 버튼, 데이터셋 트리
- **activity/log**: 왼쪽 패널 전환, 로그 뷰

#### 측정 표

| 항목 | 값 |
|------|---:|
| 총 줄 수 | 1,564 |
| 최상위 클래스 수 | 1 |
| 최상위 함수 수 | 4 |
| 최대 클래스 | `WorkspaceWindow` (1,311줄) |
| `WorkspaceWindow` 메서드 수 | 62 |
| 인스턴스 속성 수 | 118 |
| 가장 널리 쓰이는 속성 | `log` (18개 메서드), `cameras` (13), `session` (11), `worker` (9), `camera_ops` (8) |

**메서드 그룹별 호출 관계**

| 그룹 | 메서드 수 | 줄 수 | 밖으로 부르는 횟수 | 밖에서 부르는 횟수 | 비고 |
|------|----------:|------:|------------------:|------------------:|------|
| gallery | 5 | 87 | 0 | 0 | 거의 독립 |
| layout | 13 | 155 | 2(activity_log) | 4(center_crop) | 오버레이 전용 |
| center_crop | 4 | 61 | 5 | 0 | layout과 밀접 |
| worker | 3 | 96 | 4 | 0 | 연결/해제 |
| activity_log | 11 | 124 | 1 | 34 | **중심 허브** |
| process | 8 | 109 | 14 | 3 | 외부 프로세스 |
| tuning_reset | 4 | 104 | 16 | 1 | 하드웨어 보호 |
| dataset_upload | 11 | 250 | 3 | 3 | 저장소/업로드 UI |

`activity_log`가 34번 불리는 것은 모든 그룹이 로그/활동 전환을 거쳐야 하기 때문이다. 이는 자연스러운 중심 허브 역할이지만, **dataset_upload**와 **process**가 `WorkspaceWindow`에 붙어 있는 것은 분해 대상이다.

#### 이음매 후보

1. **gallery 그룹** → `apps/workspace/domains/gallery.py` 또는 `apps/workspace/pages/gallery.py`
   - 밖에서 부르는 횟수 0, 밖으로 부르는 횟수 0. 완전히 독립적이다.
   - 다만 `apps/workspace/pages/dataset.py`와 `apps/workspace/builders/gallery_tab.py`가 이미 존재하므로, 갤러리 로직을 domain으로 옮기고 builder에서 호출하는 형태가 자연스럽다.

2. **layout 그룹 + center_crop 그룹** → `apps/workspace/domains/layout.py`
   - `center_crop`이 `layout`을 4번 부르고, 둘 다 칩/프레이밍과 관련있다.
   - `camera_ops`와도 연결되므로 domain 형태(`win`을 든 클래스)가 맞다.

3. **process 그룹** → `apps/workspace/domains/process.py` 또는 별도 모듈
   - 외부 `QProcess`를 관리하는 책임. `ProcessRegistry`는 이미 `models.py`에 있지만, 실제 프로세스 시작/로그 파싱 로직은 분리할 수 있다.
   - `tuning_reset`과 상호 호출이 4번 있다(서로 부름). 같은 도메인으로 묶거나 인터페이스를 정리해야 한다.

4. **dataset_upload 그룹** → `apps/workspace/domains/upload.py`에 이미 있음
   - 실제로 `apps/workspace/domains/upload.py`가 존재하고 `UploadOps`가 있지만, `WorkspaceWindow`에 여전히 저장소 입력 검증(`_on_repo_edited`, `_recents_valid_repo`), 레이블 갱신(`_refresh_verdict_label`, `_refresh_schema_label`), 업로드 버튼 생성(`_upload_button`) 등이 남아 있다.
   - 이들을 `UploadOps`로 완전히 옮기면 `WorkspaceWindow`에서 250줄 이상 줄일 수 있다.

#### 위험

- `WorkspaceWindow`는 거의 모든 GUI 테스트의 출발점이다. `tests/gui/run_all.sh` 20개 중 상당수가 이 파일을 직접/간접 import한다.
- `test_app_structure.py`, `test_ui_surface.py`, `test_domain_attrs.py`가 안전망 역할을 한다.
- **위험한 부분**: `eventFilter`(83줄), `closeEvent`(22줄), `_set_activity`(18줄)는 테스트가 키보드/윈도우 닫기 이벤트를 실제로 시뮬레이션하지 않는다. `process` 그룹의 `QProcess` 로그 파싱도 offscreen 테스트로는 커버되지 않는다.

#### 제안

`WorkspaceWindow`에서 다음 순서로 분리한다.

1. **gallery 그룹** → `apps/workspace/domains/gallery.py` (예상 100줄)
2. **layout + center_crop** → `apps/workspace/domains/layout_overlay.py` (예상 250줄)
3. **process + tuning_reset** → `apps/workspace/domains/process.py` 확장 (예상 250줄)
4. **dataset_upload 잔여** → `apps/workspace/domains/upload.py` 확장 (예상 200줄)

이렇게 하면 `WorkspaceWindow`는 약 **700~800줄**로 줄어든다. 남은 부분은 activity 전환, 로깅, worker 연결, 이벤트 필터 등 "창 허브" 역할만 담당하게 된다.

---

### 3.2 `gello/gui/gui_widgets.py`

#### 무엇을 하는 파일인가

PyQt 위젯과 다이얼로그 모음이다. 수집 GUI(구 마법사 + 신 워크스페이스)가 공유하는 독립 조각들을 모아 놓은 파일이다. 클래스 12개, 함수 8개가 한 파일에 있다: `Recents`, `VideoView`, `DeltaBar`, `EpisodeLoadWorker`, `GalleryLoadWorker`, `CameraPreviewWorker`, `DepthCloudWorker`, `HfAccountDialog`, `DatasetSchemaDialog`, `LerobotConvertDialog`, `HdfUploadDialog`, `RepackDialog`.

#### 왜 커졌는가

**한 책임이 아니라 여러 독립 위젯의 모음**이다. 원래 쪼개기에 가장 적합한 형태다. 파일 docstring 자체가 "Split out of the old wizard GUI so the workspace UI could reuse them"이라고 말하고 있다.

#### 측정 표

| 항목 | 값 |
|------|---:|
| 총 줄 수 | 1,484 |
| 최상위 클래스 수 | 12 |
| 최상위 함수 수 | 8 |
| 최대 클래스 | `LerobotConvertDialog` (211줄, 6개 메서드) |
| 평균 클래스 크기 | 약 110줄 |

| 클래스 | 줄 수 | 메서드 수 | 책임 |
|--------|------:|----------:|------|
| `Recents` | 43 | 4 | 최근 입력 JSON 관리 |
| `VideoView` | 118 | 8 | 칩라이드/가이드 오버레이 |
| `DeltaBar` | 31 | 2 | 관절 오차 게이지 |
| `EpisodeLoadWorker` | 36 | 2 | 에피소드 이미지 백그라운드 로드 |
| `GalleryLoadWorker` | 24 | 2 | scene 갤러리 백그라운드 로드 |
| `CameraPreviewWorker` | 51 | 3 | 칩라 미리보기 스레드 |
| `DepthCloudWorker` | 83 | 3 | depth/point-cloud 스레드 |
| `HfAccountDialog` | 126 | 5 | HF 계정 전환 다이얼로그 |
| `DatasetSchemaDialog` | 157 | 4 | 데이터셋 스키마 미리보기 |
| `LerobotConvertDialog` | 211 | 6 | LeRobot 변환 인자 다이얼로그 |
| `HdfUploadDialog` | 185 | 6 | HDF5 업로드 인자 다이얼로그 |
| `RepackDialog` | 111 | 3 | HDF5 재압축 선택 다이얼로그 |

함수들: `repo_id_error`, `hf_account`, `hf_stored_accounts`, `hf_switch_account`, `hf_add_account`, `is_progress_line`, `clean_stream_lines`, `np_to_pixmap`.

#### 이음매 후보

각 클래스가 거의 독립적이다. `VideoView`와 `np_to_pixmap`만 밀접하게 연결된다.

분리 후보:
- `gello/gui/widgets/video_view.py` — `VideoView`, `np_to_pixmap`
- `gello/gui/widgets/recents.py` — `Recents`
- `gello/gui/widgets/delta_bar.py` — `DeltaBar`
- `gello/gui/workers/` — `EpisodeLoadWorker`, `GalleryLoadWorker`, `CameraPreviewWorker`, `DepthCloudWorker`
- `gello/gui/dialogs/hf_account.py` — `HfAccountDialog` + HF 계정 함수 4개
- `gello/gui/dialogs/schema.py` — `DatasetSchemaDialog`
- `gello/gui/dialogs/convert.py` — `LerobotConvertDialog`
- `gello/gui/dialogs/upload.py` — `HdfUploadDialog`
- `gello/gui/dialogs/repack.py` — `RepackDialog`
- `gello/gui/utils/progress.py` — `is_progress_line`, `clean_stream_lines`
- `gello/gui/utils/repo.py` — `repo_id_error`

#### 위험

- 이 파일은 24개 파일에서 import한다. 분해 시 임포트 경로를 모두 갱신해야 한다.
- `tests/gui/test_hub_upload_state.py`, `test_depth17.py`, `test_diversity_cloud.py`가 관련 테스트.
- `test_ui_surface.py`가 버튼 연결을 검사하지만, **다이얼로그 난독화 후 private 메서드명이 바뀌면 테스트가 깨질 수 있다**.
- `Recents`의 `RECENTS_PATH`는 모듈 상수로 다른 테스트에서도 오염 위험이 있다(주석에도 언급됨). 분리할 때도 동일한 경로 상수를 유지해야 한다.

#### 제안

이 파일은 **쪼개는 것이 명박히 정답**이다. 다만 12개 클래스를 한 번에 옮기면 변경량이 너무 크다. 다음 순서를 제안한다.

1. `gello/gui/widgets/` 디렉터리 만들고 `VideoView`/`DeltaBar`/`Recents` 옮기기 (3개 클래스, 약 200줄)
2. `gello/gui/workers/` 디렉터리 만들고 4개 Worker 옮기기 (약 200줄)
3. `gello/gui/dialogs/` 디렉터리 만들고 4개 다이얼로그 옮기기 (약 700줄)
4. 유틸리티 함수(`repo_id_error`, progress, HF 계정)는 사용처에 맞게 각 위젯/다이얼로그 모듈로 같이 옮기거나 `gello/gui/utils/`로 분리

최종적으로 `gui_widgets.py`는 삭제되고, import 경로만 `gello.gui.widgets.xxx` 형태로 바뀐다.

---

### 3.3 `gello/gui/libero_gui_worker.py`

#### 무엇을 하는 파일인가

GELLO 텔레옵 + LIBERO 포맷 기록을 담당하는 QThread worker다. GUI 스레드를 멈추지 않고 로봇 I/O를 처리한다. `record_dataset.py`의 검증된 제어 흐름을 Qt signal/slot 기반으로 포팅한 것이다.

#### 왜 커졌는가

`CollectionWorker` 클래스 하나가 1,185줄, 36개 메서드다. 책임이 명확히 여러 개다.

- 명령 큐 API (`cmd_*`)
- 인터럽트 드레이닝 (`_poll_cmd`, `_drain_interrupt`)
- 관측/칩라 처리 (`_get_obs`, `_emit_frames`)
- 램프/모션 (`_ramp_to`, `_home_trajectory`, `_approach_ramp`)
- 포즈 게이트/자동 정렬 (`_pose_gate`, `_auto_match_pose`)
- 에피소드 기록 (`_record_episode`)
- 메인 생명주기 (`run`, `_connect`, `_reset_wait`, `_wait_node_recovery`)

#### 측정 표

| 항목 | 값 |
|------|---:|
| 총 줄 수 | 1,427 |
| 최상위 클래스 수 | 3 |
| 최상위 함수 수 | 0 |
| 최대 클래스 | `CollectionWorker` (1,185줄) |
| `CollectionWorker` 메서드 수 | 36 |
| 인스턴스 속성 수 | 56 |
| 가장 널리 쓰이는 속성 | `_cmds` (13개 메서드), `_robot` (9), `log_message` (9), `_teleop` (8), `_get_obs` (5), `_writer` (5) |

**메서드 그룹별 호출 관계**

| 그룹 | 메서드 수 | 줄 수 | 밖으로 부르는 횟수 | 밖에서 부르는 횟수 | 비고 |
|------|----------:|------:|------------------:|------------------:|------|
| cmd_api | 11 | 34 | 0 | 1 | 외부에서만 부름 |
| drain | 5 | 95 | 0 | 8 | 낮은 수준 유틸 |
| obs | 3 | 105 | 0 | 14 | 데이터 획득 |
| ramp | 6 | 245 | 10 | 3 | 모션 생성 |
| gate_match | 3 | 208 | 5 | 2 | 정렬 |
| recording | 1 | 99 | 5 | 1 | 기록 |
| lifecycle | 6 | 305 | 9 | 0 | orchestration |

`lifecycle`이 모든 그룹을 부르는 orchestrator이고, `obs`가 가장 많이 불리는(14번) 하위 서비스다.

#### 이음매 후보

1. **obs 그룹** (`_get_obs`, `_emit_frames`, `_joint_vec`) → 별도 모듈
   - 밖에서 14번 불림, 밖으로 부르는 횟수 0. 완벽한 잎사귀.
   - `CollectionWorker`가 `_robot`, `_teleop`, `cfg`를 주입받으면 obs를 만들 수 있다.

2. **ramp 그룹 + gate_match 그룹** → `motion.py` 또는 `ramping.py`
   - `ramp`가 `drain`, `obs`를 부르고, `gate_match`가 `obs`, `drain`을 부른다.
   - 하지만 `lifecycle`에서만 호출되므로(밖에서 부르는 횟수 3과 2), 별도 클래스로 묶어 `CollectionWorker`가 위임하면 깔끔하다.

3. **recording 그룹** (`_record_episode`) → `episode_recorder.py`
   - 한 메서드가 99줄. `drain`, `obs`를 부른다.
   - 에피소드 기록 정책(최대 길이, 자동 저장, 칩라 stall 감지)이 별도 객체로 빠지면 `CollectionWorker`는 상태 머신만 남는다.

4. **drain 그룹** (`_poll_cmd`, `_drain_interrupt`, `_drain_match_interrupt`)
   - 명령 큐 처리는 `CollectionWorker`의 핵심이지만, 95줄이면 별도 유틸/믹스인으로 분리 가능.

#### 위험

- 이 파일은 실제 로봇/칩라 없이는 offscreen 테스트로 거의 커버되지 않는다.
- `tests/gui/test_gate_reset.py`, `test_depth17.py`, `test_match_gate.py`가 관련.
- `test_match_gate.py`는 `JointLimitWall`만 테스트하고, `CollectionWorker`의 `_pose_gate`/`_auto_match_pose`는 **유닛 테스트가 없다**.
- `_record_episode`의 칩라 stall 감지(3틱 연속 동일 프레임), `_get_obs`의 depth 지원 가드 등은 테스트되지 않는다.
- 쪼갤 때 가장 큰 위험은 `_cmds` 큐와 `self._running` 플래그를 여러 객체가 공유하면서 생기는 동시성 버그다.

#### 제안

**먼저 안전망을 만든 뒤 쪼갠다.**

안전망 우선:
- `CollectionWorker`의 상태 머신을 표로 만드는 테스트: 각 상태별 허용 명령, 다음 상태
- `_get_obs`를 mock robot으로 호출하는 테스트
- `_record_episode`를 2프레임짜리 더미로 호출하여 버퍼/저장 경로 검증

분해 순서(안전망 이후):
1. `gello/gui/worker/observations.py` — `_get_obs`, `_emit_frames`, `_joint_vec` (예상 120줄)
2. `gello/gui/worker/motion.py` — ramp + gate_match + auto_match (예상 500줄)
3. `gello/gui/worker/recorder.py` — `_record_episode` (예상 120줄)
4. `gello/gui/worker/commands.py` — drain/poll 유틸 (예상 120줄)
5. `CollectionWorker`는 orchestrator로 남음 (예상 400줄)

`EpisodeSaver`와 `WorkerConfig`는 이미 상대적으로 작으므로 별도 파일로 분리필 요소는 아니다.

---

### 3.4 `gello/data/libero_format.py`

#### 무엇을 하는 파일인가

LIBERO 포맷 HDF5 writer와 그에 딸린 도구 모음이다. action space 계산(4가지), 스키마 설명, 에피소드 버퍼, task writer, scene/legacy 공통 페이로드 쓰기, repack 상태 조회까지 한 파일에 있다.

#### 왜 커졌는가

**책임이 너무 많다.**

1. action space 수학 (`compute_delta_action`, `compute_joint_*_action`, `compute_ee_*_action`)
2. 스키마/메타데이터 설명 (`describe_schema`, `describe_episode`, `schema_from_episode`, `action_column_names`)
3. 에피소드 버퍼링 (`LiberoEpisodeBuffer`)
4. HDF5 쓰기 (`write_episode_payload`)
5. Task writer (`NullTaskWriter`, `LiberoTaskWriter`)
6. 크롭 유틸리티 (`square_crop`, `resize_rgb`, `default_crop_params`, `load/save_crop_params`)
7. repack 상태 (`hdf5_repack_status`, `_stored_bytes`)

이 중 상당수는 이미 독립 파일로 분리하기에 적합하다.

#### 측정 표

| 항목 | 값 |
|------|---:|
| 총 줄 수 | 1,367 |
| 최상위 클래스 수 | 3 |
| 최상위 함수 수 | 23 |
| 최대 클래스 | `LiberoTaskWriter` (189줄) |
| `LiberoTaskWriter` 메서드 수 | 15 |
| 인스턴스 속성 수 | 11 |
| 이 파일을 import하는 파일 수 | 16 |

**`LiberoTaskWriter` 메서드 그룹별 호출**

| 그룹 | 메서드 수 | 줄 수 | 밖으로 부르는 횟수 | 밖에서 부르는 횟수 |
|------|----------:|------:|------------------:|------------------:|
| init | 1 | 59 | 0 | 0 |
| metadata | 3 | 29 | 0 | 0 |
| episode_edit | 2 | 26 | 0 | 0 |
| buffer | 4 | 14 | 0 | 1 |
| save | 2 | 33 | 1(buffer) | 0 |
| lifecycle | 3 | 6 | 0 | 0 |

`LiberoTaskWriter` 자체는 쪼갤 필요가 없다. 문제는 파일 전체가 너무 많은 책임을 담고 있다는 점이다.

#### 이음매 후보

1. **action math** → `gello/data/actions.py`
   - `_quat_to_axis_angle`, `_quat_mul`, `_quat_conj`, `compute_*_action`
   - 순수 함수, 외부 의존성이 `numpy`뿐. 단위 테스트 쉬움.

2. **schema description** → `gello/data/schema_description.py`
   - `action_column_names`, `resolved_action_column_names`, `describe_schema`, `describe_episode`, `schema_from_episode`
   - `DatasetSchemaConfig`에만 의존.

3. **crop utilities** → `gello/data/crop.py`
   - `default_crop_params`, `crop_params_path`, `load_crop_params`, `save_crop_params`, `square_crop`, `resize_rgb`
   - `gello/core/station.py`에만 의존.

4. **episode buffer + payload writer** → `gello/data/episode_buffer.py` 또는 `gello/data/episode_io.py`
   - `LiberoEpisodeBuffer`, `write_episode_payload`
   - 둘은 밀접하게 연결되어 있어 같은 파일에 두는 것이 자연스럽다.

5. **task writer** → `gello/data/task_writer.py`
   - `NullTaskWriter`, `LiberoTaskWriter`
   - `write_episode_payload`와 `LiberoEpisodeBuffer`에 의존.

6. **repack status** → `gello/data/repack_status.py`
   - `hdf5_repack_status`, `_stored_bytes`
   - `dataset_schema` 상수에만 의존.

#### 위험

- 16개 파일에서 import한다. action space 함수는 `convert_libero_to_lerobot.py`, `gui_widgets.py`, `libero_gui_worker.py` 등 핵심 경로에서 쓰인다.
- `tests/gui/test_scene_edit.py`, `test_depth17.py`가 관련.
- **테스트가 없는 부분**: `write_episode_payload`의 4가지 action_space 분기, `hdf5_repack_status`의 mixed compression 판정, `schema_from_episode`는 "imported but never called"라고 주석이 되어 있다.
- action space 함수는 수학적으로 미묘하다(특히 `compute_joint_absolute_action` vs realized trajectory). 옮기다가 import 실수라도 나면 학습 데이터가 망가진다.

#### 제안

이 파일은 **쪼개는 것이 정답**이다. 순서:

1. `gello/data/actions.py` — action math (약 150줄)
2. `gello/data/crop.py` — crop utilities (약 120줄)
3. `gello/data/schema_description.py` — describe/schema 함수들 (약 200줄)
4. `gello/data/episode_buffer.py` — `LiberoEpisodeBuffer` + `write_episode_payload` (약 350줄)
5. `gello/data/repack_status.py` — repack 상태 (약 100줄)
6. `gello/data/task_writer.py` — `NullTaskWriter` + `LiberoTaskWriter` (약 220줄)

남은 `libero_format.py`는 상수/legacy alias만 두거나 삭제한다.

---

### 3.5 `scripts/convert/convert_libero_to_lerobot.py`

#### 무엇을 하는 파일인가

LIBERO 포맷 `.hdf5` 파일을 LeRobotDataset(parquet + video)으로 변환하는 CLI 스크립트다. `--resume`, `--push`, `--replace`, `--push-only`, `--only-success`, `--include-failed` 등 다양한 모드를 처리한다.

#### 왜 커졌는가

함수 21개 + `main` 374줄. **하나의 변환 파이프라인**이라는 단일 책임을 가지고 있지만, `main`이 374줄이라 너무 길다. 각 단계는 순차적으로 실행되고 인자를 공유한다.

#### 측정 표

| 항목 | 값 |
|------|---:|
| 총 줄 수 | 1,131 |
| 최상위 클래스 수 | 0 |
| 최상위 함수 수 | 21 |
| `main` 줄 수 | 374 |
| 이 파일을 import하는 파일 수 | 0 (CLI 진입점) |

| 함수 그룹 | 함수들 | 줄 수 | 특징 |
|----------|--------|------:|------|
| metadata | `repair_metadata`, `check_integrity`, `_fail_integrity`, `_hub_commit_message`, `_verify_tag`, `_stamp_schema_version` | 약 260 | LeRobot meta/ 조작 |
| schema | `_episode_schema`, `_check_image_shape`, `_to_target`, `_scan_schema`, `_build_features`, `_check_resume_compatible` | 약 220 | 스키마 검사/변환 |
| scene | `_is_scene_file`, `_scene_convertible`, `_load_uid_sidecar`, `_write_uid_sidecar`, `_language_instruction`, `_is_success` | 약 70 | scene-v1 처리 |
| conversion | `_task_episode_count`, `_convert_episode` | 약 90 | 실제 변환 |
| main | `main`, `_merge_card_tags` | 약 430 | orchestration |

#### 이음매 후보

이 파일은 **하나의 파이프라인**이므로 모듈 단위로 쪼개는 것은 적절하지 않다. 대신 `main` 내부의 단계를 함수로 추출하는 것이 더 낫다.

1. `main` 내부의 `--push-only` 분기 → `_push_only(args)` 함수
2. `main` 내부의 변환 루프 → `_convert_all(args, ds, schema, features, uid_records)` 함수
3. metadata 그룹 → `scripts/convert/lerobot_metadata.py`
4. schema 그룹 → `scripts/convert/lerobot_schema.py`
5. scene 그룹 → `scripts/convert/lerobot_scene.py`

하지만 이 파일은 import되는 곳이 없고, **단순히 main이 긴 것**뿐이다. 모듈 분리보다는 `main`의 함수 추출이 더 적절하다.

#### 위험

- CLI 스크립트라 offscreen GUI 테스트로는 거의 커버되지 않는다.
- `tests/gui/test_depth17.py`가 언급하는 정도.
- `--resume` + Hub 상호작용, `_hub_commit_message`의 사이드카 대조, `_verify_tag` 등은 **네트워크 없이는 테스트 불가**.
- `main`의 `_convert_episode` 내 `actions_ee` 처리, scene `edit_count` 검사 등은 복잡한 분기가 많다.

#### 제안

**쪼개지 말고 `main`을 함수로 정리하자.**

- `main`에서 `--push-only` 분기를 `_push_only(args)`로 추출 (약 120줄)
- 변환 루프와 사이드카 처리를 `_convert_all(...)`로 추출 (약 200줄)
- `_merge_card_tags`는 이미 분리되어 있음

이렇게 하면 `main`은 약 **100줄**로 줄어들고, 전체 파일은 약 **900줄**이 된다. 추가 분해는 필요하지 않다.

---

### 3.6 `gello/robots/joint_limit_wall.py`

#### 무엇을 하는 파일인가

GELLO 리더를 위한 단방향 관절 한계 벽(wall)이다. 고속 스레드에서 리더가 팔로워의 한계를 넘지 않도록 전류로 밀어낸다. 추가로 pose-match assist, gravity compensation, trigger spring, supply health monitoring을 같은 루프에서 처리한다.

#### 왜 커졌는가

`JointLimitWall` 클래스가 810줄, 11개 메서드다. 하지만 메서드 수는 적고, **한 가지 일(리더 하드웨어 제어 루프)**을 하는 클래스다. 크기의 대부분은 `__init__`(243줄, tuning parameter)과 `_run`(361줄, 실제 제어 루프)에 있다.

#### 측정 표

| 항목 | 값 |
|------|---:|
| 총 줄 수 | 1,122 |
| 최상위 클래스 수 | 2 |
| 최상위 함수 수 | 6 |
| 최대 클래스 | `JointLimitWall` (810줄) |
| `JointLimitWall` 메서드 수 | 11 |
| 인스턴스 속성 수 | 76 |
| 가장 널리 쓰이는 속성 | `_driver` (6개 메서드), `_n_arm` (5), `_stop_evt` (5), `_match_int` (4), `_match_target` (4) |

**메서드 그룹별 호출 관계**

| 그룹 | 메서드 수 | 줄 수 | 밖으로 부르는 횟수 | 밖에서 부르는 횟수 |
|------|----------:|------:|------------------:|------------------:|
| config | 3 | 67 | 0 | 0 |
| lifecycle | 4 | 48 | 0 | 0 |
| thread | 1 | 361 | 3(health) | 0 |
| health | 2 | 27 | 0 | 3 |

#### 이음매 후보

이 파일은 **쪼개지 말아야 한다**. 이유:

1. **한 가지 일을 한다**: 리더 하드웨어 제어 루프.
2. **상태 공유가 매우 밀접하다**: `_match_target`, `_match_setpoint`, `_match_int`, `_armed_mask`, `_gravity_gains` 등 70개 이상의 인스턴스 속성이 `_run` 한 메서드에서 참조된다. 분리하면 이 속성들을 어딘가로 넘겨야 하고, 그 과정에서 원자성/동시성 버그가 생긴다.
3. **하드웨어 의존성**: `set_current`가 전체 서보 벡터를 동기로 쓰므로, 제어 루프를 여러 객체로 나누면 버스 경합이 생긴다.
4. **이미 적절한 추상화가 있다**: 순수 함수 `_engage_gate`, `_well_assist`, `_wrap_pi`, `wrap_into_limits`, `_allocate_budget`, `selftest`가 모듈 최상위에 있어 단위 테스트 가능.

다만 다음과 같은 **내부 정리**는 가능하다.

- `__init__`의 tuning parameter 기본값 일부를 dataclass로 분리
- `_run` 내부의 limit spring, match assist, gravity comp, trigger spring 계산을 private helper로 추출
- 하지만 이것은 "쪼개기"가 아니라 "함수 추출"이다.

#### 위험

- `tests/gui/test_match_gate.py`가 `_engage_gate`, `_well_assist`, `wrap_into_limits` 등의 순수 함수와 `JointLimitWall`의 기본 동작을 테스트한다.
- 하지만 `_run`의 실제 제어 루프, `_read_health`/Dynamixel 통신, `set_match_target` 후 상태 변화 등은 **하드웨어 없이는 테스트되지 않는다**.
- `selftest()`가 있어 `_allocate_budget`, `_well_assist`, `_engage_gate` 등의 수학은 검증된다.

#### 제안

**그대로 둔다.** 1,100줄이 넘지만, 이는 tuning parameter와 단일 제어 루프의 복잡성 때문이지 책임 분산 문제는 아니다.

대신 다음 내부 정리를 제안한다.

1. `__init__`의 tuning defaults를 `JointLimitWallConfig` dataclass로 분리(선택)
2. `_run` 내부의 match/.limit/trigger/gravity 계산을 각각 `_compute_*` 헬퍼로 추출하여 `_run`을 200줄 이하로 줄임

이 정도면 충분하다.

---

## 4. 전체 분해 순서

| 순서 | 파일 | 제안 | 예상 결과 | 우선순위 이유 |
|------|------|------|-----------|---------------|
| 1 | `gello/data/libero_format.py` | 6개 모듈로 분해 | 1,367줄 → 6개 파일(각 100~350줄) | import가 16개로 많고, 책임 분리가 명확. 순수 함수/클래스 위주라 안전. |
| 2 | `gello/gui/gui_widgets.py` | 위젯/다이얼로그/워커별 분해 | 1,484줄 → 10개 파일 | 이미 독립된 조각들의 모음. 변경 비교적 기계적. |
| 3 | `apps/collect_workspace.py` | domain으로 잔여 책임 이동 | 1,564줄 → ~800줄 | 이미 분해가 진행 중. 남은 책임 중 gallery/layout/process/upload가 분리 가능. |
| 4 | `gello/gui/libero_gui_worker.py` | worker 내부 모듈화 | 1,427줄 → ~400줄 + 4개 모듈 | 하드웨어 의존이 커서 안전망 먼저 필요. |
| 5 | `scripts/convert/convert_libero_to_lerobot.py` | `main` 함수 추출 | 1,131줄 → ~900줄 | 쪼개기보다 정리. 네트워크 의존 테스트가 없어 위험. |
| — | `gello/robots/joint_limit_wall.py` | **그대로 둔다** | — | 한 가지 일을 하는 하드웨어 제어 루프. 쪼개면 오히려 위험. |

## 5. 쪼개지 말자고 결론 낸 파일

**`gello/robots/joint_limit_wall.py`**

- 한 가지 일(리더 하드웨어 제어 루프)을 하는 1,100줄 파일이다.
- 70개 이상의 인스턴스 속성이 `_run`에서 밀접하게 공유되고, `set_current`가 전체 서보 벡터를 동기로 쓴다.
- 쪼개면 상태 전달과 동시성 버그 위험이 커진다.
- 대신 `__init__` tuning parameter 정리와 `_run` 내부 계산 추출 정도만 한다.

## 6. 보고

대상 파일은 **6개**였다. 그중 **쪼개지 말자고 결론 낸 것은 `gello/robots/joint_limit_wall.py`** 하나다. 이 파일은 단일 책임의 하드웨어 제어 루프이며, 분해 오히려 상태 공유와 동시성 버그를 키울 수 있다.

**가장 먼저 손대야 할 파일은 `gello/data/libero_format.py`**다. 이유는 세 가지다. 첫째, action math, schema description, crop utilities, episode buffer, task writer, repack status라는 6개의 명확한 책임이 이미 존재한다. 둘째, import가 16개 파일에 걸쳐 있어, 분해 후 잘못된 import를 발견할 가능성이 높고(=오히려 검증이 쉽다), 반대로 분해하지 않으면 다음 단계의 worker/gui 분해 때 계속 거대한 파일을 import하게 된다. 셋째, 이 파일의 함수들은 순수 계산이 많아 단위 테스트를 추가하기 쉽고, `tests/gui/test_scene_edit.py`와 `test_depth17.py`가 이미 상위 흐름을 가드하고 있다.
