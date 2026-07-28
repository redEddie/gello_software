"""LIBERO-format HDF5 writer for real FR3 + GELLO teleop demonstrations.

Schema mirrors what LIBERO/robomimic loaders actually read, confirmed against the
official LIBERO repo's own reading code (``scripts/get_dataset_info.py``,
``libero/libero/utils/dataset_utils.py``) and OpenVLA's
``experiments/robot/libero/regenerate_libero_dataset.py`` (obs key names, 256x256
image convention)::

    <task_name>_demo.hdf5
      data/                         (attrs: env_args=json str, problem_info=json str)
        demo_0/                     (attrs: num_samples=int)
          obs/
            agentview_rgb           (T, H, W, 3) uint8
            eye_in_hand_rgb         (T, H, W, 3) uint8
            joint_states            (T, 7) float32
            gripper_states          (T, 1) float32   -- continuous width, 0=open..1=closed
            ee_states               (T, 6) float32   -- xyz + axis-angle
            ee_pos                  (T, 3) float32
            ee_ori                  (T, 3) float32   -- axis-angle
          actions                   (T, 7) float32   -- normalized [-1, 1]
          rewards                   (T,) float32
          dones                     (T,) float32

One HDF5 file per task/language-instruction, one ``demo_N`` group per episode,
matching the official per-task file layout (e.g.
``turn_on_the_stove_demo.hdf5``).

Deliberately dropped vs. the simulator-generated original: ``states`` and
``model_file`` (full MuJoCo sim state / scene XML -- meaningless for a real
robot) and a genuine ``env_args`` (there is no BDDL scene/controller config to
report). ``env_args``/``problem_info`` are still written as JSON so
``get_dataset_info.py``-style readers do not KeyError, but ``env_args`` is a
real-robot stub, not simulator-replayable metadata.

ACTION SPACE -- read this before trusting a trained policy
------------------------------------------------------------
GELLO teleop here drives the follower in **joint space** (the leader mirrors
the follower's joints 1:1); there is no native commanded end-effector delta.
To stay drop-in compatible with LIBERO-format consumers (which assume a
robosuite ``OSC_POSE`` controller), ``actions`` is reconstructed *after the
fact* from the realized Cartesian trajectory: at frame ``t`` it is the
world-frame delta pose that carried ``ee_pos_quat[t] -> ee_pos_quat[t+1]``,
normalized by the OSC_POSE defaults (``output_max`` = 0.05 m / 0.5 rad),
clipped to [-1, 1]. Gripper: -1=open / +1=close (robosuite Panda convention).
Frame convention and gripper sign are taken from robosuite's documented
defaults, not verified byte-for-byte against an official demo file (repeated
attempts to stream one in this sandbox hit transient network failures --
before training anything on this data, sanity-check a few saved episodes by
eye: gripper sign flips exactly when the operator's trigger did, and replaying
cumulative ``actions`` roughly reproduces ``ee_states``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import h5py
import numpy as np

# robosuite OSC_POSE defaults (position/orientation controllers), see
# robosuite.controllers.parts.arm.osc.OperationalSpaceController.
ACTION_POS_MAX = 0.05  # m per control step
ACTION_ROT_MAX = 0.5  # rad per control step (axis-angle vector component)

IMAGE_SIZE = 256  # matches OpenVLA's LIBERO regeneration convention


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


def resize_rgb(img: np.ndarray, size: int = IMAGE_SIZE) -> np.ndarray:
    """Resize an (H, W, 3) uint8 RGB image to (size, size, 3), center-cropped square first."""
    import cv2

    h, w = img.shape[:2]
    s = min(h, w)
    y0, x0 = (h - s) // 2, (w - s) // 2
    cropped = img[y0 : y0 + s, x0 : x0 + s]
    return cv2.resize(cropped, (size, size), interpolation=cv2.INTER_AREA)


class LiberoEpisodeBuffer:
    """Accumulates one episode's frames in memory before it is committed or discarded."""

    def __init__(self) -> None:
        self.agentview_rgb: list[np.ndarray] = []
        self.eye_in_hand_rgb: list[np.ndarray] = []
        self.joint_states: list[np.ndarray] = []
        self.gripper_states: list[np.ndarray] = []
        self.ee_pos_quat: list[np.ndarray] = []
        self.gripper_closed: list[bool] = []

    def __len__(self) -> int:
        return len(self.joint_states)

    def add_frame(
        self,
        agentview_rgb: np.ndarray,
        eye_in_hand_rgb: np.ndarray,
        joint_positions: np.ndarray,
        gripper_position: float,
        ee_pos_quat: np.ndarray,
        gripper_closed: bool,
    ) -> None:
        self.agentview_rgb.append(resize_rgb(agentview_rgb))
        self.eye_in_hand_rgb.append(resize_rgb(eye_in_hand_rgb))
        self.joint_states.append(np.asarray(joint_positions, dtype=np.float32))
        self.gripper_states.append(np.array([gripper_position], dtype=np.float32))
        self.ee_pos_quat.append(np.asarray(ee_pos_quat, dtype=np.float64))
        self.gripper_closed.append(bool(gripper_closed))

    def clear(self) -> None:
        self.__init__()  # type: ignore[misc]


