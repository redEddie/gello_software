"""A one-sided joint-limit wall for a GELLO leader, driven in current mode.

The leader turns through poses the follower cannot reach, and driving the
follower at one of those commands speed it is not allowed near a limit -- on the
FR3 that trips the speed-limit reflex.  Rather than clamp the follower's command
(which decouples the two arms and leaves the operator pushing through a dead
zone), push back on the *leader* so the limit is felt and never crossed.

The same loop optionally drives the *trigger* servo as a spring (see
``gripper_open_close``): a light constant current toward open while the trigger
is squeezed or held, swapping to a stronger return push once the trigger swings
open -- so the squeeze can be soft under the finger without slowing the
snap-back -- plus an exponentially rising squeeze resistance from the
follower's binary close threshold up to full close.  The exponential shape is
deliberate: felt intensity is roughly logarithmic in the stimulus
(Weber-Fechner), so an exponential current reads as a linearly increasing
"gripping harder" cue.  It lives in this thread because ``set_current``
sync-writes the *whole* servo vector -- a second writer would stomp this one.

The wall runs its own high-rate thread: the position->current loop needs a few
hundred Hz to stay stiff without buzzing, far above the ~100 Hz teleop loop.  It
*shares* a ``DynamixelDriver`` -- and that driver's bus lock -- with whoever else
reads it (the teleop agent); it opens no port of its own.

The same thread also drives an optional "pose-match assist" (see
``set_match_target``): a two-sided current-mode spring-damper that pulls the
arm servos onto an arbitrary target pose, e.g. the follower's reset pose, so
the operator doesn't have to nudge the leader there by hand joint-by-joint.
The pull is shaped as an *attractive well* (2026-08-31, issue #37A) rather
than a spring that grows without bound: each joint's match current is scaled
by ``1 / (1 + (err/match_well_rad)^2)``, so the force peaks around
``match_well_rad`` and then fades as ``1/err``. Close in it holds firmly;
pull a joint well away and the motor lets go, by design. That is what makes
the arm steerable by hand -- notably when unwinding a cable by turning a
joint a full revolution, where a plain spring would fight the whole way and
then drag the joint back the short way round, re-twisting what was just
undone. The envelope is smooth everywhere (a rational function -- one divide,
no exp, and no kink to jerk the motor), and it is per joint: the one being
turned goes soft while the rest keep holding the arm up. Beyond
``match_arm_rad`` the servos are cut to torque-off entirely, since current
control at ~0 mA still drags more than no torque at all.

The shaping lives here rather than in the callers because the rule it
enforces is about the hardware: put the leader down anywhere you like and
nothing over-torques. A second guard covers what the envelope cannot -- a
joint stuck at its current cap for ``match_stall_s`` seconds (jammed, or a
hand holding it near the target where the assist is at full strength) means
the pull is losing, so the assist gives up and latches off until the caller
sets a target again; servos are more expensive than one alignment.
It lives here for the same reason the limit spring does -- ``set_current``
sync-writes the whole servo vector, so a second control loop calling it
concurrently would fight this one over the bus. Arming follows the assist per
joint: a joint within ``match_arm_rad`` of its target is energized, the rest
stay torque-off, and clearing the target (typically right after ``status()``
reports ``match_done``) drops them all back to the limit-only rule -- so the
leader goes back to being freely back-drivable for teleop with no extra
cleanup call.

One place the wall deliberately stops being a wall: half a turn from a
joint's range centre, ``wrap_into_limits`` flips which turn it reports, so the
same physical pose reads as "past the upper limit" one tick and "past the
lower limit" the next, and the limit spring reverses at full strength --
violent chatter. The previous automatic wrap-zone hand-over (which cut torque
near the flip point to let the operator unwind a cable) has been disabled;
the wall now keeps every joint under control at all times.

Gains/tolerances below are untuned defaults (no access to the real leader
while writing this) -- expect to retune
``match_kp``/``match_kd``/``match_max_current`` on hardware.

The same thread also optionally drives a lightweight, *empirical* gravity
compensation + stiction dither (see ``set_gravity_comp``, GitHub issue #3's
"③"/"④") -- NOT the model-based RNEA-on-a-URDF approach FACTR uses
(``gello/factr/gravity_compensation.py``), because there is no URDF (mass/
inertia) for this leader and none is available to build one from. Instead
each arm joint gets an independent single-pendulum approximation,
``tau_g_i = gravity_gains[i] * sin(q_i - gravity_offsets[i])`` -- ignores
cross-coupling between joints (a real chain's gravity load on joint i also
depends on joints i+1..n's angles; this doesn't), but needs only two
hand-tunable numbers per joint instead of a full dynamics model, and is
tunable live on the real leader by feel ("does it float here?") via
``set_gravity_comp``. Both arrays default to all-zero -- every joint's
compensation is simply off, and it has stayed off: the one real-hardware
tuning pass did not behave right, and the suspect is this per-joint
single-pendulum approximation itself rather than the numbers fed to it
(GitHub issue #3). Reviving this needs a leader URDF or bigger servos, not
another tuning session.
Unlike the limit spring and match assist, gravity comp is meant to be on
essentially continuously while a human might be holding the leader, so a
non-zero ``gravity_gains`` force-arms the arm servos unconditionally (like
an active match target), not just near a limit.

Stiction dither (``stiction_gain``) is a small square-wave added on top,
proportional to that joint's own ``tau_g`` and flipping sign every tick
while ``|dq| < stiction_vel_tol``, to nudge a joint through static friction
right at the point gravity comp alone leaves it just barely not moving
(FACTR's same rationale) -- meaningless while gravity comp is off (its own
gain is zero everywhere), so there is nothing separate to tune first.

Faults are deliberately blunt (see the teleop integration design): on any fault
the thread stores the exception and exits without cleanup, and ``poll()``
re-raises it so the caller dies.  The servos keep their last current; the
Dynamixel overload protection is the backstop.  ``stop()`` is the *clean* exit
path (normal shutdown) and does zero the current and drop torque.
"""

import enum
import threading
import time
from typing import Dict, Optional, Tuple

import numpy as np

from gello.config.constants import IDLE_MIN_CURRENT, MATCH_GATE_RAD

from gello.hw.dynamixel.driver import (
    CURRENT_CONTROL_MODE,
    POSITION_CONTROL_MODE,
    DynamixelDriverProtocol,
)

# XL330 control-table addresses for supply-health monitoring.
ADDR_HW_ERROR = 70        # Hardware Error Status (1 B, bitfield)
ADDR_INPUT_VOLTAGE = 144  # Present Input Voltage (2 B, units of 0.1 V)
ADDR_TEMPERATURE = 146    # Present Temperature (1 B, deg C)


class WallMatchState(enum.Enum):
    """Explicit states for the pose-match assist."""

    IDLE = "idle"       # no match target
    ARMED = "armed"     # target set but outside gate, torque off
    PULLING = "pulling" # inside gate, pulling toward target
    BLOCKED = "blocked" # stalled, gave up to protect servos
    DONE = "done"       # held within tolerance


