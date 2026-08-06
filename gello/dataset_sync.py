"""Compare curated local .hdf5 files against a published LeRobot dataset.

The pipeline needs one question answered before it can run unattended: can the
Hub copy be brought up to date by *appending*, or does it have to be rebuilt?

LeRobotDataset.resume() only ever appends -- there is no supported way to
remove an episode from a published dataset. So appending is correct exactly
when no already-pushed task has lost episodes since. If one has, the Hub copy
contains takes the operator deleted, and only a full rebuild + replace fixes
it. Getting this wrong is not visible afterwards: the dataset just quietly
contains bad demonstrations.

Counting is enough to decide. Both sides append in order and never reorder, so
a task whose local count is >= the Hub count has only grown; one whose count
dropped has had episodes removed. (A delete-then-record that lands back on the
same count is the one case counting misses -- flagged separately, see
``ambiguous``.)
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import h5py

# Deleting an episode and recording another leaves the count unchanged, so the
# comparison cannot tell that apart from "nothing happened". The .hdf5 records
# how many episodes existed at the last repack, which is a second, independent
# witness: if that count differs from today's while the Hub count matches, the
# file was edited in a way counting alone would miss.
REPACK_COUNT_ATTR = "repacked_episodes"


def local_tasks(data_root: str | Path) -> dict:
    """{language_instruction: {"episodes": n, "path": Path, "at_repack": n|None}}."""
    out = {}
    for p in sorted(glob.glob(str(Path(data_root) / "*_demo.hdf5"))):
        try:
            with h5py.File(p, "r") as f:
                data = f["data"]
                info = data.attrs.get("problem_info")
                task = json.loads(json.loads(info)["language_instruction"]) if info else Path(p).stem
                at = data.attrs.get(REPACK_COUNT_ATTR)
                out[task] = {"episodes": len(data.keys()), "path": Path(p),
                             "at_repack": int(at) if at is not None else None}
        except Exception:  # noqa: BLE001
            continue
    return out


def hub_tasks(repo_id: str) -> tuple[dict, str]:
    """{language_instruction: episode_count} for a published LeRobot dataset.

    Returns ({}, "") when the dataset does not exist yet -- a first push is a
    normal case, not an error. Any other failure comes back in the message so
    the caller can refuse to run rather than guess.
    """
    try:
        import pandas as pd
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import RepositoryNotFoundError
    except ImportError as e:
        return {}, f"의존성 없음: {e}"
    try:
        d = snapshot_download(repo_id, repo_type="dataset",
                              allow_patterns=["meta/*", "meta/**/*"],
                              force_download=True)
    except RepositoryNotFoundError:
        return {}, ""
    except Exception as e:  # noqa: BLE001
        return {}, f"{type(e).__name__}: {e}"
    files = sorted(glob.glob(f"{d}/meta/episodes/**/*.parquet", recursive=True))
    if not files:
        return {}, ""
    try:
        eps = pd.concat([pd.read_parquet(f) for f in files])
    except Exception as e:  # noqa: BLE001
        return {}, f"메타 읽기 실패: {type(e).__name__}: {e}"
    counts: dict = {}
    for value in eps["tasks"]:
        names = [value] if isinstance(value, str) else list(value)
        for n in names:
            counts[n] = counts.get(n, 0) + 1
    return counts, ""


def plan_sync(data_root: str | Path, repo_id: str) -> dict:
    """What it would take to make ``repo_id`` match the curated local files.

    ``action`` is the recommendation:
      "up_to_date" -- nothing to do
      "resume"     -- only appends; cheap and safe
      "rebuild"    -- a pushed task lost episodes, so the Hub copy is wrong
                      until it is rebuilt and replaced
      "blocked"    -- the Hub state could not be read; refuse rather than guess
    """
    local = local_tasks(data_root)
    hub, error = hub_tasks(repo_id)
    rows, added, shrunk, ambiguous = [], 0, 0, []
    for task in sorted(set(local) | set(hub)):
        l = local.get(task, {}).get("episodes", 0)
        h = hub.get(task, 0)
        row = {"task": task, "hub": h, "local": l, "delta": l - h, "note": ""}
        if h and l > h:
            added += l - h
            row["note"] = f"+{l - h} 추가"
        elif h and l < h:
            shrunk += h - l
            row["note"] = f"{h - l}개 삭제됨"
        elif not h:
            added += l
            row["note"] = "새 task"
        else:
            # 개수는 같다. 재압축 시점 개수와 다르면 '지우고 다시 찍은' 경우라
            # 개수만으로는 알 수 없다 -- 사람이 판단하도록 표시만 한다.
            at = local.get(task, {}).get("at_repack")
            if at is not None and at != l:
                row["note"] = "개수 같음 (편집 흔적)"
                ambiguous.append(task)
            else:
                row["note"] = "일치"
        rows.append(row)

    if error:
        action = "blocked"
    elif shrunk:
        action = "rebuild"
    elif added:
        action = "resume"
    else:
        action = "up_to_date"
    return {"rows": rows, "action": action, "error": error,
            "added": added, "shrunk": shrunk, "ambiguous": ambiguous,
            "local_total": sum(v["episodes"] for v in local.values()),
            "hub_total": sum(hub.values()),
            "paths": [str(v["path"]) for v in local.values()]}
