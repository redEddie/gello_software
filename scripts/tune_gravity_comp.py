"""Interactively tune the GELLO leader's empirical gravity comp -- no robot,
no ZMQ, just the leader (issue #3's "③"/"④").

Same wall the teleop agent runs (gello.robots.joint_limit_wall.JointLimitWall),
driven standalone like scripts/gello_joint_limit_wall.py does for the limit
spring/trigger. This is NOT the FACTR/RNEA approach
(gello/factr/gravity_compensation.py) -- that needs a URDF (link mass/inertia)
of this leader, which doesn't exist and there's no CAD to build one from.
Instead each arm joint gets an independent, empirical single-pendulum
approximation:

    tau_g_i = gravity_gains[i] * sin(q_i - gravity_offsets[i])

Ignores cross-coupling between joints (a real chain's gravity load on joint i
also depends on joints i+1..n's angles; this doesn't) -- but needs only two
numbers per joint, tuned live by feel instead of measured, and reuses the
exact current-mode machinery already validated for the limit wall/pose-match
assist. See gello/robots/joint_limit_wall.py's module docstring for the full
rationale.

    ./scripts/runme.sh                     # servos must be at 1 Mbps
    python scripts/tune_gravity_comp.py

Tuning workflow: hold the leader, pick a joint that visibly sags (number
key), raise its gain a little at a time while moving through its range --
too little and it still droops, too much and it actively drives the joint
instead of just supporting it. If a joint feels right at one end of its
range but wrong at the other, nudge --offset (it shifts *where* sin(q-offset)
is zero) rather than fighting it with more gain. `p` prints the current
arrays formatted to paste straight into gello/agents/gello_agent.py's
PORT_CONFIG_MAP entry for this port; they're also printed on quit.

Keys:
    1-7         select joint to tune (highlighted)
    + / -       selected joint's gain +/- 10 mA
    ] / [       selected joint's offset +/- 0.05 rad
    c           capture: set the selected joint's offset to its CURRENT q
                (see "finding a joint's offset" below -- much faster than
                nudging with [/] from a blind guess of 0)
    r           reverse: negate the selected joint's gain (quick A/B test
                for which sign actually counteracts gravity -- see below)
    0           zero the selected joint's gain
    f / g       global stiction_gain +/- 0.02 (dither -- see class docstring;
                meaningless while every gain is still zero)
    p           print the current arrays now
    q / Ctrl+C  quit (leader released, torque dropped)

Finding a joint's offset (this is usually the actual blocker, not the gain):
sin(q - offset) is 0 exactly AT q=offset and swings through its full +/-1
range over the next +/-90deg -- so if offset is left at the default 0 while
the joint's true zero-gravity-torque pose is nowhere near q=0, the term
barely varies (or doesn't change sign at all) over the range you actually
move through, which LOOKS like "it's not posture-dependent" even though the
formula is -- it's just evaluating a nearly-flat stretch of sin() the whole
time. Two ways to find the real offset, gain still at/near 0 so it isn't
fighting you:
  1. Move the joint to where it feels most neutral (least self-motion) and
     press `c`.
  2. Or move it to each extreme where it visibly sags fastest in opposite
     directions, note both q's from the display, and set offset to their
     midpoint with [ / ] -- that midpoint is the zero-crossing regardless of
     how "neutral" any single pose feels (stiction can make several poses
     falsely feel neutral).
Once the offset is roughly right, sin(q-offset) SHOULD flip sign as you move
the joint through it and track which way gravity is actually pulling. If it
still visibly doesn't (see script docstring's opening paragraphs), that's
a real bug report, not a tuning problem -- please say so.

If a joint pushes the wrong way (fighting you instead of holding you up, or
seemingly reversed) at a *good* offset, don't fight it with more gain --
press `r` to flip that joint's gain sign instead: the raw servo's current
polarity vs. this leader's mechanical assembly determines which sign of
gain is physically "supporting", and there's no way to know it in advance
without the real hardware.

SAFETY: start with all gains at 0 (the default) and bring one joint up
gradually while holding the leader -- a gain too high for a joint's real
weight actively drives it rather than just supporting it. The Dynamixel port
is exclusive -- stop teleop first.
"""

import glob
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass
from typing import Optional

import numpy as np
import tyro
from termcolor import colored

from gello.agents.gello_agent import PORT_CONFIG_MAP
from gello.dynamixel.driver import DynamixelDriver
from gello.robots.franka_fr3 import GRIPPER_CLOSE_AT
from gello.robots.joint_limit_wall import JointLimitWall

GAIN_STEP = 10.0
OFFSET_STEP = 0.05
STICTION_STEP = 0.02


@dataclass
class Args:
    gello_port: Optional[str] = None
    grip: str = "right"
    """Which hand holds the GELLO handle -- match what you teleop with."""
    gravity_gains: str = "0,0,0,0,0,0,0"
    """Comma-separated starting per-joint gains (mA), J1..J7."""
    gravity_offsets: str = "0,0,0,0,0,0,0"
    """Comma-separated starting per-joint offsets (rad), J1..J7."""
    stiction_gain: float = 0.0
    hz: float = 20.0
    """Display/keyboard-poll rate -- independent of the wall's own 300 Hz loop."""


