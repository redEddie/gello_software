# scene 추천기 v2 — 규칙·문법 스펙 (2026-08-21)

배경: v1 추천기(#33, `gello/scene_diversity.py`)는 farthest-point 거리로 조합을
고르지만 후보 생성이 순수 무작위라 (a) 동일 외형 페어(흰 컵 01+02)가 나오고
(b) 서랍이 중앙 존에 앉으며 (c) 문장은 만들지 않는다. 사용자 결정(8/21):
룰베이스 억제 + 통일 문법 문장 추천.

## 1. configs/scene_rules.yaml (신규 — 규칙의 정본)
```yaml
version: 1
compose:
  - rule: no_lookalike_pair            # 같은 (category, color) 2개 금지 (전 소품)
  - rule: color_diverse                # 같은 category 2개 이상이면 색은 전부 달라야
    category: cup
placement:
  - rule: ban_zones                    # 해당 category 를 이 존에 두지 않는다
    category: drawer
    zones: [[1, 1]]
```
- 로더/검사기: `gello/scene_rules.py` — `load_rules()`, `check(md, props) -> [위반 문자열]`
- 사용처: ① 추천 후보 필터(rejection) ② NewSceneDialog 검증(사람이 만든 배치도
  같은 규칙으로 lint, 경고만) ③ check_scene_file 선택 검사
- 알 수 없는 rule 이름은 로드 시 오류 (조용한 무시 금지)

## 2. 후보 생성 개선 (`scene_diversity.py`)
- generate_candidate: 규칙 위반 후보는 버리고 재시도 (시도 상한 후 예외)
- 선택 스코어: min-dist + 0.05 * (scene 내부 distinct (category,color) 수 / n)
  — 규칙은 강제, 내부 다양성은 타이브레이커
- 기존 결정성(seed) 유지, 인수 기준에 "동일 외형 페어 0 / ban_zones 위반 0" 추가

## 3. 통일 문법 (`gello/instruction_grammar.py` 신규)
- 명사 매핑: cup→"cup", small_bowl→"small bowl", large_bowl→"large bowl",
  drawer→"drawer" (인벤토리 category 가 정본; 새 category 는 매핑 추가 필수)
- 지칭: "the {color} {명사}". scene 안에서 (color, category) 유일할 때만 지칭
  가능 — 유일하지 않으면 그 물체가 들어가는 문장은 생성하지 않는다.
  단일 개체 category(drawer)는 색 생략: "the drawer" / "the top drawer"
- 템플릿 (기존 데이터 문법 고정, §4 동사 집합):
  - `pick up the {obj} and place it on the {bowl}`       (obj: cup|small bowl, bowl: *_bowl)
  - `pick up the {obj} and place it on top of the drawer`
  - `pick up the {obj} and place it next to the {obj2}`  (obj2 != obj)
  - `open the top drawer` / `close the top drawer`       (drawer 가 scene 에 있을 때)
- API: `enumerate_instructions(md, props) -> list[str]` (결정적 정렬),
  `lint(sentence, md, props) -> None|경고` (계획 편집 폼·load_plan 경고에 연결)

## 4. 출력 연결
- CLI(`scripts/recommend_scene.py`): 각 추천안 아래 추천 문장 목록 출력
- GUI RecommendDialog: 문장 체크리스트 표시, 채택 시 선택 문장을 계획 파일에
  scene+slots(target 10, ID 자동)로 등록하는 옵션 (기존 PlanEditDialog 저장
  경로 재사용 — load_plan 검증 게이트 통과 필수)

## 5. 검증 (로봇 불필요 — 리모트 작업 가능)
- selftest: 후보 1000개에서 no_lookalike_pair 위반 0, drawer 중앙 0
- 문장: 같은 scene → 같은 문장 목록(결정성), 지칭 모호 케이스 생성 제외 확인
- lint: 기존 pilot.json 전 문장이 문법 통과 (통과 못 하면 매핑/템플릿 누락)
- tests/gui: RecommendDialog 문장 표시 + 계획 등록 offscreen

## 작업 순서 (각각 커밋)
1. scene_rules.yaml + scene_rules.py + selftest
2. generate_candidate 규칙 필터 + 내부 다양성 보너스
3. instruction_grammar.py + CLI 출력
4. GUI(RecommendDialog 문장 + 계획 등록) + NewSceneDialog lint
5. 문서/테스트 편입
