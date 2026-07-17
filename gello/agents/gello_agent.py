import os
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from gello.agents.agent import Agent
from gello.robots.dynamixel import DynamixelRobot
from gello.robots.franka_fr3 import FR3_Q_LOWER, FR3_Q_UPPER, GRIPPER_CLOSE_AT


@dataclass
class DynamixelRobotConfig:
    joint_ids: Sequence[int]
    """The joint ids of GELLO (not including the gripper). Usually (1, 2, 3 ...)."""

    joint_offsets: Sequence[float]
    """The joint offsets of GELLO. There needs to be a joint offset for each joint_id and should be a multiple of pi/2."""

    joint_signs: Sequence[int]
    """The joint signs of GELLO. There needs to be a joint sign for each joint_id and should be either 1 or -1.

    This will be different for each arm design. Refernce the examples below for the correct signs for your robot.
    """

    gripper_config: Tuple[int, int, int]
    """The gripper config of GELLO. This is a tuple of (gripper_joint_id, degrees in open_position, degrees in closed_position)."""

    joint_limits: Optional[Tuple[np.ndarray, np.ndarray]] = None
    """The follower's (lower, upper) joint limits (rad), arm joints only.  When
    set, GelloAgent puts a physical wall on the leader here (see JointLimitWall);
    None means no wall (the default for arms without published limits)."""

    def __post_init__(self):
        assert len(self.joint_ids) == len(self.joint_offsets)
        assert len(self.joint_ids) == len(self.joint_signs)

    def make_robot(
        self, port: str = "/dev/ttyUSB0", start_joints: Optional[np.ndarray] = None
    ) -> DynamixelRobot:
        return DynamixelRobot(
            joint_ids=self.joint_ids,
            joint_offsets=list(self.joint_offsets),
            real=True,
            joint_signs=list(self.joint_signs),
            port=port,
            gripper_config=self.gripper_config,
            start_joints=start_joints,
            joint_limits=self.joint_limits,
        )


PORT_CONFIG_MAP: Dict[str, DynamixelRobotConfig] = {
    # Franka GELLO (FR3 build), calibrated 2026-07-15 in pose (0, 0, 0, -pi/2, 0, pi/2, 0)
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBIN516-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6, 7),
        joint_offsets=(
            -2 * np.pi / 2,
            2 * np.pi / 2,
            4 * np.pi / 2,
            1 * np.pi / 2,
            2 * np.pi / 2,
            1 * np.pi / 2,
            # measured grid value is 2*pi/2. The +pi/4 deliberately zeroes
            # joint 7 at the square-handle grip (user decision) instead of the
            # real hand's -45deg mount orientation; the -pi selects the
            # right-handed grip (user-verified: left-handed grip is 180deg
            # from it, via --grip left in experiments/sim_teleop.py).
            # NOTE: recalibration scripts output the pure pi/2 grid — re-apply
            # this expression manually after any recalibration.
            2 * np.pi / 2 + np.pi / 4 - np.pi,
        ),
        joint_signs=(1, 1, 1, -1, 1, 1, 1),
        # trigger rest position drifts (173.8-185.4 deg observed); using the
        # least-open reading so a released trigger always maps to fully open
        gripper_config=(8, 174.0, 143.8),
        joint_limits=(FR3_Q_LOWER, FR3_Q_UPPER),
    ),
    # xArm
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT3M9NVB-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6, 7),
        joint_offsets=(
            3 * np.pi / 2,
            2 * np.pi / 2,
            1 * np.pi / 2,
            4 * np.pi / 2,
            -2 * np.pi / 2 + 2 * np.pi,
            3 * np.pi / 2,
            4 * np.pi / 2,
        ),
        joint_signs=(1, -1, 1, 1, 1, -1, 1),
        gripper_config=(8, 195, 152),
    ),
    # yam
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTA2U4GA-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6),
        joint_offsets=[
            0 * np.pi,
            2 * np.pi / 2,
            4 * np.pi / 2,
            6 * np.pi / 6,
            5 * np.pi / 3,
            2 * np.pi / 2,
        ],
        joint_signs=(1, -1, -1, -1, 1, 1),
        gripper_config=(
            7,
            -30,
            24,
        ),  # Reversed: now starts open (-30) and closes on press (24)
    ),
    # Left UR
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT7WBEIA-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6),
        joint_offsets=(
            0,
            1 * np.pi / 2 + np.pi,
            np.pi / 2 + 0 * np.pi,
            0 * np.pi + np.pi / 2,
            np.pi - 2 * np.pi / 2,
            -1 * np.pi / 2 + 2 * np.pi,
        ),
        joint_signs=(1, 1, -1, 1, 1, 1),
        gripper_config=(7, 20, -22),
    ),
    # Right UR
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT7WBG6A-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6),
        joint_offsets=(
            np.pi + 0 * np.pi,
            2 * np.pi + np.pi / 2,
            2 * np.pi + np.pi / 2,
            2 * np.pi + np.pi / 2,
            1 * np.pi,
            3 * np.pi / 2,
        ),
        joint_signs=(1, 1, -1, 1, 1, 1),
        gripper_config=(7, 286, 248),
    ),
}


