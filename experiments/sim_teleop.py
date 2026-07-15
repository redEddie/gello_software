"""Teleoperate a MuJoCo-simulated robot with a physical GELLO leader arm.

Unlike run_env.py, this script first syncs the simulated robot to the GELLO's
current pose (like the ROS 2 implementation does), so the GELLO does not need
to be held in any particular start position.

Usage:
    # terminal 1
    python experiments/launch_nodes.py --robot sim_panda
    # terminal 2
    python experiments/sim_teleop.py
"""

import glob
import time
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import tyro

from gello.agents.gello_agent import PORT_CONFIG_MAP, GelloAgent
from gello.zmq_core.robot_node import ZMQClientRobot


@dataclass
class Args:
    gello_port: Optional[str] = None
    robot_port: int = 6001
    hostname: str = "127.0.0.1"
    hz: float = 100.0
    sync_seconds: float = 3.0
    """Time to smoothly move the sim robot to the GELLO pose on startup."""
    invert_gripper: bool = True
    """The menagerie Panda hand opens at ctrl=255 while GELLO maps open to 0."""
    gripper_scale: float = 1.0
    """sim_robot.py scales the gripper command x255 for robots with a separately
    attached gripper XML (sim_fr3, sim_ur), so 1.0 is correct there. The
    sim_panda built-in hand is not scaled by the server: pass 255 for it."""
    print_every: float = 2.0
    """Seconds between status prints. 0 disables."""
    grip: str = "right"
    """Which hand holds the GELLO handle. The PORT_CONFIG_MAP entry is
    calibrated for the right-handed grip; "left" flips the joint 7 zero by
    180deg to the mirrored grip."""


def main(args: Args) -> None:
    gello_port = args.gello_port
    if gello_port is None:
        usb_ports = glob.glob("/dev/serial/by-id/*")
        if not usb_ports:
            raise RuntimeError("No GELLO found, please specify --gello-port")
        gello_port = usb_ports[0]
        print(f"using GELLO port {gello_port}")

    config = PORT_CONFIG_MAP.get(gello_port)
    if config is None:
        raise RuntimeError(f"Port {gello_port} not in PORT_CONFIG_MAP")
    if args.grip == "left":
        offsets = list(config.joint_offsets)
        offsets[6] += np.pi
        config = replace(config, joint_offsets=tuple(offsets))
    elif args.grip != "right":
        raise ValueError(f"grip must be 'right' or 'left', got {args.grip!r}")

    agent = GelloAgent(port=gello_port, dynamixel_config=config)
    driver = getattr(agent._robot, "_driver", None)
    if getattr(driver, "_is_fake", False):
        raise RuntimeError(
            "Dynamixel driver fell back to the fake driver. "
            "Check the port permissions and cabling."
        )

    robot = ZMQClientRobot(port=args.robot_port, host=args.hostname)
    num_dofs = robot.num_dofs()

    def gello_command() -> np.ndarray:
        # copy: act() returns the robot's internal smoothing buffer, which must
        # not be mutated by the gripper rescaling below
        cmd = agent.act({}).copy()
        gripper = cmd[-1]
        if args.invert_gripper:
            gripper = 1.0 - gripper
        cmd[-1] = gripper * args.gripper_scale
        # keep joint 7 in (-pi, pi]: flipping the handle between grips can
        # wind the wrist a full turn depending on the twist direction, and
        # the FR3 joint 7 range fits inside +-pi anyway
        cmd[6] = (cmd[6] + np.pi) % (2 * np.pi) - np.pi
        return cmd

    cmd = gello_command()
    assert len(cmd) == num_dofs, (
        f"GELLO reports {len(cmd)} dofs but sim robot expects {num_dofs}"
    )

    # Smoothly move the sim robot from its current pose to the GELLO pose.
    sim_joints = robot.get_joint_state()
    dt = 1.0 / args.hz
    n_steps = max(int(args.sync_seconds * args.hz), 1)
    print("Syncing sim robot to GELLO pose...")
    for i in range(n_steps):
        alpha = (i + 1) / n_steps
        cmd = gello_command()
        blended = (1 - alpha) * sim_joints + alpha * cmd
        blended[-1] = cmd[-1]  # gripper units differ, do not interpolate
        robot.command_joint_state(blended)
        time.sleep(dt)

    print(f"Synced. Mirroring GELLO at {args.hz:.0f} Hz. Ctrl+C to stop.")
    last_print = 0.0
    while True:
        step_start = time.time()
        cmd = gello_command()
        robot.command_joint_state(cmd)

        if args.print_every > 0 and step_start - last_print >= args.print_every:
            state = robot.get_joint_state()
            with np.printoptions(precision=3, suppress=True):
                print(f"gello: {cmd}")
                print(f"sim:   {state}")
            last_print = step_start

        rest = dt - (time.time() - step_start)
        if rest > 0:
            time.sleep(rest)


if __name__ == "__main__":
    main(tyro.cli(Args))
