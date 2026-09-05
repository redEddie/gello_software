"""다양성 축 레지스트리 -- 거리 분해와 커버리지 히스토그램의 단일 어휘.

2026-08-26 개편(이슈 #33) 이후 축 이름이 두 벌로 나뉘어 있었다: 거리
분해용 ``AXES``(category/color/position/relation)와 히스토그램용
``COVERAGE_AXES``(category/color/position/count). 겹치는데 다르고, 축을
하나 늘리려면 두 곳과 그 이름을 읽는 GUI·CLI·감사도구를 함께 고쳐야 했다.

여기서는 축을 :class:`Axis` 한 종류로 정의하고 두 목록을 그 등록부에서
**파생**시킨다. 새 축을 추가하는 비용은 등록부 항목 하나 + 추출 함수
하나이며, 소비자들은 ``AXES``/``COVERAGE_AXES`` 를 그대로 읽으므로 자동으로
새 축을 표시한다.

축의 두 역할은 서로 독립이다:
- ``distance``  두 scene 사이의 그 축 거리. 없으면 거리 분해에 안 나온다.
- ``extract``/``support``  히스토그램의 항목과 전체 bin 집합. 없으면
  커버리지 관리 대상이 아니다 (relation 은 현재 씬들이 거의 쓰지 않아
  서포트를 정의할 수 없어 거리 전용이다).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

from gello.scene.scene_rules import object_count_range
from gello.scene.signature import (
    GRID,
    Signature,
    jaccard_multiset,
    placement_distance,
)


@dataclass(frozen=True)
class Axis:
    """다양성의 한 축. distance 만 있으면 거리 전용, extract/support 만
    있으면 커버리지 전용, 둘 다 있으면 양쪽에 나온다."""

    name: str
    distance: Optional[Callable[[Signature, Signature], "float | None"]] = None
    extract: Optional[Callable[[Signature], list]] = None
    support: Optional[Callable[[dict], set]] = None

    @property
    def is_distance(self) -> bool:
        return self.distance is not None

    @property
    def is_coverage(self) -> bool:
        return self.extract is not None and self.support is not None


def _cat_distance(a: Signature, b: Signature) -> float:
    return jaccard_multiset(Counter(t[0] for t in a.triples),
                            Counter(t[0] for t in b.triples))


def _color_distance(a: Signature, b: Signature) -> float:
    return jaccard_multiset(Counter(t[1] for t in a.triples),
                            Counter(t[1] for t in b.triples))


def _relation_distance(a: Signature, b: Signature) -> float:
    if not (a.relations or b.relations):
        return 0.0
    return jaccard_multiset(Counter(a.relations), Counter(b.relations))


def _active(props: dict) -> list:
    return [p for p in props.values() if not p.retired]


def _count_support(_props: dict) -> set:
    """개수 축의 bin -- 정본은 scene_rules.yaml 의 object_count 다.
    후보 생성 범위와 반드시 같아야 하므로 둘 다 규칙에서 읽는다."""
    lo, hi = object_count_range()
    return set(range(lo, hi + 1))


#: 등록부. 순서가 곧 표시 순서이고, AXES/COVERAGE_AXES 가 여기서 파생된다.
REGISTRY: tuple = (
    Axis("category",
         distance=_cat_distance,
         extract=lambda s: [t[0] for t in s.triples],
         support=lambda props: {p.category for p in _active(props)}),
    Axis("color",
         distance=_color_distance,
         extract=lambda s: [t[1] for t in s.triples],
         support=lambda props: {p.color for p in _active(props)}),
    Axis("position",
         distance=placement_distance,
         extract=lambda s: [z for _, z in s.placements],
         support=lambda _props: {(r, c) for r in range(GRID[0])
                                 for c in range(GRID[1])}),
    # relation 은 거리 전용 -- 서포트를 정의할 수 없다.
    Axis("relation", distance=_relation_distance),
    # count 는 커버리지 전용. 안 재면 선택이 작은 씬으로 쏠린다 (2026-08-26:
    # 커버리지 gain 이 항목 평균이라 희귀 bin 하나짜리 작은 씬이 흔한 물체
    # 섞인 큰 씬을 항상 이겼다).
    Axis("count",
         extract=lambda s: [len(s.triples)],
         support=_count_support),
)

BY_NAME: dict = {ax.name: ax for ax in REGISTRY}

#: 거리 분해에 나오는 축. position 은 공통 category 가 없으면 None 이 된다.
AXES: tuple = tuple(ax.name for ax in REGISTRY if ax.is_distance)

#: 커버리지(히스토그램) 관리 대상 축.
COVERAGE_AXES: tuple = tuple(ax.name for ax in REGISTRY if ax.is_coverage)


def axis_distances(a: Signature, b: Signature) -> dict:
    """거리를 축별로 분해한다 (모니터링·커버리지 판단용).

    합산 :func:`gello.scene.signature.scene_distance` 는
    (category,color,material) 결합 Jaccard 를 쓰므로 이 분해의 가중합과
    정확히 같지는 않다 -- 이 함수의 목적은 "합산이 커도 그게 전부 위치
    차이인가" 를 보는 것이다.
    """
    return {ax.name: ax.distance(a, b) for ax in REGISTRY if ax.is_distance}


def axis_support(props: dict) -> dict:
    """각 커버리지 축의 전체 bin 집합 -- 안 쓰인 bin 도 0 으로 세어야
    균등성이 부풀지 않는다."""
    return {ax.name: ax.support(props) for ax in REGISTRY if ax.is_coverage}


def axis_coverage(sigs: list) -> dict:
    """signature 들이 각 축에서 어떤 bin 을 몇 번 썼는지 히스토그램."""
    hist = {name: Counter() for name in COVERAGE_AXES}
    for s in sigs:
        add_to_coverage(hist, s)
    return hist


def add_to_coverage(hist: dict, sig: Signature) -> None:
    """히스토그램에 signature 하나를 더한다 (추천을 뽑을 때마다 누적)."""
    for ax in REGISTRY:
        if ax.is_coverage:
            hist[ax.name].update(ax.extract(sig))


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
    return {name: _norm_entropy(hist[name], support[name])
            for name in COVERAGE_AXES}


def coverage_gain(sig: Signature, hist: dict, weights: dict) -> float:
    """이 후보가 덜 쓰인 bin 을 얼마나 채우는가 -- 축별 1/(1+count) 합을
    항목 수로 정규화하고 축 가중치(약한 축일수록 큼)를 곱한다."""
    g = 0.0
    for ax in REGISTRY:
        if not ax.is_coverage:
            continue
        w = weights.get(ax.name, 0.0)
        if w <= 0:
            continue
        vals = ax.extract(sig)
        if not vals:
            continue
        g += w * sum(1.0 / (1.0 + hist[ax.name][v]) for v in vals) / len(vals)
    return g
