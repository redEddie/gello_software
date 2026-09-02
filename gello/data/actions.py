"""Pure action-space computations for LIBERO-format demonstrations.

These functions have no side effects: they only map vectors to vectors.
Constants here are the robosuite OSC_POSE defaults (position/orientation
controllers), see robosuite.controllers.parts.arm.osc.OperationalSpaceController.
"""
from __future__ import annotations

import numpy as np

ACTION_POS_MAX = 0.05  # m per control step
ACTION_ROT_MAX = 0.5  # rad per control step (axis-angle vector component)


def _quat_to_axis_angle(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    """Scalar-last quaternion -> axis*angle (rad), matching franka_fr3's convention."""
    n = np.sqrt(qx * qx + qy * qy + qz * qz)
    if n < 1e-9:
        return np.zeros(3)
    angle = 2.0 * np.arctan2(n, qw)
    if angle > np.pi:
        angle -= 2 * np.pi
    axis = np.array([qx, qy, qz]) / n
    return axis * angle


def _quat_mul(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """Scalar-last (x, y, z, w) quaternion product q1 * q2."""
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return np.array(
        [
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        ]
    )


def _quat_conj(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    return np.array([-x, -y, -z, w])


def compute_delta_action(
    ee_pos_quat_curr: np.ndarray,
    ee_pos_quat_next: np.ndarray,
    gripper_closed: bool,
) -> np.ndarray:
    """World-frame delta pose from ``curr`` to ``next``, OSC_POSE-normalized.

    Args:
        ee_pos_quat_curr: (7,) [x, y, z, qx, qy, qz, qw] at frame t.
        ee_pos_quat_next: (7,) same layout at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (7,) float32, each component clipped to [-1, 1]:
        (dx, dy, dz, d_axis_angle_x, d_axis_angle_y, d_axis_angle_z, gripper).
    """
    p0, p1 = ee_pos_quat_curr[:3], ee_pos_quat_next[:3]
    q0, q1 = ee_pos_quat_curr[3:7], ee_pos_quat_next[3:7]

    dpos = (p1 - p0) / ACTION_POS_MAX
    # relative rotation in the world frame: q_rel = q1 * inverse(q0)
    q_rel = _quat_mul(q1, _quat_conj(q0))
    if q_rel[3] < 0:  # shortest-path: keep positive scalar part
        q_rel = -q_rel
    drot = _quat_to_axis_angle(*q_rel) / ACTION_ROT_MAX

    gripper = 1.0 if gripper_closed else -1.0
    action = np.concatenate([dpos, drot, [gripper]]).astype(np.float32)
    return np.clip(action, -1.0, 1.0)


def compute_joint_delta_action(
    q_curr: np.ndarray, q_next: np.ndarray, gripper_closed: bool
) -> np.ndarray:
    """Raw joint-space delta (rad) from ``curr`` to ``next``, unnormalized.

    Unlike :func:`compute_delta_action` there is no OSC_POSE-style external
    convention to match here -- GELLO already drives the follower in joint
    space, so this is just the realized per-step joint motion, stored as-is
    instead of clipped/normalized to [-1, 1].

    Args:
        q_curr: (7,) measured joint positions (rad) at frame t.
        q_next: (7,) same at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (8,) float32: (d_joint1..d_joint7, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    dq = (np.asarray(q_next, dtype=np.float64) - np.asarray(q_curr, dtype=np.float64)).astype(
        np.float32
    )
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([dq, [gripper]]).astype(np.float32)


def compute_ee_absolute_action(
    ee_pos_quat_next: np.ndarray, gripper_closed: bool
) -> np.ndarray:
    """Absolute world-frame EE pose at frame t+1 -- NOT a delta.

    Position in meters, orientation as axis-angle (rad) -- the same
    convention ``obs/ee_states`` already uses, NOT OSC_POSE's
    normalized-delta convention that :func:`compute_delta_action` matches
    (there is no controller-output-range to normalize against here).

    Args:
        ee_pos_quat_next: (7,) [x, y, z, qx, qy, qz, qw] at frame t+1.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (7,) float32: (x, y, z, ax, ay, az, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    pos = np.asarray(ee_pos_quat_next[:3], dtype=np.float64)
    axis_angle = _quat_to_axis_angle(*ee_pos_quat_next[3:7])
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([pos, axis_angle, [gripper]]).astype(np.float32)


def compute_joint_absolute_action(q_cmd: np.ndarray, gripper_closed: bool) -> np.ndarray:
    """The GELLO leader's absolute joint target (rad) at frame t -- NOT a delta.

    This is the ACT/ALOHA convention: observation is the follower's *measured*
    joints, action is the leader's *command*, and the force the operator is
    applying lives implicitly in the difference between them (Zhao et al.,
    "Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware").

    Do NOT substitute the follower's realized ``joint_states[t+1]`` here, even
    though it looks like the same quantity. The follower runs behind a
    critically-damped reference filter and trails the leader by ~4 ticks; the
    lead the leader needed to drag the arm forward (up to 0.28 rad measured) is
    absent from the realized trajectory. A policy trained on realized values
    can only ever emit "one tick past where the arm already is", so it cannot
    command a catch-up: its own tracking lag re-anchors at every replan, the
    target regresses behind the previous chunk's frontier, and the motion
    stutters backwards at the replan period while running 1.3-2.0x slow. See
    ``knu-physical-ai/fr3-action-space-case-study`` on the Hub for the
    measurements behind this.

    Args:
        q_cmd: (7,) GELLO leader commanded joint positions (rad) at frame t.
        gripper_closed: binary gripper *target* in effect at frame t.

    Returns:
        (8,) float32: (joint1..joint7, gripper), gripper -1=open/+1=close
        (matching compute_delta_action's sign convention).
    """
    q = np.asarray(q_cmd, dtype=np.float32)
    gripper = 1.0 if gripper_closed else -1.0
    return np.concatenate([q, [gripper]]).astype(np.float32)
