"""통일 instruction 문법 — scene 에서 생성·검증.

정본 규격 (2026-08-24 사용자 확정):

    Pick-and-place:
        pick up the {OBJECT} [QUALIFIER] and place it {RELATION} the {TARGET}
        RELATION ∈ { on, inside, next to, on top of(TARGET=drawer) }
    Drag (들지 않고 끌기):
        drag the {OBJECT} [QUALIFIER] next to the {TARGET}
    Drawer:
        open the top drawer / close the top drawer
    QUALIFIER (동일 외형이 여럿일 때만, OBJECT 바로 뒤):
        farthest from the {REFERENCE} | closest to the {REFERENCE}
        | to the left of the {REFERENCE} | to the right of the {REFERENCE}

- 넣기는 **inside** 로 통일한다 — 'place it in the ...' 는 오류로 안내.
- 지칭은 "the {색} {명사}" (색 어순은 small bowl 앞뒤 모두 허용). 명사는
  NOUN_MAP 이 정본이고, 문법에 없는 category 는 매핑 추가가 필수다.
- QUALIFIER 가 붙으면 그 (색, 종류)가 scene 에 여러 개여도 된다 — 그게
  qualifier 의 존재 이유다. 없으면 유일해야 한다.

lint 는 기존 계획 파일의 "bowl" 약칭도 받아들여 하위호환을 유지한다.
2026-08-31 의 15cm 'bowl' category 신설 이후 "the {색} bowl" 은 인벤토리로
해소한다: 그 색이 bowl category 소품의 색이면 bowl, 아니면 legacy 대로
large_bowl (selftest 가 두 category 의 색 겹침을 금지해 모호성을 차단한다).
커트러리는 2026-08-31 부터 색 없는 단일 번들("the cutlery")로 지칭한다 --
색 지칭("the pink cutlery")은 옛 계획/데이터 하위호환으로 lint 만 받는다.

커트러리 전용 동사 (2026-08-31 사용자 확정):
    Tidy (여러 낱개를 반복 운반해 정리):
        tidy the cutlery into the {TARGET}   TARGET ∈ 그릇들 | wooden tray | drawer
더미(pile)를 옮기는 동작은 단일 픽앤플레이스와 운동 구조가 달라서, 같은
"pick up" 동사·스킬로 두면 학습 시 작업 구분이 안 된다. 그래서 커트러리는
pick/drag 문장에서 빠지고(tidy 만 생성) 스킬도 tidy-into 로 따로 집계된다.
기존 수집분의 "pick up the {색} cutlery ..." 는 lint 하위호환으로만 남는다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from gello.scene.props import Prop
from gello.scene.scene_format import SceneMetadata


@lru_cache(maxsize=1)
def _known_colors() -> frozenset:
    """인벤토리(configs/scenes/props.yaml)의 색 집합 -- 색 토큰 검증용.

    md 없이 부르는 계획 파일 lint 모드에서도 "the zzz qqq cup" 같은 임의
    문자열이 색으로 통과하지 않게 하고, "the small bowl" 이 색="small" 인
    large_bowl 로 오분류되는 것도 막는다 (small 은 색이 아니다)."""
    from gello.scene.props import props_by_id
    return frozenset(p.color for p in props_by_id().values())

# category -> 사람 문법의 명사구. 새 category 는 여기 추가 후 사용.
NOUN_MAP = {
    "cup": "cup",
    "small_bowl": "small bowl",
    # 크기 3단계 (2026-08-31 사용자 확정): small_bowl 12cm / bowl 15cm /
    # large_bowl 20.5cm. 이 범위 밖은 기능성 그릇으로 별도 category 를 만든다.
    "bowl": "bowl",
    "large_bowl": "large bowl",
    "drawer": "drawer",
    # 커트러리 (2026-08-31 사용자 확정): 낱개 18종을 은퇴시키고 혼색 번들
    # 하나로 등록, drawer/tray 처럼 색 없이 "the cutlery" 로 지칭한다.
    # (2026-08-27 의 색 지칭 방식은 옛 계획/데이터 하위호환으로 lint 만 받는다.)
    "cutlery": "cutlery",
    # 'wooden tray' (2026-08-27 사용자 결정): 재질 수식이 시각 grounding 에
    # 유리하고, tray 수집분이 아직 없어 일관성 부채도 없다. lint 는 짧은
    # 'the tray' 도 계속 받는다 (_TRAY_PHRASES).
    "tray": "wooden tray",
}

# 문법상 "the top drawer" 도 drawer 를 지칭
_DRAWER_PHRASES = {"drawer", "top drawer"}
# "the tray" / "the wooden tray" 모두 트레이 지칭
_TRAY_PHRASES = {"tray", "wooden tray"}
# "the cutlery" / "the plastic cutlery" 모두 커트러리 번들 지칭
_CUTLERY_PHRASES = {"cutlery", "plastic cutlery"}

# 색 없이 지칭하는 category ('the drawer', 'the tray', 'the cutlery').
# drawer/tray 는 단일 개체라서, cutlery 는 어질러진 더미 전체가 한 대상이라서.
_NO_COLOR_CATS = {"drawer", "tray", "cutlery"}

# 들어 옮길 수 있는 것 (pick up 대상). bowl(15cm)은 림 파지 가능
# (2026-08-31 실기 확인). cutlery 는 더미라 pick/drag 가 아니라 tidy 동사
# 전용 (2026-08-31 사용자 확정 -- 모듈 docstring 참고).
_PICKABLE = {"cup", "small_bowl", "bowl"}
#: 추천기(scene_diversity)가 쓰는 공개 이름 -- "씬에 최소 한 종류" 제약의 정본.
PICKABLE_CATS = _PICKABLE
# 끌 수 있는 것 (drag 대상 -- 들지 않으므로 큰 그릇도 가능)
_DRAGGABLE = {"cup", "small_bowl", "bowl", "large_bowl"}
# on / inside 목적지 (그릇)
_BOWL_CATS = {"small_bowl", "bowl", "large_bowl"}
# 'on' 목적지 = 그릇 + 트레이
_ON_CATS = _BOWL_CATS | {"tray"}
# drawer 안('inside the drawer')에 넣을 수 있는 것 -- 커트러리만. tidy 전환
# 이후 새 문장은 안 만들지만, 기존 수집분의 "pick up the {색} cutlery and
# place it inside the drawer" 를 lint 가 계속 받기 위한 하위호환 집합.
# (컵/그릇은 서랍 높이에 안 들어간다; 2026-08-27 사용자 확정 범위)
_DRAWER_INSIDE_OBJS = {"cutlery"}
# next to 목적지 (탁상 위 아무 물체). 어질러진 커트러리 더미는 위치 기준이
# 못 되므로 제외.
_BESIDE_CATS = {"cup", "small_bowl", "bowl", "large_bowl", "tray"}
# tidy 목적지 (담을 수 있는 것): 그릇들 + 트레이 + 서랍
_TIDY_TARGETS = _BOWL_CATS | {"tray", "drawer"}

# QUALIFIER 문구 (OBJECT 바로 뒤, 필요할 때만)
_QUALIFIERS = ("farthest from", "closest to", "to the left of", "to the right of")
_QUAL_RE = "|".join(re.escape(q) for q in _QUALIFIERS)

# lint 용 object phrase 파싱 패턴. 앞쪽에 색/부가 수식이 올 수 있다.
_PARSE_PATTERNS = [
    # small bowl 의 두 가지 어순 + 색
    (re.compile(r"^the\s+(.+?)\s+small\s+bowl$"), "small_bowl"),
    (re.compile(r"^the\s+small\s+(.+?)\s+bowl$"), "small_bowl"),
    # large bowl
    (re.compile(r"^the\s+(.+?)\s+large\s+bowl$"), "large_bowl"),
    # "the {색} bowl": 색으로 category 를 해소한다 -- 그 색이 bowl(15cm)
    # 소품의 색이면 bowl, 아니면 legacy 약칭대로 large_bowl. selftest 가
    # 두 category 의 색 겹침을 금지하므로 이 해소는 항상 유일하다.
    (re.compile(r"^the\s+(.+?)\s+bowl$"), "_bowl_alias"),
    # cup
    (re.compile(r"^the\s+(.+?)\s+cup$"), "cup"),
    # legacy: 색 지칭 커트러리 ("the pink cutlery") -- 옛 계획/데이터
    # 하위호환. 새 문장은 색 없이 "the cutlery" (_CUTLERY_PHRASES).
    (re.compile(r"^the\s+(.+?)\s+cutlery$"), "cutlery"),
]


def _bowl_category_colors() -> frozenset:
    """bowl(15cm) category 소품의 색 집합 -- "the {색} bowl" 해소용."""
    from gello.scene.props import props_by_id
    return frozenset(
        p.color for p in props_by_id().values() if p.category == "bowl"
    )


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
    if rest in _TRAY_PHRASES:
        return ("", "tray")
    if rest in _CUTLERY_PHRASES:
        return ("", "cutlery")
    for pat, cat in _PARSE_PATTERNS:
        m = pat.match(phrase)
        if m:
            color = m.group(1).strip()
            # 색 토큰은 인벤토리의 실제 색만 인정한다. 아니면 다음 패턴으로 --
            # "the small green bowl" 은 2번 패턴(색 green)으로, "the small bowl"
            # 은 어느 패턴에서도 유효한 색이 없어 파싱 실패가 된다.
            if not color or color not in _known_colors():
                continue
            if cat == "_bowl_alias":
                cat = "bowl" if color in _bowl_category_colors() else "large_bowl"
            return (color, cat)
    return None


def _count(color: str, category: str, md: SceneMetadata,
           props: dict[str, Prop]) -> int:
    return sum(
        1 for oid in md.objects
        if oid in props
        and props[oid].category == category
        and props[oid].color == color
    )


def _is_unique(color: str, category: str, md: SceneMetadata,
               props: dict[str, Prop]) -> bool:
    """scene 안에서 (color, category) 가 정확히 하나인가."""
    return _count(color, category, md, props) == 1


def _reference(color: str, category: str, md: SceneMetadata,
              props: dict[str, Prop]) -> Optional[str]:
    """유일하면 "the {color} {noun}" 를 반환, 아니면 None. drawer 는 색 생략."""
    if category not in NOUN_MAP:
        raise ValueError(f"instruction_grammar NOUN_MAP 에 {category!r} 가 없다")
    if category in _NO_COLOR_CATS:
        # drawer/tray 는 단일 개체, cutlery 는 더미 전체가 한 대상 -- 색 생략.
        return f"the {NOUN_MAP[category]}"
    if not _is_unique(color, category, md, props):
        return None
    return f"the {color} {NOUN_MAP[category]}"


def enumerate_instructions(md: SceneMetadata, props: dict[str, Prop]) -> list[str]:
    """scene 에서 문법에 맞는 instruction 문장을 결정적으로 모두 생성.

    (color, category) 가 유일하지 않아 모호한 물체가 들어가는 문장은
    생성하지 않는다 (QUALIFIER 문장은 생성하지 않는다 -- 그건 동일 외형
    복수 scene 을 사람이 의도적으로 만들 때 쓰는 문법이다).
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

    # 1) pick up {obj} and place it on/inside {bowl}
    for ocolor, ocat, ooid in refs(_PICKABLE):
        for bcolor, bcat, boid in refs(_BOWL_CATS):
            if ooid == boid:
                continue
            o = _reference(ocolor, ocat, md, props)
            b = _reference(bcolor, bcat, md, props)
            sentences.add(f"pick up {o} and place it on {b}")
            sentences.add(f"pick up {o} and place it inside {b}")

    # 2) pick up {obj} and place it on top of the drawer
    if "drawer" in by_cat:
        for ocolor, ocat, _ in refs(_PICKABLE):
            sentences.add(
                f"pick up {_reference(ocolor, ocat, md, props)} "
                "and place it on top of the drawer"
            )

    # 2b) (2026-08-31 폐지) "pick up {cutlery} ... inside the drawer" 는 더
    # 이상 생성하지 않는다 -- 커트러리는 tidy 전용(2d), drawer 목적지도
    # tidy 가 담당. _DRAWER_INSIDE_OBJS 는 옛 문장 lint 하위호환용으로만 남음.

    # 2c) pick up {obj} and place it on the wooden tray
    if "tray" in by_cat:
        for ocolor, ocat, _ in refs(_PICKABLE):
            sentences.add(
                f"pick up {_reference(ocolor, ocat, md, props)} "
                f"and place it on the {NOUN_MAP['tray']}"
            )

    # 2d) tidy the cutlery into {container} -- 커트러리 전용 동사. 더미를
    # 반복 운반하는 동작이라 pick/drag 와 스킬(tidy-into)부터 분리된다.
    if "cutlery" in by_cat:
        for tcolor, tcat, _ in refs(_TIDY_TARGETS):
            sentences.add(
                f"tidy the cutlery into {_reference(tcolor, tcat, md, props)}"
            )

    # 3) pick up {obj} and place it next to {obj2}
    objs = refs(_PICKABLE)
    besides = refs(_BESIDE_CATS)
    for c1, cat1, oid1 in objs:
        for c2, cat2, oid2 in besides:
            if oid1 == oid2:
                continue
            o1 = _reference(c1, cat1, md, props)
            o2 = _reference(c2, cat2, md, props)
            # 색 없는 지칭(cutlery 더미)은 낱개 소품이 여럿이어도 같은 구가
            # 된다 -- "the cutlery next to the cutlery" 방지.
            if o1 == o2:
                continue
            sentences.add(f"pick up {o1} and place it next to {o2}")

    # 4) drag {obj} next to {obj2} -- 들지 않고 끌기 (큰 그릇 포함)
    for c1, cat1, oid1 in refs(_DRAGGABLE):
        for c2, cat2, oid2 in besides:
            if oid1 == oid2:
                continue
            o1 = _reference(c1, cat1, md, props)
            o2 = _reference(c2, cat2, md, props)
            if o1 == o2:
                continue
            sentences.add(f"drag {o1} next to {o2}")

    # 5) open/close the top drawer
    if "drawer" in by_cat:
        sentences.add("open the top drawer")
        sentences.add("close the top drawer")

    return sorted(sentences)


