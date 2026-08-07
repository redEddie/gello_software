"""FR3 + GELLO LeRobot 데이터셋 수집 도구 (lerobot-venv에서 실행).

사전 조건: robot node가 떠 있어야 한다 --
  (pylibfranka-venv) python experiments/launch_nodes.py --robot fr3

사용 예:
  (lerobot-venv) python experiments/record_dataset.py \
      --task "pick up the red block" --num-episodes 10 --grip left

에피소드 흐름: 홈 복귀 -> (2회차부터) 리셋 대기 -> 리더 자세 게이트(0.5 rad)
-> 점진 접근 램프 -> 기록.

기록 중 키: [space] 에피소드 완료(저장)  [esc] 폐기 후 재기록  [q] 저장 후 세션 종료
리셋 대기 중 키: [space] 대기 건너뛰기  [q] 세션 종료

robot node가 죽으면(reflex 후 등) 진행 중 에피소드만 폐기하고 대기 상태가 된다.
launch_nodes를 재시작하면 자동으로 재연결해 같은 에피소드 번호부터 이어간다.

세션 종료 시 ds.finalize()로 parquet footer/메타데이터까지 확정하므로 파일은
프로세스 종료를 기다리지 않고 바로 읽을 수 있다.  --resume으로 기존 데이터셋에
에피소드를 이어 붙일 수 있다(같은 --root 필요).
"""

import argparse
import select
import sys
import termios
import time
import tty
from pathlib import Path

import numpy as np
import zmq

from gello.lerobot_plugin import (
    JOINT_KEYS,
    FR3ZMQRobot,
    FR3ZMQRobotConfig,
    GelloFR3Teleop,
    GelloFR3TeleopConfig,
)
from gello.robots.franka_fr3 import DEFAULT_RESET_POSE, FR3_RESET_POSES
from gello.station import load_station
from lerobot.cameras.realsense import RealSenseCameraConfig
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.utils.feature_utils import (
    build_dataset_frame,
    combine_feature_dicts,
    hw_to_dataset_features,
)

# 스테이션 설정에서 온다 (configs/stations/<이름>.yaml). 여기에 직접 적혀
# 있던 손목 시리얼은 2026-07-27 카메라 교체(D435i -> D405) 이후로 존재하지
# 않는 장치를 가리키고 있었다 -- 같은 값이 네 곳에 복사돼 있으면 이런 건
# 반드시 어딘가 하나는 뒤처진다.
CAMERA_SERIALS = {
    role: load_station().camera(role).serial for role in ("agent", "wrist")
}

GATE_RAD = 0.5  # run_env와 동일한 자세 게이트 임계
RAMP_STEP = 0.05  # rad/tick @20Hz = 1.0 rad/s


class KeyPoller:
    """cbreak 모드로 stdin을 논블로킹 폴링. space / esc / q만 구분한다.

    stdin이 tty가 아니면(로그 파이프 등) 키 입력 없이 시간 제한만으로 동작.
    """

    def __enter__(self):
        self._old = None
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc):
        if self._old is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)

    def poll(self):
        if self._old is None:
            return None
        key = None
        while select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                # 화살표 키 등은 ESC 뒤에 바이트가 바로 따라온다 -> 무시
                if select.select([sys.stdin], [], [], 0.02)[0]:
                    while select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.read(1)
                else:
                    key = "esc"
            elif ch == " ":
                key = "space"
            elif ch in ("q", "Q"):
                key = "q"
        return key


def ramp_to(robot, target_q, max_ticks=600):
    """현재 자세에서 target_q까지 RAMP_STEP씩 이동. 그리퍼는 현 상태 유지."""
    for _ in range(max_ticks):
        obs = robot.get_observation()
        q = np.array([obs[k] for k in JOINT_KEYS[:7]])
        d = target_q - q
        if np.abs(d).max() < 0.02:
            return True
        step = np.clip(d, -RAMP_STEP, RAMP_STEP)
        cmd = dict(zip(JOINT_KEYS, np.append(q + step, obs["gripper.pos"]).tolist()))
        robot.send_action(cmd)
        time.sleep(0.05)
    return False


