#!/usr/bin/env python3
"""Curate the collected .hdf5 files, then rebuild their gripper channel.

Two passes, in this order, over every `*_demo.hdf5` in the data root:

1. **Curate.** Drop episodes that are not worth keeping, so the synthesis pass
   does not spend effort on takes that are leaving anyway.

   - gripper strokes != 2. A pick-and-place closes once and opens once;
     anything else is a regrasp or a fumble.
   - within each task, keep the best `--keep` fraction by max |Δa|.

   The quota is **per task, not global**, and that is not a stylistic choice.
   max |Δa| correlates +0.47 with how far a task has to reach, so one global
   threshold deletes by task rather than by quality: at max |Δa| <= 0.08 the
   `small green bowl` task (13.3 rad of travel) loses 14 of 14 while
   `blue cup -> blue bowl` (7.9 rad) keeps 20 of 37.

2. **Synthesise.** Replace `obs/gripper_states` with the ramp the fingers
   actually travelled (see `gello/gripper_synth.py` for where the model comes
   from and how well it fits). Written in place -- the column keeps its shape,
   dtype and lack of compression, so no dataset is recreated.

This is destructive and there is no undo. Run with `--dry-run` first; it prints
the full plan and touches nothing.

    python3 scripts/curate_and_synth.py --dry-run
    python3 scripts/curate_and_synth.py

Excluded by default: `test_demo.hdf5` (the ground-truth grasps the synthesis
model was fitted and validated against -- rebuilding those would destroy the
only measured reference) and `open_the_top_drawer_demo.hdf5` (a different
manipulation whose gripper pattern the model was not checked against).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gello.data.gripper_synth import synth_gripper_states  # noqa: E402
from gello.data.libero_format import renumber_episodes  # noqa: E402

ARM_DIMS = 7
GRIPPER_STEP = 0.05
EXCLUDE = ("test_demo.hdf5", "open_the_top_drawer_demo.hdf5")


def task_of(data) -> str:
    # language_instruction 자체가 다시 JSON 문자열이라 두 번 푼다 -- 순서를
    # 바꾸면 조용히 "?" 가 되고, task 그룹이 하나로 뭉쳐 쿼터가 무너진다.
    info = data.attrs.get("problem_info")
    try:
        return json.loads(json.loads(info)["language_instruction"])
    except Exception:  # noqa: BLE001
        return "?"


def scan(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.glob("*_demo.hdf5")):
        if p.name in EXCLUDE:
            continue
        with h5py.File(p, "r") as f:
            task = task_of(f["data"])
            for name in sorted(f["data"], key=lambda s: int(s.split("_")[1])):
                a = f["data"][name]["actions"][:]
                da = np.abs(np.diff(a[:, :ARM_DIMS], axis=0))
                strokes = int((np.abs(np.diff(a[:, -1])) > GRIPPER_STEP).sum())
                out.append({"path": p, "demo": name, "task": task,
                            "strokes": strokes, "max_da": float(da.max()),
                            "frames": int(a.shape[0])})
    return out


def plan(rows: list[dict], keep_frac: float) -> tuple[list[dict], list[dict]]:
    """Returns (keep, drop). Reasons are stamped onto the dropped rows."""
    keep, drop = [], []
    by_task: dict = {}
    for r in rows:
        if r["strokes"] != 2:
            r["why"] = f"스트로크 {r['strokes']}회"
            drop.append(r)
        else:
            by_task.setdefault(r["task"], []).append(r)
    for group in by_task.values():
        ordered = sorted(group, key=lambda r: r["max_da"])
        n_keep = int(round(len(ordered) * keep_frac))
        keep.extend(ordered[:n_keep])
        for r in ordered[n_keep:]:
            r["why"] = f"task 내 max|Δa| 하위 {100 - keep_frac * 100:.0f}%"
            drop.append(r)
    return keep, drop


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "libero_datasets"))
    ap.add_argument("--keep", type=float, default=0.70)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)

    rows = scan(root)
    if not rows:
        print(f"{root} 에 대상 파일이 없습니다.")
        return 1
    keep, drop = plan(rows, args.keep)

    print(f"대상 {len(rows)}개 에피소드 (제외: {', '.join(EXCLUDE)})\n")
    print(f"{'task':<44}{'전체':>5}{'유지':>5}{'삭제':>5}")
    for task in sorted({r["task"] for r in rows}):
        n = sum(1 for r in rows if r["task"] == task)
        k = sum(1 for r in keep if r["task"] == task)
        print(f"  {task[12:54] or task:<42}{n:>5}{k:>5}{n - k:>5}")
    print(f"  {'합계':<42}{len(rows):>5}{len(keep):>5}{len(drop):>5}")

    why: dict = {}
    for r in drop:
        why[r["why"]] = why.get(r["why"], 0) + 1
    print("\n삭제 사유:")
    for k, v in sorted(why.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>4}개  {k}")

    if args.dry_run:
        print("\n--dry-run: 아무것도 바꾸지 않았습니다.")
        return 0

    print(f"\n{len(drop)}개를 삭제하고 {len(keep)}개의 obs/gripper_states 를 "
          "합성값으로 대체합니다. 되돌릴 수 없습니다.")
    if not args.yes and input("계속하려면 'yes': ").strip().lower() != "yes":
        print("취소했습니다.")
        return 1

    # 삭제를 먼저 한다. 번호를 다시 매기므로 이름이 바뀌고, 합성은 그 뒤에
    # 살아남은 것만 훑으면 된다.
    by_file: dict = {}
    for r in drop:
        by_file.setdefault(r["path"], []).append(r["demo"])
    for path, names in by_file.items():
        with h5py.File(path, "a") as f:
            for name in names:
                del f["data"][name]
            renumber_episodes(f["data"])
        print(f"  [삭제] {path.name}: {len(names)}개")

    n_syn = 0
    for p in sorted(root.glob("*_demo.hdf5")):
        if p.name in EXCLUDE:
            continue
        with h5py.File(p, "a") as f:
            for name in sorted(f["data"], key=lambda s: int(s.split("_")[1])):
                grp = f["data"][name]
                ds = grp["obs"]["gripper_states"]
                new = synth_gripper_states(grp["actions"][:], ds[:, 0])
                ds[:, 0] = new          # 모양·dtype 그대로라 제자리 쓰기로 충분
                n_syn += 1
        print(f"  [합성] {p.name}")
    print(f"\n완료: {len(drop)}개 삭제, {n_syn}개 합성.")
    print("HDF5 는 지운 공간을 반환하지 않습니다 -- GUI 의 '용량 최적화(재압축)' 로 회수하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
