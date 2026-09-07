"""관계-모양 규칙 -- 목적지가 그릇이면 언제나 inside, on 은 평평한 것에만.

2026-09-07 사용자 확정: 그릇(small_bowl, bowl, large_bowl)은 오목한 안쪽
공간으로 물체가 들어가는 물리적 관계가 강해서(수집 사진 확인 -- 같은 크기
그릇 두 개도 포개져서 위 그릇이 아래 그릇 안으로 완전히 들어간다) 목적지가
그릇이면 언제나 inside 다. on 은 평평한 면(tray; 나중에 plate 도)에만 쓴다.

이 규칙이 생기기 전에는 _ON_CATS 가 그릇을 포함해 enumerate 가 그릇->그릇
쌍마다 on/inside 문장을 둘 다 만들었고, 물리적으로 같은 동작이라
SKILLS 의 pick-on / pick-inside 가 구분되지 않았다 (S016 재현으로 검증).

여기서 검증하는 것:
  1. 목적지가 그릇인 pick 문장은 enumerate 에서 전부 inside (on 이 없다)
     -- _PICKABLE x _BOWL_CATS 조합을 두루 덮는 scene 들로.
  2. S016 재현: 동일 15cm 그릇 2개 scene 에서 pick-on 이 없고
     pick-inside 가 양방향 2개.
  3. lint(strict 기본값) 가 그릇 목적지 on 문장을 거부하고 inside 를 안내.
  4. lint(strict_relation=False) 는 옛 겹침 집합 문장을 통과시킨다
     (기존 수집분/계획 파일 읽기 자리가 깨지지 않게 하는 하위호환).
  5. 회귀 방지: tray 목적지 on, drawer 목적지 on top of 는 여전히 합법.

소스만 읽는다. 로봇도 화면도 필요 없다.
"""
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from gello.scene.instruction_grammar import (  # noqa: E402
    enumerate_instructions, lint, skill_of,
)
from gello.scene.props import props_by_id  # noqa: E402
from gello.scene.scene_format import SceneMetadata  # noqa: E402

props = props_by_id()


