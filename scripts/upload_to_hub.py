"""Upload a single raw LIBERO-format .hdf5 file to a Hugging Face Hub dataset repo.

Thin wrapper around huggingface_hub.HfApi so the GUI's "HDF5 업로드..." button
(see experiments/collect_libero_gui.py's HdfUploadDialog/_open_hdf5_upload)
can run this as a subprocess instead of blocking the Qt event loop on the
upload. This is the raw-format half of the dual upload described in
~/huggingface_upload_process.md -- the converted half is
scripts/convert_libero_to_lerobot.py's --push.

Usage:
    python scripts/upload_to_hub.py \
        /home/franka/libero_datasets/pick_up_the_red_block_demo.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop \
        --path-in-repo pick_up_the_red_block_demo_gibeom25_20260730.hdf5
"""

import argparse
from pathlib import Path

from huggingface_hub import HfApi


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("local_file", type=Path, help="업로드할 .hdf5 파일")
    p.add_argument("--repo-id", required=True, help="예: knu-physical-ai/fr3-libero-teleop")
    p.add_argument(
        "--path-in-repo", default=None,
        help="repo 안에서의 파일 이름 (기본: 로컬 파일 이름 그대로)",
    )
    p.add_argument("--private", action=argparse.BooleanOptionalAction, default=None)
    args = p.parse_args()

    if not args.local_file.is_file():
        raise SystemExit(f"파일 없음: {args.local_file}")

    path_in_repo = args.path_in_repo or args.local_file.name
    api = HfApi()
    # exist_ok=True: repeat uploads to an already-existing repo (e.g. a
    # second task file for the same dataset) shouldn't fail here.
    api.create_repo(repo_id=args.repo_id, repo_type="dataset", private=bool(args.private), exist_ok=True)
    print(f"업로드 중: {args.local_file} -> {args.repo_id}/{path_in_repo}")
    api.upload_file(
        path_or_fileobj=str(args.local_file),
        path_in_repo=path_in_repo,
        repo_id=args.repo_id,
        repo_type="dataset",
    )
    print(f"완료: https://huggingface.co/datasets/{args.repo_id}")


if __name__ == "__main__":
    main()
