# scene 추천기 v2 구현 결과

브랜치: `feat/recommender-v2-impl`
스펙: `docs/recommender-v2-plan.md` (2026-08-21 확정)

## 커밋 단위

1. `63ef69f` — `configs/scene_rules.yaml` + `gello/scene_rules.py` + selftest
2. `547abd3` — `generate_candidate` 규칙 필터 + 추천 스코어에 낮은 다양성 본너스
3. `cecb2f9` — `gello/instruction_grammar.py` + CLI 추천 문장 출력
4. `2a5ac7e` — `RecommendDialog` 문장 체크리스트 + 계획 등록, `NewSceneDialog` 규칙 lint
5. `413ce14` — `tests/gui/test_recommend_register.py` 추가 및 `tests/gui/run_all.sh` 등록

## 검증 결과

| 항목 | 결과 | 비고 |
|------|------|------|
| `python scripts/recommend_scene.py --selftest` | 통과 | 후보 1000개 규칙 위반 0, 문법 결정성 + 모호 지칭 제외 |
| `python scripts/check_scene_file.py --selftest` | 통과 | 기존 scene 포맷 검증 그대로 통과 |
| `bash tests/gui/run_all.sh python` | test_depth17 제외 전부 OK | test_depth17 은 `lerobot.datasets.utils.DatasetInfo` 환경 의존으로 실패(스펙 예상) |
| 기존 계획 문법 lint | 통과 | `configs/collection_plans/pilot.json` 16개 문장 전부 템플릿 통과 |

## 구현 범위

- **규칙**: `no_lookalike_pair`, `color_diverse`, `ban_zones` 3가지를 yaml 정본 + 로더로 구현. 알 수 없는 rule 이름은 로드 시 `ValueError`.
- **후보 생성**: `generate_candidate` 가 규칙을 만족할 때까지 재시도(최대 200회), 실패 시 예외.
- **추천 스코어**: `min-dist + 0.05 * distinct(category,color) / n` 로 낮은 다양성 본너스 추가. 기존 결정성(seed) 유지.
- **문법**: `cup→"cup"`, `small_bowl→"small bowl"`, `large_bowl→"large bowl"`, `drawer→"drawer"` 매핑. scene 내 `(color, category)` 유일할 때만 지칭 생성. `drawer` 는 색 생략. lint 는 기존 계획의 `"bowl"` 약칭도 하위호환적으로 수용.
- **CLI**: `scripts/recommend_scene.py` 가 각 추천안 아래 추천 문장 목록을 출력.
- **GUI**:
  - `RecommendWorker(QThread)` 로 후보 계산을 GUI 스레드 밖으로 이동.
  - `RecommendDialog` 에 문장 체크리스트 추가. plan_path 를 받으면 "선택한 문장을 계획에 등록" 기능 활성화.
  - 계획 등록 시 `load_plan` 검증 게이트를 통과해야 실제 파일에 기록.
  - `NewSceneDialog` 에 `lint_label` 을 추가해 규칙 위반을 경고로 표시.

## 스펙에서 벗어난 판단

- **legacy `"bowl"` 약칭**: 스펙의 명사 매핑은 `large_bowl→"large bowl"` 이지만, 기존 `pilot.json` 의 `"pick up ... and place it on the blue bowl"` 등이 lint 를 통과하도록 `"bowl"` 을 `large_bowl` 로 간주하는 파싱을 추가. 생성은 여전히 `"large bowl"` 을 사용.
- **drawer 지칭**: 스펙은 `"the drawer" / "the top drawer"` 모두 허용. 생성은 `"the drawer"`(place-on-top)와 `"the top drawer"`(open/close)를 상황에 맞게 사용.
- **계획 등록 시 기존 scene 병합**: 스펙은 "scene+slots로 등록"만 명시. 구현에서는 동일 scene_id 가 이미 계획에 있으면 슬롯을 추가하고, 없으면 새 scene 을 생성.

## 남은 한계

- `test_depth17` 은 현재 `lerobot` 버전(0.5.0 환경에서 `DatasetInfo` import 오류)으로 인해 실패. 본 작업 범위가 아님.

## 리뷰 반영 (2026-08-24)

아키텍트 결정 2건:
- **다양성 보너스 항 제거** — no_lookalike_pair 가 전 소품에 걸려 통과 후보는
  항상 distinct == n, 보너스가 상수(0.05)라 순위 효과 0 (스펙 수식의 모순).
  스코어는 순수 min-dist 로, "기존과의 최소 거리" 라벨이 다시 정확해짐.
- **lint 대상은 실사용 계획(pilot*.json)으로 한정** — example.json 의 2문장
  (위치 지칭 I004, put I005)은 §4 동사 집합 밖의 향후 확장 예시. README 명시.

수정:
- 스펙 §1 사용처 ③ 연결: check_scene_file 파일 검사에 scene_rules 경고 출력
  (합불 아님 — 규칙 도입 전 scene 이 위반일 수 있다).
- 스펙 §3 lint 연결 + 이중 진실 해소: collection_plan 의 _ALLOWED_PATTERNS
  제거, load_plan 경고가 instruction_grammar.lint 를 정본으로 호출.
  collect_workspace 의 죽은 lint import 제거.
- RecommendWorker 수명: 실행 중 워커 참조 보관(_stale_workers) + 정체성 비교로
  낡은 결과 무시 + done() 에서 wait — "Destroyed while thread is still
  running" abort 경로 제거. finished 시그널을 recs_ready 로 개명(QThread 기본
  시그널 가림 해소).
- _parse_object_phrase 색 토큰을 인벤토리 색 집합으로 제한 ("the zzz qqq cup"
  통과, "the small bowl" 오분류 해소). lint 의 on-top-of-drawer 분기에 drawer
  존재 검사 추가.
- _register_plan 문장 기준 dedupe + load_plan 경고 표시 + temp 파일 finally
  정리. _accept 에 "N문장 × target 10 = 총 M 에피소드" 총량 확인창.
- scene_rules 기본 규칙 lru_cache (후보당 yaml 재파싱 200배 제거,
  NewSceneDialog 격자 클릭 lint 도 동일 수혜).
- 오타("낸부") 및 미사용 루프 변수 정리.
