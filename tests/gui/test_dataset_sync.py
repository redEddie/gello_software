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

from gello.dataset_sync import (  # noqa: E402
    LEROBOT_TAG,
    hub_episode_uids,
    hub_meta,
    local_tasks,
)


def test_revision_pin():
    """hub_meta/hub_episode_uids 가 lerobot 이 읽는 CODEBASE_VERSION 태그로
    snapshot_download 을 부르고, 태그가 없는 신생 repo 는 빈 결과로
    처리해야 한다."""

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

    # RevisionNotFoundError 는 "아직 태그가 없는 신생 repo" 로 취급.
    from huggingface_hub.errors import RevisionNotFoundError  # noqa: E402

    class FakeRevNotFound(RevisionNotFoundError):
        def __init__(self, msg):
            self.args = (msg,)

    with patch("huggingface_hub.snapshot_download",
               side_effect=FakeRevNotFound("no tag")):
        counts, lengths, err = hub_meta("dummy/repo")
        assert err == "" and counts == {} and lengths == {}
        uids, err2 = hub_episode_uids("dummy/repo")
        assert err2 == "" and uids == set()

    print("test_revision_pin 통과: snapshot_download revision=v3.0, RevisionNotFoundError -> 빈 결과")


def test_mixed_at_repack_cleared():
    """legacy 와 scene 이 같은 task 문장으로 합산될 때 at_repack 은
    legacy 값이 남지 말고 None 이 되어야 한다. 그래야 plan_sync 의
    '개수 같음 (편집 흔적)' 허위 경고가 사라진다."""
    root = Path(tempfile.mkdtemp(prefix="mixed_"))
    task = "mixed pick and place task"

    # ---- scene 파일: 같은 문장의 success 에피소드 1개
    from gello.scene_format import SceneMetadata, SceneWriter  # noqa: E402

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
            obs.create_dataset("agentview_rgb", data=np.zeros((7, 48, 64, 3), dtype=np.uint8))

    tasks = local_tasks(root)
    info = tasks.get(task)
    assert info is not None, list(tasks)
    assert info["episodes"] == 4, info  # 3 legacy + 1 scene
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
            obs.create_dataset("agentview_rgb", data=np.zeros((6, 48, 64, 3), dtype=np.uint8))
    pure = local_tasks(leg_root)
    assert pure["pure legacy task"]["at_repack"] == 5, pure

    print("test_mixed_at_repack_cleared 통과: legacy+scene 혼합 시 at_repack=None, 순수 legacy 는 보존")


if __name__ == "__main__":
    test_revision_pin()
    test_mixed_at_repack_cleared()
    print("\ndataset_sync 검증 통과")
