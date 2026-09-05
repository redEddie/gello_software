"""scene 다양성 추천 -- 공개 API 파사드 (이슈 #33, 2026-09-06 분할).

구현은 역할별로 나뉘어 있고 이 모듈은 이름을 한곳에 모아 재수출만 한다.
호출자(GUI·CLI·감사도구·테스트)는 지금까지처럼 여기서 가져오면 된다.

    signature.py   Signature · 합산 거리 · scene 모양 상수(GRID, 개수 범위)
    axes.py        다양성 축 레지스트리 -- 거리 분해와 커버리지의 단일 어휘
    sampler.py     후보 생성 (조합+배치 / 배치만)
    placement_solver.py  배치 CP-SAT -- 규칙을 제약으로, 실행 가능 배치 전수
    selector.py    선택 정책 -- 거리 버킷 쿼터 + 커버리지 보강

왜 나눴나: 배치 특징을 축으로 추가하려면 예전에는 두 벌의 축 이름
(``AXES``/``COVERAGE_AXES``)과 그것을 읽는 세 소비자를 함께 고쳐야 했다.
이제 축 추가는 :data:`gello.scene.axes.REGISTRY` 항목 하나다.
배경과 결정 근거는 ``docs/recommender-v3-plan.md``.

추천의 두 진입점:
    recommend_detailed(existing, props, ...)              조합+배치 (전체 추천)
    recommend_placement(objects, existing, props, ...)    배치만 (물체는 사람이 선택)
"""

from gello.scene.axes import (  # noqa: F401  (재수출)
    AXES,
    COVERAGE_AXES,
    REGISTRY,
    Axis,
    add_to_coverage,
    axis_coverage,
    axis_distances,
    axis_support,
    coverage_gain,
    coverage_uniformity,
)
from gello.scene.sampler import (  # noqa: F401  (재수출)
    MAX_OBJECTS,
    MIN_OBJECTS,
    all_placements,
    generate_candidate,
    place_objects,
)
from gello.scene.selector import (  # noqa: F401  (재수출)
    BUCKETS,
    recommend,
    recommend_detailed,
    recommend_placement,
)
from gello.scene.signature import (  # noqa: F401  (재수출)
    GRID,
    W_OBJ,
    W_PLACE,
    W_REL,
    Signature,
    scene_distance,
    signature,
)

__all__ = [
    "AXES", "COVERAGE_AXES", "REGISTRY", "Axis", "GRID",
    "MIN_OBJECTS", "MAX_OBJECTS", "BUCKETS", "W_OBJ", "W_PLACE", "W_REL",
    "Signature", "signature", "scene_distance",
    "axis_distances", "axis_support", "axis_coverage", "add_to_coverage",
    "coverage_uniformity", "coverage_gain",
    "generate_candidate", "place_objects", "all_placements",
    "recommend", "recommend_detailed", "recommend_placement",
]