class GelloAgent(Agent):
    def __init__(
        self,
        port: str,
        dynamixel_config: Optional[DynamixelRobotConfig] = None,
        start_joints: Optional[np.ndarray] = None,
        enable_wall: bool = True,
    ):
        # Ensure start_joints is a numpy array if provided
        if start_joints is not None and not isinstance(start_joints, np.ndarray):
            start_joints = np.array(start_joints)
        if dynamixel_config is not None:
            config = dynamixel_config
        else:
            assert os.path.exists(port), port
            assert port in PORT_CONFIG_MAP, f"Port {port} not in config map"
            config = PORT_CONFIG_MAP[port]
        # make_robot receives config.joint_limits: when set, get_joint_state
        # fixes any phantom full turn in the leader reading (see
        # wrap_into_limits), before smoothing, so a joint whose calibrated value
        # lands 2*pi out of range does not fail the start gate every power-up.
        self._robot = config.make_robot(port=port, start_joints=start_joints)

        # Optional joint-limit wall on the leader.  Only when the config carries
        # the follower's limits (e.g. the FR3 GELLO); other arms keep their
        # current behaviour untouched.  Shares the robot's driver -- the port is
        # exclusive, so a separate wall process is impossible.
        self._wall = None
        if enable_wall and config.joint_limits is not None:
            from gello.robots.joint_limit_wall import JointLimitWall

            lower, upper = config.joint_limits
            n_arm = len(config.joint_ids)
            # Use the robot's *resolved* offsets/signs: start_joints can shift
            # the offsets in DynamixelRobot.__init__, and the wall must land
            # where the follower's limit actually is.
            self._wall = JointLimitWall(
                self._robot._driver,
                lower,
                upper,
                offsets=self._robot._joint_offsets,
                signs=self._robot._joint_signs,
                n_arm=n_arm,
                # Trigger spring: auto-open return plus an exponential squeeze
                # wall starting exactly where the follower's binary gripper
                # closes, so resistance onset == grasp.
                gripper_open_close=self._robot.gripper_open_close,
                trigger_start=GRIPPER_CLOSE_AT,
            )
            self._wall.start()
        elif enable_wall:
            print("[wall] no joint_limits for this port; leader wall disabled")

    def act(self, obs: Dict[str, np.ndarray]) -> np.ndarray:
        if self._wall is not None:
            self._wall.poll()  # re-raises if the wall thread died -> teleop dies
        return self._robot.get_joint_state()

    def close(self) -> None:
        """Clean shutdown of the leader wall (no-op if there is none)."""
        if self._wall is not None:
            self._wall.stop()
            self._wall = None
