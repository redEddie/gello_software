"""FR3 kinematics for EE-delta policy deployment: FK (Craig MDH) + damped-LS IK.

The EE-frame delta policy outputs normalized deltas (0.05 m / 0.5 rad, LIBERO
actions_ee convention). The server integrates them into target EE poses and
solves IK back to joint targets, so the robot client keeps receiving absolute
joint commands (protocol unchanged).

flange->EE transform X: constant for a given gripper mount / O_T_EE config.
Loaded from fr3_flange_to_ee.npy, calibrated offline from the training HDF5
(FK(q) vs recorded ee_pos_quat; residual was 0.00 mm — libfranka's own model).
Re-run scripts/datasets_fr3/calib_flange_to_ee.py if the EE config changes.
"""

from pathlib import Path

import numpy as np

_MDH = np.array([
    [0.0,      0.333,  0.0],
    [0.0,      0.0,   -np.pi / 2],
    [0.0,      0.316,  np.pi / 2],
    [0.0825,   0.0,    np.pi / 2],
    [-0.0825,  0.384, -np.pi / 2],
    [0.0,      0.0,    np.pi / 2],
    [0.088,    0.0,    np.pi / 2],
    [0.0,      0.107,  0.0],
])

# FR3 joint position limits (rad), Franka datasheet
FR3_Q_MIN = np.array([-2.7437, -1.7837, -2.9007, -3.0421, -2.8065, 0.5445, -3.0159])
FR3_Q_MAX = np.array([2.7437, 1.7837, 2.9007, -0.1518, 2.8065, 4.5169, 3.0159])

X_EE = np.load(Path(__file__).parent / "fr3_flange_to_ee.npy")  # (4,4)


def fk(q7: np.ndarray) -> np.ndarray:
    """q (7,) -> EE pose (4,4) in base frame (includes flange->EE)."""
    T = np.eye(4)
    theta = np.append(np.asarray(q7, dtype=np.float64), 0.0)
    for i in range(8):
        a, d, alpha = _MDH[i]
        ca, sa = np.cos(alpha), np.sin(alpha)
        ct, st = np.cos(theta[i]), np.sin(theta[i])
        Ti = np.array([
            [ct, -st, 0.0, a],
            [st * ca, ct * ca, -sa, -d * sa],
            [st * sa, ct * sa, ca, d * ca],
            [0.0, 0.0, 0.0, 1.0],
        ])
        T = T @ Ti
    return T @ X_EE


def _rot_to_axis_angle(R: np.ndarray) -> np.ndarray:
    tr = np.clip((R[0, 0] + R[1, 1] + R[2, 2] - 1.0) / 2.0, -1.0, 1.0)
    angle = np.arccos(tr)
    if angle < 1e-9:
        return np.zeros(3)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if angle > np.pi - 1e-4:
        M = (R + np.eye(3)) / 2.0
        d = np.array([M[0, 0], M[1, 1], M[2, 2]])
        k = int(d.argmax())
        ax = np.sqrt(max(d[k], 0.0))
        col = M[:, k] / (ax if ax > 1e-9 else 1.0)
        if (col * v).sum() < 0:
            col = -col
        return col * angle
    return v / (2.0 * np.sin(angle)) * angle


def axis_angle_to_rot(aa: np.ndarray) -> np.ndarray:
    theta = float(np.linalg.norm(aa))
    if theta < 1e-9:
        return np.eye(3)
    k = aa / theta
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * (K @ K)


def _fk_with_frames(q7: np.ndarray):
    """EE pose (4,4) + 각 관절의 (z축, 원점) — 해석적 자코비안용."""
    T = np.eye(4)
    theta = np.append(np.asarray(q7, dtype=np.float64), 0.0)
    axes, origins = [], []
    for i in range(8):
        a, d, alpha = _MDH[i]
        ca, sa = np.cos(alpha), np.sin(alpha)
        ct, st = np.cos(theta[i]), np.sin(theta[i])
        Ti = np.array([
            [ct, -st, 0.0, a],
            [st * ca, ct * ca, -sa, -d * sa],
            [st * sa, ct * sa, ca, d * ca],
            [0.0, 0.0, 0.0, 1.0],
        ])
        if i < 7:  # revolute joint i+1의 회전축 = frame i의 z (Craig: joint축 = 자기 frame z)
            T_pre = T @ Ti
            axes.append(T_pre[:3, 2].copy())
            origins.append(T_pre[:3, 3].copy())
            T = T_pre
        else:
            T = T @ Ti
    return T @ X_EE, axes, origins


