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

from gello.dynamixel.driver import (
    CURRENT_CONTROL_MODE,
    POSITION_CONTROL_MODE,
    DynamixelDriverProtocol,
)

# XL330 control-table addresses for supply-health monitoring.
ADDR_HW_ERROR = 70        # Hardware Error Status (1 B, bitfield)
ADDR_INPUT_VOLTAGE = 144  # Present Input Voltage (2 B, units of 0.1 V)
ADDR_TEMPERATURE = 146    # Present Temperature (1 B, deg C)


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

        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._error: Optional[BaseException] = None
        self._armed = False
        self._status: Dict = {"armed": False, "hz": 0.0, "slack": None}

    # -------------------------------------------------------------- lifecycle
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

                # Arm torque only near a limit: current-control-at-zero drags
                # more than torque-off, so the rest of the workspace is left
                # torque-off and feels exactly as it does without the wall.
                # Arm servos only -- the trigger spring stays on throughout.
                slack = float(np.minimum(self._hi - q, q - self._lo).min())
                want = slack < (
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
                # scale down together so the 5 V / 4 A supply is not overdrawn.
                total = float(np.abs(cur).sum()) + abs(i_trig)
                if total > self._budget:
                    scale = self._budget / total
                    cur *= scale
                    i_trig *= scale

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
