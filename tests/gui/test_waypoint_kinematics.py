"""waypoint 액션 변환 검증 — 서버와 byte-identical 인 쪽과 클라이언트가 쓰는 쪽.

``gello/robots/fr3_kinematics.py`` 는 mamba real_deploy 의 사본이고, 그쪽과
값이 어긋나면 정책이 학습한 것과 다른 목표를 로봇에 보낸다. 그런데 파일 안에
같은 변환이 두 벌 있다:

* ``ee_waypoint_step_to_joint`` -- 한 스텝. 서버 쪽과 byte-identical 을 위해 둔다.
* ``ee_chunk_to_joint_chunk``   -- 청크 전체. **클라이언트가 실제로 쓰는 쪽**
  (``fr3_policy_client --waypoint``).

둘이 갈라지면 조용히 틀린다 -- 어느 쪽도 예외를 내지 않고 로봇만 다르게
움직인다. 그래서 같은 입력에 같은 답을 내는지 본다.

로봇이 필요 없다 (순수 기구학).
"""
import sys
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)

from gello.robots.fr3_kinematics import (  # noqa: E402
    POS_MAX_WP,
    ROT_MAX_WP,
    ee_chunk_to_joint_chunk,
    ee_waypoint_step_to_joint,
    fk,
)

# --- 1) 학습과 맞춰 둔 프리스케일 상수 ------------------------------------
# waypoint_actions.py 의 값과 반드시 같아야 한다. 바뀌면 정책의 출력 범위가
# 로봇에서 다른 거리로 해석된다.
assert POS_MAX_WP == 0.30, POS_MAX_WP
assert ROT_MAX_WP == 1.5, ROT_MAX_WP
print("1 통과: 프리스케일 상수 POS 0.30 / ROT 1.5")

# --- 2) 스텝 변환과 청크 변환이 같은 답을 낸다 ----------------------------
rng = np.random.default_rng(0)
q = np.array([0.0, -0.4, 0.0, -2.0, 0.0, 1.6, 0.8])
for trial in range(5):
    w = np.r_[rng.uniform(-1, 1, 6), rng.uniform(-1, 1)]
    step = ee_waypoint_step_to_joint(w, q, q)
    chunk = ee_chunk_to_joint_chunk(w[None, :], q,
                                    pos_max=POS_MAX_WP, rot_max=ROT_MAX_WP)
    assert np.allclose(step, chunk[0], atol=1e-9), (trial, step - chunk[0])
print("2 통과: ee_waypoint_step_to_joint == ee_chunk_to_joint_chunk[0]")

# --- 3) 앵커가 스텝마다 움직이지 않는다 -----------------------------------
# waypoint 규약의 핵심. 같은 waypoint 를 K 번 넣으면 K 개 목표가 전부 같아야
# 한다 -- 앵커가 따라 움직이면(증분 방식) 점점 멀어진다.
w = np.array([0.2, -0.1, 0.15, 0.0, 0.05, 0.0, 1.0])
out = ee_chunk_to_joint_chunk(np.tile(w, (4, 1)), q,
                              pos_max=POS_MAX_WP, rot_max=ROT_MAX_WP)
assert np.allclose(out[0], out[1:], atol=1e-9), out - out[0]
print("3 통과: 같은 waypoint 를 반복해도 목표가 누적되지 않는다 (고정 앵커)")

# --- 4) 그리퍼 규약: 정책 -1..+1 -> 로봇 0..1 -----------------------------
for raw, want in ((-1.0, 0.0), (0.0, 0.5), (1.0, 1.0), (-3.0, 0.0), (3.0, 1.0)):
    w = np.array([0, 0, 0, 0, 0, 0, raw], dtype=float)
    got = ee_waypoint_step_to_joint(w, q, q)[7]
    assert abs(got - want) < 1e-9, (raw, got, want)
print("4 통과: 그리퍼 -1..+1 -> 0..1 (범위 밖은 잘림)")

# --- 5) 0 waypoint 는 제자리 ----------------------------------------------
# 위치·회전 성분이 0 이면 앵커 그대로여야 한다. 프리스케일이나 부호가 틀리면
# 여기서 어긋난다.
zero = np.zeros(7)
out0 = ee_waypoint_step_to_joint(zero, q, q)
assert np.allclose(fk(out0[:7])[:3, 3], fk(q)[:3, 3], atol=1e-4), \
    (fk(out0[:7])[:3, 3], fk(q)[:3, 3])
print("5 통과: 0 waypoint 는 앵커 위치를 유지한다")

print("\nwaypoint 기구학 검증 통과")
