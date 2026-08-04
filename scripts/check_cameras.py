#!/home/franka/lerobot-venv/bin/python
"""Check that the RealSense cameras are attached, on a USB 3 link, and streaming.

Why this exists
---------------
A D405 on a marginal cable enumerates happily but negotiates a USB 2 link, and
the first sign of it is a session dying mid-collection with

    TimeoutError: latest frame is too old: 548.2 ms (max allowed: 500 ms)

By then the take is lost. This answers the question before a session instead:
is each camera there, how fast is its link, and does it actually deliver frames
at the rate the collector expects.

Two sources, because neither alone is enough. sysfs has the *negotiated link
speed*, which is the ground truth and is readable even while another process
owns the camera. librealsense has the *RealSense serial* -- the number the
collector and the datasets use -- which sysfs does not expose (its `serial`
attribute is a different, USB-level string).

Usage:
    scripts/check_cameras.py              # 링크/열거 확인만 (GUI 켜져 있어도 안전)
    scripts/check_cameras.py --stream     # 실제 프레임까지 (카메라가 비어 있어야 함)
    scripts/check_cameras.py --stream 5 --fps 30

Exit status is 0 only if every check passed, so it can gate a launcher.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

USB_SYSFS = Path("/sys/bus/usb/devices")
INTEL_VENDOR = "8086"
# 5 Gbps SuperSpeed. A D405/D455 that comes up at 480 works well enough to
# preview and badly enough to drop frames under load, which is the failure this
# script is meant to catch early.
MIN_SPEED_MBPS = 5000

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


def _model(text: str) -> str:
    """Model number out of either naming style ("Depth Camera 405" / "D405")."""
    digits = re.findall(r"\d{3}", text)
    return digits[-1] if digits else text


def usb_devices() -> list[dict]:
    """Intel USB devices with their negotiated link speed, straight from sysfs."""
    out = []
    for d in sorted(USB_SYSFS.glob("*")):
        try:
            if (d / "idVendor").read_text().strip() != INTEL_VENDOR:
                continue
            out.append({
                "port": d.name,
                "product": (d / "product").read_text().strip(),
                "id": (d / "idProduct").read_text().strip(),
                "speed": int((d / "speed").read_text().strip()),
                "version": (d / "version").read_text().strip(),
                "devnum": int((d / "devnum").read_text().strip()),
            })
        except (OSError, ValueError):
            continue
    return out


def realsense_devices() -> tuple[list[dict], str]:
    """RealSense serials + the SDK's own view of the USB descriptor."""
    try:
        import pyrealsense2 as rs
    except ImportError as e:
        return [], f"pyrealsense2 없음: {e}"
    try:
        ctx = rs.context()
        out = []
        for dev in ctx.devices:
            def info(key, default="-"):
                try:
                    return dev.get_info(key)
                except Exception:  # noqa: BLE001
                    return default
            out.append({
                "name": info(rs.camera_info.name),
                "serial": info(rs.camera_info.serial_number),
                "usb": info(rs.camera_info.usb_type_descriptor),
                "fw": info(rs.camera_info.firmware_version),
            })
        return out, ""
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