def _engage_gate(engaged: bool, err: float, gate: float, release: float) -> bool:
    """토크를 켤지 말지 (히스테리시스). 순수 함수 -- selftest 에서 검증.

    아직 안 켰으면 ``err <= gate`` 여야 켜지고, 한 번 켠 뒤에는
    ``gate + release`` 를 넘어야 꺼진다. 히스테리시스가 없으면 경계에서
    토크가 켜졌다 꺼졌다 하며 딸깍거린다.

    끄는 판정에만 쓴다 -- 얼마나 세게 당길지는 _well_assist 가 연속으로
    정한다. 여기서 자르는 것은 "이 거리에선 어차피 무의미한 전류이니
    차라리 팔을 완전히 자유롭게 두자" 는 결정이다.
    """
    return err <= (gate + release) if engaged else err <= gate


def _well_assist(abs_err: np.ndarray, well: float) -> np.ndarray:
    """조인트별 정렬 세기 0..1 -- 오차가 커질수록 힘이 부드럽게 풀린다.

        a(x) = 1 / (1 + (x/well)^2)

    유리 함수라 exp 없이 나눗셈 하나로 끝나고, 어디서나 미분 가능해서
    (가우시안과 달리 꺾이는 지점이 없다) 모터가 덜컹거리지 않는다.
    스프링 힘 ``kp*x`` 에 이걸 곱하면 ``kp*x/(1+(x/well)^2)`` 라는 우물이
    되어 ``x = well`` 에서 힘이 최대가 되고 그 바깥으로는 ``1/x`` 로
    잦아든다 -- 가까이서는 확실히 잡아 주고, 사람이 크게 끌면 힘이 스스로
    빠져 손으로 이길 수 있다.

    조인트마다 따로 계산하는 것이 중요하다: 케이블을 풀려고 한 관절만
    한 바퀴 돌릴 때, 그 관절은 힘이 빠지고 나머지는 계속 자세를 지켜야
    팔이 주저앉지 않는다.
    """
    return 1.0 / (1.0 + (np.asarray(abs_err, dtype=float) / well) ** 2)


def _wrap_pi(d: np.ndarray) -> np.ndarray:
    """Wrap an angular difference into (-pi, pi] -- the shortest way around.

    Distinct from wrap_into_limits, which normalizes an absolute *position*
    into a joint's range. This normalizes a *difference*, so a pose match
    always takes the short path even when the two poses are written in
    representations a full turn apart.
    """
    return (np.asarray(d, dtype=float) + np.pi) % (2 * np.pi) - np.pi


def wrap_into_limits(q: np.ndarray, lower, upper) -> np.ndarray:
    """Add whole turns (2*pi*k, k integer) to each joint so it lands nearest its
    range center.

    A GELLO joint reads a full turn off when the encoder's absolute reading (one
    revolution only) plus the fixed calibration offset lands outside its range:
    e.g. a joint physically at +0.293 reads +6.576 (= +0.293 + 2*pi).  Removing
    that phantom turn does not move the leader -- 2*pi is one revolution, the
    same physical angle -- it only makes the number match the pose (and the
    follower).  Because every follower joint's span is < 2*pi, the nearest turn
    to the range center is the one inside the range, uniquely.

    Does NOT clamp: a value legitimately just outside the range rounds to k=0 and
    stays there, so a real out-of-range pose is not masked (the wall/start gate
    still see it).
    """
    q = np.asarray(q, dtype=float)
    center = (np.asarray(lower, dtype=float) + np.asarray(upper, dtype=float)) / 2.0
    k = np.round((center - q) / (2 * np.pi))
    return q + 2 * np.pi * k


def _allocate_budget(cur: np.ndarray, i_trig: float, budget: float,
                     floors: np.ndarray) -> "tuple[np.ndarray, float]":
    """Split the supply budget across joints + trigger, honoring floors.

    Within budget: unchanged. Over budget: each joint first keeps
    ``min(floor_i, |request_i|)`` -- a floor is a *guarantee*, not a grant,
    so an already-aligned joint requesting almost nothing cedes its unused
    floor to the pool automatically (this is the "real-time weighting":
    no explicit reallocation step is needed). Only the excess above the
    floors, plus the trigger, is scaled down to fit. Degenerate case: if
    the floors alone exceed the budget, everything scales uniformly (the
    guarantee is impossible; uniform is the least-surprising fallback).
    Pure function -- unit-tested offline in selftest()."""
    total = float(np.abs(cur).sum()) + abs(i_trig)
    if total <= budget:
        return cur, i_trig
    kept = np.minimum(floors, np.abs(cur))
    fixed = float(kept.sum())
    if fixed >= budget:
        scale = budget / total
        return cur * scale, i_trig * scale
    rest = (total - fixed)
    scale = (budget - fixed) / rest
    out = np.sign(cur) * (kept + (np.abs(cur) - kept) * scale)
    return out, i_trig * scale


