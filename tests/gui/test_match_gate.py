"""정렬 힘 우물 검증 (issue #37A) -- 로봇 없이 가짜 드라이버로.

확인하는 계약:
  1. 목표가 설정돼도 리더를 아주 멀리 놓아 두면 토크도 전류도 0
     (= 리더암을 어디에 놓아두든 과토크가 걸리지 않는다).
  2. 손으로 가까이 가져다 놓으면 스스로 걸리고, 힘이 목표 근처에서 가장 세다.
  3. 힘은 오차에 따라 연속으로 풀린다 -- 케이블을 풀려고 한 관절을 크게
     돌리면 그 관절만 힘이 빠지고 나머지는 계속 자세를 지킨다.
  4. 목표 근처에서 캡에 붙어 버티면(걸림/붙잡힘) 정렬을 포기하고 래치되며,
     다시 거는 유일한 방법은 set_match_target 재호출이다.
"""
import sys
import time
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)

from gello.hw.dynamixel.driver import FakeDynamixelDriver  # noqa: E402
from gello.robots.joint_limit_wall import (  # noqa: E402
    IDLE_MIN_CURRENT,
    MATCH_GATE_RAD,
    JointLimitWall,
)

N_ARM = 7
LOWER = np.full(N_ARM, -4.0)
UPPER = np.full(N_ARM, 4.0)
TARGET = np.zeros(N_ARM)
WELL = MATCH_GATE_RAD / 2          # wall 의 기본 우물 폭
ARM_RAD = MATCH_GATE_RAD * 2       # 이 밖은 토크 오프


def _wall(driver, **kw):
    opts = dict(
        hz=200.0, health_every=0.0,      # 가짜 드라이버에는 health 레지스터가 없다
        match_max_current=np.full(N_ARM, 400.0),
        match_kp=np.full(N_ARM, 2800.0),  # 실기 J2/J4 와 같은 강성
    )
    opts.update(kw)
    return JointLimitWall(
        driver, LOWER, UPPER,
        offsets=np.zeros(N_ARM), signs=np.ones(N_ARM), n_arm=N_ARM,
        **opts,
    )


def _settle(wall, ticks=40):
    time.sleep(ticks / 200.0)
    wall.poll()          # 스레드가 죽었으면 여기서 터진다
    return wall.status()


def _put_leader_at(driver, q):
    driver._joint_angles = np.append(np.asarray(q, dtype=float), 0.0)


# ---------------------------------------------------------------- 1 & 2
drv = FakeDynamixelDriver(list(range(1, N_ARM + 2)))   # 팔 7 + 트리거 1
_put_leader_at(drv, np.full(N_ARM, ARM_RAD + 0.5))     # 목표에서 한참 멀리
w = _wall(drv)
w.start()
try:
    st = _settle(w)
    assert st["armed"] is False, "목표도 없는데 토크가 켜졌다"

    w.set_match_target(TARGET)
    st = _settle(w)
    assert st["match_engaged"] is False, "정렬 반경 밖인데 토크를 켰다"
    assert st["armed"] is False
    assert np.allclose(drv._currents[:N_ARM], 0.0), \
        f"정렬 반경 밖인데 전류가 흘렀다: {drv._currents[:N_ARM]}"
    print("1 통과: 멀리 놓아 두면 전류·토크 0 (팔은 자유)")

    # 손으로 우물 안까지 가져다 놓는다 -- 스스로 걸리고 전류가 붙는다
    _put_leader_at(drv, np.full(N_ARM, WELL))
    st = _settle(w)
    assert st["match_engaged"] is True, "우물 안인데 걸지 않았다"
    assert st["armed"] is True
    cur_at_well = float(np.abs(drv._currents[:N_ARM]).max())
    assert cur_at_well > 0.0, "우물 안인데 전류가 0"
    print(f"2 통과: 가까이 가져오면 스스로 engage (well 에서 {cur_at_well:.0f} mA)")

    # 힘 프로파일: 게이지 초록 경계 -> 그 두 배로 갈수록 힘이 빠진다
    _put_leader_at(drv, np.full(N_ARM, MATCH_GATE_RAD))
    cur_green = float(np.abs(_settle(w)["cur"]).max())
    _put_leader_at(drv, np.full(N_ARM, ARM_RAD - 0.05))
    cur_red = float(np.abs(_settle(w)["cur"]).max())
    # 계약 (2026-09-01): 봉우리 근처는 가진 힘 전부, 멀어질수록 약해지되
    # 걸려 있는 동안에는 IDLE_MIN_CURRENT 아래로 내려가지 않는다 -- 그래야
    # 오차가 큰 상태에서 시작한 정렬도 실제로 다가온다.
    assert cur_at_well >= cur_green >= cur_red, \
        f"힘이 커진다: well={cur_at_well:.0f} green={cur_green:.0f} red={cur_red:.0f}"
    assert cur_at_well > cur_red, "우물 모양이 사라졌다 (전 구간 평평)"
    assert cur_red >= IDLE_MIN_CURRENT - 1e-6, \
        f"걸린 관절인데 최소 당김({IDLE_MIN_CURRENT:.0f} mA) 아래다: {cur_red:.0f}"
    print(f"3 통과: 봉우리에서 가장 세고 멀수록 약해지며 하한 유지 "
          f"(well {cur_at_well:.0f} -> 게이지경계 {cur_green:.0f} -> "
          f"먼쪽 {cur_red:.0f} mA)")

    # 케이블 풀기: 한 관절만 크게 돌려도 그 관절만 힘이 빠지고
    # 나머지는 계속 버틴다 (조인트별 우물)
    q = np.full(N_ARM, 0.05)
    q[0] = np.pi                      # J1 을 반 바퀴 돌린 상태
    _put_leader_at(drv, q)
    st = _settle(w)
    cur = np.abs(np.asarray(st["cur"]))
    assert st["armed"] is True, "다른 관절이 가까운데 토크가 꺼졌다"
    assert cur[0] < 0.05 * cur[1:].max(), \
        f"돌린 관절이 아직 세게 당긴다: J1={cur[0]:.1f} 나머지={cur[1:].max():.1f}"
    # 힘만 0 이 아니라 토크 자체가 꺼져 있어야 정말 자유롭다
    # (전류제어 0 도 토크 오프보다 끌린다).
    mask = np.asarray(st["armed_mask"])
    assert mask[0] == False and mask[1:].all(), f"armed_mask={mask}"
    assert drv._ids[0] not in drv._torque_ids, "돌린 관절의 토크가 아직 켜져 있다"
    assert drv._ids[1] in drv._torque_ids, "나머지 관절의 토크가 꺼졌다"
    print(f"4 통과: 한 관절만 돌리면 그 관절만 토크 오프 "
          f"(J1 {cur[0]:.1f} mA·토크 off vs 나머지 {cur[1:].max():.0f} mA)")
