from dataclasses import dataclass
from multiprocessing import Process

import tyro

import sys
from pathlib import Path

# 이 스크립트가 속한 체크아웃의 gello 를 쓴다. venv 에 설치된 editable
# gello 는 다른 워크트리(deploy)를 가리킬 수 있고, 그러면 여기 코드를
# 실행해도 라이브러리는 저쪽 것이 import 된다 (2026-08-31 실제 사고).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gello.cameras.realsense_camera import RealSenseCamera, get_device_ids  # noqa: E402
from gello.comm.zmq_core.camera_node import ZMQServerCamera  # noqa: E402


@dataclass
class Args:
    # hostname: str = "127.0.0.1"
    hostname: str = "128.32.175.167"


def launch_server(port: int, camera_id: int, args: Args):
    camera = RealSenseCamera(camera_id)
    server = ZMQServerCamera(camera, port=port, host=args.hostname)
    print(f"Starting camera server on port {port}")
    server.serve()


def main(args):
    ids = get_device_ids()
    camera_port = 5000
    camera_servers = []
    for camera_id in ids:
        # start a python process for each camera
        print(f"Launching camera {camera_id} on port {camera_port}")
        camera_servers.append(
            Process(target=launch_server, args=(camera_port, camera_id, args))
        )
        camera_port += 1

    for server in camera_servers:
        server.start()


if __name__ == "__main__":
    main(tyro.cli(Args))
