"""정렬 engage 게이트 검증 (issue #37A) -- 로봇 없이 가짜 드라이버로.

확인하는 계약:
  1. 목표가 설정돼도 리더가 게이트 밖이면 전류도 토크도 걸리지 않는다
     (= 리더암을 어디에 놓아두든 과토크가 걸리지 않는다).
  2. 사람이 손으로 게이트 안까지 가져다 놓으면 스스로 engage 한다.
  3. 게이트 임계는 GUI 자세 매칭 게이지의 임계와 같은 상수다.
  4. 어느 조인트가 캡에 붙어 몇 초씩 버티면(걸림/붙잡힘) 정렬을 포기하고
     래치되며, 다시 거는 유일한 방법은 set_match_target 재호출이다.
"""
import sys
import time
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)

from gello.hw.dynamixel.driver import FakeDynamixelDriver  # noqa: E402
from gello.robots.joint_limit_wall import (  # noqa: E402
    MATCH_GATE_RAD,
    JointLimitWall,
)

N_ARM = 7
LOWER = np.full(N_ARM, -2.5)
UPPER = np.full(N_ARM, 2.5)
TARGET = np.zeros(N_ARM)


def _wall(driver, **kw):
    return JointLimitWall(
        driver, LOWER, UPPER,
        offsets=np.zeros(N_ARM), signs=np.ones(N_ARM), n_arm=N_ARM,
        hz=200.0, health_every=0.0,      # 가짜 드라이버에는 health 레지스터가 없다
        match_max_current=np.full(N_ARM, 400.0),
        match_kp=np.full(N_ARM, 2800.0),  # 실기 J2/J4 와 같은 강성
        **kw,
    )


def _settle(driver, wall, ticks=40):
    """루프가 몇 틱 돌 시간을 준다 (200 Hz -> 5 ms/틱)."""
    time.sleep(ticks / 200.0)
    wall.poll()          # 스레드가 죽었으면 여기서 터진다
    return wall.status()


def _put_leader_at(driver, q):
    driver._joint_angles = np.append(np.asarray(q, dtype=float), 0.0)


# ---------------------------------------------------------------- 1 & 2
drv = FakeDynamixelDriver(list(range(1, N_ARM + 2)))   # 팔 7 + 트리거 1
_put_leader_at(drv, np.full(N_ARM, 1.2))               # 목표에서 한참 멀리
w = _wall(drv)
w.start()
try:
    st = _settle(drv, w)
    assert st["armed"] is False, "게이트 밖인데 토크가 켜졌다"
    assert not drv._torque_ids & set(drv._ids[:N_ARM]), "팔 서보에 토크가 걸렸다"

    w.set_match_target(TARGET)
    st = _settle(drv, w)
    assert st["match_engaged"] is False, "게이트 밖인데 engage 했다"
    assert st["armed"] is False, "게이트 밖인데 팔에 토크가 켜졌다"
    assert np.allclose(drv._currents[:N_ARM], 0.0), \
        f"게이트 밖인데 전류가 흘렀다: {drv._currents[:N_ARM]}"
    assert st["match_error"] is not None and st["match_error"] > MATCH_GATE_RAD
    print("1 통과: 목표가 설정돼도 게이트 밖이면 전류·토크 0")

    # 사람이 손으로 게이트 안까지 가져다 놓는다
    _put_leader_at(drv, np.full(N_ARM, MATCH_GATE_RAD - 0.05))
    st = _settle(drv, w)
    assert st["match_engaged"] is True, "게이트 안인데 engage 하지 않았다"
    assert st["armed"] is True, "engage 했는데 토크가 꺼져 있다"
    assert np.abs(drv._currents[:N_ARM]).max() > 0.0, "engage 했는데 전류가 0"
    print("2 통과: 게이트 안으로 들어오면 스스로 engage")

    # 히스테리시스: 조금 벗어나도 유지, 많이 벗어나면 풀린다
    _put_leader_at(drv, np.full(N_ARM, MATCH_GATE_RAD + 0.1))
    assert _settle(drv, w)["match_engaged"] is True, "히스테리시스 안인데 풀렸다"
    _put_leader_at(drv, np.full(N_ARM, MATCH_GATE_RAD + 0.5))
    st = _settle(drv, w)
    assert st["match_engaged"] is False, "크게 벗어났는데 계속 걸려 있다"
    assert np.allclose(drv._currents[:N_ARM], 0.0), "풀렸는데 전류가 남았다"
    print("3 통과: 히스테리시스 (조금 벗어남=유지, 크게 벗어남=해제)")
finally:
    w.stop()

# ---------------------------------------------------------------- 3
assert MATCH_GATE_RAD == 0.5
from gello.gui.libero_gui_worker import GATE_RAD  # noqa: E402

assert GATE_RAD == MATCH_GATE_RAD, \
    "GUI 게이지 임계와 wall engage 임계가 다르다 -- 초록인데 안 당기거나 그 반대"
print("4 통과: GUI 게이지 임계 == wall engage 임계")

# ---------------------------------------------------------------- 4
drv2 = FakeDynamixelDriver(list(range(1, N_ARM + 2)))
_put_leader_at(drv2, np.full(N_ARM, 0.3))     # 게이트 안 -- 바로 engage
w2 = _wall(drv2, match_stall_s=0.3)           # 짧게 잡아 테스트를 빨리
w2.start()
try:
    w2.set_match_target(TARGET)
    st = _settle(drv2, w2, ticks=20)
    assert st["match_engaged"] is True
    # 가짜 드라이버는 전류를 줘도 안 움직인다 = 리더가 걸린 상황 그 자체.
    # kp*max_lead = 2800*0.35 = 980 mA 요구 -> 400 mA 캡에 붙어 버틴다.
    time.sleep(0.6)
    st = _settle(drv2, w2, ticks=10)
    assert st["match_blocked"] is True, "캡에 붙어 버텼는데 포기하지 않았다"
    assert st["match_engaged"] is False, "blocked 인데 engage 상태가 남았다"
    assert np.allclose(drv2._currents[:N_ARM], 0.0), "blocked 인데 전류가 남았다"
    print("5 통과: 포화 지속 -> 과부하 전에 정렬 포기(래치)")

    # 래치는 다시 요청해야만 풀린다
    st = _settle(drv2, w2, ticks=20)
    assert st["match_blocked"] is True, "래치가 저절로 풀렸다"
    w2.set_match_target(TARGET)
    st = _settle(drv2, w2, ticks=5)
    assert st["match_blocked"] is False, "재요청했는데 래치가 안 풀렸다"
    print("6 통과: 래치는 set_match_target 재호출로만 해제")
finally:
    w2.stop()

print("\nmatch gate 검증 통과")
