"""Feel the GELLO leader's joint-limit wall on its own -- no robot, no ZMQ.

Same wall the teleop agent runs (gello.robots.joint_limit_wall.JointLimitWall);
this just drives it standalone so the wall and its currents can be tuned by
hand.  Move a joint past a follower limit and feel the push-back.

    ./scripts/runme.sh                     # servos must be at 1 Mbps
    python scripts/gello_joint_limit_wall.py

The wall is a cue, not a restraint: even at the servo's ceiling a determined
hand overpowers it.  The Dynamixel port is exclusive -- stop teleop first.
"""

import glob
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import tyro

from gello.agents.gello_agent import PORT_CONFIG_MAP
from gello.dynamixel.driver import DynamixelDriver
from gello.robots.joint_limit_wall import JointLimitWall


@dataclass
class Args:
    gello_port: Optional[str] = None
    margin: float = 0.02
    """Start the wall this far inside the follower's limit (rad)."""
    max_current: float = 500.0
    """Per-servo wall saturation (mA).  ~1000 mA+ held against the wall trips the
    servo's overload protection; 500 mA (~1/3 of stall) is a firm cue without it."""
    current_budget: float = 2800.0
    """Total current cap across joints (mA), protecting the 5 V / 4 A supply."""
    wall_depth: float = 0.1
    """How far past the limit the wall reaches full force (rad); kp = max/depth."""
    kd: float = 40.0
    """Wall damping (mA per rad/s); raise if the wall buzzes."""
    arm_margin: float = 0.25
    """Enable torque within this distance of a limit (rad); off elsewhere so the
    free workspace keeps its feel.  Must exceed --wall-depth."""
    arm_hysteresis: float = 0.05
    hz: float = 300.0
    health_every: float = 0.5
    min_voltage: float = 4.5
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
    if config.joint_limits is None:
        raise RuntimeError(f"Port {port} has no joint_limits; nothing to wall")

    lower, upper = config.joint_limits
    n_arm = len(config.joint_ids)
    ids = list(config.joint_ids) + [config.gripper_config[0]]
    driver = DynamixelDriver(ids, port=port)

    wall = JointLimitWall(
        driver,
        lower,
        upper,
        offsets=np.array(config.joint_offsets),
        signs=np.array(config.joint_signs),
        n_arm=n_arm,
        margin=args.margin,
        max_current=args.max_current,
        current_budget=args.current_budget,
        wall_depth=args.wall_depth,
        kd=args.kd,
        arm_margin=args.arm_margin,
        arm_hysteresis=args.arm_hysteresis,
        hz=args.hz,
        health_every=args.health_every,
        min_voltage=args.min_voltage,
    )

    wall.start()
    print(f"wall at follower limits +/-{args.margin} rad, armed within "
          f"{args.arm_margin} rad of one")
    print(f"kp={args.max_current / args.wall_depth:.0f} mA/rad  "
          f"kd={args.kd:.0f} mA/(rad/s)  max={args.max_current:.0f} mA")
    print("move a joint past its limit to feel the wall; Ctrl+C to stop\n")

    n_lines = 0
    try:
        while True:
            wall.poll()  # re-raises a wall-thread fault here
            st = wall.status()
            q = st.get("q")
            if q is not None and args.print_every > 0:
                cur, lo, hi = st["cur"], st["lo"], st["hi"]
                over_hi, over_lo = st["over_hi"], st["over_lo"]
                v = [h[0] for h in st["health"] if h[0] is not None]
                t = [h[2] for h in st["health"] if h[2] is not None]
                hs = (f"supply {min(v):.1f} V  temp<={max(t)} C" if v and t
                      else "supply --")
                lines = [
                    f"  torque {'ARMED  ' if st['armed'] else 'off    '}"
                    f"nearest {st['slack']:+.3f} rad  sum {np.abs(cur).sum():5.0f} mA  "
                    f"loop {st['hz']:5.1f} Hz  {hs}"
                ]
                for i in range(n_arm):
                    hit = over_hi[i] or over_lo[i]
                    bar = (f"{'HI' if over_hi[i] else 'LO'} {cur[i]:+7.1f} mA"
                           if hit else "--")
                    lines.append(
                        f"  J{i + 1}  q {q[i]:+7.3f}  "
                        f"[{lo[i]:+6.3f},{hi[i]:+6.3f}]  {bar}"
                    )
                out_s = "\n".join("\x1b[K" + ln for ln in lines)
                print(f"\x1b[{n_lines}F{out_s}" if n_lines else out_s)
                n_lines = len(lines)
            time.sleep(args.print_every if args.print_every > 0 else 0.1)
    except KeyboardInterrupt:
        pass
    finally:
        wall.stop()
        driver.close()
        print("\ntorque off, servos restored to position mode")


if __name__ == "__main__":
    main(tyro.cli(Args))
