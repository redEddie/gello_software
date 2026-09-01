from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from gello.core.robot import Robot


class DynamixelRobot(Robot):
    """A class representing a UR robot."""

    def __init__(
        self,
        joint_ids: Sequence[int],
        joint_offsets: Optional[Sequence[float]] = None,
        joint_signs: Optional[Sequence[int]] = None,
        real: bool = False,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 1000000,
        gripper_config: Optional[Tuple[int, float, float]] = None,
        start_joints: Optional[np.ndarray] = None,
        joint_limits: Optional[Tuple[np.ndarray, np.ndarray]] = None,
        servo_types: Optional[Sequence[str]] = None,
    ):
        from gello.hw.dynamixel.driver import (
            DynamixelDriver,
            DynamixelDriverProtocol,
            FakeDynamixelDriver,
        )

        print(f"attempting to connect to port: {port}")
        self.gripper_open_close: Optional[Tuple[float, float]]
        if gripper_config is not None:
            assert joint_offsets is not None
            assert joint_signs is not None

            # joint_ids.append(gripper_config[0])
            # joint_offsets.append(0.0)
            # joint_signs.append(1)
            joint_ids = tuple(joint_ids) + (gripper_config[0],)
            joint_offsets = tuple(joint_offsets) + (0.0,)
            joint_signs = tuple(joint_signs) + (1,)
            self.gripper_open_close = (
                gripper_config[1] * np.pi / 180,
                gripper_config[2] * np.pi / 180,
            )
        else:
            self.gripper_open_close = None

        self._joint_ids = joint_ids
        self._driver: DynamixelDriverProtocol

        if joint_offsets is None:
            self._joint_offsets = np.zeros(len(joint_ids))
        else:
            self._joint_offsets = np.array(joint_offsets)

        if joint_signs is None:
            self._joint_signs = np.ones(len(joint_ids))
        else:
            self._joint_signs = np.array(joint_signs)

        assert len(self._joint_ids) == len(self._joint_offsets), (
            f"joint_ids: {len(self._joint_ids)}, "
            f"joint_offsets: {len(self._joint_offsets)}"
        )
        assert len(self._joint_ids) == len(self._joint_signs), (
            f"joint_ids: {len(self._joint_ids)}, "
            f"joint_signs: {len(self._joint_signs)}"
        )
        assert np.all(
            np.abs(self._joint_signs) == 1
        ), f"joint_signs: {self._joint_signs}"
        if servo_types is not None:
            # Caller passes one entry per servo actually on the bus -- arm
            # joints AND the gripper if gripper_config is set (joint_ids
            # above has already grown to include it by this point).
            assert len(servo_types) == len(joint_ids), (
                f"servo_types: {len(servo_types)}, joint_ids: {len(joint_ids)}"
            )

        if real:
            self._driver = DynamixelDriver(
                joint_ids, servo_types=servo_types, port=port, baudrate=baudrate
            )
            self._driver.set_torque_mode(False)
        else:
            self._driver = FakeDynamixelDriver(joint_ids)
        self._torque_on = False
        self._last_pos = None
        self._turn_k = None   # 관절별 바퀴 번호 (resolve_turns 히스테리시스)
        self._alpha = 0.99
        # (lower, upper) follower limits, arm joints only.  When set,
        # get_joint_state removes any phantom full turn from the reading (a
        # GELLO joint can read 2*pi out of range); None leaves readings as-is.
        self._joint_limits = joint_limits

        if start_joints is not None:
            # loop through all joints and add +- 2pi to the joint offsets to get the closest to start joints
            new_joint_offsets = []
            current_joints = self.get_joint_state()
            assert current_joints.shape == start_joints.shape
            if gripper_config is not None:
                current_joints = current_joints[:-1]
                start_joints = start_joints[:-1]
            for idx, (c_joint, s_joint, joint_offset) in enumerate(
                zip(current_joints, start_joints, self._joint_offsets)
            ):
                new_joint_offsets.append(
                    np.pi
                    * 2
                    * np.round((-s_joint + c_joint) / (2 * np.pi))
                    * self._joint_signs[idx]
                    + joint_offset
                )
            if gripper_config is not None:
                new_joint_offsets.append(self._joint_offsets[-1])
            self._joint_offsets = np.array(new_joint_offsets)

    def num_dofs(self) -> int:
        return len(self._joint_ids)

    def get_joint_state(self) -> np.ndarray:
        pos = (self._driver.get_joints() - self._joint_offsets) * self._joint_signs
        assert len(pos) == self.num_dofs()

        # Remove any phantom full turn before smoothing, so a joint sitting on
        # its encoder wrap (e.g. FR3 J3 at q~=0) stays continuous instead of
        # feeding a 2*pi jump into the EWMA.  Arm joints only.
        if self._joint_limits is not None:
            from gello.robots.joint_limit_wall import resolve_turns

            n_arm = len(pos) - (1 if self.gripper_open_close is not None else 0)
            lower, upper = self._joint_limits
            # 고른 바퀴를 기억해서 다음 읽기에 넘긴다 (2026-09-01). 매번 새로
            # 고르면 ±180° 경계에서 센서 노이즈만으로 답이 한 바퀴 튀고,
            # 원점이 반 바퀴 어긋난 것처럼 보인다. resolve_turns 참고.
            pos[:n_arm], self._turn_k = resolve_turns(
                pos[:n_arm], lower, upper, prev_k=self._turn_k)

        if self.gripper_open_close is not None:
            # map pos to [0, 1]
            g_pos = (pos[-1] - self.gripper_open_close[0]) / (
                self.gripper_open_close[1] - self.gripper_open_close[0]
            )
            g_pos = min(max(0, g_pos), 1)
            pos[-1] = g_pos

        if self._last_pos is None:
            self._last_pos = pos
        else:
            # exponential smoothing
            pos = self._last_pos * (1 - self._alpha) + pos * self._alpha
            self._last_pos = pos

        return pos

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        self._driver.set_joints((joint_state + self._joint_offsets).tolist())

    def set_torque_mode(self, mode: bool):
        if mode == self._torque_on:
            return
        self._driver.set_torque_mode(mode)
        self._torque_on = mode

    def get_observations(self) -> Dict[str, np.ndarray]:
        return {"joint_state": self.get_joint_state()}

    def close(self) -> None:
        """Releases the serial port and stops the driver's reading thread.

        Without this, a process that connects/disconnects more than once
        (e.g. a GUI session that reconnects) leaves the old DynamixelDriver's
        port open, so the next connect finds it "busy" and its recovery
        logic tries to kill whatever holds it -- which is this same process.
        """
        self._driver.close()
