"""LeRobot plugins bridging the GELLO/FR3 teleop stack into lerobot-record.

Two classes, mirroring lerobot's Robot/Teleoperator split:

* :class:`FR3ZMQRobot` -- lerobot ``Robot`` over the existing ZMQ robot node
  (``scripts/launch/launch_nodes.py --robot fr3``).  The node keeps the verified
  1 kHz reference-filter thread; this class only updates its setpoint at the
  record-loop rate (20 Hz) and reads state + cameras.  Run the node in the
  pylibfranka venv; this plugin lives in the collection venv (py3.13) and needs
  no pylibfranka.
* :class:`GelloFR3Teleop` -- lerobot ``Teleoperator`` wrapping
  :class:`~gello.agents.gello_agent.GelloAgent` (dynamixel leader + joint-limit
  wall + trigger spring).

Action/observation are the 8 keys ``joint1.pos`` .. ``joint7.pos`` (rad) +
``gripper.pos``.  The gripper action is BINARY (0=open, 1=closed): the leader
trigger is discretized here with the same hysteresis the robot node applies,
so the recorded action is exactly what a deployed policy must output, and the
0.2..0.8 trigger dead zone can never leak into the dataset (a regressed-to-mean
continuous value would sit in the dead zone forever at rollout).  The gripper
observation stays continuous (0=open .. 1=closed, measured width).
"""

import glob
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import numpy as np

from lerobot.cameras import CameraConfig, make_cameras_from_configs
from lerobot.robots import Robot, RobotConfig
from lerobot.teleoperators import Teleoperator, TeleoperatorConfig

from gello.robots.franka_fr3 import GRIPPER_CLOSE_AT
from gello.core.station import load_station

JOINT_KEYS = [f"joint{i}.pos" for i in range(1, 8)] + ["gripper.pos"]


def _find_gello_port() -> str:
    """리더암 시리얼 포트. 스테이션이 지정했으면 그것, 아니면 FTDI 자동 탐색."""
    configured = load_station().leader.port
    if configured:
        return configured
    ports = glob.glob("/dev/serial/by-id/*FTDI*")
    if not ports:
        raise RuntimeError("no GELLO port found (/dev/serial/by-id/*FTDI*)")
    return ports[0]


@RobotConfig.register_subclass("fr3_zmq")
@dataclass
class FR3ZMQRobotConfig(RobotConfig):
    # 노드 주소는 스테이션 설정에서. 지금 호출자(수집 GUI, 정책 클라이언트)는
    # 둘 다 명시적으로 넘겨 주지만, 기본값이 하드코딩이면 그러지 않는 호출자가
    # 조용히 다른 곳에 붙는다 -- 클라이언트 타입 자체가 스테이션을 알아야 한다.
    host: str = field(default_factory=lambda: load_station().node.host)
    port: int = field(default_factory=lambda: load_station().node.port)
    cameras: dict[str, CameraConfig] = field(default_factory=dict)


