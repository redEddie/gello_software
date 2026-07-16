"""Give the GELLO leader a physical wall at the FR3's joint limits.

The leader turns through poses the FR3 cannot reach (J4 is never positive, J6
never reaches 0).  Driving the follower at one of those makes it command speed
the FR3 does not allow near a limit, which trips the speed-limit reflex.  Rather
than silently clamp the follower's command -- that decouples the two arms and
leaves the operator pushing through a dead zone -- push back on the *leader* so
the limit is felt and never crossed in the first place.

Runs the leader alone: no robot, no ZMQ.  Move each joint toward its limit and
feel the wall.

    ./scripts/runme.sh                     # servos must be at 1 Mbps
    python scripts/gello_joint_limit_wall.py

The wall is a cue, not a restraint: even at the servo's ceiling (1750 mA, about
0.6 Nm) a determined hand overpowers it.  Start low and raise --max-current only
as far as the wall needs to be noticed.

Safety: the servos hold the last commanded current, so torque is disabled on
every exit path.  The Dynamixel port is exclusive -- stop teleop first.
"""

import glob
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import tyro

from gello.agents.gello_agent import PORT_CONFIG_MAP
from gello.dynamixel.driver import (
    CURRENT_CONTROL_MODE,
    POSITION_CONTROL_MODE,
    DynamixelDriver,
)
from gello.robots.franka_fr3 import FR3_Q_LOWER, FR3_Q_UPPER

SERVO_TYPE = "XL330_M288_T"  # Franka GELLO, all joints unified at gear ratio 288

# XL330 control-table addresses for supply-health monitoring.
ADDR_HW_ERROR = 70       # Hardware Error Status (1 B, bitfield)
ADDR_INPUT_VOLTAGE = 144  # Present Input Voltage (2 B, units of 0.1 V)
ADDR_TEMPERATURE = 146   # Present Temperature (1 B, deg C)


def read_health(driver, ids):
    """Per-servo (voltage V, hw_error, temp C), read under the driver's lock so
    the reads do not race its background state-polling thread on the same bus."""
    ph, pk = driver._portHandler, driver._packetHandler
    out = []
    with driver._lock:
        for i in ids:
            v, rv, _ = pk.read2ByteTxRx(ph, i, ADDR_INPUT_VOLTAGE)
            e, re, _ = pk.read1ByteTxRx(ph, i, ADDR_HW_ERROR)
            t, rt, _ = pk.read1ByteTxRx(ph, i, ADDR_TEMPERATURE)
            out.append((v / 10 if rv == 0 else None, e if re == 0 else None,
                        t if rt == 0 else None))
    return out


@dataclass
class Args:
    gello_port: Optional[str] = None
    margin: float = 0.02
    """Start the wall this far inside the FR3's limit (rad)."""
    max_current: float = 500.0
    """Per-servo wall saturation (mA).  Held against the wall, ~1000 mA+ trips the
    servo's overload protection (HW error 0x20) within seconds and it drops
    torque.  500 mA (~1/3 of the 1.5 A stall) stays clear of that while still
    giving a firm cue.  A determined hand still overpowers it by design."""
    current_budget: float = 2800.0
    """Total current cap across all joints (mA), protecting the 5 V / 4 A supply.
    If several joints hit their limits at once and the sum would exceed this, all
    wall currents scale down together -- so a single joint still gets the full
    per-servo force, but the supply is never overdrawn.  4000 mA rating minus
    idle draw and headroom (cheap supplies sag past ~80% and brown out servos)."""
    wall_depth: float = 0.1
    """How far past the limit the wall reaches full force (rad).  Sets the
    stiffness: kp = max_current / wall_depth.  Smaller = harder wall."""
    kd: float = 40.0
    """Wall damping (mA per rad/s).  Without it a stiff wall buzzes: the joint
    crosses, gets shoved back, crosses again.  Raise this if it chatters."""
    health_every: float = 0.5
    """Seconds between supply-health reads (voltage/error/temp).  0 disables.
    Read under the driver's lock so they do not collide with its state thread."""
    min_voltage: float = 4.5
    """Stop if any servo's input voltage sags below this (V).  A drooping supply
    browns servos out; catching it here beats a mid-teleop shutdown."""
    arm_margin: float = 0.25
    """Enable torque once a joint comes this close to a limit (rad).

    Holding torque on everywhere makes the whole arm feel heavier -- measured by
    hand, current-control-at-zero drags noticeably more than torque-off, because
    the servo drives the windings instead of leaving them open.  Since the wall
    only has work to do near a limit, torque stays off elsewhere and the free
    workspace keeps exactly the feel it has today.  Must exceed --wall-depth so
    the wall is live before the joint reaches it."""
    arm_hysteresis: float = 0.05
    """Disarm only once every joint is this much further out again (rad), so
    hovering at the boundary does not toggle torque on and off repeatedly."""
    hz: float = 300.0
    print_every: float = 0.1


