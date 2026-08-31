"""scene 구성 규칙 로더/검사기 -- configs/scenes/scene_rules.yaml 이 정본이다.

사용처:
  1) 추천 후보 필터(rejection)
  2) NewSceneDialog 검증(사람이 만든 배치도 같은 규칙으로 lint, 경고만)
  3) scripts/check/check_scene_file.py 선택 검사
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from gello.scene.props import Prop
from gello.scene.scene_format import SceneMetadata

RULES_PATH = Path(__file__).resolve().parents[2] / "configs" / "scenes" / "scene_rules.yaml"


_KNOWN_RULES = {"no_lookalike_pair", "color_diverse", "ban_zones",
                "pair_if_present", "exclusive_column"}


def _validate_rules(data: dict, path: "str | Path" = RULES_PATH) -> None:
    """data 의 rule 이름을 검증한다. 알 수 없는 이름이면 ValueError."""
    if not isinstance(data, dict):
        raise ValueError(f"{path}: 규칙 파일은 dict 여야 한다")
    for section in ("compose", "placement"):
        for entry in data.get(section, []) or []:
            rule = entry.get("rule")
            if rule not in _KNOWN_RULES:
                raise ValueError(
                    f"{path}: 알 수 없는 rule 이름 {rule!r} -- "
                    f"구현 후 known 집합에 추가하거나 yaml 을 고치세요"
                )


def load_rules(path: Path = RULES_PATH) -> dict:
    """scene_rules.yaml 을 읽고, 알 수 없는 rule 이름은 오류를 낸다.

    조용한 무시 금지 -- 새 rule 을 추가하려면 이 모듈의 check() 에도
    구현하고 yaml 에 이름을 적어야 한다.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    _validate_rules(data, path)
    return data


@lru_cache(maxsize=1)
def _default_rules() -> dict:
    """기본 경로 규칙의 1회 캐시. check() 가 호출마다 yaml 을 다시 파싱하면
    추천 후보 생성(후보당 1회 호출)과 NewSceneDialog 의 격자 클릭 lint 가
    대부분 파싱 비용이 된다 (실측 200배). 규칙 yaml 을 고치면 프로세스
    재시작(또는 _default_rules.cache_clear()) 이 필요하다 -- 규칙은 git 으로
    관리되는 정본이라 런타임 변경을 전제하지 않는다."""
    return load_rules()


def _prop_for(oid: str, props: dict[str, Prop]) -> Optional[Prop]:
    return props.get(oid)


def _object_triples(md: SceneMetadata, props: dict[str, Prop]) -> list[tuple[str, str, str]]:
    """scene 의 물체를 (id, category, color) 로 변환. 인벤토리에 없으면 제외."""
    out = []
    for oid in md.objects:
        p = _prop_for(oid, props)
        if p is None:
            continue
        out.append((oid, p.category, p.color))
    return out


def check(md: SceneMetadata, props: dict[str, Prop],
          rules_data: Optional[dict] = None) -> list[str]:
    """scene metadata 가 규칙을 얼마나 위반했는지 반환.

    반환값이 비어 있으면 통과. props 는 gello.scene.props.props_by_id() 결과.
    """
    data = rules_data if rules_data is not None else _default_rules()
    if rules_data is not None:
        # 기본 경로는 load_rules() 가 이미 검증했다 -- 주입분만 재검증.
        _validate_rules(data, "<injected rules>")
    violations: list[str] = []
    triples = _object_triples(md, props)

    for entry in data.get("compose", []) or []:
        rule = entry.get("rule")
        if rule == "no_lookalike_pair":
            counter = Counter((cat, color) for _, cat, color in triples)
            for (cat, color), n in sorted(counter.items()):
                if n >= 2:
                    violations.append(
                        f"no_lookalike_pair: ({cat}, {color}) 가 {n}개"
                    )
        elif rule == "color_diverse":
            cat = entry.get("category")
            colors = [color for _, c, color in triples if c == cat]
            if len(colors) >= 2 and len(set(colors)) < len(colors):
                dup = sorted({c for c in colors if colors.count(c) > 1})
                violations.append(
                    f"color_diverse: {cat} 에 중복 색 {dup}"
                )
        elif rule == "pair_if_present":
            cats = entry.get("categories", [])
            need = int(entry.get("min_count", 2))
            counter = Counter(cat for _, cat, _ in triples)
            for c in cats:
                n = counter.get(c, 0)
                if 0 < n < need:
                    violations.append(
                        f"pair_if_present: {c} 가 {n}개 -- 등장하려면 "
                        f"{need}개 이상 (한 개면 색 없이 지칭 가능해져 "
                        "shortcut 학습 위험)"
                    )

    for entry in data.get("placement", []) or []:
        rule = entry.get("rule")
        if rule == "ban_zones":
            cat = entry.get("category")
            banned = [tuple(z) for z in entry.get("zones", [])]
            placements = md.layout.get("placements", {})
            for oid, c, _ in triples:
                if c != cat:
                    continue
                zone = tuple(placements.get(oid, {}).get("zone", []))
                if zone in banned:
                    violations.append(
                        f"ban_zones: {cat} ({oid}) 가 금지 존 {list(zone)} 에 있음"
                    )
        elif rule == "exclusive_column":
            # 가림 방지 (2026-08-26): 해당 category(키 큰 서랍)가 있는 열에는
            # 다른 물체를 두지 않는다 -- agentview 에서 같은 열의 물체가
            # 서랍에 가려진다. "뒤쪽만" 이 아니라 열 전체를 비우는 이유:
            # 존 좌표는 카메라 프레임이라 어느 row 가 카메라 쪽인지가
            # 규칙 파일에선 보이지 않고, 열 전체 배제가 항상 안전하다.
            cat = entry.get("category")
            placements = md.layout.get("placements", {})

            def _zone_of(oid: str) -> "tuple | None":
                z = placements.get(oid, {}).get("zone", [])
                return tuple(z) if len(z) == 2 else None

            for a_oid, c, _ in triples:
                if c != cat:
                    continue
                az = _zone_of(a_oid)
                if az is None:
                    continue
                for oid, oc, _ in triples:
                    if oid == a_oid or oc == cat:
                        continue
                    z = _zone_of(oid)
                    if z is not None and z[1] == az[1]:
                        violations.append(
                            f"exclusive_column: {cat} ({a_oid}) 의 열 "
                            f"{az[1]} 에 {oid} 가 있음 (존 {list(z)} -- "
                            "가림 위험)"
                        )

    return violations


