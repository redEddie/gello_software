"""scene 후보 생성 -- 인벤토리 제약 안의 무작위 조합과 배치.

두 진입점이 있고, 워크플로 두 가지에 각각 대응한다:

- :func:`generate_candidate`  물체 조합까지 새로 뽑는다 (전체 추천).
- :func:`place_objects`       물체 집합은 주어지고 배치만 뽑는다
  (사람이 소품을 고른 뒤 배치만 추천받는 워크플로).

배치는 지금 균등 난수다. recommender-v3-plan.md D2 에 따라 다음 단계에서
:mod:`gello.scene.placement_solver` (CP-SAT) 가 이 자리를 대신한다 --
그때도 이 두 함수의 시그니처는 그대로다.
"""

from __future__ import annotations

import random

from gello.scene.scene_format import SceneMetadata
from gello.scene.scene_rules import (
    check,
    object_count_range,
    violations_by_section,
)
from gello.scene.signature import GRID

#: 씬의 물체 개수 범위. 정본은 scene_rules.yaml 의 object_count 규칙이고,
#: count 커버리지 축의 서포트와 같은 값을 본다. 규칙 yaml 을 고치면
#: 프로세스 재시작이 필요하다 (_default_rules 캐시와 같은 정책).
MIN_OBJECTS, MAX_OBJECTS = object_count_range()

__all__ = ["MIN_OBJECTS", "MAX_OBJECTS", "generate_candidate", "place_objects"]


def _shuffled_cells(rng: random.Random) -> list:
    cells = [(r, c) for r in range(GRID[0]) for c in range(GRID[1])]
    rng.shuffle(cells)
    return cells


def _md(objects: list, cells: list, scene_id: str,
        description: str = "(추천안 -- 채택 시 배치 의도를 적어주세요)") -> SceneMetadata:
    return SceneMetadata(
        scene_id=scene_id,
        objects=list(objects),
        layout={"grid": list(GRID),
                "placements": {o: {"zone": list(cells[i])}
                               for i, o in enumerate(objects)}},
        description=description,
    )


def generate_candidate(props: dict, rng: random.Random,
                       scene_id: str = "S999",
                       max_attempts: int = 200) -> SceneMetadata:
    """인벤토리 제약 안의 무작위 scene: 등장 category 는 색 다른 2개 이상
    (pair_if_present), pickable 최소 한 종류, 물체 2~5개, 존 비충돌.
    configs/scenes/scene_rules.yaml 규칙을 만족하지 않으면 재시도한다.

    2026-08-27 일반화: category 목록을 하드코딩(cup/small_bowl/large_bowl/
    drawer)하지 않고 인벤토리에서 파생한다 -- 활성 색이 2개 이상인
    category 는 "짝" 후보(등장 시 2~3색), 색이 하나뿐인 category(drawer/
    tray)는 확률적 단일 추가. pickable 판정은 문법(PICKABLE_CATS)이 정본.
    새 소품은 props.yaml + NOUN_MAP 등록만으로 추천 대상이 된다."""
    from gello.scene.instruction_grammar import PICKABLE_CATS
    from gello.scene.scene_rules import stack_pair_categories

    stack_cats = stack_pair_categories()
    active = [p for p in props.values() if not p.retired]
    # pair_if_present 규칙(2026-08-24) 아래에서는 물체 단위 무작위 뽑기가
    # 거의 다 기각된다 -- category 단위로 "짝"을 뽑는다.
    by_cat: dict = {}
    for p_ in active:
        by_cat.setdefault(p_.category, {}).setdefault(p_.color, []).append(p_)
    paired_cats = sorted(c for c in by_cat if len(by_cat[c]) >= 2)
    single_cats = sorted(c for c in by_cat if len(by_cat[c]) == 1)
    if not any(c in PICKABLE_CATS for c in paired_cats):
        raise ValueError("인벤토리에 2색 이상인 pickable category 가 없다")
    for _ in range(max_attempts):
        n_pair = rng.randint(1, min(2, len(paired_cats)))
        cats = rng.sample(paired_cats, n_pair)
        if not any(c in PICKABLE_CATS for c in cats):
            continue
        picked = []
        for c in cats:
            colors = list(by_cat[c])
            rng.shuffle(colors)
            take = rng.randint(2, min(3, len(colors)))
            picked += [rng.choice(by_cat[c][col]) for col in colors[:take]]
        # 동일 외형 쌍 (2026-08-31): 포갤 수 있는 category 는 같은 색 2개를
        # 넣어 "stack all the {색} {복수}" 과제를 만든다 -- 규칙의 stack
        # 예외가 허용하는 범위(씬당 1쌍)와 같은 목록을 쓴다.
        if len(picked) <= 4 and rng.random() < 0.25:
            twins = [
                (p, q) for p in picked if p.category in stack_cats
                for q in by_cat[p.category][p.color] if q.id != p.id
            ]
            if twins:
                picked.append(rng.choice(twins)[1])
        for c in single_cats:
            if len(picked) <= 4 and rng.random() < 0.4:
                picked.append(rng.choice(by_cat[c][next(iter(by_cat[c]))]))
        if not MIN_OBJECTS <= len(picked) <= MAX_OBJECTS:
            continue
        md = _md([p.id for p in picked], _shuffled_cells(rng), scene_id)
        if not check(md, props):
            return md
    raise ValueError(
        f"규칙을 만족하는 후보를 {max_attempts}회 시도 중 생성하지 못함"
    )


def place_objects(objects: list, props: dict, rng: random.Random,
                  scene_id: str = "S999",
                  max_attempts: int = 200) -> SceneMetadata:
    """물체 집합이 정해졌을 때 규칙을 만족하는 배치 하나.

    **배치 규칙만** 본다. 물체 구성 자체의 위반(compose: 예를 들어 컵이 한
    개뿐)은 어떤 배치로도 고칠 수 없으므로 여기서 후보를 버리는 근거가 되지
    않는다 -- 사람이 고른 조합을 추천기가 조용히 거부하면 "왜 아무것도 안
    나오는지" 알 수 없게 된다. 그런 위반은
    :func:`gello.scene.scene_rules.violations_by_section` 으로 호출자가
    읽어 사용자에게 보여준다.
    """
    if not objects:
        raise ValueError("배치할 물체가 없다")
    if len(objects) > GRID[0] * GRID[1]:
        raise ValueError(
            f"물체 {len(objects)}개는 격자 {GRID[0]}x{GRID[1]} 칸보다 많다")
    for _ in range(max_attempts):
        md = _md(objects, _shuffled_cells(rng), scene_id)
        if not violations_by_section(md, props)["placement"]:
            return md
    raise ValueError(
        f"배치 규칙을 만족하는 배치를 {max_attempts}회 시도 중 찾지 못함 -- "
        "물체 구성이 격자에 들어갈 수 없을 수 있다")