def _jacobian_analytic(q: np.ndarray):
    """기하 자코비안 (6x7): J_col_i = [z_i x (p_ee - p_i); z_i]. FK 1회 비용."""
    T_ee, axes, origins = _fk_with_frames(q)
    p_ee = T_ee[:3, 3]
    J = np.zeros((6, 7))
    for i in range(7):
        J[:3, i] = np.cross(axes[i], p_ee - origins[i])
        J[3:, i] = axes[i]
    return J, T_ee


def ik(target: np.ndarray, q_seed: np.ndarray, iters: int = 30,
       damping: float = 1e-4, tol: float = 1e-5) -> np.ndarray:
    """Damped-LS IK. Warm-started from q_seed — the redundant DOF stays near
    the seed configuration (matches demo-like postures when seeded from the
    measured/previous joint solution)."""
    q = np.asarray(q_seed, dtype=np.float64).copy()
    for _ in range(iters):
        J, T = _jacobian_analytic(q)
        ep = target[:3, 3] - T[:3, 3]
        eR = _rot_to_axis_angle(target[:3, :3] @ T[:3, :3].T)
        e = np.concatenate([ep, eR])
        if np.linalg.norm(e) < tol:
            break
        dq = J.T @ np.linalg.solve(J @ J.T + damping * np.eye(6), e)
        q = np.clip(q + dq, FR3_Q_MIN, FR3_Q_MAX)
    return q


def ee_step_to_joint(delta7: np.ndarray, q_meas: np.ndarray,
                     pos_max: float = 0.05, rot_max: float = 0.5) -> np.ndarray:
    """One normalized EE-frame delta (7,) + measured joints (7,) -> joint target (8,).

    Per-step re-anchor: the delta is applied from the CURRENT measured EE pose,
    matching the training label exactly (build_ee_variant_from_lerobot: each
    frame's delta is R_t^T(p_cmd - p_meas) anchored to THAT frame's measured
    pose). Call it every control tick with fresh q_meas — the measured anchor
    resets each step, so tracking lag/drift never accumulates and the
    reconstructed target equals the commanded absolute pose p_cmd.

    Deltas are tool-frame, so the anchor's rotation R (not just position) maps
    them back to base:
      p_target = p_meas + R @ (dpos*pos_max)
      R_target = expmap(R @ (drot*rot_max)) @ R
    Gripper: policy ±1 -> 0..1 (client/FR3 convention).
    """
    d = np.asarray(delta7, dtype=np.float64)
    q_meas = np.asarray(q_meas, dtype=np.float64)
    T = fk(q_meas)
    R, p = T[:3, :3], T[:3, 3]
    T_tgt = np.eye(4)
    T_tgt[:3, 3] = p + R @ (d[:3] * pos_max)
    T_tgt[:3, :3] = axis_angle_to_rot(R @ (d[3:6] * rot_max)) @ R
    q = ik(T_tgt, q_meas)
    out = np.zeros(8)
    out[:7] = q
    out[7] = float(np.clip((d[6] + 1.0) / 2.0, 0.0, 1.0))
    return out


def ee_chunk_to_joint_chunk(actions_ee: np.ndarray, q_meas: np.ndarray,
                            pos_max: float = 0.05, rot_max: float = 0.5) -> np.ndarray:
    """[K,7] normalized EE-frame deltas + measured joints (7,) -> [K,8] joint targets.

    EVERY step's delta is applied from the SAME measured pose (fixed anchor).
    Training deltas are cmd(t) relative to that frame's MEASURED pose, and the
    command persistently LEADS the follower by ~30-40 mm; chaining deltas onto
    the previous TARGET stacks that lead K times (~0.3 m overshoot per chunk →
    divergence, observed on the real robot). Anchoring at the measured pose
    keeps every target ≈ one lead ahead — carrot-chasing dynamics, re-anchored
    at each replan, motion speed ≈ lead/replan-period ≈ demo speed.
    Gripper: policy ±1 -> 0..1 (client/FR3 convention).
    """
    K = actions_ee.shape[0]
    out = np.zeros((K, 8))
    T0 = fk(q_meas)
    R0, p0 = T0[:3, :3], T0[:3, 3]
    q = np.asarray(q_meas, dtype=np.float64).copy()  # IK warm start만 연쇄
    for k in range(K):
        d = actions_ee[k]
        T_tgt = np.eye(4)
        T_tgt[:3, 3] = p0 + R0 @ (d[:3] * pos_max)
        T_tgt[:3, :3] = axis_angle_to_rot(R0 @ (d[3:6] * rot_max)) @ R0
        q = ik(T_tgt, q)
        out[k, :7] = q
        out[k, 7] = float(np.clip((d[6] + 1.0) / 2.0, 0.0, 1.0))
    return out
