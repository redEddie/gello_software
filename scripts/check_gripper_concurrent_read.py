#!/usr/bin/env python3
"""Can `Gripper.read_once()` keep sampling while `grasp()`/`move()` is blocking?

Why this question exists
------------------------
`obs/gripper_states` in the collected .hdf5 files is not the width trajectory --
it is a frozen value that jumps once. Measured on 254 real episodes: after the
close command the reading holds its old value for 37 frames (1.85 s at 20 Hz)
and then moves 100% of its range in a *single* frame. A hand physically closing
over ~0.8 s cannot produce that; a reader that is not being called can.

The cause is in `gello/robots/franka_fr3.py::_gripper_loop`: one thread both
reads state and issues commands, and `franka::Gripper::grasp/move` block until
the fingers stop. For the whole stroke nothing calls `read_once()`.

The delay itself is real physics and belongs in the data -- a policy has to
learn that its gripper command lands ~0.8 s later. What must not be in the data
is a step function standing in for that ramp.

What this script tests (option A)
---------------------------------
The cheapest fix is to keep one `Gripper` object and move the blocking command
to its own thread, leaving a reader thread polling at 20 Hz. pylibfranka's GIL
patch already releases the GIL inside grasp/move/read_once, so Python itself is
not the obstacle. The open question is libfranka: `franka::Network` is not
documented as thread-safe, so a concurrent read during an in-flight command may
throw, block, or return garbage.

This runs exactly that pattern and reports which of those happened.

SAFETY: this MOVES THE FINGERS (close, then open). Clear the hand first.
Do not run while a collection session or launch_nodes.py owns the gripper --
only one process can hold the connection.

    python3 scripts/check_gripper_concurrent_read.py            # 기본 172.16.0.2
    python3 scripts/check_gripper_concurrent_read.py --ip ...   # 다른 주소
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time

MAX_GRIPPER_WIDTH = 0.08
POLL_HZ = 20.0
# 스트로크가 0.08 m / 0.1 m/s = 0.8 s 이므로, 이 안에 20Hz면 16개는 찍혀야 한다.
MIN_SAMPLES_IN_STROKE = 8
# 읽기가 명령에 막히지 않았다면 표본 간격은 폴링 주기 근처에 머문다.
MAX_GAP_S = 0.20
# 램프라면 스트로크 동안 서로 다른 폭 값이 여러 개 나온다. 계단이면 1~2개.
MIN_DISTINCT_WIDTHS = 5


class Reader(threading.Thread):
    """20Hz로 read_once()만 한다. 명령은 절대 보내지 않는다."""

    def __init__(self, gripper):
        super().__init__(daemon=True)
        self.g = gripper
        self.stop_flag = threading.Event()
        self.samples: list[tuple[float, float]] = []   # (t, width)
        self.errors: list[tuple[float, str]] = []

    def run(self) -> None:
        period = 1.0 / POLL_HZ
        while not self.stop_flag.is_set():
            t0 = time.monotonic()
            try:
                gs = self.g.read_once()
                self.samples.append((t0, float(gs.width)))
            except Exception as e:  # noqa: BLE001
                self.errors.append((t0, f"{type(e).__name__}: {e}"))
            time.sleep(max(0.0, period - (time.monotonic() - t0)))


def window(samples, t0, t1):
    return [(t, w) for t, w in samples if t0 <= t <= t1]


def report(name: str, samples, errors, t0: float, t1: float) -> bool:
    seg = window(samples, t0, t1)
    errs = [e for e in errors if t0 <= e[0] <= t1]
    print(f"\n--- {name}  (명령 {t1 - t0:.2f}s 동안) ---")
    if not seg:
        print("  표본 0개 — 읽기가 명령에 완전히 막혔습니다")
        return False
    gaps = [b[0] - a[0] for a, b in zip(seg, seg[1:])]
    widths = [w for _, w in seg]
    distinct = sorted({round(w, 4) for w in widths})
    max_gap = max(gaps) if gaps else 0.0
    print(f"  표본 {len(seg)}개, 최대 간격 {max_gap * 1000:.0f} ms "
          f"(중앙 {statistics.median(gaps) * 1000:.0f} ms)" if gaps
          else f"  표본 {len(seg)}개")
    print(f"  폭 {min(widths):.4f} → {max(widths):.4f} m, 서로 다른 값 {len(distinct)}개")
    print(f"  궤적: {' '.join(f'{w:.3f}' for _, w in seg[:24])}"
          + (" ..." if len(seg) > 24 else ""))
    if errs:
        print(f"  ✗ read_once() 예외 {len(errs)}건: {errs[0][1][:100]}")
    checks = [
        ("예외 없음", not errs),
        (f"표본 >= {MIN_SAMPLES_IN_STROKE}", len(seg) >= MIN_SAMPLES_IN_STROKE),
        (f"최대 간격 < {MAX_GAP_S * 1000:.0f} ms", max_gap < MAX_GAP_S),
        (f"서로 다른 폭 >= {MIN_DISTINCT_WIDTHS} (계단이 아니라 램프)",
         len(distinct) >= MIN_DISTINCT_WIDTHS),
        ("폭이 물리적으로 타당 (0 ~ 0.081 m)",
         all(-0.001 <= w <= 0.081 for w in widths)),
    ]
    ok = True
    for label, passed in checks:
        print(f"    {'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="172.16.0.2")
    ap.add_argument("--force", type=float, default=40.0)
    ap.add_argument("--speed", type=float, default=0.1)
    ap.add_argument("--yes", action="store_true", help="확인 프롬프트 건너뛰기")
    args = ap.parse_args()

    print(__doc__.split("SAFETY:")[1].split("\n\n")[0].strip())
    if not args.yes:
        if input("\n손을 비웠습니까? 계속하려면 'yes': ").strip().lower() != "yes":
            print("취소했습니다.")
            return 1

    import pylibfranka as pf

    print(f"\n{args.ip} 그리퍼에 연결...")
    g = pf.Gripper(args.ip)
    print(f"  server_version={g.server_version()}  시작 폭={g.read_once().width:.4f} m")

    reader = Reader(g)
    reader.start()
    time.sleep(0.5)                     # 명령 전 기준선
    base = len(reader.samples)
    print(f"  명령 전 0.5초 동안 표본 {base}개 (기대 ~{int(POLL_HZ * 0.5)}개)")

    t0 = time.monotonic()
    print("\ngrasp(0.0) 실행 — 리더 스레드는 계속 폴링해야 합니다...")
    try:
        ok_grasp = g.grasp(0.0, args.speed, args.force,
                           epsilon_inner=0.08, epsilon_outer=0.08)
    except Exception as e:  # noqa: BLE001
        print(f"  grasp 예외: {type(e).__name__}: {e}")
        ok_grasp = False
    t1 = time.monotonic()
    print(f"  grasp 반환 {ok_grasp} ({t1 - t0:.2f}s 걸림)")

    time.sleep(0.3)
    t2 = time.monotonic()
    print(f"\nmove({MAX_GRIPPER_WIDTH}) 실행...")
    try:
        ok_move = g.move(MAX_GRIPPER_WIDTH, args.speed)
    except Exception as e:  # noqa: BLE001
        print(f"  move 예외: {type(e).__name__}: {e}")
        ok_move = False
    t3 = time.monotonic()
    print(f"  move 반환 {ok_move} ({t3 - t2:.2f}s 걸림)")

    time.sleep(0.5)
    reader.stop_flag.set()
    reader.join(timeout=2.0)

    a = report("닫기 (grasp)", reader.samples, reader.errors, t0, t1)
    b = report("열기 (move)", reader.samples, reader.errors, t2, t3)

    print("\n" + "=" * 62)
    if a and b:
        print("판정: A안 가능 — 같은 Gripper 객체로 동시 읽기가 됩니다.")
        print("      _gripper_loop 을 '읽기 스레드 + 명령 스레드'로 쪼개면 됩니다.")
    else:
        print("판정: A안 불가 — 위에서 ✗ 표시된 항목을 보세요.")
        print("      예외/막힘이면 B안(Gripper 연결 2개)을 시도하세요.")
    print("=" * 62)
    print(f"\n총 read_once() 예외 {len(reader.errors)}건 / 표본 {len(reader.samples)}개")
    return 0 if (a and b) else 2


if __name__ == "__main__":
    raise SystemExit(main())
