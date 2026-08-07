"""FR3 pre-flight check -- run this BEFORE any GELLO teleop.

It performs *no motion*.  It:
  1. Checks real-time readiness (memlock, CPU governor) -- see
     ~/pylibfranka-setup.md section 7-2.
  2. Connects to the robot over FCI and reads joint state / mode / errors.
  3. Reports control_command_success_rate.

Run inside the pylibfranka venv, from OUTSIDE ~/libfranka-0.17.0:

    source ~/pylibfranka-venv/bin/activate
    cd ~/gello_software
    python scripts/fr3_preflight.py --robot-ip 172.16.0.2
"""

import resource
import sys
from dataclasses import dataclass
from pathlib import Path

import tyro

# 리포 루트에서 실행하지 않아도 gello.station 을 찾게 한다 -- 이 스크립트는
# 어느 디렉터리에서든 돌 수 있어야 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gello.station import load_station  # noqa: E402


@dataclass
class Args:
    # 기본값은 스테이션 설정에서 (configs/stations/<이름>.yaml)
    robot_ip: str = load_station().robot.ip


def check_realtime() -> bool:
    ok = True
    print("=== Real-time readiness (see pylibfranka-setup.md 7-2) ===")

    # memlock limit: mlockall() needs this effectively unlimited for a 1 kHz loop.
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    unlimited = resource.RLIM_INFINITY
    if soft == unlimited:
        print("  [OK]   memlock: unlimited")
    else:
        mb = soft / (1024 * 1024)
        print(f"  [WARN] memlock soft limit = {mb:.0f} MB (want: unlimited).")
        print("         mlockall() may fail -> page faults in the RT loop.")
        print("         Fix: set '@realtime - memlock unlimited' and re-login.")
        ok = False

    # CPU governor.
    gov_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if gov_path.exists():
        gov = gov_path.read_text().strip()
        if gov == "performance":
            print("  [OK]   cpu governor: performance")
        else:
            print(f"  [WARN] cpu governor = {gov} (want: performance).")
            print("         Fix: sudo cpupower frequency-set -g performance")
            ok = False
    else:
        print("  [??]   cpu governor: could not read")

    # PREEMPT_RT kernel.
    ver = Path("/proc/version").read_text()
    if "PREEMPT_RT" in ver or "-rt" in ver:
        print("  [OK]   kernel appears to be PREEMPT_RT")
    else:
        print("  [WARN] kernel does not look like PREEMPT_RT")
        ok = False

    return ok


def check_robot(robot_ip: str) -> bool:
    print(f"\n=== Robot connection ({robot_ip}) -- NO motion ===")
    import pylibfranka as pf

    robot = pf.Robot(robot_ip)  # kEnforce by default; connect only
    st = robot.read_once()

    print(f"  q            = {[round(x, 3) for x in st.q]}")
    print(f"  robot_mode   = {st.robot_mode}")
    print(f"  success_rate = {st.control_command_success_rate}")

    active = [
        n
        for n in dir(st.current_errors)
        if not n.startswith("_") and getattr(st.current_errors, n) is True
    ]
    print(f"  errors       = {active or 'none'}")

    ok = True
    if str(st.robot_mode).endswith("UserStopped"):
        print("  [WARN] robot is UserStopped -> release the physical e-stop button.")
        ok = False
    if str(st.robot_mode).endswith("Reflex"):
        print("  [WARN] robot in Reflex -> run robot.automatic_error_recovery().")
        ok = False
    if active:
        print("  [WARN] active errors present; clear them in Desk before teleop.")
        ok = False
    return ok


def main(args: Args) -> None:
    rt_ok = check_realtime()
    robot_ok = check_robot(args.robot_ip)

    print("\n=== Summary ===")
    print(f"  real-time ready : {'YES' if rt_ok else 'NO (fix warnings above)'}")
    print(f"  robot ready     : {'YES' if robot_ok else 'NO (see warnings above)'}")
    if rt_ok and robot_ok:
        print("  -> Safe to proceed to a low-velocity teleop test.")
    else:
        print("  -> Resolve the warnings before commanding motion.")


if __name__ == "__main__":
    main(tyro.cli(Args))
