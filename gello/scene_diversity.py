"""scene 다양성 추천 — 거리 버킷 쿼터 + 축별 커버리지 보강 (이슈 #33, 2026-08-26 개편).

기존 scene 들과의 거리를 계산해, 겹치지 않는 다음 소품 조합·배치를
추천한다. 전부 순수 함수 + 주입된 난수(seed)라 결정적이고 로봇이 필요
없다.

합산 거리 [0,1] = 가중 합 (선택 기준·기존과 동일):
- 물체 조합 차이 (multiset Jaccard, (category,color,material) 기준) — 0.5
- 배치 차이 (같은 category 끼리 매칭 후 존 맨해튼 거리, 최대 4 정규화) — 0.35
- 관계 차이 (relations 집합 Jaccard, instance ID 를 category 로 치환) — 0.15
매칭되는 공통 category 가 없으면 배치 성분은 나머지에 재분배한다.

2026-08-26 개편 — 생성 알고리즘의 편향이 데이터셋에 새는 세 함정 대응:

함정 1) 거리 메트릭이 재지 않는 축은 다양해지지 않는다.
  합산 거리 하나만 보면 "전부 위치 차이"인 후보가 이겨도 색·종류 다양성은
  제자리다. → :func:`axis_distances` 로 거리를 축별(category/color/position/
  relation)로 분해해 관리하고, 선택 시에는 :func:`axis_coverage` 히스토그램의
  균등성(정규화 엔트로피)이 낮은 축을 보강하는 후보를 우선한다.

함정 2) farthest-point 반복은 공간의 모서리로 밀린다.
  유한한 조합 공간에서 최대-최소 거리만 반복해 뽑으면 극단 조합으로
  쏠린다 — 로봇이 실제로 만날 씬은 중간 거리에 몰려 있다. →
  :func:`recommend_detailed` 는 후보의 기존-최소거리를 3분위(원거리/중간/
  근거리) 버킷으로 나누고 버킷을 순환하며 뽑는다 (거리 구간별 쿼터).
  근처 변형과 먼 변형을 둘 다 확보한다.

함정 3) 거리 기반은 scene→task 상관을 보장하지 못한다.
  새 씬이 아무리 멀어도 태스크가 씬에서 같은 방식으로 결정되면 상관은
  남는다. → 지시문은 거리 선택 **이후 별도 단계**에서 정한다
  (:mod:`gello.skill_stats` — 실행 가능한 스킬 중 누적 수집횟수가 적은
  것 우선). 상관 자체는 이 모듈이 아니라 감사 도구
  (``scripts/audit_scene_diversity.py``)가 별도 지표로 감시한다.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass

from gello.scene_format import SceneMetadata
from gello.scene_rules import check

W_OBJ, W_PLACE, W_REL = 0.5, 0.35, 0.15
GRID = (3, 3)

#: 축별 분해의 축 이름. position 은 공통 category 가 없으면 정의되지 않아
#: axis_distances 가 None 을 줄 수 있다.
AXES = ("category", "color", "position", "relation")

#: 커버리지(히스토그램) 관리 대상 축 — relation 은 현재 씬들이 거의 쓰지
#: 않아 서포트를 정의할 수 없으므로 제외한다. count(물체 개수)는 거리
#: 축은 아니지만 커버리지 축이다 (2026-08-26): 안 재면 선택이 작은 씬으로
#: 쏠린다 -- 커버리지 gain 이 항목 평균이라 희귀 bin 하나짜리 작은 씬이
#: 흔한 물체 섞인 큰 씬을 항상 이겼다 (실측: 후보 풀 과반이 5물체인데
#: 추천은 전부 3물체). 함정 1 의 재발 사례.
COVERAGE_AXES = ("category", "color", "position", "count")

#: generate_candidate 의 물체 개수 범위 -- count 축 서포트와 일치해야 한다.
MIN_OBJECTS, MAX_OBJECTS = 2, 5

#: 버킷 순환 순서. 첫 추천은 여전히 가장 새로운(원거리) 것이 되도록
#: 원거리부터 돈다.
BUCKETS = ("원거리", "중간", "근거리")


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


def axis_distances(a: Signature, b: Signature) -> dict:
    """거리를 축별로 분해한다 (함정 1 대응 — 모니터링·커버리지 판단용).

    반환: {"category": [0,1], "color": [0,1], "position": [0,1] | None,
           "relation": [0,1]}. position 은 공통 category 가 없으면 정의되지
    않아 None. 합산 :func:`scene_distance` 는 (category,color,material)
    결합 Jaccard 를 쓰므로 이 분해의 가중합과 정확히 같지는 않다 — 이
    함수의 목적은 "합산이 커도 그게 전부 위치 차이인가" 를 보는 것이다.
    """
    return {
        "category": _jaccard_multiset(
            Counter(t[0] for t in a.triples), Counter(t[0] for t in b.triples)),
        "color": _jaccard_multiset(
            Counter(t[1] for t in a.triples), Counter(t[1] for t in b.triples)),
        "position": _placement_distance(a, b),
        "relation": _jaccard_multiset(Counter(a.relations),
                                      Counter(b.relations))
        if (a.relations or b.relations) else 0.0,
    }


# ---- 축별 커버리지 (함정 1·2의 선택 보정에 사용) ---------------------------

def axis_support(props: dict) -> dict:
    """각 커버리지 축의 전체 bin 집합 — 안 쓰인 bin 도 0 으로 세어야
    균등성이 부풀지 않는다. category/color 는 활성 인벤토리, position 은
    3×3 격자 전체."""
    active = [p for p in props.values() if not p.retired]
    return {
        "category": {p.category for p in active},
        "color": {p.color for p in active},
        "position": {(r, c) for r in range(GRID[0]) for c in range(GRID[1])},
        "count": set(range(MIN_OBJECTS, MAX_OBJECTS + 1)),
    }


def axis_coverage(sigs: list) -> dict:
    """signature 들이 각 축에서 어떤 bin 을 몇 번 썼는지 히스토그램."""
    hist = {ax: Counter() for ax in COVERAGE_AXES}
    for s in sigs:
        hist["category"].update(t[0] for t in s.triples)
        hist["color"].update(t[1] for t in s.triples)
        hist["position"].update(z for _, z in s.placements)
        hist["count"][len(s.triples)] += 1
    return hist


def _norm_entropy(counter: Counter, support: set) -> float:
    """support 전체 bin 기준 정규화 엔트로피 [0,1]. 1 = 완전 균등."""
    if len(support) <= 1:
        return 1.0
    total = sum(counter.get(s, 0) for s in support)
    if total == 0:
        return 0.0
    h = 0.0
    for s in support:
        p = counter.get(s, 0) / total
        if p > 0:
            h -= p * math.log(p)
    return h / math.log(len(support))


def coverage_uniformity(hist: dict, support: dict) -> dict:
    """축별 균등성 {axis: [0,1]}. 낮은 축이 '다양해지지 않은 축'이다."""
    return {ax: _norm_entropy(hist[ax], support[ax]) for ax in COVERAGE_AXES}


def _coverage_gain(sig: Signature, hist: dict, weights: dict) -> float:
    """이 후보가 덜 쓰인 bin 을 얼마나 채우는가 — 축별 1/(1+count) 합을
    항목 수로 정규화하고 축 가중치(약한 축일수록 큼)를 곱한다."""
    items = {
        "category": [t[0] for t in sig.triples],
        "color": [t[1] for t in sig.triples],
        "position": [z for _, z in sig.placements],
        "count": [len(sig.triples)],
    }
    g = 0.0
    for ax in COVERAGE_AXES:
        vals = items[ax]
        w = weights.get(ax, 0.0)
        if not vals or w <= 0:
            continue
        g += w * sum(1.0 / (1.0 + hist[ax][v]) for v in vals) / len(vals)
    return g


def generate_candidate(props: dict, rng: random.Random,
                       scene_id: str = "S999",
                       max_attempts: int = 200) -> SceneMetadata:
    """인벤토리 제약 안의 무작위 scene: 등장 category 는 색 다른 2개 이상
    (pair_if_present), pickable 최소 한 종류, 물체 2~5개, 존 비충돌.
    configs/scene_rules.yaml 규칙을 만족하지 않으면 재시도한다."""
    active = [p for p in props.values() if not p.retired]
    # pair_if_present 규칙(2026-08-24) 아래에서는 물체 단위 무작위 뽑기가
    # 거의 다 기각된다 -- category 단위로 "짝"을 뽑는다: 등장시키는
    # category 마다 서로 다른 색 min 2개, 총 2~5개, pickable(cup/small_bowl)
    # 최소 한 종류 포함, drawer 는 단일이라 자유.
    by_cat: dict = {}
    for p_ in active:
        by_cat.setdefault(p_.category, {}).setdefault(p_.color, []).append(p_)
    paired_cats = [c for c in ("cup", "small_bowl", "large_bowl")
                   if len(by_cat.get(c, {})) >= 2]
    if not any(c in paired_cats for c in ("cup", "small_bowl")):
        raise ValueError("인벤토리에 2색 이상인 pickable category 가 없다")
    for _ in range(max_attempts):
        cats = [c for c in paired_cats if rng.random() < 0.6]
        if not any(c in cats for c in ("cup", "small_bowl")):
            continue
        picked = []
        for c in cats:
            colors = list(by_cat[c])
            rng.shuffle(colors)
            take = rng.randint(2, min(3, len(colors)))
            picked += [rng.choice(by_cat[c][col]) for col in colors[:take]]
        if "drawer" in by_cat and len(picked) <= 4 and rng.random() < 0.5:
            picked.append(rng.choice(by_cat["drawer"][
                next(iter(by_cat["drawer"]))]))
        if not 2 <= len(picked) <= 5:
            continue
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


def recommend_detailed(existing: list, props: dict, k: int = 3,
                       n_candidates: int = 400, seed: int = 0,
                       scene_id: str = "S999",
                       min_objects: int = MIN_OBJECTS) -> list:
    """거리 버킷 쿼터 + 축별 커버리지 보강으로 k 개 추천.

    절차:
      1. 규칙을 통과한 무작위 후보 n_candidates 개 생성(중복·기존 동일 제외).
      2. 각 후보의 기존-최소 합산거리를 3분위로 잘라 원거리/중간/근거리
         버킷을 만든다 (분위 기반이라 버킷이 비지 않는다 — 함정 2).
      3. 원거리→중간→근거리 순환으로 버킷에서 하나씩 뽑되, 버킷 안에서는
         (기존+이미 뽑힌 것 기준) 커버리지 균등성이 낮은 축의 덜 쓰인 bin 을
         가장 많이 채우는 후보를 고른다 (함정 1). 동률이면 거리가 큰 쪽.

    반환: [{"md", "min_dist", "bucket", "axes", "weak_axis", "uniformity"}].
    axes = 기존 scene 들과의 축별 최소 거리 (position 은 정의 안 되면 None),
    uniformity = 이 추천을 뽑기 직전의 축별 균등성. 기존이 비어 있으면
    모든 후보가 원거리(거리 1.0)라 순수 커버리지 순으로 뽑힌다.
    min_objects 로 작은 씬을 후보에서 제외할 수 있다 (예: 5 = 5물체만) --
    기본은 전 범위이고, 개수 균형은 count 커버리지 축이 잡는다.
    지시문 결정은 여기서 하지 않는다 — :mod:`gello.skill_stats` 로 별도
    단계에서 (함정 3).
    """
    rng = random.Random(seed)
    ex_sigs = [signature(md, props) for md in existing]
    cands: list = []
    seen: set = set()
    for _ in range(n_candidates):
        md = generate_candidate(props, rng, scene_id=scene_id)
        if len(md.objects) < min_objects:
            continue
        sig = signature(md, props)
        key = (sig.triples, sig.placements)
        if key in seen:                      # 후보끼리 중복 제거
            continue
        seen.add(key)
        dmin = min((scene_distance(sig, e) for e in ex_sigs), default=1.0)
        if ex_sigs and dmin == 0.0:
            continue                         # 기존 scene 과 동일 -- 제외
        cands.append((md, sig, dmin))
    if not cands:
        return []

    ds = sorted(c[2] for c in cands)
    q1, q2 = ds[len(ds) // 3], ds[(2 * len(ds)) // 3]

    def bucket_of(d: float) -> str:
        if d >= q2:
            return "원거리"
        if d >= q1:
            return "중간"
        return "근거리"

    hist = axis_coverage(ex_sigs)
    support = axis_support(props)
    picked: list = []
    picked_sigs: list = []
    step = 0
    while cands and len(picked) < k:
        pool: list = []
        bname = BUCKETS[0]
        for attempt in range(len(BUCKETS)):
            b = BUCKETS[(step + attempt) % len(BUCKETS)]
            pool = [c for c in cands
                    if bucket_of(c[2]) == b
                    and all(scene_distance(c[1], s) > 0.0
                            for s in picked_sigs)]
            if pool:
                bname = b
                break
        if not pool:
            break
        step += 1
        uni = coverage_uniformity(hist, support)
        # 약한 축일수록 큰 가중치. +0.05 는 전 축이 완전 균등이어도 커버리지
        # 신호가 완전히 죽지 않게 하는 바닥값.
        weights = {ax: (1.0 - uni[ax]) + 0.05 for ax in COVERAGE_AXES}
        weak_axis = min(COVERAGE_AXES, key=lambda ax: uni[ax])

        def _score(c):
            _md, sig, dmin = c
            d_all = min((scene_distance(sig, s) for s in picked_sigs),
                        default=dmin)
            return (_coverage_gain(sig, hist, weights), min(dmin, d_all))

        best = max(pool, key=_score)         # 동률이면 먼저 생성된 후보(결정적)
        md, sig, dmin = best
        cands.remove(best)
        axes = {}
        for ax in AXES:
            vals = [axis_distances(sig, e)[ax] for e in ex_sigs]
            vals = [v for v in vals if v is not None]
            axes[ax] = round(min(vals), 4) if vals else None
        picked.append({
            "md": md,
            "min_dist": round(dmin, 4),
            "bucket": bname,
            "axes": axes,
            "weak_axis": weak_axis,
            "uniformity": {ax: round(v, 3) for ax, v in uni.items()},
        })
        picked_sigs.append(sig)
        # 다음 픽은 이 픽까지 반영한 커버리지로 판단한다.
        hist["category"].update(t[0] for t in sig.triples)
        hist["color"].update(t[1] for t in sig.triples)
        hist["position"].update(z for _, z in sig.placements)
        hist["count"][len(sig.triples)] += 1
    return picked


def recommend(existing: list, props: dict, k: int = 3,
              n_candidates: int = 400, seed: int = 0,
              scene_id: str = "S999",
              min_objects: int = MIN_OBJECTS) -> list:
    """호환 래퍼: [(SceneMetadata, min_distance), ...].

    2026-08-26 이전에는 순수 greedy farthest-point 였다 (모서리 쏠림 —
    함정 2). 지금은 :func:`recommend_detailed` 의 버킷 쿼터 결과를 그대로
    돌려준다. 버킷 순환이 원거리부터라 거리 내림차순 경향은 유지되지만
    보장은 아니다 — 근거는 detailed 쪽 bucket/axes 필드를 보라.
    """
    return [(r["md"], r["min_dist"])
            for r in recommend_detailed(existing, props, k=k,
                                        n_candidates=n_candidates,
                                        seed=seed, scene_id=scene_id,
                                        min_objects=min_objects)]
