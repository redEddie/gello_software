"""Derive commanded EE-delta actions from recorded GELLO leader commands.

Why
---
``actions`` in files written by gello/libero_format.py is reconstructed from
the follower's *realized* trajectory. Wherever the follower is in contact the
realized delta collapses to ~zero even though the operator is still pressing
-- the force intent lives only in the leader command stream
(``obs/commanded_joint_states``, recorded since the commanded-ee-actions
branch). This script turns that stream into OSC_POSE-style commanded delta
actions, offline:

    actions_world_cmd[t] = [ (p_cmd_t - p_t)/0.05,
                             axis_angle(R_cmd_t · R_t^T)/0.5,
                             gripper ]            clipped to [-1, 1]
    actions_ee[t]        = same, with both 3-vectors rotated into the EE
                           frame of t: v_ee = R_t^T · v_world

where (p_t, R_t) is the follower's measured EE pose at frame t (obs/ee_states
timebase -- the same "R_t = FK(states[t])" rule the training side uses) and
(p_cmd_t, R_cmd_t) = FK(commanded_joint_states[t]).

``actions_ee`` matches what a LIBERO ``actions_ee`` consumer expects
(EE-frame command delta, gripper column untouched), so a trainer can point
``action_field=actions_ee`` at these files directly.

FK and calibration
------------------
FR3 forward kinematics uses the Franka-published Craig-modified DH chain
(identical table for Panda/FR3) up to the flange. The flange->EE transform
(gripper mount, O_T_EE config) is NOT assumed: it is recovered per file by
averaging  X_t = T_flange(q_t)^{-1} · T_meas_t  over all frames of all
episodes, then validated -- the residual of FK(q_t)·X against the recorded
``ee_pos_quat`` is reported, and the run aborts if the median exceeds
--max-fk-residual-mm (default 5 mm), which would mean the kinematic model
does not match this robot/EE config and every derived action would be junk.

Existing datasets are never modified; the two new datasets are added next to
``actions`` (원본 보존). Re-run with --overwrite to replace previously
derived ones.

Usage:
    python scripts/derive_commanded_ee_actions.py <task>_demo.hdf5 [more.hdf5 ...]
        [--overwrite] [--dry-run] [--max-fk-residual-mm 5.0]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np

from gello.data.dataset_schema import (
    OBS_COMMANDED_GRIPPER_STATES,
    OBS_COMMANDED_JOINT_STATES,
    OBS_EE_STATES,
    OBS_JOINT_STATES,
)

# OSC_POSE normalization, same constants as gello.data.actions.
ACTION_POS_MAX = 0.05  # m
ACTION_ROT_MAX = 0.5  # rad

# Craig-modified DH for Panda/FR3 (Franka Control Interface docs), flange row
# last: (a_{i-1} [m], d_i [m], alpha_{i-1} [rad]); theta_8 = 0 for the flange.
_FR3_MDH = np.array([
    [0.0,      0.333,  0.0],
    [0.0,      0.0,   -np.pi / 2],
    [0.0,      0.316,  np.pi / 2],
    [0.0825,   0.0,    np.pi / 2],
    [-0.0825,  0.384, -np.pi / 2],
    [0.0,      0.0,    np.pi / 2],
    [0.088,    0.0,    np.pi / 2],
    [0.0,      0.107,  0.0],
])


def fk_flange(q: np.ndarray) -> np.ndarray:
    """Batch FK: joint angles (T, 7) -> flange pose in base frame (T, 4, 4)."""
    q = np.asarray(q, dtype=np.float64)
    T = q.shape[0]
    theta = np.concatenate([q, np.zeros((T, 1))], axis=1)  # flange: theta=0
    out = np.broadcast_to(np.eye(4), (T, 4, 4)).copy()
    for i in range(8):
        a, d, alpha = _FR3_MDH[i]
        ca, sa = np.cos(alpha), np.sin(alpha)
        ct, st = np.cos(theta[:, i]), np.sin(theta[:, i])
        # Craig: ^{i-1}T_i = RotX(alpha_{i-1}) TransX(a_{i-1}) RotZ(theta_i) TransZ(d_i)
        Ti = np.zeros((T, 4, 4))
        Ti[:, 0, 0] = ct;       Ti[:, 0, 1] = -st;      Ti[:, 0, 3] = a
        Ti[:, 1, 0] = st * ca;  Ti[:, 1, 1] = ct * ca;  Ti[:, 1, 2] = -sa; Ti[:, 1, 3] = -d * sa
        Ti[:, 2, 0] = st * sa;  Ti[:, 2, 1] = ct * sa;  Ti[:, 2, 2] = ca;  Ti[:, 2, 3] = d * ca
        Ti[:, 3, 3] = 1.0
        out = out @ Ti
    return out


def quat_to_mat(q_xyzw: np.ndarray) -> np.ndarray:
    """Scalar-last quaternions (T, 4) -> rotation matrices (T, 3, 3)."""
    x, y, z, w = (q_xyzw[:, i] for i in range(4))
    n = x * x + y * y + z * z + w * w
    s = 2.0 / np.where(n < 1e-12, 1.0, n)
    R = np.empty((q_xyzw.shape[0], 3, 3))
    R[:, 0, 0] = 1 - s * (y * y + z * z); R[:, 0, 1] = s * (x * y - z * w); R[:, 0, 2] = s * (x * z + y * w)
    R[:, 1, 0] = s * (x * y + z * w); R[:, 1, 1] = 1 - s * (x * x + z * z); R[:, 1, 2] = s * (y * z - x * w)
    R[:, 2, 0] = s * (x * z - y * w); R[:, 2, 1] = s * (y * z + x * w); R[:, 2, 2] = 1 - s * (x * x + y * y)
    return R


def mat_to_axis_angle(R: np.ndarray) -> np.ndarray:
    """Rotation matrices (T, 3, 3) -> axis*angle vectors (T, 3)."""
    tr = np.clip((R[:, 0, 0] + R[:, 1, 1] + R[:, 2, 2] - 1.0) / 2.0, -1.0, 1.0)
    angle = np.arccos(tr)  # [0, pi]
    v = np.stack([
        R[:, 2, 1] - R[:, 1, 2],
        R[:, 0, 2] - R[:, 2, 0],
        R[:, 1, 0] - R[:, 0, 1],
    ], axis=1)
    s = 2.0 * np.sin(angle)
    small = angle < 1e-7
    # near pi, v ~ 0; fall back to the diagonal formula
    near_pi = angle > np.pi - 1e-3
    axis = np.zeros_like(v)
    ok = ~small & ~near_pi
    axis[ok] = v[ok] / s[ok, None]
    if near_pi.any():
        # axis from largest diagonal element of (R + I)/2
        M = (R[near_pi] + np.eye(3)) / 2.0
        d = np.stack([M[:, 0, 0], M[:, 1, 1], M[:, 2, 2]], axis=1)
        k = d.argmax(axis=1)
        ax = np.sqrt(np.maximum(d[np.arange(len(k)), k], 0.0))
        cols = M[np.arange(len(k)), :, k] / np.where(ax < 1e-9, 1.0, ax)[:, None]
        # sign is unrecoverable at exactly pi; pick the one matching v
        sign = np.where((cols * v[near_pi]).sum(axis=1) < 0, -1.0, 1.0)
        axis[near_pi] = cols * sign[:, None]
    return axis * angle[:, None]


def average_transform(Xs: np.ndarray) -> np.ndarray:
    """Average of rigid transforms (T, 4, 4): mean position + quaternion eigen-mean."""
    p = Xs[:, :3, 3].mean(axis=0)
    # rotation average via the largest eigenvector of sum(q q^T)
    R = Xs[:, :3, :3]
    qs = _mat_to_quat_batch(R)
    qs = np.where((qs @ qs[0])[:, None] < 0, -qs, qs)  # hemisphere align
    A = (qs[:, :, None] * qs[:, None, :]).sum(axis=0)
    w, v = np.linalg.eigh(A)
    q_mean = v[:, -1]
    out = np.eye(4)
    out[:3, :3] = quat_to_mat(q_mean[None, [0, 1, 2, 3]])[0]
    out[:3, 3] = p
    return out


def _mat_to_quat_batch(R: np.ndarray) -> np.ndarray:
    """(T,3,3) -> scalar-last quaternions (T,4), Shepperd's method (vectorized-enough)."""
    T = R.shape[0]
    q = np.empty((T, 4))
    for i in range(T):  # T is a few thousand at most; clarity over speed
        m = R[i]
        tr = m[0, 0] + m[1, 1] + m[2, 2]
        if tr > 0:
            s = np.sqrt(tr + 1.0) * 2
            q[i] = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s,
                    (m[1, 0] - m[0, 1]) / s, 0.25 * s]
        elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
            s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
            q[i] = [0.25 * s, (m[0, 1] + m[1, 0]) / s,
                    (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
        elif m[1, 1] > m[2, 2]:
            s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
            q[i] = [(m[0, 1] + m[1, 0]) / s, 0.25 * s,
                    (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
        else:
            s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
            q[i] = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s,
                    0.25 * s, (m[1, 0] - m[0, 1]) / s]
    return q


def pose_from_pos_quat(pos_quat: np.ndarray) -> np.ndarray:
    """(T, 7) [x,y,z,qx,qy,qz,qw] -> (T, 4, 4)."""
    T = np.broadcast_to(np.eye(4), (pos_quat.shape[0], 4, 4)).copy()
    T[:, :3, :3] = quat_to_mat(pos_quat[:, 3:7])
    T[:, :3, 3] = pos_quat[:, :3]
    return T


def _episode_pose_pairs(grp) -> tuple[np.ndarray, np.ndarray]:
    """(measured EE poses (T,4,4), measured joints (T,7)) for calibration."""
    obs = grp["obs"]
    q = obs[OBS_JOINT_STATES][:].astype(np.float64)
    if OBS_EE_STATES in obs:
        ee = obs[OBS_EE_STATES][:].astype(np.float64)  # pos + axis-angle
        T = np.broadcast_to(np.eye(4), (ee.shape[0], 4, 4)).copy()
        T[:, :3, 3] = ee[:, :3]
        T[:, :3, :3] = _axis_angle_to_mat_batch(ee[:, 3:6])
        return T, q
    raise KeyError("obs/ee_states missing -- cannot calibrate flange->EE transform")


def _axis_angle_to_mat_batch(aa: np.ndarray) -> np.ndarray:
    theta = np.linalg.norm(aa, axis=1)
    safe = np.where(theta > 1e-8, theta, 1.0)
    k = aa / safe[:, None]
    K = np.zeros((aa.shape[0], 3, 3))
    K[:, 0, 1] = -k[:, 2]; K[:, 0, 2] = k[:, 1]
    K[:, 1, 0] = k[:, 2];  K[:, 1, 2] = -k[:, 0]
    K[:, 2, 0] = -k[:, 1]; K[:, 2, 1] = k[:, 0]
    c = np.cos(theta)[:, None, None]
    s = np.sin(theta)[:, None, None]
    R = np.broadcast_to(np.eye(3), K.shape).copy() + s * K + (1 - c) * (K @ K)
    R[theta <= 1e-8] = np.eye(3)
    return R


def process_file(path: Path, overwrite: bool, dry_run: bool,
                 max_fk_residual_mm: float) -> bool:
    print(f"\n=== {path}")
    with h5py.File(path, "r" if dry_run else "a") as f:
        data = f["data"]
        demos = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
        usable = []
        for name in demos:
            obs = data[name]["obs"]
            if OBS_COMMANDED_JOINT_STATES not in obs:
                print(f"  [skip] {name}: no commanded_joint_states (old recording)")
                continue
            if not overwrite and "actions_ee" in data[name]:
                print(f"  [skip] {name}: actions_ee exists (use --overwrite)")
                continue
            usable.append(name)
        if not usable:
            print("  nothing to do")
            return True

        # ── flange->EE calibration over ALL frames of usable episodes ──
        Xs, resid_frames = [], []
        for name in usable:
            T_meas, q = _episode_pose_pairs(data[name])
            T_fl = fk_flange(q)
            Xs.append(np.linalg.inv(T_fl) @ T_meas)
        X = average_transform(np.concatenate(Xs, axis=0))

        pos_res, rot_res = [], []
        for name in usable:
            T_meas, q = _episode_pose_pairs(data[name])
            T_pred = fk_flange(q) @ X
            pos_res.append(np.linalg.norm(T_pred[:, :3, 3] - T_meas[:, :3, 3], axis=1))
            R_err = T_pred[:, :3, :3].transpose(0, 2, 1) @ T_meas[:, :3, :3]
            rot_res.append(np.linalg.norm(mat_to_axis_angle(R_err), axis=1))
        pos_res = np.concatenate(pos_res) * 1000.0  # mm
        rot_res = np.degrees(np.concatenate(rot_res))
        print(f"  [calib] FK residual: pos median {np.median(pos_res):.2f} mm "
              f"(p95 {np.percentile(pos_res, 95):.2f}) | "
              f"rot median {np.median(rot_res):.3f} deg (p95 {np.percentile(rot_res, 95):.3f})")
        if np.median(pos_res) > max_fk_residual_mm:
            print(f"  [ABORT] median FK residual > {max_fk_residual_mm} mm -- "
                  "kinematic model does not match this file; derived actions would be wrong.")
            return False

        # ── per-episode derivation ──
        for name in usable:
            grp = data[name]
            obs = grp["obs"]
            q_cmd = obs[OBS_COMMANDED_JOINT_STATES][:].astype(np.float64)
            T_meas, _q = _episode_pose_pairs(grp)
            n = q_cmd.shape[0]

            T_cmd = fk_flange(q_cmd) @ X
            p_ach, R_ach = T_meas[:, :3, 3], T_meas[:, :3, :3]
            p_cmd, R_cmd = T_cmd[:, :3, 3], T_cmd[:, :3, :3]

            dpos_w = p_cmd - p_ach                                     # (n, 3) m
            R_rel = R_cmd @ R_ach.transpose(0, 2, 1)                    # world-frame
            drot_w = mat_to_axis_angle(R_rel)                           # (n, 3) rad

            # gripper column: keep the file's existing convention
            grip_conv = grp.attrs.get("gripper_action_convention", "pm1")
            if OBS_COMMANDED_GRIPPER_STATES in obs:
                g_closed = obs[OBS_COMMANDED_GRIPPER_STATES][:, 0] > 0.5
            else:
                g_closed = grp["actions"][:, -1] > (0.5 if grip_conv == "01" else 0.0)
            grip = np.where(g_closed, 1.0, -1.0)
            if grip_conv == "01":
                grip = (grip + 1.0) / 2.0

            def pack(dpos, drot):
                a = np.concatenate([
                    dpos / ACTION_POS_MAX, drot / ACTION_ROT_MAX, grip[:, None],
                ], axis=1).astype(np.float32)
                a[:, :6] = np.clip(a[:, :6], -1.0, 1.0)
                return a

            a_world = pack(dpos_w, drot_w)
            # EE frame of t: rotate both world 3-vectors by R_t^T
            dpos_e = np.einsum("tji,tj->ti", R_ach, dpos_w)
            drot_e = np.einsum("tji,tj->ti", R_ach, drot_w)
            a_ee = pack(dpos_e, drot_e)

            clip_frac = float((np.abs(a_world[:, :6]) >= 1.0).any(axis=1).mean())
            gap_mm = np.linalg.norm(dpos_w, axis=1) * 1000.0
            print(f"  {name}: n={n} | cmd-ach gap mm median {np.median(gap_mm):.1f} "
                  f"p95 {np.percentile(gap_mm, 95):.1f} max {gap_mm.max():.1f} | "
                  f"clipped {clip_frac * 100:.1f}%")

            if dry_run:
                continue
            for key, arr in (("actions_ee", a_ee), ("actions_world_cmd", a_world)):
                if key in grp:
                    del grp[key]
                grp.create_dataset(key, data=arr)
            grp.attrs["actions_ee_derivation"] = json.dumps({
                "source": "commanded_joint_states via FR3 MDH FK + per-file flange->EE calib",
                "normalization": {"pos_max_m": ACTION_POS_MAX, "rot_max_rad": ACTION_ROT_MAX},
                "fk_residual_mm_median": float(np.median(pos_res)),
                "timebase": "R_t, p_t from obs/ee_states[t] (follower measured at frame t)",
            })
        if not dry_run:
            f.flush()
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--overwrite", action="store_true",
                    help="re-derive even if actions_ee already exists")
    ap.add_argument("--dry-run", action="store_true",
                    help="calibrate + report stats, write nothing")
    ap.add_argument("--max-fk-residual-mm", type=float, default=5.0,
                    help="abort if median FK-vs-measured residual exceeds this")
    args = ap.parse_args()
    ok = True
    for p in args.files:
        ok &= process_file(p, args.overwrite, args.dry_run, args.max_fk_residual_mm)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
