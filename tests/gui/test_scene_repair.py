"""찍은 뒤 바로잡기(gello/scene/scene_repair.py) 검증.

세 가지를 본다:
  1. audit 이 task 단위로 세는가 (에피소드 10개짜리 문장 하나 = 1건)
  2. 기록 정정이 metadata 만 바꾸고 edit_count 를 **안** 올리는가
  3. 문장 정정이 그 task 의 모든 에피소드 + 계획을 함께 바꾸고
     edit_count 를 **올리는가** (Hub 재빌드 강제 -- 조용한 어긋남 방지)

3번이 이 파일에서 가장 중요하다. 변환기가 uid 로 "이미 올림"을 거르므로,
문장만 고치고 마커를 안 올리면 Hub 에 옛 문장이 남고 아무도 못 챈다.
"""
import json
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gello.scene.props import props_by_id                          # noqa: E402
from gello.scene.scene_repair import (                             # noqa: E402
    apply_object_fix,
    audit_scene,
    rewrite_task_text,
    suggest_object_fix,
)

TASKS = [
    ("I000", "pick up the white cup and place it inside the small blue bowl", 10),
    ("I001", "pick up the white cup and place it inside the small gray bowl", 10),
]
#: 문장은 blue/gray 를 말하는데 기록에는 green 이 들어간, S008 과 같은 모양.
OBJECTS = ["OBJ-CUP-WHT-02", "OBJ-BOWLS-BLU-01", "OBJ-BOWLS-GRN-01"]
ZONES = {"OBJ-CUP-WHT-02": [0, 0], "OBJ-BOWLS-BLU-01": [0, 1],
         "OBJ-BOWLS-GRN-01": [2, 0]}


