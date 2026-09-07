"""찍은 뒤에 바로잡기 -- scene 기록과 지시문의 불일치를 찾고 고친다.

수집이 끝난 파일을 여는 일은 두 가지다. **기록이 틀린 것**(책상 위에는 회색
그릇이 있었는데 metadata 에 초록이라고 적혔다)과 **문장이 틀린 것**(나중에
정한 어휘 규칙에 옛 문장이 안 맞는다). 값이 전혀 다르므로 함수를 나눈다.

    기록 정정  apply_object_fix   metadata attr 두 개. 에피소드 안 건드림.
    문장 정정  rewrite_task_text  에피소드 attrs + 계획 파일. edit_count 증가.

**edit_count 를 왜 문장 쪽에만 올리는가.** 변환기는 이어붙이기에서
``episode_uid`` 로 "이미 올린 것"을 걸러낸다. 문장을 고쳐도 uid 는 그대로라,
그냥 두면 resume 이 그 에피소드들을 건너뛰고 Hub 에는 **옛 문장이 남는다** --
검증도 통과하고 개수도 맞아서 아무도 못 챈다. edit_count 가 오르면 변환기가
사이드카 기준값과 다른 것을 보고 resume 을 거부하므로(전체 재빌드만 허용),
그 조용한 어긋남이 구조적으로 막힌다. 기록 정정은 uid 도 문장도 그대로라
올릴 이유가 없다 -- 올리면 멀쩡한 이어붙이기만 막는다.

용어: 여기서 말하는 단위는 **task**(= 지시문)다. 계획 파일의 JSON 키는 아직
``"slots"`` 지만 그것은 하위호환이고, 사람에게 보이는 자리에는 쓰지 않는다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import h5py

from gello.data.edit_marker import mark_scene_edited
from gello.scene.instruction_grammar import lint
from gello.scene.props import Prop, active_prop_ids, props_by_id
from gello.scene.scene_format import (
    iter_scene_files,
    read_scene_metadata,
)


@dataclass(frozen=True)
class Violation:
    """한 task 의 문장이 규칙을 어긴 사실. 세는 단위는 에피소드다."""

    scene_id: str
    instruction_id: str
    instruction: str
    message: str
    episodes: int


@dataclass(frozen=True)
class ObjectFix:
    """기록 정정 제안. 적용은 사람이 사진을 보고 확인한 뒤에 한다."""

    scene_id: str
    old_id: str
    new_id: str
    reason: str


def _episode_tasks(f: h5py.File) -> "dict[str, tuple[str, int]]":
    """instruction_id -> (문장, 에피소드 수). 한 task 안에서 문장이 갈리면
    가장 많은 쪽을 대표로 삼는다 -- 갈렸다는 사실 자체는 audit 이 잡는다."""
    per: dict[str, Counter] = {}
    for name in f:
        if not name.startswith("episode"):
            continue
        a = f[name].attrs
        iid = str(a.get("instruction_id", "?"))
        per.setdefault(iid, Counter())[str(a.get("instruction", ""))] += 1
    return {iid: (c.most_common(1)[0][0], sum(c.values()))
            for iid, c in per.items()}


def audit_scene(path: Path, props: "dict[str, Prop] | None" = None, *,
                strict_relation: bool = True) -> list[Violation]:
    """이 scene 의 task 중 규칙을 어기는 것들. 에피소드 하나씩이 아니라
    **task 단위**로 돌려준다 -- 같은 문장 10개는 하나의 결정이지 열 개가
    아니다."""
    props = props_by_id() if props is None else props
    md = read_scene_metadata(path)
    out: list[Violation] = []
    with h5py.File(path, "r") as f:
        for iid, (text, n) in sorted(_episode_tasks(f).items()):
            msg = lint(text, md, props, strict_relation=strict_relation)
            if msg:
                out.append(Violation(md.scene_id, iid, text, msg, n))
    return out


def audit_dataset(root: Path, props: "dict[str, Prop] | None" = None, *,
                  strict_relation: bool = True) -> "dict[str, list[Violation]]":
    """데이터셋의 모든 scene. 위반이 없는 scene 도 빈 목록으로 넣는다 --
    화면이 "검사했고 깨끗하다"와 "안 봤다"를 구별할 수 있어야 한다."""
    props = props_by_id() if props is None else props
    return {p.stem: audit_scene(p, props, strict_relation=strict_relation)
            for p in iter_scene_files(root)}


def _words(text: str) -> set:
    return set(re.findall(r"[a-z]+", text.lower()))


def suggest_object_fix(path: Path, props: "dict[str, Prop] | None" = None,
                       ) -> Optional[ObjectFix]:
    """metadata 의 물체 하나가 오등록된 것으로 보이면 그 정정을 제안한다.

    성립 조건은 좁다. 이 scene 의 문장들이 말하는 색 중 **딱 하나**가
    metadata 에 없고, metadata 의 색 중 **딱 하나**가 문장에 안 나오고, 그 둘의
    category 가 같아야 한다. 그러면 후자가 전자로 잘못 적힌 것이다 -- S008 이
    이 모양이었다 (문장 120개가 만장일치로 gray, metadata 만 green).

    조건이 안 맞으면 None 이다. 어느 쪽이 정본인지 파일이 말해 주지 않는
    경우까지 기계가 고르면, 맞는 문장을 틀린 metadata 에 맞추는 사고가 난다.
    """
    props = props_by_id() if props is None else props
    md = read_scene_metadata(path)
    with h5py.File(path, "r") as f:
        said = set()
        for text, _n in _episode_tasks(f).values():
            said |= _words(text)

    inventory = {p.color for p in props.values() if " " not in p.color}
    said_colors = said & inventory
    mine = {oid: props[oid].color for oid in md.objects if oid in props}
    missing = said_colors - set(mine.values())
    unused = set(mine.values()) - said_colors
    if len(missing) != 1 or len(unused) != 1:
        return None

    want_color = missing.pop()
    dead_color = unused.pop()
    old_id = next(o for o, c in mine.items() if c == dead_color)
    cat = props[old_id].category
    cands = [p for p in props.values()
             if p.color == want_color and p.category == cat
             and p.id in active_prop_ids()]
    if len(cands) != 1:
        return None
    return ObjectFix(
        md.scene_id, old_id, cands[0].id,
        f"문장은 모두 {want_color} 를 말하는데 기록은 "
        f"{dead_color} 다 ({cat})")


def apply_object_fix(path: Path, old_id: str, new_id: str) -> None:
    """metadata 의 ``objects`` / ``layout`` 에서 물체 ID 를 갈아 끼운다.

    에피소드는 열지 않는다. uid 도 문장도 그대로이므로 edit_count 는 올리지
    않는다 (모듈 docstring 참고). 새 구성이 규칙을 통과하는지 먼저 본다.
    """
    md = read_scene_metadata(path)
    if old_id not in md.objects:
        raise ValueError(f"{md.scene_id} 에 {old_id} 가 없다")
    if new_id in md.objects:
        raise ValueError(f"{md.scene_id} 에 {new_id} 가 이미 있다")
    md.objects = [new_id if o == old_id else o for o in md.objects]
    pl = md.layout.get("placements", {})
    if old_id in pl:
        pl[new_id] = pl.pop(old_id)
    md.validate(known_prop_ids=active_prop_ids())

    with h5py.File(path, "r+") as f:
        meta = f["metadata"]
        meta.attrs["objects"] = json.dumps(md.objects)
        meta.attrs["layout"] = json.dumps(md.layout)


def rewrite_task_text(scene_path: Path, instruction_id: str, new_text: str, *,
                      plan_path: "Path | None" = None,
                      props: "dict[str, Prop] | None" = None) -> int:
    """한 task 의 문장을 바꾼다 -- 그 task 의 **모든 에피소드가 함께** 바뀐다.

    에피소드 하나만 고치는 길은 두지 않는다 (2026-09-07 사용자 확정). 같은
    task 안에서 문장이 갈리는 것은 기능이 아니라 그 자체가 결함이다.

    새 문장은 이 scene 기준으로 규칙을 통과해야 한다 -- 통과 못 하면 아무것도
    쓰지 않고 ValueError 다. 고치다가 더 나쁜 문장을 넣는 것을 막는다.

    돌려주는 값은 바뀐 에피소드 수. 계획 파일을 주면 같은 문장으로 맞춘다.
    """
    props = props_by_id() if props is None else props
    new_text = new_text.strip()
    md = read_scene_metadata(scene_path)
    msg = lint(new_text, md, props)
    if msg:
        raise ValueError(f"새 문장이 규칙에 어긋난다 -- {msg}")

    with h5py.File(scene_path, "r+") as f:
        names = [n for n in f
                 if n.startswith("episode")
                 and str(f[n].attrs.get("instruction_id", "")) == instruction_id]
        if not names:
            raise ValueError(f"{md.scene_id} 에 {instruction_id} 에피소드가 없다")
        for n in names:
            f[n].attrs["instruction"] = new_text
        # 문장이 바뀌면 Hub 의 task 문자열과 어긋난다 -- 변환기가 이어붙이기를
        # 거부하고 전체 재빌드를 요구하게 만든다 (모듈 docstring 참고).
        mark_scene_edited(f["metadata"])

    if plan_path is not None:
        _rewrite_plan_text(Path(plan_path), md.scene_id, instruction_id, new_text)
    return len(names)


def _rewrite_plan_text(plan_path: Path, scene_id: str, instruction_id: str,
                       new_text: str) -> None:
    """계획 파일의 같은 task 문장을 맞춘다. 다른 필드는 건드리지 않는다."""
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    for sc in raw.get("scenes", []):
        if sc.get("scene_id") != scene_id:
            continue
        for slot in sc.get("slots", []):
            if slot.get("instruction_id") == instruction_id:
                slot["instruction"] = new_text
                plan_path.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                return
    raise ValueError(f"계획에 {scene_id} {instruction_id} 가 없다")


def _plan_slots(plan_path: Path, scene_id: str) -> "tuple[dict, list]":
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    for sc in raw.get("scenes", []):
        if sc.get("scene_id") == scene_id:
            return raw, sc.get("slots", [])
    raise ValueError(f"계획에 {scene_id} 가 없다")


def _save_plan(plan_path: Path, raw: dict) -> None:
    plan_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")


def episode_counts(scene_path: Path) -> "dict[str, int]":
    """instruction_id -> 에피소드 수. 계획에만 있고 안 찍은 task 는 안 나온다."""
    with h5py.File(scene_path, "r") as f:
        return {iid: n for iid, (_t, n) in _episode_tasks(f).items()}


def swap_task_texts(scene_path: Path, iid_a: str, iid_b: str, *,
                    plan_path: Path, props: "dict[str, Prop] | None" = None,
                    ) -> "tuple[int, int]":
    """두 task 의 문장을 맞바꾼다 -- 라벨이 서로 바뀌어 기록된 것을 되돌린다.

    재작성 두 번으로 하면 안 된다. 중간에 같은 문장이 두 task 에 겹치는
    순간이 생기고, 그 사이에 무엇이 끼어들면 두 task 가 같은 말을 하는 파일이
    남는다. 한 번의 연산이어야 한다.

    **계획에만 있고 안 찍은 task 와도 바꿀 수 있다.** S016 이 그 경우였다:
    'on' 으로 찍힌 10개와, 같은 동작을 옳게 적어 둔 빈 칸을 맞바꾸면 찍은
    것은 맞는 문장을 갖고 틀린 문장은 빈 칸으로 옮겨 간다 -- 에피소드를
    하나도 버리지 않는다.

    문법 검사는 하지 않는다. 교환은 "이 둘의 라벨이 뒤바뀌었다" 는 사실을
    바로잡는 것이고, 문장이 규칙에 맞는가는 그것과 직교한다 (틀린 문장을
    옮기는 것이 바로 이 연산의 쓸모다).

    돌려주는 값은 (a 에서 바뀐 에피소드 수, b 에서 바뀐 수).
    """
    if iid_a == iid_b:
        raise ValueError("같은 task 끼리는 바꿀 수 없다")
    md = read_scene_metadata(scene_path)
    raw, slots = _plan_slots(Path(plan_path), md.scene_id)
    by_id = {s.get("instruction_id"): s for s in slots}
    for iid in (iid_a, iid_b):
        if iid not in by_id:
            raise ValueError(f"계획에 {md.scene_id} {iid} 가 없다")
    text_a = str(by_id[iid_a].get("instruction", ""))
    text_b = str(by_id[iid_b].get("instruction", ""))

    with h5py.File(scene_path, "r+") as f:
        moved = {iid_a: 0, iid_b: 0}
        for n in f:
            if not n.startswith("episode"):
                continue
            iid = str(f[n].attrs.get("instruction_id", ""))
            if iid == iid_a:
                f[n].attrs["instruction"] = text_b
                moved[iid_a] += 1
            elif iid == iid_b:
                f[n].attrs["instruction"] = text_a
                moved[iid_b] += 1
        if moved[iid_a] or moved[iid_b]:
            mark_scene_edited(f["metadata"])

    by_id[iid_a]["instruction"] = text_b
    by_id[iid_b]["instruction"] = text_a
    _save_plan(Path(plan_path), raw)
    return moved[iid_a], moved[iid_b]


def remove_task_from_plan(plan_path: Path, scene_id: str, instruction_id: str,
                          *, scene_path: "Path | None" = None) -> None:
    """계획에서 task 하나를 뺀다 -- "더는 이 문장으로 찍지 않는다".

    **에피소드가 있는 task 는 뺄 수 없다.** 빼면 파일 안의 에피소드가 계획에
    없는 지시문을 가리키게 되고, 그 어긋남은 검증도 개수도 통과해서 조용하다.
    찍은 것을 버리려면 그것은 에피소드 삭제(Dataset 의 삭제)지 계획 편집이
    아니다 -- 값이 다른 두 조작을 한 버튼에 숨기지 않는다.
    """
    if scene_path is not None and Path(scene_path).is_file():
        n = episode_counts(Path(scene_path)).get(instruction_id, 0)
        if n:
            raise ValueError(
                f"{scene_id} {instruction_id} 에는 에피소드가 {n}개 있다 -- "
                "계획에서만 뺄 수 없다 (찍은 것을 버리려면 Dataset 에서 "
                "에피소드를 지운다)")
    raw, slots = _plan_slots(Path(plan_path), scene_id)
    keep = [s for s in slots if s.get("instruction_id") != instruction_id]
    if len(keep) == len(slots):
        raise ValueError(f"계획에 {scene_id} {instruction_id} 가 없다")
    for sc in raw.get("scenes", []):
        if sc.get("scene_id") == scene_id:
            sc["slots"] = keep
    _save_plan(Path(plan_path), raw)


def plan_task_texts(plan_path: Path, scene_id: str) -> "dict[str, str]":
    """계획에 적힌 instruction_id -> 문장. 검증하지 않는다.

    ``collection_plan.load_plan`` 을 쓰지 않는 이유: 그것은 계획 전체를
    검증하므로 문장 하나가 규칙에 안 맞으면 통째로 실패한다. 닥터가 다루는
    계획은 **정확히 그런 상태**다 -- 고치러 온 것을 못 읽으면 쓸모가 없다.
    교환 연산(swap_task_texts)도 같은 원본 읽기를 쓰므로, 화면이 보여 주는
    후보와 실제로 바꿀 수 있는 것이 어긋나지 않는다.
    """
    _raw, slots = _plan_slots(Path(plan_path), scene_id)
    return {str(s.get("instruction_id")): str(s.get("instruction", ""))
            for s in slots if s.get("instruction_id")}
