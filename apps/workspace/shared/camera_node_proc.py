"""카메라 노드 프로세스를 띄우는 한 곳.

마법사와 워크스페이스가 **같은 노드를 이어서 쓴다** (2026-09-05). 마법사가
하드웨어 페이지에서 미리보기를 보여주려면 노드가 필요한데, 거기서 띄운 것을
워크스페이스가 물려받지 않으면 창이 뜨자마자 같은 카메라를 두 번 열려다
포트(6021)가 겹쳐 죽는다. 그래서 spec 문자열을 만드는 규칙과 프로세스를
띄우는 방법을 여기 하나로 두고, 양쪽이 이것만 쓴다.

인계는 같은 프로세스 안이라 QProcess 객체를 그대로 넘기면 된다 (런처가
collect_workspace.main() 을 부르는 구조). 넘길 때 부모와 시그널 연결만
새 주인에게 옮긴다 -- adopt_node() 참조.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QProcess

from apps.workspace.constants import WT_ROOT

#: 1 이면 노드를 띄우지 않는다. 인수 테스트는 "로봇·카메라 불필요"가 계약인데
#: 창 테스트는 CameraOps.refresh_cameras 를 stub 해서 그걸 지켜 왔다. 마법사가
#: 노드를 띄우게 되면서 그 우회로를 비켜가 실제 카메라를 열었다 (2026-09-05).
#: 새 호출부가 stub 을 잊어도 막히도록 여기 하나로 막는다. run_all.sh 가 켠다.
NO_NODE_ENV = "GELLO_NO_CAMERA_NODE"


def node_specs(serials) -> list[str]:
    """--cam 인자 목록 = 시리얼 그대로. 빈 값과 중복은 빠진다.

    노드는 역할을 모른다 (2026-09-05 3층 분리) -- 신원이 시리얼이라
    "역할만 바꿨는데 노드가 재시작"하는 일이 없다.
    """
    out: list[str] = []
    for serial in serials:
        if serial and serial not in out:
            out.append(serial)
    return out


def spec_key(specs: list[str]) -> str:
    """"이 노드가 지금 어떤 구성인가"를 나타내는 비교용 문자열."""
    return ",".join(specs)


def spawn_node(specs: list[str], parent=None) -> "QProcess | None":
    """카메라 노드를 띄운다. specs 가 비면 None (띄울 것이 없다).

    --die-with-parent: closeEvent 가 정리하지만 그건 정상 종료일 때뿐이다.
    GUI 가 갑자기 죽으면 노드가 카메라를 쥔 채 남아 다음 실행을 막는다.
    """
    if not specs or os.environ.get(NO_NODE_ENV) == "1":
        return None
    proc = QProcess(parent)
    proc.setProgram(sys.executable)
    proc.setArguments(["-m", "gello.comm.camera_node", "--die-with-parent"]
                      + [a for sp in specs for a in ("--cam", sp)])
    # 노드는 GUI 의 sys.path 를 물려받지 않는다 -- 저장소 루트에서 띄워야
    # `python -m gello.comm.camera_node` 가 gello 를 찾는다.
    proc.setWorkingDirectory(str(WT_ROOT))
    proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    proc.start()
    return proc


def adopt_node(proc: QProcess, new_parent) -> None:
    """마법사가 띄운 노드를 창이 넘겨받는다.

    옛 주인의 슬롯이 붙어 있으면 그 주인이 사라진 뒤 신호가 갈 곳이 없다.
    연결을 끊고 부모를 옮긴다 -- 새 주인이 자기 슬롯을 다시 연결한다.
    """
    for sig in (proc.readyReadStandardOutput, proc.finished):
        try:
            sig.disconnect()
        except TypeError:
            pass          # 붙은 것이 없다
    proc.setParent(new_parent)
