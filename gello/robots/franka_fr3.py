"""GELLO robot backend for a Franka FR3 controlled through `pylibfranka`.

Unlike ``gello/robots/panda.py`` (which targets Facebook's *polymetis* server),
this backend talks to the robot directly with the locally built ``pylibfranka``
0.1.x bindings (libfranka 0.17, FR3 system image 5.7.2).  See
``~/pylibfranka-setup.md`` for how that binding was produced.

REQUIRED: ``pylibfranka`` must be patched with ``patches/pylibfranka-gil-release.diff``
and rebuilt.  The stock bindings never release the GIL, so a blocking gripper
call freezes the 1 kHz control thread and the robot aborts with
``communication_constraints_violation`` within a second of connecting.  See
``patches/README.md``.

Design
------
The GELLO teleop stack drives the robot through a synchronous ZMQ REQ/REP
server (:class:`gello.zmq_core.robot_node.ZMQServerRobot`).  Each
``command_joint_state`` call must return quickly.  ``pylibfranka`` on the other
hand exposes an *active control* interface that has to be serviced at 1 kHz
(``readOnce``/``writeOnce``).  We bridge the two:

* ``command_joint_state`` only updates a shared setpoint (``_desired_q``) and a
  shared gripper target -- it never blocks on the robot.
* A background thread owns the 1 kHz joint-position control loop.  Every tick it
  advances an internal command ``_q_cmd`` toward ``_desired_q`` through a
  critically-damped second-order reference filter saturated in jerk,
  acceleration and velocity.

  The jerk clamp is load-bearing, not a nicety.  libfranka only runs
  ``limitRate()``/``lowpassFilter()`` on the blocking ``Robot::control()`` path
  (``src/control_loop.cpp``); the active-control API we use here sends ``q_c``
  to the robot completely unshaped.  This filter is therefore the *only* thing
  keeping the command inside the reflex limits.  Without the jerk clamp, normal
  ~1 Hz hand motion on the leader saturates the acceleration clamp and flips it
  between +/-a_max on adjacent ticks -- a jerk of 2*a_max/dt = 8000 rad/s^3
  against a ``kMaxJointJerk`` of 5000, which aborts with
  ``joint_motion_generator_acceleration_discontinuity``.
* A second thread services the (blocking) gripper API with a dead-band.

Safety
------
* ``read_only=True`` (the default is *False*) connects and streams state but
  never starts motion -- use it to validate the pipeline first.
* Collision thresholds and joint impedance are set conservatively on connect.
* ``max_joint_velocity`` / ``max_joint_acceleration`` default low.  Raise them
  only after the arm tracks GELLO smoothly.  ``max_joint_jerk`` is a limit, not
  a tuning knob -- keep it under ``franka::kMaxJointJerk`` (5000) with margin.

Make sure ``ros2_control_node`` is **not** running: the FCI accepts one client.

Note that ``~/pylibfranka-setup.md`` section 7-2 blames the FCI aborts on the
real-time setup (memlock).  That is wrong.  Nothing in libfranka or pylibfranka
calls ``mlockall()``, so the memlock limit never applied, and ``rtprio`` is
already sufficient -- libfranka sets SCHED_FIFO itself under ``kEnforce``.  The
two real causes were the GIL (see ``patches/README.md``) and the missing jerk
clamp above.
"""

import threading
import time
from typing import Dict, Optional

import numpy as np

from gello.robots.robot import Robot

# FR3 gripper stroke (m).  Franka Hand opens to ~0.08 m.
MAX_GRIPPER_WIDTH = 0.08

# Conservative default joint impedance (N*m/rad), same order as libfranka docs.
DEFAULT_JOINT_IMPEDANCE = [3000.0, 3000.0, 3000.0, 2500.0, 2500.0, 2000.0, 2000.0]