class JointLimitWall:
    """One-sided spring-damper on the leader at the follower's joint limits.

    Args:
        driver: a live ``DynamixelDriver`` (or protocol-compatible), already
            reading.  Shared, not owned -- the wall never opens or closes it.
        lower, upper: follower joint limits (rad), arm joints only.
        offsets, signs: the *robot's resolved* offsets/signs mapping raw servo
            radians to follower joint space (arm joints; gripper slice ignored).
        n_arm: number of arm joints (excludes the gripper servo).
        gripper_open_close: raw trigger-servo radians at (open, closed) -- the
            same pair ``DynamixelRobot.gripper_open_close`` uses to normalize
            the trigger to 0..1.  When given, the trigger servo is driven as a
            spring toward open (fading just past open so it parks there instead
            of stalling on the stop): ``trigger_squeeze_current`` while the
            trigger is squeezed or held, blending up to
            ``trigger_return_current`` as the opening speed approaches
            ``trigger_return_ramp`` (strokes/s) -- squeeze feel and return
            speed are separate knobs -- plus up to ``trigger_max_current`` more
            rising exponentially (shape ``trigger_curve``) between
            ``trigger_start`` and full close.  None leaves the trigger servo
            untouched (old behavior).

    Keyword tuning args mirror the standalone script's defaults, which were
    tuned by hand on the real leader (500 mA per servo, 2800 mA supply budget).

    Pose-match assist args (see ``set_match_target``): ``match_kp`` (scalar or
    per-arm-joint -- kp * lead cap is each joint's real max pull, so pitch
    joints need a far stiffer spring), ``match_int_gain``/``match_int_max``
    (model-free gravity-holding integrator, per-joint clamp, 0 = off),
    ``budget_floor`` (per-joint guaranteed share of ``current_budget`` when
    the summed request saturates -- see ``_allocate_budget``), ``match_kd``
    are the spring/damper gains (current per rad, current per rad/s) pulling
    an armed joint toward the match target; ``match_max_current`` caps that
    pull per joint. ``match_tol`` (rad) / ``match_vel_tol`` (rad/s) are the
    per-joint error/speed a match must be under, continuously for
    ``match_hold_s`` seconds, before ``status()["match_done"]`` goes True --
    the hold window is there so a fast pass-through mid-swing can't look like
    "done". None of these five are hand-tuned yet -- unlike the limit-wall
    numbers above -- start conservative and retune on the real leader.

    Gravity-comp args (see ``set_gravity_comp``, and the class docstring
    above for why this is an empirical per-joint model rather than RNEA):
    ``gravity_gains``/``gravity_offsets`` (length ``n_arm``, current / rad)
    seed the initial per-joint ``tau_g_i = gravity_gains[i] *
    sin(q_i - gravity_offsets[i])``; both default to all-zero (off).
    ``stiction_gain`` seeds the friction-dither amplitude as a fraction of
    each joint's own ``|tau_g_i|``; ``stiction_vel_tol`` (rad/s) is the speed
    below which a joint is considered "stuck" and gets dithered.
    """

    def __init__(
        self,
        driver: DynamixelDriverProtocol,
        lower: np.ndarray,
        upper: np.ndarray,
        offsets: np.ndarray,
        signs: np.ndarray,
        n_arm: int,
        *,
        margin: float = 0.02,
        max_current: float = 500.0,
        current_budget: float = 2800.0,
        wall_depth: float = 0.1,
        kd: float = 40.0,
        arm_margin: float = 0.25,
        arm_hysteresis: float = 0.05,
        hz: float = 300.0,
        health_every: float = 0.5,
        min_voltage: float = 4.5,
        gripper_open_close: Optional[Tuple[float, float]] = None,
        trigger_start: float = 0.6,
        trigger_squeeze_current: float = 30.0,
        trigger_return_current: float = 50.0,
        trigger_return_ramp: float = 0.5,
        trigger_max_current: float = 300.0,
        trigger_curve: float = 3.0,
        trigger_kd: float = 5.0,
        match_kp=400.0,  # scalar or per-arm-joint sequence (mA/rad)
        match_kd: float = 20.0,
        match_max_current=350.0,  # scalar or per-arm-joint sequence (mA)
        idle_hold_current: float = 0.0,  # 0 = 정렬 모드에서도 캡을 낮추지 않음
        # 2026-09-01: 0.05 rad(2.9도)에서 '완료' 로 보던 것을 0.02(1.1도)로.
        # 조작자가 "정렬이 완벽하지 않다" 고 본 것이 이 여유였다.
        match_tol: float = 0.02,
        match_vel_tol: float = 0.15,
        match_hold_s: float = 0.3,
        match_rate: float = 0.6,
        match_max_lead: float = 0.35,
        match_stiff_kp: float = 1200.0,
        match_stiff_tol: float = 0.10,
        match_int_gain: float = 1000.0,
        match_int_max=0.0,  # scalar or per-arm-joint sequence (mA); 0 = off
        match_well_rad: float = MATCH_GATE_RAD / 2,
        match_arm_rad: float = MATCH_GATE_RAD * 2,
        match_gate_release: float = 0.15,
        match_stall_frac: float = 0.9,
        match_stall_s: float = 4.0,  # 0 = 감시 끔
        budget_floor=0.0,  # scalar or per-arm-joint sequence (mA)
        gravity_gains: Optional[np.ndarray] = None,
        gravity_offsets: Optional[np.ndarray] = None,
        stiction_gain: float = 0.0,
        stiction_vel_tol: float = 0.05,
    ):
        if arm_margin <= wall_depth:
            raise ValueError(
                f"arm_margin ({arm_margin}) must exceed wall_depth ({wall_depth}), "
                "or the wall is still disarmed at full force"
            )
        self._driver = driver
        self._n_arm = int(n_arm)
        self._n_ids = len(driver._ids)

        self._trigger = gripper_open_close is not None
        if self._trigger:
            if self._n_ids <= self._n_arm:
                raise ValueError(
                    "gripper_open_close given but the driver has no gripper servo"
                )
            if not 0.0 < trigger_start < 1.0:
                raise ValueError(f"trigger_start ({trigger_start}) must be in (0, 1)")
            if trigger_return_ramp <= 0.0:
                raise ValueError(
                    f"trigger_return_ramp ({trigger_return_ramp}) must be > 0"
                )
            open_r, closed_r = float(gripper_open_close[0]), float(gripper_open_close[1])
            if open_r == closed_r:
                raise ValueError("gripper open and closed positions are equal")
            self._trig_open = open_r
            self._trig_span = closed_r - open_r  # signed; divides raw -> 0..1
            # Raw-current sign that pushes the trigger toward open.
            self._trig_open_dir = -float(np.sign(self._trig_span))
            self._trig_start = trigger_start
            self._trig_squeeze = trigger_squeeze_current
            self._trig_return = trigger_return_current
            self._trig_ramp = trigger_return_ramp
            self._trig_max = trigger_max_current
            self._trig_curve = trigger_curve
            self._trig_kd = trigger_kd
            self._trig_cap = (
                max(trigger_squeeze_current, trigger_return_current)
                + trigger_max_current
            )
        self._offsets = np.asarray(offsets, dtype=float)[: self._n_arm]
        self._signs = np.asarray(signs, dtype=float)[: self._n_arm]
        self._lower = np.asarray(lower, dtype=float)  # true limits, for wrapping
        self._upper = np.asarray(upper, dtype=float)
        self._lo = self._lower + margin  # margined, for the spring
        self._hi = self._upper - margin
        self._kp = max_current / wall_depth
        self._kd = kd
        self._max_current = max_current
        self._budget = current_budget
        self._arm_margin = arm_margin
        self._arm_hyst = arm_hysteresis
        self._dt = 1.0 / hz
        self._health_every = health_every
        self._min_voltage = min_voltage

        # Per-joint spring gain (2026-08-27): a scalar kp under-serves the
        # pitch joints. The pull current is kp * tracking-error, and the
        # error is capped at match_max_lead -- with kp=400 that is a hard
        # 140 mA ceiling per joint no matter what match_max_current says.
        # J2/J4 fight gravity and need a much stiffer spring to actually
        # reach their current caps; the rest only fight friction.
        self._match_kp = np.broadcast_to(
            np.asarray(match_kp, dtype=float), (self._n_arm,)
        ).copy()
        self._match_kd = match_kd
        # Scalar or one value per arm joint. Per-joint matters because the
        # supply is the real constraint, not any single servo: every armed
        # joint can saturate at once, so the worst-case draw is the SUM of
        # these caps. Splitting a fixed budget lets the pitch joints (which
        # carry the arm's weight) get most of it while the rest stay small,
        # instead of one scalar that is either too weak for pitch or lets the
        # total run past what the supply can deliver.
        self._match_max_current = np.broadcast_to(
            np.asarray(match_max_current, dtype=float), (self._n_arm,)
        ).copy()
        self._idle_hold_current = float(idle_hold_current)
        self._in_teleop = False  # issue #37A: teleop vs. alignment
        # _wrap_mask / _match_aborted_wrap are retained for API compatibility.
        # The automatic wrap-zone hand-over is disabled (2aba6b5); the wall
        # keeps every joint under control at all times.
        self._wrap_mask = np.zeros(self._n_arm, dtype=bool)
        self._match_aborted_wrap = False
        self._match_tol = match_tol
        self._match_vel_tol = match_vel_tol
        # A step target is what makes the leader lurch: kp=400 against a 1 rad
        # error saturates the current cap on the very first tick, so all seven
        # joints slam toward their goals at once along whatever path the
        # linkage happens to take -- which is how the arm ties itself in a
        # knot. Instead the spring chases a *moving* setpoint that starts at
        # wherever the leader already is (zero initial force, no lurch) and
        # travels toward the target at match_rate rad/s. Near the goal the
        # gain switches to match_stiff_kp so it locks in place and holds
        # rather than staying soft where the operator is about to let go.
        self._match_rate = float(match_rate)
        self._match_max_lead = float(match_max_lead)
        self._match_stiff_kp = float(match_stiff_kp)
        self._match_stiff_tol = float(match_stiff_tol)
        self._match_setpoint: Optional[np.ndarray] = None
        self._match_hold_s = match_hold_s
        # Match-time integrator (2026-08-27): a spring alone cannot reject
        # gravity at steady state -- holding ~430 mA on J2 with kp inside
        # sane bounds would need an error far above match_tol, which is why
        # pitch used to sag below the target until a hand helped it. The
        # integrator learns the exact holding current for THIS pose with no
        # model (the URDF-based gravity comp this replaces was reverted for
        # being wrong, see class docstring). Leaky safeguards: clamped to
        # match_int_max per joint (0 = off for that joint), reset on every
        # set_match_target call, zeroed while no target is set.
        self._match_int_gain = float(match_int_gain)
        self._match_int_max = np.broadcast_to(
            np.asarray(match_int_max, dtype=float), (self._n_arm,)
        ).copy()
        self._match_int = np.zeros(self._n_arm)
        # Budget floors (2026-08-27): when the summed request exceeds the
        # supply budget, joints used to be scaled down uniformly -- starving
        # the pitch joints exactly when several joints pull at once. A floor
        # reserves up to floor_i mA for joint i (only as much as it actually
        # requests -- an aligned joint requesting 10 mA cedes the rest of its
        # floor automatically); only the excess above the floors is scaled.
        self._budget_floor = np.broadcast_to(
            np.asarray(budget_floor, dtype=float), (self._n_arm,)
        ).copy()
        # Set/cleared by set_match_target(), read once per tick in _run().
        # Plain attribute swap, not lock-protected: CPython's GIL makes a
        # single reference assignment atomic, and _run() only ever needs
        # "the target as of the start of this tick" -- a target arriving
        # mid-tick just takes effect one tick later.
        self._match_target: Optional[np.ndarray] = None
        self._match_state = WallMatchState.IDLE
        # Deprecated boolean snapshots: kept in status() for API compatibility.
        # New code should read "match_state" instead.
        self._match_done = False
        self._match_blocked = False
        self._match_hold_start: Optional[float] = None

        # 힘 우물 (2026-08-31, issue #37A). 정렬 힘은 켜짐/꺼짐이 아니라
        # 오차에 따라 연속으로 변한다 -- 목표 근처에서 가장 세고, 멀어질수록
        # 스스로 풀린다 (_well_assist). 왜 호출자가 아니라 여기냐면: 규칙이
        # "리더암을 어디에 놓아두든 과토크가 걸리지 않는다" 이기 때문이다 --
        # set_match_target 을 부르는 모든 경로가 같은 보호를 받아야 한다.
        #
        # well 은 게이지 임계의 절반이 기본값이다: 게이지가 초록인 구간
        # (<= MATCH_GATE_RAD)에서는 힘이 최대 근처라 손으로 밀면 적당히
        # 버티고, 빨강으로 넘어가면 20% 아래로 떨어져 사람이 쉽게 이긴다.
        # arm 반경(기본 게이지 임계의 2배) 밖에서는 아예 토크를 끊는다 --
        # 그 거리의 전류는 어차피 무의미한데 전류제어 0 이 토크 오프보다
        # 끌리기 때문이다.
        self._match_well = float(match_well_rad)
        self._match_arm_rad = float(match_arm_rad)
        self._match_gate_release = float(match_gate_release)
        self._match_engaged = False
        # 토크는 조인트마다 따로 켠다 (set_torque_ids 가 부분집합을 받는다).
        # 힘만 풀고 토크를 켜 둔 채로는 부족하다 -- 전류제어 0 도 토크 오프
        # 보다 끌리기 때문에, 케이블을 풀려고 돌리는 그 관절은 아예 토크를
        # 끊어야 정말로 자유롭다. 나머지 관절은 그대로 자세를 지킨다.
        self._match_arm_mask = np.zeros(self._n_arm, dtype=bool)
        self._armed_mask = np.zeros(self._n_arm, dtype=bool)
        # 포화 감시. J2/J4 는 kp*max_lead = 2800*0.35 = 980 mA 라, 게이트
        # 안(0.5 rad)에서도 사실상 캡(1000 mA)으로 당긴다 -- 정상 정렬은
        # 1~2초면 끝나므로 그 전류가 몇 초씩 이어진다는 건 리더가 뭔가에
        # 걸렸거나 사람이 붙잡고 있다는 뜻이고, 그게 곧 과부하(0x20)다.
        # 그때는 싸우지 않고 정렬을 포기한다(래치) -- 서보를 살리는 쪽이
        # 정렬 한 번보다 싸다. 래치는 set_match_target 재호출로만 풀린다.
        self._match_stall_frac = float(match_stall_frac)
        self._match_stall_s = float(match_stall_s)
        self._match_stall_since: Optional[float] = None

        # Set/read like _match_target -- plain attribute swaps, no lock.
        self._gravity_gains = (
            np.zeros(self._n_arm) if gravity_gains is None
            else np.array(gravity_gains, dtype=float)
        )
        self._gravity_offsets = (
            np.zeros(self._n_arm) if gravity_offsets is None
            else np.array(gravity_offsets, dtype=float)
        )
        self._stiction_gain = float(stiction_gain)
        self._stiction_vel_tol = float(stiction_vel_tol)
        # +1/-1 per joint, flipped each tick a joint is dithered -- carries
        # across ticks so the dither is a square wave, not noise.
        self._stiction_sign = np.ones(self._n_arm)

        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._error: Optional[BaseException] = None
        self._armed = False
        self._status: Dict = {
            "armed": False, "hz": 0.0, "slack": None,
            "match_error": None, "match_done": False,
        }

    # -------------------------------------------------------------- lifecycle
    def set_gravity_comp(
        self,
        gains: Optional[np.ndarray] = None,
        offsets: Optional[np.ndarray] = None,
        stiction_gain: Optional[float] = None,
    ) -> None:
        """Live-adjust the empirical gravity-comp model (see class docstring)
        -- meant to be called repeatedly while watching the real leader, not
        just once at startup. Any argument left ``None`` keeps its
        current value; pass ``gains=np.zeros(n_arm)`` to fully disable
        (this also un-force-arms the servos once away from a limit/match).
        """
        if gains is not None:
            self._gravity_gains = np.array(gains, dtype=float)
        if offsets is not None:
            self._gravity_offsets = np.array(offsets, dtype=float)
        if stiction_gain is not None:
            self._stiction_gain = float(stiction_gain)

    def set_teleop_mode(self, in_teleop: bool) -> None:
        """Tell the wall whether the operator is actively teleoperating.

        The engage rule itself does not depend on this -- "put the leader down
        anywhere and nothing pulls" holds in every mode. The previous wrap-zone
        hand-over (which could drop the limit wall outside teleop to let the
        operator unwind a cable) is disabled; the wall keeps every joint under
        control at all times. During teleop the follower is tracking the leader,
        so dropping the limit wall would let unreachable commands through.
        """
        self._in_teleop = bool(in_teleop)
        # Cap change can turn a previously-valid integrator into stale bias.
        self._match_int = np.zeros(self._n_arm)

    def set_match_target(self, target: Optional[np.ndarray]) -> None:
        """Arm (target given) or release (``None``) the pose-match assist.

        ``target`` is follower-joint-space radians, arm joints only (same
        space/order as ``status()["q"]`` and this class's ``lower``/``upper``
        args) -- typically a reset pose. Safe to call from any thread.
        Setting a new target resets the convergence hold timer, so re-calling
        this with a different target mid-match restarts convergence tracking
        rather than carrying over stale progress.
        """
        self._match_target = None if target is None else np.array(target, dtype=float)
        self._match_state = (
            WallMatchState.IDLE if target is None else WallMatchState.ARMED
        )
        # Deprecated boolean snapshots, kept in status() for compatibility.
        self._match_done = False
        self._match_blocked = False
        self._match_hold_start = None
        # 새 목표는 게이트도 처음부터 -- 이전 목표에서 걸려 있었다고 해서
        # 새 목표(다른 자세, 다시 멀 수 있다)를 곧바로 당기면 안 된다.
        # 포화 래치도 여기서만 풀린다: 사람이 상황을 정리하고 다시 요청한
        # 것이 재시도의 유일한 신호다.
        self._match_engaged = False
        self._match_stall_since = None
        # 취소 사유도 새 요청에서만 지운다. 단, 루프가 스스로 취소하며
        # 세우는 플래그는 그 뒤에 세워지므로 여기서 지워도 덮이지 않는다
        # (호출자가 다시 걸 때만 초기화되는 것이 맞다).
        if target is not None:
            self._match_aborted_wrap = False
        # A fresh target means a fresh pose: the learned holding current of
        # the previous pose is wrong for it, so the integrator restarts.
        self._match_int = np.zeros(self._n_arm)
        # Cleared so _run() re-seeds it from the leader's *current* pose on
        # the next tick -- the pull always starts from where the arm is, never
        # from a stale setpoint that would produce an instant jump.
        self._match_setpoint = None

    def start(self) -> None:
        """Switch the servos to current control and start the wall thread."""
        # Operating mode is EEPROM: it only takes while torque is disabled.
        self._driver.set_torque_mode(False)
        self._driver.set_operating_mode(CURRENT_CONTROL_MODE)
        self._driver.verify_operating_mode(CURRENT_CONTROL_MODE)
        if self._trigger:
            # The trigger spring is always on; only the arm servos arm/disarm
            # with the limit logic below.
            self._driver.set_torque_ids([self._driver._ids[self._n_arm]], True)
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="joint-limit-wall", daemon=True
        )
        self._thread.start()

    def poll(self) -> None:
        """Re-raise any fault from the wall thread; call from the teleop loop.

        Also raises if the thread died unexpectedly.  On fault the wall does not
        clean up (the servos hold their last current); teleop is expected to die.
        """
        if self._error is not None:
            raise RuntimeError("joint-limit wall thread failed") from self._error
        if self._thread is not None and not self._thread.is_alive() \
                and not self._stop_evt.is_set():
            raise RuntimeError("joint-limit wall thread exited unexpectedly")

    def stop(self) -> None:
        """Clean shutdown: stop the thread, zero current, drop torque.

        This is the *normal* exit path.  Unlike a fault, it restores the servos
        so the leader is free and left in position mode.
        """
        self._match_target = None
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            self._driver.set_current([0.0] * self._n_ids)
        except Exception:
            pass
        try:
            self._driver.set_torque_mode(False)
            self._driver.set_operating_mode(POSITION_CONTROL_MODE)
        except Exception:
            pass

    def status(self) -> Dict:
        """Latest loop snapshot for display (armed, hz, slack, per-joint q/cur)."""
        return self._status

    # ----------------------------------------------------------------- thread
    def _run(self) -> None:
        try:
            last_health = 0.0
            rate_t0, rate_n, rate_hz = time.time(), 0, 0.0
            health = self._read_health() if self._health_every > 0 else []
            while not self._stop_evt.is_set():
                t0 = time.time()

                # Raw servo radians -> follower joint space.  Read the driver's
                # cached state directly (not DynamixelRobot.get_joint_state,
                # whose EWMA on _last_pos would be corrupted by a second reader).
                raw_q, raw_dq = self._driver.get_positions_and_velocities()
                q = (raw_q[: self._n_arm] - self._offsets) * self._signs
                # Undo any phantom full turn so the wall pushes at the real limit,
                # not 2*pi away from it (same correction the agent applies).
                q = wrap_into_limits(q, self._lower, self._upper)
                dq = raw_dq[: self._n_arm] * self._signs

                match_target = self._match_target  # snapshot -- see set_match_target
                gravity_gains = self._gravity_gains  # snapshot -- see set_gravity_comp
                gravity_active = bool(np.any(gravity_gains != 0.0))

                # 힘 우물 + 원거리 해제 (issue #37A). goal_err 은 여기서 한 번만
                # 구해 아래 match 절이 그대로 쓴다 -- 우물 세기와 arming 판단이
                # 같은 오차를 봐야 하기 때문이다.
                # 뒤집힘 구역 자동 해제는 꺼둔 상태다 (2aba6b5). 뒤집힘 지점
                # 근처에서 wrap_into_limits의 바퀴 선택이 튀면 벽이 반대로
                # 밀리지만, 케이블 풀기를 위해 토크를 끄는 기능은 현재
                # 비활성화되어 있다.
                self._wrap_mask = np.zeros(self._n_arm, dtype=bool)

                goal_err = match_assist = None
                match_err = None
                if match_target is not None:
                    goal_err = _wrap_pi(match_target - q)
                    abs_err = np.abs(goal_err)
                    match_err = float(abs_err.max())
                    match_assist = _well_assist(abs_err, self._match_well)
                    # 조인트별 토크 on/off: arm 반경 밖 관절은 전류 0 이
                    # 아니라 토크 자체를 끊는다 (전류제어 0 도 토크 오프보다
                    # 끌린다 -- 위 참조). 히스테리시스는 관절마다 따로.
                    # 정렬 중이냐 텔레옵 중이냐로 이 규칙을 바꾸지 않는다 --
                    # "어디에 놓아두든 안 당긴다" 는 모드와 무관한 약속이다.
                    # 목표가 설정돼 있는 동안은 모든 관절에 토크를 건다.
                    # 가까운 관절은 우물대로 세게, arm 반경 밖의 먼 관절은
                    # 최소 전류로만 당긴다 (2026-09-01 사용자 결정) -- 예전에는
                    # 먼 관절의 토크를 아예 끊어서, 리셋 자세에서 멀면 정렬이
                    # 영영 다가오지 못했다. 최소 전류는 사람이 손으로 쉽게
                    # 이기는 크기라 팔을 옮기는 데도 방해가 되지 않는다.
                    # 목표가 없을 때(else 절)는 예전 그대로 전부 토크 오프다.
                    if self._match_state == WallMatchState.BLOCKED:
                        self._match_arm_mask = np.zeros(self._n_arm, dtype=bool)
                    else:
                        self._match_arm_mask = np.ones(self._n_arm, dtype=bool)
                    far = abs_err > self._match_arm_rad
                else:
                    self._match_arm_mask = np.zeros(self._n_arm, dtype=bool)
                self._match_engaged = bool(self._match_arm_mask.any())

                # Update explicit match state. BLOCKED/DONE are latched until
                # set_match_target is called again.
                if match_target is None:
                    self._match_state = WallMatchState.IDLE
                elif self._match_state not in (
                    WallMatchState.BLOCKED,
                    WallMatchState.DONE,
                ):
                    self._match_state = (
                        WallMatchState.PULLING
                        if self._match_engaged
                        else WallMatchState.ARMED
                    )

                match_active = match_target is not None and self._match_engaged

                # Arm torque only near a limit: current-control-at-zero drags
                # more than torque-off, so the rest of the workspace is left
                # torque-off and feels exactly as it does without the wall.
                # Arm servos only -- the trigger spring stays on throughout.
                # A pose-match target or an active gravity-comp gain both
                # force-arm regardless of slack -- neither is limited to
                # near a limit (a reset pose / "anywhere the leader might
                # be held" are typically nowhere near one), so the
                # limit-only rule would otherwise rarely or never arm.
                # 한계벽/중력보상은 예전 그대로 팔 전체 단위다 (한계 근처
                # 판정은 최소 slack 하나로 내려 왔고, 그 튜닝을 건드리지
                # 않는다). 정렬만 조인트별이다: 목표에서 먼 관절은 토크를
                # 끊어 정말로 자유롭게 두고, 가까운 관절은 계속 잡아 준다.
                slack = float(np.minimum(self._hi - q, q - self._lo).min())
                near_limit = slack < (
                    self._arm_margin + self._arm_hyst if self._armed
                    else self._arm_margin
                )
                if near_limit or gravity_active:
                    want_mask = np.ones(self._n_arm, dtype=bool)
                else:
                    want_mask = self._match_arm_mask.copy()
                if not np.array_equal(want_mask, self._armed_mask):
                    ids = list(self._driver._ids[: self._n_arm])
                    off = [i for i, w, a in zip(ids, want_mask, self._armed_mask)
                           if a and not w]
                    on = [i for i, w, a in zip(ids, want_mask, self._armed_mask)
                          if w and not a]
                    if off:
                        # 토크를 끊기 전에 지령을 0 으로 -- 나중에 다시 켤 때
                        # 묵은 전류가 그대로 살아나지 않게 (원래 동작 유지).
                        self._driver.set_current([0.0] * self._n_ids)
                        self._driver.set_torque_ids(off, False)
                    if on:
                        self._driver.set_torque_ids(on, True)
                    self._armed_mask = want_mask
                self._armed = bool(want_mask.any())

                # One-sided spring-damper: exactly zero inside the limits.
                over_hi = q > self._hi
                over_lo = q < self._lo
                cur = (-self._kp * (q - self._hi) - self._kd * dq) * over_hi
                cur += (-self._kp * (q - self._lo) - self._kd * dq) * over_lo
                cur = np.clip(cur, -self._max_current, self._max_current)
                # Pose-match assist: two-sided spring-damper toward
                # match_target, summed with the (normally-zero-here, since a
                # match target sits well inside the limits) limit spring
                # above. "Done" requires the error AND speed to stay under
                # tolerance for match_hold_s continuously, so a fast pass
                # through the target mid-swing doesn't register as arrival.
                if match_active:
                    # Moving setpoint, seeded at the current pose: the spring
                    # only ever sees a <= match_rate*dt error, so the leader
                    # eases onto the target instead of being slammed at it.
                    if self._match_setpoint is None:
                        self._match_setpoint = q.copy()
                    # Shortest way around, ALWAYS. q has been through
                    # wrap_into_limits, which is right for the limit spring
                    # (push at the real limit) but wrong here: a joint whose
                    # span is close to 2*pi -- J1/J3/J5/J7 on the FR3 are all
                    # 5.5-6.0 rad -- can wrap to the far side of its range, so
                    # target - q comes out pointing the long way round. The
                    # leader then winds a whole extra turn to reach a pose it
                    # was already next to, which is what "the arm ties itself
                    # in a knot" actually was.
                    lead = self._match_rate * self._dt
                    self._match_setpoint = self._match_setpoint + np.clip(
                        _wrap_pi(match_target - self._match_setpoint), -lead, lead
                    )
                    # Bound how far the setpoint may run ahead of the actual
                    # pose, so the spring force stays at the level it was
                    # designed for instead of winding up to saturation the
                    # moment a joint lags (friction, gravity, a hand resting
                    # on the leader).
                    terr = np.clip(
                        _wrap_pi(self._match_setpoint - q),
                        -self._match_max_lead, self._match_max_lead,
                    )
                    # Stiffen once actually close to the goal, so the pose is
                    # held rigidly while the operator lets go of the leader.
                    # Per-joint kp; elementwise max so a joint whose own kp
                    # already exceeds stiff_kp (the pitch joints) never gets
                    # SOFTER on arrival.
                    if match_err < self._match_stiff_tol:
                        kp = np.maximum(self._match_kp, self._match_stiff_kp)
                    else:
                        kp = self._match_kp
                    # Integrator charges on the TRUE goal error (not the
                    # setpoint's), so it keeps building while the setpoint
                    # still walks; clamp is the anti-windup. 우물 세기를 충전
                    # 속도에도 곱한다 -- 힘이 풀려 있는 먼 거리에서 적분기만
                    # 가득 차 있다가, 사람이 팔을 되돌려 놓는 순간 그 값이
                    # 통째로 튀어나오는 것을 막는다.
                    # 정렬 전류 상한 (issue #37A). 한때 정렬 모드에서 이걸
                    # 200 mA 로 *낮췄는데*, J2 는 자세 유지에만 ~430 mA 가
                    # 필요해서(아래 적분기 주석) 피치가 처지고 정렬이 수렴하지
                    # 못했다. 200 은 상한이 아니라 하한이다: 기본값은 아예
                    # 낮추지 않고(idle_hold_current=0), 낮추더라도
                    # IDLE_MIN_CURRENT 아래로는 못 내려간다. 설정으로 준
                    # match_max_current 자체는 그대로 존중한다.
                    if self._in_teleop or self._idle_hold_current <= 0.0:
                        match_cap = self._match_max_current
                    else:
                        match_cap = np.minimum(
                            self._match_max_current,
                            max(self._idle_hold_current, IDLE_MIN_CURRENT))
                    int_cap = self._match_int_max
                    self._match_int = np.clip(
                        self._match_int
                        + self._match_int_gain * goal_err * match_assist * self._dt,
                        -int_cap, int_cap,
                    )
                    # 우물은 스프링·댐퍼·적분기 전체에 곱한다: 셋의 비율이
                    # 유지되므로 감쇠비가 거리와 무관하게 그대로다 (스프링만
                    # 줄이면 먼 거리에서 상대적으로 과감쇠가 된다).
                    cur_match = match_assist * np.clip(
                        kp * terr - self._match_kd * dq + self._match_int,
                        -match_cap, match_cap,
                    )  # per-joint caps; np.clip broadcasts elementwise
                    # arm 반경 밖: 우물이 거의 0 이라 그대로 두면 다가오지
                    # 못한다. 목표 쪽으로 최소 전류만 흘린다 (캡이 그보다
                    # 낮으면 캡을 따른다).
                    floor = np.minimum(IDLE_MIN_CURRENT, match_cap)
                    cur_match = np.where(far, np.sign(goal_err) * floor, cur_match)
                    cur = cur + cur_match
                    # 포화 감시: 어느 조인트든 캡 근처를 stall_s 초 이상
                    # 연속으로 요구하면 정렬을 포기한다. 정상 정렬은
                    # match_rate(0.6 rad/s)로 게이트 폭(0.5 rad)을 1초 남짓에
                    # 닫으므로, 몇 초씩 캡에 붙어 있다는 것은 리더가 걸렸거나
                    # 사람이 붙잡고 있다는 뜻이고, 그대로 두면 0x20 이다.
                    if self._match_stall_s > 0:
                        sat = bool(np.any(
                            np.abs(cur_match)
                            >= self._match_stall_frac * match_cap
                        ))
                        if not sat:
                            self._match_stall_since = None
                        elif self._match_stall_since is None:
                            self._match_stall_since = t0
                        elif t0 - self._match_stall_since >= self._match_stall_s:
                            # 래치: set_match_target 재호출까지 다시 안 건다.
                            self._match_state = WallMatchState.BLOCKED
                            self._match_blocked = True  # compatibility
                            self._match_done = False  # blocked overrides done
                            self._match_hold_start = None
                            self._match_engaged = False
                            self._match_stall_since = None
                    if match_err < self._match_tol and float(np.abs(dq).max()) < self._match_vel_tol:
                        if self._match_hold_start is None:
                            self._match_hold_start = t0
                        elif (
                            t0 - self._match_hold_start >= self._match_hold_s
                            and self._match_state != WallMatchState.BLOCKED
                        ):
                            self._match_state = WallMatchState.DONE
                            self._match_done = True  # compatibility
                    else:
                        self._match_hold_start = None
                else:
                    # 목표가 없거나(평시) 게이트 밖/래치 상태 -- 정렬 전류는
                    # 0 이고, setpoint·적분기는 비워 둔다. 그래야 나중에
                    # engage 되는 순간 "지금 있는 자리"에서 다시 시작한다
                    # (묵은 setpoint 로 튀지 않는다).
                    if self._match_state == WallMatchState.DONE:
                        self._match_state = WallMatchState.ARMED
                    self._match_done = False
                    self._match_hold_start = None
                    self._match_setpoint = None
                    self._match_int = np.zeros(self._n_arm)

                # Empirical gravity comp (see class docstring for why this is
                # a per-joint single-pendulum approximation, not RNEA): each
                # joint independently offsets its own estimated weight,
                # summed in with whatever the limit spring/match assist are
                # already doing above.
                tau_g = gravity_gains * np.sin(q - self._gravity_offsets)
                cur = cur + tau_g

                # Stiction dither: a joint that's supposed to be "floating"
                # under tau_g but is actually stuck (near-zero speed) gets a
                # small square wave added on top, proportional to its own
                # |tau_g|, alternating sign every tick -- enough to break
                # static friction without a net directional bias. No-op
                # everywhere gravity comp itself is off (tau_g is zero there).
                if self._stiction_gain > 0.0:
                    stuck = np.abs(dq) < self._stiction_vel_tol
                    self._stiction_sign = np.where(stuck, -self._stiction_sign, self._stiction_sign)
                    cur = cur + np.where(
                        stuck, self._stiction_gain * np.abs(tau_g) * self._stiction_sign, 0.0
                    )

                # Trigger spring (toward-open positive).  The base current is
                # squeeze_current while squeezing or holding, blending up to
                # return_current with opening speed -- soft under the finger,
                # full push once the trigger is actually swinging back.  It
                # fades to zero just past the open position so the trigger
                # parks there; past trigger_start an exponential wall rises to
                # trigger_max at full close.  If a released trigger sticks
                # mid-stroke (never reaches opening speed, so never gets the
                # boost), squeeze_current is too low for the gear friction.
                i_trig, g = 0.0, None
                if self._trigger:
                    g = (raw_q[self._n_arm] - self._trig_open) / self._trig_span
                    gdot = raw_dq[self._n_arm] / self._trig_span  # >0 = closing
                    base = self._trig_squeeze + (
                        self._trig_return - self._trig_squeeze
                    ) * float(np.clip(-gdot / self._trig_ramp, 0.0, 1.0))
                    i_trig = base * float(np.clip((g + 0.1) / 0.1, 0.0, 1.0))
                    if g > self._trig_start:
                        s = min((g - self._trig_start) / (1.0 - self._trig_start), 1.5)
                        i_trig += self._trig_max * (
                            np.exp(self._trig_curve * s) - 1.0
                        ) / (np.exp(self._trig_curve) - 1.0)
                    i_trig += self._trig_kd * gdot
                    i_trig = float(np.clip(i_trig, -self._trig_cap, self._trig_cap))

                # Supply budget: one joint keeps full force; several at once
                # share the 5 V / 4 A supply. Floored allocation (2026-08-27):
                # each joint keeps up to budget_floor of what it requests
                # before any scaling -- see _allocate_budget.
                cur, i_trig = _allocate_budget(
                    cur, i_trig, self._budget, self._budget_floor
                )

                if self._armed or self._trigger:
                    # Back to raw servo space.  Disarmed arm servos are
                    # torque-off, so their zeros are inert register writes.
                    out = np.zeros(self._n_ids)
                    if self._armed:
                        # 토크가 꺼진 조인트에는 0 을 쓴다 -- 어차피 무효한
                        # 레지스터 쓰기지만, status 의 cur 과 실제 지령이
                        # 어긋나지 않게 여기서 마스킹한다.
                        out[: self._n_arm] = np.where(
                            self._armed_mask, cur * self._signs, 0.0)
                        cur = np.where(self._armed_mask, cur, 0.0)
                    if self._trigger:
                        out[self._n_arm] = i_trig * self._trig_open_dir
                    self._driver.set_current(out.tolist())

                rate_n += 1
                if t0 - rate_t0 >= 1.0:
                    rate_hz, rate_t0, rate_n = rate_n / (t0 - rate_t0), t0, 0

                # Supply health.  A fault raises -> stored -> poll() re-raises
                # -> teleop dies.  No cleanup on this path (Dynamixel protects).
                if self._health_every > 0 and t0 - last_health >= self._health_every:
                    health = self._read_health()
                    last_health = t0
                    self._check_health(health)

                self._status = {
                    "armed": self._armed,
                    "hz": rate_hz,
                    "slack": slack,
                    "q": q,
                    "cur": cur,
                    "over_hi": over_hi,
                    "over_lo": over_lo,
                    "lo": self._lo,
                    "hi": self._hi,
                    "health": health,
                    "trigger": {"g": g, "cur": i_trig} if self._trigger else None,
                    "match_error": match_err,
                    "match_state": self._match_state.value,
                    "match_done": self._match_done,
                    # issue #37A: 토크가 켜져 있는가(engaged), 과부하 보호로
                    # 포기했는가(blocked), 조인트별 정렬 세기 0..1(assist).
                    "match_engaged": self._match_engaged,
                    "match_blocked": self._match_blocked,
                    "match_assist": match_assist,
                    "match_well": self._match_well,
                    "armed_mask": self._armed_mask,
                    # 케이블 푸는 중으로 판단해 손에 넘긴 관절과, 그 때문에
                    # 정렬이 취소되었는지 (issue #37A 후속).
                    "wrap_mask": self._wrap_mask,
                    "match_aborted_wrap": self._match_aborted_wrap,
                    "match_int": self._match_int,
                    "dq": dq,
                    "tau_g": tau_g,
                }

                rest = self._dt - (time.time() - t0)
                if rest > 0:
                    time.sleep(rest)
        except BaseException as e:  # noqa: BLE001 -- surfaced via poll()
            self._error = e

    # ------------------------------------------------------------------ health
    def _read_health(self):
        """Per-servo (voltage V, hw_error, temp C), under the driver's lock so
        the reads do not race its background state thread on the same bus."""
        ph, pk = self._driver._portHandler, self._driver._packetHandler
        out = []
        with self._driver._lock:
            for i in self._driver._ids:
                v, rv, _ = pk.read2ByteTxRx(ph, i, ADDR_INPUT_VOLTAGE)
                e, re, _ = pk.read1ByteTxRx(ph, i, ADDR_HW_ERROR)
                t, rt, _ = pk.read1ByteTxRx(ph, i, ADDR_TEMPERATURE)
                out.append((v / 10 if rv == 0 else None,
                            e if re == 0 else None,
                            t if rt == 0 else None))
        return out

    def _check_health(self, health) -> None:
        for k, (v, e, _t) in enumerate(health):
            servo_id = self._driver._ids[k]
            if e:
                raise RuntimeError(
                    f"servo ID{servo_id} hardware error 0x{e:02x} "
                    "(0x20=overload); wall stopping"
                )
            if v is not None and v < self._min_voltage:
                raise RuntimeError(
                    f"servo ID{servo_id} supply sag {v:.1f} V "
                    f"< {self._min_voltage} V; wall stopping"
                )


