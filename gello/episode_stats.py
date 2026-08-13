"""Per-episode motion statistics for curating collected demonstrations.

Three numbers, chosen by the operator who uses them:

task_dev    mean_da minus the mean of the *same task*, in rad/frame. Signed:
            positive = hurried through this take, negative = dawdled. Compared
            within the task because mean_da is `travel / ((T-1) * ARM_DIMS)` --
            an identity, not an approximation -- so between tasks it measures
            how far the arm must reach (corr +0.88 with travel, +0.23 with
            duration), and a global cut at 0.0095 deletes 86% of one task and
            0% of another.
still_frac  fraction of frames the arm was parked (< STILL_VEL). Hesitation.
seconds     take duration.

A difference and not a ratio or a z-score: those were both tried and both
read worse at the bench. The cost is known and accepted -- tasks differ in
spread as well as centre (10%..27% of their own mean), so a single rad/frame
limit flags proportionally more takes in the loose tasks. Read the flag as
"look at this", never as "delete this".

Deliberately not included
-------------------------
* acc_p95 / spikes -- acceleration-based smoothness scores. Dropped on the
  operator's call.
* Flagging takes whose max |Δa| exceeds the follower's per-tick velocity limit
  (0.05 rad at 20 Hz): `actions` is the leader's command and is supposed to
  lead the follower, so 99% of episodes exceed it. A property of the
  convention, not a defect.
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

# 20Hz 기준. 0.002 rad/frame = 0.04 rad/s ≈ 2.3°/s -- 손을 얹고만 있는 상태.
STILL_VEL = 0.002
# 같은 task 평균에서 이만큼(rad/frame) 벗어나면 확인 대상으로 표시한다.
# 실측 |편차| 분포는 p50=0.00093 / p90=0.00216 / max=0.00417.
TASK_DEV_LIMIT = 0.0026

JOINT_LABELS = [f"joint{i}" for i in range(1, 8)] + ["gripper"]


@dataclass
class EpisodeStat:
    path: str
    demo: str
    task: str
    n_frames: int
    success: bool | None
    mean_da: float          # 평균 관절 속도 (rad/frame) = travel / (프레임수 * 7)
    p95_da: float
    max_da: float
    travel: float           # 팔 관절각 총 이동거리 (rad). task마다 다른 게 정상.
    still_frac: float       # 거의 멈춰 있던 프레임 비율 (0~1)
    per_dim_sigma: np.ndarray = field(repr=False, default=None)
    per_dim_max: np.ndarray = field(repr=False, default=None)
    task_dev: float = 0.0   # 같은 task 평균과의 차 (rad/frame). +면 급함, -면 느림

    @property
    def seconds(self) -> float:
        return self.n_frames / 20.0

    @property
    def key(self) -> tuple:
        return (self.path, self.demo)

    @property
    def flagged(self) -> bool:
        """양쪽 끝 -- 같은 task의 평범한 테이크보다 급했거나 늘어졌거나."""
        return abs(self.task_dev) > TASK_DEV_LIMIT


def scan_dataset(paths) -> list[EpisodeStat]:
    """Reads only `actions` (a few KB per episode) -- never images.

    272 episodes take ~0.05 s, so the analysis view can rebuild from disk every
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
                    if a.ndim != 2 or a.shape[0] < 4:
                        continue
                    arm = a[:, :ARM_DIMS]
                    da = np.abs(np.diff(arm, axis=0))
                    vel = da.max(axis=1)
                    success = grp.attrs.get("success")
                    out.append(EpisodeStat(
                        path=str(p), demo=name, task=task,
                        n_frames=int(a.shape[0]),
                        success=None if success is None else bool(success),
                        mean_da=float(da.mean()),
                        p95_da=float(np.percentile(da, 95)),
                        max_da=float(da.max()),
                        travel=float(da.sum()),
                        still_frac=float((vel < STILL_VEL).mean()),
                        per_dim_sigma=da.std(axis=0),
                        per_dim_max=da.max(axis=0),
                    ))
        except Exception:  # noqa: BLE001
            continue
    _add_task_dev(out)
    return out


