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

--resume: TRUE INCREMENTAL UPLOAD (verified against lerobot's actual code,
not just its docstring)
--------------------------------------------------------------------------
Without --resume, every run rebuilds the whole LeRobotDataset from scratch
via LeRobotDataset.create() -- converting/re-encoding *all* task files again
just to add one new task gets more wasteful as the dataset grows.
LeRobotDataset.resume(repo_id, root) fixes this, but three things about it
are non-obvious enough that they were checked directly against
lerobot/datasets/{lerobot_dataset,dataset_metadata,dataset_writer}.py
before wiring it in here (not assumed from the docstring):

1. What resume() actually downloads: ONLY meta/ (info.json, stats.json,
   tasks.parquet, episodes/*.parquet) -- never data/ or videos/. This is
   safe (not "silently incomplete") because the first episode saved after
   resume() *always* starts a brand-new chunk/file for both the parquet and
   the video (dataset_writer.py's _save_episode_data/_save_episode_video
   unconditionally call update_chunk_file_indices() the first time), so it
   never needs to open or append into an old chunk file it doesn't have
   locally. Verified locally (no network) by resuming into the SAME root a
   create()'d dataset was built in, adding a new task's episodes, and
   hashing every file before/after: only meta/info.json, meta/stats.json,
   and meta/tasks.parquet changed (all KB-sized bookkeeping); every
   data/*.parquet and videos/*.mp4 file was byte-identical. push_to_hub()
   (huggingface_hub's upload_folder) only uploads new/changed local files
   and does not delete untouched remote ones, so a --resume + --push cycle
   only ever transfers the small metadata + the new task's own chunk files
   -- old tasks' videos are never re-encoded or re-uploaded.

2. Re-curating an ALREADY-PUSHED task (deleting/relabeling episodes in the
   source .hdf5 after that batch is already on the Hub): there is no
   supported way to edit or remove an individual episode from a published
   LeRobotDataset -- resume() only ever appends. The .hdf5 files remain the
   editable source of truth; the Hub LeRobotDataset should be treated as
   append-only once pushed. Practical rule: finish curating a task's .hdf5
   (delete bad takes) *before* the first --resume --push for it, the same
   "convert after you've deleted what you don't want" rule this script
   already followed pre-resume.

3. Concurrent --resume + --push from two people is NOT safe. Both sides
   compute their next chunk/file index from whatever meta/ they each
   downloaded; if two people resume from the same base state, both compute
   the SAME next chunk/file path and each push overwrites the other's file
   at that path with their own content (huggingface_hub's commit API has no
   compare-and-swap against a stale base revision) -- silent data loss for
   whoever pushes second, not a merge conflict either side would notice.
   There is no locking here (out of scope) -- coordinate so only one person
   resume+pushes to a given --repo-id at a time; if you weren't first,
   re-run --resume against the now-current Hub state before pushing.

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

    # Later: add a new task's episodes to that same Hub dataset, without
    # re-converting/re-uploading the earlier tasks (see --resume above):
    python scripts/convert_libero_to_lerobot.py \
        /home/franka/libero_datasets/pick_up_the_blue_cup_demo.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop-lerobot \
        --root /home/franka/lerobot_upload \
        --resume --push --private=false
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
    has_gripper = action_dim == len(base_cols) + 1
    # Episodes written after the schema dialog's per-column name overrides
    # were added carry the ACTUAL names used (see gello/libero_format.py's
    # save_episode); older episodes predate the attr and never had
    # overrides, so the built-in names are exactly right for them too.
    raw_names = grp.attrs.get("action_column_names")
    if raw_names:
        action_names = tuple(json.loads(raw_names))
    else:
        action_names = tuple(base_cols) + (("gripper.pos",) if has_gripper else ())
    return {
        "obs_keys": obs_keys,
        "action_space": action_space,
        "has_gripper": has_gripper,
        "action_names": action_names,
        # derive_commanded_ee_actions.py가 추가하는 파생 액션 (있는 파일만)
        "has_actions_ee": "actions_ee" in grp,
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

    # GELLO leader 명령 스트림 (commanded-ee-actions 브랜치 수집분).
    # 차원 이름을 observation.state와 동일하게 맞춰 시각화에서 실측 vs 명령이
    # 같은 이름으로 나란히 비교되게 한다.
    cmd_parts = []
    cmd_names = []
    if "commanded_joint_states" in obs_keys:
        cmd_parts.append("commanded_joint_states")
        cmd_names += [f"joint{i}.pos" for i in range(1, 8)]
    if "commanded_gripper_states" in obs_keys:
        cmd_parts.append("commanded_gripper_states")
        cmd_names += ["gripper.pos"]
    if cmd_parts:
        features["observation.commanded_state"] = {
            "dtype": "float32", "shape": (len(cmd_names),), "names": cmd_names,
        }

    action_names = list(schema["action_names"])
    features["action"] = {
        "dtype": "float32", "shape": (len(action_names),), "names": action_names,
    }
    if schema["has_actions_ee"]:
        features["action_ee"] = {
            "dtype": "float32", "shape": (7,),
            "names": ["dx", "dy", "dz", "d_axis_x", "d_axis_y", "d_axis_z", "gripper.pos"],
        }
    return features, state_parts, cmd_parts


# Only the features this script itself declares -- not the bookkeeping keys
# (timestamp, frame_index, episode_index, index, task_index) LeRobotDataset
# adds on its own regardless of what's passed to create().
_CHECKED_FEATURE_KEYS = ("observation.state", "observation.commanded_state",
                         "observation.images.agent", "observation.images.wrist",
                         "action", "action_ee")


def _check_resume_compatible(remote_features: dict, local_features: dict) -> None:
    """--resume appends into an existing Hub dataset, which already has ONE
    fixed features dict -- LeRobotDataset.resume() doesn't take a `features`
    argument to re-declare it, so a mismatch would only surface later, mid-
    conversion, as a validate_frame() crash inside add_frame(). Catch it here
    instead, before any conversion work starts."""
    for key in _CHECKED_FEATURE_KEYS:
        in_remote, in_local = key in remote_features, key in local_features
        if in_remote != in_local:
            raise SystemExit(
                f"--resume 대상 Hub 데이터셋과 스키마가 다릅니다: '{key}'가 "
                f"{'기존 데이터셋에는 있는데 지금 변환할 파일들에는 없음' if in_remote else '지금 변환할 파일들에는 있는데 기존 데이터셋에는 없음'}.\n"
                "다른 스키마로 이어붙이면 Hub 데이터셋이 망가집니다 -- --repo-id를 바꿔 별도 데이터셋으로 변환하세요."
            )
        if not in_remote:
            continue
        r, local = remote_features[key], local_features[key]
        if (
            r["dtype"] != local["dtype"]
            or tuple(r["shape"]) != tuple(local["shape"])
            or list(r.get("names") or []) != list(local.get("names") or [])
        ):
            raise SystemExit(
                f"--resume 대상 Hub 데이터셋과 '{key}' 스키마가 다릅니다:\n"
                f"  기존: dtype={r['dtype']} shape={tuple(r['shape'])} names={r.get('names')}\n"
                f"  지금: dtype={local['dtype']} shape={tuple(local['shape'])} names={local.get('names')}\n"
                "다른 스키마로 이어붙이면 Hub 데이터셋이 망가집니다 -- --repo-id를 바꿔 별도 데이터셋으로 변환하세요."
            )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hdf5_paths", type=Path, nargs="+", help="<task>_demo.hdf5 파일들 (여러 개 가능)")
    p.add_argument("--repo-id", required=True, help="예: knu-physical-ai/fr3-libero-teleop-lerobot")
    p.add_argument("--root", type=Path, required=True, help="로컬에 LeRobotDataset을 만들 경로")
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--only-success", action="store_true", help="success=True인 에피소드만 포함")
    p.add_argument("--image-writer-threads", type=int, default=4)
    p.add_argument(
        "--resume",
        action="store_true",
        help=(
            "처음부터 새로 만드는 대신 --repo-id의 기존 Hub 데이터셋에 이 파일들의 에피소드만 "
            "이어붙임 (기존 task는 재변환/재업로드하지 않음). 동시에 두 명이 같은 --repo-id에 "
            "--resume --push 하지 말 것 -- 이 파일 상단 docstring 3번 참고"
        ),
    )
    p.add_argument("--push", action="store_true", help="변환 후 바로 Hugging Face Hub에 업로드")
    p.add_argument("--private", action=argparse.BooleanOptionalAction, default=None, help="--push일 때만 적용")
    args = p.parse_args()

    schema = _scan_schema(args.hdf5_paths, args.only_success)
    features, state_parts, cmd_parts = _build_features(schema)
    print(
        f"감지된 스키마: action_space={schema['action_space']!r} "
        f"(gripper {'포함' if schema['has_gripper'] else '제외'}), "
        f"obs={sorted(schema['obs_keys'])}"
    )
    print(f"features: {list(features.keys())}")

    if args.resume:
        ds = LeRobotDataset.resume(
            repo_id=args.repo_id,
            root=args.root,
            image_writer_processes=0,
            image_writer_threads=args.image_writer_threads,
        )
        _check_resume_compatible(ds.meta.features, features)
        print(
            f"기존 데이터셋에 이어붙임: 현재 {ds.meta.total_episodes}개 에피소드, "
            f"{ds.meta.total_frames} 프레임, task {ds.meta.total_tasks}개"
        )
    else:
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
                cmd_arrays = [obs[part][:] for part in cmd_parts]
                agent_rgb = obs["agentview_rgb"][:] if has_agent else None
                wrist_rgb = obs["eye_in_hand_rgb"][:] if has_wrist else None
                actions = grp["actions"][:]
                actions_ee = grp["actions_ee"][:] if schema["has_actions_ee"] else None
                n = actions.shape[0]
                for t in range(n):
                    frame = {"action": actions[t].astype("float32"), "task": task}
                    if state_arrays:
                        frame["observation.state"] = np.concatenate(
                            [arr[t] for arr in state_arrays]
                        ).astype("float32")
                    if cmd_arrays:
                        frame["observation.commanded_state"] = np.concatenate(
                            [arr[t] for arr in cmd_arrays]
                        ).astype("float32")
                    if actions_ee is not None:
                        frame["action_ee"] = actions_ee[t].astype("float32")
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
