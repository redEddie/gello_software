"""Convert curated LIBERO-format <task>_demo.hdf5 files into one LeRobotDataset
(parquet + video) for efficient, HF-Viewer-browsable upload.

Collection + curation (deleting bad takes) stays exactly as-is in the GUI's
HDF5 workflow -- this only runs afterward, at upload time. Convert *after*
you've deleted what you don't want; this script has no delete/filter step
of its own beyond --only-success, so whatever's still in the .hdf5 files is
what ends up in the LeRobotDataset.

Multiple task files become one multi-task LeRobotDataset (LeRobot's native
way to hold many tasks): each episode carries the source file's language
instruction as its `task`.

SCHEMA IS DETECTED FROM THE FILES, NOT HARDCODED
--------------------------------------------------
Since the GUI's "데이터셋 구조 사용자 지정" dialog (gello/dataset_schema.py) lets an
operator pick a different action space and/or drop obs fields per session,
this script reads each episode's actual obs/ keys, `actions` width, and
`action_space` attr (see gello/libero_format.py's LiberoTaskWriter) instead
of assuming the original fixed LIBERO schema. Files predating that attr are
treated as `ee_delta` (what they always were).

LeRobotDataset fixes ONE `features` dict for the whole dataset at creation
time, so every episode being converted together must share the exact same
obs fields + action space + gripper-in-action-or-not. A quick pre-flight
pass (attrs/keys only, no image data) checks this and fails with a clear
message before any conversion work starts -- convert mismatched files
separately, with different --repo-id/--root.

`observation.state` is built from whichever of `joint_states`/`gripper_states`
are present (skipped entirely if neither is). Other optional obs fields
(ee_pos, ee_ori, ee_states, joint_velocities, timestamp) are NOT currently
propagated into the LeRobotDataset -- only images, joint/gripper state, and
actions are.

Images are assumed IMAGE_SIZE x IMAGE_SIZE (LIBERO/OpenVLA's 256x256
convention, see gello/libero_format.py). The GUI's schema dialog can record
at native camera resolution instead (image_size="원본 해상도 유지") -- this
script does not support converting those files yet; it fails loudly with a
clear message rather than mis-declaring the LeRobotDataset feature shape.

Usage:
    python scripts/convert_libero_to_lerobot.py \
        /home/franka/libero_datasets/*.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop-lerobot \
        --root /home/franka/lerobot_upload

    # ... review locally, then push:
    python scripts/convert_libero_to_lerobot.py \
        /home/franka/libero_datasets/*.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop-lerobot \
        --root /home/franka/lerobot_upload \
        --push --private=false
"""

import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gello.dataset_schema import ACTION_SPACE_EE_DELTA  # noqa: E402
from gello.libero_format import IMAGE_SIZE, action_column_names  # noqa: E402


def _language_instruction(f: h5py.File) -> str:
    info = json.loads(f["data"].attrs["problem_info"])
    lang = info.get("language_instruction", "")
    if len(lang) >= 2 and lang.startswith('"') and lang.endswith('"'):
        lang = lang[1:-1]
    return lang


def _episode_schema(grp: h5py.Group) -> dict:
    obs = grp["obs"]
    obs_keys = frozenset(obs.keys())
    action_space = grp.attrs.get("action_space", ACTION_SPACE_EE_DELTA)
    base_cols = action_column_names(action_space)
    action_dim = grp["actions"].shape[1]
    if action_dim not in (len(base_cols), len(base_cols) + 1):
        raise ValueError(
            f"actions has {action_dim} columns, expected {len(base_cols)} or "
            f"{len(base_cols) + 1} for action_space={action_space!r}"
        )
    # Images are assumed IMAGE_SIZE x IMAGE_SIZE here (LIBERO/OpenVLA
    # convention) -- the GUI's "사용자 지정" dialog can record at native
    # camera resolution instead (DatasetSchemaConfig.image_size=None), but
    # this script does not support converting those; check_image_shapes()
    # fails loudly rather than silently mis-declaring the LeRobotDataset
    # feature shape.
    return {
        "obs_keys": obs_keys,
        "action_space": action_space,
        "has_gripper": action_dim == len(base_cols) + 1,
    }


def _check_image_shape(path: Path, name: str, obs: h5py.Group, key: str) -> None:
    shape = obs[key].shape[1:]
    if shape != (IMAGE_SIZE, IMAGE_SIZE, 3):
        raise SystemExit(
            f"{path.name}/{name}의 {key} shape={shape}인데 {(IMAGE_SIZE, IMAGE_SIZE, 3)}가 아닙니다.\n"
            "이 스크립트는 256x256(LIBERO 기본) 해상도만 지원합니다 -- GUI의 \"데이터셋 구조 "
            "사용자 지정\"에서 이미지 해상도를 \"원본 해상도 유지\"로 수집한 파일은 지금 변환할 수 없습니다."
        )


