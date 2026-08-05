"""Per-episode motion statistics for curating collected demonstrations.

What this measures, and why it is not an absolute threshold
-----------------------------------------------------------
The natural "is this take jerky" metric is the per-frame action change |Δa|.
Measured over 273 real episodes it turned out to be a poor *absolute* signal
and a good *relative* one:

  * The spread is tight -- p99/p50 = 1.67. There is no population of wild
    outliers to cut off, so any hardcoded threshold either flags everything
    or nothing.
  * Ranking episodes globally mostly ranks *tasks*: the between-task spread
    (0.0071 - 0.0110) is far wider than the within-task spread (±0.0012).
    A global top-10 was almost entirely one task, which tells a curator
    nothing about which take to delete.

So the primary score is the z-score **within a task**: how unusual an episode
is compared to others of the same instruction. Sorting by that changed the
top-5 completely (zero overlap with the global top-5).

A second, independent score counts *spikes* -- frames whose |Δa| exceeds five
times that episode's own median. A single glitch in an otherwise clean take
is invisible to the mean but obvious here.

Deliberately NOT included: flagging episodes whose max |Δa| exceeds the
follower's per-tick velocity limit (0.05 rad at 20 Hz). In this action space
`actions` is the *leader's command*, which is supposed to lead the follower --
99% of episodes exceed it, so it is a property of the convention, not a defect.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path

import h5py
import numpy as np

# 그리퍼는 0/1 이산값이라 열릴 때마다 |Δa|가 1이 된다 -- 평활도 판정에 섞으면
# 모든 에피소드가 똑같이 거칠어 보인다. 팔 7관절만 쓴다.
ARM_DIMS = 7
SPIKE_FACTOR = 5.0

JOINT_LABELS = [f"joint{i}" for i in range(1, 8)] + ["gripper"]


@dataclass
class EpisodeStat:
    path: str
    demo: str
    task: str
    n_frames: int
    success: bool | None
    mean_da: float          # 팔 7관절의 평균 |Δa| (rad/frame)
    p95_da: float
    max_da: float
    spikes: int             # 자기 중앙값의 SPIKE_FACTOR 배를 넘는 프레임 수
    per_dim_sigma: np.ndarray = field(repr=False, default=None)
    per_dim_max: np.ndarray = field(repr=False, default=None)
    z_in_task: float = 0.0  # 같은 task 안에서의 표준화 점수
    spike_rate: float = 0.0  # 프레임당 스파이크 비율 (길이 보정)

    @property
    def seconds(self) -> float:
        return self.n_frames / 20.0

    @property
    def key(self) -> tuple:
        return (self.path, self.demo)


def scan_dataset(paths) -> list[EpisodeStat]:
    """Reads only `actions` (a few KB per episode) -- never images.

    273 episodes take ~0.05 s, so the analysis view can rebuild from disk every
    time it opens instead of maintaining a cache that could go stale after a
    collection session.
    """
    out: list[EpisodeStat] = []
    for p in paths:
        try:
            with h5py.File(p, "r") as f:
                data = f["data"]
                info = data.attrs.get("problem_info")
                try:
                    task = json.loads(json.loads(info)["language_instruction"]) if info else Path(p).stem
                except Exception:  # noqa: BLE001
                    task = Path(p).stem
                for name in sorted(data.keys(), key=lambda s: int(s.split("_")[1])):
                    grp = data[name]
                    a = grp["actions"][:]
                    if a.ndim != 2 or a.shape[0] < 3:
                        continue
                    da = np.abs(np.diff(a, axis=0))[:, :ARM_DIMS]
                    per_frame = da.max(axis=1)
                    med = float(np.median(per_frame)) or 1e-9
                    success = grp.attrs.get("success")
                    out.append(EpisodeStat(
                        path=str(p), demo=name, task=task,
                        n_frames=int(a.shape[0]),
                        success=None if success is None else bool(success),
                        mean_da=float(da.mean()),
                        p95_da=float(np.percentile(da, 95)),
                        max_da=float(da.max()),
                        spikes=int((per_frame > SPIKE_FACTOR * med).sum()),
                        per_dim_sigma=da.std(axis=0),
                        per_dim_max=da.max(axis=0),
                    ))
        except Exception:  # noqa: BLE001
            continue
    _add_relative_scores(out)
    return out


def _add_relative_scores(stats: list[EpisodeStat]) -> None:
    """z-score within each task, because between-task differences dominate."""
    by_task: dict = {}
    for s in stats:
        by_task.setdefault(s.task, []).append(s)
    for group in by_task.values():
        vals = np.array([s.mean_da for s in group])
        mu, sd = float(vals.mean()), float(vals.std())
        for s in group:
            s.z_in_task = (s.mean_da - mu) / sd if sd > 1e-12 else 0.0
            s.spike_rate = s.spikes / max(1, s.n_frames - 1)


def summarize(stats: list[EpisodeStat]) -> dict:
    """Population-level view + an honest verdict.

    The verdict is stated in terms of the spread, not a pass/fail against an
    invented constant: "p99 is 1.7x the median" is a fact a curator can act on,
    "JERKY" would not be.
    """
    if not stats:
        return {"n": 0, "verdict": "에피소드가 없습니다", "ratio": 0.0}
    means = np.array([s.mean_da for s in stats])
    p50, p99 = float(np.percentile(means, 50)), float(np.percentile(means, 99))
    ratio = p99 / p50 if p50 else 0.0
    if ratio < 2.0:
        verdict = f"전반적으로 균일 (p99가 중앙값의 {ratio:.1f}배) — 뚜렷한 이상치 없음"
    elif ratio < 4.0:
        verdict = f"편차 있음 (p99가 중앙값의 {ratio:.1f}배) — 상위 몇 개는 확인해볼 만함"
    else:
        verdict = f"이상치 존재 (p99가 중앙값의 {ratio:.1f}배) — 상위 에피소드를 살펴보세요"
    per_dim = np.stack([s.per_dim_sigma for s in stats]).mean(axis=0)
    return {
        "n": len(stats),
        "frames": int(sum(s.n_frames for s in stats)),
        "tasks": len({s.task for s in stats}),
        "p50": p50, "p90": float(np.percentile(means, 90)),
        "p99": p99, "ratio": ratio,
        "verdict": verdict,
        "per_dim_sigma": per_dim,
        "len_min": min(s.n_frames for s in stats),
        "len_max": max(s.n_frames for s in stats),
        "spiky": sum(1 for s in stats if s.spikes > 0),
    }


def task_table(stats: list[EpisodeStat]) -> list[dict]:
    """Per-task rollup -- the between-task spread is the dominant effect, so
    showing it stops a curator from reading a task's pace as a defect."""
    by: dict = {}
    for s in stats:
        by.setdefault(s.task, []).append(s)
    rows = []
    for task, group in by.items():
        means = np.array([s.mean_da for s in group])
        rows.append({
            "task": task, "n": len(group),
            "mean": float(means.mean()), "sd": float(means.std()),
            "frames": int(sum(s.n_frames for s in group)),
            "sec_min": min(s.seconds for s in group),
            "sec_max": max(s.seconds for s in group),
            "fails": sum(1 for s in group if s.success is False),
        })
    return sorted(rows, key=lambda r: -r["mean"])


def load_series(path: str, demo: str) -> dict:
    """The three aligned series the LeRobot viewer plots, per joint.

    Returns {"state": (T,8), "commanded": (T,8)|None, "action": (T,8), "n": T}
    with the gripper appended as the 8th column so one loop covers all plots.
    """
    with h5py.File(path, "r") as f:
        grp = f["data"][demo]
        obs = grp["obs"]
        action = grp["actions"][:]
        state = np.concatenate(
            [obs["joint_states"][:], obs["gripper_states"][:]], axis=1)
        commanded = None
        if "commanded_joint_states" in obs:
            cg = (obs["commanded_gripper_states"][:]
                  if "commanded_gripper_states" in obs
                  else np.zeros((len(state), 1), dtype=np.float32))
            commanded = np.concatenate([obs["commanded_joint_states"][:], cg], axis=1)
    return {"state": state, "commanded": commanded, "action": action,
            "n": int(len(action))}


def hdf5_files(data_root) -> list:
    return sorted(glob.glob(str(Path(data_root) / "*_demo.hdf5")))
