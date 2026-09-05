"""scene 의 거리 계산용 요약(Signature)과 합산 거리.

거리는 metadata 에서 파생만 하고 저장하지 않는다 -- 저장하면 placements 와
어긋날 수 있는 두 번째 진실이 생긴다.

합산 거리 [0,1] = 가중 합:
- 물체 조합 차이 (multiset Jaccard, (category,color,material) 기준) — 0.5
- 배치 차이 (같은 category 끼리 매칭 후 존 맨해튼 거리, 최대 4 정규화) — 0.35
- 관계 차이 (relations 집합 Jaccard, instance ID 를 category 로 치환) — 0.15
매칭되는 공통 category 가 없으면 배치 성분은 나머지에 재분배한다.

축별 분해는 :mod:`gello.scene.axes` 가 이 모듈의 부품(jaccard_multiset,
placement_distance)으로 조립한다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from gello.scene.scene_format import STANDARD_GRID

W_OBJ, W_PLACE, W_REL = 0.5, 0.35, 0.15

# ---- scene 모양 상수 -----------------------------------------------------
# 후보 생성(sampler)과 커버리지 서포트(axes)가 반드시 같은 값을 봐야 하는
# 것들이라 두 모듈이 함께 의존하는 여기에 둔다.

#: 격자 크기. 정본은 scene_format.STANDARD_GRID 다 -- 여기서 다시 적으면
#: 표준 격자를 바꿀 때 한쪽만 바뀐다.
GRID = tuple(STANDARD_GRID)

#: 씬의 물체 개수 범위. 후보 생성 범위이자 count 축의 서포트다.
#: (recommender-v3-plan.md D8: 다음 단계에서 scene_rules.yaml 로 옮긴다.)
MIN_OBJECTS, MAX_OBJECTS = 2, 5


@dataclass(frozen=True)
class Signature:
    """거리 계산에 쓰는 scene 의 요약 -- metadata 에서 파생만 하고 저장 안 함."""

    triples: tuple            # ((category,color,material), ...) 정렬됨
    placements: tuple         # ((category, (r,c)), ...) 정렬됨
    relations: frozenset      # (category, rel, category)


def _prop_triple(oid: str, props: dict) -> tuple:
    p = props.get(oid)
    if p is None:
        # 인벤토리에서 빠진 ID(은퇴 후 삭제 등) -- ID 토큰으로 근사한다.
        parts = oid.split("-")
        return (parts[1].lower() if len(parts) > 1 else oid, "?", "?")
    return (p.category, p.color, p.material)


def signature(md, props: dict) -> Signature:
    """md 는 SceneMetadata 또는 같은 필드를 가진 객체."""
    placements = md.layout.get("placements", {})
    cats = {oid: _prop_triple(oid, props)[0] for oid in md.objects}
    return Signature(
        triples=tuple(sorted(_prop_triple(o, props) for o in md.objects)),
        placements=tuple(sorted(
            (cats[oid], tuple(spec["zone"]))
            for oid, spec in placements.items() if oid in cats)),
        relations=frozenset(
            (cats.get(a, a), rel, cats.get(b, b))
            for a, rel, b in md.layout.get("relations", [])),
    )


def jaccard_multiset(a: Counter, b: Counter) -> float:
    union = sum((a | b).values())
    if not union:
        return 0.0
    return 1.0 - sum((a & b).values()) / union


def placement_distance(sa: Signature, sb: Signature) -> "float | None":
    """같은 category 끼리 가까운 존부터 그리디 매칭. 매칭 쌍이 없으면 None."""
    by_cat_a: dict = {}
    by_cat_b: dict = {}
    for cat, zone in sa.placements:
        by_cat_a.setdefault(cat, []).append(zone)
    for cat, zone in sb.placements:
        by_cat_b.setdefault(cat, []).append(zone)
    dists = []
    for cat in set(by_cat_a) & set(by_cat_b):
        remaining = list(by_cat_b[cat])
        for za in by_cat_a[cat]:
            if not remaining:
                break
            zb = min(remaining,
                     key=lambda z: abs(z[0] - za[0]) + abs(z[1] - za[1]))
            remaining.remove(zb)
            d = abs(zb[0] - za[0]) + abs(zb[1] - za[1])
            dists.append(min(d, 4) / 4.0)
    if not dists:
        return None
    return sum(dists) / len(dists)


def scene_distance(a: Signature, b: Signature) -> float:
    d_obj = jaccard_multiset(Counter(a.triples), Counter(b.triples))
    d_rel = jaccard_multiset(Counter(a.relations), Counter(b.relations)) \
        if (a.relations or b.relations) else 0.0
    d_place = placement_distance(a, b)
    if d_place is None:
        # 공통 category 가 없다 -- 배치 성분을 나머지에 재분배
        w = W_OBJ + W_REL
        return (W_OBJ * d_obj + W_REL * d_rel) / w
    return W_OBJ * d_obj + W_PLACE * d_place + W_REL * d_rel