def _scan_schema(hdf5_paths: list, only_success: bool) -> dict:
    """Pre-flight pass over every episode that will be converted -- cheap
    (attrs/group keys only, no array data) -- so a schema mismatch is caught
    before LeRobotDataset.create() has written anything to --root."""
    reference = None
    reference_loc = None
    for path in hdf5_paths:
        with h5py.File(path, "r") as f:
            data = f["data"]
            for name in sorted(data.keys(), key=lambda n: int(n.split("_")[1])):
                grp = data[name]
                if only_success and grp.attrs.get("success") is not True:
                    continue
                schema = _episode_schema(grp)
                if reference is None:
                    reference = schema
                    reference_loc = f"{path.name}/{name}"
                elif schema != reference:
                    raise SystemExit(
                        f"스키마 불일치: {reference_loc}는 {reference}였는데 "
                        f"{path.name}/{name}는 {schema}입니다.\n"
                        "LeRobotDataset은 변환 전체에 걸쳐 하나의 고정된 obs/action "
                        "구조가 필요합니다 -- 스키마가 다른 파일은 --repo-id/--root를 "
                        "바꿔 따로 변환하세요."
                    )
    if reference is None:
        raise SystemExit("변환할 에피소드가 없습니다 (--only-success로 전부 걸러졌을 수 있음)")
    return reference


def _build_features(schema: dict) -> tuple[dict, list[str]]:
    obs_keys = schema["obs_keys"]
    features = {}

    state_parts = []
    state_names = []
    if "joint_states" in obs_keys:
        state_parts.append("joint_states")
        state_names += [f"joint{i}.pos" for i in range(1, 8)]
    if "gripper_states" in obs_keys:
        state_parts.append("gripper_states")
        state_names += ["gripper.pos"]
    if state_parts:
        features["observation.state"] = {
            "dtype": "float32", "shape": (len(state_names),), "names": state_names,
        }

    if "agentview_rgb" in obs_keys:
        features["observation.images.agent"] = {
            "dtype": "video", "shape": (IMAGE_SIZE, IMAGE_SIZE, 3), "names": ["height", "width", "channel"],
        }
    if "eye_in_hand_rgb" in obs_keys:
        features["observation.images.wrist"] = {
            "dtype": "video", "shape": (IMAGE_SIZE, IMAGE_SIZE, 3), "names": ["height", "width", "channel"],
        }

    action_names = action_column_names(schema["action_space"])
    if schema["has_gripper"]:
        action_names = action_names + ["gripper"]
    features["action"] = {
        "dtype": "float32", "shape": (len(action_names),), "names": action_names,
    }
    return features, state_parts


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hdf5_paths", type=Path, nargs="+", help="<task>_demo.hdf5 파일들 (여러 개 가능)")
    p.add_argument("--repo-id", required=True, help="예: knu-physical-ai/fr3-libero-teleop-lerobot")
    p.add_argument("--root", type=Path, required=True, help="로컬에 LeRobotDataset을 만들 경로")
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--only-success", action="store_true", help="success=True인 에피소드만 포함")
    p.add_argument("--image-writer-threads", type=int, default=4)
    p.add_argument("--push", action="store_true", help="변환 후 바로 Hugging Face Hub에 업로드")
    p.add_argument("--private", action=argparse.BooleanOptionalAction, default=None, help="--push일 때만 적용")
    args = p.parse_args()

    schema = _scan_schema(args.hdf5_paths, args.only_success)
    features, state_parts = _build_features(schema)
    print(
        f"감지된 스키마: action_space={schema['action_space']!r} "
        f"(gripper {'포함' if schema['has_gripper'] else '제외'}), "
        f"obs={sorted(schema['obs_keys'])}"
    )
    print(f"features: {list(features.keys())}")

    ds = LeRobotDataset.create(
        repo_id=args.repo_id,
        fps=args.fps,
        root=args.root,
        robot_type="fr3_gello_real",
        features=features,
        use_videos=True,
        image_writer_processes=0,
        image_writer_threads=args.image_writer_threads,
    )

    has_agent = "observation.images.agent" in features
    has_wrist = "observation.images.wrist" in features

    n_episodes = 0
    n_skipped = 0
    for path in args.hdf5_paths:
        with h5py.File(path, "r") as f:
            task = _language_instruction(f)
            data = f["data"]
            demo_names = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
            print(f"{path.name}: task={task!r}, {len(demo_names)} episodes")
            for name in demo_names:
                grp = data[name]
                success = grp.attrs.get("success")
                if args.only_success and success is not True:
                    n_skipped += 1
                    continue
                obs = grp["obs"]
                if has_agent:
                    _check_image_shape(path, name, obs, "agentview_rgb")
                if has_wrist:
                    _check_image_shape(path, name, obs, "eye_in_hand_rgb")
                state_arrays = [obs[part][:] for part in state_parts]
                agent_rgb = obs["agentview_rgb"][:] if has_agent else None
                wrist_rgb = obs["eye_in_hand_rgb"][:] if has_wrist else None
                actions = grp["actions"][:]
                n = actions.shape[0]
                for t in range(n):
                    frame = {"action": actions[t].astype("float32"), "task": task}
                    if state_arrays:
                        frame["observation.state"] = np.concatenate(
                            [arr[t] for arr in state_arrays]
                        ).astype("float32")
                    if has_agent:
                        frame["observation.images.agent"] = agent_rgb[t]
                    if has_wrist:
                        frame["observation.images.wrist"] = wrist_rgb[t]
                    ds.add_frame(frame)
                ds.save_episode()
                n_episodes += 1
                print(f"  {name} ({n} frames, success={success}) converted")

    ds.finalize()
    print(f"\n완료: {n_episodes}개 에피소드 변환, {n_skipped}개 건너뜀 (--only-success) -> {args.root}")

    if args.push:
        print("Hugging Face Hub에 업로드 중...")
        ds.push_to_hub(private=args.private)
        print(f"완료: https://huggingface.co/datasets/{args.repo_id}")


if __name__ == "__main__":
    main()