# ---- 정본 문장 정규식 --------------------------------------------------------
# OBJECT 뒤에 선택적 QUALIFIER, 그 뒤 관계. 관계 alternation 은 긴 것 먼저
# ("on top of" 가 "on" 에 잡아먹히지 않게).
_PICK_RE = re.compile(
    r"^pick up the (?P<obj>.+?)"
    rf"(?: (?P<qual>(?:{_QUAL_RE}) the .+?))?"
    r" and place it (?P<rel>on top of|inside|next to|on) the (?P<tgt>.+)$"
)
_DRAG_RE = re.compile(
    r"^drag the (?P<obj>.+?)"
    rf"(?: (?P<qual>(?:{_QUAL_RE}) the .+?))?"
    r" next to the (?P<tgt>.+)$"
)
# 커트러리 정리 (2026-08-31): QUALIFIER 없음 -- 더미 전체가 한 대상이다.
_TIDY_RE = re.compile(r"^tidy the (?P<obj>.+?) into the (?P<tgt>.+)$")


#: 문법이 표현할 수 있는 스킬(동사×관계) 전집합 — skill_of() 의 치역.
#: scene 추천의 지시문 단계(gello.scene.skill_stats)가 "실행 가능한 스킬 중
#: 누적 수집횟수가 가장 적은 것 우선" 을 판단할 때의 단위다.
#: tidy-into 는 반복 운반(더미 정리)이라 pick 계열과 운동 구조가 달라
#: 별도 스킬로 집계한다 (2026-08-31 사용자 결정).
SKILLS = ("pick-on", "pick-inside", "pick-next_to", "pick-on_top_of",
          "drag-next_to", "tidy-into", "drawer-open", "drawer-close")


