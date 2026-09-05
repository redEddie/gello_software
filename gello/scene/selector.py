"""추천 선택 정책 -- 거리 버킷 쿼터 + 축별 커버리지 보강.

2026-08-26 개편(이슈 #33)의 두 함정 대응이 여기 있다.

함정 1) 거리 메트릭이 재지 않는 축은 다양해지지 않는다.
  합산 거리 하나만 보면 "전부 위치 차이"인 후보가 이겨도 색·종류 다양성은
  제자리다. → 버킷 안에서는 :mod:`gello.scene.axes` 의 커버리지 균등성이
  낮은 축의 덜 쓰인 bin 을 가장 많이 채우는 후보를 고른다.

함정 2) farthest-point 반복은 공간의 모서리로 밀린다.
  유한한 조합 공간에서 최대-최소 거리만 반복해 뽑으면 극단 조합으로
  쏠린다 -- 로봇이 실제로 만날 씬은 중간 거리에 몰려 있다. → 후보의
  기존-최소거리를 3분위(원거리/중간/근거리)로 잘라 버킷을 순환한다.

함정 3(지시문과 씬의 상관)은 여기서 다루지 않는다 -- 지시문은 선택 이후
별도 단계(:mod:`gello.scene.skill_stats`)에서 정하고, 상관 자체는 감사
도구(``scripts/analyze/audit_scene_diversity.py``)가 감시한다.
"""

from __future__ import annotations

import random

from gello.scene.axes import (
    AXES,
    COVERAGE_AXES,
    add_to_coverage,
    axis_coverage,
    axis_distances,
    axis_support,
    coverage_gain,
    coverage_uniformity,
)
from gello.scene.sampler import MIN_OBJECTS, generate_candidate, place_objects
from gello.scene.signature import scene_distance, signature

#: 버킷 순환 순서. 첫 추천은 여전히 가장 새로운(원거리) 것이 되도록
#: 원거리부터 돈다.
BUCKETS = ("원거리", "중간", "근거리")


def _select(cands: list, ex_sigs: list, props: dict, k: int) -> list:
    """후보 [(md, sig, 기존최소거리), ...] 에서 k 개를 고른다.

    절차: 거리 3분위로 버킷을 만들고(분위 기반이라 버킷이 비지 않는다),
    원거리→중간→근거리 순환으로 하나씩 뽑되 버킷 안에서는 (기존 + 이미
    뽑힌 것 기준) 커버리지 이득이 가장 큰 후보를 고른다. 동률이면 거리가
    큰 쪽, 그래도 동률이면 먼저 생성된 후보(결정적).
    """
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
            return (coverage_gain(sig, hist, weights), min(dmin, d_all))

        best = max(pool, key=_score)
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
        add_to_coverage(hist, sig)
    return picked


def _collect(make_md, props: dict, ex_sigs: list, n_candidates: int,
             keep=None) -> list:
    """후보를 n_candidates 번 만들어 중복·기존동일을 걸러 모은다.

    make_md() 가 SceneMetadata 를 준다. keep(md) 가 False 면 버린다.
    """
    cands: list = []
    seen: set = set()
    for _ in range(n_candidates):
        md = make_md()
        if keep is not None and not keep(md):
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
    return cands


def recommend_detailed(existing: list, props: dict, k: int = 3,
                       n_candidates: int = 400, seed: int = 0,
                       scene_id: str = "S999",
                       min_objects: int = MIN_OBJECTS) -> list:
    """조합·배치를 함께 추천한다 (워크플로 ① 전체 추천).

    반환: [{"md", "min_dist", "bucket", "axes", "weak_axis", "uniformity"}].
    axes = 기존 scene 들과의 축별 최소 거리 (position 은 정의 안 되면 None),
    uniformity = 이 추천을 뽑기 직전의 축별 균등성. 기존이 비어 있으면
    모든 후보가 원거리(거리 1.0)라 순수 커버리지 순으로 뽑힌다.
    min_objects 로 작은 씬을 후보에서 제외할 수 있다 (예: 5 = 5물체만) --
    기본은 전 범위이고, 개수 균형은 count 커버리지 축이 잡는다.
    """
    rng = random.Random(seed)
    ex_sigs = [signature(md, props) for md in existing]
    cands = _collect(lambda: generate_candidate(props, rng, scene_id=scene_id),
                     props, ex_sigs, n_candidates,
                     keep=lambda md: len(md.objects) >= min_objects)
    return _select(cands, ex_sigs, props, k)


def recommend_placement(objects: list, existing: list, props: dict,
                        k: int = 3, n_candidates: int = 200, seed: int = 0,
                        scene_id: str = "S999") -> list:
    """물체 집합이 정해졌을 때 배치만 추천한다 (워크플로 ② 배치만 추천).

    반환 모양은 :func:`recommend_detailed` 와 같아 GUI·CLI 가 같은 카드를
    쓴다. 커버리지 히스토그램도 같은 것을 보므로, 이렇게 만든 scene 도
    전체 추천의 다양성 계산에 그대로 반영된다.

    물체 구성 자체의 규칙 위반(compose)은 배치로 고칠 수 없어 여기서
    후보를 버리는 근거가 되지 않는다 -- 호출자가
    :func:`gello.scene.scene_rules.violations_by_section` 으로 읽어
    사용자에게 보여줄 것.
    """
    rng = random.Random(seed)
    ex_sigs = [signature(md, props) for md in existing]
    cands = _collect(
        lambda: place_objects(objects, props, rng, scene_id=scene_id),
        props, ex_sigs, n_candidates)
    return _select(cands, ex_sigs, props, k)


def recommend(existing: list, props: dict, k: int = 3,
              n_candidates: int = 400, seed: int = 0,
              scene_id: str = "S999",
              min_objects: int = MIN_OBJECTS) -> list:
    """호환 래퍼: [(SceneMetadata, min_distance), ...].

    2026-08-26 이전에는 순수 greedy farthest-point 였다 (모서리 쏠림 --
    함정 2). 지금은 :func:`recommend_detailed` 의 버킷 쿼터 결과를 그대로
    돌려준다. 버킷 순환이 원거리부터라 거리 내림차순 경향은 유지되지만
    보장은 아니다 -- 근거는 detailed 쪽 bucket/axes 필드를 보라.
    """
    return [(r["md"], r["min_dist"])
            for r in recommend_detailed(existing, props, k=k,
                                        n_candidates=n_candidates,
                                        seed=seed, scene_id=scene_id,
                                        min_objects=min_objects)]
