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
* A second thread services the (blocking) gripper API as a binary
  grasp/release state machine with hysteresis (see ``_gripper_loop`` for why
  ``move()`` alone cannot hold objects).

Safety
------
* ``read_only=True`` (the default is *False*) connects and streams state but
  never starts motion -- use it to validate the pipeline first.
* Collision-reflex thresholds are 100 N*m (shoulders) / 40 N*m (wrists) and
  100 N cartesian: legitimate teleop contact never fires them (the datasheet
  joint torque limits are NOT a valid choice here -- see FR3_COLLISION_TORQUE),
  while abnormal wrist loads and violent impacts still do.  Joint impedance is
  set on connect.  The robot's hard limits stay the safety floor.
* ``max_joint_velocity``/``max_joint_acceleration`` (1.5 rad/s, 6.0 rad/s^2)
  keep real margin below libfranka's actual per-joint hard limits (2.62,
  10.0 -- see ``rate_limiting.h``/``fr3.urdf``), since FR3's true velocity
  limit shrinks near each joint's position limits and this filter applies a
  flat cap everywhere. Raise further only after confirming on hardware that
  tracking still feels smooth with no reflex trips near those position
  limits specifically. ``max_joint_jerk`` is a limit, not a tuning knob --
  keep it under ``franka::kMaxJointJerk`` (5000) with margin.

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

# Normalized leader-trigger value (0=open .. 1=closed) at which the binary
# gripper closes.  The GELLO leader's trigger spring (JointLimitWall) starts its
# exponential squeeze resistance at this same value, so the moment resistance is
# felt under the finger is the moment the hand grasps.
GRIPPER_CLOSE_AT = 0.6

# Conservative default joint impedance (N*m/rad), same order as libfranka docs.
DEFAULT_JOINT_IMPEDANCE = [3000.0, 3000.0, 3000.0, 2500.0, 2500.0, 2000.0, 2000.0]

# FR3 joint torque limits (N*m), datasheet.  These are the *actuation* limits,
# and they are NOT a way to disable the collision reflex: the reflex compares
# the estimated *external* torque (tau_ext_hat_filtered), which a braced
# contact pushes far beyond what the motor itself can output.  Poking a desk
# puts ~12 N*m on a wrist joint at only ~80 N of contact force (0.15 m lever),
# tripping joint_reflex before the 100 N cartesian threshold -- observed live
# on 2026-07-17.  Kept for reference; do not use as collision thresholds.
FR3_MAX_JOINT_TORQUE = [87.0, 87.0, 87.0, 87.0, 12.0, 12.0, 12.0]

# Collision-reflex torque thresholds (N*m) actually used.  Sized from the
# 100 N cartesian force reflex, which fires first in any sustained tool
# contact and so caps the joint torques legitimate contact can produce:
# * J1-J4: 100.  Full-reach contact at the 100 N force cap puts up to
#   ~0.9 m x 100 N = 90 N*m on the shoulder -- the 87 N*m datasheet value
#   would nuisance-trip exactly at the force cap, so sit just above it.
# * J5-J7: 40.  The same 100 N at the hand acts on <= ~0.25 m of wrist
#   lever, i.e. <= ~25 N*m during any legitimate contact, so 40 never fires
#   in teleop -- but it still catches abnormal loads (snags, prying with a
#   long tool, the arm bracing its own wrist against an edge) at ~3x the
#   wrist's 12 N*m actuation limit, instead of giving the wrist structure
#   no watchdog at all as a flat 100 would.
# The non-tunable safety layers (Watchman limits, torque-sensor range
# faults, the hard joint limits) remain active regardless of these values.
FR3_COLLISION_TORQUE = [100.0, 100.0, 100.0, 100.0, 40.0, 40.0, 40.0]

# FR3 joint position limits (rad), same values as dsfranka's
# cpp/bridge/robot_limits.hpp.  Note J4 is never positive and J6 never reaches
# 0: the GELLO leader turns through both regions freely, so a leader pose does
# not imply a reachable FR3 pose.
#
# These are data, not enforcement.  The follower deliberately does NOT clamp
# commands to them -- silently overriding the leader decouples the two arms and
# leaves the operator with a dead zone.  They exist so the *leader* can be given
# a physical wall at the same place (scripts/gello_joint_limit_wall.py).
FR3_Q_LOWER = np.array([-2.7437, -1.7837, -2.9007, -3.0421, -2.8065, 0.5445, -3.0159])
FR3_Q_UPPER = np.array([2.7437, 1.7837, 2.9007, -0.1518, 2.8065, 4.5169, 3.0159])

