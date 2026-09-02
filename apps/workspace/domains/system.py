"""Machine-management operations for WorkspaceWindow: tuning, robot node,
leader servo protection, and camera checks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess

from apps.workspace.constants import CHECK_CAMERAS, RESET_PROTECTION, RUNME_SCRIPT
from gello.config.station import load_station
from gello.gui.i18n import tr

STATION = load_station()
PYLIBFRANKA_PYTHON = STATION.node.python_path
LAUNCH_NODES_SCRIPT = str(
    Path(__file__).resolve().parent.parent.parent / "scripts" / "launch" / "launch_nodes.py"
)


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


class SystemOps:
    """Tuning, robot node, and leader servo protection operations."""

    def __init__(self, win) -> None:
        self.win = win

    @staticmethod
    def check_tuning() -> list:
        """What scripts/runme.sh would change, read without touching anything.

        Both settings reset on reboot and on replugging the GELLO, and both
        are invisible until they bite: a 16 ms FTDI latency timer drops the
        Dynamixel sync-read from ~340 Hz to ~55 Hz, and the powersave governor
        produces the latency spikes that end an FR3 session with
        communication_constraints_violation.

        Checking here rather than just running the script means the pkexec
        password prompt only ever appears when something actually needs
        changing -- a prompt on every launch trains people to dismiss it.
        """
        issues = []
        ports = sorted(Path("/dev/serial/by-id").glob("*FTDI*")) \
            if Path("/dev/serial/by-id").is_dir() else []
        if not ports:
            issues.append(("gello", "GELLO(FTDI)를 찾지 못했습니다 -- USB 연결 확인"))
        else:
            tty = Path(os.path.realpath(ports[0])).name
            lat = Path(f"/sys/bus/usb-serial/devices/{tty}/latency_timer")
            try:
                value = lat.read_text().strip()
                if value != "1":
                    issues.append(("latency",
                                   f"FTDI latency_timer={value} (1이어야 함, {tty})"))
            except OSError:
                issues.append(("latency", f"{lat} 를 읽을 수 없습니다"))
        govs = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"))
        if govs:
            perf = sum(1 for g in govs if _read(g) == "performance")
            if perf != len(govs):
                issues.append(("governor",
                               f"CPU governor performance {perf}/{len(govs)} 코어"))
        return issues

    def startup_tuning(self) -> None:
        issues = self.check_tuning()
        if not issues:
            self.win.log("[튜닝] FTDI latency_timer=1, CPU governor=performance — 이미 적용됨.")
            return
        self.win.log("[튜닝] 조정이 필요합니다:")
        for _key, text in issues:
            self.win.log(f"  - {text}")
        if any(k == "gello" for k, _ in issues):
            # 케이블 문제는 pkexec로 해결되지 않는다. 스크립트를 띄워봐야
            # 비밀번호만 묻고 같은 경고를 낼 뿐이다.
            self.win.log("[튜닝] GELLO가 연결되면 Tools > 시스템 튜닝 실행 을 눌러주세요.")
            return
        self.win.log("[튜닝] scripts/runme.sh 를 실행합니다 (관리자 비밀번호 창이 뜹니다).")
        self.run_runme()

    def on_check_cameras(self) -> None:
        """Runs scripts/check/check_cameras.py into the Validation tab.

        No --stream: the previews (or a session) usually hold the cameras, and
        the link-speed half is exactly the part that stays readable anyway.
        """
        proc = QProcess(self.win)
        proc.setProgram(sys.executable)
        proc.setArguments([CHECK_CAMERAS])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: [self.win.log(ln, "validation")
                     for ln in self.win._proc_text(proc).splitlines()])
        proc.finished.connect(lambda c, _s: self.win.log(
            {0: "카메라 점검: 모두 정상", 1: "카메라 점검: 문제 발견",
             2: "카메라 점검: 일부 확인 못 함"}.get(c, f"카메라 점검 종료 (exit={c})"),
            "validation"))
        self.win._camera_check_process = proc
        self.win.bottom_tabs.setCurrentWidget(self.win.validation_view)
        self.win.log("=== 카메라 점검 ===", "validation")
        proc.start()

    def on_reset_leader_protection(self) -> None:
        """scripts/check/gello_reset_protection.py 실행 -- 과토크 보호모드 해제 (#37B).

        서보의 overload(0x20) 래치는 Reboot 으로만 풀린다. 스크립트가 리더암
        시리얼 포트를 직접 여므로, 세션(worker)이 포트를 잡고 있는 동안은
        실행하지 않는다 -- wall 스레드와 같은 버스를 두고 싸우는 경로 자체를
        막는다. 재부팅 후 재설정은 필요 없다: operating mode 는 EEPROM 이라
        살아남고, 다음 세션의 wall.start() 가 나머지를 다시 세팅한다.
        """
        p = self.win.procs.reset_protection_process
        if p is not None and p.state() != QProcess.ProcessState.NotRunning:
            self.win.log("[리더암] 보호 해제가 이미 실행 중입니다.")
            return
        if self.win.worker is not None:
            self.win.log("[리더암] 세션이 리더암 포트를 잡고 있어 실행할 수 없습니다 -- "
                     "Robot > 세션 종료 후 다시 시도하세요.")
            return
        proc = QProcess(self.win)
        proc.setProgram(sys.executable)
        proc.setArguments([RESET_PROTECTION])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: [self.win.log(f"[리더암] {ln}")
                     for ln in self.win._proc_text(proc).splitlines() if ln.strip()])
        proc.finished.connect(lambda c, _s: self.win.log(
            {0: "[리더암] 보호 해제 완료 -- 새 세션을 시작할 수 있습니다.",
             1: "[리더암] 일부 서보가 복구되지 않았습니다 -- 5V 전원을 껐다 켜고 "
                "관절이 물리적으로 걸려 있지 않은지 확인하세요.",
             2: "[리더암] 리더암 포트를 열지 못했습니다 -- 연결/전원을 확인하세요."}
            .get(c, f"[리더암] 보호 해제 종료 (exit={c})")))
        self.win.procs.reset_protection_process = proc
        self.win.log("=== 리더암 서보 보호 해제 ===")
        proc.start()

    def run_runme(self) -> None:
        # runme.sh 는 pkexec 로 관리자 비밀번호 창을 띄운다. 사람이 없는
        # 자리(인수 테스트, 밤샘 리팩토링 러너)에서 그 창이 뜨면 답할 사람이
        # 없어 그대로 멈춘다 -- 실제로 2026-09-01 에 테스트 실행 중 창이 떴다.
        # 시작 시 자동 실행이라 눌러야만 뜨는 것도 아니고, 튜닝이 어긋나
        # 있을 때만이라 기계 상태에 따라 떴다 안 떴다 한다.
        if os.environ.get("GELLO_NO_PRIVILEGED"):
            self.win.log("[튜닝] GELLO_NO_PRIVILEGED -- 관리자 권한 작업을 건너뜁니다. "
                     "필요하면 사람이 scripts/runme.sh 를 직접 실행하세요.")
            return
        if self.win.procs.runme_process is not None and \
                self.win.procs.runme_process.state() != QProcess.ProcessState.NotRunning:
            self.win.log("[튜닝] 이미 실행 중입니다.")
            return
        proc = QProcess(self.win)
        proc.setProgram("/usr/bin/env")
        proc.setArguments(["bash", RUNME_SCRIPT])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: [self.win.log(f"[튜닝] {ln}")
                     for ln in self.win._proc_text(proc).splitlines() if ln.strip()])
        proc.finished.connect(self.on_runme_finished)
        self.win.procs.runme_process = proc
        proc.start()

    def on_runme_finished(self, code: int, _status) -> None:
        left = self.check_tuning()
        if code == 0 and not left:
            self.win.log("[튜닝] 완료 — 모두 적용되었습니다.")
        else:
            self.win.log(f"[튜닝] 종료 (exit={code}). 남은 항목: "
                     + (", ".join(t for _k, t in left) if left else "없음"))
            if left:
                self.win.log("[튜닝] 취소했거나 실패했습니다. Tools > 시스템 튜닝 실행 으로 다시 할 수 있습니다.")
        self.win.procs.runme_process = None

    def on_start_node(self) -> None:
        if self.win.procs.node_process is not None and \
                self.win.procs.node_process.state() != QProcess.ProcessState.NotRunning:
            self.win.log("[노드] 이미 실행 중입니다.")
            return
        proc = QProcess(self.win)
        proc.setProgram(PYLIBFRANKA_PYTHON)
        # --die-with-parent: closeEvent가 노드를 정리하지만 그건 정상 종료일
        # 때뿐이다. GUI가 갑자기 죽으면 노드가 FCI 연결을 쥔 채 남아 다음 실행이
        # 노드를 못 띄운다. 커널이 대신 정리하게 한다.
        # 주소를 명시적으로 넘긴다. 노드는 다른 venv 에서 도는 별도
        # 프로세스라 스테이션 설정을 자기가 다시 읽는데, GELLO_STATION 이
        # 전달되지 않거나 그 사이 파일이 바뀌면 GUI 가 붙을 곳과 노드가 여는
        # 곳이 조용히 어긋난다. 여기서 넘기면 둘은 항상 같은 값을 본다.
        proc.setArguments([
            LAUNCH_NODES_SCRIPT,
            "--robot", STATION.robot.kind,
            "--robot-ip", STATION.robot.ip,
            "--robot-port", str(STATION.node.port),
            "--hostname", STATION.node.host,
            "--die-with-parent",
        ])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self.on_node_output)
        proc.finished.connect(self.on_node_finished)
        self.win.procs.node_process = proc
        self.win.log("[노드] 시작합니다...")
        # 인디케이터는 세션 중 장애 신호(node_status)로만 갱신되고 있어서,
        # 노드가 잘 떠 있어도 '노드 -' 로 남았다 (실화면에서 확인된 혼란).
        # GUI 가 켠 시점/종료 시점에도 갱신한다.
        self.win.lights["node"].set("busy", tr("시작 중"))
        self.win.right_fields["node"].setText(tr("시작 중"))
        proc.start()

    def on_node_finished(self, code: int, _status) -> None:
        self.win.log(f"[노드] 종료 (exit={code})")
        self.win.lights["node"].set("off", "-")
        self.win.right_fields["node"].setText("-")

    def on_node_output(self) -> None:
        if self.win.procs.node_process is None:
            return
        data = self.win._proc_text(self.win.procs.node_process)
        for line in data.splitlines():
            if line.strip():
                self.win.log(f"[노드] {line}")

    def on_stop_node(self) -> None:
        if self.win.procs.node_process is None or \
                self.win.procs.node_process.state() == QProcess.ProcessState.NotRunning:
            return
        self.win.procs.node_process.terminate()
        if not self.win.procs.node_process.waitForFinished(3000):
            self.win.procs.node_process.kill()
            self.win.procs.node_process.waitForFinished(2000)
        self.win.log("[노드] 종료했습니다.")
