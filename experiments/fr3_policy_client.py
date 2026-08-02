"""FR3 policy client — runs on the FR3 controller computer (lerobot-venv).

Streams observations to the GPU policy server (mamba-embeddingvla
real_deploy/fr3_policy_server.py) and executes the returned 8-dim absolute
joint-angle chunks on the robot. No model compute on this machine.

Data path per replan cycle (default 0.75 s = 15 steps @ 20 Hz):
  FR3ZMQRobot.get_observation()             joints 7 rad + gripper 0..1, 2x 640x480 RGB
    -> resize_rgb (libero_format)           center-crop 480^2 -> 256^2  (train-identical)
    -> base64 JSON POST /infer              ~0.4 MB/request
    <- {"actions": [[8] x 15]}              joint1-7 rad + gripper 0..1 (absolute)
    -> send_action at 20 Hz                 with per-step |dq| safety clamp

Prerequisites:
  * robot node running:  (pylibfranka-venv) python experiments/launch_nodes.py --robot fr3
  * policy server running on the GPU machine (see SERVER_URL)

Comm test WITHOUT the robot (synthetic obs, checks server round-trip + latency):
  python experiments/fr3_policy_client.py --dry-run

Real run:
  python experiments/fr3_policy_client.py [--instruction "..."] [--max-seconds 30]
"""

from __future__ import annotations

import argparse
import base64
import time

import numpy as np
import requests

# ───────────────────────── CONFIG (edit me) ─────────────────────────
SERVER_URL = "http://192.168.0.10:8080"   # GPU 머신 IP:port
ROBOT_PORT = 6001                          # launch_nodes.py ZMQ port
HOSTNAME = "127.0.0.1"
AGENT_CAMERA_SERIAL = "338122300664"       # RealSense serials (수집 GUI와 동일)
WRIST_CAMERA_SERIAL = "230422272249"
FPS = 20                                   # 학습 데이터와 동일 (20 Hz)
EXEC_HORIZON = 15                          # 청크 중 실행 개수 (15=full=0.75s 재계획)
RESET_POSE = "libero"                      # FR3_RESET_POSES key (수집 세션과 동일해야 함)
DEFAULT_INSTRUCTION = "pick up the skyblue cup and place it on the yellow bowl"
RAMP_STEP = 0.05                           # rad/tick @20Hz — 홈 복귀 램프 (수집기와 동일)
MAX_STEP_RAD = 0.15                        # 안전 클램프: 스텝당 관절 목표-측정 최대 괴리
GRIPPER_OPEN = 0.0
# ─────────────────────────────────────────────────────────────────────


def _b64(img: np.ndarray) -> dict:
    img = np.ascontiguousarray(img, dtype=np.uint8)
    return {"base64": base64.b64encode(img.tobytes()).decode(),
            "shape": list(img.shape), "dtype": "uint8"}


