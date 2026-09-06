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

#: **새로 만들** scene 의 격자. 정본은 scene_format.STANDARD_GRID 다 --
#: 여기서 다시 적으면 표준 격자를 바꿀 때 한쪽만 바뀐다.
#:
#: 이미 있는 파일을 **읽을 때는 쓰지 않는다**. 파일마다 자기 격자가
#: layout.grid 에 적혀 있고, 거리 계산은 그것을 쓴다 (Signature.grid) --
#: 표준을 3×3 에서 바꾸는 날 옛 파일이 조용히 새 격자로 해석되면 다양성
#: 추천과 scene 경계 판정이 통째로 어긋난다 (2026-09-06 사용자 지적).
GRID = tuple(STANDARD_GRID)


def _max_manhattan(grid) -> int:
    """그 격자에서 가능한 최대 맨해튼 거리 = 대각선 양 끝."""
    rows, cols = int(grid[0]), int(grid[1])
    return max(1, (rows - 1) + (cols - 1))


@dataclass(frozen=True)
class Signature:
    """거리 계산에 쓰는 scene 의 요약 -- metadata 에서 파생만 하고 저장 안 함."""

    triples: tuple            # ((category,color,material), ...) 정렬됨
    placements: tuple         # ((category, (r,c)), ...) 정렬됨
    relations: frozenset      # (category, rel, category)
    #: 이 scene 자신의 격자 (파일의 layout.grid). 거리 정규화에 쓴다 --
    #: 코드 상수가 아니라 여기 값을 쓰는 것이 요점이다.
    grid: tuple = GRID


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
    # 격자는 **그 scene 이 적어 둔 것**을 쓴다. 없는 옛 파일만 표준으로 본다.
    grid = md.layout.get("grid") or GRID
    return Signature(
        grid=(int(grid[0]), int(grid[1])),
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
    """같은 category 끼리 가까운 존부터 그리디 매칭. 매칭 쌍이 없으면 None.

    거리는 그 격자의 최대 맨해튼 거리로 나눠 0~1 로 만든다. 전에는 3×3 의
    값(4)이 숫자로 박혀 있었다 -- 격자를 바꾸면 정규화가 조용히 틀려진다.
    두 scene 의 격자가 다르면 큰 쪽으로 나눈다 (그런 비교 자체가 이미
    의심스럽지만, 여기서 죽을 일은 아니다).
    """
    by_cat_a: dict = {}
    by_cat_b: dict = {}
    for cat, zone in sa.placements:
        by_cat_a.setdefault(cat, []).append(zone)
    for cat, zone in sb.placements:
        by_cat_b.setdefault(cat, []).append(zone)
    span = max(_max_manhattan(sa.grid), _max_manhattan(sb.grid))
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
            dists.append(min(d, span) / span)
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
