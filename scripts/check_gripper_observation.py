#!/usr/bin/env python3
"""Does the *observation* now follow the fingers, at the rate it is recorded?

`obs/gripper_states` in the .hdf5 files comes from
`FrankaFR3Robot.get_observations()["gripper_position"]`, which reads
`_gripper_state_width`. Before the reader/commander split, one thread both
sampled that width and issued the blocking grasp/move, so it stopped sampling
for the whole stroke and the recorded channel was a frozen value that jumped
once (37 frames held, then 100% of the range in a single frame, over 254
episodes).

`check_gripper_concurrent_read.py` already showed the *hardware* can be read
during a command. This checks the layer above it: that the split actually
reaches the observation the collector writes down.

The robot is constructed **read_only** -- the arm streams state and is never
commanded, so nothing moves except the fingers, which this script drives
directly the way `_gripper_cmd_loop` would.

SAFETY: this MOVES THE FINGERS (close with force, then open). Clear the hand.
Do not run while a collection session or launch_nodes.py owns the robot.

    ~/pylibfranka-venv/bin/python3 scripts/check_gripper_observation.py
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE_HZ = 20.0          # 수집기와 같은 기록 주기
MIN_DISTINCT = 5          # 램프라면 스트로크 동안 서로 다른 값이 여럿 나온다


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="172.16.0.2")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    print(__doc__.split("SAFETY:")[1].split("\n\n")[0].strip())
    if not args.yes and input("\n손을 비웠습니까? 'yes': ").strip().lower() != "yes":
        return 1

    from gello.robots.franka_fr3 import MAX_GRIPPER_WIDTH, FrankaFR3Robot

    print(f"\n{args.ip} 연결 (read_only -- 팔은 절대 움직이지 않습니다)...")
    robot = FrankaFR3Robot(robot_ip=args.ip, use_gripper=True, read_only=True,
                           home_gripper=False)
    time.sleep(0.5)

    samples: list[tuple[float, float]] = []
    stop = threading.Event()

    def sampler() -> None:
        """수집기가 하는 것과 같은 호출을 같은 주기로."""
        period = 1.0 / SAMPLE_HZ
        while not stop.is_set():
            t0 = time.monotonic()
            obs = robot.get_observations()
            samples.append((t0, float(obs["gripper_position"])))
            time.sleep(max(0.0, period - (time.monotonic() - t0)))

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    time.sleep(0.5)

    def phase(name: str, fn) -> bool:
        t0 = time.monotonic()
        fn()
        t1 = time.monotonic()
        seg = [v for t, v in samples if t0 <= t <= t1]
        distinct = {round(v, 3) for v in seg}
        print(f"\n--- {name} ({t1 - t0:.2f}s) ---")
        print(f"  표본 {len(seg)}개, 서로 다른 값 {len(distinct)}개")
        print(f"  궤적: {' '.join(f'{v:.3f}' for v in seg[:26])}"
              + (" ..." if len(seg) > 26 else ""))
        ok = len(distinct) >= MIN_DISTINCT
        print(f"  {'PASS' if ok else 'FAIL'}  관측이 램프를 따라감 "
              f"(서로 다른 값 >= {MIN_DISTINCT})")
        return ok

    a = phase("닫기 grasp(0.0, 40N)", lambda: robot._gripper.grasp(
        0.0, 0.1, 40.0, epsilon_inner=0.08, epsilon_outer=0.08))
    time.sleep(0.4)
    b = phase("열기 move(0.08)",
              lambda: robot._gripper.move(MAX_GRIPPER_WIDTH, 0.1))

    time.sleep(0.4)
    stop.set()
    th.join(timeout=2.0)
    robot.stop()

    print("\n" + "=" * 62)
    if a and b:
        print("관측 경로 정상 — .hdf5 의 obs/gripper_states 가 이제 실제 폭 궤적입니다.")
    else:
        print("아직 계단입니다 — 위 FAIL 항목을 보세요.")
    print("=" * 62)
    print(f"총 표본 {len(samples)}개 "
          f"({len(samples) / max(samples[-1][0] - samples[0][0], 1e-9):.1f} Hz)")
    return 0 if (a and b) else 2


if __name__ == "__main__":
    raise SystemExit(main())
