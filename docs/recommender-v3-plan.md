# scene 추천기 v3 — 결정 기록 (2026-09-06)

`recommender-v2-plan.md` 후속. v2 는 조합·문장을 규칙화했고, v3 는 **배치**를
다룬다. 코드를 고치기 전에 내린 결정만 적는다.
(문서 언어는 사용자 요청으로 한국어 — 이슈 #42 영문 규약의 예외.)

## 1. 문제

`generate_candidate` 가 9칸을 균등 셔플해 앞에서부터 꽂고 규칙 통과만 본다.
칸 사용이 균등하니 `position` 축 균등성이 0.992 로 포화되고, 그래서 이 축은
"약한 축"이 될 수 없어 선택 가중치가 바닥값 0.05 에 고정된다 —
**배치는 선택 점수에 사실상 영향을 주지 못한다.**

## 2. 판단 근거 (2026-09-05/06 실측, 수집분 16 scene / 1109 episode)

| 측정 | 값 | 함의 |
| --- | --- | --- |
| 거부 표본추출 수용률 | **82.2%** (후보 2000개 0.07초) | 탐색 난이도가 없다 → CSP 를 *실행 가능성* 엔진으로 쓸 이유 없음 |
| 규칙 통과 물체집합 전수 | **3,122개** / 35,420, **0.3초** | 조합 공간은 전수 열거 가능 |
| 커버리지 균등성 | category 0.677 · color 0.769 · position 0.992 · count 0.874 | category 최약, position 은 포화되어 무력 |
| 최근접 scene 거리 | 최소 0.117 (S010↔S012, 물체 동일·배치만 다름) | "배치가 얼마나 달라야 새 scene 인가" 기준이 없다 |
| `import scene_diversity` | 115ms (`scene_rules` 11ms) | 순수 알고리즘이 `SceneMetadata` 로 HDF5 사슬을 끌고 온다 |
| 짝 category 3종 조합 | **0개** | 생성기 버그 아님. 3종이면 6물체인데 `MAX_OBJECTS`=5 |

## 3. 라이브러리 결정

완제품은 없으므로 in-house 유지, 엔진만 차용한다.

| 라이브러리 | 판정 | 근거 |
| --- | --- | --- |
| **OR-Tools (CP-SAT)** | **도입** — 배치 *최적화기* | 제약 필터로는 불필요(수용률 82%). "하드 규칙 하에서 커버리지 최대화"가 CP-SAT 의 형태이고, 물리 규칙을 절차 대신 선언으로 늘릴 수 있다 |
| pyribs (MAP-Elites) | 기각, 개념만 차용 | fitness·연속 genotype·수천 회 평가가 전제. 우리는 이진 실행가능성·이산 집합·한 번에 3개를 사람이 배치한다. 아카이브는 이미 `axis_coverage()` |
| diversipy / pyDOE2 | 기각 | 연속 박스 도메인용. 쓸 함수 하나는 2026-08-26 에 모서리 쏠림으로 버린 farthest-point |
| scene_synthesizer | 기각 | 연속 3D·물리 안정성용. 우리 격자는 이산이고 배치는 사람이 한다 |

설치(2026-09-06): `ortools 9.15.6755` + `protobuf 6.33.6` · `absl-py 2.5.0` ·
`immutabledict 4.3.1`. protobuf 를 요구하던 기존 패키지 없음, numpy/pandas/torch
무변경, torch·lerobot·PyQt6 import 확인.

**재검토 트리거**: scene 생성이 사람 없는 오프라인 배치 작업이 되면 QD 재검토.
배치 목적함수를 없애면 OR-Tools 도 같이 뺀다.

## 4. 구조 결정

### D1 — 역할 분담과 워크플로 2종

```
조합 : 전수 열거(itertools) → 커버리지 히스토그램으로 선택
배치 : CP-SAT (하드 규칙 = 제약, 커버리지 = 목적함수)
문장 : 변경 없음 (skill_stats.py)
```

배치 추천은 **1급 진입점**이고 두 워크플로가 같은 엔진을 쓴다.

```
① 전체 추천    조합 열거 → 커버리지 선택 → recommend_placement()
② 배치만 추천  사람이 고른 물체 집합 ────────→ recommend_placement()
```

`recommend_placement(objects, existing, props, seed)` 가 공통 바닥이므로 ②로
만든 scene 도 ①의 커버리지 계산에 그대로 반영된다. 조합을 CP-SAT 에 넘기지
않는 이유: 0.3초에 전수 열거되는 공간에서 이점이 없고 히스토그램이 더 설명 가능하다.

### D2 — 다양성은 제약이 아니라 목적함수

CP-SAT 은 기본적으로 사전순 첫 해를 준다(9칸 중 3칸 → 0,1,2번).

```
maximize  Σ (배치특징 bin 부족도 × 사용여부)  +  ε · Σ (seed 난수계수 × x[물체,칸])
```

앞항이 배치가 선택에 영향을 주는 **최초의** 경로(1절의 해결), 뒷항이 사전순
쏠림 제거. 재현성: `random_seed` = 추천 seed, `num_search_workers = 1`.

### D3 — 규칙 정본은 yaml 하나, 소비자가 둘

```
configs/scenes/scene_rules.yaml
   ├─→ check(md, props)       사후 검증 (사람이 만든 배치, check_scene_file) — 기존 유지
   └─→ CP-SAT 규칙 컴파일러    사전 생성 (신규)
```

유일한 진짜 위험은 두 소비자가 어긋나는 것. 6절 인수 조건 1번이 방어선이며 생략 불가.

### D4 — ortools 는 한 모듈에만

`gello/scene/placement_solver.py` 만 import 하고, 지연 import(0.33초)라 GUI
워커 스레드만 비용을 낸다. 나머지 `gello/scene/` 는 표준 라이브러리 전용 유지 —
로봇 없이 오프스크린 검증이 되는 근거가 이 성질이다.

### D5 — 모듈 분할 + 축 레지스트리 통합

```
scene_signature.py   Signature · 거리 · 축별 분해
scene_axes.py        축 레지스트리: 이름 → (support, extract, distance)
scene_sampler.py     조합 전수 열거 + placement_solver 호출
scene_select.py      거리 버킷 쿼터 + 커버리지 선택 정책
scene_diversity.py   기존 공개 API 재수출 (GUI·CLI·감사도구·테스트 무수정)
```

레지스트리가 이중 어휘(`AXES` vs `COVERAGE_AXES`)를 합친다. 이후 배치 특징
추가는 항목 하나 + 추출 함수 하나이고, GUI·CLI·감사도구가 자동 반영한다.

## 5. 물리 규칙 결정

### D6 — 격자 기하 선언 (2026-09-06 사용자 확정)

```
row 0 = 로봇 쪽 (FR3 베이스)     row 2 = 카메라 쪽 (agentview)     칸 = 19~22cm
```

`exclusive_column` 은 방향이 반대인 두 물리 사실을 한 규칙으로 근사한 것이라
yaml 주석이 근거를 "가림 위험"으로만 적고 있었다. 둘로 나눈다.

| 규칙 | 막는 칸 (키 큰 소품이 `(r,c)`) | 근거 |
| --- | --- | --- |
| `occludes_behind` | 같은 열의 `r' < r` (카메라에서 더 먼 칸) | 서랍 뒤에 숨어 agentview 에 안 잡힌다 |
| `robot_clearance` | 같은 열의 `r' > r` (로봇에서 더 먼 칸) | 팔이 서랍을 넘어가야 해 접근 경로가 막힌다 |

합집합 = 서랍 칸을 뺀 **열 전체**로, 막는 칸은 현행과 동일하다. 달라지는 것은
① 위반 메시지가 가림인지 팔 경로인지 정확히 말한다 ② 키만 크고 팔은 안 막는
소품이 생기면 차폐만 걸린다. 두 규칙 모두 `props.yaml` 의 `tall` 에서 파생해
`category: drawer` 하드코딩을 없앤다. 현재 tall 은 서랍뿐이다.

### D7 — 보류·기각

- `no_adjacent_large`(칸 19~22cm 에 20.5cm 볼): 컴파일러 어휘만 만들고 yaml 에선
  **끈다**. 켜는 것은 yaml 3줄.
- 서랍 여닫이 공간(앞 1칸 비우기): **기각**. "서랍 앞 물건 치우기"가 잠재 과제라
  그 과제에 필요한 배치를 규칙이 금지하게 된다.
- 도달 한계·집기↔목표 거리 상한: 관측된 실패 없어 미채택.

### D8 — 물체 개수 범위를 설정으로

`MIN_OBJECTS`/`MAX_OBJECTS`(2/5)를 `scene_rules.yaml` 로 옮긴다. 최약 축인
category 는 알고리즘으로 못 연다 — 3종이면 6물체인데 상한이 5다. 6으로 올릴지는
현장에서 놓아 보고 정하며, 설정에 있으면 코드 수정 없이 바꾼다. 단 6물체
scene 에는 서랍이 못 들어간다(열 하나가 통째로 빈다).

## 6. 인수 조건 (로봇 불필요·오프스크린·결정적)

1. **규칙 엔진 동등성**: 표본 1000개 이상에서 `check(md) == []` ⟺ CP-SAT
   feasible, 양방향. D3 의 방어선이며 생략 불가.
2. 알 수 없는 rule 이름은 CP-SAT 컴파일러도 `load_rules()` 와 똑같이 던진다.
3. **결정성**: 같은 seed → 같은 추천(CP-SAT 배치 포함).
4. **배치가 무력하지 않다**: 배치 특징이 우연이 아니라 설계로 변하고,
   `audit_scene_diversity.py` 가 새 축을 보고한다.
5. **무회귀**: `recommend_scene.py --selftest`, `python -m gello.scene.scene_rules`,
   `python -m gello.scene.instruction_grammar`, `bash tests/gui/run_all.sh` 통과.
6. 배치를 풀지 않는 경로는 `ortools` 를 import 하지 않는다.

## 7. 작업 순서 (커밋 단위)

1. 리팩터만 — 모듈 분할 + 축 레지스트리, 공개 API 무변경. `recommend_placement()`
   진입점을 이 단계에서 노출한다(워크플로 ②의 토대).
2. `props.yaml` 의 `tall`; `occludes_behind` + `robot_clearance` 가
   `exclusive_column` 대체; 개수 범위 yaml 이관. `check()` 쪽만.
3. `placement_solver.py` — 규칙 컴파일러 + CP-SAT 모델 + 동등성 테스트(인수 1~3).
4. 배치 특징 축 + 커버리지 목적함수; 조합 전수 열거가 무작위 표본 대체(인수 4).
5. 감사도구·GUI/CLI 연동, 워크플로 ② UI, 문서.
