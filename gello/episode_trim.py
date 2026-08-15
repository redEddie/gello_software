"""Trimming frames off the end of a recorded episode.

Why this exists
---------------
Saving is a key press, and the operator's hand is still on the leader when it
happens. Measured over 254 real episodes, the per-frame joint speed of the last
20 frames -- normalised by each episode's own median so tasks of different
reach are comparable -- looks like this:

    frame from end  -20  -18  -15  -12  -10   -8   -6   -4   -2   -1
    median x median 0.30 0.50 0.93 1.58 1.73 1.78 1.56 1.24 0.99 0.85
    p90             2.09 2.37 3.01 4.01 4.16 4.06 3.60 3.06 2.67 2.40

The take is already settling at -20 (0.30x), then speed *rises* to a bump
peaking around -10 to -7 before the recording stops. That bump is the reach for
the key, not the task, and it is the last thing a policy sees -- so it is
learned as "how to finish".

How far it is safe to cut
-------------------------
The real end of the task is the gripper release. Across the same episodes the
last gripper change sits at:

    p5 = 19,  p50 = 27,  p95 = 39,  max = 61  frames from the end

So a trim below ~19 frames leaves every recorded release intact, and anything
larger starts eating the release itself in the earliest 5%. `plan_trim` returns
that distance per episode so the caller can refuse the ones that would cut it,
rather than trusting a single global number.

The comparison is on indices, and that detail is the whole guard: the last
frame kept after cutting n is index `T-n-1`, and the release must land at or
before it. Comparing frame *counts* instead lets through exactly the n that
removes the release frame and nothing else -- the one value the guard exists
to catch. GRIPPER_MARGIN then keeps a couple of frames past it, so the take
still ends with "released, and holding still" rather than on the release tick.

Editing HDF5 in place
---------------------
Every dataset here is fixed-size (`maxshape == shape`), so `resize()` is not
available: each one is read, deleted and rewritten with the same dtype, chunks
and compression. That leaves the freed bytes as file slack -- HDF5 does not
return them to the filesystem -- which is what the existing repack step is for.
`trimmed` is written into the group's attrs so a second pass can tell an
already-trimmed take from a naturally short one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import h5py
import numpy as np

ARM_DIMS = 7
# 그리퍼 명령은 0/1 이산값이라, 이보다 큰 변화는 열림/닫힘 한 번을 뜻한다.
GRIPPER_STEP = 0.05
# 놓은 뒤 남겨야 할 최소 프레임 수. 놓는 프레임 하나만 겨우 남기면 정책이 보는
# "다 놓고 끝난 상태"가 한 틱뿐이라, 마지막 동작이 사실상 학습되지 않는다.
GRIPPER_MARGIN = 2
# 자르고도 남겨야 할 최소 길이. LeRobot 변환과 Δa 통계 모두 3프레임 미만은 못 쓴다.
MIN_FRAMES = 20


@dataclass
class TrimPlan:
    demo: str
    n_frames: int
    n_trim: int
    release_idx: int | None    # 마지막 그리퍼 변화가 *확립되는* 프레임 인덱스
    already: str | None        # 이미 자른 이력 (attrs["trimmed"])
    scene: bool = False        # scene-v1 에피소드 (표시용; 처리 경로는 동일)

    @property
    def result_frames(self) -> int:
        return self.n_frames - self.n_trim

    @property
    def gripper_tail(self) -> int | None:
        """놓는 프레임이 끝에서 몇 번째인가 (표시용)."""
        return None if self.release_idx is None else self.n_frames - self.release_idx

    @property
    def max_safe_trim(self) -> int | None:
        """놓기와 그 뒤 여유를 남기면서 자를 수 있는 최대 프레임 수."""
        if self.release_idx is None:
            return None
        return max(0, self.n_frames - 1 - self.release_idx - GRIPPER_MARGIN)

    @property
    def cuts_gripper(self) -> bool:
        """남는 마지막 프레임이 '놓기 + 여유' 뒤까지 가는가.

        인덱스로 따진다. n개를 자르면 남는 마지막 인덱스는 result_frames-1 이고,
        놓기가 보존되려면 그 값이 release_idx 이상이어야 한다 -- 프레임 *수*와
        비교하면 정확히 하나가 어긋나서, 놓는 프레임만 딱 잘려나가는 값이
        통과한다(실측: 120프레임/놓기 90번 에피소드에서 n=30).
        """
        if self.release_idx is None:
            return False
        return self.result_frames - 1 < self.release_idx + GRIPPER_MARGIN

    @property
    def too_short(self) -> bool:
        return self.result_frames < MIN_FRAMES

    @property
    def blocked(self) -> str | None:
        # scene 에피소드도 자른다 (2026-08-14 결정: 실패/튀는 궤적 삭제와 함께
        # HDF5 큐레이션 편집 허용). 자르기는 프레임축 데이터셋만 줄이고
        # uid·번호·instruction 은 손대지 않으므로 slot 계보에 영향이 없다.
        if self.n_trim <= 0:
            return "자를 프레임 수가 0입니다"
        if self.too_short:
            return f"남는 프레임이 {self.result_frames}개뿐입니다 (최소 {MIN_FRAMES})"
        if self.cuts_gripper:
            return (f"물체를 놓는 프레임이 끝에서 {self.gripper_tail}번째입니다 "
                    f"— 최대 {self.max_safe_trim}프레임까지만 자를 수 있습니다")
        return None


def _release_idx(actions: np.ndarray) -> int | None:
    """Frame at which the last gripper change has taken effect.

    `diff[i] != 0` means the value differs between frame i and i+1, so the new
    command is first *observed* at i+1 -- that later frame is the one that has
    to survive, not i.
    """
    if actions.shape[1] <= ARM_DIMS:
        return None
    changes = np.where(np.abs(np.diff(actions[:, ARM_DIMS])) > GRIPPER_STEP)[0]
    return int(changes[-1]) + 1 if len(changes) else None


def _episode_group(f: h5py.File, demo: str):
    """legacy 는 data/demo_N, scene(scene-v1)은 루트 episode_NNN.
    에피소드 안쪽 페이로드는 동일하다. (그룹, scene 여부) 를 돌려준다."""
    if demo in f:
        return f[demo], True
    return f["data"][demo], False


def plan_trim(path: str, demos: list[str], n_trim: int) -> list[TrimPlan]:
    """Checks, without writing anything, what trimming `n_trim` would do."""
    out: list[TrimPlan] = []
    with h5py.File(path, "r") as f:
        for demo in demos:
            grp, scene = _episode_group(f, demo)
            a = grp["actions"][:]
            out.append(TrimPlan(
                demo=demo, n_frames=int(a.shape[0]), n_trim=int(n_trim),
                release_idx=_release_idx(a),
                already=grp.attrs.get("trimmed"),
                scene=scene,
            ))
    return out


def tail_speed(path: str, demo: str, k: int = 40) -> np.ndarray:
    """Per-frame arm speed for the last `k` frames, in units of the episode's
    own median -- the curve the operator picks a cut point off."""
    with h5py.File(path, "r") as f:
        a = _episode_group(f, demo)[0]["actions"][:]
    v = np.abs(np.diff(a[:, :ARM_DIMS], axis=0)).max(axis=1)
    med = float(np.median(v)) or 1e-9
    return v[-k:] / med


def suggest_trim(path: str, demo: str, max_trim: int = 15) -> int:
    """Frames to cut so the take ends where it had already settled.

    Walks back from the end while the speed is still above the episode's own
    median and stops at the first frame that is not -- i.e. cuts the closing
    bump and nothing before it. Returns 0 when the take already ends quietly,
    which is the answer for a take that needs no edit.
    """
    v = tail_speed(path, demo, k=max_trim + 1)
    n = 0
    for x in reversed(v):
        if x <= 1.0 or n >= max_trim:
            break
        n += 1
    return n


def trim_tail(path: str, demo: str, n_trim: int) -> int:
    """Drops the last `n_trim` frames of one episode. Returns the new length.

    Raises ValueError if the plan is blocked -- the caller is expected to have
    shown `TrimPlan.blocked` to the operator first, so reaching here with a bad
    plan is a bug, not a user mistake.
    """
    plan = plan_trim(path, [demo], n_trim)[0]
    if plan.blocked:
        raise ValueError(plan.blocked)
    keep = plan.result_frames
    with h5py.File(path, "a") as f:
        grp, _scene = _episode_group(f, demo)   # 양포맷 동일 처리
        targets: list[str] = []
        grp.visititems(
            lambda name, obj: targets.append(name)
            if isinstance(obj, h5py.Dataset) else None)
        for name in targets:
            ds = grp[name]
            if ds.shape[0] != plan.n_frames:
                continue  # 프레임축이 아닌 데이터셋은 건드리지 않는다
            data = ds[:keep]
            spec = {"dtype": ds.dtype, "chunks": ds.chunks,
                    "compression": ds.compression,
                    "compression_opts": ds.compression_opts}
            if ds.chunks is not None:
                # 청크의 첫 축이 남길 길이보다 크면 생성이 실패한다.
                spec["chunks"] = (min(ds.chunks[0], keep), *ds.chunks[1:])
            del grp[name]
            grp.create_dataset(name, data=data, **spec)
        grp.attrs["num_samples"] = keep
        prev = grp.attrs.get("trimmed", "")
        stamp = f"{time.strftime('%Y-%m-%d %H:%M')} -{n_trim}f"
        grp.attrs["trimmed"] = f"{prev}; {stamp}" if prev else stamp
    return keep