class LiberoTaskWriter:
    """Owns one ``<task>_demo.hdf5`` file: one file per task, one ``demo_N`` per episode.

    Not safe for concurrent writers on the same file; one collection session
    owns one open writer.
    """

    def __init__(
        self,
        root: Path,
        task_name: str,
        language_instruction: str,
        robot_name: str = "fr3_gello_real",
        resume: bool = False,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = task_name.strip().replace(" ", "_")
        self.path = self.root / f"{safe_name}_demo.hdf5"
        self.language_instruction = language_instruction
        self._buffer = LiberoEpisodeBuffer()

        if self.path.exists() and not resume:
            raise FileExistsError(
                f"{self.path} already exists; pass resume=True to append episodes."
            )

        self._file = h5py.File(self.path, "a")
        self._data = self._file.require_group("data")
        if "env_args" not in self._data.attrs:
            env_args = {
                "env_name": "real_fr3_gello",
                "type": "real_robot",
                "env_kwargs": {
                    "robot": robot_name,
                    "note": (
                        "Real-robot capture -- no simulator/BDDL scene; this "
                        "field exists only so LIBERO-style readers that "
                        "expect it do not KeyError."
                    ),
                },
            }
            self._data.attrs["env_args"] = json.dumps(env_args)
        if "problem_info" not in self._data.attrs:
            problem_info = {
                "language_instruction": f'"{self.language_instruction}"',
                "problem_name": safe_name,
            }
            self._data.attrs["problem_info"] = json.dumps(problem_info)
        if "next_demo_idx" not in self._data.attrs:
            # Monotonic, never reused -- so deleting demo_2 and recording a new
            # episode afterwards can't collide with the name of a demo that
            # still exists. `num_episodes` (live count) and this counter
            # (total ever assigned) intentionally diverge once anything has
            # been deleted.
            existing = [int(k.split("_")[1]) for k in self._data.keys()]
            self._data.attrs["next_demo_idx"] = max(existing, default=-1) + 1
        self._file.flush()

    @property
    def num_episodes(self) -> int:
        return len(self._data.keys())

    def list_episodes(self) -> list[dict]:
        """Current demos sorted by index: ``[{"name", "num_samples", "success"}, ...]``."""
        items = []
        for name in self._data.keys():
            grp = self._data[name]
            success = grp.attrs.get("success")
            items.append(
                {
                    "name": name,
                    "num_samples": int(grp.attrs.get("num_samples", grp["actions"].shape[0])),
                    "success": None if success is None else bool(success),
                }
            )
        items.sort(key=lambda d: int(d["name"].split("_")[1]))
        return items

    def delete_episode(self, name: str) -> None:
        """Removes a ``demo_N`` group.

        HDF5 does not shrink the file on delete -- the freed space is only
        reusable by later writes *within this same file*, not returned to the
        OS. Run ``h5repack`` afterwards if reclaiming disk space matters.
        """
        if name not in self._data:
            raise KeyError(f"{name!r} not found in {self.path}")
        del self._data[name]
        self._file.flush()

    def start_episode(self) -> None:
        self._buffer.clear()

    def add_frame(self, **kwargs: Any) -> None:
        """Forwards to :meth:`LiberoEpisodeBuffer.add_frame`."""
        self._buffer.add_frame(**kwargs)

    def discard_episode(self) -> None:
        self._buffer.clear()

    def save_episode(self, success: Optional[bool] = None) -> Optional[str]:
        """Commits the buffered episode as a new ``demo_N`` group.

        Args:
            success: operator-labeled outcome (no simulator goal-check exists
                for a real robot). Not a canonical LIBERO field; stored as a
                per-demo attr for downstream filtering. ``None`` if unlabeled.

        Returns the group name, or None if the buffer was empty.
        """
        n = len(self._buffer)
        if n < 2:
            self._buffer.clear()
            return None

        ee = np.stack(self._buffer.ee_pos_quat)  # (n, 7)
        actions = np.zeros((n, 7), dtype=np.float32)
        for t in range(n - 1):
            actions[t] = compute_delta_action(
                ee[t], ee[t + 1], self._buffer.gripper_closed[t]
            )
        # Terminal frame: no further motion recorded; hold gripper state.
        actions[n - 1, :6] = 0.0
        actions[n - 1, 6] = 1.0 if self._buffer.gripper_closed[-1] else -1.0

        ee_ori = np.stack(
            [_quat_to_axis_angle(*q[3:7]) for q in ee]
        ).astype(np.float32)
        ee_pos = ee[:, :3].astype(np.float32)
        ee_states = np.concatenate([ee_pos, ee_ori], axis=1).astype(np.float32)

        demo_idx = int(self._data.attrs["next_demo_idx"])
        self._data.attrs["next_demo_idx"] = demo_idx + 1
        name = f"demo_{demo_idx}"
        grp = self._data.create_group(name)
        grp.attrs["num_samples"] = n
        if success is not None:
            grp.attrs["success"] = bool(success)

        obs = grp.create_group("obs")
        obs.create_dataset(
            "agentview_rgb",
            data=np.stack(self._buffer.agentview_rgb),
            compression="gzip",
            compression_opts=4,
        )
        obs.create_dataset(
            "eye_in_hand_rgb",
            data=np.stack(self._buffer.eye_in_hand_rgb),
            compression="gzip",
            compression_opts=4,
        )
        obs.create_dataset("joint_states", data=np.stack(self._buffer.joint_states))
        obs.create_dataset(
            "gripper_states", data=np.stack(self._buffer.gripper_states)
        )
        obs.create_dataset("ee_states", data=ee_states)
        obs.create_dataset("ee_pos", data=ee_pos)
        obs.create_dataset("ee_ori", data=ee_ori)

        grp.create_dataset("actions", data=actions)
        grp.create_dataset("rewards", data=np.zeros(n, dtype=np.float32))
        dones = np.zeros(n, dtype=np.float32)
        dones[-1] = 1.0
        grp.create_dataset("dones", data=dones)

        self._file.flush()
        self._buffer.clear()
        return name

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "LiberoTaskWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
