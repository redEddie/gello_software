"""에피소드 쓰기 경로 왕복 검증 -- 데이터 파이프라인 분리 전 안전망.

로봇/칩라 없이 가짜 관측으로 임시 디렉터리에 HDF5 를 쓰고 되읽어
LiberoTaskWriter / LiberoEpisodeBuffer / write_episode_payload / action_space
계산 / hdf5_repack_status 의 현재 동작을 붙잡는다.
"""
import json
import math
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
    NullTaskWriter,
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

        # 기대값은 2026-09-02 의 코드가 위 입력에 대해 실제로 낸 값을 적어
        # 굳힌 것이다. 검사 대상 함수를 다시 불러 기준을 만들면 양쪽이 같이
        # 바뀌어 아무것도 잡지 못한다 -- 실제로 처음 판에서는 이 파일을 옮기며
        # 관절 델타를 0.9배로 줄이는 변이가 그대로 통과했다.
        # 이 숫자가 달라졌다면 데이터셋이 달라진 것이므로, 기준선을 고치기 전에
        # 왜 달라졌는지부터 답해야 한다.
        references = {
            ACTION_SPACE_JOINT_DELTA: np.array(
                [0.01, 0.02, 0.03, 0.03999999, 0.05000001, 0.06, 0.06999999, 1.0]),
            ACTION_SPACE_JOINT_ABSOLUTE: np.array(
                [0.1, 0.2, 0.30000001, 0.40000001, 0.5, 0.60000002, 0.69999999, 1.0]),
            ACTION_SPACE_EE_DELTA: np.array(
                [1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0]),
            ACTION_SPACE_EE_ABSOLUTE: np.array(
                [0.15000001, 0.25, 0.34999999, 0.0, 0.0, 0.0, 1.0]),
        }

        # 버퍼를 거치지 않고 함수를 직접 부른 값도 같은 기준으로 확인한다.
        # write_episode_payload 의 첫 프레임은 이전 프레임이 없어 특수 처리를
        # 하므로, 위 왕복만으로는 함수 자체의 변화를 놓칠 수 있다.
        direct = {
            ACTION_SPACE_JOINT_DELTA: compute_joint_delta_action(q0, q1, True),
            ACTION_SPACE_JOINT_ABSOLUTE: compute_joint_absolute_action(q0, True),
            ACTION_SPACE_EE_DELTA: compute_delta_action(ee0, ee1, True),
            ACTION_SPACE_EE_ABSOLUTE: compute_ee_absolute_action(ee1, True),
        }
        # 위 입력은 쿼터니언이 전부 단위라 회전 변환 경로를 한 번도 밟지
        # 않는다 -- _quat_to_axis_angle 을 1.01배로 틀어도 0 은 0 이라 통과했다.
        # 실제로 회전이 있는 자세로 그 경로를 밟는다 (z축 30도 -> 45도).
        def _qz(deg: float) -> np.ndarray:
            h = math.radians(deg) / 2
            return np.array([0.0, 0.0, math.sin(h), math.cos(h)])

        rot_a = np.concatenate([[0.1, 0.2, 0.3], _qz(30)])
        rot_b = np.concatenate([[0.15, 0.25, 0.35], _qz(45)])
        assert np.allclose(
            np.asarray(compute_delta_action(rot_a, rot_b, True), dtype=float),
            [1.0, 1.0, 1.0, 0.0, 0.0, 0.52359879, 1.0], atol=1e-6), \
            compute_delta_action(rot_a, rot_b, True)
        assert np.allclose(
            np.asarray(compute_ee_absolute_action(rot_b, True), dtype=float),
            [0.15000001, 0.25, 0.34999999, 0.0, 0.0, 0.78539819, 1.0], atol=1e-6), \
            compute_ee_absolute_action(rot_b, True)

        for space, want in references.items():
            got = np.asarray(direct[space], dtype=float)
            if space == ACTION_SPACE_JOINT_DELTA:
                # 굳힌 값은 q0->q1 한 걸음. 버퍼 왕복의 첫 프레임과는 다르다.
                assert np.allclose(got, want, atol=1e-6), (space, got, want)
            elif space == ACTION_SPACE_JOINT_ABSOLUTE:
                assert np.allclose(got, want, atol=1e-6), (space, got, want)

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


