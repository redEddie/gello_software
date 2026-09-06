"""노드가 죽었을 때 **왜** 죽었는지가 화면까지 오는가 (2026-09-06).

조작자 보고: "로그에서 reflex 종류가 안 보이더라구요. 그냥 노드가 죽은걸로
로그가 나왔어요."

반사의 이름은 libfranka 의 abort 메시지 안에만 있고, 화면까지 오려면 네 번의
경계를 무사히 건너야 한다:

    FR3 제어 스레드 (예외)
      -> franka_fr3._control_error          (print, 노드 프로세스의 stdout)
      -> get_observations 가 RuntimeError   (ZMQ 서버가 {"error": ...} 로)
      -> ZMQClientRobot 이 다시 raise
      -> CollectionWorker 의 except         (여기서 버려지고 있었다)
      -> log_message -> GUI Log 탭

세 곳이 새고 있었다: worker 가 예외를 안 받았고(`except (...):`), 노드가
버퍼링된 stdout 으로 돌았고, 종료가 정상/크래시 구분 없이 찍혔다.

문자열 규약도 함께 못박는다 -- 이 검사들이 없으면 한쪽만 고쳤을 때 조용히
영영 안 맞는다 (안내가 틀리거나, 노드 준비를 영영 못 알아본다).

로봇도 카메라도 필요 없다.
"""
import re
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

import zmq  # noqa: E402

from gello.collect.worker import (  # noqa: E402
    CONTROL_DEAD_MARK,
    _node_down_hint,
    _why,
)

REFLEX = ('libfranka: Move command aborted: motion aborted by reflex! '
          '["joint_velocity_violation"] control_command_success_rate: 0.87')

# ------------------------------------------------- 1. 반사 이름이 살아남는가
# ZMQ 경계를 건너온 모양 그대로 (robot_node.py 가 타입 이름을 문자열에 싣고
# 클라이언트가 그것으로 RuntimeError 를 만든다).
crossed = RuntimeError(
    f"RuntimeError: FR3 control loop is dead: ControlException: {REFLEX}")
why = _why(crossed)
assert "joint_velocity_violation" in why, why
assert "aborted by reflex" in why, why
# 타입 이름이 두 번 붙으면 정작 읽어야 할 뒤쪽이 밀린다.
assert why.count("RuntimeError:") == 1, why
print("1. 반사 이름이 로그 한 줄까지 살아남는다 OK")
print(f"   -> {why[:96]}...")

# 원인 체인은 잃지 않는다 (관절 한계 벽의 서보 에러 비트가 여기 있다).
try:
    try:
        raise ValueError("servo 3 error 0x20")
    except ValueError as cause:
        raise RuntimeError("joint-limit wall thread failed") from cause
except RuntimeError as e:
    chained = _why(e)
assert "0x20" in chained and "원인" in chained, chained
print("2. 원인 체인(서보 에러 비트)을 잃지 않는다 OK")

# 메시지가 없는 예외도 최소한 타입은 남긴다.
assert _why(RuntimeError()) == "RuntimeError"

# ------------------------------------------ 2. 두 사건을 다르게 안내하는가
dead = _node_down_hint(crossed)
gone = _node_down_hint(zmq.ZMQError(11))
assert "노드 재시작" in dead and "기다려도" in dead, dead
assert "자동으로" in gone, gone
assert dead != gone, "제어 루프 다운과 프로세스 무응답은 고치는 방법이 다르다"
print("3. 제어 루프 다운 / 프로세스 무응답을 다르게 안내 OK")

# ---------------------------------------- 3. 파일 사이의 문자열 규약 (조용한 파손)
fr3 = (WT / "gello/robots/franka_fr3.py").read_text(encoding="utf-8")
assert CONTROL_DEAD_MARK in fr3, (
    f"worker.CONTROL_DEAD_MARK({CONTROL_DEAD_MARK!r}) 가 franka_fr3.py 에 없다 -- "
    "한쪽만 바꾸면 제어 루프 다운을 알아보지 못하고 엉뚱한 안내를 한다")
assert 'flush=True' in fr3[fr3.index("CONTROL LOOP ABORTED") - 200:
                           fr3.index("CONTROL LOOP ABORTED") + 120], (
    "제어 루프 중단 메시지를 flush 없이 찍는다 -- 파이프로 넘어가면 버퍼에 "
    "갇힌 채 사라진다 (그 줄이 원인을 가진 유일한 것이다)")
print("4. CONTROL_DEAD_MARK 규약 + 중단 메시지 flush OK")

from apps.workspace.features.system.ops import NODE_READY_MARK  # noqa: E402

launch = (WT / "scripts/launch/launch_nodes.py").read_text(encoding="utf-8")
assert NODE_READY_MARK in launch, (
    f"NODE_READY_MARK({NODE_READY_MARK!r}) 를 launch_nodes.py 가 찍지 않는다 -- "
    "빠른 재개가 노드 준비를 영영 못 알아본다")
print("5. NODE_READY_MARK 규약 OK")

node_proc = (WT / "apps/workspace/shared/robot_node_proc.py").read_text(encoding="utf-8")
args = node_proc[node_proc.index("setArguments"):node_proc.index("setWorkingDirectory")]
assert re.search(r'"-u"', args), (
    "노드를 -u 없이 띄운다 -- stdout 이 블록 버퍼라 죽기 직전에 찍은 줄이 "
    "사라진다 (원인이 거기 있다)")
print("6. 노드를 버퍼 없이(-u) 띄운다 OK")

sysops = (WT / "apps/workspace/features/system/ops.py").read_text(encoding="utf-8")
assert "CrashExit" in sysops, (
    "노드 종료 로그가 정상 종료와 크래시를 구별하지 않는다")
print("7. 노드 종료 로그가 크래시를 구별한다 OK")

# --------------------------------------- 4. 신호가 이유를 나르는가 (배선 검사)
from gello.collect.worker import CollectionWorker  # noqa: E402

sig = CollectionWorker.node_status
worker_src = (WT / "gello/collect/worker.py").read_text(encoding="utf-8")
assert "node_status = pyqtSignal(bool, str)" in worker_src, (
    "node_status 가 이유를 나르지 않는다 -- 상태표시등이 '응답 없음' 이라고만 "
    "말하고 무엇 때문인지는 로그를 거슬러야 알 수 있게 된다")
assert "self.node_status.emit(False, _why(e))" in worker_src
# 예외를 실제로 받고 있는가 (`except (...):` 로 되돌아가면 이유가 다시 사라진다)
assert "except (zmq.ZMQError, RuntimeError) as e:" in worker_src, (
    "노드 실패 예외를 이름 없이 받는다 -- 그 예외가 반사 이름을 가진 유일한 것이다")
assert "[NODE DOWN] {_why(e)}" in worker_src
print("8. node_status 가 이유를 함께 나른다 OK")

from apps.workspace.features.collection.ops import CollectionOps  # noqa: E402

import inspect  # noqa: E402

params = list(inspect.signature(CollectionOps.on_node_status).parameters)
assert params[1:3] == ["ok", "why"], params
print("9. GUI 슬롯이 (ok, why) 두 인자를 받는다 OK")

print("\n노드 진단 인수 통과")