def reset_wait(seconds, keys):
    print(f"[RESET] 환경을 리셋하세요 ({seconds:.0f}s, space=건너뛰기)", flush=True)
    end = time.monotonic() + seconds
    last = None
    while True:
        remain = end - time.monotonic()
        if remain <= 0:
            return "done"
        k = keys.poll()
        if k == "space":
            return "done"
        if k == "q":
            return "quit"
        if int(remain) != last:
            last = int(remain)
            print(f"  {last + 1}s...", flush=True)
        time.sleep(0.1)


def pose_gate(teleop, reset_q, keys, timeout=60.0):
    print("[GATE] 리더를 로봇(홈) 자세에 맞춰 주세요...", flush=True)
    deadline = time.monotonic() + timeout
    t_print = 0.0
    while True:
        if keys.poll() == "q":
            return "quit"
        act = teleop.get_action()
        q_led = np.array([act[k] for k in JOINT_KEYS[:7]])
        delta = np.abs(q_led - reset_q)
        if delta.max() <= GATE_RAD:
            return "ok"
        if time.monotonic() > deadline:
            print(f"[GATE FAIL] {timeout:.0f}초 내 자세 불일치 (최대 {delta.max():.3f} rad)", flush=True)
            return "fail"
        if time.monotonic() > t_print:
            t_print = time.monotonic() + 1.0
            print(f"  대기 중: 최대 차이 joint{int(delta.argmax()) + 1} {delta.max():.2f} rad", flush=True)
        time.sleep(0.05)


def approach_ramp(robot, teleop):
    for _ in range(100):
        obs = robot.get_observation()
        act = teleop.get_action()
        q_rob = np.array([obs[k] for k in JOINT_KEYS[:7]])
        q_led = np.array([act[k] for k in JOINT_KEYS[:7]])
        d = q_led - q_rob
        if np.abs(d).max() < RAMP_STEP:
            return
        step = np.clip(d, -RAMP_STEP, RAMP_STEP)
        cmd = dict(zip(JOINT_KEYS, np.append(q_rob + step, act["gripper.pos"]).tolist()))
        robot.send_action(cmd)
        time.sleep(0.05)


def wait_node_recovery(robot, keys):
    """노드 사망(reflex 후 종료 등) 시 launch_nodes 수동 재시작을 기다린다.

    카메라/GELLO/데이터셋은 그대로 두고 ZMQ 클라이언트만 재생성한다.
    """
    print(
        "[NODE DOWN] robot node 무응답 -- launch_nodes를 재시작하세요. "
        "복구되면 자동으로 이어갑니다. (q=세션 종료)",
        flush=True,
    )
    while True:
        if keys.poll() == "q":
            return False
        try:
            robot.reconnect_node()
        except Exception:
            time.sleep(2.0)
            continue
        print("[NODE OK] 재연결 완료 -- 홈 복귀부터 재개", flush=True)
        return True


def record_episode(robot, teleop, ds, task, fps, max_frames, keys):
    """1 에피소드 기록. (outcome, n_frames) 반환; outcome은 save|discard|quit."""
    budget_ms = 1000.0 / fps
    lat = []
    outcome = "save"
    n = 0
    t_next = time.monotonic()
    for i in range(max_frames):
        k = keys.poll()
        if k == "esc":
            outcome = "discard"
            break
        if k in ("space", "q"):
            outcome = "quit" if k == "q" else "save"
            break
        t0 = time.monotonic()
        action = teleop.get_action()
        robot.send_action(action)
        obs = robot.get_observation()
        frame = {
            **build_dataset_frame(ds.features, obs, prefix="observation"),
            **build_dataset_frame(ds.features, action, prefix="action"),
        }
        frame["task"] = task
        ds.add_frame(frame)
        lat.append((time.monotonic() - t0) * 1000)
        n = i + 1
        if n % (fps * 5) == 0:
            print(f"  {n // fps}s / {max_frames // fps}s", flush=True)
        t_next += 1.0 / fps
        time.sleep(max(0.0, t_next - time.monotonic()))
    if lat:
        arr = np.array(lat)
        over = int((arr > budget_ms).sum())
        print(
            f"  틱 작업시간 mean={arr.mean():.1f}ms p95={np.percentile(arr, 95):.1f}ms "
            f"max={arr.max():.1f}ms | 예산({budget_ms:.0f}ms) 초과 {over}/{len(arr)}",
            flush=True,
        )
    return outcome, n


