"""에피소드 쓰기 경로 왕복 검증 -- 데이터 파이프라인 분리 전 안전망.

로봇/칩라 없이 가짜 관측으로 임시 디렉터리에 HDF5 를 쓰고 되읽어
LiberoTaskWriter / LiberoEpisodeBuffer / write_episode_payload / action_space
계산 / hdf5_repack_status 의 현재 동작을 붙잡는다.
"""
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")

import h5py
import numpy as np

from gello.data.actions import (
    compute_delta_action,
    compute_ee_absolute_action,
    compute_joint_absolute_action,
    compute_joint_delta_action,
)
from gello.data.dataset_schema import (
    ACTION_SPACE_EE_ABSOLUTE,
    ACTION_SPACE_EE_DELTA,
    ACTION_SPACE_JOINT_ABSOLUTE,
    ACTION_SPACE_JOINT_DELTA,
    SCHEMA_FIELDS,
    SCHEMA_VERSION,
    DatasetSchemaConfig,
)
from gello.data.libero_format import (
    LiberoEpisodeBuffer,
    LiberoTaskWriter,
    hdf5_repack_status,
    write_episode_payload,
)


def _make_frame(seed=0, gripper_closed=False):
    rng = np.random.default_rng(seed)
    H, W = 480, 640
    return {
        "agentview_rgb": rng.integers(0, 256, (H, W, 3), dtype=np.uint8),
        "eye_in_hand_rgb": rng.integers(0, 256, (H, W, 3), dtype=np.uint8),
        "joint_positions": rng.random(7).astype(np.float32),
        "gripper_position": float(rng.random()),
        "ee_pos_quat": np.array([0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0], dtype=np.float64),
        "gripper_closed": gripper_closed,
        "commanded_joint_positions": rng.random(7).astype(np.float32),
        "commanded_gripper": float(rng.random()),
    }


def test_round_trip():
    """LiberoTaskWriter 로 3개 에피소드를 쓰고 h5py 로 되읽어 구조를 확인."""
    d = tempfile.mkdtemp(prefix="episode_io_")
    try:
        root = Path(d)
        schema = DatasetSchemaConfig()
        with LiberoTaskWriter(
            root=root,
            task_name="test task",
            language_instruction="pick the cube",
            schema=schema,
        ) as writer:
            for ep_idx in range(3):
                writer.start_episode()
                for i in range(5):
                    f = _make_frame(seed=ep_idx * 10 + i, gripper_closed=(i % 2 == 0))
                    writer.add_frame(**f)
                name = writer.save_episode(success=(ep_idx != 1))
                assert name == f"demo_{ep_idx}", name

            assert writer.num_episodes == 3
            episodes = writer.list_episodes()
            assert len(episodes) == 3
            for ep in episodes:
                assert ep["num_samples"] == 5

        path = root / "test_task_demo.hdf5"
        assert path.exists()

        with h5py.File(path, "r") as f:
            assert "data" in f
            data = f["data"]
            assert set(data.keys()) == {"demo_0", "demo_1", "demo_2"}

            required_datasets = set(SCHEMA_FIELDS[SCHEMA_VERSION]["episode_datasets"])
            required_obs = set(SCHEMA_FIELDS[SCHEMA_VERSION]["obs_datasets"])

            for name in data.keys():
                grp = data[name]
                for key in required_datasets:
                    assert key in grp, (name, key, list(grp.keys()))

                obs = grp["obs"]
                for key in required_obs:
                    assert key in obs, (name, key, list(obs.keys()))

                assert obs["agentview_rgb"].shape == (5, 480, 640, 3)
                assert obs["agentview_rgb"].dtype == np.uint8
                assert obs["eye_in_hand_rgb"].shape == (5, 480, 640, 3)
                assert obs["eye_in_hand_rgb"].dtype == np.uint8
                assert obs["joint_states"].shape == (5, 7)
                assert obs["joint_states"].dtype == np.float32
                assert obs["gripper_states"].shape == (5, 1)
                assert obs["gripper_states"].dtype == np.float32
                assert obs["ee_states"].shape == (5, 6)
                assert obs["ee_pos"].shape == (5, 3)
                assert obs["ee_ori"].shape == (5, 3)
                assert obs["commanded_joint_states"].shape == (5, 7)
                assert obs["commanded_gripper_states"].shape == (5, 1)

                assert grp.attrs["num_samples"] == 5
                assert grp.attrs["action_space"] == ACTION_SPACE_JOINT_ABSOLUTE
                assert grp.attrs["gripper_action_convention"] == "01"
                assert "action_column_names" in grp.attrs
                assert "crop_params" in grp.attrs
                assert "station" in grp.attrs

                assert grp["actions"].shape == (5, 8)
                assert grp["actions"].dtype == np.float32
                assert grp["rewards"].shape == (5,)
                assert grp["dones"].shape == (5,)
                assert grp["dones"][-1] == 1.0
                assert np.all(grp["dones"][:-1] == 0.0)
                assert np.all(grp["rewards"][:] == 0.0)

                if name == "demo_1":
                    assert bool(grp.attrs["success"]) is False
                else:
                    assert bool(grp.attrs["success"]) is True
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("1. round-trip OK")


