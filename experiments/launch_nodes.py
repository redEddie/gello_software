import ctypes
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

import tyro

from gello.robots.robot import BimanualRobot, PrintRobot
from gello.zmq_core.robot_node import ZMQServerRobot

_PR_SET_PDEATHSIG = 1


def die_with_parent(sig: int = signal.SIGTERM) -> None:
    """Ask the kernel to signal this process when its parent goes away.

    The GUI stops this node in closeEvent, but that only runs on an orderly
    quit. A hard exit (PyQt aborts the process on an unhandled exception in a
    slot) leaves the node running and holding the robot's FCI connection, so
    the next GUI cannot start one -- and nothing on screen explains why.
    PDEATHSIG survives execve and does not depend on the parent running any
    cleanup code, which is exactly the case that was failing.

    Opt-in via --die-with-parent so a node started by hand in a terminal is
    unaffected.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0)
    except Exception as e:  # noqa: BLE001
        print(f"[node] PDEATHSIG 설정 실패 (계속 진행): {e}", flush=True)


@dataclass
class Args:
    robot: str = "xarm"
    robot_port: int = 6001
    hostname: str = "127.0.0.1"
    # GUI가 켜줄 때 붙인다. 부모가 죽으면 커널이 이 프로세스를 종료시킨다.
    die_with_parent: bool = False
    # 로봇 팔의 IP (정책 서버 주소가 아니다). FR3는 172.16.0.2 --
    # 192.168.1.10은 상류 GELLO 저장소의 xArm/UR 기본값이라 FR3에선 항상 타임아웃난다.
    robot_ip: str = "172.16.0.2"
    # FR3 (pylibfranka) hardware options; only used when robot == "fr3".
    fr3_read_only: bool = False
    fr3_use_gripper: bool = True
    fr3_enforce_rt: bool = True


def launch_robot_server(args: Args):
    port = args.robot_port
    if args.robot == "sim_ur":
        MENAGERIE_ROOT: Path = (
            Path(__file__).parent.parent / "third_party" / "mujoco_menagerie"
        )
        xml = MENAGERIE_ROOT / "universal_robots_ur5e" / "ur5e.xml"
        gripper_xml = MENAGERIE_ROOT / "robotiq_2f85" / "2f85.xml"
        from gello.robots.sim_robot import MujocoRobotServer

        server = MujocoRobotServer(
            xml_path=xml, gripper_xml_path=gripper_xml, port=port, host=args.hostname
        )
        server.serve()
    elif args.robot == "sim_yam":
        MENAGERIE_ROOT: Path = (
            Path(__file__).parent.parent / "third_party" / "mujoco_menagerie"
        )
        xml = MENAGERIE_ROOT / "i2rt_yam" / "yam.xml"
        from gello.robots.sim_robot import MujocoRobotServer

        server = MujocoRobotServer(
            xml_path=xml, gripper_xml_path=None, port=port, host=args.hostname
        )
        server.serve()
    elif args.robot == "sim_fr3":
        from gello.robots.sim_robot import MujocoRobotServer

        MENAGERIE_ROOT: Path = (
            Path(__file__).parent.parent / "third_party" / "mujoco_menagerie"
        )
        xml = MENAGERIE_ROOT / "franka_fr3" / "fr3.xml"
        gripper_xml = MENAGERIE_ROOT / "franka_emika_panda" / "hand.xml"
        # fr3.xml's attachment_site is the bare flange frame; rotate it by +135deg
        # so the attached hand lands at the real robot's -45deg mount, identical
        # to the integrated franka_emika_panda/panda.xml
        server = MujocoRobotServer(
            xml_path=xml,
            gripper_xml_path=gripper_xml,
            gripper_quat=(0.3826834, 0, 0, 0.9238795),
            port=port,
            host=args.hostname,
        )
        server.serve()
    elif args.robot == "sim_panda":
        from gello.robots.sim_robot import MujocoRobotServer

        MENAGERIE_ROOT: Path = (
            Path(__file__).parent.parent / "third_party" / "mujoco_menagerie"
        )
        xml = MENAGERIE_ROOT / "franka_emika_panda" / "panda.xml"
        gripper_xml = None
        server = MujocoRobotServer(
            xml_path=xml, gripper_xml_path=gripper_xml, port=port, host=args.hostname
        )
        server.serve()
    elif args.robot == "sim_xarm":
        from gello.robots.sim_robot import MujocoRobotServer

        MENAGERIE_ROOT: Path = (
            Path(__file__).parent.parent / "third_party" / "mujoco_menagerie"
        )
        xml = MENAGERIE_ROOT / "ufactory_xarm7" / "xarm7.xml"
        gripper_xml = None
        server = MujocoRobotServer(
            xml_path=xml, gripper_xml_path=gripper_xml, port=port, host=args.hostname
        )
        server.serve()

    else:
        if args.robot == "xarm":
            from gello.robots.xarm_robot import XArmRobot

            robot = XArmRobot(ip=args.robot_ip)
        elif args.robot == "ur":
            from gello.robots.ur import URRobot

            robot = URRobot(robot_ip=args.robot_ip)
        elif args.robot == "panda":
            from gello.robots.panda import PandaRobot

            robot = PandaRobot(robot_ip=args.robot_ip)
        elif args.robot == "fr3":
            # Real FR3 via pylibfranka (see gello/robots/franka_fr3.py).
            from gello.robots.franka_fr3 import FrankaFR3Robot

            robot = FrankaFR3Robot(
                robot_ip=args.robot_ip,
                use_gripper=args.fr3_use_gripper,
                read_only=args.fr3_read_only,
                enforce_rt=args.fr3_enforce_rt,
            )
        elif args.robot == "bimanual_ur":
            from gello.robots.ur import URRobot

            # IP for the bimanual robot setup is hardcoded
            _robot_l = URRobot(robot_ip="192.168.2.10")
            _robot_r = URRobot(robot_ip="192.168.1.10")
            robot = BimanualRobot(_robot_l, _robot_r)
        elif args.robot == "yam":
            from gello.robots.yam import YAMRobot

            robot = YAMRobot(channel="can0")
        elif args.robot == "none" or args.robot == "print":
            robot = PrintRobot(8)

        else:
            raise NotImplementedError(
                f"Robot {args.robot} not implemented, choose one of: sim_ur, xarm, ur, bimanual_ur, none"
            )
        server = ZMQServerRobot(robot, port=port, host=args.hostname)
        print(f"Starting robot server on port {port}")
        server.serve()


def main(args):
    if args.die_with_parent:
        die_with_parent()
    launch_robot_server(args)


if __name__ == "__main__":
    main(tyro.cli(Args))
