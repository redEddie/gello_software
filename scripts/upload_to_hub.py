"""Upload raw LIBERO-format .hdf5 files to a Hugging Face Hub dataset repo.

Thin wrapper around huggingface_hub.HfApi so the GUI's "HDF5 업로드..." button
(gello/gui_widgets.py's HdfUploadDialog, wired up by
experiments/collect_workspace.py's _on_hdf5_upload)
can run this as a subprocess instead of blocking the Qt event loop on the
upload. This is the raw-format half of the dual upload described in
~/huggingface_upload_process.md -- the converted half is
scripts/convert_libero_to_lerobot.py's --push.

Re-uploading to the SAME --path-in-repo already overwrites the previous
content wholesale (upload_file() replaces whatever's at that path in a new
commit) -- since the local .hdf5 keeps accumulating episodes across
sessions, re-running this with the same path naturally makes the Hub copy
whole again. There is no partial/byte-level append.

--delete-existing is for a DIFFERENT case: the file on the Hub was uploaded
under an old name (e.g. an earlier naming convention) and you want that old
entry gone as part of this upload, not left behind as a stale duplicate.
Combine with --old-path-in-repo when the stale name differs from the new
--path-in-repo; otherwise it just deletes-then-recreates the same path
(equivalent to the implicit overwrite above, just explicit about it).

Usage:
    python scripts/upload_to_hub.py \
        /home/franka/libero_datasets/pick_up_the_red_block_demo.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop \
        --path-in-repo pick_up_the_red_block_demo_gibeom25_20260730.hdf5

    # Replace a stale, differently-named upload of the same task:
    python scripts/upload_to_hub.py \
        /home/franka/libero_datasets/pick_up_the_red_block_demo.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop \
        --path-in-repo pick_up_the_red_block_demo_gibeom25_20260730.hdf5 \
        --delete-existing --old-path-in-repo pick_up_the_red_block_demo_old.hdf5
"""

import argparse
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.errors import BadRequestError, EntryNotFoundError


