"""scene 다양성 추천 — max-min(farthest-point) 거리 기반 (이슈 #33).

기존 scene 들과의 거리를 계산해, 겹치지 않는 다음 소품 조합·배치를
추천한다. 전부 순수 함수 + 주입된 난수(seed)라 결정적이고 로봇이 필요
없다.

거리 [0,1] = 가중 합:
- 물체 조합 차이 (multiset Jaccard, (category,color,material) 기준) — 0.5
- 배치 차이 (같은 category 끼리 매칭 후 존 맨해튼 거리, 최대 4 정규화) — 0.35
- 관계 차이 (relations 집합 Jaccard, instance ID 를 category 로 치환) — 0.15
매칭되는 공통 category 가 없으면 배치 성분은 나머지에 재분배한다.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from gello.scene_format import SceneMetadata
from gello.scene_rules import check

W_OBJ, W_PLACE, W_REL = 0.5, 0.35, 0.15
GRID = (3, 3)


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


def _jaccard_multiset(a: Counter, b: Counter) -> float:
    union = sum((a | b).values())
    if not union:
        return 0.0
    return 1.0 - sum((a & b).values()) / union


def _placement_distance(sa: Signature, sb: Signature) -> "float | None":
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
    d_obj = _jaccard_multiset(Counter(a.triples), Counter(b.triples))
    d_rel = _jaccard_multiset(Counter(a.relations), Counter(b.relations)) \
        if (a.relations or b.relations) else 0.0
    d_place = _placement_distance(a, b)
    if d_place is None:
        # 공통 category 가 없다 -- 배치 성분을 나머지에 재분배
        w = W_OBJ + W_REL
        return (W_OBJ * d_obj + W_REL * d_rel) / w
    return W_OBJ * d_obj + W_PLACE * d_place + W_REL * d_rel


def generate_candidate(props: dict, rng: random.Random,
                       scene_id: str = "S999",
                       max_attempts: int = 200) -> SceneMetadata:
    """인벤토리 제약 안의 무작위 scene: 컵 ≥1 + 다른 종류 ≥1, 물체 2~5개,
    존 비충돌. 추가로 configs/scene_rules.yaml 의 규칙을 만족하지 않으면
    재시도한다."""
    active = [p for p in props.values() if not p.retired]
    cups = [p for p in active if p.category == "cup"]
    others = [p for p in active if p.category != "cup"]
    if not cups or not others:
        raise ValueError("인벤토리에 컵과 다른 종류가 최소 1개씩 필요하다")
    for attempt in range(max_attempts):
        n = rng.randint(2, min(5, len(active)))
        picked = [rng.choice(cups), rng.choice(others)]
        pool = [p for p in active if p not in picked]
        rng.shuffle(pool)
        picked += pool[:n - 2]
        cells = [(r, c) for r in range(GRID[0]) for c in range(GRID[1])]
        rng.shuffle(cells)
        placements = {p.id: {"zone": list(cells[i])} for i, p in enumerate(picked)}
        md = SceneMetadata(
            scene_id=scene_id,
            objects=[p.id for p in picked],
            layout={"grid": list(GRID), "placements": placements},
            description="(추천안 -- 채택 시 배치 의도를 적어주세요)",
        )
        if not check(md, props):
            return md
    raise ValueError(
        f"규칙을 만족하는 후보를 {max_attempts}회 시도 중 생성하지 못함"
    )


def recommend(existing: list, props: dict, k: int = 3,
              n_candidates: int = 400, seed: int = 0,
              scene_id: str = "S999") -> list:
    """기존 scene 들과의 최소 거리가 최대인 상위 k 개 추천.

    반환: [(SceneMetadata, min_distance), ...]. 추천안끼리도 greedy
    farthest-point 로 서로 멀게 고른다. 기존과 동일한 (조합, 배치)는
    거리 0 이라 자연히 탈락한다.
    """
    rng = random.Random(seed)
    ex_sigs = [signature(md, props) for md in existing]
    cands = []
    seen: set = set()
    for _ in range(n_candidates):
        md = generate_candidate(props, rng, scene_id=scene_id)
        sig = signature(md, props)
        key = (sig.triples, sig.placements)
        if key in seen:                      # 후보끼리 중복 제거
            continue
        seen.add(key)
        if any(scene_distance(sig, e) == 0.0 for e in ex_sigs):
            continue                         # 기존 scene 과 동일 -- 제외
        cands.append((md, sig))
    picked: list = []
    picked_sigs: list = []
    def _diversity_bonus(md: SceneMetadata) -> float:
        """scene 낸부 distinct (category, color) 비율 -- 타이브레이커."""
        n = len(md.objects)
        if n <= 1:
            return 0.0
        distinct = len({(_prop_triple(o, props)[0], _prop_triple(o, props)[1])
                        for o in md.objects})
        return 0.05 * distinct / n

    while cands and len(picked) < k:
        def score(item):
            _md, sig = item
            ds = [scene_distance(sig, e) for e in ex_sigs + picked_sigs]
            base = min(ds) if ds else 1.0
            return base + _diversity_bonus(_md)
        best = max(cands, key=score)
        picked.append((best[0], round(score(best), 4)))
        picked_sigs.append(best[1])
        cands.remove(best)
    return picked
