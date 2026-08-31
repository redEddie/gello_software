#!/home/franka/lerobot-venv/bin/python
"""Clear a Dynamixel torque-protection shutdown on the GELLO leader (issue #37B).

When a servo trips its overload protection (Hardware Error Status bit 0x20 --
typically after the leader is pulled hard against the match-assist spring), it
shuts torque down and latches the error bit. The only way to clear that latch
is a protocol-2.0 Reboot (instruction 0x08); power-cycling the whole supply
works too, but a reboot targets just the tripped servo and takes under a
second. Until it is cleared, the joint-limit wall's health check raises on the
0x20 bit and every new session dies on connect.

This script owns the serial port for its whole run, so it must NOT run while a
collection session (or anything else holding the leader's port) is up -- the
GUI menu entry enforces that; from a terminal, end the session first.

Reboot side effects, and why no reconfiguration step is needed here: the
operating mode lives in EEPROM and survives the reboot, while everything
volatile resets to power-on state -- torque off, goal current zero, error latch
cleared. That is exactly the state a fresh session expects: the wall's
``start()`` re-runs the whole mode/torque setup itself.

Usage:
    python scripts/gello_reset_protection.py            # reboot only errored servos
    python scripts/gello_reset_protection.py --all      # reboot every servo found
    python scripts/gello_reset_protection.py --ids 2 4  # reboot specific IDs
    python scripts/gello_reset_protection.py --dry-run  # report health, change nothing

Exit codes: 0 = every servo healthy (after any reboots), 1 = an error bit is
still set (or a rebooted servo never came back), 2 = could not open the port /
no servos answered.
"""

import argparse
import glob
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler  # noqa: E402

from gello.robots.joint_limit_wall import (  # noqa: E402
    ADDR_HW_ERROR,
    ADDR_INPUT_VOLTAGE,
    ADDR_TEMPERATURE,
)
from gello.station import load_station  # noqa: E402

ADDR_TORQUE_ENABLE = 64  # XL330; read back after reboot as a sanity check

# XL330 Hardware Error Status bits (control table addr 70).
_ERROR_BITS = {
    0x01: "input voltage",
    0x04: "overheating",
    0x08: "motor encoder",
    0x10: "electrical shock",
    0x20: "overload",
}


def _describe_error(e: int) -> str:
    if not e:
        return "ok"
    names = [n for bit, n in _ERROR_BITS.items() if e & bit]
    return f"0x{e:02x} ({', '.join(names) or 'unknown bit'})"


def _find_port() -> str:
    """Same resolution order the collector uses: station config, then the
    first FTDI adapter (see gello.lerobot_plugin._find_gello_port -- not
    imported here because that module pulls in all of lerobot)."""
    configured = load_station().leader.port
    if configured:
        return configured
    ports = glob.glob("/dev/serial/by-id/*FTDI*")
    if not ports:
        raise RuntimeError("no GELLO port found (/dev/serial/by-id/*FTDI*)")
    return ports[0]


def _read_health(pk, port, servo_id):
    """(hw_error, voltage V, temp C) -- None per field on a comm failure."""
    e, re_, _ = pk.read1ByteTxRx(port, servo_id, ADDR_HW_ERROR)
    v, rv, _ = pk.read2ByteTxRx(port, servo_id, ADDR_INPUT_VOLTAGE)
    t, rt, _ = pk.read1ByteTxRx(port, servo_id, ADDR_TEMPERATURE)
    return (
        e if re_ == COMM_SUCCESS else None,
        v / 10.0 if rv == COMM_SUCCESS else None,
        t if rt == COMM_SUCCESS else None,
    )


def _reboot_and_wait(pk, port, servo_id, timeout: float = 3.0) -> str:
    """Reboot one servo and wait for it to answer pings again.

    Returns "ok", or a short failure description. The servo drops off the bus
    for a few hundred ms during the reboot, so the ping is retried until
    ``timeout``.
    """
    comm, err = pk.reboot(port, servo_id)
    if comm != COMM_SUCCESS:
        return f"reboot command failed: {pk.getTxRxResult(comm)}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        _model, comm, _err = pk.ping(port, servo_id)
        if comm == COMM_SUCCESS:
            e, _, _ = _read_health(pk, port, servo_id)
            if e is None:
                return "came back but health read failed"
            if e:
                return f"error still latched after reboot: {_describe_error(e)}"
            trq, rc, _ = pk.read1ByteTxRx(port, servo_id, ADDR_TORQUE_ENABLE)
            if rc == COMM_SUCCESS and trq:
                # Never expected -- torque resets to off on power-on/reboot.
                return "rebooted but torque is unexpectedly ON"
            return "ok"
    return f"did not answer within {timeout:.0f}s after reboot"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default=None, help="serial port (default: station config or FTDI autodetect)")
    ap.add_argument("--baudrate", type=int, default=1000000, help="bus baudrate (driver default: 1000000)")
    ap.add_argument("--ids", type=int, nargs="+", default=None, help="reboot exactly these servo IDs")
    ap.add_argument("--all", action="store_true", help="reboot every servo found, errored or not")
    ap.add_argument("--dry-run", action="store_true", help="report health only, reboot nothing")
    args = ap.parse_args()

    try:
        port_name = args.port or _find_port()
    except RuntimeError as e:
        print(f"[reset] {e}")
        return 2

    port = PortHandler(port_name)
    if not port.openPort():
        print(f"[reset] cannot open {port_name} -- if a collection session is "
              "running it holds this port; end the session and retry")
        return 2
    try:
        port.setBaudRate(args.baudrate)
        pk = PacketHandler(2.0)

        found, comm = pk.broadcastPing(port)
        ids = sorted(found.keys())
        if not ids:
            print(f"[reset] no servos answered on {port_name} @ {args.baudrate} "
                  "-- check power and baudrate")
            return 2
        print(f"[reset] {port_name}: {len(ids)} servos found: {ids}")

        errored = []
        for sid in ids:
            e, v, t = _read_health(pk, port, sid)
            volt = f"{v:.1f}V" if v is not None else "?V"
            temp = f"{t}C" if t is not None else "?C"
            state = _describe_error(e) if e is not None else "health read failed"
            print(f"[reset]   ID{sid}: {state}  {volt} {temp}")
            if e:
                errored.append(sid)

        if args.dry_run:
            print("[reset] dry run -- nothing rebooted")
            return 1 if errored else 0

        if args.ids is not None:
            targets = [i for i in args.ids if i in ids]
            missing = sorted(set(args.ids) - set(ids))
            if missing:
                print(f"[reset] requested IDs not on the bus, skipped: {missing}")
        elif args.all:
            targets = ids
        else:
            targets = errored

        if not targets:
            print("[reset] all servos healthy -- nothing to do")
            return 0

        failed = []
        for sid in targets:
            print(f"[reset] rebooting ID{sid}...")
            result = _reboot_and_wait(pk, port, sid)
            print(f"[reset]   ID{sid}: {result}")
            if result != "ok":
                failed.append(sid)

        if failed:
            print(f"[reset] NOT recovered: {failed} -- if a reboot loop persists, "
                  "power-cycle the leader's 5V supply and check for a mechanical jam")
            return 1
        print(f"[reset] done -- {len(targets)} servo(s) rebooted, protection cleared; "
              "the leader is torque-off and ready for a new session")
        return 0
    finally:
        port.closePort()


if __name__ == "__main__":
    sys.exit(main())
