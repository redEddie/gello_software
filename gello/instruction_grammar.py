"""통일 instruction 문법 -- scene 에서 생성·검증.

인벤토리 category 를 사람 문장으로 매핑하고, scene 안에서 (color, category)
가 유일할 때만 지칭한다. 문법에 없는 category 는 매핑 추가가 필수다.

생성은 명사 매핑(§3)을 그대로 따르고, lint 는 기존 계획 파일의 "bowl" 처럼
legacy 약칭도 받아들여 하위호환성을 유지한다.
"""

from __future__ import annotations

import re
from typing import Optional

from gello.props import Prop
from gello.scene_format import SceneMetadata

# category -> 사람 문법의 명사구. 새 category 는 여기 추가 후 사용.
NOUN_MAP = {
    "cup": "cup",
    "small_bowl": "small bowl",
    "large_bowl": "large bowl",
    "drawer": "drawer",
}

# 문법상 "the top drawer" 도 drawer 를 지칭
_DRAWER_PHRASES = {"drawer", "top drawer"}

# pick-place 문장의 pick 대상. (템플릿 §4)
_PICKABLE = {"cup", "small_bowl"}
# place-on 대상. (템플릿 §4)
_BOWL_CATS = {"small_bowl", "large_bowl"}

# lint 용 object phrase 파싱 패턴. 앞쪽에 색/부가 수식이 올 수 있다.
_PARSE_PATTERNS = [
    # small bowl 의 두 가지 어순 + 색
    (re.compile(r"^the\s+(.+?)\s+small\s+bowl$"), "small_bowl"),
    (re.compile(r"^the\s+small\s+(.+?)\s+bowl$"), "small_bowl"),
    # large bowl
    (re.compile(r"^the\s+(.+?)\s+large\s+bowl$"), "large_bowl"),
    # legacy: "the blue bowl" -> large_bowl 로 간주
    (re.compile(r"^the\s+(.+?)\s+bowl$"), "large_bowl"),
    # cup
    (re.compile(r"^the\s+(.+?)\s+cup$"), "cup"),
]


def _parse_object_phrase(phrase: str) -> Optional[tuple[str, str]]:
    """"the blue cup" -> ("blue", "cup"), "the small green bowl" 등도 파싱.

    drawer 는 색 생략. bowl 의 legacy 약칭은 large_bowl 로 간주한다.
    """
    phrase = phrase.strip().lower()
    if not phrase.startswith("the "):
        return None
    rest = phrase[4:].strip()
    if rest in _DRAWER_PHRASES:
        return ("", "drawer")
    for pat, cat in _PARSE_PATTERNS:
        m = pat.match(phrase)
        if m:
            color = m.group(1).strip()
            if not color:
                return None
            return (color, cat)
    return None


def _is_unique(color: str, category: str, md: SceneMetadata,
               props: dict[str, Prop]) -> bool:
    """scene 안에서 (color, category) 가 정확히 하나인가."""
    matches = [
        oid for oid in md.objects
        if oid in props
        and props[oid].category == category
        and props[oid].color == color
    ]
    return len(matches) == 1


def _reference(color: str, category: str, md: SceneMetadata,
              props: dict[str, Prop]) -> Optional[str]:
    """유일하면 "the {color} {noun}" 를 반환, 아니면 None. drawer 는 색 생략."""
    if category not in NOUN_MAP:
        raise ValueError(f"instruction_grammar NOUN_MAP 에 {category!r} 가 없다")
    if category == "drawer":
        # drawer 는 단일 개체로 가정하고 색 생략
        return "the drawer"
    if not _is_unique(color, category, md, props):
        return None
    return f"the {color} {NOUN_MAP[category]}"


