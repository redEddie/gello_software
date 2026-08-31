#!/usr/bin/env python3
"""How close to continuous width control can the Franka Hand actually get?

The trigger on the leader is already an analog 0..1 value -- `_gripper_target`
in `gello/robots/franka_fr3.py` is a float, and only the *use* of it is binary
(hysteresis at 0.6/0.2, then a full-stroke grasp or a full-stroke move). The
question is whether the hand can honour a proportional command instead.

Two things decide that, and neither is in the API docs:

1. What does a *small* move cost? A full stroke measured 1.39 s, but if the
   cost is dominated by distance then a 5 mm correction may be ~100 ms and
   proportional control is usable. If it is dominated by per-command overhead,
   every update costs about the same and it is not.

2. What happens to an in-flight command when the next one arrives? Proportional
   control means re-targeting before the previous motion finished. libfranka
   may queue it, reject it, or need an explicit stop() first.

Measures both, plus the achieved-vs-commanded width error.

SAFETY: this MOVES THE FINGERS repeatedly across the whole stroke. Clear the
hand. Nothing is grasped -- `move` is position-only, no grip force -- so do not
leave an object between the fingers.
Do not run while a collection session or launch_nodes.py owns the gripper.

    ~/pylibfranka-venv/bin/python3 scripts/check_gripper_continuous.py
"""

from __future__ import annotations

import argparse
import threading
import time

MAX_W = 0.08


def timed(fn, *a, **kw):
    t0 = time.monotonic()
    try:
        r = fn(*a, **kw)
        err = None
    except Exception as e:  # noqa: BLE001
        r, err = None, f"{type(e).__name__}: {e}"
    return time.monotonic() - t0, r, err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="172.16.0.2")
    ap.add_argument("--speed", type=float, default=0.1)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    print(__doc__.split("SAFETY:")[1].split("\n\n")[0].strip())
    if not args.yes and input("\n손을 비웠습니까? 'yes': ").strip().lower() != "yes":
        return 1

    import pylibfranka as pf

    g = pf.Gripper(args.ip)
    print(f"\n연결됨. 시작 폭 {g.read_once().width:.4f} m\n")

    # ---------------------------------------------------------- 1) 이동 거리별
    print("=" * 66)
    print("1) 이동 거리에 따른 소요 시간 — 거리 비례인가, 고정 오버헤드인가")
    print("=" * 66)
    print(f"  {'목표 변화':>10} {'소요':>8} {'도달 폭':>9} {'오차':>8}   {'거리/시간':>10}")
    rows = []
    for delta_mm in (1, 2, 5, 10, 20, 40, 79):
        d = delta_mm / 1000.0
        timed(g.move, MAX_W, args.speed)          # 매번 완전 개방에서 출발
        time.sleep(0.15)
        w0 = g.read_once().width
        dt, ok, err = timed(g.move, MAX_W - d, args.speed)
        time.sleep(0.15)
        w1 = g.read_once().width
        if err:
            print(f"  {delta_mm:>8} mm  예외: {err}")
            continue
        moved = (w0 - w1) * 1000
        rows.append((delta_mm, dt, moved))
        print(f"  {delta_mm:>8} mm {dt:>7.3f}s {w1:>8.4f}m {moved - delta_mm:>+7.1f}mm"
              f" {moved / max(dt, 1e-9):>9.1f} mm/s")
    if len(rows) >= 2:
        big, small = rows[-1], rows[0]
        # t = overhead + dist/speed 로 보고 두 점에서 오버헤드를 뽑는다.
        v = (big[2] - small[2]) / max(big[1] - small[1], 1e-9)
        overhead = small[1] - small[2] / max(v, 1e-9)
        print(f"\n  → 실효 속도 약 {v:.0f} mm/s, 명령당 고정 오버헤드 약 {overhead * 1000:.0f} ms")
        print(f"    (20Hz 로 갱신하려면 명령 하나가 50 ms 안에 끝나야 함)")

    # ------------------------------------------------------ 2) 도중에 재타겟팅
    print("\n" + "=" * 66)
    print("2) 진행 중인 명령을 새 명령으로 덮어쓸 수 있는가")
    print("=" * 66)
    timed(g.move, MAX_W, args.speed)
    time.sleep(0.2)

    result = {}

    def issue_long():
        result["long"] = timed(g.move, 0.0, args.speed)   # 전체 스트로크 (~1.4s)

    th = threading.Thread(target=issue_long, daemon=True)
    th.start()
    time.sleep(0.35)                                      # 한창 움직이는 중
    w_mid = g.read_once().width
    dt2, ok2, err2 = timed(g.move, 0.04, args.speed)      # 도중에 다른 목표로
    th.join(timeout=5.0)
    time.sleep(0.3)
    w_end = g.read_once().width
    dt1, ok1, err1 = result.get("long", (0, None, "실행 안 됨"))
    print(f"  진행 중 폭        : {w_mid:.4f} m")
    print(f"  1차 move(0.000)   : {dt1:.3f}s  반환={ok1}  {err1 or ''}")
    print(f"  2차 move(0.040)   : {dt2:.3f}s  반환={ok2}  {err2 or ''}")
    print(f"  최종 폭           : {w_end:.4f} m  (2차 목표 0.0400 m)")
    if err2:
        verdict2 = "거부됨 — 재타겟팅하려면 stop() 이 먼저 필요"
    elif abs(w_end - 0.04) < 0.005:
        verdict2 = "가능 — 새 명령이 이전 것을 대체하고 새 목표에 도달"
    else:
        verdict2 = f"불명확 — 최종 폭이 두 목표 어느 쪽도 아님 ({w_end:.4f})"
    print(f"  → {verdict2}")

    # --------------------------------------------------- 3) stop() 후 재타겟팅
    print("\n" + "=" * 66)
    print("3) stop() 으로 끊고 새 목표를 주면")
    print("=" * 66)
    timed(g.move, MAX_W, args.speed)
    time.sleep(0.2)
    th = threading.Thread(target=lambda: result.update(l2=timed(g.move, 0.0, args.speed)),
                          daemon=True)
    th.start()
    time.sleep(0.35)
    dts, oks, errs = timed(g.stop)
    dt3, ok3, err3 = timed(g.move, 0.04, args.speed)
    th.join(timeout=5.0)
    time.sleep(0.3)
    print(f"  stop()          : {dts:.3f}s 반환={oks} {errs or ''}")
    print(f"  이후 move(0.040): {dt3:.3f}s 반환={ok3} {err3 or ''}")
    print(f"  최종 폭         : {g.read_once().width:.4f} m")

    timed(g.move, MAX_W, args.speed)
    print("\n완료 — 손을 열어두었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