def test_curation():
    """삭제·재번호·재판정 -- GUI 큐레이션이 쓰는 경로.

    2026-09-02 에 안전망을 만들 때 여기가 비어 있었다. 삭제는 되돌릴 수 없고,
    재번호가 어긋나면 LeRobot 변환과 데이터셋 탐색기가 엉뚱한 에피소드를
    가리키는데 그 사실이 몇 주 뒤 학습이 이상할 때에야 드러난다.

    이름만 보지 않고 **내용이 따라왔는지**까지 본다: 에피소드마다 프레임 수를
    다르게 두어, 재번호 뒤에도 각 이름이 원래 그 데이터를 가리키는지 확인한다.
    """
    d = tempfile.mkdtemp(prefix="episode_io_curate_")
    try:
        root = Path(d)
        lengths = [3, 5, 7, 9]          # 에피소드마다 길이를 달리해 신원을 만든다
        with LiberoTaskWriter(
            root=root,
            task_name="curate task",
            language_instruction="pick the cube",
            schema=DatasetSchemaConfig(),
        ) as writer:
            for ep_idx, n in enumerate(lengths):
                writer.start_episode()
                for i in range(n):
                    writer.add_frame(**_make_frame(seed=ep_idx * 100 + i))
                writer.save_episode(success=(ep_idx % 2 == 0))
            path = writer.path

            got = [(e["name"], e["num_samples"], e["success"])
                   for e in writer.list_episodes()]
            assert got == [("demo_0", 3, True), ("demo_1", 5, False),
                           ("demo_2", 7, True), ("demo_3", 9, False)], got

            # 가운데 하나를 지우면 뒤가 당겨져 빈 번호가 없어야 한다.
            writer.delete_episode("demo_1")
            after = [(e["name"], e["num_samples"]) for e in writer.list_episodes()]
            assert after == [("demo_0", 3), ("demo_1", 7), ("demo_2", 9)], after
            assert writer.num_episodes == 3

            # 판정도 데이터와 함께 따라와야 한다 (원래 demo_2 는 성공이었다).
            succ = {e["name"]: e["success"] for e in writer.list_episodes()}
            assert succ == {"demo_0": True, "demo_1": True, "demo_2": False}, succ

            # 재판정은 attr 만 바꾸고 프레임·번호는 건드리지 않는다.
            writer.set_episode_success("demo_1", False)
            re = [(e["name"], e["num_samples"], e["success"])
                  for e in writer.list_episodes()]
            assert re == [("demo_0", 3, True), ("demo_1", 7, False),
                          ("demo_2", 9, False)], re

            # 없는 이름은 조용히 지나가지 않고 KeyError 여야 한다.
            for fn, args in ((writer.delete_episode, ("demo_9",)),
                             (writer.set_episode_success, ("demo_9", True))):
                try:
                    fn(*args)
                except KeyError:
                    pass
                else:
                    raise AssertionError(f"{fn.__name__} 이 없는 이름에 KeyError 를 내지 않았다")

            # 다음 에피소드는 빈 번호가 아니라 그 다음 번호를 받아야 한다.
            writer.start_episode()
            for i in range(2):
                writer.add_frame(**_make_frame(seed=999 + i))
            assert writer.save_episode(success=True) == "demo_3"

        # 파일을 닫고 되읽어도 같은가 -- flush 가 실제로 됐는지.
        with h5py.File(path, "r") as f:
            names = sorted(f["data"].keys(), key=lambda n: int(n.split("_")[1]))
            assert names == ["demo_0", "demo_1", "demo_2", "demo_3"], names
            assert [f["data"][n]["actions"].shape[0] for n in names] == [3, 7, 9, 2]
            assert f["data"].attrs["next_demo_idx"] == 4, dict(f["data"].attrs)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("5. 큐레이션(삭제·재번호·재판정) OK")


def test_session_config_and_null_writer():
    """session_config 는 마지막 것만 남는다. NullTaskWriter 는 아무것도 안 쓴다."""
    d = tempfile.mkdtemp(prefix="episode_io_cfg_")
    try:
        root = Path(d)
        with LiberoTaskWriter(
            root=root,
            task_name="cfg task",
            language_instruction="pick the cube",
            schema=DatasetSchemaConfig(),
        ) as writer:
            writer.record_session_config(reset_wait_seconds=3, grip=0.5)
            writer.record_session_config(reset_wait_seconds=7, grip=0.9)
            path = writer.path
        with h5py.File(path, "r") as f:
            cfg = json.loads(f["data"].attrs["session_config"])
        # 이력이 아니라 "이 task 를 이어서 하려면" 이라서 일부러 덮어쓴다.
        assert cfg == {"reset_wait_seconds": 7, "grip": 0.9}, cfg

        before = set(Path(d).rglob("*"))
        null = NullTaskWriter(schema=DatasetSchemaConfig())
        null.start_episode()
        null.add_frame(**_make_frame(seed=1))
        assert null.save_episode(success=True) is None
        assert null.num_episodes == 0
        assert null.list_episodes() == []
        null.close()
        assert set(Path(d).rglob("*")) == before, "NullTaskWriter 가 파일을 남겼다"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("6. session_config / NullTaskWriter OK")