def skill_of(sentence: str) -> Optional[str]:
    """문장을 스킬(동사×관계)로 분류한다. 정본 문법이 아니면 None.

    물체 색·종류는 스킬이 아니다 — "pick up the blue cup and place it on
    the white bowl" 과 "... pink small bowl ..." 은 같은 pick-on 스킬이다.
    legacy 따옴표 감싸기는 벗겨서 판정한다 (v0 파일 대조용)."""
    s = sentence.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()
    if s == "open the top drawer":
        return "drawer-open"
    if s == "close the top drawer":
        return "drawer-close"
    m = _PICK_RE.match(s)
    if m is not None:
        return "pick-" + m.group("rel").replace(" ", "_")
    if _DRAG_RE.match(s) is not None:
        return "drag-next_to"
    if _TIDY_RE.match(s) is not None:
        return "tidy-into"
    return None


def _parse_qualifier(qual: str) -> Optional[tuple[str, str]]:
    """"farthest from the yellow bowl" -> ("farthest from", "the yellow bowl")."""
    for q in _QUALIFIERS:
        if qual.startswith(q + " "):
            return q, qual[len(q) + 1:]
    return None


def lint(sentence: str, md: Optional[SceneMetadata] = None,
         props: Optional[dict[str, Prop]] = None) -> Optional[str]:
    """문장이 정본 문법을 따르는지 검증.

    md 가 주어지면 scene 에서 지칭 유일성·존재까지 검사한다. 주어지지 않으면
    템플릿/어휘만 검사한다(계획 파일 하위호환용). QUALIFIER 가 붙은 지칭은
    유일하지 않아도 되지만 최소 1개는 존재해야 하고, REFERENCE 는 유일해야
    한다.
    """
    sentence = sentence.strip()
    if not sentence:
        return "빈 문장"
    if sentence.endswith("."):
        return "문장 끝 마침표 금지"

    def _has_cat(cat: str) -> Optional[str]:
        if md is not None and props is not None and not any(
                props.get(o, Prop("", "", "", "")).category == cat
                for o in md.objects):
            return f"{NOUN_MAP.get(cat, cat)} 가 scene 에 없음"
        return None

    def _check_ref(phrase: str, role: str, allowed: set[str],
                   qualified: bool = False) -> "tuple[str, str] | str":
        """지칭 구를 파싱·검증. 성공 시 (color, category), 실패 시 오류 문자열."""
        parsed = _parse_object_phrase(f"the {phrase}" if not phrase.startswith("the")
                                      else phrase)
        if parsed is None:
            return f"{role} 지칭 파싱 실패: {phrase!r}"
        color, cat = parsed
        if cat in _NO_COLOR_CATS:
            if cat not in allowed:
                return f"{role} 에 {cat} 불가"
            err = _has_cat(cat)
            return err if err else (color, cat)
        if cat not in allowed:
            return f"{role} category 불가: {cat!r}"
        if md is not None and props is not None:
            n = _count(color, cat, md, props)
            if n == 0:
                return f"{role} 대상이 scene 에 없음: the {color} {NOUN_MAP[cat]}"
            if n > 1 and not qualified:
                return (f"모호한 {role} 지칭: the {color} {NOUN_MAP[cat]} "
                        "(QUALIFIER 필요: farthest from / closest to / "
                        "to the left of / to the right of)")
        return (color, cat)

    # open / close
    if sentence in {"open the top drawer", "close the top drawer"}:
        return _has_cat("drawer")

    # 'in' 오사용을 콕 집어 안내 (inside 로 통일 -- 2026-08-24 결정)
    if re.search(r"\bplace it in the\b", sentence):
        return "'place it in' 대신 'place it inside' (inside 로 통일)"
    if re.match(r"^put the ", sentence):
        return ("'put the X inside the Y' 대신 정본 "
                "'pick up the X and place it inside the Y'")
    if " that is " in sentence:
        return "QUALIFIER 는 'that is' 없이 붙인다 (예: the blue cup farthest from ...)"

    # tidy: 커트러리 전용 (2026-08-31). QUALIFIER 없음.
    t = _TIDY_RE.match(sentence)
    if t is not None:
        obj = _check_ref(t.group("obj"), "tidy", {"cutlery"})
        if isinstance(obj, str):
            return obj
        tgt_parsed = _check_ref(t.group("tgt"), "tidy-into", _TIDY_TARGETS)
        if isinstance(tgt_parsed, str):
            return tgt_parsed
        return None

    m = _PICK_RE.match(sentence)
    d = _DRAG_RE.match(sentence) if m is None else None
    if m is None and d is None:
        return "통일 문법 템플릿에 맞지 않음"

    # cutlery 는 tidy 전용이지만 기존 수집분/계획의 색 지칭 문장("the pink
    # cutlery")은 lint 하위호환으로 계속 받는다 -- 색 없는 새 지칭("the
    # cutlery")이 pick/drag 에 오면 아래에서 tidy 로 안내한다.
    if m is not None:
        obj_phrase, qual, rel, tgt = (m.group("obj"), m.group("qual"),
                                      m.group("rel"), m.group("tgt"))
        obj_allowed = _PICKABLE | {"cutlery"}
        tgt_allowed = {"on": _ON_CATS, "inside": _BOWL_CATS | {"drawer"},
                       "next to": _BESIDE_CATS | {"cutlery"},
                       "on top of": {"drawer"}}[rel]
        verb_role = ("pick", f"place-{rel}")
    else:
        obj_phrase, qual, tgt = d.group("obj"), d.group("qual"), d.group("tgt")
        rel = "next to"
        obj_allowed = _DRAGGABLE | {"cutlery"}
        tgt_allowed = _BESIDE_CATS | {"cutlery"}
        verb_role = ("drag", "next-to")

    obj = _check_ref(obj_phrase, verb_role[0], obj_allowed, qualified=bool(qual))
    if isinstance(obj, str):
        return obj
    if obj == ("", "cutlery"):
        return ("커트러리 더미는 pick/drag 가 아니라 "
                "'tidy the cutlery into the ...' 를 쓴다 (2026-08-31)")
    if qual:
        parsed_q = _parse_qualifier(qual)
        if parsed_q is None:
            return f"QUALIFIER 파싱 실패: {qual!r}"
        ref = _check_ref(parsed_q[1][4:], "reference",
                         set(NOUN_MAP) | {"drawer"})
        if isinstance(ref, str):
            return ref
    tgt_parsed = _check_ref(tgt, verb_role[1], tgt_allowed)
    if isinstance(tgt_parsed, str):
        return tgt_parsed
    if tgt_parsed == ("", "cutlery"):
        return "어질러진 커트러리 더미는 위치 기준(next to)으로 못 쓴다"
    if rel == "inside" and tgt_parsed[1] == "drawer" \
            and obj[1] not in _DRAWER_INSIDE_OBJS:
        return ("drawer 안에는 커트러리만 넣는다 -- "
                f"{NOUN_MAP[obj[1]]} 는 'on top of the drawer' 를 쓰세요")
    if obj == tgt_parsed:
        return f"{verb_role[1]} 대상이 {verb_role[0]} 대상과 같음"
    return None


