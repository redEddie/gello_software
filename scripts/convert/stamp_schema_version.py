#!/home/franka/lerobot-venv/bin/python
"""Write the current schema version into existing scene HDF5 files (issue #41).

Files collected before 2026-08-31 carry the old label ``dataset_version =
"scene-v1"``. Readers resolve that through an alias, so stamping is not
required for anything to work -- this exists so the files say out loud what
they are, which matters most for the copies that leave this machine (Hub,
backups) where our alias table is not present.

What it does per file: sets ``metadata.attrs["dataset_version"]`` and
``metadata.attrs["schema_version"]`` to the target version. Nothing else --
no episode is touched, no data is rewritten, and ``edit_count`` is left
alone on purpose (bumping it would force a full LeRobot rebuild, and no
episode changed).

It refuses to stamp a file whose fields do not actually match the target
version. The point of the label is that it is true; a file that is missing
a required field, or is a different shape than the version claims, is
reported and skipped rather than mislabelled.

Note on uploads: an attribute write changes the file's mtime, so the Hub
upload ledger (gello/data/hub_upload_state.py) will list every stamped file
as changed. Stamping all of them therefore queues all of them for re-upload.
See --help for --only-pending if that is not what you want.

Usage:
    python scripts/convert/stamp_schema_version.py ~/libero_datasets/scene_*.hdf5
    python scripts/convert/stamp_schema_version.py <dir> --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import h5py

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gello.data.dataset_schema import (  # noqa: E402
    SCHEMA_VERSION,
    normalize_schema_version,
    schema_required_fields,
)
from gello.scene.scene_format import EPISODE_GROUP_RE  # noqa: E402


def _mismatches(f: h5py.File, required: dict) -> list[str]:
    """이 파일이 정말 그 버전의 구성인가. 아니면 이유 목록."""
    out: list[str] = []
    meta = f.get("metadata")
    if meta is None:
        return ["metadata 그룹이 없다"]
    for k in required["metadata_attrs"]:
        if k not in meta.attrs:
            out.append(f"metadata.attrs[{k!r}] 없음")
    eps = [k for k in f.keys() if EPISODE_GROUP_RE.match(k)]
    if not eps:
        out.append("에피소드가 하나도 없다")
    for name in eps:
        grp = f[name]
        for ds in required["episode_datasets"]:
            if ds not in grp:
                out.append(f"{name}: {ds} 없음")
        obs = grp.get("obs")
        for ds in required["obs_datasets"]:
            if obs is None or ds not in obs:
                out.append(f"{name}: obs/{ds} 없음")
        for k in required["episode_attrs"]:
            if k not in grp.attrs:
                out.append(f"{name}: attrs[{k!r}] 없음")
        if len(out) > 20:
            out.append("... (이하 생략)")
            break
    return out


def stamp(path: Path, target: str, dry_run: bool) -> str:
    """한 파일을 검사하고(필요하면) 기록한다. 사람이 읽을 한 줄을 반환."""
    required = schema_required_fields(target)
    if required is None:
        return f"{path.name}: 대상 버전 {target} 의 필드 목록이 없다 -- 중단"
    with h5py.File(path, "r") as f:
        meta = f.get("metadata")
        if meta is None:
            return f"{path.name}: metadata 그룹이 없다 -- 건너뜀"
        current_raw = str(meta.attrs.get("dataset_version", ""))
        current = normalize_schema_version(current_raw)
        already = (current_raw == target
                   and str(meta.attrs.get("schema_version", "")) == target)
        if already:
            return f"{path.name}: 이미 {target} -- 변경 없음"
        if current != target:
            return (f"{path.name}: 파일이 주장하는 버전({current_raw!r} -> "
                    f"{current})이 대상({target})과 다르다 -- 건너뜀")
        problems = _mismatches(f, required)
    if problems:
        return (f"{path.name}: {target} 구성과 맞지 않아 건너뜀 -- "
                + "; ".join(problems[:3]))
    if dry_run:
        return f"{path.name}: {current_raw!r} -> {target} (예정)"
    with h5py.File(path, "a") as f:
        f["metadata"].attrs["dataset_version"] = target
        f["metadata"].attrs["schema_version"] = target
    with h5py.File(path, "r") as f:   # 쓴 대로 들어갔는지 되읽어 확인
        a = f["metadata"].attrs
        if str(a.get("dataset_version")) != target or \
                str(a.get("schema_version")) != target:
            return f"{path.name}: 기록 후 확인 실패 -- 수동 확인 필요"
    return f"{path.name}: {current_raw!r} -> {target} 기록됨"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", type=Path,
                    help="scene_*.hdf5 파일들, 또는 그것들이 든 디렉터리")
    ap.add_argument("--version", default=SCHEMA_VERSION,
                    help=f"기록할 버전 (기본: {SCHEMA_VERSION})")
    ap.add_argument("--dry-run", action="store_true",
                    help="무엇이 바뀔지만 보여주고 파일은 건드리지 않는다")
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        files.extend(sorted(p.glob("scene_*.hdf5")) if p.is_dir() else [p])
    if not files:
        print("대상 파일이 없습니다.")
        return 1

    print(f"대상 {len(files)}개, 기록할 버전: {args.version}"
          + (" (dry-run)" if args.dry_run else ""))
    changed = skipped = 0
    for p in files:
        line = stamp(p, args.version, args.dry_run)
        print("  " + line)
        if "기록됨" in line or "예정" in line:
            changed += 1
        elif "건너뜀" in line or "실패" in line:
            skipped += 1
    print(f"\n{changed}개 {'변경 예정' if args.dry_run else '기록'}, {skipped}개 건너뜀")
    if changed and not args.dry_run:
        print("주의: mtime 이 바뀌었으므로 업로드 장부가 이 파일들을 "
              "'변경됨'으로 판정합니다 (다음 업로드에 포함됨).")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