def test_ordering_and_resume():
    """번호가 두 자리로 넘어가는 순간과, 파일을 다시 열어 이어 쓰는 경로.

    list_episodes 는 이름을 사전순이 아니라 번호순으로 정렬해야 한다. 9개까지는
    둘이 같아서 티가 안 나고, demo_10 이 생기는 순간 사전순은 demo_10 을 demo_2
    앞에 놓는다 -- 데이터셋 탐색기와 LeRobot 변환이 엉뚱한 순서를 보게 된다.
    실제 세션은 열 개를 쉽게 넘긴다.

    resume 은 이어받기다. resume 없이 같은 경로를 열면 덮어쓰지 않고 거절해야
    한다 (수집한 것을 통째로 날리는 사고가 나므로).
    """
    d = tempfile.mkdtemp(prefix="episode_io_order_")
    try:
        root = Path(d)
        kw = dict(root=root, task_name="order task",
                  language_instruction="pick the cube",
                  schema=DatasetSchemaConfig())
        with LiberoTaskWriter(**kw) as writer:
            for ep in range(12):
                writer.start_episode()
                for i in range(2):      # 1프레임 짜리는 아래에서 따로 본다
                    writer.add_frame(**_make_frame(seed=ep * 10 + i))
                writer.save_episode(success=True)
            path = writer.path
            names = [e["name"] for e in writer.list_episodes()]
        assert names == [f"demo_{i}" for i in range(12)], names
        assert names.index("demo_2") < names.index("demo_10"), names

        # resume 없이 같은 파일을 열면 거절해야 한다.
        try:
            LiberoTaskWriter(**kw).close()
        except FileExistsError:
            pass
        else:
            raise AssertionError("resume 없이 기존 파일을 열었는데 거절하지 않았다")

        # resume 으로 열면 번호를 이어받는다.
        with LiberoTaskWriter(resume=True, **kw) as writer:
            assert writer.num_episodes == 12
            writer.start_episode()
            writer.add_frame(**_make_frame(seed=99))
            writer.add_frame(**_make_frame(seed=100))
            assert writer.save_episode(success=False) == "demo_12"

            # 프레임이 2개 미만인 테이크는 저장하지 않고 조용히 버린다.
            # 손이 미끄러져 바로 끊은 경우가 데이터셋에 남지 않게 하는 규칙이라,
            # 여기서 None 이 아니게 되면 한 프레임짜리 에피소드가 쌓인다.
            writer.start_episode()
            writer.add_frame(**_make_frame(seed=101))
            assert writer.save_episode(success=True) is None
            assert writer.num_episodes == 13
        with h5py.File(path, "r") as f:
            assert len(f["data"].keys()) == 13
            assert f["data"].attrs["next_demo_idx"] == 13
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("7. 번호 정렬(두 자리) / resume 이어받기 OK")


def test_optional_obs_fields():
    """기본이 아닌 스키마 조합 -- depth 와 timestamp 를 켜면 실제로 들어가는가."""
    d = tempfile.mkdtemp(prefix="episode_io_opt_")
    try:
        root = Path(d)
        schema = DatasetSchemaConfig(
            save_agentview_depth=True,
            save_eye_in_hand_depth=True,
            save_timestamp=True,
            save_joint_velocities=True,
        )
        with LiberoTaskWriter(
            root=root, task_name="opt task",
            language_instruction="pick the cube", schema=schema,
        ) as writer:
            writer.start_episode()
            for i in range(4):
                f = _make_frame(seed=i)
                f["agentview_depth"] = np.full((480, 640), 0.5, np.float32)
                f["eye_in_hand_depth"] = np.full((480, 640), 0.4, np.float32)
                f["timestamp"] = 1000.0 + i
                f["joint_velocities"] = np.full(7, 0.01, np.float32)
                writer.add_frame(**f)
            writer.save_episode(success=True)
            path = writer.path
        with h5py.File(path, "r") as f:
            obs = f["data/demo_0/obs"]
            for name in ("agentview_depth", "eye_in_hand_depth",
                         "timestamp", "joint_velocities"):
                assert name in obs, (name, list(obs.keys()))
                assert obs[name].shape[0] == 4, (name, obs[name].shape)
            assert np.allclose(obs["timestamp"][...], [1000.0, 1001.0, 1002.0, 1003.0])
        # 기본 스키마에서는 이것들이 없어야 한다 (켜야만 들어간다).
        d2 = tempfile.mkdtemp(prefix="episode_io_opt2_")
        try:
            with LiberoTaskWriter(
                root=Path(d2), task_name="plain task",
                language_instruction="pick the cube",
                schema=DatasetSchemaConfig(),
            ) as writer:
                writer.start_episode()
                writer.add_frame(**_make_frame(seed=0))
                writer.add_frame(**_make_frame(seed=1))
                writer.save_episode(success=True)
                p2 = writer.path
            with h5py.File(p2, "r") as f:
                obs = f["data/demo_0/obs"]
                for name in ("agentview_depth", "timestamp", "joint_velocities"):
                    assert name not in obs, (name, list(obs.keys()))
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("8. 선택 obs 필드(depth/timestamp/joint_velocities) OK")


if __name__ == "__main__":
    test_round_trip()
    test_action_space_branches()
    test_buffer()
    test_repack_status()
    test_curation()
    test_session_config_and_null_writer()
    test_ordering_and_resume()
    test_optional_obs_fields()
    print("test_episode_io 전체 통과")