def _make(root: Path) -> tuple:
    path = root / "scene_008.hdf5"
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["scene_id"] = "S008"
        meta.attrs["objects"] = json.dumps(OBJECTS)
        meta.attrs["layout"] = json.dumps(
            {"grid": [3, 3],
             "placements": {o: {"zone": z} for o, z in ZONES.items()}})
        meta.attrs["description"] = ""
        meta.attrs["station"] = "knu-eng7"
        meta.attrs["dataset_version"] = "knu-1.0.0"
        meta.attrs["created"] = "2026-08-22T16:35:57+09:00"
        i = 0
        for iid, text, n in TASKS:
            for _ in range(n):
                g = f.create_group(f"episode_{i:03d}")
                g.attrs["scene_id"] = "S008"
                g.attrs["instruction_id"] = iid
                g.attrs["instruction"] = text
                g.attrs["episode_uid"] = f"S008-{iid}-E{i:03d}"
                g.create_dataset("actions", data=np.zeros((2, 7), np.float32))
                i += 1
    plan = root / "instructions.json"
    plan.write_text(json.dumps({
        "plan_version": 1,
        "scenes": [{"scene_id": "S008",
                    "slots": [{"instruction_id": iid, "instruction": t,
                               "target": n} for iid, t, n in TASKS]}],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, plan


def _edit_count(path: Path) -> int:
    with h5py.File(path, "r") as f:
        return int(f["metadata"].attrs.get("edit_count", 0))


def main() -> None:
    props = props_by_id()
    with tempfile.TemporaryDirectory() as d:
        path, plan = _make(Path(d))

        # 1) task 단위로 센다 -- gray 를 말하는 문장 하나가 1건, 10건 아니다.
        v = audit_scene(path, props)
        assert len(v) == 1, [x.message for x in v]
        assert v[0].instruction_id == "I001" and v[0].episodes == 10, v[0]
        assert "gray" in v[0].message, v[0].message

        # 2) 제안이 나오고, 적용하면 위반이 사라진다.
        fix = suggest_object_fix(path, props)
        assert fix is not None and fix.old_id == "OBJ-BOWLS-GRN-01", fix
        assert fix.new_id == "OBJ-BOWLS-GRY-01", fix
        apply_object_fix(path, fix.old_id, fix.new_id)
        assert audit_scene(path, props) == [], audit_scene(path, props)
        with h5py.File(path, "r") as f:
            lay = json.loads(f["metadata"].attrs["layout"])
        # 존은 따라와야 한다 -- 물체는 그대로 그 자리에 있었다.
        assert lay["placements"]["OBJ-BOWLS-GRY-01"]["zone"] == [2, 0], lay
        # 기록 정정은 uid 도 문장도 안 건드리므로 이어붙이기를 막지 않는다.
        assert _edit_count(path) == 0, "기록 정정이 edit_count 를 올렸다"

        # 고칠 것이 없으면 제안하지 않는다 (거짓 양성 금지).
        assert suggest_object_fix(path, props) is None

        # 3) 문장 정정: 그 task 전체 + 계획이 함께, edit_count 는 오른다.
        new = "drag the white cup next to the small gray bowl"
        n = rewrite_task_text(path, "I001", new, plan_path=plan, props=props)
        assert n == 10, n
        with h5py.File(path, "r") as f:
            got = {str(f[k].attrs["instruction"]) for k in f
                   if k.startswith("episode")
                   and str(f[k].attrs["instruction_id"]) == "I001"}
        assert got == {new}, got
        raw = json.loads(plan.read_text(encoding="utf-8"))
        slots = raw["scenes"][0]["slots"]
        assert slots[1]["instruction"] == new, slots
        assert slots[1]["target"] == 10, "target 이 사라졌다"
        assert slots[0]["instruction"] == TASKS[0][1], "다른 task 가 바뀌었다"
        assert _edit_count(path) == 1, "문장 정정이 edit_count 를 안 올렸다"

        # 규칙에 어긋나는 문장은 아무것도 쓰지 않고 거부한다.
        before = _edit_count(path)
        try:
            rewrite_task_text(path, "I000", "pick up the purple cup",
                              plan_path=plan, props=props)
        except ValueError:
            pass
        else:
            raise AssertionError("규칙 위반 문장이 통과했다")
        assert _edit_count(path) == before, "거부했는데 마커가 올랐다"

    print("test_scene_repair OK")




def swap_case() -> None:
    """S016 의 모양 -- 찍은 task 의 문장이 틀렸고, 옳은 문장이 빈 칸에 있다.

    교환하면 찍은 것이 옳은 문장을 갖고 틀린 문장은 빈 칸으로 옮겨 간다.
    그 빈 칸을 계획에서 빼면 에피소드를 하나도 안 버리고 끝난다.
    """
    from gello.scene.scene_repair import (
        episode_counts,
        remove_task_from_plan,
        swap_task_texts,
    )

    ON = "pick up the small blue bowl and place it on the small gray bowl"
    INSIDE = "pick up the small blue bowl and place it inside the small gray bowl"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path, plan = _make(root)
        # I002 는 'on' 으로 10개 찍혔고, I003 은 옳은 문장인데 0개다.
        with h5py.File(path, "r+") as f:
            for i in range(5):
                g = f.create_group(f"episode_1{i:02d}")
                g.attrs["scene_id"] = "S008"
                g.attrs["instruction_id"] = "I002"
                g.attrs["instruction"] = ON
                g.attrs["episode_uid"] = f"S008-I002-E{i:03d}"
        raw = json.loads(plan.read_text(encoding="utf-8"))
        raw["scenes"][0]["slots"] += [
            {"instruction_id": "I002", "instruction": ON, "target": 5},
            {"instruction_id": "I003", "instruction": INSIDE, "target": 5},
        ]
        plan.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        assert episode_counts(path).get("I003", 0) == 0

        a, b = swap_task_texts(path, "I002", "I003", plan_path=plan)
        assert (a, b) == (5, 0), (a, b)
        with h5py.File(path, "r") as f:
            got = {str(f[k].attrs["instruction"]) for k in f
                   if k.startswith("episode")
                   and str(f[k].attrs["instruction_id"]) == "I002"}
        assert got == {INSIDE}, got
        raw = json.loads(plan.read_text(encoding="utf-8"))
        by = {s["instruction_id"]: s["instruction"]
              for s in raw["scenes"][0]["slots"]}
        assert by["I002"] == INSIDE and by["I003"] == ON, by

        # 빈 칸이 된 I003 은 계획에서 뺄 수 있다.
        remove_task_from_plan(plan, "S008", "I003", scene_path=path)
        raw = json.loads(plan.read_text(encoding="utf-8"))
        ids = [s["instruction_id"] for s in raw["scenes"][0]["slots"]]
        assert "I003" not in ids and "I002" in ids, ids

        # 에피소드가 있는 task 는 계획에서만 뺄 수 없다.
        try:
            remove_task_from_plan(plan, "S008", "I002", scene_path=path)
        except ValueError as e:
            assert "에피소드가 5개" in str(e), str(e)
        else:
            raise AssertionError("에피소드가 있는 task 가 계획에서 빠졌다")
    print("swap_case OK")


def qualifier_case() -> None:
    """동일 외형이 여럿인 scene -- 한정어로 하나씩 집는다 (S007 의 모양).

    전에는 그런 물체가 든 문장을 아예 안 만들어서, 닥터가 그 scene 의
    지시문을 고칠 수 없었다 (만들 수 있는 문장이 stack 하나뿐이었다).
    """
    from gello.scene.instruction_grammar import (
        enumerate_instructions,
        resolve_reference,
    )
    from gello.scene.props import props_by_id
    from gello.scene.scene_format import SceneMetadata

    P = props_by_id()
    md = SceneMetadata(
        scene_id="S007",
        objects=["OBJ-CUP-WHT-02", "OBJ-CUP-BLU-01", "OBJ-CUP-BLU-02",
                 "OBJ-BOWLL-YEL-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUP-WHT-02": {"zone": [2, 1]},
            "OBJ-CUP-BLU-01": {"zone": [0, 2]},
            "OBJ-CUP-BLU-02": {"zone": [1, 1]},
            "OBJ-BOWLL-YEL-01": {"zone": [0, 0]}}})
    ss = enumerate_instructions(md, P)
    blue = [s for s in ss if "blue cup" in s and "cups" not in s]
    assert blue, "동일 외형이 둘이면 문장이 하나도 안 나온다"

    # 컵 하나에 이름 하나 -- 다 만들면 같은 컵이 이름을 셋씩 갖고 그것들끼리
    # 조합돼 문장이 14 -> 128 개가 된다.
    from apps.workspace.features.doctor.sentence_builder import _split

    names = set()
    for s in blue:
        for ph in _split(s):
            if ph.startswith("the blue cup"):
                names.add(ph)
    assert len(names) == 2, names
    for ph in names:
        assert resolve_reference(ph, md, P) is not None, ph
    assert len({resolve_reference(ph, md, P) for ph in names}) == 2, names

    # 기준점이 달라도 같은 컵이면 같은 물체로 풀린다 -- 화면이 글자로
    # 맞추면 엉뚱한 컵을 기본값으로 켠다.
    a = resolve_reference("the blue cup farthest from the yellow bowl", md, P)
    b = resolve_reference("the blue cup farthest from the white cup", md, P)
    assert a == b == "OBJ-CUP-BLU-01", (a, b)

    # 뜻 열쇠는 한정어를 남긴다 -- 떼면 서로 다른 컵이 같아진다.
    from apps.workspace.features.doctor.sentence_builder import sense_key
    x = "pick up the blue cup farthest from the white cup and place it inside the large yellow bowl"
    y = "pick up the blue cup closest to the white cup and place it inside the large yellow bowl"
    assert sense_key(x) != sense_key(y)
    print("qualifier_case OK")


def two_reasons_case() -> None:
    """한 지시문이 두 가지로 틀릴 수 있다 -- 어순이 뒤집혔고 관계도 틀렸다.

    audit 이 사유마다 한 줄씩 내야 화면이 둘 다 보여줄 수 있다. 전에는
    표의 툴팁이 dict 덮어쓰기로 마지막 하나만, 우측 상세가 break 로 첫
    하나만 보여줬다 (2026-09-07 재점검에서 발견).
    """
    from gello.scene.scene_repair import ORDER_MSG, audit_scene

    # 'on' + 뒤집힌 어순을 한 문장에 담는다.
    bad = "pick up the white cup and place it on the blue small bowl"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path, _plan = _make(root)
        with h5py.File(path, "r+") as f:
            for k in list(f):
                if k.startswith("episode") and \
                        str(f[k].attrs["instruction_id"]) == "I000":
                    f[k].attrs["instruction"] = bad
        vs = [v for v in audit_scene(path) if v.instruction_id == "I000"]
        msgs = {v.message for v in vs}
        assert len(vs) == 2, [v.message for v in vs]
        assert ORDER_MSG in msgs, msgs
        assert any("inside" in m for m in msgs), msgs
        # 세는 단위는 지시문이다 -- 두 사유가 걸린 한 줄은 한 줄의 문제다
        assert len({v.instruction_id for v in vs}) == 1
    print("two_reasons_case OK")


if __name__ == "__main__":
    main()
    swap_case()
    qualifier_case()
    two_reasons_case()
