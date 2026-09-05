"""로봇 노드(launch_nodes.py) 프로세스를 띄우는 한 곳.

카메라 노드와 같은 이유로 마법사와 워크스페이스가 **같은 노드를 이어서
쓴다** (2026-09-05). 마법사의 데이터세트 버전 [확인] 은 로봇에게 관측을 한 번
물어봐야 하는데, 그러려면 노드가 떠 있어야 한다. 확인하려고 띄운 노드를
워크스페이스가 물려받지 않으면 창이 뜬 뒤 사용자가 노드를 또 띄우게 되고,
FCI 는 클라이언트 하나만 받으므로 뒤엣것이 실패한다.

노드는 pylibfranka-venv 에서 돈다 (GUI 의 lerobot-venv 가 아니다). 주소를
인자로 명시하는 이유는 ``SystemOps.on_start_node`` 에 적힌 것과 같다 -- 노드가
스테이션 파일을 자기가 다시 읽으면 GUI 가 붙을 곳과 조용히 어긋날 수 있다.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QProcess

from apps.workspace.constants import WT_ROOT

LAUNCH_NODES_SCRIPT = str(WT_ROOT / "scripts" / "launch" / "launch_nodes.py")

#: 1 이면 노드를 띄우지 않는다. 카메라 쪽 GELLO_NO_CAMERA_NODE 와 같은 계약 --
#: 인수 테스트는 로봇 없이 돌아야 한다. run_all.sh 가 켠다.
NO_NODE_ENV = "GELLO_NO_ROBOT_NODE"


def spawn_node(station, parent=None) -> "QProcess | None":
    """station 설정대로 로봇 노드를 띄운다. 억제 환경변수가 켜져 있으면 None.

    --die-with-parent: GUI 가 갑자기 죽으면 노드가 FCI 연결을 쥔 채 남아
    다음 실행이 노드를 못 띄운다. 커널이 대신 정리하게 한다.
    """
    if os.environ.get(NO_NODE_ENV) == "1":
        return None
    proc = QProcess(parent)
    proc.setProgram(station.node.python_path)
    proc.setArguments([
        LAUNCH_NODES_SCRIPT,
        "--robot", station.robot.kind,
        "--robot-ip", station.robot.ip,
        "--robot-port", str(station.node.port),
        "--hostname", station.node.host,
        "--die-with-parent",
    ])
    proc.setWorkingDirectory(str(WT_ROOT))
    proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    proc.start()
    return proc


def adopt_node(proc: QProcess, new_parent) -> None:
    """마법사가 띄운 노드를 창이 넘겨받는다 (camera_node_proc.adopt_node 와 동일)."""
    for sig in (proc.readyReadStandardOutput, proc.finished):
        try:
            sig.disconnect()
        except TypeError:
            pass          # 붙은 것이 없다
    proc.setParent(new_parent)
