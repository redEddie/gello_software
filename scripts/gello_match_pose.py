"""Live leader/follower joint monitor -- match the GELLO to the robot by hand.

Reads the GELLO (leader) and the robot (follower) and redraws a per-joint
delta table in place until every joint is under --threshold, so you can
physically move the leader arm into the pose that experiments/run_env.py's
start gate checks. No motion is commanded; the robot is only read over ZMQ.

Usage:
    # terminal 1
    python experiments/launch_nodes.py --robot fr3
    # terminal 2
    python scripts/gello_match_pose.py
    # move the red joints until everything is green, then Ctrl+C

The Dynamixel serial port is exclusive: stop this script (Ctrl+C) before
launching run_env.py --agent gello.
"""

import glob
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import tyro
from termcolor import colored

from gello.agents.gello_agent import GelloAgent
from gello.zmq_core.robot_node import ZMQClientRobot


@dataclass
class Args:
    gello_port: Optional[str] = None
    robot_port: int = 6001
    hostname: str = "127.0.0.1"
    hz: float = 10.0
    threshold: float = 0.5
    """Per-joint pass mark in radians; keep equal to max_joint_delta in run_env.py."""
    grip: str = "right"
    """Which hand holds the GELLO handle ("right" or "left").  Pass the same
    --grip you will give run_env.py, or the handle-roll joint shown here will
    be 180deg off from what its start gate sees."""


def main(args: Args) -> None:
    gello_port = args.gello_port
    if gello_port is None:
        usb_ports = glob.glob("/dev/serial/by-id/*")
        if not usb_ports:
            raise RuntimeError("No GELLO found, please specify --gello-port")
        gello_port = usb_ports[0]
        print(f"using GELLO port {gello_port}")

    # Same construction as run_env.py (PORT_CONFIG_MAP, no start_joints, same
    # --grip), so the deltas shown here are exactly what its start gate will
    # see.
    print(f"grip: {args.grip}")
    agent = GelloAgent(port=gello_port, grip=args.grip)
    driver = getattr(agent._robot, "_driver", None)
    if getattr(driver, "_is_fake", False):
        raise RuntimeError(
            "Dynamixel driver fell back to the fake driver. "
            "Check the port permissions and cabling."
        )

    robot = ZMQClientRobot(port=args.robot_port, host=args.hostname)
    print("waiting for robot state (is launch_nodes.py running?)...")

    dt = 1.0 / args.hz
    n_lines = 0
    try:
        while True:
            t0 = time.time()
            leader = np.asarray(agent.act({}))
            follower = np.asarray(robot.get_joint_state())
            n = min(len(leader), len(follower))

            lines = []
            all_ok = True
            for i in range(n):
                name = f"J{i + 1}" if i < 7 else "grip"
                delta = leader[i] - follower[i]
                ok = abs(delta) <= args.threshold
                all_ok &= ok
                mark = colored("OK", "green") if ok else colored("!!", "red", attrs=["bold"])
                lines.append(
                    f"  {name:4s} leader {leader[i]:+7.3f}  follower {follower[i]:+7.3f}"
                    f"  delta {delta:+6.3f}  [{mark}]"
                )
            if all_ok:
                lines.append(
                    colored(
                        f"  MATCHED: all deltas <= {args.threshold} rad -- "
                        "Ctrl+C and start run_env.py",
                        "green",
                        attrs=["bold"],
                    )
                )
            else:
                lines.append(
                    colored(f"  move the red joints (gate: {args.threshold} rad)", "yellow")
                )

            out = "\n".join("\x1b[K" + line for line in lines)
            if n_lines:
                print(f"\x1b[{n_lines}F{out}")
            else:
                print(out)
            n_lines = len(lines)

            rest = dt - (time.time() - t0)
            if rest > 0:
                time.sleep(rest)
    except KeyboardInterrupt:
        print("\nstopped, serial port released")


if __name__ == "__main__":
    main(tyro.cli(Args))