def _add_task_dev(stats: list[EpisodeStat]) -> None:
    """Deviation from the mean of the same task, as the operator asked for."""
    by_task: dict = {}
    for s in stats:
        by_task.setdefault(s.task, []).append(s)
    for group in by_task.values():
        mu = float(np.mean([s.mean_da for s in group]))
        for s in group:
            s.task_dev = s.mean_da - mu


def summarize(stats: list[EpisodeStat]) -> dict:
    """Population-level view + an honest verdict.

    The verdict counts takes outside the band rather than passing the dataset
    against an invented constant: "평균과 0.0026 넘게 차이 나는 것 N개" is a
    fact a curator can act on, "JERKY" would not be.
    """
    if not stats:
        return {"n": 0, "verdict": "에피소드가 없습니다", "n_fast": 0, "n_slow": 0}
    means = np.array([s.mean_da for s in stats])
    n_fast = sum(1 for s in stats if s.task_dev > TASK_DEV_LIMIT)
    n_slow = sum(1 for s in stats if s.task_dev < -TASK_DEV_LIMIT)
    off = n_fast + n_slow
    if off == 0:
        verdict = f"전부 자기 task 평균의 ±{TASK_DEV_LIMIT} 안 — 잘라낼 것 없음"
    else:
        verdict = (f"자기 task 평균에서 {TASK_DEV_LIMIT} 넘게 벗어난 것 {off}개 "
                   f"(급함 {n_fast} / 늘어짐 {n_slow}) — 재생해서 확인해보세요")
    per_dim = np.stack([s.per_dim_sigma for s in stats]).mean(axis=0)
    return {
        "n": len(stats),
        "frames": int(sum(s.n_frames for s in stats)),
        "tasks": len({s.task for s in stats}),
        "p50": float(np.percentile(means, 50)),
        "p90": float(np.percentile(means, 90)),
        "p99": float(np.percentile(means, 99)),
        "verdict": verdict,
        "n_fast": n_fast, "n_slow": n_slow,
        "per_dim_sigma": per_dim,
        "len_min": min(s.n_frames for s in stats),
        "len_max": max(s.n_frames for s in stats),
        "still_p50": float(np.median([s.still_frac for s in stats])),
        "travel_p50": float(np.median([s.travel for s in stats])),
    }


def task_table(stats: list[EpisodeStat]) -> list[dict]:
    """Per-task rollup -- the between-task spread in speed is the dominant
    effect, so showing it stops a curator from reading a task's reach as a
    defect."""
    by: dict = {}
    for s in stats:
        by.setdefault(s.task, []).append(s)
    rows = []
    for task, group in by.items():
        means = np.array([s.mean_da for s in group])
        rows.append({
            "task": task, "n": len(group),
            "mean": float(means.mean()), "median": float(np.median(means)),
            "travel": float(np.mean([s.travel for s in group])),
            "frames": int(sum(s.n_frames for s in group)),
            "sec_min": min(s.seconds for s in group),
            "sec_max": max(s.seconds for s in group),
            "fails": sum(1 for s in group if s.success is False),
            "off": sum(1 for s in group if s.flagged),
        })
    return sorted(rows, key=lambda r: -r["travel"])


def load_series(path: str, demo: str) -> dict:
    """The three aligned series the LeRobot viewer plots, per joint.

    Returns {"state": (T,8), "commanded": (T,8)|None, "action": (T,8), "n": T}
    with the gripper appended as the 8th column so one loop covers all plots.
    """
    with h5py.File(path, "r") as f:
        # legacy 는 data/demo_N, scene(scene-v1)은 루트의 episode_NNN --
        # 에피소드 안쪽 페이로드는 동일하다.
        grp = f[demo] if demo in f else f["data"][demo]
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