def enumerate_instructions(md: SceneMetadata, props: dict[str, Prop]) -> list[str]:
    """scene 에서 문법에 맞는 instruction 문장을 결정적으로 모두 생성.

    (color, category) 가 유일하지 않아 모호한 물체가 들어가는 문장은
    생성하지 않는다.
    """
    by_cat: dict[str, list[tuple[str, str]]] = {}  # category -> [(color, oid), ...]
    for oid in md.objects:
        p = props.get(oid)
        if p is None:
            continue
        by_cat.setdefault(p.category, []).append((p.color, oid))

    def refs(cats: set[str]) -> list[tuple[str, str, str]]:
        """지칭 가능한 (color, category, oid) 목록."""
        out = []
        for cat in sorted(cats):
            for color, oid in by_cat.get(cat, []):
                if _reference(color, cat, md, props) is not None:
                    out.append((color, cat, oid))
        return out

    sentences: set[str] = set()

    # 1) pick up {obj} and place it on {bowl}
    for ocolor, ocat, ooid in refs(_PICKABLE):
        for bcolor, bcat, boid in refs(_BOWL_CATS):
            if ooid == boid:
                continue
            sentences.add(
                f"pick up {_reference(ocolor, ocat, md, props)} "
                f"and place it on {_reference(bcolor, bcat, md, props)}"
            )

    # 2) pick up {obj} and place it on top of the drawer
    if "drawer" in by_cat:
        for ocolor, ocat, _ in refs(_PICKABLE):
            sentences.add(
                f"pick up {_reference(ocolor, ocat, md, props)} "
                "and place it on top of the drawer"
            )

    # 3) pick up {obj} and place it next to {obj2}
    objs = refs(_PICKABLE)
    for i, (c1, cat1, oid1) in enumerate(objs):
        for c2, cat2, oid2 in objs:
            if oid1 == oid2:
                continue
            sentences.add(
                f"pick up {_reference(c1, cat1, md, props)} "
                f"and place it next to {_reference(c2, cat2, md, props)}"
            )

    # 4) open/close the top drawer
    if "drawer" in by_cat:
        sentences.add("open the top drawer")
        sentences.add("close the top drawer")

    return sorted(sentences)


def lint(sentence: str, md: Optional[SceneMetadata] = None,
         props: Optional[dict[str, Prop]] = None) -> Optional[str]:
    """문장이 문법을 따르는지 검증.

    md 가 주어지면 scene 에서 지칭 유일성·존재까지 검사한다. 주어지지 않으면
    템플릿/어휘만 검사한다(계획 파일 하위호환용).
    """
    sentence = sentence.strip()
    if not sentence:
        return "빈 문장"

    def _scene_check(color: str, cat: str, role: str) -> Optional[str]:
        if md is None or props is None:
            return None
        if cat == "drawer":
            if not any(props.get(o, Prop("", "", "", "")).category == "drawer"
                       for o in md.objects):
                return "drawer 가 scene 에 없음"
            return None
        if not _is_unique(color, cat, md, props):
            return f"모호한 {role} 지칭: the {color} {NOUN_MAP[cat]}"
        return None

    # open / close
    if sentence in {"open the top drawer", "close the top drawer"}:
        if md is not None and props is not None:
            if not any(props.get(o, Prop("", "", "", "")).category == "drawer"
                       for o in md.objects):
                return "drawer 가 scene 에 없음"
        return None

    # pick up ... and place it on top of the drawer
    m = re.match(
        r"^pick up the (.+?) and place it on top of the (top drawer|drawer)$",
        sentence,
    )
    if m:
        obj_phrase = m.group(1)
        parsed = _parse_object_phrase(f"the {obj_phrase}")
        if parsed is None:
            return f"지칭 파싱 실패: {obj_phrase!r}"
        color, cat = parsed
        if cat not in _PICKABLE:
            return f"pick 대상 category 불가: {cat!r}"
        err = _scene_check(color, cat, "pick")
        if err:
            return err
        return None

    # pick up ... and place it on ...
    m = re.match(r"^pick up the (.+?) and place it on the (.+)$", sentence)
    if m:
        obj_phrase = m.group(1)
        target_phrase = m.group(2)
        obj = _parse_object_phrase(f"the {obj_phrase}")
        target = _parse_object_phrase(f"the {target_phrase}")
        if obj is None:
            return f"pick 대상 파싱 실패: {obj_phrase!r}"
        if target is None:
            return f"place 대상 파싱 실패: {target_phrase!r}"
        ocolor, ocat = obj
        tcolor, tcat = target
        if ocat not in _PICKABLE:
            return f"pick 대상 category 불가: {ocat!r}"
        if tcat not in _BOWL_CATS:
            return f"place-on 대상 category 불가: {tcat!r}"
        err = _scene_check(ocolor, ocat, "pick")
        if err:
            return err
        err = _scene_check(tcolor, tcat, "place")
        if err:
            return err
        # 같은 물체인지 -- category+color 가 유일하므로 (color, category) 로 비교
        if (ocolor, ocat) == (tcolor, tcat):
            return "place-on 대상이 pick 대상과 같음"
        return None

    # pick up ... and place it next to ...
    m = re.match(r"^pick up the (.+?) and place it next to the (.+)$", sentence)
    if m:
        obj_phrase = m.group(1)
        obj2_phrase = m.group(2)
        obj = _parse_object_phrase(f"the {obj_phrase}")
        obj2 = _parse_object_phrase(f"the {obj2_phrase}")
        if obj is None:
            return f"pick 대상 파싱 실패: {obj_phrase!r}"
        if obj2 is None:
            return f"next-to 대상 파싱 실패: {obj2_phrase!r}"
        c1, cat1 = obj
        c2, cat2 = obj2
        if cat1 not in _PICKABLE or cat2 not in _PICKABLE:
            return "next-to 문장은 cup/small bowl 끼리만 가능"
        err = _scene_check(c1, cat1, "pick")
        if err:
            return err
        err = _scene_check(c2, cat2, "next-to")
        if err:
            return err
        if (c1, cat1) == (c2, cat2):
            return "next-to 대상이 pick 대상과 같음"
        return None

    return "통일 문법 템플릿에 맞지 않음"


