"""dataset_sync Hub 조회 revision 핀 검증."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np

WT = str(Path(__file__).resolve().parents[2])  # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/scripts")
sys.argv = ["t"]

from gello.data.dataset_schema import OBS_AGENTVIEW_RGB  # noqa: E402
from gello.data.dataset_sync import (  # noqa: E402
    _ordered_paths,
    legacy_files,
    local_tasks,
    LEROBOT_TAG,
    hub_episode_uids,
    hub_meta,
    local_tasks,
)


def test_revision_pin():
    """hub_meta/hub_episode_uids 가 lerobot 이 읽는 CODEBASE_VERSION 태그로
    snapshot_download 을 부르고, repo 는 있는데 태그만 없는 상태는 빈 결과가
    아니라 오류로 거부해야 한다 (태그 생성 실패 사고를 '새 repo' 로 위장하면
    plan_sync 가 중복 업로드를 권한다 -- refuse rather than guess)."""

    def _fake_download(repo_id, repo_type="dataset", allow_patterns=None,
                       revision=None, force_download=True):
        calls.append((repo_id, revision, allow_patterns))
        # 메타 쪽은 parquet 를, 사이드카 쪽은 json 을 남긴다.
        if allow_patterns and "meta/episode_uids.json" in allow_patterns:
            (tmp / "meta").mkdir(parents=True, exist_ok=True)
            (tmp / "meta" / "episode_uids.json").write_text(
                json.dumps({"0": {"episode_uid": "EP-S000-I000-E000"}}),
                encoding="utf-8",
            )
        else:
            (tmp / "meta" / "episodes").mkdir(parents=True, exist_ok=True)
        return str(tmp)

    tmp = Path(tempfile.mkdtemp(prefix="hubsync_"))
    calls = []

    with patch("huggingface_hub.snapshot_download", side_effect=_fake_download):
        counts, lengths, err = hub_meta("dummy/repo")
        assert err == "", err
        assert counts == {} and lengths == {}  # parquet 가 없어서 빈 결과
        assert any(c[1] == LEROBOT_TAG for c in calls), calls

        uids, err2 = hub_episode_uids("dummy/repo")
        assert err2 == "", err2
        assert uids == {"EP-S000-I000-E000"}, uids
        assert any(c[1] == LEROBOT_TAG and c[2] == ["meta/episode_uids.json"]
                   for c in calls), calls

    # RevisionNotFoundError = "repo 는 있는데 태그가 없음" -- 오류로 거부.
    from huggingface_hub.errors import RepositoryNotFoundError, RevisionNotFoundError  # noqa: E402

    class FakeRevNotFound(RevisionNotFoundError):
        def __init__(self, msg):
            self.args = (msg,)

    with patch("huggingface_hub.snapshot_download",
               side_effect=FakeRevNotFound("no tag")):
        counts, lengths, err = hub_meta("dummy/repo")
        assert "태그" in err and counts == {} and lengths == {}, err
        uids, err2 = hub_episode_uids("dummy/repo")
        assert "태그" in err2 and uids is None, (uids, err2)

    # RepositoryNotFoundError = repo 부재 (첫 업로드 전) -- 여전히 빈 결과가 정상.
    class FakeRepoNotFound(RepositoryNotFoundError):
        def __init__(self, msg):
            self.args = (msg,)

    with patch("huggingface_hub.snapshot_download",
               side_effect=FakeRepoNotFound("no repo")):
        counts, lengths, err = hub_meta("dummy/repo")
        assert err == "" and counts == {} and lengths == {}
        uids, err2 = hub_episode_uids("dummy/repo")
        assert err2 == "" and uids == set()

    print("test_revision_pin 통과: revision=v3.0 핀, 태그 없음 -> 오류 거부, repo 없음 -> 빈 결과")


def test_mixed_at_repack_cleared():
    """legacy 는 업로드 집계에 들어가지 않는다 (2026-09-01 결정, issue #15).

    예전에는 같은 문장이면 legacy 와 scene 을 합산했다. 그 경로가 지시문을
    key 로 덮어써서 같은 문장을 가진 legacy 파일이 둘이면 하나가 조용히
    사라졌기 때문에, 고치는 대신 legacy 를 배포 대상에서 뺐다. scene 분만
    세고, at_repack/lengths 는 scene 기여라 None 이어야 한다."""
    root = Path(tempfile.mkdtemp(prefix="mixed_"))
    task = "mixed pick and place task"

    # ---- scene 파일: 같은 문장의 success 에피소드 1개
    from gello.scene.scene_format import SceneMetadata, SceneWriter  # noqa: E402

    md = SceneMetadata(
        scene_id="S000",
        objects=["OBJ-CUP-BLU-01"],
        layout={"grid": [3, 3], "placements": {"OBJ-CUP-BLU-01": {"zone": [1, 1]}}},
        station="test",
    )
    sw = SceneWriter(root, metadata=md, collector="t")
    sw.set_reference_image(np.zeros((48, 64, 3), dtype=np.uint8))
    sw.start_episode()
    for _ in range(5):
        sw.add_frame(
            agentview_rgb=np.zeros((48, 64, 3), dtype=np.uint8),
            eye_in_hand_rgb=np.zeros((48, 64, 3), dtype=np.uint8),
            joint_positions=np.zeros(7, dtype=np.float32),
            gripper_position=0.5,
            ee_pos_quat=np.zeros(7),
            gripper_closed=False,
            commanded_joint_positions=np.zeros(7, dtype=np.float32),
            commanded_gripper=0.0,
        )
    sw.save_buffer(sw.detach_buffer(), instruction=task, instruction_id="I000", success=True)
    sw.close()

    # ---- legacy 파일: 같은 문장, repacked_episodes 마커 있음
    legacy = root / "mixed_task_demo.hdf5"
    with h5py.File(legacy, "w") as f:
        data = f.create_group("data")
        info = {"language_instruction": json.dumps(task)}
        data.attrs["problem_info"] = json.dumps(info)
        data.attrs["repacked_episodes"] = 10
        for i in range(3):
            grp = data.create_group(f"demo_{i}")
            grp.attrs["num_samples"] = 7
            grp.create_dataset("actions", data=np.zeros((7, 8), dtype=np.float32))
            obs = grp.create_group("obs")
            obs.create_dataset(OBS_AGENTVIEW_RGB, data=np.zeros((7, 48, 64, 3), dtype=np.uint8))

    tasks = local_tasks(root)
    info = tasks.get(task)
    assert info is not None, list(tasks)
    assert info["episodes"] == 1, info  # scene 1개만 (legacy 3개는 제외)
    assert info["lengths"] is None, info  # scene 이 섞여서 지문 끔
    assert info["at_repack"] is None, info  # <-- 핵심: legacy 의 10이 남으면 안 됨

    # scene 이 없는 순수 legacy task 는 at_repack 이 보존
    leg_root = Path(tempfile.mkdtemp(prefix="legonly_"))
    with h5py.File(leg_root / "only_legacy_demo.hdf5", "w") as f:
        data = f.create_group("data")
        info = {"language_instruction": json.dumps("pure legacy task")}
        data.attrs["problem_info"] = json.dumps(info)
        data.attrs["repacked_episodes"] = 5
        for i in range(2):
            grp = data.create_group(f"demo_{i}")
            grp.attrs["num_samples"] = 6
            grp.create_dataset("actions", data=np.zeros((6, 8), dtype=np.float32))
            obs = grp.create_group("obs")
            obs.create_dataset(OBS_AGENTVIEW_RGB, data=np.zeros((6, 48, 64, 3), dtype=np.uint8))
    # legacy 만 있는 루트는 업로드할 것이 없다 -- 빈 계획이 나오고, 무시한
    # 파일이 무엇인지는 legacy_files() 로 알린다 (조용히 빠지지 않게).
    pure = local_tasks(leg_root)
    assert pure == {}, pure
    assert [q.name for q in legacy_files(leg_root)] == ["only_legacy_demo.hdf5"]

    print("test_mixed_at_repack_cleared 통과: legacy 는 집계·업로드에서 제외, "
          "남아 있으면 legacy_files 로 보고")


if __name__ == "__main__":
    test_revision_pin()
    test_mixed_at_repack_cleared()
    print("\ndataset_sync 검증 통과")


# ---------------------------------------------------------------- issue #15
# "지시문 1개 = HDF5 1개" 가정이 되살아나지 않게 고정한다. 같은 문장이 여러
# scene 파일에 걸치는 것은 예외가 아니라 일상이다 -- 실제 수집분에서 문장
# 12개가 파일 2~5개에 걸쳐 있다. 집계가 덮어쓰기로 돌아가면 그 파일들이
# 업로드 계획에서 조용히 빠지고, 동기화 화면은 '일치'로 보인다.
def _make_scene(root: Path, scene_id: str, instructions: list[str]) -> Path:
    """instructions 하나당 성공 에피소드 하나인 최소 scene 파일."""
    from gello.scene.scene_format import SceneMetadata, SceneWriter

    md = SceneMetadata(
        scene_id=scene_id,
        objects=["OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"],
        layout={"grid": [3, 3], "placements": {
            "OBJ-CUP-BLU-01": {"zone": [0, 0]},
            "OBJ-BOWLS-WHT-01": {"zone": [1, 1]}}})
    w = SceneWriter(root=root, scene_id=scene_id, metadata=md)
    try:
        for i, ins in enumerate(instructions):
            w.start_episode()
            for _ in range(3):
                w.add_frame(
                    agentview_rgb=np.zeros((4, 4, 3), np.uint8),
                    eye_in_hand_rgb=np.zeros((4, 4, 3), np.uint8),
                    joint_positions=np.zeros(7, np.float32),
                    gripper_position=0.0,
                    ee_pos_quat=np.zeros(7, np.float32),
                    gripper_closed=False,
                    commanded_joint_positions=np.zeros(7, np.float32),
                    commanded_gripper=0.0,
                )
            w.save_buffer(w.detach_buffer(), instruction=ins,
                          instruction_id=f"I{i:03d}", success=True)
    finally:
        w.close()
    return root / f"scene_{int(scene_id[1:]):03d}.hdf5"


SHARED = "pick up the blue cup and place it on the white small bowl"
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    _make_scene(root, "S000", [SHARED, "open the top drawer"])
    _make_scene(root, "S001", [SHARED])
    _make_scene(root, "S002", [SHARED, "close the top drawer"])

    t = local_tasks(root)
    assert t[SHARED]["episodes"] == 3, \
        f"같은 문장이 합산되지 않았다: {t[SHARED]['episodes']}"
    assert len(t[SHARED]["paths"]) == 3, \
        f"문장이 걸친 파일이 다 모이지 않았다: {t[SHARED]['paths']}"
    # 계획의 파일 목록에 셋 다, 중복 없이 들어간다 -- 하나라도 빠지면 그
    # 파일은 변환에도 백업 업로드에도 안 들어가고 화면은 '일치'로 보인다.
    names = [Path(p).name for p in _ordered_paths(t)]
    assert sorted(names) == ["scene_000.hdf5", "scene_001.hdf5", "scene_002.hdf5"], names
    assert len(names) == len(set(names)), f"중복: {names}"
    assert sum(v["episodes"] for v in t.values()) == 5

    # legacy 는 업로드 대상이 아니다 (2026-09-01 결정). 루트에 있어도 계획에
    # 들어가지 않고, 대신 무시했다는 사실이 계획에 실린다.
    legacy = root / "some_task_demo.hdf5"
    with h5py.File(legacy, "w") as f:
        f.create_group("data")
    t2 = local_tasks(root)
    assert len(_ordered_paths(t2)) == 3, "legacy 가 업로드 계획에 들어갔다"
    assert [p.name for p in legacy_files(root)] == ["some_task_demo.hdf5"]

print("issue #15 회귀 방지: 문장이 여러 scene 에 걸쳐도 전량 집계·전량 업로드, "
      "legacy 는 제외")
