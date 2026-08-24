"""다음 scene 추천 CLI — 기존 scene 들과 가장 먼 소품 조합·배치 3안.

사용:
    python scripts/recommend_scene.py [--root ~/libero_datasets] [--seed 0] [-k 3]
    python scripts/recommend_scene.py --selftest

각 추천안은 describe_scene 격자 지도와, GUI '새 Scene 구성' 없이 바로
쓸 수 있는 layout JSON 으로 출력된다. 로봇·카메라 불필요.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.props import props_by_id  # noqa: E402
from gello.instruction_grammar import enumerate_instructions  # noqa: E402
from gello.scene_rules import check  # noqa: E402
from gello.scene_diversity import recommend, scene_distance, signature  # noqa: E402
from gello.scene_format import (  # noqa: E402
    SceneMetadata,
    describe_scene,
    iter_scene_files,
    next_scene_id,
    read_scene_metadata,
)


def _selftest() -> None:
    import random

    from gello.props import active_prop_ids
    from gello.scene_diversity import generate_candidate

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
        assert "cup" in cats and len(cats) >= 2
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
    from gello.instruction_grammar import enumerate_instructions
    sents_a = enumerate_instructions(base, props)
    sents_b = enumerate_instructions(base, props)
    assert sents_a == sents_b
    assert all("white cup" not in x for x in enumerate_instructions(
        SceneMetadata(
            scene_id="S001",
            objects=["OBJ-CUP-WHT-01", "OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01"],
            layout={"grid": [3, 3], "placements": {
                "OBJ-CUP-WHT-01": {"zone": [0, 0]},
                "OBJ-CUP-WHT-02": {"zone": [0, 1]},
                "OBJ-BOWLS-BLU-01": {"zone": [0, 2]}}}),
        props))
    print("5 통과: 문법 결정성 + 모호 지칭 제외")
    print("\nselftest 통과")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path,
                    default=Path.home() / "libero_datasets")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-k", type=int, default=3)
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
    print(f"기존 scene {len(existing)}개 기준, 다음 ID {sid}\n")
    recs = recommend(existing, props, k=args.k, seed=args.seed, scene_id=sid)
    for i, (md, dist) in enumerate(recs, 1):
        print("=" * 56)
        print(f"추천 {i}  (기존과의 최소 거리 {dist})")
        print(describe_scene(md))
        print("추천 문장:")
        for s in enumerate_instructions(md, props):
            print(f"  - {s}")
        print("layout JSON:")
        print(json.dumps({"objects": md.objects, "layout": md.layout},
                         ensure_ascii=False))
        print()


if __name__ == "__main__":
    main()
