"""배치 CP-SAT 솔버 -- scene_rules.yaml 의 placement 규칙을 제약으로 컴파일한다.

물체 집합은 **주어진 것**으로 보고 "어느 칸에 놓을까"만 푼다. 물체 구성
규칙(compose: pair_if_present, object_count ...)은 여기 들어오지 않는다 --
그건 "무엇을 고를까"의 규칙이고, 이 모델에서는 이미 정해져 있다.

    변수      x[물체, 칸] 불리언
    구조      물체마다 정확히 한 칸 / 칸마다 최대 한 물체
    규칙      configs/scenes/scene_rules.yaml 의 placement 섹션

규칙 정본은 여전히 그 yaml 하나이고, 소비자가 둘이 됐다 --
:func:`gello.scene.scene_rules.check` (사후 검증)와 이 컴파일러(사전 생성).
**둘이 어긋나면 "규칙에 맞는 배치를 추천했다"는 말이 거짓이 된다.** 그래서
구현되지 않은 placement rule 은 조용히 넘어가지 않고 예외이며,
``scripts/analyze/recommend_scene.py --selftest`` 가 두 엔진의 동등성을
양방향으로(솔버 해는 전부 check 통과 / check 통과 배치는 전부 feasible)
가능한 배치 전체에서 검사한다.

ortools 는 함수 안에서 지연 import 한다 -- 거리만 계산하는 경로(감사도구,
축 분해)는 이 무거운 의존을 건드리지 않는다.
"""

from __future__ import annotations

import random
from typing import Optional

from gello.scene.props import Prop
from gello.scene.scene_rules import _default_rules, rule_names
from gello.scene.signature import GRID

#: 결정성: 같은 seed 는 같은 배치여야 한다 (scene provenance). 다중 워커는
#: 탐색 순서가 비결정적이고, 해 열거는 애초에 단일 워커를 요구한다.
_WORKERS = 1


def cells() -> list:
    return [(r, c) for r in range(GRID[0]) for c in range(GRID[1])]


def _tall(objects: list, props: dict) -> list:
    return [o for o in objects if props[o].tall]


def _blocked_rows(ref: int) -> set:
    """기준 행 ``ref`` 에서 볼 때 (앞 물체 행, 뒤 물체 행) 로 막히는 쌍.

    ref 에서 **더 먼** 행이 막힌다 -- 카메라 기준이면 가려지고, 로봇
    기준이면 팔이 넘어가야 한다. 두 규칙이 같은 모양이라 함수 하나다.
    """
    return {(ra, rb) for ra in range(GRID[0]) for rb in range(GRID[0])
            if abs(rb - ref) > abs(ra - ref)}


def _add_ban_zones(model, x, objects, props, entry) -> None:
    cat = entry.get("category")
    banned = [tuple(z) for z in entry.get("zones", [])]
    for o in objects:
        if props[o].category != cat:
            continue
        for z in banned:
            if z in x[o]:
                model.Add(x[o][z] == 0)


def _add_column_rule(model, x, objects, props, entry, field) -> None:
    ref = entry.get(field)
    if ref is None:
        raise ValueError(f"rule {entry.get('rule')!r} 에 {field!r} 가 없다")
    pairs = _blocked_rows(int(ref))
    for a in _tall(objects, props):
        for b in objects:
            if b == a:
                continue
            for (ra, rb) in pairs:
                for c in range(GRID[1]):
                    model.Add(x[a][(ra, c)] + x[b][(rb, c)] <= 1)


#: placement rule 이름 -> 제약 추가 함수. check() 가 아는 규칙인데 여기
#: 없으면 컴파일이 예외다 -- 두 소비자가 말없이 어긋나는 것을 막는 지점.
_COMPILERS = {
    "ban_zones": _add_ban_zones,
    "occludes_behind":
        lambda m, x, o, p, e: _add_column_rule(m, x, o, p, e, "camera_row"),
    "robot_clearance":
        lambda m, x, o, p, e: _add_column_rule(m, x, o, p, e, "robot_row"),
}


