"""다음 scene 추천 CLI — 기존 scene 들과 가장 먼 소품 조합·배치 3안.

사용:
    python scripts/analyze/recommend_scene.py [--root ~/libero_datasets] [--seed 0] [-k 3]
    python scripts/analyze/recommend_scene.py --selftest

각 추천안은 describe_scene 격자 지도와, GUI '새 Scene 구성' 없이 바로
쓸 수 있는 layout JSON 으로 출력된다. 로봇·카메라 불필요.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gello.scene.props import props_by_id  # noqa: E402
from gello.scene.instruction_grammar import enumerate_instructions  # noqa: E402
from gello.scene.scene_rules import check  # noqa: E402
from gello.scene.scene_diversity import (  # noqa: E402
    AXES,
    axis_distances,
    recommend,
    recommend_detailed,
    scene_distance,
    signature,
)
from gello.scene.skill_stats import (  # noqa: E402
    collected_skill_counts,
    format_skill_counts,
    rank_instructions,
)
from gello.scene.scene_format import (  # noqa: E402
    SceneMetadata,
    describe_scene,
    iter_scene_files,
    next_scene_id,
    read_scene_metadata,
)


def _selftest() -> None:
    import random

    from gello.scene.props import active_prop_ids
    from gello.scene.scene_diversity import generate_candidate

    props = props_by_id()
    base = SceneMetadata(
        scene_id="S000",
        objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUP-BLU-01": {"zone": [0, 0]},
            "OBJ-BOWLS-WHT-01": {"zone": [1, 1]}}})
    # 1. 동일 scene 거리 0, 자기 자신과의 배치 이동은 > 0
    s0 = signature(base, props)
    assert scene_distance(s0, s0) == 0.0
    moved = SceneMetadata(
        scene_id="S001", objects=list(base.objects),
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUP-BLU-01": {"zone": [2, 2]},
            "OBJ-BOWLS-WHT-01": {"zone": [1, 1]}}})
    d = scene_distance(s0, signature(moved, props))
    assert 0.0 < d < 1.0, d
    print(f"1 통과: 거리 (동일=0, 배치 이동={d:.3f})")

    # 2. 후보가 제약과 validate 를 통과한다
    rng = random.Random(7)
    ids = active_prop_ids()
    for _ in range(50):
        c = generate_candidate(props, rng)
        c.validate(known_prop_ids=ids)
        cats = {props[o].category for o in c.objects}
        # pair_if_present(2026-08-24): 등장 category(단일 개체 제외)는 2개
        # 이상, pickable 최소 한 종류 -- 목록은 문법이 정본 (하드코딩 금지).
        from collections import Counter

        from gello.scene.instruction_grammar import PICKABLE_CATS
        cnt = Counter(props[o].category for o in c.objects)
        assert any(k in cnt for k in PICKABLE_CATS)
        # drawer/tray/cutlery(색 없는 단일 번들)는 짝 규칙에서 제외
        assert all(v >= 2 for k, v in cnt.items()
                   if k not in ("drawer", "tray", "cutlery")), cnt
        assert 2 <= len(c.objects) <= 5
    print("2 통과: 후보 50개 제약+validate 통과")

    # 3. 같은 seed 는 같은 추천, 기존과 동일 조합·배치는 안 나온다
    r1 = recommend([base], props, k=3, seed=42)
    r2 = recommend([base], props, k=3, seed=42)
    assert [m.objects for m, _ in r1] == [m.objects for m, _ in r2]
    assert len(r1) == 3 and all(d > 0 for _, d in r1)
    for md, _ in r1:
        assert signature(md, props) != s0
    # 추천끼리도 서로 다르다
    sigs = [signature(m, props) for m, _ in r1]
    assert len({(s.triples, s.placements) for s in sigs}) == 3
    print("3 통과: 결정성 + 비중복 + 기존 배제")

    # 4. 규칙 필터: 후보 1000개에서 금지 사항 0
    rng2 = random.Random(1234)
    for _ in range(1000):
        c = generate_candidate(props, rng2)
        assert not check(c, props), check(c, props)
    print("4 통과: 후보 1000개 규칙 위반 0")

    # 5. 문법: 같은 scene -> 같은 문장 목록, 모호 지칭 미생성
    from gello.scene.instruction_grammar import enumerate_instructions
    sents_a = enumerate_instructions(base, props)
    sents_b = enumerate_instructions(base, props)
    assert sents_a == sents_b
    # 개별 지칭만 배제된다 -- 집합 지칭("stack all the white cups")은
    # 2026-08-31 부터 정상 생성 (모호하지 않다).
    assert all(not re.search(r"the white cup\b(?!s)", x)
               for x in enumerate_instructions(
        SceneMetadata(
            scene_id="S001",
            objects=["OBJ-CUP-WHT-01", "OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01"],
            layout={"grid": [3, 3], "placements": {
                "OBJ-CUP-WHT-01": {"zone": [0, 0]},
                "OBJ-CUP-WHT-02": {"zone": [0, 1]},
                "OBJ-BOWLS-BLU-01": {"zone": [0, 2]}}}),
        props))
    print("5 통과: 문법 결정성 + 모호 지칭 제외")

    # 6. 축별 분해: 색만 바꾸면 color 만, 위치만 옮기면 position 만 커진다.
    # (색을 물체끼리 '맞바꾸면' 색 팔레트 multiset 이 같아 color 거리 0 이다
    #  -- 축은 팔레트를 재지, 색-종류 결합은 재지 않는다. 결합 편향은 감사
    #  도구의 scene→task 상관 지표가 잡는다.)
    color_swapped = SceneMetadata(
        scene_id="S002",
        objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-PNK-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUP-BLU-01": {"zone": [0, 0]},
            "OBJ-BOWLS-PNK-01": {"zone": [1, 1]}}})
    ax = axis_distances(s0, signature(color_swapped, props))
    assert ax["category"] == 0.0 and ax["color"] > 0.0 and ax["position"] == 0.0, ax
    ax2 = axis_distances(s0, signature(moved, props))
    assert ax2["category"] == 0.0 and ax2["color"] == 0.0 and ax2["position"] > 0.0, ax2
    assert axis_distances(s0, s0) == {a: 0.0 for a in AXES}
    print("6 통과: 축별 거리 분해 (색↔color, 이동↔position 만 반응)")

    # 7. 버킷 쿼터: k=3 이 전부 원거리로 쏠리지 않고, 결정적이다
    ex3 = [base, moved, color_swapped]
    det_a = recommend_detailed(ex3, props, k=3, seed=42)
    det_b = recommend_detailed(ex3, props, k=3, seed=42)
    assert [r["md"].objects for r in det_a] == [r["md"].objects for r in det_b]
    assert len(det_a) == 3
    buckets = [r["bucket"] for r in det_a]
    assert buckets[0] == "원거리", buckets     # 첫 픽은 여전히 가장 새로운 것
    assert len(set(buckets)) >= 2, buckets    # 원거리 독점 금지 (거리 구간 쿼터)
    for r in det_a:
        assert set(r["axes"]) == set(AXES)
        assert r["weak_axis"] in ("category", "color", "position", "count")
        assert 0 < r["min_dist"] <= 1
    # count 커버리지: 기존이 전부 2물체 씬이면 count 축이 최약이 되고,
    # 추천이 2물체만 반복하지 않는다 (물체 개수도 측정되는 축 -- 함정 1)
    from gello.scene.scene_diversity import axis_coverage, axis_support, coverage_uniformity
    uni2 = coverage_uniformity(axis_coverage(
        [signature(m, props) for m in ex3]), axis_support(props))
    assert "count" in uni2
    sizes = {len(r["md"].objects) for r in det_a}
    assert sizes != {2}, sizes
    # min_objects: 5물체만 강제
    det5 = recommend_detailed(ex3, props, k=3, seed=42, min_objects=5)
    assert det5 and all(len(r["md"].objects) == 5 for r in det5), \
        [len(r["md"].objects) for r in det5]
    print(f"7 통과: 버킷 쿼터 {buckets} + count 축 {sorted(sizes)} + "
          f"min_objects=5 강제 OK")

    # 8. 지시문 단계: 부족 스킬 우선 랭킹 + 전 문장 lint 통과 (유일 지칭)
    from collections import Counter as _C

    from gello.scene.instruction_grammar import lint
    from gello.scene.skill_stats import rank_instructions
    fake_counts = _C({"pick-on": 100, "pick-inside": 3, "drawer-open": 50})
    md_d = SceneMetadata(
        scene_id="S003",
        objects=["OBJ-CUP-BLU-01", "OBJ-CUP-WHT-01", "OBJ-BOWLS-WHT-01",
                 "OBJ-BOWLS-BLU-01", "OBJ-DRAWER-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUP-BLU-01": {"zone": [0, 0]},
            "OBJ-CUP-WHT-01": {"zone": [1, 0]},
            "OBJ-BOWLS-WHT-01": {"zone": [0, 1]},
            "OBJ-BOWLS-BLU-01": {"zone": [2, 2]},
            "OBJ-DRAWER-01": {"zone": [0, 2]}}})
    ranked = rank_instructions(md_d, props, fake_counts)
    assert ranked, "랭킹이 비어 있음"
    assert [n for _, _, n in ranked] == sorted(n for _, _, n in ranked)
    assert ranked[0][1] not in ("pick-on",), ranked[0]   # 최다 수집 스킬이 1순위 금지
    for s, _sk, _n in ranked:
        assert lint(s, md_d, props) is None, s           # 유일 지칭 게이트
    print("8 통과: 부족 스킬 우선 랭킹 + 유일 지칭 lint")
    print("\nselftest 통과")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path,
                    default=Path.home() / "libero_datasets")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-k", type=int, default=3)
    ap.add_argument("--min-objects", type=int, default=2,
                    help="이 개수 미만 물체의 후보 제외 (기본 2 = 전 범위; "
                         "개수 균형은 count 커버리지 축이 자동으로 잡는다)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return

    props = props_by_id()
    existing = []
    for p in iter_scene_files(args.root):
        try:
            existing.append(read_scene_metadata(p))
        except Exception as e:  # noqa: BLE001 -- 잠긴 파일(수집 중)은 건너뛴다
            print(f"[경고] {p.name} 읽기 실패 ({type(e).__name__}) -- 제외")
    sid = next_scene_id(args.root)
    counts = collected_skill_counts(args.root)
    print(f"기존 scene {len(existing)}개 기준, 다음 ID {sid}")
    print(f"스킬별 누적 수집 (적은 순): {format_skill_counts(counts)}\n")
    recs = recommend_detailed(existing, props, k=args.k, seed=args.seed,
                              scene_id=sid, min_objects=args.min_objects)
    for i, rec in enumerate(recs, 1):
        md = rec["md"]
        print("=" * 56)
        print(f"추천 {i}  [{rec['bucket']} 변형]  기존과의 최소 거리 "
              f"{rec['min_dist']}")
        ax = rec["axes"]
        ax_s = "  ".join(
            f"{a}={ax[a]:.2f}" if ax.get(a) is not None else f"{a}=--"
            for a in AXES)
        print(f"  축별 최소 거리: {ax_s}")
        uni_s = "  ".join(f"{a}={v:.2f}"
                          for a, v in rec["uniformity"].items())
        print(f"  커버리지 보강 축: {rec['weak_axis']} (균등성 {uni_s})")
        print(describe_scene(md))
        print("추천 문장 (누적 수집이 적은 스킬 우선):")
        for s, sk, n in rank_instructions(md, props, counts):
            print(f"  - [{sk} · 누적 {n}] {s}")
        print("layout JSON:")
        print(json.dumps({"objects": md.objects, "layout": md.layout},
                         ensure_ascii=False))
        print()


if __name__ == "__main__":
    main()