finally:
    w.stop()

# ---------------------------------------------------------------- 5
from gello.gui.libero_gui_worker import GATE_RAD  # noqa: E402

assert GATE_RAD == MATCH_GATE_RAD, \
    "GUI 게이지 임계와 wall 우물 기준이 다르다 -- 보이는 것과 느낌이 어긋난다"
print("5 통과: GUI 게이지 임계 == wall 우물 기준")

# ---------------------------------------------------------------- 6
drv2 = FakeDynamixelDriver(list(range(1, N_ARM + 2)))
_put_leader_at(drv2, np.full(N_ARM, 0.05))    # 목표 바로 옆 = 우물 최대 세기
# 캡을 낮게 잡아, 걸린 팔이 즉시 캡에 붙는 상황을 만든다 (실기에서는
# 적분기가 감기며 같은 상태가 된다).
w2 = _wall(drv2, match_stall_s=0.3, match_max_current=np.full(N_ARM, 100.0))
w2.start()
try:
    w2.set_match_target(TARGET)
    st = _settle(w2, ticks=20)
    assert st["match_engaged"] is True
    # 가짜 드라이버는 전류를 줘도 안 움직인다 = 리더가 걸린 상황 그 자체.
    time.sleep(0.6)
    st = _settle(w2, ticks=10)
    assert st["match_blocked"] is True, "캡에 붙어 버텼는데 포기하지 않았다"
    assert st["match_engaged"] is False, "blocked 인데 engage 상태가 남았다"
    assert np.allclose(drv2._currents[:N_ARM], 0.0), "blocked 인데 전류가 남았다"
    print("6 통과: 목표 근처에서 포화 지속 -> 과부하 전에 정렬 포기(래치)")

    st = _settle(w2, ticks=20)
    assert st["match_blocked"] is True, "래치가 저절로 풀렸다"
    w2.set_match_target(TARGET)
    st = _settle(w2, ticks=5)
    assert st["match_blocked"] is False, "재요청했는데 래치가 안 풀렸다"
    print("7 통과: 래치는 set_match_target 재호출로만 해제")
finally:
    w2.stop()

# ---------------------------------------------------------------- 8
# 뒤집힘 구역 자동 해제는 2026-09-01 에 껐다 (원점이 한 바퀴 어긋나는 회귀).
# 판정 함수는 남아 있고 wall selftest 가 검사한다 -- 여기서는 "벽이 항상
# 서 있다" 는 현재 계약만 확인한다.
from gello.robots.franka_fr3 import FR3_Q_LOWER, FR3_Q_UPPER  # noqa: E402

LO7 = np.asarray(FR3_Q_LOWER, dtype=float)
HI7 = np.asarray(FR3_Q_UPPER, dtype=float)
CTR = (LO7 + HI7) / 2.0

drv3 = FakeDynamixelDriver(list(range(1, N_ARM + 2)))
q_spun = CTR.copy()
q_spun[0] = CTR[0] + np.pi          # J1 을 뒤집힘 지점에 둔다
_put_leader_at(drv3, q_spun)
w3 = JointLimitWall(
    drv3, LO7, HI7,
    offsets=np.zeros(N_ARM), signs=np.ones(N_ARM), n_arm=N_ARM,
    hz=200.0, health_every=0.0,
    match_max_current=np.full(N_ARM, 400.0),
    match_kp=np.full(N_ARM, 2800.0),
)
w3.start()
try:
    st = _settle(w3)
    assert not np.asarray(st["wrap_mask"]).any(), "자동 해제가 아직 살아 있다"
    assert st["match_aborted_wrap"] is False
    print("8 통과: 뒤집힘 구역 자동 해제 off -- 벽이 항상 선다")
finally:
    w3.stop()

print("\nmatch well 검증 통과")