def compile_placement_model(objects: list, props: "dict[str, Prop]",
                            rules_data: Optional[dict] = None):
    """(model, x) 를 만든다. x[물체][칸] 이 불리언 변수.

    미지원 placement rule 이름은 예외 -- 조용히 무시하면 규칙이 걸리지 않은
    배치를 "규칙에 맞다"고 추천하게 된다.
    """
    from ortools.sat.python import cp_model

    data = rules_data if rules_data is not None else _default_rules()
    if len(objects) > GRID[0] * GRID[1]:
        raise ValueError(
            f"물체 {len(objects)}개는 격자 {GRID[0]}x{GRID[1]} 칸보다 많다")
    for o in objects:
        if o not in props:
            raise ValueError(f"인벤토리에 없는 물체: {o}")

    model = cp_model.CpModel()
    grid = cells()
    x = {o: {z: model.NewBoolVar(f"x[{o},{z[0]}{z[1]}]") for z in grid}
         for o in objects}
    for o in objects:                       # 물체마다 정확히 한 칸
        model.AddExactlyOne(list(x[o].values()))
    for z in grid:                          # 칸마다 최대 한 물체
        model.AddAtMostOne([x[o][z] for o in objects])

    for entry in data.get("placement", []) or []:
        rule = entry.get("rule")
        fn = _COMPILERS.get(rule)
        if fn is None:
            raise ValueError(
                f"placement rule {rule!r} 의 CP-SAT 컴파일러가 없다 -- "
                "규칙을 구현하거나 yaml 에서 빼세요 (조용한 무시 금지)")
        fn(model, x, objects, props, entry)
    return model, x


def _assignment(solver, x, objects: list) -> dict:
    return {o: next(z for z, v in x[o].items() if solver.Value(v))
            for o in objects}


def solve_placement(objects: list, props: "dict[str, Prop]",
                    seed: int = 0,
                    rules_data: Optional[dict] = None) -> "dict | None":
    """실행 가능한 배치 하나 {물체: (행, 열)}. 불가능하면 None.

    목적함수는 seed 로 만든 난수 계수의 합이다. 목적함수가 없으면 CP-SAT 은
    사전순 첫 해를 주므로(9칸 중 3칸 → 0,1,2번) 배치가 한쪽으로 쏠린다 --
    난수 목적은 그것을 흩는다. 커버리지 목적함수는 다음 단계에서 이 자리에
    들어간다 (recommender-v3-plan.md D2).
    """
    from ortools.sat.python import cp_model

    model, x = compile_placement_model(objects, props, rules_data)
    rng = random.Random(seed)
    model.Maximize(sum(rng.randint(0, 10_000) * x[o][z]
                       for o in objects for z in cells()))
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = _WORKERS
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return _assignment(solver, x, objects)


def enumerate_placements(objects: list, props: "dict[str, Prop]",
                         rules_data: Optional[dict] = None,
                         limit: int = 200_000) -> list:
    """실행 가능한 배치를 **전부** 센다 [{물체: (행,열)}, ...].

    물체 5개면 상한이 9P5 = 15,120 이고 규칙이 걸리면 더 줄어든다 -- 무작위
    표본이 아니라 가능한 배치 전체 위에서 고를 수 있다는 뜻이고, 규칙 엔진
    동등성 검사도 표본이 아니라 전수로 돌릴 수 있다.

    limit 은 폭주 방지용 안전장치다 (초과하면 예외 -- 조용히 잘라내면
    "전수"라는 말이 거짓이 된다).
    """
    from ortools.sat.python import cp_model

    model, x = compile_placement_model(objects, props, rules_data)

    class _Collect(cp_model.CpSolverSolutionCallback):
        def __init__(self) -> None:
            super().__init__()
            self.found: list = []

        def on_solution_callback(self) -> None:
            if len(self.found) >= limit:
                self.StopSearch()
                return
            self.found.append(
                {o: next(z for z, v in x[o].items() if self.Value(v))
                 for o in objects})

    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = _WORKERS
    cb = _Collect()
    solver.Solve(model, cb)
    if len(cb.found) >= limit:
        raise ValueError(
            f"실행 가능한 배치가 limit({limit})을 넘었다 -- 잘라내지 않는다")
    return cb.found