# Named reset (home) poses, mirroring dsfranka's configs/teleop.yaml
# ``home.presets``.  dsfranka still spells these ``franka_ready`` / ``dsfranka``;
# the names here are the ones both repos are converging on.  All four are inside
# the FR3's joint limits.
FR3_RESET_POSES = {
    # Franka's built-in "ready" pose -- what the arm boots to, and where Desk's
    # "move to start" parks it.  q4 = -3pi/4.
    "fr3_ready": np.array([0.0, -0.785398, 0.0, -2.356194, 0.0, 1.570796, 0.785398]),
    # menagerie fr3_hand / panda "home" keyframe -- matches the sim model.
    # q4 = -pi/2.
    "panda": np.array([0.0, 0.0, 0.0, -1.570796, 0.0, 1.570796, 0.785398]),
    # LIBERO's init_qpos.  The benchmark OVERRIDES the robosuite default below
    # (libero/envs/robots/mounted_panda.py); the two are ~41 deg apart at J6, so
    # do not substitute one for the other.
    "libero": np.array(
        [0.0, -0.161037389, 0.0, -2.44459747, 0.0, 2.2267522, 0.785398]
    ),
    # robosuite's default Panda init_qpos -- NOT what LIBERO uses; kept for
    # reference so the two are not confused again.
    "robosuite": np.array([0.0, 0.196350, 0.0, -2.617994, 0.0, 2.941593, 0.785398]),
}
DEFAULT_RESET_POSE = "panda"


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
        # Raised 2026-07-28 from the original conservative defaults
        # (1.0 / 4.0 / 8.0) once the real root causes of the reflex aborts
        # (CPU governor on powersave, see scripts/runme.sh) were fixed --
        # the low defaults were masking a ~0.5-0.9s step-response lag that
        # became very noticeable ("buffering" feel) once the arm stopped
        # crashing every few seconds. Margins below libfranka 0.21.2's
        # actual per-joint hard limits (include/franka/rate_limiting.h,
        # test/fr3.urdf): kMaxJointVelocity=2.62 rad/s, kMaxJointAcceleration
        # =10.0 rad/s^2, kMaxJointJerk=5000 rad/s^3. Kept well under those
        # (not just barely under) because FR3's real velocity limit shrinks
        # near each joint's position limits and this filter applies a flat
        # cap everywhere, and because dt jitter/ZMQ latency eat into margin
        # that a pure-filter analysis doesn't account for.
        max_joint_velocity: float = 1.5,
        max_joint_acceleration: float = 6.0,
        # Below franka::kMaxJointJerk (5000) with margin. The ActiveControl API
        # does NOT run libfranka's limitRate()/lowpassFilter() -- only the
        # blocking Robot::control() path does -- so this filter is the only
        # thing keeping the command inside the robot's reflex limits.
        max_joint_jerk: float = 3000.0,
        filter_wn: float = 10.0,
        home_gripper: bool = False,
        collision_torque: Optional[list] = None,  # None -> FR3_COLLISION_TORQUE
        collision_force: float = 100.0,
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

        # Clear a leftover reflex/error state (e.g. joint_reflex from the last
        # run) so a plain restart works without touching Desk.  Harmless when
        # the robot is already Idle; fails (and only prints) when e.g. the
        # user stop is pressed -- read-only streaming still works then.
        try:
            self.robot.automatic_error_recovery()
        except Exception as e:  # noqa: BLE001
            print(f"[FR3] automatic error recovery failed: {e}")

        # Collision-reflex thresholds.  NOT the datasheet joint torque limits,
        # whose 12 N*m wrist values trip joint_reflex on mere desk contact (the
        # reflex watches estimated *external* torque; see FR3_COLLISION_TORQUE
        # for how these values are sized against the 100 N force reflex).  The
        # gripper must be able to touch the ground during teleop.  lower==upper
        # (contact report == reflex); we only care about the reflex level here.
        torque_thresh = list(collision_torque or FR3_COLLISION_TORQUE)
        self.robot.set_collision_behavior(
            torque_thresh,
            torque_thresh,
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
        # If the 1kHz control thread has died (e.g. a reflex abort), q/dq/
        # pose just stop updating forever -- silently returning that frozen
        # snapshot makes the robot look "stopped" to every caller with no
        # error anywhere (this is how a dead control loop turned into a
        # silent GUI freeze instead of a visible failure). Surface it
        # instead: raise here so it becomes a request-level error over ZMQ
        # (see zmq_core/robot_node.py), which the client already knows how
        # to catch and react to.
        if self._control_error is not None:
            raise RuntimeError(f"FR3 control loop is dead: {self._control_error}")
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
            # First tick: latch the command to the robot's own desired q_d, not
            # the measured q.  The robot-side motion generator differentiates
            # the incoming command stream starting from q_d, so any q-vs-q_d
            # gap becomes a phantom velocity step: 0.011 rad (observed after
            # hand-guiding on system 5.10.0, where q_d resyncs lazily) reads as
            # 11 rad/s in one tick and aborts with joint_motion_generator_
            # velocity/acceleration_discontinuity before teleop even starts.
            # Same convention as libfranka's own examples (q_d start pose).
            state, _ = ctrl.readOnce()
            with self._lock:
                self._q = np.asarray(state.q, dtype=float)
                q_d = np.asarray(state.q_d, dtype=float)
                gap = float(np.abs(q_d - self._q).max())
                if gap > 0.05:
                    # Impedance (3000 N*m/rad) would yank the arm toward a
                    # stale q_d.  Refuse instead; recovery/brake cycle resyncs.
                    raise RuntimeError(
                        f"stale desired pose: max|q - q_d| = {gap:.3f} rad; "
                        "run error recovery or re-open the brakes, then relaunch"
                    )
                self._q_cmd = q_d.copy()
                self._qd_cmd = np.zeros(7)
                self._desired_q = q_d.copy()
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
        """Drive the Franka Hand as a binary grasp/release state machine.

        ``move(width, speed)`` must not be used to close on objects: when the
        fingers hit an object short of the target width, the command aborts
        *without holding force* and the fingers unload and back off a little --
        the "touches the object, then reopens" failure.  The only call that
        holds force is ``grasp(width, speed, force, eps_in, eps_out)``, and it
        self-releases after ~1 s if the final width lands outside
        ``[width - eps_in, width + eps_out]``, so the window must span the
        whole stroke.  (franka_ros#130, libfranka#93.)

        The hand cannot servo width continuously anyway (~0.8 s per stroke), so
        the leader trigger (0=open .. 1=closed) is discretized with a
        hysteresis: closing edge at >= ``close_at`` issues
        ``grasp(0.0, ..., eps=0.08)`` -- success at any object width, held at
        ``grasp_force`` -- and the opening edge at <= ``open_at`` issues
        ``move`` back to full open.  Do NOT narrow the epsilons (see above).

        Commands are issued on state edges only and block this thread for up to
        a stroke.  That is fine: the GIL-release patch keeps the 1 kHz control
        thread running, and a pending edge is acted on as soon as the in-flight
        command returns.  On an exception the state is left unchanged, so the
        edge is retried while the trigger still demands it.
        """
        close_at = GRIPPER_CLOSE_AT  # trigger crossing that closes the hand ...
        open_at = 0.2      # ... and the one that reopens it (hysteresis)
        speed = 0.1        # m/s, Franka Hand max
        grasp_force = 40.0  # N holding force
        eps = 0.08         # m; success window must span the full stroke

        with self._lock:
            closed = self._gripper_target > 0.5  # match the hand's startup state
        while not self._stop.is_set():
            with self._lock:
                target = self._gripper_target
            try:
                gs = self._gripper.read_once()
                with self._lock:
                    self._gripper_state_width = float(gs.width)
            except Exception:  # noqa: BLE001
                pass
            try:
                if not closed and target >= close_at:
                    ok = self._gripper.grasp(
                        0.0, speed, grasp_force,
                        epsilon_inner=eps, epsilon_outer=eps,
                    )
                    closed = True
                    if not ok:
                        # Unreachable with a full-stroke window; if it fires,
                        # the epsilons no longer cover the stroke.
                        print("[FR3] gripper grasp reported failure")
                elif closed and target <= open_at:
                    self._gripper.move(MAX_GRIPPER_WIDTH, speed)
                    closed = False
            except Exception as e:  # noqa: BLE001
                print(f"[FR3] gripper {'grasp' if not closed else 'move'} failed: {e}")
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
