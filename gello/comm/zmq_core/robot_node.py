import pickle
import threading
from typing import Any, Dict

import numpy as np
import zmq

from gello.core.robot import Robot

DEFAULT_ROBOT_PORT = 6000


class ZMQServerRobot:
    def __init__(
        self,
        robot: Robot,
        port: int = DEFAULT_ROBOT_PORT,
        host: str = "127.0.0.1",
    ):
        self._robot = robot
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        addr = f"tcp://{host}:{port}"
        debug_message = f"Robot Sever Binding to {addr}, Robot: {robot}"
        print(debug_message)
        self._timout_message = f"Timeout in Robot Server, Robot: {robot}"
        self._socket.bind(addr)
        self._stop_event = threading.Event()

    def serve(self) -> None:
        """Serve the leader robot state over ZMQ."""
        self._socket.setsockopt(zmq.RCVTIMEO, 1000)  # Set timeout to 1000 ms
        while not self._stop_event.is_set():
            try:
                # Wait for next request from client
                message = self._socket.recv()
                request = pickle.loads(message)

                # Call the appropriate method based on the request. Errors
                # from the robot call itself (e.g. FrankaFR3Robot reporting
                # its control loop died) are caught here, not just
                # zmq.Again below: REQ/REP is strict lock-step, so if an
                # exception escaped between recv() and send() the socket
                # would be left expecting a reply that never comes, hanging
                # every later request -- and an uncaught exception here
                # would kill this whole serve() loop (this process's only
                # thread handling robot commands), not just the one
                # request. Sending back {"error": ...} keeps the loop (and
                # the process) alive so the client's existing error
                # handling can react instead of everything just hanging.
                method = request.get("method")
                args = request.get("args", {})
                result: Any
                try:
                    if method == "num_dofs":
                        result = self._robot.num_dofs()
                    elif method == "get_joint_state":
                        result = self._robot.get_joint_state()
                    elif method == "command_joint_state":
                        result = self._robot.command_joint_state(**args)
                    elif method == "get_observations":
                        result = self._robot.get_observations()
                    else:
                        result = {"error": "Invalid method"}
                        print(result)
                        raise NotImplementedError(
                            f"Invalid method: {method}, {args, result}"
                        )
                except Exception as e:  # noqa: BLE001
                    result = {"error": f"{type(e).__name__}: {e}"}
                    print(f"[ZMQServerRobot] {method} failed: {result['error']}")

                self._socket.send(pickle.dumps(result))
            except zmq.Again:
                # Timeout occurred - don't spam the console
                pass

    def stop(self) -> None:
        """Signal the server to stop serving."""
        self._stop_event.set()


class ZMQClientRobot(Robot):
    """A class representing a ZMQ client for a leader robot."""

    def __init__(self, port: int = DEFAULT_ROBOT_PORT, host: str = "127.0.0.1"):
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REQ)
        self._socket.connect(f"tcp://{host}:{port}")

    def num_dofs(self) -> int:
        """Get the number of joints in the robot.

        Returns:
            int: The number of joints in the robot.
        """
        request = {"method": "num_dofs"}
        send_message = pickle.dumps(request)
        self._socket.send(send_message)
        result = pickle.loads(self._socket.recv())
        return result

    def get_joint_state(self) -> np.ndarray:
        """Get the current state of the leader robot.

        Returns:
            T: The current state of the leader robot.
        """
        request = {"method": "get_joint_state"}
        send_message = pickle.dumps(request)
        try:
            self._socket.send(send_message)
            result = pickle.loads(self._socket.recv())
            if isinstance(result, dict) and "error" in result:
                raise RuntimeError(result["error"])
            return result
        except zmq.Again:
            raise RuntimeError("ZMQ timeout - robot may be disconnected")

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        """Command the leader robot to the given state.

        Args:
            joint_state (T): The state to command the leader robot to.
        """
        request = {
            "method": "command_joint_state",
            "args": {"joint_state": joint_state},
        }
        send_message = pickle.dumps(request)
        try:
            self._socket.send(send_message)
            result = pickle.loads(self._socket.recv())
            if isinstance(result, dict) and "error" in result:
                raise RuntimeError(result["error"])
            return result
        except zmq.Again:
            raise RuntimeError("ZMQ timeout - robot may be disconnected")

    def get_observations(self) -> Dict[str, np.ndarray]:
        """Get the current observations of the leader robot.

        Returns:
            Dict[str, np.ndarray]: The current observations of the leader robot.
        """
        request = {"method": "get_observations"}
        send_message = pickle.dumps(request)
        try:
            self._socket.send(send_message)
            result = pickle.loads(self._socket.recv())
            if isinstance(result, dict) and "error" in result:
                raise RuntimeError(result["error"])
            return result
        except zmq.Again:
            raise RuntimeError("ZMQ timeout - robot may be disconnected")

    def close(self) -> None:
        """Close the ZMQ socket and context."""
        self._socket.close()
        self._context.term()


def probe_observation(host: str = "127.0.0.1", port: int = DEFAULT_ROBOT_PORT,
                      timeout_ms: int = 3000) -> "Dict[str, np.ndarray]":
    """로봇 노드에 관측을 한 번만 물어보고 소켓을 닫는다.

    "이 장비가 이 스키마 버전이 요구하는 값을 실제로 주는가" 를 찍기 전에
    확인하기 위한 것이다 (런처 하드웨어 페이지의 '확인' 버튼). 포스·토크는
    FR3 펌웨어가 그 필드를 노출할 때만 오므로, 안 오는 장비에서 그 버전을
    고르면 필수 필드가 빠진 파일이 된다 -- 수집을 시작하기 전에 알아야 한다.

    노드가 없으면 예외를 던진다. 부르는 쪽이 안내로 바꿔 보여준다.
    """
    ctx = zmq.Context()
    s = ctx.socket(zmq.REQ)
    s.setsockopt(zmq.RCVTIMEO, timeout_ms)
    s.setsockopt(zmq.SNDTIMEO, timeout_ms)
    s.setsockopt(zmq.LINGER, 0)
    try:
        s.connect(f"tcp://{host}:{port}")
        s.send(pickle.dumps({"method": "get_observations"}))
        return pickle.loads(s.recv())
    finally:
        s.close(linger=0)
        ctx.term()
