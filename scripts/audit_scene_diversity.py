"""scene 다양성 감사 — 추천 알고리즘과 독립인 데이터셋 편향 지표.

거리 기반 추천을 아무리 고쳐도 "다양해졌는가/상관이 없는가"는 별도
지표로 감시해야 한다 (2026-08-26 추천 개편의 함정 3). 이 도구는 데이터
루트의 scene HDF5 들만 읽어(쓰기 없음) 세 가지를 보고한다:

  1. 축별 히스토그램 균등성 — category/color/position(존)/skill 이 실제로
     고르게 쓰였는가 (정규화 엔트로피, 1.0 = 완전 균등).
  2. 쌍별 최소거리 분포 — scene 마다 가장 가까운 이웃과의 합산·축별
     거리. 0 에 붙은 쌍(클러스터)과 전부 극단(모서리 쏠림) 둘 다 표시.
  3. scene→skill 상관 — leave-one-out 1-NN: 어떤 scene 의 에피소드 스킬을
     "가장 비슷한 다른 scene 의 최빈 스킬"로 맞힐 수 있으면 씬 유형이
     태스크를 예측한다는 뜻이다. 다수결 베이스라인보다 뚜렷이 높으면 경고.

사용:
    python scripts/audit_scene_diversity.py [--root ~/libero_datasets]
    python scripts/audit_scene_diversity.py --selftest
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.instruction_grammar import skill_of  # noqa: E402
from gello.props import props_by_id  # noqa: E402
from gello.scene_diversity import (  # noqa: E402
    AXES,
    axis_coverage,
    axis_distances,
    axis_support,
    coverage_uniformity,
    scene_distance,
    signature,
)
from gello.scene_format import (  # noqa: E402
    iter_scene_files,
    list_scene_episodes,
    read_scene_metadata,
)

#: 최근접 이웃 거리가 이보다 작으면 "사실상 같은 씬 무리"로 표시한다.
CLUSTER_THRESHOLD = 0.15


def _bar(v: float, width: int = 20) -> str:
    n = round(max(0.0, min(1.0, v)) * width)
    return "#" * n + "." * (width - n)


def audit_histograms(sigs: list, skills: Counter, props: dict) -> dict:
    """축별 히스토그램 + 균등성. 반환값은 selftest 검증용."""
    hist = axis_coverage(sigs)
    support = axis_support(props)
    uni = coverage_uniformity(hist, support)
    print("== 1. 축별 히스토그램 균등성 (1.0 = 완전 균등) ==")
    for ax in ("category", "color", "position"):
        print(f"  {ax:<9} 균등성 {uni[ax]:.3f} [{_bar(uni[ax])}]")
        for b in sorted(support[ax], key=str):
            print(f"    {str(b):<14} {hist[ax].get(b, 0)}")
    if skills:
        sk_support = set(skills)
        sk_uni = 0.0
        total = sum(skills.values())
        if len(sk_support) > 1 and total:
            import math
            sk_uni = -sum((n / total) * math.log(n / total)
                          for n in skills.values() if n) / math.log(len(sk_support))
        uni["skill"] = sk_uni
        print(f"  {'skill':<9} 균등성 {sk_uni:.3f} [{_bar(sk_uni)}] "
              "(수집된 스킬 기준)")
        for sk, n in sorted(skills.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {sk:<20} {n}")
    weak = min(("category", "color", "position"), key=lambda ax: uni[ax])
    print(f"  → 가장 약한 축: {weak} (추천기가 다음 픽에서 보강하는 축)")
    return uni


def audit_pairwise(ids: list, sigs: list) -> list:
    """scene 별 최근접 이웃 거리(합산+축별). 반환: [(id, nn_id, dist)]."""
    print("\n== 2. 쌍별 최소거리 분포 (scene 별 최근접 이웃) ==")
    if len(sigs) < 2:
        print("  scene 이 2개 미만 — 생략")
        return []
    rows = []
    for i, si in enumerate(sigs):
        dists = [(scene_distance(si, sj), j)
                 for j, sj in enumerate(sigs) if j != i]
        d, j = min(dists)
        ax = axis_distances(si, sigs[j])
        rows.append((ids[i], ids[j], d))
        ax_s = " ".join(
            f"{a[:3]}={ax[a]:.2f}" if ax[a] is not None else f"{a[:3]}=--"
            for a in AXES)
        flag = "  ← 클러스터" if d < CLUSTER_THRESHOLD else ""
        print(f"  {ids[i]} ↔ {ids[j]}  합산 {d:.3f}  ({ax_s}){flag}")
    ds = sorted(r[2] for r in rows)
    mid = ds[len(ds) // 2]
    print(f"  최소 {ds[0]:.3f} / 중앙값 {mid:.3f} / 최대 {ds[-1]:.3f}")
    n_cluster = sum(1 for d in ds if d < CLUSTER_THRESHOLD)
    n_extreme = sum(1 for d in ds if d > 0.7)
    if n_cluster:
        print(f"  [경고] 최근접 거리 < {CLUSTER_THRESHOLD} 인 scene "
              f"{n_cluster}개 — 사실상 중복 무리")
    if n_extreme == len(ds) and len(ds) >= 4:
        print("  [경고] 전부 극단 거리(>0.7) — 모서리 쏠림 의심 "
              "(중간 거리 변형이 없다)")
    return rows


def audit_scene_task(ids: list, sigs: list, per_scene_skills: dict) -> dict:
    """LOO 1-NN 으로 scene→skill 예측 정확도를 잰다. 반환은 selftest 용.

    per_scene_skills: {scene_id: Counter(skill -> n에피소드)}.
    """
    print("\n== 3. scene→task 상관 (leave-one-out 1-NN) ==")
    usable = [(i, ids[i]) for i in range(len(ids))
              if per_scene_skills.get(ids[i])]
    if len(usable) < 2:
        print("  에피소드가 있는 scene 이 2개 미만 — 생략")
        return {}
    total_eps = Counter()
    for sid in per_scene_skills:
        total_eps.update(per_scene_skills[sid])
    n_total = sum(total_eps.values())
    majority_skill, majority_n = total_eps.most_common(1)[0]
    majority_acc = majority_n / n_total
    chance = 1.0 / len(total_eps)

    hit = n = 0
    for i, sid in usable:
        # 이웃: 에피소드가 있는 '다른' scene 중 가장 가까운 것
        cands = [(scene_distance(sigs[i], sigs[j]), jd)
                 for j, jd in usable if jd != sid]
        if not cands:
            continue
        _, nn_sid = min(cands)
        pred = per_scene_skills[nn_sid].most_common(1)[0][0]
        for sk, cnt in per_scene_skills[sid].items():
            hit += cnt if sk == pred else 0
            n += cnt
    acc = hit / n if n else 0.0
    print(f"  1-NN 정확도       {acc:.3f}  (에피소드 {n}개)")
    print(f"  다수결 베이스라인 {majority_acc:.3f}  (항상 {majority_skill!r})")
    print(f"  우연 수준         {chance:.3f}  (스킬 {len(total_eps)}종)")
    if acc > majority_acc + 0.10:
        print("  [경고] 씬 생김새로 태스크가 예측된다 — scene→task 상관 존재. "
              "부족 스킬 우선 배정(skill_stats)이 흐트러뜨리고 있는지 확인 필요")
    else:
        print("  → 다수결 이하: 씬 유형이 태스크를 특별히 더 예측하지 않음")
    return {"acc": acc, "majority": majority_acc, "chance": chance, "n": n}


def _selftest() -> None:
    from gello.scene_format import SceneMetadata

    props = props_by_id()

    def md(sid, objs, zones):
        return SceneMetadata(
            scene_id=sid, objects=objs,
            layout={"grid": [3, 3], "placements": {
                o: {"zone": list(z)} for o, z in zip(objs, zones)}})

    a = md("S000", ["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"], [(0, 0), (1, 1)])
    b = md("S001", ["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"], [(0, 1), (1, 1)])  # a 와 거의 동일
    c = md("S002", ["OBJ-CUP-WHT-01", "OBJ-CUP-BLU-01", "OBJ-BOWLL-WHT-01"],
           [(2, 2), (2, 0), (0, 2)])
    ids = [m.scene_id for m in (a, b, c)]
    sigs = [signature(m, props) for m in (a, b, c)]

    # 1. 히스토그램: 균등성 [0,1], 약한 축 식별
    uni = audit_histograms(sigs, Counter({"pick-on": 3, "drawer-open": 1}), props)
    assert all(0.0 <= v <= 1.0 for v in uni.values()), uni

    # 2. 쌍별: a-b 는 클러스터로 잡힌다
    rows = audit_pairwise(ids, sigs)
    d_ab = next(d for i_, j_, d in rows if {i_, j_} == {"S000", "S001"})
    assert d_ab < CLUSTER_THRESHOLD, d_ab

    # 3. 상관: 비슷한 씬 무리(a,b / c,d)가 서로 다른 스킬만 가지면 1-NN 이
    #    다수결을 크게 웃돌아 경고가 발동해야 한다.
    d_ = md("S003", ["OBJ-CUP-WHT-01", "OBJ-CUP-BLU-01", "OBJ-BOWLL-WHT-01"],
            [(2, 1), (2, 0), (0, 2)])   # c 와 거의 동일한 배치
    ids4 = ids + ["S003"]
    sigs4 = sigs + [signature(d_, props)]
    per = {"S000": Counter({"pick-on": 10}), "S001": Counter({"pick-on": 10}),
           "S002": Counter({"drawer-open": 10}),
           "S003": Counter({"drawer-open": 10})}
    r = audit_scene_task(ids4, sigs4, per)
    assert r["n"] == 40
    assert r["acc"] > r["majority"] + 0.10, r   # 상관 경고 구간
    # 스킬이 씬과 무관하게 섞이면 정확도는 다수결 수준으로 내려간다
    per2 = {sid: Counter({"pick-on": 5, "drawer-open": 5}) for sid in ids4}
    r2 = audit_scene_task(ids4, sigs4, per2)
    assert r2["acc"] <= r2["majority"] + 1e-9, r2
    print("\naudit selftest 통과")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path,
                    default=Path.home() / "libero_datasets")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return

    props = props_by_id()
    ids: list = []
    sigs: list = []
    per_scene_skills: dict = {}
    skills = Counter()
    for p in iter_scene_files(args.root):
        try:
            md = read_scene_metadata(p)
        except Exception as e:  # noqa: BLE001 -- 잠긴 파일(수집 중) 등
            print(f"[경고] {p.name} 읽기 실패 ({type(e).__name__}) -- 제외")
            continue
        ids.append(md.scene_id)
        sigs.append(signature(md, props))
        sk = Counter()
        try:
            for e in list_scene_episodes(p):
                sk[skill_of(str(e.get("instruction", ""))) or "?"] += 1
        except Exception:  # noqa: BLE001
            pass
        per_scene_skills[md.scene_id] = sk
        skills.update(sk)
    if not ids:
        print(f"{args.root} 에 scene 파일이 없다")
        return
    print(f"scene {len(ids)}개, 에피소드 {sum(skills.values())}개 기준\n")
    audit_histograms(sigs, skills, props)
    audit_pairwise(ids, sigs)
    audit_scene_task(ids, sigs, per_scene_skills)


if __name__ == "__main__":
    main()