def main(args: Args) -> None:
    port = args.gello_port
    if port is None:
        ports = glob.glob("/dev/serial/by-id/*")
        if not ports:
            raise RuntimeError("No GELLO found, please specify --gello-port")
        port = ports[0]
    config = PORT_CONFIG_MAP.get(port)
    if config is None:
        raise RuntimeError(f"Port {port} not in PORT_CONFIG_MAP")

    # The wall lives in FR3 joint space, so use the same offsets/signs the agent
    # does -- otherwise it lands somewhere other than where the follower's limit
    # actually is.  The gripper (last id) has no limit and is never driven.
    offsets = np.array(config.joint_offsets)
    signs = np.array(config.joint_signs)
    n_arm = len(config.joint_ids)
    ids = list(config.joint_ids) + [config.gripper_config[0]]
    lo, hi = FR3_Q_LOWER + args.margin, FR3_Q_UPPER - args.margin
    kp = args.max_current / args.wall_depth

    driver = DynamixelDriver(ids, port=port, servo_types=[SERVO_TYPE] * len(ids))

    def shutdown(*_):
        try:
            driver.set_current([0.0] * len(ids))
        except Exception:
            pass
        try:
            driver.set_torque_mode(False)
            driver.set_operating_mode(POSITION_CONTROL_MODE)
        except Exception:
            pass
        driver.close()
        print("\ntorque off, servos restored to position mode")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if args.arm_margin <= args.wall_depth:
        raise ValueError(
            f"--arm-margin ({args.arm_margin}) must exceed --wall-depth "
            f"({args.wall_depth}), or the wall is still disarmed at full force"
        )

    try:
        # Operating mode is EEPROM: it only takes while torque is disabled.
        # Torque stays off until a joint approaches a limit (see --arm-margin).
        driver.set_torque_mode(False)
        driver.set_operating_mode(CURRENT_CONTROL_MODE)
        driver.verify_operating_mode(CURRENT_CONTROL_MODE)
        print(f"wall at FR3 limits +/-{args.margin} rad, armed within "
              f"{args.arm_margin} rad of one")
        print(f"kp={kp:.0f} mA/rad  kd={args.kd:.0f} mA/(rad/s)  "
              f"max={args.max_current:.0f} mA")
        print("move a joint past its limit to feel the wall; Ctrl+C to stop\n")

        dt = 1.0 / args.hz
        last_print = 0.0
        n_lines = 0
        armed = False
        rate_t0, rate_n, rate_hz = time.time(), 0, 0.0
        last_health = 0.0
        health = read_health(driver, ids) if args.health_every > 0 else []
        while True:
            t0 = time.time()
            raw_q, raw_dq = driver.get_positions_and_velocities()
            q = (raw_q[:n_arm] - offsets[:n_arm]) * signs[:n_arm]
            dq = raw_dq[:n_arm] * signs[:n_arm]

            # Distance to the nearest limit; negative once past it.
            slack = np.minimum(hi - q, q - lo).min()
            want = slack < (args.arm_margin + args.arm_hysteresis if armed
                            else args.arm_margin)
            if want != armed:
                if not want:
                    driver.set_current([0.0] * len(ids))  # release before cutting
                driver.set_torque_mode(want)
                armed = want

            # One-sided spring-damper: exactly zero inside the limits, so the
            # arm stays as free as it is today until the operator reaches one.
            over_hi = q > hi
            over_lo = q < lo
            cur = (-kp * (q - hi) - args.kd * dq) * over_hi
            cur += (-kp * (q - lo) - args.kd * dq) * over_lo
            cur = np.clip(cur, -args.max_current, args.max_current)

            # Supply budget: one joint keeps full force; if several fire at once
            # and the total would exceed the supply, scale them down together.
            total = np.abs(cur).sum()
            if total > args.current_budget:
                cur *= args.current_budget / total

            if armed:
                # Back to raw servo space; the gripper is never driven.
                driver.set_current(
                    (cur * signs[:n_arm]).tolist() + [0.0] * (len(ids) - n_arm)
                )

            rate_n += 1
            if t0 - rate_t0 >= 1.0:
                rate_hz, rate_t0, rate_n = rate_n / (t0 - rate_t0), t0, 0

            # Supply health.  A servo that browns out or faults drops torque on
            # its own, so catch a sagging supply or a set error bit and stop
            # cleanly (shutdown() releases current and restores position mode).
            if args.health_every > 0 and t0 - last_health >= args.health_every:
                health = read_health(driver, ids)
                last_health = t0
                volts = [v for v, _, _ in health if v is not None]
                errs = [(ids[k], e) for k, (_, e, _) in enumerate(health) if e]
                if errs:
                    print(f"\n!! hardware error: "
                          + ", ".join(f"ID{i}=0x{e:02x}" for i, e in errs))
                    shutdown()
                if volts and min(volts) < args.min_voltage:
                    print(f"\n!! supply sag: {min(volts):.1f} V "
                          f"< {args.min_voltage} V -- reduce --max-current/-budget")
                    shutdown()

            if args.print_every > 0 and t0 - last_print >= args.print_every:
                v_ok = [v for v, _, _ in health if v is not None]
                t_ok = [t for _, _, t in health if t is not None]
                hs = (f"supply {min(v_ok):.1f} V  temp<={max(t_ok)} C"
                      if v_ok and t_ok else "supply --")
                lines = [
                    f"  torque {'ARMED  ' if armed else 'off    '}"
                    f"nearest {slack:+.3f} rad  sum {np.abs(cur).sum():5.0f} mA  "
                    f"loop {rate_hz:5.1f} Hz  {hs}"
                ]
                for i in range(n_arm):
                    hit = over_hi[i] or over_lo[i]
                    bar = f"{'HI' if over_hi[i] else 'LO'} {cur[i]:+7.1f} mA" if hit else "--"
                    lines.append(
                        f"  J{i + 1}  q {q[i]:+7.3f}  [{lo[i]:+6.3f},{hi[i]:+6.3f}]  {bar}"
                    )
                out_s = "\n".join("\x1b[K" + ln for ln in lines)
                print(f"\x1b[{n_lines}F{out_s}" if n_lines else out_s)
                n_lines = len(lines)
                last_print = t0

            rest = dt - (time.time() - t0)
            if rest > 0:
                time.sleep(rest)
    except BaseException:
        shutdown()
        raise


if __name__ == "__main__":
    main(tyro.cli(Args))
