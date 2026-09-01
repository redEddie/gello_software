"""LIBERO 포맷 <task>_demo.hdf5의 에피소드를 mp4로 뽑아서 빠르게 눈으로 확인한다.

agentview_rgb / eye_in_hand_rgb를 좌우로 붙여서 한 파일에 담는다. HF Dataset
Viewer는 hdf5를 못 읽으므로, 업로드 전/후 검수용으로 로컬에서 쓴다.

Usage:
    python scripts/visualize_libero_demo.py /home/franka/libero_datasets/<task>_demo.hdf5
    python scripts/visualize_libero_demo.py <path> --demo demo_3
    python scripts/visualize_libero_demo.py <path> --out-dir /tmp/check --fps 20
"""

import argparse
from pathlib import Path

import cv2
import h5py
import numpy as np

from gello.data.dataset_schema import OBS_AGENTVIEW_RGB, OBS_EYE_IN_HAND_RGB


def export_episode(grp: h5py.Group, out_path: Path, fps: int) -> None:
    agent = grp["obs"][OBS_AGENTVIEW_RGB]
    wrist = grp["obs"][OBS_EYE_IN_HAND_RGB]
    n, h, w, _ = agent.shape
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w * 2, h))
    try:
        for i in range(n):
            frame = np.concatenate([agent[i], wrist[i]], axis=1)
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hdf5_path", type=Path, help="<task>_demo.hdf5 경로")
    p.add_argument("--demo", default=None, help="특정 에피소드만 (예: demo_3). 기본: 전부")
    p.add_argument(
        "--out-dir", type=Path, default=None,
        help="기본: hdf5 파일 옆에 <stem>_videos/ 생성",
    )
    p.add_argument("--fps", type=int, default=20, help="수집 시 fps와 맞추는 게 자연스럽다 (기본 20)")
    args = p.parse_args()

    out_dir = args.out_dir or args.hdf5_path.parent / f"{args.hdf5_path.stem}_videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    with h5py.File(args.hdf5_path, "r") as f:
        data = f["data"]
        names = (
            [args.demo] if args.demo
            else sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
        )
        for name in names:
            grp = data[name]
            success = grp.attrs.get("success")
            tag = "success" if success else ("fail" if success is False else "unlabeled")
            n_samples = int(grp.attrs.get("num_samples", grp["actions"].shape[0]))
            out_path = out_dir / f"{name}_{tag}.mp4"
            export_episode(grp, out_path, args.fps)
            print(f"{name} ({n_samples} frames, {tag}) -> {out_path}")

    print(f"\n완료: {out_dir}")


if __name__ == "__main__":
    main()