def selftest() -> None:
    """로더/검사기 스스로를 검증한다."""
    from gello.scene.props import props_by_id

    props = props_by_id()
    rules = load_rules()

    # 통과 케이스 (pair_if_present: 등장 category 는 2색 이상 -- 2026-08-24;
    # exclusive_column: 서랍 열(2)은 비움 -- 2026-08-26)
    ok_md = SceneMetadata(
        scene_id="S000",
        objects=["OBJ-CUP-BLU-01", "OBJ-CUP-WHT-01",
                 "OBJ-BOWLS-WHT-01", "OBJ-BOWLS-BLU-01", "OBJ-DRAWER-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-BLU-01": {"zone": [0, 0]},
                "OBJ-CUP-WHT-01": {"zone": [1, 0]},
                "OBJ-BOWLS-WHT-01": {"zone": [0, 1]},
                "OBJ-BOWLS-BLU-01": {"zone": [2, 0]},
                "OBJ-DRAWER-01": {"zone": [0, 2]},
            },
        },
    )
    assert check(ok_md, props, rules) == [], check(ok_md, props, rules)

    # lookalike 페어
    look_md = SceneMetadata(
        scene_id="S001",
        objects=["OBJ-CUP-WHT-01", "OBJ-CUP-WHT-02", "OBJ-BOWLS-WHT-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-WHT-01": {"zone": [0, 0]},
                "OBJ-CUP-WHT-02": {"zone": [0, 1]},
                "OBJ-BOWLS-WHT-01": {"zone": [0, 2]},
            },
        },
    )
    v = check(look_md, props, rules)
    assert any("no_lookalike_pair" in x for x in v)

    # drawer 중앙 존
    drawer_md = SceneMetadata(
        scene_id="S002",
        objects=["OBJ-CUP-BLU-01", "OBJ-DRAWER-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-BLU-01": {"zone": [0, 0]},
                "OBJ-DRAWER-01": {"zone": [1, 1]},
            },
        },
    )
    v = check(drawer_md, props, rules)
    assert any("ban_zones" in x for x in v)

    # 앞줄 중앙 [2,1] 도 금지 (2026-08-26)
    front_md = SceneMetadata(
        scene_id="S005",
        objects=["OBJ-CUP-BLU-01", "OBJ-DRAWER-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-BLU-01": {"zone": [0, 0]},
                "OBJ-DRAWER-01": {"zone": [2, 1]},
            },
        },
    )
    assert any("ban_zones" in x for x in check(front_md, props, rules))

    # exclusive_column: 서랍 열에 다른 물체 -> 위반, 다른 열이면 통과
    occl_md = SceneMetadata(
        scene_id="S003",
        objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01", "OBJ-DRAWER-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-BLU-01": {"zone": [2, 2]},     # 서랍(열 2) 앞
                "OBJ-BOWLS-WHT-01": {"zone": [0, 1]},
                "OBJ-DRAWER-01": {"zone": [0, 2]},
            },
        },
    )
    v = check(occl_md, props, rules)
    assert any("exclusive_column" in x and "OBJ-CUP-BLU-01" in x for x in v), v
    clear_md = SceneMetadata(
        scene_id="S004",
        objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01", "OBJ-DRAWER-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-BLU-01": {"zone": [2, 0]},
                "OBJ-BOWLS-WHT-01": {"zone": [0, 1]},
                "OBJ-DRAWER-01": {"zone": [0, 2]},
            },
        },
    )
    assert not any("exclusive_column" in x
                   for x in check(clear_md, props, rules))

    # 알 수 없는 rule 이름은 로드 시 예외
    bad = {"version": 1, "compose": [{"rule": "no_such_rule"}]}
    try:
        check(ok_md, props, bad)
    except ValueError as e:
        assert "no_such_rule" in str(e)
    else:
        raise AssertionError("알 수 없는 rule 은 예외가 나와야 한다")

    print("scene_rules selftest 통과")


if __name__ == "__main__":
    selftest()