class KeyPoller:
    """cbreak-mode raw stdin poll -- returns the raw character typed, or None.

    Unlike record_dataset.py's KeyPoller (which only distinguishes space/esc/
    q), this tool needs arbitrary single characters (1-7, +-[]0fgpq).
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

    def poll(self) -> Optional[str]:
        if self._old is None:
            return None
        key = None
        while select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)  # last key wins if several arrived at once
        return key


def _parse_floats(s: str, n: int) -> np.ndarray:
    vals = [float(x) for x in s.split(",")]
    if len(vals) != n:
        raise ValueError(f"expected {n} comma-separated values, got {len(vals)}: {s!r}")
    return np.array(vals)


def _fmt_arrays(gains: np.ndarray, offsets: np.ndarray, stiction_gain: float) -> str:
    g = ", ".join(f"{v:.1f}" for v in gains)
    o = ", ".join(f"{v:.3f}" for v in offsets)
    return (
        f"        gravity_gains=({g}),\n"
        f"        gravity_offsets=({o}),\n"
        f"        stiction_gain={stiction_gain:.3f},"
    )


def main(args: Args) -> None:
    port = args.gello_port
    if port is None:
        ports = glob.glob("/dev/serial/by-id/*")
        if not ports:
            raise RuntimeError("No GELLO found, please specify --gello-port")
        port = ports[0]
    config = PORT_CONFIG_MAP.get(port)
    if config is None:
        raise RuntimeError(f"Port {port} not in PORT_CONFIG_MAP")
    config = config.with_grip(args.grip)
    if config.joint_limits is None:
        raise RuntimeError(f"Port {port} has no joint_limits; no wall to drive gravity comp from")

    n_arm = len(config.joint_ids)
    gains = _parse_floats(args.gravity_gains, n_arm)
    offsets = _parse_floats(args.gravity_offsets, n_arm)
    stiction_gain = args.stiction_gain
    selected = 0

    ids = list(config.joint_ids) + [config.gripper_config[0]]
    driver = DynamixelDriver(
        ids, servo_types=list(config.servo_types) if config.servo_types else None, port=port
    )

    wall = JointLimitWall(
        driver,
        *config.joint_limits,
        offsets=np.array(config.joint_offsets),
        signs=np.array(config.joint_signs),
        n_arm=n_arm,
        gripper_open_close=(
            config.gripper_config[1] * np.pi / 180,
            config.gripper_config[2] * np.pi / 180,
        ),
        trigger_start=GRIPPER_CLOSE_AT,
        gravity_gains=gains,
        gravity_offsets=offsets,
        stiction_gain=stiction_gain,
    )
    wall.start()
    print(colored("리더를 잡은 상태에서 처지는 조인트를 골라 게인을 천천히 올려보세요.", "cyan"))
    print(colored("Ctrl+C 또는 q로 종료 -- 최종 값을 PORT_CONFIG_MAP 형식으로 출력합니다.\n", "cyan"))

    dt = 1.0 / args.hz
    n_lines = 0
    try:
        with KeyPoller() as keys:
            while True:
                t0 = time.time()
                wall.poll()  # re-raises a wall-thread fault here
                key = keys.poll()
                st_before = wall.status()  # for `c` -- current q, before this tick's changes
                changed = False
                if key:
                    if key in "1234567"[:n_arm]:
                        selected = int(key) - 1
                    elif key in ("+", "="):
                        gains[selected] += GAIN_STEP
                        changed = True
                    elif key == "-":
                        gains[selected] -= GAIN_STEP
                        changed = True
                    elif key == "0":
                        gains[selected] = 0.0
                        changed = True
                    elif key == "]":
                        offsets[selected] += OFFSET_STEP
                        changed = True
                    elif key == "[":
                        offsets[selected] -= OFFSET_STEP
                        changed = True
                    elif key == "c":
                        q_now = st_before.get("q")
                        if q_now is not None:
                            offsets[selected] = float(q_now[selected])
                            changed = True
                    elif key == "r":
                        gains[selected] = -gains[selected]
                        changed = True
                    elif key == "f":
                        stiction_gain = min(1.0, stiction_gain + STICTION_STEP)
                        changed = True
                    elif key == "g":
                        stiction_gain = max(0.0, stiction_gain - STICTION_STEP)
                        changed = True
                    elif key == "p":
                        print("\n" + _fmt_arrays(gains, offsets, stiction_gain) + "\n")
                        n_lines = 0  # next redraw starts fresh below the printout
                    elif key in ("q", "Q"):
                        break
                if changed:
                    wall.set_gravity_comp(gains=gains, offsets=offsets, stiction_gain=stiction_gain)

                st = wall.status()
                q = st.get("q")
                lines = [
                    f"  torque {'ARMED  ' if st['armed'] else 'off    '}"
                    f"loop {st['hz']:5.1f} Hz   stiction_gain {stiction_gain:.3f}"
                ]
                if q is not None:
                    tau_g, dq = st["tau_g"], st["dq"]
                    for i in range(n_arm):
                        tag = colored(f"J{i + 1}", "yellow", attrs=["bold"]) if i == selected else f"J{i + 1}"
                        lines.append(
                            f"  {tag}  q {q[i]:+6.3f}  dq {dq[i]:+6.3f}  "
                            f"gain {gains[i]:+7.1f}  offset {offsets[i]:+6.3f}  "
                            f"tau_g {tau_g[i]:+7.1f} mA"
                        )
                lines.append(
                    "  1-7 select | +/- gain | [/] offset | c capture offset | r reverse gain | "
                    "0 zero | f/g stiction | p print | q quit"
                )
                out = "\n".join("\x1b[K" + ln for ln in lines)
                print(f"\x1b[{n_lines}F{out}" if n_lines else out)
                n_lines = len(lines)

                rest = dt - (time.time() - t0)
                if rest > 0:
                    time.sleep(rest)
    except KeyboardInterrupt:
        pass
    finally:
        wall.stop()
        driver.close()

    print("\n" + colored("최종 값 (PORT_CONFIG_MAP에 붙여넣기):", "cyan"))
    print(_fmt_arrays(gains, offsets, stiction_gain))


if __name__ == "__main__":
    main(tyro.cli(Args))
