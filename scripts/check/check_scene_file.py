"""scene_XXX.hdf5 구조 검사기 + 포맷 자가 검증.

두 용도:

  python scripts/check/check_scene_file.py <scene_000.hdf5> [...]
      파일의 metadata / 에피소드 / slot 현황을 표로 출력하고 불변식을 검사한다.
      QA 때 "이 파일이 규격대로인가"를 사람이 확인하는 용도.

  python scripts/check/check_scene_file.py --selftest [--keep DIR]
      로봇 없이 더미 데이터로 scene 파일을 처음부터 만들어 보고(생성 → 기준
      사진 → 에피소드 3개 → QA 재판정 → resume 후 1개 추가) 규격 전체를
      검증한다. 리팩터링된 write_episode_payload 를 legacy writer 가 그대로
      쓰는지도 함께 확인한다. CI/개발머신용 -- 카메라·로봇·데이터 불필요.

exit 0 = 전부 통과.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gello.scene.props import active_prop_ids  # noqa: E402
from gello.scene.scene_format import (  # noqa: E402
    EPISODE_GROUP_RE,
    QUALITY_BAD_DATA,
    QUALITY_STATUSES,
    SCENE_FILE_RE,
    SceneMetadata,
    SceneWriter,
    count_by_slot,
    describe_scene,
    empty_zones,
    list_scene_episodes,
    next_scene_id,
    read_reference_image,
    read_scene_metadata,
)

REQUIRED_EPISODE_ATTRS = (
    "scene_id", "instruction_id", "episode_id", "episode_uid", "instruction",
    "success", "quality_status", "collector", "timestamp",
    "num_samples", "action_space", "gripper_action_convention",
    "action_column_names", "crop_params", "station",
)
REQUIRED_METADATA_ATTRS = (
    "scene_id", "objects", "layout", "description", "station",
    "dataset_version", "created", "next_episode_idx",
)


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def verify_scene_file(path: Path) -> list[str]:
    """불변식 위반 목록(비면 통과). 완료 기준(계획서 §8)을 파일 하나에 대해
    기계적으로 검사한다."""
    problems: list[str] = []
    if not SCENE_FILE_RE.match(path.name):
        problems.append(f"파일명이 scene_XXX.hdf5 형식이 아니다: {path.name}")
    if path.match("*_demo.hdf5"):
        problems.append("파일명이 legacy 글롭(*_demo.hdf5)에 걸린다")

    with h5py.File(path, "r") as f:
        # legacy 흔적 금지 -- 이 두 스텁을 새 포맷에서 빼기로 결정했다
        if "data" in f:
            problems.append("legacy 'data' 그룹이 있다")
        for bad in ("problem_info", "env_args", "next_demo_idx"):
            if bad in f.attrs or ("metadata" in f and bad in f["metadata"].attrs):
                problems.append(f"legacy attr {bad!r} 가 있다")

        if "metadata" not in f:
            problems.append("metadata 그룹이 없다")
            return problems
        meta = f["metadata"]
        for k in REQUIRED_METADATA_ATTRS:
            if k not in meta.attrs:
                problems.append(f"metadata.attrs[{k!r}] 가 없다")

        try:
            md = _read_md_checked(meta)
        except Exception as e:  # noqa: BLE001
            problems.append(f"metadata 파싱 실패: {e}")
            md = None
        if md is not None:
            for oid in md.objects:
                if not oid.startswith("OBJ-"):
                    problems.append(f"objects 에 instance ID 가 아닌 값: {oid!r}")

        ep_names = sorted(
            (k for k in f.keys() if EPISODE_GROUP_RE.match(k)),
            key=lambda k: int(EPISODE_GROUP_RE.match(k).group(1)),
        )
        stray = [k for k in f.keys() if k != "metadata" and not EPISODE_GROUP_RE.match(k)]
        if stray:
            problems.append(f"episode_NNN 도 metadata 도 아닌 그룹: {stray}")

        seen_ids: list[int] = []
        seen_uids: set = set()
        slot_seq: dict = {}   # instruction_id -> [slot_episode_idx ...] (그룹 순)
        for name in ep_names:
            grp = f[name]
            for k in REQUIRED_EPISODE_ATTRS:
                if k not in grp.attrs:
                    problems.append(f"{name}: attrs[{k!r}] 가 없다")
            if "instruction" in grp.attrs:
                ins = str(grp.attrs["instruction"])
                if len(ins) >= 2 and ins[0] == '"' and ins[-1] == '"':
                    problems.append(f"{name}: instruction 이 따옴표로 감싸져 있다: {ins!r}")
            if "quality_status" in grp.attrs and str(grp.attrs["quality_status"]) not in QUALITY_STATUSES:
                problems.append(f"{name}: 알 수 없는 quality_status {grp.attrs['quality_status']!r}")
            if "episode_id" in grp.attrs:
                eid = int(grp.attrs["episode_id"])
                if eid != int(EPISODE_GROUP_RE.match(name).group(1)):
                    problems.append(f"{name}: episode_id attr({eid})와 그룹 이름이 다르다")
                seen_ids.append(eid)
            if "episode_uid" in grp.attrs:
                uid = str(grp.attrs["episode_uid"])
                if uid in seen_uids:
                    problems.append(f"{name}: episode_uid 중복 {uid}")
                seen_uids.add(uid)
                if "slot_episode_idx" in grp.attrs and "instruction_id" in grp.attrs:
                    slot_seq.setdefault(str(grp.attrs["instruction_id"]), []).append(
                        int(grp.attrs["slot_episode_idx"]))
                # E번호는 slot 로컬 (2026-08-13 결정) -- attr 이 있으면 uid 와
                # 일치해야 한다 (없는 파일은 전역 번호 시절의 구형)
                if "slot_episode_idx" in grp.attrs:
                    want = f"E{int(grp.attrs['slot_episode_idx']):03d}"
                    if not uid.endswith("-" + want):
                        problems.append(
                            f"{name}: episode_uid({uid})가 slot_episode_idx({want})와 다르다")
            if md is not None and "scene_id" in grp.attrs and str(grp.attrs["scene_id"]) != md.scene_id:
                problems.append(f"{name}: scene_id 가 metadata 와 다르다")
            n = int(grp.attrs.get("num_samples", -1))
            if "actions" not in grp or "obs" not in grp:
                problems.append(f"{name}: actions/obs 페이로드가 없다")
            elif grp["actions"].shape[0] != n:
                problems.append(f"{name}: actions 길이 {grp['actions'].shape[0]} != num_samples {n}")
        if seen_ids != sorted(seen_ids) or len(set(seen_ids)) != len(seen_ids):
            problems.append(f"episode_id 가 단조 증가·유일하지 않다: {seen_ids}")
        # 삭제 후 renumber 규칙: 그룹 번호 0..N-1 연속, slot E번호도 그 slot 안에서
        # 0..k-1 연속 (빈자리는 renumber 누락 신호)
        if seen_ids and seen_ids != list(range(len(seen_ids))):
            problems.append(f"episode 번호가 0..N-1 연속이 아니다 (renumber 누락?): {seen_ids}")
        for iid, seq in slot_seq.items():
            if seq != list(range(len(seq))):
                problems.append(f"{iid}: slot E번호가 0..k-1 연속이 아니다: {seq}")

        next_idx = int(meta.attrs.get("next_episode_idx", -1))
        if seen_ids and next_idx <= max(seen_ids):
            problems.append(f"next_episode_idx({next_idx})가 기존 최대 episode_id({max(seen_ids)}) 이하다")
    return problems


def _read_md_checked(meta: h5py.Group) -> SceneMetadata:
    md = SceneMetadata(
        scene_id=str(meta.attrs["scene_id"]),
        objects=json.loads(meta.attrs["objects"]),
        layout=json.loads(meta.attrs["layout"]),
        description=str(meta.attrs.get("description", "")),
        station=str(meta.attrs.get("station", "")),
        dataset_version=str(meta.attrs.get("dataset_version", "")),
        created=str(meta.attrs.get("created", "")),
    )
    md.validate()
    return md


def print_scene_file(path: Path) -> None:
    md = read_scene_metadata(path)
    ref = read_reference_image(path)
    print(f"\n== {path.name} ==")
    for line in describe_scene(md).splitlines():
        print(f"  {line}")
    print(f"  reference_image : {'%dx%d' % (ref.shape[1], ref.shape[0]) if ref is not None else '(없음)'}")
    eps = list_scene_episodes(path)
    print(f"  episodes        : {len(eps)}개")
    for ep in eps:
        print(f"    {ep['episode_uid']}  [{ep['quality_status']:>10}]  {ep['num_samples']:4d}f  "
              f"{ep['collector'] or '-':<10} {ep['instruction']}")
    counts = count_by_slot(path)
    if counts:
        print("  slot 현황       :", "  ".join(
            f"{iid}: {c['usable']}/{c['total']} usable" for iid, c in sorted(counts.items())))


# ------------------------------------------------------------------ selftest
def _dummy_frames(writer, n: int = 5, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    for _ in range(n):
        writer.add_frame(
            agentview_rgb=rng.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            eye_in_hand_rgb=rng.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            joint_positions=rng.standard_normal(7).astype(np.float32),
            gripper_position=float(rng.random()),
            ee_pos_quat=np.array([0.4, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0]),
            gripper_closed=bool(rng.random() > 0.5),
            commanded_joint_positions=rng.standard_normal(7).astype(np.float32),
            commanded_gripper=float(rng.random() > 0.5),
            joint_torques=rng.standard_normal(7).astype(np.float32),
            ext_joint_torques=rng.standard_normal(7).astype(np.float32),
            ee_wrench=rng.standard_normal(6).astype(np.float32),
        )


def _expect_raise(exc_type, fn, what: str) -> None:
    try:
        fn()
    except exc_type:
        print(f"  ✓ 거부됨: {what}")
        return
    _fail(f"거부됐어야 한다: {what}")


def selftest(keep: Path | None) -> None:
    root = keep or Path(tempfile.mkdtemp(prefix="scene_selftest_"))
    root.mkdir(parents=True, exist_ok=True)
    print(f"selftest root: {root}")
    prop_ids = active_prop_ids()

    layout = {
        "grid": [3, 3],
        "placements": {
            "OBJ-CUP-BLU-01": {"zone": [0, 2]},
            "OBJ-CUP-WHT-01": {"zone": [0, 0]},
            "OBJ-BOWLS-YEL-01": {"zone": [1, 1]},
            "OBJ-DRAWER-01": {"zone": [2, 0]},
            "OBJ-CUP-WHT-02": {"zone": [2, 2]},
        },
        "relations": [["OBJ-CUP-BLU-01", "next_to", "OBJ-BOWLS-YEL-01"]],
    }
    sid = next_scene_id(root)
    assert sid == "S000", sid
    md = SceneMetadata(
        scene_id=sid,
        objects=["OBJ-CUP-BLU-01", "OBJ-CUP-WHT-01", "OBJ-BOWLS-YEL-01",
                 "OBJ-DRAWER-01", "OBJ-CUP-WHT-02"],
        layout=layout,
        description="컵 2개가 노란 그릇 양옆, 서랍장 왼쪽 아래. 종이컵은 어떤 instruction 에도 안 나오는 무시 대상.",
        station="selftest",
    )

    # -- metadata 검증이 실제로 막는지
    bad = SceneMetadata(scene_id=sid, objects=["blue cup"], layout=layout)
    _expect_raise(ValueError, lambda: bad.validate(), "색 이름을 objects 에 적음")
    bad2 = SceneMetadata(scene_id=sid, objects=["OBJ-CUP-BLU-01"],
                         layout={"grid": [3, 3], "placements": {"OBJ-CUP-BLU-01": {"zone": [5, 0]}}})
    _expect_raise(ValueError, lambda: bad2.validate(), "격자를 벗어난 존")
    bad3 = SceneMetadata(scene_id=sid, objects=["OBJ-CUP-BLU-01"],
                         layout={"grid": [4, 3], "placements": {"OBJ-CUP-BLU-01": {"zone": [0, 0]}}})
    _expect_raise(ValueError, lambda: bad3.validate(), "표준(3x3)이 아닌 격자")
    _expect_raise(ValueError, lambda: SceneMetadata(
        scene_id=sid, objects=["OBJ-NOPE-XXX-01"], layout=layout).validate(prop_ids),
        "인벤토리에 없는 instance ID")

    # -- 생성 + 에피소드 3개 (instruction 2종)
    w = SceneWriter(root, metadata=md, collector="tester", known_prop_ids=prop_ids)
    w.set_reference_image(np.zeros((48, 64, 3), dtype=np.uint8))
    I0 = "pick up the blue cup and place it on the yellow bowl"
    I3 = "open the top drawer"
    _dummy_frames(w, seed=1)
    assert w.save_buffer(w.detach_buffer(), instruction=I0, instruction_id="I000", success=True) == "episode_000"
    _dummy_frames(w, seed=2)
    assert w.save_buffer(w.detach_buffer(), instruction=I0, instruction_id="I000", success=False) == "episode_001"
    _dummy_frames(w, seed=3)
    assert w.save_buffer(w.detach_buffer(), instruction=I3, instruction_id="I003", success=True) == "episode_002"

    # -- 저장 시점 가드
    _dummy_frames(w, seed=4)
    buf = w.detach_buffer()
    _expect_raise(ValueError, lambda: w.save_buffer(
        buf, instruction=f'"{I0}"', instruction_id="I000", success=True), "따옴표로 감싼 instruction")
    _expect_raise(ValueError, lambda: w.save_buffer(
        buf, instruction=I0, instruction_id="task_7", success=True), "잘못된 instruction ID")
    _expect_raise(ValueError, lambda: w.save_buffer(
        buf, instruction=I0, instruction_id="I000"), "라벨 없는 에피소드")
    # 빈 버퍼는 조용히 None (legacy 와 동일)
    assert w.save_buffer(w.detach_buffer(), instruction=I0, instruction_id="I000", success=True) is None

    # -- QA 재판정 (덮어쓰기·삭제 대신 상태만)
    w.set_quality_status("episode_001", QUALITY_BAD_DATA)
    _expect_raise(ValueError, lambda: w.set_quality_status("episode_000", "great"), "알 수 없는 quality_status")
    w.close()

    # -- 같은 scene 을 실수로 다시 만들면 거부
    _expect_raise(FileExistsError, lambda: SceneWriter(root, metadata=md), "기존 scene 덮어쓰기")

    # -- resume: 파일에서 metadata 를 읽고, 번호는 이어서
    w2 = SceneWriter(root, scene_id="S000", resume=True, collector="tester2")
    assert w2.metadata.objects == md.objects
    _dummy_frames(w2, seed=5)
    assert w2.save_buffer(w2.detach_buffer(), instruction=I3, instruction_id="I003", success=True) == "episode_003"
    assert w2.num_episodes == 4
    w2.close()

    path = root / "scene_000.hdf5"

    # -- 불변식 전수 검사
    problems = verify_scene_file(path)
    if problems:
        _fail("불변식 위반:\n  " + "\n  ".join(problems))
    print("  ✓ verify_scene_file: 위반 없음")

    # -- 내용 검사
    eps = list_scene_episodes(path)
    # E번호는 slot 로컬 -- I000 과 I003 이 각각 E000 부터 센다
    assert [e["episode_uid"] for e in eps] == [
        "EP-S000-I000-E000", "EP-S000-I000-E001", "EP-S000-I003-E000", "EP-S000-I003-E001"]
    assert [e["collector"] for e in eps] == ["tester", "tester", "tester", "tester2"]
    assert eps[1]["quality_status"] == "bad_data" and eps[1]["success"] is False
    assert count_by_slot(path) == {
        "I000": {"total": 2, "usable": 1}, "I003": {"total": 2, "usable": 2}}
    with h5py.File(path, "r") as f:
        g = f["episode_000"]
        assert g["actions"].shape == (5, 8)
        assert g["obs/joint_states"].shape == (5, 7)
        assert g["obs/agentview_rgb"].shape == (5, 48, 64, 3)
        # 포스·토크: 호출자가 주면 스키마 토글 없이 기록된다 (2026-08-23)
        assert g["obs/joint_torques"].shape == (5, 7)
        assert g["obs/joint_torques"].dtype == np.float32
        assert g["obs/ext_joint_torques"].shape == (5, 7)
        assert g["obs/ee_wrench"].shape == (5, 6)
        assert str(g.attrs["instruction"]) == I0  # 따옴표 없이 그대로
    assert read_reference_image(path) is not None
    assert next_scene_id(root) == "S001"
    md_back = read_scene_metadata(path)
    assert md_back.description == md.description
    assert empty_zones(md_back.layout) == [(0, 1), (1, 0), (1, 2), (2, 1)]
    desc = describe_scene(md_back)
    assert "빈 존: (0,1) (1,0) (1,2) (2,1)" in desc and "CUP-BLU-01" in desc
    print("  ✓ scene 포맷: UID·slot 카운트·페이로드·기준사진·describe_scene 확인")

    # -- 큐레이션 편집 마커 (2026-08-23): 삭제하면 edit_count 가 올라가고,
    #    변환기의 resume 게이트가 이 값으로 이어붙이기를 거부한다.
    #    --keep 산출물은 tests/gui 픽스처로도 쓰이므로 원본은 건드리지 않고
    #    하위 디렉터리 사본에서 검증한다.
    import shutil as _shutil
    from gello.scene.scene_format import delete_scene_episodes
    probe_dir = root / "editprobe"
    probe_dir.mkdir(exist_ok=True)
    probe = probe_dir / "scene_000.hdf5"
    _shutil.copyfile(path, probe)
    with h5py.File(probe, "r") as f:
        assert int(f["metadata"].attrs.get("edit_count", 0)) == 0
    delete_scene_episodes(probe, ["episode_001"])
    with h5py.File(probe, "r") as f:
        assert int(f["metadata"].attrs["edit_count"]) == 1
        assert "edited" in f["metadata"].attrs
    delete_scene_episodes(probe, ["episode_002"])
    with h5py.File(probe, "r") as f:
        assert int(f["metadata"].attrs["edit_count"]) == 2  # 단조 증가
    problems = verify_scene_file(probe)
    if problems:
        _fail("삭제 후 불변식 위반:\n  " + "\n  ".join(problems))
    _shutil.rmtree(probe_dir)
    print("  ✓ 편집 마커: 삭제마다 edit_count 증가, 삭제 후 불변식 유지")

    # -- 리팩터링 회귀: legacy writer 가 공용 페이로드로 여전히 demo_N 을 쓴다
    from gello.data.libero_format import LiberoTaskWriter
    lw = LiberoTaskWriter(root, task_name="selftest task", language_instruction="selftest task")
    _dummy_frames(lw, seed=6)
    assert lw.save_episode(success=True) == "demo_0"
    lw.close()
    with h5py.File(root / "selftest_task_demo.hdf5", "r") as f:
        d = f["data/demo_0"]
        assert d["actions"].shape == (5, 8) and d["obs/joint_states"].shape == (5, 7)
        assert "problem_info" in f["data"].attrs  # legacy 는 스텁 유지
        assert int(f["data"].attrs["next_demo_idx"]) == 1
    print("  ✓ legacy writer 회귀: demo_0 페이로드·스텁 동일")

    print_scene_file(path)
    if keep is None:
        import shutil
        shutil.rmtree(root)
        print("\nselftest 통과 (임시 파일 삭제됨). --keep DIR 로 결과 파일을 남길 수 있다.")
    else:
        print(f"\nselftest 통과. 결과 파일: {root}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, help="검사할 scene_XXX.hdf5")
    ap.add_argument("--selftest", action="store_true", help="더미 데이터로 포맷 전체를 자가 검증")
    ap.add_argument("--keep", type=Path, default=None, help="selftest 결과 파일을 이 디렉터리에 남긴다")
    args = ap.parse_args()

    if args.selftest:
        selftest(args.keep)
        return 0
    if not args.paths:
        ap.error("검사할 파일을 주거나 --selftest 를 쓴다")
    rc = 0
    for p in args.paths:
        problems = verify_scene_file(p)
        print_scene_file(p)
        # scene 구성 규칙(configs/scenes/scene_rules.yaml)은 경고로만 보여준다 --
        # 규칙은 추천 후보 필터/신규 배치 lint 용이지 기존 파일의 합불
        # 기준이 아니다 (규칙 도입 전에 찍힌 scene 이 위반일 수 있다).
        try:
            from gello.scene.props import props_by_id
            from gello.scene.scene_format import read_scene_metadata
            from gello.scene.scene_rules import check as rules_check
            rv = rules_check(read_scene_metadata(p), props_by_id())
            for msg in rv:
                print(f"  ⚠ 규칙 경고: {msg}")
        except Exception as e:  # noqa: BLE001 -- 규칙 검사 실패가 QA 를 막으면 안 된다
            print(f"  (규칙 검사 생략: {type(e).__name__}: {e})")
        if problems:
            rc = 1
            print("  ✖ 불변식 위반:")
            for msg in problems:
                print(f"    - {msg}")
        else:
            print("  ✓ 불변식 통과")
    return rc


if __name__ == "__main__":
    sys.exit(main())