class FrankaFR3Robot(Robot):
    """Franka FR3 backend driven by ``pylibfranka``.

    Args:
        robot_ip: FCI address of the robot (default ``172.16.0.2``).
        use_gripper: expose / drive the Franka Hand.
        read_only: connect and stream state but never command motion.
        enforce_rt: pass ``RealtimeConfig.kEnforce`` (True) or ``kIgnore``.
        max_joint_velocity: reference-filter velocity saturation (rad/s).
        max_joint_acceleration: reference-filter acceleration saturation (rad/s^2).
        filter_wn: natural frequency of the reference filter (rad/s); higher =
            more responsive, lower = smoother.
        home_gripper: run a blocking gripper ``homing()`` on connect (it moves!).
    """

    def __init__(
        self,
        robot_ip: str = "172.16.0.2",
        use_gripper: bool = True,
        read_only: bool = False,
        enforce_rt: bool = True,
        max_joint_velocity: float = 1.0,
        max_joint_acceleration: float = 4.0,
        # Below franka::kMaxJointJerk (5000) with margin. The ActiveControl API
        # does NOT run libfranka's limitRate()/lowpassFilter() -- only the
        # blocking Robot::control() path does -- so this filter is the only
        # thing keeping the command inside the robot's reflex limits.
        max_joint_jerk: float = 3000.0,
        filter_wn: float = 8.0,
        home_gripper: bool = False,
        collision_torque: float = 20.0,
        collision_force: float = 20.0,
    ):
        import pylibfranka as pf

        self._pf = pf
        self._read_only = read_only
        self._use_gripper = use_gripper
        self._v_max = float(max_joint_velocity)
        self._a_max = float(max_joint_acceleration)
        self._j_max = float(max_joint_jerk)
        # critically damped: kd = 2*wn, kp = wn^2
        self._kp = float(filter_wn) ** 2
        self._kd = 2.0 * float(filter_wn)
        self._dt = 1e-3  # FCI control period (1 kHz)

        rt = pf.RealtimeConfig.kEnforce if enforce_rt else pf.RealtimeConfig.kIgnore
        print(f"[FR3] connecting to {robot_ip} (realtime={'enforce' if enforce_rt else 'ignore'})")
        self.robot = pf.Robot(robot_ip, rt)

        # Conservative safety limits before any motion.
        self.robot.set_collision_behavior(
            [collision_torque] * 7,
            [collision_torque] * 7,
            [collision_force] * 6,
            [collision_force] * 6,
        )
        self.robot.set_joint_impedance(DEFAULT_JOINT_IMPEDANCE)

        st = self.robot.read_once()
        q0 = np.asarray(st.q, dtype=float)
        print(f"[FR3] connected. q = {np.round(q0, 3)}  mode = {st.robot_mode}")

        # Shared state (guarded by _lock).
        self._lock = threading.Lock()
        self._q = q0.copy()            # latest measured joint positions
        self._dq = np.zeros(7)         # latest measured joint velocities
        self._desired_q = q0.copy()    # setpoint from command_joint_state
        self._q_cmd = q0.copy()        # filtered command sent to the robot
        self._qd_cmd = np.zeros(7)     # filter velocity state
        self._ee_pose = np.asarray(st.O_T_EE, dtype=float)
        self._success_rate = 1.0
        self._control_error: Optional[str] = None
        self._stop = threading.Event()

        # Gripper.
        self._gripper = None
        self._gripper_target = 0.0     # normalized 1=closed, 0=open (GELLO convention)
        self._gripper_state_width = MAX_GRIPPER_WIDTH
        if use_gripper:
            self._gripper = pf.Gripper(robot_ip)
            if home_gripper:
                print("[FR3] homing gripper (this moves the fingers)...")
                self._gripper.homing()
            gs = self._gripper.read_once()
            self._gripper_state_width = float(gs.width)
            self._gripper_target = 1.0 - gs.width / MAX_GRIPPER_WIDTH

        # Background threads.
        self._control_thread: Optional[threading.Thread] = None
        self._gripper_thread: Optional[threading.Thread] = None
        if read_only:
            # Stream measured state only; never command motion.
            self._control_thread = threading.Thread(
                target=self._read_only_loop, daemon=True
            )
            self._control_thread.start()
            print("[FR3] read-only mode: streaming state, NO motion will be commanded.")
        else:
            self._control_thread = threading.Thread(
                target=self._control_loop, daemon=True
            )
            self._control_thread.start()
            if use_gripper:
                self._gripper_thread = threading.Thread(
                    target=self._gripper_loop, daemon=True
                )
                self._gripper_thread.start()
            # Give the control loop a moment to establish the 1 kHz stream.
            time.sleep(0.2)
            if self._control_error is not None:
                raise RuntimeError(f"[FR3] control loop failed to start: {self._control_error}")

    # ------------------------------------------------------------------ Robot
    def num_dofs(self) -> int:
        return 8 if self._use_gripper else 7

    def get_joint_state(self) -> np.ndarray:
        with self._lock:
            q = self._q.copy()
            gripper_norm = 1.0 - self._gripper_state_width / MAX_GRIPPER_WIDTH
        if self._use_gripper:
            return np.append(q, gripper_norm)
        return q

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        joint_state = np.asarray(joint_state, dtype=float)
        q_des = joint_state[:7]
        with self._lock:
            self._desired_q = q_des.copy()
            if self._use_gripper and len(joint_state) >= 8:
                self._gripper_target = float(np.clip(joint_state[7], 0.0, 1.0))

    def get_observations(self) -> Dict[str, np.ndarray]:
        with self._lock:
            q = self._q.copy()
            dq = self._dq.copy()
            pose = self._ee_pose.copy()
            gripper_norm = 1.0 - self._gripper_state_width / MAX_GRIPPER_WIDTH
        if self._use_gripper:
            pos = np.append(q, gripper_norm)
            vel = np.append(dq, 0.0)
        else:
            pos, vel = q, dq
        return {
            "joint_positions": pos,
            "joint_velocities": vel,
            "ee_pos_quat": self._pose_to_pos_quat(pose),
            "gripper_position": np.array(gripper_norm),
        }

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _pose_to_pos_quat(o_t_ee: np.ndarray) -> np.ndarray:
        """Convert a column-major 4x4 flat pose into [x, y, z, qx, qy, qz, qw]."""
        T = np.asarray(o_t_ee, dtype=float).reshape(4, 4, order="F")
        pos = T[:3, 3]
        R = T[:3, :3]
        # Robust rotation-matrix -> quaternion (Shepperd's method, simple form).
        tr = np.trace(R)
        if tr > 0:
            s = np.sqrt(tr + 1.0) * 2
            qw = 0.25 * s
            qx = (R[2, 1] - R[1, 2]) / s
            qy = (R[0, 2] - R[2, 0]) / s
            qz = (R[1, 0] - R[0, 1]) / s
        elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
            s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
            qw = (R[2, 1] - R[1, 2]) / s
            qx = 0.25 * s
            qy = (R[0, 1] + R[1, 0]) / s
            qz = (R[0, 2] + R[2, 0]) / s
        elif R[1, 1] > R[2, 2]:
            s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
            qw = (R[0, 2] - R[2, 0]) / s
            qx = (R[0, 1] + R[1, 0]) / s
            qy = 0.25 * s
            qz = (R[1, 2] + R[2, 1]) / s
        else:
            s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
            qw = (R[1, 0] - R[0, 1]) / s
            qx = (R[0, 2] + R[2, 0]) / s
            qy = (R[1, 2] + R[2, 1]) / s
            qz = 0.25 * s
        return np.array([pos[0], pos[1], pos[2], qx, qy, qz, qw])

    # ------------------------------------------------------------------ loops
    def _read_only_loop(self) -> None:
        while not self._stop.is_set():
            try:
                st = self.robot.read_once()
                with self._lock:
                    self._q = np.asarray(st.q, dtype=float)
                    self._dq = np.asarray(st.dq, dtype=float)
                    self._ee_pose = np.asarray(st.O_T_EE, dtype=float)
                    self._success_rate = float(st.control_command_success_rate)
            except Exception as e:  # noqa: BLE001
                self._control_error = str(e)
                print(f"[FR3] read-only loop error: {e}")
                break
            time.sleep(0.001)

    def _control_loop(self) -> None:
        pf = self._pf
        try:
            ctrl = self.robot.start_joint_position_control(
                pf.ControllerMode.JointImpedance
            )
            # First tick: latch the command to the measured position (no jump).
            state, _ = ctrl.readOnce()
            with self._lock:
                self._q = np.asarray(state.q, dtype=float)
                self._q_cmd = self._q.copy()
                self._qd_cmd = np.zeros(7)
                self._desired_q = self._q.copy()
                q_cmd = self._q_cmd.copy()
                qd_cmd = self._qd_cmd.copy()
            cmd = pf.JointPositions(list(q_cmd))
            ctrl.writeOnce(cmd)

            dt = self._dt
            acc_prev = np.zeros(7)
            while not self._stop.is_set():
                state, _ = ctrl.readOnce()
                with self._lock:
                    target = self._desired_q.copy()
                    self._q = np.asarray(state.q, dtype=float)
                    self._dq = np.asarray(state.dq, dtype=float)
                    self._ee_pose = np.asarray(state.O_T_EE, dtype=float)
                    self._success_rate = float(state.control_command_success_rate)

                # Critically-damped second-order reference filter, saturated in
                # jerk, acceleration and velocity -> smooth, bounded command.
                #
                # The jerk clamp is what keeps the robot from tripping
                # joint_motion_generator_acceleration_discontinuity: the leader
                # setpoint is a noisy 100 Hz staircase, so an unclamped acc
                # flips between +/-a_max on adjacent ticks, which is a jerk of
                # 2*a_max/dt -- far past franka::kMaxJointJerk.
                err = target - q_cmd
                acc_target = np.clip(
                    self._kp * err - self._kd * qd_cmd, -self._a_max, self._a_max
                )
                dacc_max = self._j_max * dt
                acc = np.clip(acc_target, acc_prev - dacc_max, acc_prev + dacc_max)

                qd_new = np.clip(qd_cmd + acc * dt, -self._v_max, self._v_max)
                # Feed back the acceleration that actually survived the velocity
                # clamp; using the pre-clamp value would let acc_prev drift away
                # from reality and spike the moment the clamp releases.
                acc_prev = (qd_new - qd_cmd) / dt
                qd_cmd = qd_new
                q_cmd = q_cmd + qd_cmd * dt

                cmd = pf.JointPositions(list(q_cmd))
                if self._stop.is_set():
                    cmd.motion_finished = True
                ctrl.writeOnce(cmd)

                with self._lock:
                    self._q_cmd = q_cmd.copy()
                    self._qd_cmd = qd_cmd.copy()
        except Exception as e:  # noqa: BLE001
            self._control_error = str(e)
            print(f"[FR3] CONTROL LOOP ABORTED: {e}")

    def _gripper_loop(self) -> None:
        """Service the blocking gripper API with a dead-band."""
        deadband = 0.04  # normalized units; avoid chattering the slow gripper
        # Latch to the current target: the gripper already sits at this width, so
        # a first-pass move() would only stall the control loop as it comes up.
        with self._lock:
            last_cmd = self._gripper_target
        speed = 0.1
        while not self._stop.is_set():
            with self._lock:
                target = self._gripper_target
            try:
                gs = self._gripper.read_once()
                with self._lock:
                    self._gripper_state_width = float(gs.width)
            except Exception:  # noqa: BLE001
                pass
            if last_cmd is None or abs(target - last_cmd) > deadband:
                width = float(np.clip(1.0 - target, 0.0, 1.0)) * MAX_GRIPPER_WIDTH
                try:
                    self._gripper.move(width, speed)
                    last_cmd = target
                except Exception as e:  # noqa: BLE001
                    print(f"[FR3] gripper move failed: {e}")
            time.sleep(0.05)

    # ------------------------------------------------------------------ misc
    @property
    def control_command_success_rate(self) -> float:
        with self._lock:
            return self._success_rate

    def stop(self) -> None:
        self._stop.set()
        if self._control_thread is not None:
            self._control_thread.join(timeout=1.0)
        if self._gripper_thread is not None:
            self._gripper_thread.join(timeout=1.0)
        try:
            self.robot.stop()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self):
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    # Read-only smoke test (no motion).
    import tyro

    def main(robot_ip: str = "172.16.0.2", use_gripper: bool = True):
        r = FrankaFR3Robot(robot_ip=robot_ip, use_gripper=use_gripper, read_only=True)
        try:
            while True:
                print("q =", np.round(r.get_joint_state(), 3),
                      " success=", round(r.control_command_success_rate, 3))
                time.sleep(0.5)
        except KeyboardInterrupt:
            r.stop()

    tyro.cli(main)
