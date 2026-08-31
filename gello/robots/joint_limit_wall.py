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
The pull is *gated* (2026-08-31, issue #37A): a target alone never energizes
anything. Current flows only once the leader is already within
``match_gate_rad`` of it -- the same threshold the collector's pose-match
gauge paints green, so "it pulls when the gauge is green" is literally one
number (``MATCH_GATE_RAD``) shared by the display and the motors. Outside the
gate the arm servos stay torque-off, i.e. free to be carried there by hand,
and engage by themselves on arrival. The gate lives here rather than in the
callers because the rule it enforces is about the hardware: put the leader
down anywhere you like and nothing over-torques. A second guard covers the
case the gate cannot -- a joint stuck at its current cap for
``match_stall_s`` seconds (jammed, or a hand holding it) means the pull is
losing, so the assist gives up and latches off until the caller sets a
target again; servos are more expensive than one alignment.
It lives here for the same reason the limit spring does -- ``set_current``
sync-writes the whole servo vector, so a second control loop calling it
concurrently would fight this one over the bus. While a match target is set
the arm servos are force-armed regardless of limit slack (the limit spring
alone only arms near a limit); once the caller clears the target (typically
right after ``status()`` reports ``match_done``), arming reverts to the
limit-only rule, which disarms torque again at a normal (not-near-a-limit)
pose -- so the leader goes back to being freely back-drivable for teleop
without any extra cleanup call. Gains/tolerances below are untuned defaults
(no access to the real leader while writing this) -- expect to retune
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

import threading
import time
from typing import Dict, Optional, Tuple

import numpy as np

from gello.hw.dynamixel.driver import (
    CURRENT_CONTROL_MODE,
    POSITION_CONTROL_MODE,
    DynamixelDriverProtocol,
)

# XL330 control-table addresses for supply-health monitoring.
ADDR_HW_ERROR = 70        # Hardware Error Status (1 B, bitfield)
ADDR_INPUT_VOLTAGE = 144  # Present Input Voltage (2 B, units of 0.1 V)
ADDR_TEMPERATURE = 146    # Present Temperature (1 B, deg C)

#: 자세 정렬을 걸기 시작하는 최대 조인트 오차 (rad) -- 이 파일이 정본이다.
#: 수집 GUI 의 "자세 매칭" 게이지가 초록/빨강을 가르는 값과 같은 상수여야
#: 한다 (gello.gui.libero_gui_worker.GATE_RAD 가 이걸 가져다 쓴다): 게이지가
#: 초록일 때만 모터가 당긴다는 규칙이 눈에 보이는 것과 실제 동작에서 같은
#: 숫자로 성립해야 하기 때문이다 (GitHub issue #37A).
MATCH_GATE_RAD = 0.5


def _engage_gate(engaged: bool, err: float, gate: float, release: float) -> bool:
    """정렬 engage 여부 (히스테리시스). 순수 함수 -- selftest 에서 검증.

    아직 안 걸렸으면 ``err <= gate`` 여야 걸리고, 한 번 걸린 뒤에는
    ``gate + release`` 를 넘어야 풀린다. 히스테리시스가 없으면 게이트
    경계에서 전류가 켜졌다 꺼졌다 하며 리더가 덜덜 떨린다.
    """
    return err <= (gate + release) if engaged else err <= gate


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
        match_tol: float = 0.05,
        match_vel_tol: float = 0.15,
        match_hold_s: float = 0.3,
        match_rate: float = 0.6,
        match_max_lead: float = 0.35,
        match_stiff_kp: float = 1200.0,
        match_stiff_tol: float = 0.10,
        match_int_gain: float = 1000.0,
        match_int_max=0.0,  # scalar or per-arm-joint sequence (mA); 0 = off
        match_gate_rad: float = MATCH_GATE_RAD,
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
        self._match_done = False
        self._match_hold_start: Optional[float] = None

        # Engage 게이트 (2026-08-31, issue #37A). 정렬 목표가 설정돼 있어도
        # 리더가 목표 근처(게이지가 초록)에 올 때까지는 전류를 한 톨도 걸지
        # 않는다. 왜 호출자가 아니라 여기냐면: 규칙이 "리더암을 어디에
        # 놓아두든 과토크가 걸리지 않는다" 이기 때문이다 -- GUI 든 정렬
        # 스크립트든 앞으로 생길 무엇이든, set_match_target 을 부르는 모든
        # 경로가 같은 보호를 받아야 한다. 게이트 밖에서는 팔이 토크 오프라
        # 사람이 손으로 자유롭게 가져다 놓을 수 있고, 그 순간 자동으로 걸린다.
        self._match_gate = float(match_gate_rad)
        self._match_gate_release = float(match_gate_release)
        self._match_engaged = False
        # 포화 감시. J2/J4 는 kp*max_lead = 2800*0.35 = 980 mA 라, 게이트
        # 안(0.5 rad)에서도 사실상 캡(1000 mA)으로 당긴다 -- 정상 정렬은
        # 1~2초면 끝나므로 그 전류가 몇 초씩 이어진다는 건 리더가 뭔가에
        # 걸렸거나 사람이 붙잡고 있다는 뜻이고, 그게 곧 과부하(0x20)다.
        # 그때는 싸우지 않고 정렬을 포기한다(래치) -- 서보를 살리는 쪽이
        # 정렬 한 번보다 싸다. 래치는 set_match_target 재호출로만 풀린다.
        self._match_stall_frac = float(match_stall_frac)
        self._match_stall_s = float(match_stall_s)
        self._match_stall_since: Optional[float] = None
        self._match_blocked = False

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
        self._match_done = False
        self._match_hold_start = None
        # 새 목표는 게이트도 처음부터 -- 이전 목표에서 걸려 있었다고 해서
        # 새 목표(다른 자세, 다시 멀 수 있다)를 곧바로 당기면 안 된다.
        # 포화 래치도 여기서만 풀린다: 사람이 상황을 정리하고 다시 요청한
        # 것이 재시도의 유일한 신호다.
        self._match_engaged = False
        self._match_stall_since = None
        self._match_blocked = False
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

                # Engage 게이트 (issue #37A): 목표가 있어도 리더가 목표
                # 근처(GUI 자세 매칭 게이지가 초록)에 올 때까지는 걸지
                # 않는다. 아래 arming 이 이걸 보고 토크 자체를 안 켜므로,
                # 게이트 밖 리더는 그냥 자유롭게 움직이는 팔이다.
                match_err = None
                if match_target is not None:
                    match_err = float(
                        np.abs(_wrap_pi(match_target - q)).max()
                    )
                    self._match_engaged = (
                        False if self._match_blocked else
                        _engage_gate(self._match_engaged, match_err,
                                     self._match_gate, self._match_gate_release)
                    )
                else:
                    self._match_engaged = False
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
                slack = float(np.minimum(self._hi - q, q - self._lo).min())
                want = match_active or gravity_active or slack < (
                    self._arm_margin + self._arm_hyst if self._armed
                    else self._arm_margin
                )
                if want != self._armed:
                    if not want:
                        self._driver.set_current([0.0] * self._n_ids)
                    self._driver.set_torque_ids(
                        self._driver._ids[: self._n_arm], want
                    )
                    self._armed = want

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
                    goal_err = _wrap_pi(match_target - q)
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
                    # still walks; clamp is the anti-windup.
                    self._match_int = np.clip(
                        self._match_int
                        + self._match_int_gain * goal_err * self._dt,
                        -self._match_int_max, self._match_int_max,
                    )
                    cur_match = np.clip(
                        kp * terr - self._match_kd * dq + self._match_int,
                        -self._match_max_current, self._match_max_current,
                    )  # per-joint caps; np.clip broadcasts elementwise
                    cur = cur + cur_match
                    # 포화 감시: 어느 조인트든 캡 근처를 stall_s 초 이상
                    # 연속으로 요구하면 정렬을 포기한다. 정상 정렬은
                    # match_rate(0.6 rad/s)로 게이트 폭(0.5 rad)을 1초 남짓에
                    # 닫으므로, 몇 초씩 캡에 붙어 있다는 것은 리더가 걸렸거나
                    # 사람이 붙잡고 있다는 뜻이고, 그대로 두면 0x20 이다.
                    if self._match_stall_s > 0:
                        sat = bool(np.any(
                            np.abs(cur_match)
                            >= self._match_stall_frac * self._match_max_current
                        ))
                        if not sat:
                            self._match_stall_since = None
                        elif self._match_stall_since is None:
                            self._match_stall_since = t0
                        elif t0 - self._match_stall_since >= self._match_stall_s:
                            # 래치: set_match_target 재호출까지 다시 안 건다.
                            self._match_blocked = True
                            self._match_engaged = False
                            self._match_stall_since = None
                    if match_err < self._match_tol and float(np.abs(dq).max()) < self._match_vel_tol:
                        if self._match_hold_start is None:
                            self._match_hold_start = t0
                        elif t0 - self._match_hold_start >= self._match_hold_s:
                            self._match_done = True
                    else:
                        self._match_hold_start = None
                else:
                    # 목표가 없거나(평시) 게이트 밖/래치 상태 -- 정렬 전류는
                    # 0 이고, setpoint·적분기는 비워 둔다. 그래야 나중에
                    # engage 되는 순간 "지금 있는 자리"에서 다시 시작한다
                    # (묵은 setpoint 로 튀지 않는다).
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
                        out[: self._n_arm] = cur * self._signs
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
                    "match_done": self._match_done,
                    # issue #37A: 게이지가 초록인가(engaged), 과부하 보호로
                    # 포기했는가(blocked), 그 판정에 쓴 임계값(gate).
                    "match_engaged": self._match_engaged,
                    "match_blocked": self._match_blocked,
                    "match_gate": self._match_gate,
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

    # 6. engage 게이트 (issue #37A): 멀리 있으면 안 걸리고, 게이트 안에서
    #    걸리고, 한 번 걸린 뒤에는 release 만큼 더 나가야 풀린다.
    gate, rel = MATCH_GATE_RAD, 0.15
    assert not _engage_gate(False, 1.2, gate, rel)          # 멀다 -> 안 건다
    assert not _engage_gate(False, gate + 0.01, gate, rel)  # 경계 밖
    assert _engage_gate(False, gate, gate, rel)             # 경계에서 건다
    assert _engage_gate(False, 0.1, gate, rel)
    assert _engage_gate(True, gate + 0.1, gate, rel)        # 히스테리시스 안
    assert not _engage_gate(True, gate + rel + 0.01, gate, rel)   # 벗어나면 풀림
    # 게이트 폭을 왕복해도 상태가 튀지 않는다 (덜덜 떨림 방지)
    eng = False
    for e in (1.0, 0.6, 0.5, 0.52, 0.6, 0.66, 0.9):
        eng = _engage_gate(eng, e, gate, rel)
    assert not eng                                          # 0.66 에서 풀린 뒤 유지
    print("joint_limit_wall selftest 통과")


if __name__ == "__main__":
    selftest()