def dry_run(url: str, instruction: str, n: int = 5) -> None:
    """Server round-trip test with synthetic obs — robot/cameras NOT required."""
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)
    state = [0.0, -0.161, 0.0, -2.445, 0.0, 2.227, 0.785, 0.0]  # libero reset pose
    r = requests.post(f"{url}/reset", json={"instruction": instruction}, timeout=60)
    r.raise_for_status()
    print(f"[dry-run] /reset ok: {r.json()}")
    for i in range(n):
        payload = {
            "observation.state": state,
            "observation.images.agent": _b64(img),
            "observation.images.wrist": _b64(img),
        }
        t0 = time.perf_counter()
        r = requests.post(f"{url}/infer", json=payload, timeout=60)
        r.raise_for_status()
        ms = (time.perf_counter() - t0) * 1000
        chunk = r.json()["actions"]
        a0 = np.array(chunk[0])
        print(f"[dry-run] /infer #{i}: {len(chunk)}x{len(chunk[0])} actions, "
              f"round-trip {ms:.1f} ms | a[0] joints(rad)={np.round(a0[:7], 3)} grip={a0[7]:.2f}")
    print("[dry-run] OK — comm path verified.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=SERVER_URL)
    ap.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    ap.add_argument("--dry-run", action="store_true",
                    help="server comm test with synthetic obs (no robot needed)")
    ap.add_argument("--max-seconds", type=float, default=30.0)
    ap.add_argument("--exec-horizon", type=int, default=EXEC_HORIZON)
    args = ap.parse_args()

    if args.dry_run:
        dry_run(args.server, args.instruction)
        return

    from lerobot.cameras.realsense import RealSenseCameraConfig

    from gello.lerobot_plugin import JOINT_KEYS, FR3ZMQRobot, FR3ZMQRobotConfig
    from gello.libero_format import resize_rgb
    from gello.robots.franka_fr3 import FR3_RESET_POSES

    robot = FR3ZMQRobot(FR3ZMQRobotConfig(
        id="fr3", host=HOSTNAME, port=ROBOT_PORT,
        cameras={
            "agent": RealSenseCameraConfig(
                serial_number_or_name=AGENT_CAMERA_SERIAL, fps=30, width=640, height=480),
            "wrist": RealSenseCameraConfig(
                serial_number_or_name=WRIST_CAMERA_SERIAL, fps=30, width=640, height=480),
        }))
    robot.connect()
    reset_q = FR3_RESET_POSES[RESET_POSE]

    def joints(obs) -> np.ndarray:
        return np.array([obs[k] for k in JOINT_KEYS[:7]])

    def command(q7: np.ndarray, grip: float) -> None:
        robot.send_action(dict(zip(JOINT_KEYS, np.append(q7, grip).tolist())))

    try:
        # ── 홈 복귀 램프 (수집기 _ramp_to와 동일 상수) ──
        print(f"[client] ramping to reset pose '{RESET_POSE}' ...")
        for _ in range(600):
            obs = robot.get_observation()
            q = joints(obs)
            d = reset_q - q
            if np.abs(d).max() < 0.02:
                break
            command(q + np.clip(d, -RAMP_STEP, RAMP_STEP), GRIPPER_OPEN)
            time.sleep(1.0 / FPS)
        else:
            raise RuntimeError("reset ramp did not converge")
        print("[client] at reset pose.")

        r = requests.post(f"{args.server}/reset",
                          json={"instruction": args.instruction}, timeout=60)
        r.raise_for_status()
        print(f"[client] /reset ok: {r.json()['instruction']!r}")

        dt = 1.0 / FPS
        deadline = time.monotonic() + args.max_seconds
        n_replans = 0
        while time.monotonic() < deadline:
            obs = robot.get_observation()
            state = [float(obs[k]) for k in JOINT_KEYS]  # 7 rad + gripper 0..1
            payload = {
                "observation.state": state,
                "observation.images.agent": _b64(resize_rgb(obs["agent"])),
                "observation.images.wrist": _b64(resize_rgb(obs["wrist"])),
            }
            t0 = time.perf_counter()
            r = requests.post(f"{args.server}/infer", json=payload, timeout=60)
            r.raise_for_status()
            chunk = np.asarray(r.json()["actions"], dtype=float)  # [15,8]
            n_replans += 1
            if n_replans % 4 == 1:
                print(f"[client] replan #{n_replans}: infer {1000*(time.perf_counter()-t0):.0f} ms")

            t_next = time.monotonic()
            for a in chunk[: args.exec_horizon]:
                q_meas = joints(robot.get_observation())
                # 안전 클램프: 목표가 측정치에서 MAX_STEP_RAD 이상 벗어나지 않게
                q_tgt = q_meas + np.clip(a[:7] - q_meas, -MAX_STEP_RAD, MAX_STEP_RAD)
                command(q_tgt, float(np.clip(a[7], 0.0, 1.0)))
                t_next += dt
                time.sleep(max(0.0, t_next - time.monotonic()))
                if time.monotonic() >= deadline:
                    break
        print(f"[client] done ({n_replans} replans).")
    except KeyboardInterrupt:
        print("\n[client] interrupted.")
    finally:
        robot.disconnect()
        print("[client] disconnected.")


if __name__ == "__main__":
    main()
