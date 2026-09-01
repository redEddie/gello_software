import time
from typing import Any, Dict, Optional

import numpy as np

from gello.core.camera import CameraDriver
from gello.core.robot import Robot
from gello.data.dataset_schema import (
    ROBOT_EE_POS_QUAT,
    ROBOT_GRIPPER_POSITION,
    ROBOT_JOINT_POSITIONS,
    ROBOT_JOINT_VELOCITIES,
)


class Rate:
    def __init__(self, rate: float):
        self.last = time.time()
        self.rate = rate

    def sleep(self) -> None:
        while self.last + 1.0 / self.rate > time.time():
            time.sleep(0.0001)
        self.last = time.time()


class RobotEnv:
    def __init__(
        self,
        robot: Robot,
        control_rate_hz: float = 100.0,
        camera_dict: Optional[Dict[str, CameraDriver]] = None,
    ) -> None:
        self._robot = robot
        self._rate = Rate(control_rate_hz)
        self._camera_dict = {} if camera_dict is None else camera_dict

    def robot(self) -> Robot:
        """Get the robot object.

        Returns:
            robot: the robot object.
        """
        return self._robot

    def __len__(self):
        return 0

    def step(self, joints: np.ndarray) -> Dict[str, Any]:
        """Step the environment forward.

        Args:
            joints: joint angles command to step the environment with.

        Returns:
            obs: observation from the environment.
        """
        assert len(joints) == (
            self._robot.num_dofs()
        ), f"input:{len(joints)}, robot:{self._robot.num_dofs()}"
        assert self._robot.num_dofs() == len(joints)
        self._robot.command_joint_state(joints)
        self._rate.sleep()
        return self.get_obs()

    def get_obs(self) -> Dict[str, Any]:
        """Get observation from the environment.

        Returns:
            obs: observation from the environment.
        """
        observations = {}
        for name, camera in self._camera_dict.items():
            image, depth = camera.read()
            observations[f"{name}_rgb"] = image
            observations[f"{name}_depth"] = depth

        robot_obs = self._robot.get_observations()
        assert ROBOT_JOINT_POSITIONS in robot_obs
        assert ROBOT_JOINT_VELOCITIES in robot_obs
        assert ROBOT_EE_POS_QUAT in robot_obs
        observations[ROBOT_JOINT_POSITIONS] = robot_obs[ROBOT_JOINT_POSITIONS]
        observations[ROBOT_JOINT_VELOCITIES] = robot_obs[ROBOT_JOINT_VELOCITIES]
        observations[ROBOT_EE_POS_QUAT] = robot_obs[ROBOT_EE_POS_QUAT]
        observations[ROBOT_GRIPPER_POSITION] = robot_obs[ROBOT_GRIPPER_POSITION]
        return observations


def main() -> None:
    pass


if __name__ == "__main__":
    main()
