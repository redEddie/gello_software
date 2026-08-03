"""Repack a LIBERO-format HDF5: reclaim deleted space and (re)compress images.

Why
---
Deleting a ``demo_N`` group with h5py frees the space *inside* the file for
reuse but never shrinks the file on disk -- HDF5 has no hole-punching. A
dataset that has had episodes curated out therefore keeps paying for them.
Rewriting the file into a fresh one is the only way to actually reclaim it,
which is what ``h5repack`` does; that tool is not installed here, so this is
the same operation in h5py (no external dependency).

It also re-applies compression, which is where most of the win is: the image
datasets dominate the file, and the collector writes them with ``lzf`` (fast,
chosen so the background save never stalls the operator). Once collection is
over that trade-off no longer applies, so gzip can be spent instead.

Safety
------
The rewrite goes to a sibling temp file and is verified (every dataset's
shape/dtype and a content checksum are compared against the source) before
the original is replaced. A failure anywhere leaves the original untouched.

Usage:
    python scripts/repack_hdf5.py <file.hdf5> [more.hdf5 ...]
        [--compression gzip|lzf|none] [--level 4] [--dry-run] [--keep-original]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

# Images are the only datasets big enough for compression to matter; the
# rest are (T, <=8) float arrays where chunking overhead can exceed the win.
_MIN_COMPRESS_BYTES = 1 << 16  # 64 KiB


def _copy_attrs(src, dst) -> None:
    for k, v in src.attrs.items():
        dst.attrs[k] = v


def _checksum(ds: h5py.Dataset) -> tuple:
    """Cheap content fingerprint: shape, dtype, and a strided sample sum."""
    a = ds[()]
    arr = np.asarray(a)
    if arr.dtype.kind in "fc":
        flat = arr.ravel()
        s = float(np.nansum(flat[::max(1, flat.size // 4096)]))
    elif arr.dtype.kind in "iub":
        flat = arr.ravel()
        s = int(flat[::max(1, flat.size // 4096)].astype(np.int64).sum())
    else:
        s = 0
    return (arr.shape, str(arr.dtype), s)


def _walk_datasets(g, prefix=""):
    for key in g:
        item = g[key]
        path = f"{prefix}/{key}"
        if isinstance(item, h5py.Group):
            yield from _walk_datasets(item, path)
        else:
            yield path, item


class _Progress:
    """Byte-based progress, throttled so a multi-GB file doesn't spam the log.

    stdout is a pipe when the GUI runs this (QProcess), so Python block-buffers
    it -- without flush=True nothing appears until the process exits, which is
    exactly the "no feedback for several minutes" this exists to fix.
    """

    def __init__(self, total: int, every_s: float = 2.0) -> None:
        self.total = max(total, 1)
        self.done = 0
        self.t0 = time.monotonic()
        self._last = 0.0
        self._every = every_s

    def add(self, n: int) -> None:
        self.done += n
        now = time.monotonic()
        el = now - self.t0
        # Skip the first tick: with ~no bytes and ~no elapsed time behind it,
        # the rate and ETA are meaningless (and alarmingly large).
        if self.done < self.total * 0.02 or el < self._every:
            return
        if now - self._last < self._every:
            return
        self._last = now
        pct = 100.0 * self.done / self.total
        rate = self.done / el / 1e6 if el > 0 else 0.0
        eta = (self.total - self.done) / (self.done / el) if self.done and el > 0 else 0.0
        print(f"  진행 {pct:5.1f}%  ({self.done/1e6:.0f}/{self.total/1e6:.0f} MB)  "
              f"{rate:.0f} MB/s  경과 {el:.0f}s  남은시간 ~{eta:.0f}s", flush=True)


def _total_bytes(path: Path) -> int:
    with h5py.File(path, "r") as f:
        return sum(int(ds.size) * ds.dtype.itemsize for _, ds in _walk_datasets(f))


def _rewrite(src_path: Path, dst_path: Path, compression: str, level: int,
             progress: "Optional[_Progress]" = None) -> None:
    with h5py.File(src_path, "r") as src, h5py.File(dst_path, "w") as dst:
        _copy_attrs(src, dst)

        def rec(sg, dg):
            _copy_attrs(sg, dg)
            for key in sg:
                item = sg[key]
                if isinstance(item, h5py.Group):
                    rec(item, dg.create_group(key))
                    continue
                data = item[()]
                nbytes = getattr(data, "nbytes", 0)
                kw = {}
                if compression != "none" and nbytes >= _MIN_COMPRESS_BYTES:
                    if compression == "gzip":
                        kw = {"compression": "gzip", "compression_opts": level}
                    else:
                        kw = {"compression": compression}
                    kw["shuffle"] = True
                out = dg.create_dataset(key, data=data, **kw)
                _copy_attrs(item, out)
                if progress is not None:
                    progress.add(nbytes)

        rec(src, dst)


def _verify(src_path: Path, dst_path: Path) -> None:
    with h5py.File(src_path, "r") as a, h5py.File(dst_path, "r") as b:
        sa = dict(_walk_datasets(a))
        sb = dict(_walk_datasets(b))
        if set(sa) != set(sb):
            missing = (set(sa) ^ set(sb))
            raise RuntimeError(f"dataset set differs: {sorted(missing)[:5]}")
        for path, ds in sa.items():
            ca, cb = _checksum(ds), _checksum(sb[path])
            if ca != cb:
                raise RuntimeError(f"content mismatch at {path}: {ca} vs {cb}")
        if dict(a.attrs) .keys() != dict(b.attrs).keys():
            raise RuntimeError("root attrs differ")


def process(path: Path, compression: str, level: int, dry_run: bool,
            keep_original: bool) -> bool:
    if not path.exists():
        print(f"  [skip] {path}: 파일 없음", flush=True)
        return False
    before = path.stat().st_size
    tmp = path.with_suffix(path.suffix + ".repack.tmp")
    t_start = time.monotonic()
    print(f"\n=== {path.name}", flush=True)
    print(f"  현재 크기 : {before/1e6:>9.1f} MB", flush=True)
    try:
        total = _total_bytes(path)
        print(f"  압축 해제 기준 {total/1e6:.0f} MB 처리 예정", flush=True)
        prog = _Progress(total)
        _rewrite(path, tmp, compression, level, progress=prog)
        t_written = time.monotonic() - t_start
        print(f"  쓰기 완료 ({t_written:.1f}s) -- 내용 검증 중...", flush=True)
        _verify(path, tmp)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        print(f"  [실패] {type(e).__name__}: {e}", flush=True)
        print("  원본은 그대로입니다.", flush=True)
        return False
    elapsed = time.monotonic() - t_start
    after = tmp.stat().st_size
    saved = before - after
    print(f"  재압축 후 : {after/1e6:>9.1f} MB  "
          f"({100*after/before:.1f}%, {saved/1e6:+.1f} MB)  소요 {elapsed:.1f}s", flush=True)
    if dry_run:
        tmp.unlink(missing_ok=True)
        print("  [dry-run] 원본 유지, 임시 파일 삭제", flush=True)
        return True
    if saved <= 0:
        tmp.unlink(missing_ok=True)
        print("  줄어들지 않아 교체하지 않았습니다 (원본 유지)", flush=True)
        return True
    if keep_original:
        backup = path.with_suffix(path.suffix + ".orig")
        shutil.move(str(path), str(backup))
        print(f"  원본 보관: {backup.name}", flush=True)
    else:
        path.unlink()
    shutil.move(str(tmp), str(path))
    # Record that this file has been repacked, so a later run (or the GUI's
    # selection list) can skip it without inferring from the compressor.
    try:
        with h5py.File(path, "a") as f:
            f["data"].attrs["repacked"] = (
                f"{time.strftime('%Y-%m-%d %H:%M')} {compression}"
                + (f"-{level}" if compression == "gzip" else "")
            )
    except Exception as e:  # noqa: BLE001
        print(f"  (경고) repacked 표시 기록 실패: {e}", flush=True)
    print("  교체 완료", flush=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--compression", default="gzip", choices=["gzip", "lzf", "none"],
                    help="이미지 재압축 방식 (기본 gzip -- 수집 후엔 속도보다 용량)")
    ap.add_argument("--level", type=int, default=4,
                    help="gzip 레벨 1-9 (기본 4; 높을수록 느리고 조금 더 작음)")
    ap.add_argument("--dry-run", action="store_true",
                    help="줄어드는 크기만 확인하고 원본은 건드리지 않음")
    ap.add_argument("--keep-original", action="store_true",
                    help="교체 대신 원본을 .orig 로 남김")
    ap.add_argument("--skip-repacked", action="store_true",
                    help="이미 재압축된 파일은 건너뜀 (repacked 표시 또는 이미지가 gzip)")
    args = ap.parse_args()

    total_before = total_after = 0
    ok = True
    t_all = time.monotonic()
    for p in args.files:
        if args.skip_repacked:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from gello.libero_format import hdf5_repack_status

            st = hdf5_repack_status(p)
            if st["repacked"]:
                print(f"\n=== {p.name}\n  [건너뜀] 이미 재압축됨 "
                      f"(압축={st['compression']}"
                      + (f", {st['marker']}" if st["marker"] else "") + ")", flush=True)
                continue
        b = p.stat().st_size if p.exists() else 0
        ok &= process(p, args.compression, args.level, args.dry_run, args.keep_original)
        a = p.stat().st_size if p.exists() else 0
        total_before += b
        total_after += a
    dt = time.monotonic() - t_all
    if len(args.files) > 1:
        print(f"\n합계: {total_before/1e6:.1f} MB -> {total_after/1e6:.1f} MB "
              f"({total_before and 100*total_after/total_before:.1f}%), "
              f"총 소요 {dt:.1f}s", flush=True)
    else:
        print(f"\n총 소요 {dt:.1f}s", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