def selftest() -> None:
    """문법 생성/검증 스스로를 검증한다."""
    from gello.props import props_by_id

    props = props_by_id()

    # blue cup + white small bowl + drawer
    md1 = SceneMetadata(
        scene_id="S000",
        objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01", "OBJ-DRAWER-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-BLU-01": {"zone": [0, 0]},
                "OBJ-BOWLS-WHT-01": {"zone": [0, 1]},
                "OBJ-DRAWER-01": {"zone": [0, 2]},
            },
        },
    )
    s1 = enumerate_instructions(md1, props)
    assert "pick up the blue cup and place it on the white small bowl" in s1
    assert "pick up the blue cup and place it on top of the drawer" in s1
    assert "open the top drawer" in s1
    assert "close the top drawer" in s1
    assert lint("pick up the blue cup and place it on the white small bowl", md1, props) is None
    assert lint("pick up the blue cup and place it on the blue bowl", md1, props) is not None

    # 두 개의 흰 컵 -> 흰 컵 지칭 문장은 생성되지 않아야 함
    md2 = SceneMetadata(
        scene_id="S001",
        objects=["OBJ-CUP-WHT-01", "OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01"],
        layout={
            "grid": [3, 3],
            "placements": {
                "OBJ-CUP-WHT-01": {"zone": [0, 0]},
                "OBJ-CUP-WHT-02": {"zone": [0, 1]},
                "OBJ-BOWLS-BLU-01": {"zone": [0, 2]},
            },
        },
    )
    s2 = enumerate_instructions(md2, props)
    assert not any("white cup" in x for x in s2)
    assert lint("pick up the white cup and place it on the blue bowl", md2, props) is not None

    # legacy "bowl" 약칭 파싱
    assert lint("pick up the blue cup and place it on the white bowl") is None
    assert lint("pick up the pink small bowl and place it on the white bowl") is None
    assert lint("pick up the small green bowl and place it on the yellow bowl") is None

    # 결정성
    assert enumerate_instructions(md1, props) == enumerate_instructions(md1, props)
    print("instruction_grammar selftest 통과")


if __name__ == "__main__":
    selftest()