def test_action_space_branches():
    """action_space 네 갈래 각각 대표 입력으로 첫 프레임 action 을 고정."""
    d = tempfile.mkdtemp(prefix="episode_io_actions_")
    try:
        q0 = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], dtype=np.float32)
        q1 = np.array([0.11, 0.22, 0.33, 0.44, 0.55, 0.66, 0.77], dtype=np.float32)
        ee0 = np.array([0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0], dtype=np.float64)
        ee1 = np.array([0.15, 0.25, 0.35, 0.0, 0.0, 0.0, 1.0], dtype=np.float64)

        # 현재 코드가 내는 값을 그대로 기준으로 삼는다.
        references = {
            ACTION_SPACE_JOINT_DELTA: compute_joint_delta_action(q0, q1, True),
            ACTION_SPACE_JOINT_ABSOLUTE: compute_joint_absolute_action(q0, True),
            ACTION_SPACE_EE_DELTA: compute_delta_action(ee0, ee1, True),
            ACTION_SPACE_EE_ABSOLUTE: compute_ee_absolute_action(ee1, True),
        }

        path = Path(d) / "actions.hdf5"
        with h5py.File(path, "w") as f:
            data = f.create_group("data")
            for action_space in (
                ACTION_SPACE_JOINT_DELTA,
                ACTION_SPACE_JOINT_ABSOLUTE,
                ACTION_SPACE_EE_DELTA,
                ACTION_SPACE_EE_ABSOLUTE,
            ):
                schema = DatasetSchemaConfig(action_space=action_space)
                buf = LiberoEpisodeBuffer(schema=schema)
                for i in range(3):
                    buf.add_frame(
                        agentview_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
                        eye_in_hand_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
                        joint_positions=q0 if i == 0 else q1,
                        gripper_position=0.5,
                        ee_pos_quat=ee0 if i == 0 else ee1,
                        gripper_closed=(i % 2 == 0),
                        commanded_joint_positions=q0,
                        commanded_gripper=0.5,
                    )
                grp = data.create_group(f"demo_{action_space}")
                write_episode_payload(grp, buf, schema)

                actions = grp["actions"][...]
                ref = references[action_space]
                assert np.allclose(actions[0], ref, atol=1e-6), (
                    action_space,
                    actions[0],
                    ref,
                )

                if action_space in (ACTION_SPACE_JOINT_DELTA, ACTION_SPACE_JOINT_ABSOLUTE):
                    assert actions.shape == (3, 8), (action_space, actions.shape)
                else:
                    assert actions.shape == (3, 7), (action_space, actions.shape)

                # 기본 스키마는 gripper_action_match_obs=True -> 0/1
                assert actions[0, -1] == 1.0  # 첫 프레임 closed=True
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("2. action_space branches OK")