def _commit_message(local: Path) -> str:
    """Hub 커밋 메시지 -- 파일이 무엇을 담고 있는지 이력에서 읽히게.
    scene 파일: 'scene_001.hdf5: S001 · 20 ep (I000 10, I001 10) · 2.1 GB'
    legacy: 'x_demo.hdf5: 11 ep · 1.3 GB'. 읽기 실패(잠금 등)면 이름·크기만."""
    size_gb = local.stat().st_size / 1e9
    try:
        import h5py

        with h5py.File(local, "r") as f:
            if "metadata" in f:
                sid = str(f["metadata"].attrs.get("scene_id", "?"))
                per: dict = {}
                for k in f.keys():
                    if k.startswith("episode_"):
                        iid = str(f[k].attrs.get("instruction_id", "?"))
                        per[iid] = per.get(iid, 0) + 1
                n = sum(per.values())
                slots = ", ".join(f"{k} {v}" for k, v in sorted(per.items()))
                return f"{local.name}: {sid} · {n} ep ({slots}) · {size_gb:.1f} GB"
            if "data" in f:
                return f"{local.name}: {len(f['data'])} ep · {size_gb:.1f} GB"
    except Exception:  # noqa: BLE001
        pass
    return f"{local.name}: {size_gb:.1f} GB"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("local_file", type=Path, nargs="+", help="업로드할 .hdf5 파일 (여러 개 가능)")
    p.add_argument("--repo-id", required=True, help="예: knu-physical-ai/fr3-libero-teleop")
    p.add_argument(
        "--path-in-repo", default=None,
        help="repo 안에서의 파일 이름 (기본: 로컬 파일 이름 그대로). 파일을 여러 개 준 "
             "경우에는 이름이 아니라 폴더로 취급한다 -- 이름 하나로 여러 파일을 "
             "올리면 마지막 것만 남기 때문",
    )
    p.add_argument("--private", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument(
        "--delete-existing", action="store_true",
        help="업로드 전에 Hub에 있는 기존 파일을 먼저 삭제 (기본은 --path-in-repo와 동일한 경로, "
        "--old-path-in-repo로 다른 이름 지정 가능)",
    )
    p.add_argument(
        "--old-path-in-repo", default=None,
        help="--delete-existing과 함께 사용 -- 삭제할 파일이 --path-in-repo와 다른 이름일 때 지정 "
        "(기본: --path-in-repo와 동일)",
    )
    args = p.parse_args()

    missing = [f for f in args.local_file if not f.is_file()]
    if missing:
        raise SystemExit("파일 없음: " + ", ".join(str(f) for f in missing))
    multi = len(args.local_file) > 1
    if multi and args.old_path_in_repo:
        raise SystemExit("--old-path-in-repo 는 파일 하나일 때만 쓸 수 있습니다.")

    api = HfApi()
    # exist_ok=True: repeat uploads to an already-existing repo (e.g. a
    # second task file for the same dataset) shouldn't fail here.
    api.create_repo(repo_id=args.repo_id, repo_type="dataset", private=bool(args.private), exist_ok=True)

    total = len(args.local_file)
    for i, local in enumerate(args.local_file, 1):
        if multi:
            # 여러 개일 때 --path-in-repo 는 폴더다. 이름으로 쓰면 전부 같은
            # 경로에 덮어써져 마지막 하나만 남는다.
            prefix = args.path_in_repo.rstrip("/") + "/" if args.path_in_repo else ""
            path_in_repo = prefix + local.name
        else:
            path_in_repo = args.path_in_repo or local.name

        if args.delete_existing:
            old_path = (args.old_path_in_repo or path_in_repo) if not multi else path_in_repo
            try:
                api.delete_file(path_in_repo=old_path, repo_id=args.repo_id, repo_type="dataset")
                print(f"기존 파일 삭제: {args.repo_id}/{old_path}", flush=True)
            except EntryNotFoundError:
                print(f"기존 파일 없음 (건너뜀): {args.repo_id}/{old_path}", flush=True)

        size_mb = local.stat().st_size / 1e6
        print(f"[{i}/{total}] 업로드 중: {local.name} ({size_mb:,.0f} MB) "
              f"-> {args.repo_id}/{path_in_repo}", flush=True)
        try:
            api.upload_file(
                path_or_fileobj=str(local),
                path_in_repo=path_in_repo,
                repo_id=args.repo_id,
                repo_type="dataset",
                commit_message=_commit_message(local),
            )
        except BadRequestError as e:
            # 'Invalid file change' 의 대표적인 원인: 만들려는 폴더와 같은 이름의
            # 파일이 이미 repo에 있다. Hub은 이유를 말해주지 않으므로 여기서
            # 확인해 알려준다 -- 1.3GB를 다 올린 뒤 원인 모를 400을 보는 것보다
            # 낫다.
            if "Invalid file change" in str(e) and "/" in path_in_repo:
                folder = path_in_repo.split("/", 1)[0]
                clash = any(s.rfilename == folder
                            for s in api.dataset_info(args.repo_id).siblings)
                if clash:
                    raise SystemExit(
                        f"업로드 거절됨: repo에 '{folder}' 라는 이름의 **파일**이 이미 있어서\n"
                        f"같은 이름의 폴더('{path_in_repo}')를 만들 수 없습니다.\n"
                        f"  - 그 파일을 지우거나(Hub 웹에서 삭제), \n"
                        f"  - 'Repo 안 폴더'를 비우거나 다른 이름으로 바꾸세요."
                    ) from e
            raise
        print(f"[{i}/{total}] 완료: {local.name}", flush=True)

    print(f"전체 완료 ({total}개): https://huggingface.co/datasets/{args.repo_id}", flush=True)


if __name__ == "__main__":
    main()
