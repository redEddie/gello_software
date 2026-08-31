"""Rebuilding `obs/gripper_states` as the ramp the fingers actually travelled.

Why the recorded channel needs rebuilding
-----------------------------------------
Until the reader/commander thread split, one thread both sampled the finger
width and issued the blocking `grasp`/`move`, so it stopped sampling for the
whole stroke. What landed in the .hdf5 was not a trajectory but a frozen value
that jumped once: over 254 episodes the reading held for 37 frames (1.85 s at
20 Hz) after the close command and then moved 100% of its range in a *single*
frame. The fingers were meanwhile taking ~1.4 s to get there.

That is not a small inaccuracy. The wrist camera sees the fingers, so for those
37 frames the image says "closing" while the state channel says "still open" --
about 34% of every episode had its two input modalities contradicting each
other. A policy either learns to distrust the state channel or learns a wrong
association.

Where the shape comes from
--------------------------
Two grasps were recorded with the fixed reader (`test` task): one closing on a
cup wall (settles at 3.1 mm) and one on the handle (settles at 28.8 mm). Aligned
on first motion they traverse *identical* widths:

    두껍게  79.8  73.7  61.4  49.6  37.8  28.8 ← 접촉, 정지
    얇게    79.8  73.6  61.4  49.6  37.8  26.0  14.2  5.3  3.1 ← 접촉, 정지

The hand closes at a fixed speed and stops wherever it touches. So the final
width alone determines the whole trajectory -- and the final width *is* present
in the old data, because `grasp()` returns after contact and the next poll
recorded it. Nothing has to be guessed.

Measured parameters (20 Hz, `speed=0.1` as issued by `_gripper_cmd_loop`),
fitted to the two ground-truth ramps above:

    닫기   지연 5.00 프레임 (0.25 s)   114 mm/s   접촉에서 정지
    열기   지연 6.50 프레임 (0.33 s)    98 mm/s   항상 완전 개방에서 정지

Fit quality on those four command-referenced ramps: mean 0.9 mm, max 12 mm on
an 80 mm range. The frozen recording it replaces is off by mean 20.7 mm, max
76.8 mm on the same episodes.

The lag is the weak part and cannot be improved from the recorded data: the
four measured lags were 4, 8 (close) and 9, 8 (open) frames. The command loop
polls the trigger every 50 ms and the state stream free-runs at ~8 Hz against
the recording clock, so ±2 frames of jitter is structural. Nothing in a frozen
recording says which end of that range a given episode sat at, so a fixed lag
is the best available and the residual shows up as a ~2 frame shift of the
ramp -- 100 ms, against the 1.85 s error it replaces.

Not quantised, deliberately. The recorded channel *is* a staircase -- the
gripper state stream runs at roughly 8 Hz, so a 20 Hz recording holds each
value for 2 or 3 frames. But that stream free-runs against the recording
clock, so its phase differs per episode and cannot be reproduced: fitting a
quantised model to these two ramps was *worse* (max 11.7 mm) than not
quantising, because a wrong phase costs a full step. The underlying finger
width is smooth, and a smooth ramp is the best per-frame estimate of it --
half a step of residual against any particular staircase is the floor, not a
modelling error.

Opening always targets full open: `_gripper_cmd_loop` issues
`move(MAX_GRIPPER_WIDTH, speed)` on the release edge, with no object to stop
it, so the target is not object-dependent the way the grasp is.
"""

from __future__ import annotations

import numpy as np

FPS = 20.0
MAX_WIDTH_MM = 80.0

# 실측 램프 두 개에 맞춘 값. 지연은 명령 프레임 기준이고 소수 프레임을 허용한다
# -- 정수로 반올림하면 램프 전체가 한 프레임씩 밀려 오차가 배로 뛴다.
CLOSE_LAG_FRAMES = 5.0      # 0.25 s
CLOSE_SPEED_MM_S = 114.0
OPEN_LAG_FRAMES = 6.5       # 0.33 s
OPEN_SPEED_MM_S = 98.0

GRIPPER_STEP = 0.05         # 0/1 이산 명령에서 전환으로 볼 최소 변화


def _transitions(gripper_cmd: np.ndarray) -> list[tuple[int, bool]]:
    """(프레임, 닫는 명령인가) 목록. 새 값이 확립되는 프레임을 쓴다."""
    idx = np.where(np.abs(np.diff(gripper_cmd)) > GRIPPER_STEP)[0]
    return [(int(i) + 1, bool(gripper_cmd[int(i) + 1] > 0.5)) for i in idx]


def synth_gripper_states(actions: np.ndarray, measured: np.ndarray) -> np.ndarray:
    """Returns a rebuilt `obs/gripper_states` column (normalised 0=open..1=closed).

    `measured` is only read for the two things the old recording *did* get
    right: the resting open value, and the width each grasp settled at. The
    frozen stretch between them is discarded and replaced.
    """
    n = len(measured)
    w = (1.0 - np.asarray(measured, dtype=float)) * MAX_WIDTH_MM      # mm
    # 그리퍼는 어느 액션 규약에서든 마지막 열이다(libero_format 참고). 열 번호를
    # 박아두면 action_include_gripper 를 끈 파일에서 팔 관절을 그리퍼로 읽는다.
    a = np.asarray(actions, dtype=float)
    trans = _transitions(a[:, -1] if a.ndim == 2 else a)
    if not trans:
        return np.asarray(measured, dtype=np.float32).copy()

    # 시작 폭: 첫 명령 전 구간의 중앙값. 그 구간은 명령이 없어 얼어붙을 일이 없다.
    first = trans[0][0]
    w_open = float(np.median(w[:first])) if first > 0 else MAX_WIDTH_MM

    # 각 구간의 도착 폭. 닫기는 접촉 지점(기록된 값), 열기는 언제나 완전 개방.
    targets: list[float] = []
    for k, (frame, closing) in enumerate(trans):
        end = trans[k + 1][0] if k + 1 < len(trans) else n
        if closing:
            seg = w[frame:end]
            # 얼어붙은 앞부분을 빼고 실제로 도달한 값을 쓴다 = 그 구간의 최소 폭.
            targets.append(float(seg.min()) if len(seg) else w_open)
        else:
            targets.append(w_open)

    out = np.empty(n, dtype=float)
    out[:first] = w_open
    cur = w_open
    for k, (frame, closing) in enumerate(trans):
        end = trans[k + 1][0] if k + 1 < len(trans) else n
        target = targets[k]
        lag = CLOSE_LAG_FRAMES if closing else OPEN_LAG_FRAMES
        per_frame = (CLOSE_SPEED_MM_S if closing else OPEN_SPEED_MM_S) / FPS
        for i in range(frame, end):
            elapsed = i - frame - lag
            if elapsed < 0:
                out[i] = cur
                continue
            moved = per_frame * elapsed
            out[i] = (max(target, cur - moved) if closing
                      else min(target, cur + moved))
        cur = out[end - 1] if end > frame else cur

    return np.clip(1.0 - out / MAX_WIDTH_MM, 0.0, 1.0).astype(np.float32)