def stream_test(serial: str, seconds: float, fps: int, width: int, height: int) -> dict:
    """Opens one camera and measures what it actually delivers.

    Reports the frame interval spread, not just the mean: a camera that
    averages 30 fps while stalling for 200 ms every few seconds is exactly the
    one that kills a session, and the mean hides it.
    """
    import numpy as np
    import pyrealsense2 as rs

    res = {"serial": serial, "ok": False, "error": "", "n": 0,
           "fps": 0.0, "p95_gap_ms": 0.0, "max_gap_ms": 0.0, "stalls": 0}
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_device(serial)
    cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    try:
        pipe.start(cfg)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    try:
        gaps, last, t_end = [], None, time.monotonic() + seconds
        while time.monotonic() < t_end:
            try:
                frames = pipe.wait_for_frames(timeout_ms=2000)
            except Exception as e:  # noqa: BLE001
                res["error"] = f"{type(e).__name__}: {e}"
                break
            now = time.monotonic()
            if last is not None:
                gaps.append((now - last) * 1000.0)
            last = now
            res["n"] += 1
            if frames:
                pass
        if gaps:
            g = np.asarray(gaps)
            res["fps"] = 1000.0 / float(g.mean())
            res["p95_gap_ms"] = float(np.percentile(g, 95))
            res["max_gap_ms"] = float(g.max())
            # 500 ms is the collector's read_latest tolerance; anything past it
            # is what raises "latest frame is too old" and ends the session.
            res["stalls"] = int((g > 500).sum())
            res["ok"] = not res["error"] and res["stalls"] == 0
    finally:
        try:
            pipe.stop()
        except Exception:  # noqa: BLE001
            pass
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stream", nargs="?", type=float, const=3.0, default=None,
                    metavar="SEC",
                    help="실제 프레임 수신까지 확인 (기본 3초). 카메라가 비어 있어야 함")
    ap.add_argument("--fps", type=int, default=30, help="스트림 테스트 fps (기본 30)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    problems = []
    usb = usb_devices()
    rsd, rs_err = realsense_devices()

    print(_c("USB 링크 (sysfs — GUI가 켜져 있어도 정확)", DIM))
    if not usb:
        print(_c("  Intel RealSense 장치가 USB에 없습니다.", RED))
        problems.append("장치 없음")
    for d in usb:
        ok = d["speed"] >= MIN_SPEED_MBPS
        mark = _c("OK ", GREEN) if ok else _c("느림", RED)
        print(f"  [{mark}] {d['product']}")
        print(f"         포트 {d['port']}  링크 {d['speed']}Mbps  USB {d['version']}"
              f"  (devnum {d['devnum']})")
        if not ok:
            problems.append(f"{d['product']} {d['speed']}Mbps")
            print(_c("         -> USB 2 링크입니다. 케이블/포트를 3.0으로 바꾸세요.", RED))
        if d["devnum"] > 20:
            # devnum only ever increases on this bus, so a high one means the
            # device has re-enumerated many times -- a loose connector.
            print(_c(f"         -> devnum {d['devnum']}: 재연결이 잦았습니다. "
                     f"커넥터 접촉을 확인하세요.", YELLOW))

    print()
    unknown = []
    print(_c("RealSense SDK (수집기가 쓰는 시리얼)", DIM))
    if rs_err:
        print(_c(f"  조회 실패: {rs_err}", YELLOW))
        unknown.append("SDK 조회")
        if "Protocol error" in rs_err or "busy" in rs_err.lower():
            print(_c("         -> 다른 프로그램이 카메라를 쓰는 중일 때 자주 납니다. "
                     "수집 GUI를 끄고 다시 실행하세요.", YELLOW))
    for d in rsd:
        bad = not str(d["usb"]).startswith("3")
        mark = _c("OK ", GREEN) if not bad else _c("느림", RED)
        print(f"  [{mark}] {d['name']}  serial={d['serial']}  USB {d['usb']}  fw {d['fw']}")
        if bad:
            problems.append(f"{d['name']} USB{d['usb']}")
    if not rsd and not rs_err:
        print(_c("  SDK가 카메라를 하나도 못 봤습니다.", RED))
        problems.append("SDK 미검출")
    elif rsd and len(rsd) < len(usb):
        # USB에는 있는데 SDK는 못 보는 상태. 케이블이 헐거워 열거가 반복되는
        # 카메라가 딱 이렇게 보인다 -- 커널은 방금 붙은 장치를 알지만 SDK가
        # 열려는 순간 이미 빠져 있다.
        # sysfs는 "... Depth Camera 405", SDK는 "Intel RealSense D405" 라 문자열이
        # 그대로는 안 맞는다. 모델 번호(끝의 숫자)로 맞춘다.
        seen = {_model(d["name"]) for d in rsd}
        missing = [d["product"] for d in usb if _model(d["product"]) not in seen]
        for name in missing:
            print(_c(f"  [{_c('불일치', RED)}] {name}: USB에는 붙어 있는데 SDK가 못 봅니다.", RED))
            print(_c("         -> 연결이 불안정합니다. 케이블/커넥터를 확인하세요.", RED))
            problems.append(f"{name} SDK 미검출")

    if args.stream is not None:
        print()
        print(_c(f"스트림 확인 ({args.stream:.0f}초, {args.fps}fps 요청)", DIM))
        if not rsd:
            print(_c("  대상 카메라가 없어 건너뜁니다.", YELLOW))
        for d in rsd:
            r = stream_test(d["serial"], args.stream, args.fps, args.width, args.height)
            if r["error"]:
                print(f"  [{_c('실패', RED)}] {d['serial']}: {r['error']}")
                if "Device or resource busy" in r["error"] or "busy" in r["error"].lower():
                    print(_c("         -> 다른 프로그램(수집 GUI 등)이 쓰는 중입니다.", YELLOW))
                problems.append(f"{d['serial']} 스트림 실패")
                continue
            mark = _c("OK ", GREEN) if r["ok"] else _c("불안정", RED)
            print(f"  [{mark}] {d['serial']}  {r['n']}프레임  실측 {r['fps']:.1f}fps")
            print(f"         프레임 간격 p95 {r['p95_gap_ms']:.0f}ms  최대 {r['max_gap_ms']:.0f}ms"
                  f"  500ms 초과 {r['stalls']}회")
            if not r["ok"]:
                problems.append(f"{d['serial']} 정지 {r['stalls']}회")
                print(_c("         -> 수집 중 'latest frame is too old'로 세션이 끊길 수 있습니다.",
                         RED))

    if args.stream is None:
        unknown.append("실제 프레임 수신")

    print()
    if problems:
        print(_c(f"문제 {len(problems)}건: " + ", ".join(problems), RED))
        return 1
    if unknown:
        # 확인하지 못한 것을 통과로 보고하지 않는다. 종료 코드를 나눠, 스크립트가
        # '문제 있음'과 '확인 못 함'을 구분해서 다룰 수 있게 한다.
        print(_c("확인한 범위에서는 문제 없음. 다만 확인하지 못한 항목이 있습니다: "
                 + ", ".join(unknown), YELLOW))
        if "실제 프레임 수신" in unknown:
            print(_c("  --stream 을 붙이면 실제 프레임까지 확인합니다 "
                     "(카메라가 비어 있어야 함).", DIM))
        return 2
    print(_c("모두 정상 — 링크 속도와 실제 프레임 수신 모두 확인했습니다.", GREEN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