class FR3ZMQRobot(Robot):
    config_class = FR3ZMQRobotConfig
    name = "fr3_zmq"

    def __init__(self, config: FR3ZMQRobotConfig):
        super().__init__(config)
        self.config = config
        self._client = None
        self.cameras = make_cameras_from_configs(config.cameras)

    @cached_property
    def _cameras_ft(self) -> dict[str, tuple]:
        return {
            cam: (cfg.height, cfg.width, 3) for cam, cfg in self.config.cameras.items()
        }

    @property
    def observation_features(self) -> dict:
        return {**{k: float for k in JOINT_KEYS}, **self._cameras_ft}

    @property
    def action_features(self) -> dict:
        return {k: float for k in JOINT_KEYS}

    @property
    def is_connected(self) -> bool:
        return self._client is not None and all(
            cam.is_connected for cam in self.cameras.values()
        )

    def _connect_node(self) -> None:
        import zmq

        from gello.comm.zmq_core.robot_node import ZMQClientRobot

        if self._client is not None:
            try:
                self._client._socket.close(linger=0)
                self._client._context.term()
            except Exception:
                pass
            self._client = None
        client = ZMQClientRobot(port=self.config.port, host=self.config.host)
        # Fail fast instead of blocking forever when the robot node dies
        # mid-session (e.g. killed after a reflex): a REQ socket with no
        # timeout would hang every later call, including cleanup.  A timed-out
        # REQ socket is also unusable afterwards, so recovery goes through
        # reconnect_node(), which rebuilds it.
        client._socket.setsockopt(zmq.RCVTIMEO, 2000)
        client._socket.setsockopt(zmq.SNDTIMEO, 2000)
        client._socket.setsockopt(zmq.LINGER, 0)
        try:
            n = client.num_dofs()
            if n != 8:
                raise RuntimeError(f"robot node reports {n} dofs, expected 8 (arm+gripper)")
            # num_dofs() only proves the ZMQ server process is up -- it says
            # nothing about whether FrankaFR3Robot's 1kHz control thread is
            # actually running inside it. That thread can die on its own
            # (reflex abort) while the server keeps answering requests fine,
            # so a real liveness check has to touch something the control
            # thread itself produces. get_observations() now raises if the
            # control thread reported an error (see franka_fr3.py), so
            # calling it here means "reconnected" only gets declared once
            # the arm is genuinely back, not just once the process answers.
            client.get_observations()
        except Exception:
            client._socket.close(linger=0)
            client._context.term()
            raise
        self._client = client

    def connect(self, calibrate: bool = True) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._connect_node()
        if self.cameras:
            with ThreadPoolExecutor(len(self.cameras)) as pool:
                list(pool.map(lambda cam: cam.connect(), self.cameras.values()))

    def reconnect_node(self) -> None:
        """Rebuild the ZMQ client after a robot-node restart; cameras stay up.

        Raises if the node is still down -- call again to retry.
        """
        self._connect_node()

    @property
    def is_calibrated(self) -> bool:
        return True  # joint offsets live in the robot node / GELLO config

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def get_observation(self) -> dict[str, Any]:
        obs = self._client.get_observations()
        pos = np.asarray(obs["joint_positions"], dtype=float)  # 7 rad + gripper 0..1
        out: dict[str, Any] = dict(zip(JOINT_KEYS, pos.tolist()))
        for cam_key, cam in self.cameras.items():
            out[cam_key] = cam.read_latest()
        return out

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        vec = np.array([float(action[k]) for k in JOINT_KEYS])
        self._client.command_joint_state(vec)
        return action

    def disconnect(self) -> None:
        for cam in self.cameras.values():
            cam.disconnect()
        self._client = None


@TeleoperatorConfig.register_subclass("gello_fr3")
@dataclass
class GelloFR3TeleopConfig(TeleoperatorConfig):
    port: str = ""  # empty -> auto-detect the FTDI serial port
    enable_wall: bool = True
    grip: str = "right"  # "right" | "left" -- same semantics as run_env --grip
    close_at: float = GRIPPER_CLOSE_AT  # trigger crossing that closes the hand
    open_at: float = 0.2  # ... and the one that reopens it (hysteresis)


class GelloFR3Teleop(Teleoperator):
    config_class = GelloFR3TeleopConfig
    name = "gello_fr3"

    def __init__(self, config: GelloFR3TeleopConfig):
        super().__init__(config)
        self.config = config
        self._agent = None
        self._closed = False

    @property
    def action_features(self) -> dict:
        return {k: float for k in JOINT_KEYS}

    @property
    def feedback_features(self) -> dict:
        return {}

    @property
    def is_connected(self) -> bool:
        return self._agent is not None

    def connect(self, calibrate: bool = True) -> None:
        from gello.agents.gello_agent import GelloAgent

        port = self.config.port or _find_gello_port()
        self._agent = GelloAgent(
            port=port, enable_wall=self.config.enable_wall, grip=self.config.grip
        )
        self._closed = False

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def get_action(self) -> dict[str, Any]:
        raw = np.asarray(self._agent.act({}), dtype=float)  # 7 joints + trigger 0..1
        trigger = float(raw[7])
        if not self._closed and trigger >= self.config.close_at:
            self._closed = True
        elif self._closed and trigger <= self.config.open_at:
            self._closed = False
        vec = np.append(raw[:7], 1.0 if self._closed else 0.0)
        return dict(zip(JOINT_KEYS, vec.tolist()))

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        pass

    # ------------------------------------------------------- pose-match assist
    def start_pose_match(self, target_q: np.ndarray) -> None:
        """See ``GelloAgent.start_pose_match``. Requires ``connect()`` first."""
        assert self._agent is not None, "not connected"
        self._agent.start_pose_match(target_q)

    def pose_match_status(self) -> dict[str, Any]:
        if self._agent is None:
            return {"error": None, "done": True}
        return self._agent.pose_match_status()

    def cancel_pose_match(self) -> None:
        if self._agent is not None:
            self._agent.cancel_pose_match()

    def disconnect(self) -> None:
        if self._agent is not None:
            self._agent.close()
            self._agent = None