def _md(scene_id: str, objects: list[str]) -> SceneMetadata:
    return SceneMetadata(
        scene_id=scene_id,
        objects=objects,
        layout={"grid": [3, 3],
                "placements": {oid: {"zone": [i % 3, i // 3]}
                               for i, oid in enumerate(objects)}},
    )


def _assert_inside_only(sents: list[str], scene_desc: str) -> None:
    """scene 의 pick 문장 중 그릇 목적지 on 이 하나도 없고 inside 만 있는가."""
    on_bowl = [s for s in sents if "place it on the" in s and "bowl" in s]
    assert not on_bowl, f"{scene_desc}: 그릇 목적지 on 문장이 나왔다 -- {on_bowl}"
    assert any("place it inside the" in s for s in sents), \
        f"{scene_desc}: inside 문장이 하나도 없다"


# ---- 1. 목적지가 그릇이면 전부 inside (_PICKABLE x _BOWL_CATS 조합 커버) ----
# 컵 -> 작은 그릇
s_a = enumerate_instructions(
    _md("RS-A", ["OBJ-CUP-WHT-01", "OBJ-BOWLS-WHT-01"]), props)
_assert_inside_only(s_a, "컵->작은그릇")
assert "pick up the white cup and place it inside the small white bowl" in s_a
# 컵 -> 15cm 그릇
s_b = enumerate_instructions(
    _md("RS-B", ["OBJ-CUP-RED-01", "OBJ-BOWLM-JPN-01"]), props)
_assert_inside_only(s_b, "컵->15cm그릇")
assert ("pick up the red cup and place it inside the blue japanese bowl"
        in s_b)
# 작은 그릇 -> 큰 그릇
s_c = enumerate_instructions(
    _md("RS-C", ["OBJ-BOWLS-BLU-01", "OBJ-BOWLL-BLU-01"]), props)
_assert_inside_only(s_c, "작은그릇->큰그릇")
assert ("pick up the small blue bowl and place it inside the large blue bowl"
        in s_c)
# 큰 것 -> 작은 것 방향 (15cm 그릇 -> 작은 그릇; large_bowl 은 pickable 이
# 아니라서 _PICKABLE 안에서 가장 큰 쪽 방향이다)
s_e = enumerate_instructions(
    _md("RS-E", ["OBJ-BOWLM-JPN-01", "OBJ-BOWLS-WHT-01"]), props)
_assert_inside_only(s_e, "15cm그릇->작은그릇")
assert ("pick up the blue japanese bowl and place it inside "
        "the small white bowl" in s_e)
print("1 통과: 그릇 목적지 pick 문장은 전부 inside, on 은 없다 "
      "(_PICKABLE x _BOWL_CATS 조합)")

# ---- 2. S016 재현: 같은 크기 그릇 2개 -- pick-on 없음, pick-inside 양방향 2개 ----
md_s016 = _md("S016", ["OBJ-BOWLM-JPN-01", "OBJ-BOWLM-PNKSTR-01"])
s_s016 = enumerate_instructions(md_s016, props)
inside = [s for s in s_s016 if skill_of(s) == "pick-inside"]
on = [s for s in s_s016 if skill_of(s) == "pick-on"]
assert len(inside) == 2, inside
assert set(inside) == {
    "pick up the blue japanese bowl and place it inside the pink striped bowl",
    "pick up the pink striped bowl and place it inside the blue japanese bowl",
}, inside
assert not on, f"S016 에 pick-on 이 나왔다 -- {on}"
print("2 통과: S016 재현 -- pick-on 0개, pick-inside 양방향 2개")

# ---- 3. lint(strict) 가 그릇 목적지 on 을 거부 + inside 안내 ----
err = lint("pick up the white cup and place it on the large white bowl")
assert err is not None, "strict lint 가 그릇 목적지 on 을 통과시켰다"
assert "inside" in err, err
assert "오목한" in err, err
# scene 을 주면 마찬가지 (목적지 지칭 유일성 통과 뒤 관계 검증이 걸린다)
err = lint("pick up the white cup and place it on the large white bowl",
           _md("RS-F", ["OBJ-CUP-WHT-01", "OBJ-BOWLL-WHT-01"]), props)
assert err is not None and "inside" in err, err
print(f"3 통과: strict lint 거부 + inside 안내 -- {err!r}")

# ---- 4. 하위호환: strict_relation=False 는 옛 겹침 집합 문장을 통과 ----
assert lint("pick up the white cup and place it on the large white bowl",
            strict_relation=False) is None
assert lint("pick up the white cup and place it on the large white bowl",
            _md("RS-F", ["OBJ-CUP-WHT-01", "OBJ-BOWLL-WHT-01"]), props,
            strict_relation=False) is None
print("4 통과: strict_relation=False 하위호환 통과")

# ---- 5. 회귀 방지: tray on / drawer on top of / inside 그릇 은 여전히 합법 ----
assert lint("pick up the blue cup and place it on the wooden tray") is None
assert lint("pick up the blue cup and place it on the tray") is None
assert lint("pick up the blue cup and place it on top of the drawer") is None
assert lint("pick up the blue cup and place it inside the large white bowl") is None
# 커트러리 drawer inside 하위호환도 그대로
assert lint("pick up the pink cutlery and place it inside the drawer") is None
print("5 통과: tray on / drawer on top of / inside 그릇 / 커트러리 하위호환 합법")

# ---- 6. enumerate 에서 생성된 문장은 strict lint 를 전부 통과 (자기일관성) ----
for s in s_s016:
    assert lint(s, md_s016, props) is None, (s, lint(s, md_s016, props))
print("6 통과: 생성 문장 자기일관성 (strict)")

print("\n관계-모양 규칙 (그릇->inside, 평평한 것->on) 인수 테스트 통과")