def selftest() -> None:
    """문법 생성/검증 스스로를 검증한다."""
    from gello.scene.props import props_by_id

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
    assert "pick up the blue cup and place it inside the white small bowl" in s1
    assert "pick up the blue cup and place it on top of the drawer" in s1
    assert "drag the blue cup next to the white small bowl" in s1
    assert "open the top drawer" in s1
    assert lint("pick up the blue cup and place it on the white small bowl", md1, props) is None
    assert lint("pick up the blue cup and place it inside the white small bowl", md1, props) is None
    assert lint("drag the blue cup next to the white small bowl", md1, props) is None
    assert lint("pick up the blue cup and place it on the blue bowl", md1, props) is not None

    # 정본 위반 안내 -- put / in / that is / 마침표
    assert "inside" in lint("pick up the blue cup and place it in the white bowl")
    assert "정본" in lint("put the blue cup inside the white bowl")
    assert "that is" in lint(
        "pick up the blue cup that is farthest from the yellow bowl and place it on the yellow bowl")
    assert "마침표" in lint("open the top drawer.")

    # QUALIFIER: 템플릿만 검사(md 없이)
    assert lint("pick up the blue cup farthest from the yellow bowl "
                "and place it on the yellow bowl") is None
    assert lint("drag the blue cup closest to the white bowl next to the white cup") is None
    assert lint("pick up the blue cup nearest the bowl and place it on the bowl") is not None

    # 두 개의 흰 컵 -> qualifier 없으면 모호, 있으면 허용
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
    err = lint("pick up the white cup and place it on the blue small bowl", md2, props)
    assert err is not None and "QUALIFIER" in err, err
    assert lint("pick up the white cup farthest from the blue small bowl "
                "and place it on the blue small bowl", md2, props) is None
    # drag: 큰 그릇도 끌 수 있다
    assert lint("drag the white bowl next to the blue cup") is None

    # legacy "bowl" 약칭 파싱
    assert lint("pick up the blue cup and place it on the white bowl") is None
    assert lint("pick up the pink small bowl and place it on the white bowl") is None
    assert lint("pick up the small green bowl and place it on the yellow bowl") is None

    # 색 토큰 검증 -- 인벤토리에 없는 색/색 아닌 수식어는 파싱 실패
    assert lint("pick up the zzz qqq cup and place it on the wwww bowl") is not None
    assert _parse_object_phrase("the small bowl") is None      # small 은 색이 아님
    assert _parse_object_phrase("the large bowl") is None
    assert _parse_object_phrase("the small green bowl") == ("green", "small_bowl")

    # 목적지 drawer 존재 검사 (md 제공 시)
    err = lint("pick up the blue small bowl and place it on top of the drawer",
               md2, props)
    assert err is not None and "drawer" in err, err

    # 커트러리 번들 (2026-08-31): 색 없이 "the cutlery" 로 지칭
    md4 = SceneMetadata(
        scene_id="S004",
        objects=["OBJ-CUTLERY-SET-01", "OBJ-TRAY-01", "OBJ-DRAWER-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUTLERY-SET-01": {"zone": [0, 0]},
            "OBJ-TRAY-01": {"zone": [0, 1]},
            "OBJ-DRAWER-01": {"zone": [0, 2]}}})
    s4 = enumerate_instructions(md4, props)
    # tidy 전용 동사 (2026-08-31): 더미 정리는 pick 과 스킬부터 분리
    assert "tidy the cutlery into the wooden tray" in s4
    assert "tidy the cutlery into the drawer" in s4
    assert not any(x.startswith("pick up the cutlery") for x in s4)
    assert not any("drag the cutlery" in x for x in s4)
    assert not any("mixed" in x for x in s4)   # 인벤토리 색(mixed)은 문장에 안 나온다
    assert lint("tidy the cutlery into the wooden tray", md4, props) is None
    # "the plastic cutlery" / 짧은 "the tray" 지칭도 lint 는 허용
    assert lint("tidy the plastic cutlery into the tray", md4, props) is None
    assert lint("tidy the cutlery into the drawer", md4, props) is None
    # 색 없는 커트러리를 pick 에 쓰면 tidy 로 안내
    err = lint("pick up the cutlery and place it on the wooden tray", md4, props)
    assert err is not None and "tidy" in err, err
    # 커트러리 없는 씬에서 tidy -> 오류
    err = lint("tidy the cutlery into the wooden tray", md1, props)
    assert err is not None
    # tidy 목적지는 담을 수 있는 것만 (컵 불가)
    err = lint("tidy the cutlery into the blue cup", md4, props)
    assert err is not None
    # 컵은 drawer 안에 못 넣는다 (2026-08-27 확정 범위: 커트러리만)
    err = lint("pick up the blue cup and place it inside the drawer", md1, props)
    assert err is not None and "커트러리" in err, err
    # 은퇴한 낱개 커트러리가 든 옛 scene: 색 지칭 pick 문장은 lint 만
    # 허용(하위호환), 새로 생성되는 문장은 tidy 뿐이다.
    md5 = SceneMetadata(
        scene_id="S005",
        objects=["OBJ-SPOON-PNK-01", "OBJ-FORK-PNK-01", "OBJ-TRAY-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-SPOON-PNK-01": {"zone": [0, 0]},
            "OBJ-FORK-PNK-01": {"zone": [1, 0]},
            "OBJ-TRAY-01": {"zone": [0, 1]}}})
    s5 = enumerate_instructions(md5, props)
    assert not any("pink cutlery" in x for x in s5)
    assert "tidy the cutlery into the wooden tray" in s5
    assert not any(x.startswith("pick up the cutlery") for x in s5)
    assert lint("pick up the pink cutlery and place it on the wooden tray",
                md5, props) is None   # legacy 색 지칭
    assert lint("drag the pink cutlery next to the wooden tray",
                md5, props) is None   # legacy drag 도 하위호환
    # tray 없는 씬에서 tray 목적지 -> 오류
    err = lint("pick up the blue cup and place it on the tray", md1, props)
    assert err is not None and "tray" in err, err
    # tidy 는 별도 스킬로 분류된다
    assert skill_of("tidy the cutlery into the wooden tray") == "tidy-into"
    assert skill_of("tidy the cutlery into the blue japanese bowl") == "tidy-into"
    assert all(skill_of(s) in SKILLS for s in s4)

    # bowl(15cm) category (2026-08-31): 색 토큰이 여러 단어여도 동작하고,
    # "the {색} bowl" 은 인벤토리로 bowl/large_bowl 을 해소한다.
    assert not (_bowl_category_colors()
                & {p.color for p in props.values()
                   if p.category == "large_bowl" and not p.retired}), \
        "bowl 과 large_bowl 의 색이 겹치면 'the {색} bowl' 해소가 모호해진다"
    assert _parse_object_phrase("the blue japanese bowl") == ("blue japanese", "bowl")
    assert _parse_object_phrase("the pink striped bowl") == ("pink striped", "bowl")
    assert _parse_object_phrase("the white bowl") == ("white", "large_bowl")  # legacy
    md6 = SceneMetadata(
        scene_id="S006",
        objects=["OBJ-BOWLM-JPN-01", "OBJ-CUP-RED-01", "OBJ-BOWLL-WHT-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-BOWLM-JPN-01": {"zone": [0, 0]},
            "OBJ-CUP-RED-01": {"zone": [0, 1]},
            "OBJ-BOWLL-WHT-01": {"zone": [0, 2]}}})
    s6 = enumerate_instructions(md6, props)
    assert "pick up the red cup and place it inside the blue japanese bowl" in s6
    # bowl 은 pickable (림 파지 -- 2026-08-31 실기 확인)
    assert "pick up the blue japanese bowl and place it on the white large bowl" in s6
    assert lint("pick up the red cup and place it inside the blue japanese bowl",
                md6, props) is None
    assert lint("drag the blue japanese bowl next to the white large bowl",
                md6, props) is None
    assert skill_of("pick up the red cup and place it inside the blue japanese bowl") == "pick-inside"

    # 결정성
    assert enumerate_instructions(md1, props) == enumerate_instructions(md1, props)

    # 스킬 분류 -- 색·종류가 달라도 같은 스킬, 정본 아니면 None
    assert skill_of("pick up the blue cup and place it on the white bowl") == "pick-on"
    assert skill_of("pick up the pink small bowl and place it on the white bowl") == "pick-on"
    assert skill_of("pick up the blue cup and place it inside the white small bowl") == "pick-inside"
    assert skill_of("pick up the blue cup and place it on top of the drawer") == "pick-on_top_of"
    assert skill_of("pick up the blue cup and place it next to the white bowl") == "pick-next_to"
    assert skill_of("drag the blue cup next to the white small bowl") == "drag-next_to"
    assert skill_of("open the top drawer") == "drawer-open"
    assert skill_of('"close the top drawer"') == "drawer-close"   # legacy 따옴표
    assert skill_of("put the cup somewhere") is None
    assert all(skill_of(s) in SKILLS for s in s1)   # 생성 문장은 전부 분류 가능
    print("instruction_grammar selftest 통과")


if __name__ == "__main__":
    selftest()
