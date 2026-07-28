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

Schema (matches record_dataset.py's hw_to_dataset_features naming):
    observation.state          (8,) float32  -- 7 joint pos + gripper pos
    observation.images.agent   video          -- from agentview_rgb
    observation.images.wrist   video          -- from eye_in_hand_rgb
    action                     (7,) float32  -- OSC_POSE-style EE delta,
                                                 copied as-is from the HDF5
                                                 (see gello/libero_format.py
                                                 for exactly how it's defined)

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
from pathlib import Path

import h5py
import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset


def _language_instruction(f: h5py.File) -> str:
    info = json.loads(f["data"].attrs["problem_info"])
    lang = info.get("language_instruction", "")
    if len(lang) >= 2 and lang.startswith('"') and lang.endswith('"'):
        lang = lang[1:-1]
    return lang


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

    features = {
        "observation.state": {
            "dtype": "float32", "shape": (8,),
            "names": ["joint1.pos", "joint2.pos", "joint3.pos", "joint4.pos",
                      "joint5.pos", "joint6.pos", "joint7.pos", "gripper.pos"],
        },
        "observation.images.agent": {
            "dtype": "video", "shape": (256, 256, 3), "names": ["height", "width", "channel"],
        },
        "observation.images.wrist": {
            "dtype": "video", "shape": (256, 256, 3), "names": ["height", "width", "channel"],
        },
        "action": {
            "dtype": "float32", "shape": (7,),
            "names": ["dx", "dy", "dz", "d_axis_angle_x", "d_axis_angle_y", "d_axis_angle_z", "gripper"],
        },
    }

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
                joint_states = obs["joint_states"][:]
                gripper_states = obs["gripper_states"][:]
                agent_rgb = obs["agentview_rgb"][:]
                wrist_rgb = obs["eye_in_hand_rgb"][:]
                actions = grp["actions"][:]
                n = joint_states.shape[0]
                for t in range(n):
                    frame = {
                        "observation.state": np.concatenate(
                            [joint_states[t], gripper_states[t]]
                        ).astype("float32"),
                        "observation.images.agent": agent_rgb[t],
                        "observation.images.wrist": wrist_rgb[t],
                        "action": actions[t].astype("float32"),
                        "task": task,
                    }
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
