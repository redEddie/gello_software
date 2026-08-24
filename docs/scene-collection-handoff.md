# Scene 기반 수집 전환 — 인수인계 문서

브랜치 `scene-based-collection`. 저장 계층(포맷·검증)은 완료됐고, GUI 통합부터가
남은 작업이다. 이 문서 하나로 맥락 파악 → 작업 착수가 되도록 쓴다.

라인 번호는 커밋 `2530754`(이 브랜치) / `c1a91eb`(main) 기준이다.

## 0. 5분 온보딩

```bash
git checkout scene-based-collection
python scripts/check_scene_file.py --selftest          # 로봇·카메라·데이터 불필요
python scripts/check_scene_file.py --selftest --keep /tmp/scene_demo   # 결과 파일 구경
```

selftest가 만드는 `scene_000.hdf5`가 곧 목표 포맷의 실물이다. 출력의 ASCII
격자 지도(`describe_scene`)가 scene 하나를 요약하는 표준 뷰다.

읽을 것 (순서대로):
1. 이 문서
2. Notion 프로토콜 §2(데이터 모델·ID), §4(scene 설계·**통제 변수 레지스트리**),
   §7(HDF5 규격) — [DB 링크](https://app.notion.com/p/jeonchanwook/60b41bd2091f4e55aa383492f41e5875)
3. `gello/scene_format.py` 모듈 docstring (포맷 정의와 legacy 와의 차이가 전부 있다)
4. 이 브랜치의 커밋 메시지 2개 (`git log main..HEAD`)

## 1. 왜 바꾸나 (한 문단)

legacy 는 파일 하나 = task 하나였고 **파일명이 사실상 source of truth** 였다.
문장을 고치면 파일이 갈라지고, 장면 배치는 기록 없이 녹아 있었다. 새 체계는
파일 하나 = scene(책상 배치) 하나, instruction 은 **episode attrs 안에만**,
배치는 3×3 격자 존으로 구조화해 기록한다. legacy 621 에피소드는 보존만 하고
**마이그레이션하지 않는다.**

## 2. 확정 결정 (2026-08-13)

| # | 결정 | 근거 |
|---|---|---|
| 1 | `problem_info`/`env_args` 스텁 **완전 제거** | 저장소 내 `env_args` 소비자 0곳, 외부 LIBERO 리더 호환 포기 |
| 2 | `layout` 은 **3×3 격자 존 JSON 고정** | "같은 존 안 이동 = 같은 scene, 경계 넘으면 새 scene ID". 코드가 다른 격자를 거부 |
| 3 | slot 계획 파일은 **저장소 내 JSON** (`configs/collection_plans/`) | git 이력 = 계획 변경 기록 |
| 4 | `distractors` **필드 없음** — 개념은 운영 관례로만 | objects 에 넣고 description 에 사람 말로. 필요 시 scene-v2 에서 부활 |
| 5 | 통제 변수는 **metadata 가 아니라 운영 규칙** | Notion §4 레지스트리 표가 정본 (조명·배경·동사 집합·30초 상한 등) |

대원칙: **VLA 를 아직 자유자재로 다루지 못하므로, 포맷은 확장 가능하게 두되
운영은 작게 통제한다.** 그리고 **같은 사실은 한 곳에만 적는다** — 빈 존·소품
종류는 저장하지 않고 파생한다(`empty_zones`/`describe_scene`).

## 3. 구현된 것 (파일 맵)

| 파일 | 내용 |
|---|---|
| `gello/scene_format.py` | `SceneWriter`(생성/resume/append/QA), `SceneMetadata`+validate, `describe_scene`, `count_by_slot`, `iter_scene_files`, `next_scene_id`, `episode_uid` |
| `gello/props.py` + `configs/props.yaml` | 소품 instance ID 인벤토리 15개 정본. 미등록 ID 는 scene 생성 거부 |
| `gello/libero_format.py` | `write_episode_payload()` 추출 — legacy `demo_N` 과 scene `episode_NNN` 이 **에피소드 안쪽 페이로드를 공유** (변환기가 같은 코드로 읽게) |
| `scripts/check_scene_file.py` | 실파일 QA 검사기 + `--selftest`. **모든 작업의 인수 기준이 이 검사기 통과다** |
| `configs/collection_plans/` | slot 계획 JSON 스키마 + Notion §6 matrix 예시 |

핵심 설계 요점 (코드 읽기 전에 알아둘 것):

- **instruction 은 save 시점에 명시 인자다.** writer 상태로 들고 있지 않는다 —
  저장이 백그라운드 스레드(EpisodeSaver)라, 조작자가 다음 slot 으로 넘어간 뒤
  직전 에피소드가 저장되는 경합에서 "실제 수행한 문장"이 찍혀야 한다.
  crop_params 를 buffer 에 싣는 기존 설계와 같은 이유.
- **에피소드 편집 정책 (2026-08-14 개정).** 1차 큐레이션은 `set_quality_status`
  재판정, 불량 궤적은 삭제 후 renumber(slot E번호·uid 재배정), 끝만 자르는
  트림 허용. 삭제·트림은 `metadata.attrs["edit_count"]` 를 올리고, 변환기
  resume 이 이 값으로 이어붙이기를 거부한다(전체 재빌드만 허용) -- uid 가
  재배정되므로 "이미 올린 uid 스킵" 대조를 믿을 수 없기 때문. 라벨 없는
  에피소드는 저장 자체가 거부된다.
- **글롭 불교차.** scene 파일은 `scene_*.hdf5`, legacy 는 `*_demo.hdf5` —
  같은 디렉터리에 있어도 서로 안 보인다. legacy 코드는 수정 없이 그대로 돈다.
- instruction 은 따옴표 없는 순수 문자열 (legacy 는 `f'"{...}"'` 래핑이었고
  변환기가 벗겼다 — 새 포맷은 감싸진 입력을 거부한다).

## 4. 남은 작업 (권장 순서)

각 작업의 공통 인수 기준: `python scripts/check_scene_file.py <파일>` 위반 0,
legacy 경로 무변경(기존 `*_demo.hdf5` 수집·변환이 그대로 동작).

### ⑤ GUI: New/Existing Scene 수집 통합 ← **여기부터. 로봇 필요** ([#30](https://github.com/redEddie/gello_software/issues/30))

목표: GUI 가 SceneWriter 로 기록할 수 있게 한다. 이게 되면 pilot 수집 가능.

1. **Writer 연결 (GUI 없이 검증 가능한 부분부터)**
   - `gello/libero_gui_worker.py:881-888` 이 `LiberoTaskWriter` 를 하드코딩 —
     `WorkerConfig`(`:146` 근처)에 scene 모드(scene_id / metadata / resume)를
     추가하고 SceneWriter 분기.
   - `EpisodeSaver`(`:67-137`) 큐가 `(buf, success)` — `(buf, success,
     instruction, instruction_id)` 로 확장. 워커가 에피소드 종료 시점의
     현재 slot 을 캡처해서 큐에 싣는다.
2. **Configure 페이지 scene 모드** (`experiments/collect_workspace.py`
   `_page_configure:708`)
   - Existing Scene: `iter_scene_files()` 드롭다운 + 선택 시 `describe_scene()`
     표시. 기존 resume 콤보(`_refresh_resume_combo:2509`, `_on_resume_selected:
     2536-2581`)와 같은 패턴. **파일명에서 아무것도 역산하지 않는다** —
     metadata 만 읽는다.
   - New Scene: `next_scene_id()` 자동, objects 는 props.yaml 목록에서 선택,
     description 입력. 입력 검증은 `SceneMetadata.validate()` 가 다 하므로
     UI 는 얇게. legacy 의 파일명 중복 검사(`:2885-2900`)는 scene 모드에서
     scene_id 기반으로 대체.
3. **카메라 확인 + 기준 사진**: 기존 `CameraPreviewWorker`(`gello/gui_widgets
   .py:501-557`)를 New Scene 흐름에 붙이고, 캡처 → `set_reference_image()`.
   3×3 존 입력도 이 프리뷰 위 격자 오버레이 클릭으로 받으면 §6 "사진 필수"까지
   한 번에 해결. 오버레이는 `VideoView._decorate`(`:386-407`) 참고.
4. **수집 중 slot 전환**: 에피소드 사이에 다음 instruction 을 고르는 UI
   (Collect 페이지 `_page_collect:840`). ⑦의 기초.
5. **collector 기록**: 수집자 식별자를 GUI 입력(또는 환경변수)으로 받아
   writer 에 전달 — 에피소드 필수 attr 이다.

인수 기준: GUI 로 만든 scene 파일이 검사기 통과, 같은 scene 에 서로 다른
instruction 에피소드 공존, 이어찍기에서 새 파일이 생기지 않음, `no_dataset`
연습 모드(NullTaskWriter)와 legacy task 모드 회귀 없음.

### ⑥ 에피소드 갤러리 그리드 + 재생 ([#31](https://github.com/redEddie/gello_software/issues/31))

- `list_scene_episodes()` 로 에피소드 요약(UID·instruction·quality) 목록,
  각 에피소드 첫 agentview 프레임을 썸네일 그리드로. instruction 필터.
- 썸네일 캐시: `~/libero_gui_logs/thumbs/<episode_uid>.jpg` (매번 HDF5 열지
  않도록). 재생은 기존 `EpisodeLoadWorker`(`gui_widgets.py:466`)+Playback 탭 재활용.
- scene 갤러리(파일 단위)는 `metadata/reference_image` 를 대표 이미지로.

### ⑦ slot 계획 표시와 카운트 ([#32](https://github.com/redEddie/gello_software/issues/32))

- `configs/collection_plans/*.json` 로드 (스키마는 그 디렉터리 README).
- `count_by_slot()` 과 대조해 `S000 / I003 / open the top drawer / 4 of 10
  collected` 표시, 다음 미수집 slot 자동 제시. **collected 는 계획 파일에
  넣지 않는다** — 항상 파일에서 계산.
- freeze 검증 보너스: 계획의 instruction 문장이 §5 동사 집합(pick-place /
  open / close)을 벗어나면 로드 시 경고.

### ⑧ scene 다양성 추천 (로봇 불필요 — 병행 가능) ([#33](https://github.com/redEddie/gello_software/issues/33))

`gello/scene_diversity.py` + `scripts/recommend_scene.py` CLI 로 먼저 만들고
GUI 통합(New Scene "추천 받기")은 후속.

- **벡터화**: metadata 에서 (category, color, material) multiset + 존 배치 +
  relations + 개수. 종류·색은 props.yaml 조회로 파생.
- **거리** = 가중 합: 객체 조합(multiset Jaccard) 0.5 + 배치(같은 종류끼리
  매칭 후 존 맨해튼 거리, 3×3 최대 4 로 정규화) 0.35 + 관계 차이 0.15.
  공통 객체가 없으면 배치 성분 가중치를 재분배.
- **추천** = max-min(farthest-point): 인벤토리 제약 안에서 후보 생성(컵 ≥1 +
  그릇/서랍 ≥1, 물체 2~5개, 존 무작위 비충돌) → 기존 모든 scene 과의 최소
  거리가 최대인 상위 3안. §4 family A~G 중 미커버 family 보너스 가산.
  제안 3안끼리도 greedy farthest-point 로 서로 다르게.
- 결정성: seed 인자. 출력: `describe_scene` 과 같은 격자 지도 + 복사 가능한
  layout JSON.

### ⑨ 변환기 양포맷 지원 (로봇 불필요 — 병행 가능) ([#34](https://github.com/redEddie/gello_software/issues/34))

`scripts/convert_libero_to_lerobot.py`:

- `_language_instruction`(`:317-322`)이 "파일 = instruction 하나"를 전제(파일
  레벨 `problem_info` 1회 읽기, 호출 `:717`) — scene 파일이면 **에피소드
  attrs 의 `instruction`** 을 읽도록 분기. legacy 쪽 따옴표 벗기기는 유지.
- 파일 순회: legacy 는 `data/demo_N`, scene 은 루트 `episode_NNN`
  (`scene_format.EPISODE_GROUP_RE`). 에피소드 안쪽(obs/actions/crop_params)은
  **두 포맷이 동일**하므로 그 아래 코드는 그대로.
- task 별 스킵/카운트(`:726-739`)를 에피소드 단위로 재정의.
- `quality_status` 필터: 기본 `success` 만 변환, `--include-failed` 옵션.
- 테스트: `check_scene_file.py --selftest --keep DIR` 의 더미 scene 파일 +
  legacy 파일 회귀(변환 결과 불변).
- 범위 외로 명시: `gello/dataset_sync.py` 의 Hub 대조는 task 단위 전제라
  scene 포맷에서 (scene, instruction) 슬롯 단위 재설계가 필요 — 별도 작업.
  길이 지문(prefix) 검증 로직 자체는 이식 가능.

## 5. 가드레일 — 절대 하지 말 것

- **파일명으로 instruction/task 를 판별하지 않는다.** 파일 내부 metadata 가
  유일한 정본. legacy 사고의 원인이었다.
- **기존 GUI 를 재작성하지 않는다.** `collect_workspace.py` 에 최소 기능만
  추가 (Notion §11). 중앙 카메라 뷰는 절대 건드리지 않는 게 기존 설계 불변식.
- **legacy 621 에피소드와 legacy 코드 경로를 건드리지 않는다.** 마이그레이션
  시도 금지.
- ~~에피소드를 지우거나 덮어쓰는 UI 를 만들지 않는다~~ → 2026-08-14 완화: 재판정이 1차 수단이되, 실패·튀는 궤적은 푸시 전 **삭제(legacy 와 같이 삭제 후 번호·slot E·uid 재매김)** 와 **끝 다듬기(트림)** 를 허용한다. 프레임 덮어쓰기는 여전히 금지. 삭제 확인창이 성공분 포함·Hub 영향을 경고하고, 전체 처리의 삭제 보호 게이트가 리모트 반영 전 마지막 확인이다.
- **파생 가능한 것을 저장하지 않는다.** 빈 존, 소품 종류, collected 카운트.
- h5py 는 비스레드안전 — 파일 접근은 EpisodeSaver 스레드로만 (기존 규칙).

## 5b. Hub 저장소 (2026-08-18 확정)

| 용도 | repo | 비고 |
|---|---|---|
| 학습·공유용 LeRobot 변환본 | `knu-physical-ai/fr3-tabletop-lerobot` | Upload 페이지 "LeRobot repo" 기본값 |
| 원본 HDF5 백업 | `knu-physical-ai/fr3-tabletop` | Upload 페이지 "HDF5 repo" 기본값 |
| legacy (재사용 금지) | `knu-physical-ai/fr3-pick-place-lerobot` / `fr3-pick-place` | 728개. 전체 처리 대상 아님 — 삭제 게이트가 막는다 |
| pilot 시험본 | `knu-physical-ai/fr3-scene-pilot-lerobot` / `fr3-scene-pilot` | S000 4개짜리. 필요 시 삭제 |

기본값은 `experiments/collect_workspace.py` 의 `DEFAULT_REPOS` 에 있고, recents 에
legacy repo 만 남아 있어도 기본값으로 되살리지 않는다(`LEGACY_REPOS`).

## 6. 참고

- Notion: [프로토콜 DB](https://app.notion.com/p/jeonchanwook/60b41bd2091f4e55aa383492f41e5875)
  — §4 에 **통제 변수 레지스트리**(조명·배경·동사·30초 상한·pilot 범위),
  §7 에 구현 현황, §8 에 세션 시작 체크가 2026-08-13 자로 갱신돼 있다.
- pilot 목표: scene 2개 × instruction 4~6개 × slot 당 10 에피소드 → 수집→변환
  →학습→평가 한 바퀴 후 확장.
- 아키텍처 전반: `docs/architecture.md` (GUI/워커/노드 구조와 타이밍).

## 7. 2026-08-14 추가분 (당일 scene-v2 브랜치에서 작업 후 scene-v1 으로 병합)

GUI/도구가 scene-v1 대비 다음이 늘었다. 상세 사용법은 각 커밋 메시지와
`tests/gui/README.md` 가 정본.

- **게이트 안전화**: 자동 정렬이 켜져 있어도 리더가 GATE_RAD 밖이면 당기지
  않고 기다린다. Start 버튼·Space 는 자세가 맞아야 열린다.
- **리셋 대기 중 라이브 뷰 유지** (`_reset_wait` 프레임 방출).
- **실로봇 재생**: Dataset/Gallery 버튼 + 중단 버튼. `experiments/replay_episode.py`
  (양포맷, 램프+클램프+감속, 노드 미기동/잠금/없는 에피소드는 친절 오류).
- **3×3 워크스페이스 격자**: `gello/grid_overlay.py` + 편집 다이얼로그(꼭짓점
  드래그, 상하좌우 정렬, 변환, 실행취소, 이름 저장/불러오기), Live 오버레이
  (체크박스+투명도, agent 전용, 저장 데이터에는 안 들어감).
- **수집 계획**: Configure '계획 문장' 드롭다운(카운트 표시, ID 자동 매칭,
  세션 중 캐시 카운트), 계획 편집 폼(문장·목표만 편집, ID 자동·번호 재사용
  금지, 행 이동/삭제, scene 추가/삭제, 중복 즉시 표시; 원문은 'JSON 직접
  편집'), Statistics '수집 계획 진행률' 표.
- **전체 처리 삭제 보호**: Hub 에서 에피소드가 사라지는 계획이면 빨간 확인
  체크 없이 시작 버튼이 열리지 않는다 (legacy repo 실수 방지의 마지막 잠금).
- **오른쪽 패널 배치도**: 수집 중 scene 의 3×3 배치를 세션 내내 표시.
- **테스트**: `tests/gui/` (offscreen, 수집 중에도 실행 가능).