def test_buffer():
    """LiberoEpisodeBuffer 에 프레임을 넣고 비운 뒤 길이와 순서 확인."""
    schema = DatasetSchemaConfig()
    buf = LiberoEpisodeBuffer(schema=schema)
    assert len(buf) == 0

    frames = []
    for i in range(4):
        f = _make_frame(seed=i, gripper_closed=(i % 2 == 0))
        frames.append(f)
        buf.add_frame(**f)

    assert len(buf) == 4
    assert len(buf.joint_states) == 4
    assert len(buf.agentview_rgb) == 4
    assert len(buf.eye_in_hand_rgb) == 4
    assert len(buf.ee_pos_quat) == 4
    assert len(buf.commanded_joint_positions) == 4

    # 순서가 넣은 대로인지 (seed 기반으로 비교)
    for i, f in enumerate(frames):
        assert np.allclose(buf.joint_states[i], f["joint_positions"])
        assert np.allclose(buf.ee_pos_quat[i], f["ee_pos_quat"])
        assert np.allclose(buf.commanded_joint_positions[i], f["commanded_joint_positions"])

    buf.clear()
    assert len(buf) == 0
    assert buf.joint_states == []
    assert buf.agentview_rgb == []
    assert buf.commanded_joint_positions == []
    print("3. buffer OK")


def test_repack_status():
    """hdf5_repack_status 가 압축 여부에 따라 갈리는지 확인."""
    d = tempfile.mkdtemp(prefix="episode_io_repack_")
    try:
        rng = np.random.default_rng(0)
        img = rng.integers(0, 256, (10, 128, 128, 3), dtype=np.uint8)

        # 압축 안 된 파일
        path_uncompressed = Path(d) / "uncompressed.hdf5"
        with h5py.File(path_uncompressed, "w") as f:
            data = f.create_group("data")
            grp = data.create_group("demo_0")
            obs = grp.create_group("obs")
            obs.create_dataset("agentview_rgb", data=img)
            obs.create_dataset("eye_in_hand_rgb", data=img)
            grp.create_dataset("actions", data=np.zeros((10, 7), dtype=np.float32))
            grp.create_dataset("rewards", data=np.zeros(10, dtype=np.float32))
            grp.create_dataset("dones", data=np.zeros(10, dtype=np.float32))

        st = hdf5_repack_status(path_uncompressed)
        assert st["error"] is None, st
        assert not st["repacked"], st
        assert not st["mixed"], st
        assert st["compression"] in (None, "없음"), st

        # gzip 으로 재압축된 파일
        path_compressed = Path(d) / "compressed.hdf5"
        with h5py.File(path_compressed, "w") as f:
            data = f.create_group("data")
            data.attrs["repacked"] = "2026-09-01T00:00:00"
            data.attrs["repacked_episodes"] = 1
            grp = data.create_group("demo_0")
            obs = grp.create_group("obs")
            obs.create_dataset("agentview_rgb", data=img, compression="gzip")
            obs.create_dataset("eye_in_hand_rgb", data=img, compression="gzip")
            grp.create_dataset("actions", data=np.zeros((10, 7), dtype=np.float32))
            grp.create_dataset("rewards", data=np.zeros(10, dtype=np.float32))
            grp.create_dataset("dones", data=np.zeros(10, dtype=np.float32))

        st = hdf5_repack_status(path_compressed)
        assert st["error"] is None, st
        assert st["repacked"], st
        assert not st["mixed"], st
        assert st["compression"] == "gzip", st
        assert st["marker"] == "2026-09-01T00:00:00", st
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("4. repack status OK")


if __name__ == "__main__":
    test_round_trip()
    test_action_space_branches()
    test_buffer()
    test_repack_status()
    print("test_episode_io 전체 통과")