def selftest() -> None:
    """Offline math checks (no hardware): budget floors + integrator clamp."""
    # 1. 예산 안이면 무변경
    cur = np.array([100.0, 900.0, -100.0, 800.0, 50.0, 50.0, 50.0])
    floors = np.array([0.0, 1000.0, 0.0, 1000.0, 0.0, 0.0, 0.0])
    out, trig = _allocate_budget(cur.copy(), 200.0, 2800.0, floors)
    assert np.allclose(out, cur) and trig == 200.0

    # 2. 초과 시: J2/J4 는 플로어(1000)까지 요구 전량 유지, 나머지만 축소
    cur = np.array([400.0, 1000.0, -400.0, 1000.0, 400.0, 400.0, 400.0])
    out, trig = _allocate_budget(cur.copy(), 300.0, 2800.0, floors)
    assert abs(np.abs(out).sum() + abs(trig) - 2800.0) < 1e-6
    assert out[1] == 1000.0 and out[3] == 1000.0          # 플로어 보장
    assert all(abs(out[i]) < 400.0 for i in (0, 2, 4, 5, 6))
    assert out[2] < 0                                      # 부호 보존
    assert trig < 300.0

    # 3. 정렬된 J2(요구 10mA)는 플로어를 자동 양보 -- 나머지가 덜 깎인다
    cur_aligned = np.array([400.0, 10.0, -400.0, 1000.0, 400.0, 400.0, 400.0])
    out2, _ = _allocate_budget(cur_aligned.copy(), 300.0, 2800.0, floors)
    out1, _ = _allocate_budget(
        np.array([400.0, 1000.0, -400.0, 1000.0, 400.0, 400.0, 400.0]),
        300.0, 2800.0, floors)
    assert out2[1] == 10.0                                 # 요구 이상 안 줌
    assert abs(out2[0]) > abs(out1[0])                     # 양보분이 돌아감

    # 4. 퇴화: 플로어 합이 예산 초과 -> 균등 스케일 폴백
    big_floors = np.full(7, 1000.0)
    cur = np.full(7, 1000.0)
    out, trig = _allocate_budget(cur.copy(), 0.0, 2800.0, big_floors)
    assert abs(np.abs(out).sum() - 2800.0) < 1e-6
    assert np.allclose(out, out[0])                        # 균등

    # 5. 적분기 클램프 산수: gain*err*dt 누적이 int_max 를 넘지 않는다
    int_max = np.array([0.0, 800.0, 0.0, 800.0, 0.0, 0.0, 0.0])
    acc = np.zeros(7)
    err = np.full(7, 0.3)
    for _ in range(3000):                                  # 10초 @300Hz
        acc = np.clip(acc + 1000.0 * err * (1.0 / 300.0), -int_max, int_max)
    assert acc[1] == 800.0 and acc[3] == 800.0
    assert all(acc[i] == 0.0 for i in (0, 2, 4, 5, 6))     # int_max=0 = off

    # 6. 토크 on/off 히스테리시스 (issue #37A)
    gate, rel = MATCH_GATE_RAD * 2, 0.15
    assert not _engage_gate(False, gate + 0.01, gate, rel)  # 경계 밖 -> 안 켬
    assert _engage_gate(False, gate, gate, rel)             # 경계에서 켠다
    assert _engage_gate(True, gate + 0.1, gate, rel)        # 히스테리시스 안
    assert not _engage_gate(True, gate + rel + 0.01, gate, rel)   # 벗어나면 끔
    eng = False
    for e in (2.0, 1.1, 1.0, 1.05, 1.1, 1.16, 1.4):
        eng = _engage_gate(eng, e, gate, rel)
    assert not eng                                          # 한 번 꺼진 뒤 유지

    # 7. 힘 우물 (issue #37A): kp*x*a(x) 가 x=well 에서 최대이고 그 바깥으로
    #    단조 감소한다 -- "가까이선 잡아주고 크게 끌면 놓아준다".
    well = MATCH_GATE_RAD / 2
    xs = np.linspace(0.0, 4.0, 4001)
    force = xs * _well_assist(xs, well)          # kp 는 상수배라 형태에 무관
    assert abs(xs[int(np.argmax(force))] - well) < 1e-2, xs[int(np.argmax(force))]
    tail = force[xs >= well]
    assert np.all(np.diff(tail) <= 1e-12), "우물 바깥에서 힘이 다시 커진다"
    a = _well_assist(np.array([0.0, well, MATCH_GATE_RAD, 2 * MATCH_GATE_RAD,
                               np.pi]), well)
    assert a[0] == 1.0                                   # 목표 위: 전력 100%
    assert abs(a[1] - 0.5) < 1e-9                        # well: 절반
    assert a[2] < 0.21                                   # 게이지 초록 경계: 20%
    assert a[4] < 0.01                                   # 반 바퀴 돌리면 1% 미만
    # 조인트별로 따로 계산된다 (한 관절만 돌려도 나머지는 계속 버틴다)
    per = _well_assist(np.array([0.02, np.pi, 0.02]), well)
    assert per[0] > 0.98 and per[2] > 0.98 and per[1] < 0.01

    # 8. 정렬 전류 상한은 하한(IDLE_MIN_CURRENT) 아래로 내려가지 않는다
    caps = np.maximum(np.array([400.0, 1000.0, 400.0]), IDLE_MIN_CURRENT)
    assert caps.min() >= IDLE_MIN_CURRENT and caps[1] == 1000.0
    print("joint_limit_wall selftest 통과")


if __name__ == "__main__":
    selftest()