def validate(root, fps):
    import pandas as pd

    files = sorted(root.glob("data/**/*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    print(f"\n=== 검증: {len(files)} parquet, 총 {len(df)} 프레임 ===", flush=True)
    worst_dev = 0.0
    for ep, g in df.groupby("episode_index"):
        ts = g["timestamp"].to_numpy()
        dev = float(np.abs(np.diff(ts) - 1.0 / fps).max()) if len(ts) > 1 else 0.0
        worst_dev = max(worst_dev, dev)
        ac = np.stack(g["action"])
        grip_changes = int((np.diff(ac[:, 7]) != 0).sum())
        print(
            f"  ep{int(ep)}: {len(g)} 프레임 ({len(g) / fps:.1f}s), "
            f"그리퍼 전환 {grip_changes}회",
            flush=True,
        )
    print(f"timestamp 최대 편차: {worst_dev * 1000:.3f}ms", flush=True)
    videos = list(root.rglob("*.mp4"))
    print(f"비디오 파일: {len(videos)}개", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", required=True, help="에피소드 task 문자열 (예: 'pick up the red block')")
    p.add_argument("--num-episodes", type=int, default=10, help="이번 세션에서 수집할 에피소드 수")
    p.add_argument("--max-seconds", type=float, default=20.0, help="에피소드 길이 상한 (초)")
    p.add_argument("--reset-seconds", type=float, default=10.0, help="에피소드 사이 리셋 대기 (초)")
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--repo-id", default="local/fr3_teleop", help="HF repo id (push 시 사용자계정/이름)")
    p.add_argument("--root", type=Path, default=None, help="데이터셋 디렉토리 (기본: ~/fr3_datasets/<이름>)")
    p.add_argument("--grip", choices=("right", "left"), default="right", help="GELLO 손잡이 방향 (run_env와 동일)")
    p.add_argument("--resume", action="store_true", help="기존 데이터셋에 에피소드 이어 붙이기")
    args = p.parse_args()

    root = args.root or Path.home() / "fr3_datasets" / args.repo_id.split("/")[-1]
    if root.exists() and not args.resume:
        raise SystemExit(f"[ABORT] {root} 이미 존재. --resume으로 이어붙이거나 --root를 바꾸세요.")
    if args.resume and not root.exists():
        raise SystemExit(f"[ABORT] --resume이지만 {root}가 없습니다.")

    robot = FR3ZMQRobot(
        FR3ZMQRobotConfig(
            id="fr3",
            # 스트림 포맷도 스테이션 설정에서. 여기 박혀 있던 fps=60 은 손목이
            # D435i 이던 시절 값이라, D405 로 바뀐 지금은 librealsense 가 스트림
            # 설정을 거부하고 "device busy" ConnectionError 로 나온다.
            cameras={
                name: RealSenseCameraConfig(
                    serial_number_or_name=serial,
                    fps=load_station().camera(name).fps,
                    width=load_station().camera(name).width,
                    height=load_station().camera(name).height,
                )
                for name, serial in CAMERA_SERIALS.items()
            },
        )
    )
    teleop = GelloFR3Teleop(GelloFR3TeleopConfig(id="gello", grip=args.grip))
    t0 = time.monotonic()
    robot.connect()
    t1 = time.monotonic()
    teleop.connect()
    t2 = time.monotonic()
    print(
        f"[OK] 연결 완료 -- robot node+카메라 {t1 - t0:.1f}s, GELLO {t2 - t1:.1f}s",
        flush=True,
    )

    if args.resume:
        ds = LeRobotDataset.resume(args.repo_id, root=root)
        done = ds.meta.total_episodes
        print(f"[RESUME] 기존 {done} 에피소드에 이어서 기록", flush=True)
    else:
        features = combine_feature_dicts(
            hw_to_dataset_features(robot.observation_features, "observation"),
            hw_to_dataset_features(robot.action_features, "action"),
        )
        ds = LeRobotDataset.create(
            args.repo_id, args.fps, root=root, robot_type=robot.name,
            features=features, use_videos=True,
        )
    print(f"[DS] {root}", flush=True)

    reset_q = FR3_RESET_POSES[DEFAULT_RESET_POSE]
    max_frames = int(args.max_seconds * args.fps)
    saved = 0
    try:
        with KeyPoller() as keys:
            ep = 0
            need_reset = False  # 첫 에피소드 전에는 리셋 대기 없음
            while ep < args.num_episodes:
                try:
                    print(f"\n[EP {ep + 1}/{args.num_episodes}] 홈 복귀...", flush=True)
                    if not ramp_to(robot, reset_q):
                        print("[ABORT] 홈 자세 미도달 -- 로봇 상태 확인", flush=True)
                        break
                    if need_reset:
                        if reset_wait(args.reset_seconds, keys) == "quit":
                            break
                    g = pose_gate(teleop, reset_q, keys)
                    if g != "ok":
                        break
                    approach_ramp(robot, teleop)
                    print(
                        f"=== EP {ep + 1} 기록 (최대 {args.max_seconds:.0f}s | "
                        "space=완료  esc=폐기  q=저장 후 종료) ===",
                        flush=True,
                    )
                    outcome, n = record_episode(
                        robot, teleop, ds, args.task, args.fps, max_frames, keys
                    )
                    need_reset = True
                    if outcome == "discard":
                        ds.clear_episode_buffer()
                        print(f"[EP {ep + 1}] 폐기 ({n} 프레임) -- 재기록", flush=True)
                        continue
                    t0 = time.monotonic()
                    ds.save_episode()
                    saved += 1
                    print(
                        f"[EP {ep + 1}] 저장 ({n} 프레임 {n / args.fps:.1f}s, "
                        f"save {time.monotonic() - t0:.1f}s)",
                        flush=True,
                    )
                    ep += 1
                    if outcome == "quit":
                        break
                except zmq.ZMQError:
                    # 노드 사망(reflex 후 등): 진행 중 에피소드는 폐기하고,
                    # launch_nodes 수동 재시작을 기다렸다가 같은 번호를 재기록.
                    try:
                        ds.clear_episode_buffer()
                    except Exception:
                        pass
                    print(f"\n[EP {ep + 1}] 노드 통신 두절 -- 에피소드 폐기", flush=True)
                    if not wait_node_recovery(robot, keys):
                        break
                    need_reset = True
    except KeyboardInterrupt:
        print("\n[CTRL-C] 진행 중 에피소드는 폐기하고 종료합니다.", flush=True)
    except Exception as e:
        # 노드 사망(ZMQ 타임아웃) 등 -- 저장된 에피소드는 finalize로 보존한다.
        print(f"\n[ERROR] {type(e).__name__}: {e} -- 진행 중 에피소드 폐기 후 종료", flush=True)
    finally:
        # 데이터 확정이 최우선: 로봇/카메라 정리가 실패하거나 여기서 다시
        # Ctrl-C가 들어와도 저장분은 이미 디스크에 완성돼 있어야 한다.
        try:
            ds.clear_episode_buffer()  # 미저장 부분 에피소드는 폐기 (없으면 no-op)
        except Exception:
            pass
        ds.finalize()  # parquet footer + 메타데이터 확정 (프로세스 종료 불필요)
        print(f"[END] finalize 완료 -- 이번 세션 {saved} 에피소드 저장", flush=True)
        try:
            print("[END] 홈 복귀...", flush=True)
            ramp_to(robot, reset_q)
        except (Exception, KeyboardInterrupt):
            print("[END] 홈 복귀 생략(노드 무응답 또는 중단)", flush=True)
        for cleanup in (teleop.disconnect, robot.disconnect):
            try:
                cleanup()
            except (Exception, KeyboardInterrupt):
                pass

    if saved:
        validate(root, args.fps)
    print("\n완료.", flush=True)


if __name__ == "__main__":
    main()
