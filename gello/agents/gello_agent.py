import os
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from gello.core.agent import Agent
from gello.robots.dynamixel import DynamixelRobot
from gello.robots.franka_fr3 import FR3_Q_LOWER, FR3_Q_UPPER, GRIPPER_CLOSE_AT


@dataclass
class DynamixelRobotConfig:
    joint_ids: Sequence[int]
    """The joint ids of GELLO (not including the gripper). Usually (1, 2, 3 ...)."""

    joint_offsets: Sequence[float]
    """The joint offsets of GELLO. There needs to be a joint offset for each joint_id and should be a multiple of pi/2."""

    joint_signs: Sequence[int]
    """The joint signs of GELLO. There needs to be a joint sign for each joint_id and should be either 1 or -1.

    This will be different for each arm design. Refernce the examples below for the correct signs for your robot.
    """

    gripper_config: Tuple[int, int, int]
    """The gripper config of GELLO. This is a tuple of (gripper_joint_id, degrees in open_position, degrees in closed_position)."""

    joint_limits: Optional[Tuple[np.ndarray, np.ndarray]] = None
    """The follower's (lower, upper) joint limits (rad), arm joints only.  When
    set, GelloAgent puts a physical wall on the leader here (see JointLimitWall);
    None means no wall (the default for arms without published limits)."""

    servo_types: Optional[Sequence[str]] = None
    """One Dynamixel model name per servo actually on the bus -- joint_ids'
    arm servos AND the gripper (len == len(joint_ids) + 1), e.g.
    ("XL330_M288_T",) * 8.  Feeds DynamixelDriver's TORQUE_TO_CURRENT_MAPPING
    / SERVO_CURRENT_LIMITS; None (the default) leaves per-servo current
    clipping off entirely -- set_current() then trusts every caller (wall,
    match, gravity comp, ...) to have already clipped its own contribution."""

    gravity_gains: Optional[Sequence[float]] = None
    """Empirical per-arm-joint gravity-comp gains (current per rad, see
    JointLimitWall.set_gravity_comp) -- None/all-zero (the default) leaves
    gravity comp off, which is where it has stayed (GitHub issue #3)."""

    gravity_offsets: Optional[Sequence[float]] = None
    """Per-arm-joint angle (rad) where that joint's own gravity_gains term is
    zero -- pairs with gravity_gains; None defaults to all-zero."""

    stiction_gain: float = 0.0
    """Friction-dither amplitude as a fraction of each joint's own |tau_g| --
    see JointLimitWall.set_gravity_comp. Meaningless while gravity_gains is
    all-zero."""

    match_max_current: Optional[Sequence[float]] = None
    """Per-arm-joint current cap (mA) for the pose-match pull, or None for
    JointLimitWall's scalar default. Sized to the SUPPLY, not to any one
    servo: every armed joint can saturate simultaneously, so the worst-case
    draw is the sum of these plus the trigger spring's."""

    match_kp: Optional[Sequence[float]] = None
    """Per-arm-joint pose-match spring gain (mA/rad), or None for the wall's
    scalar default. The pull current is kp * tracking-error and the error is
    capped at match_max_lead, so kp -- not match_max_current -- is what
    actually sets each joint's pull force; pitch joints need a much stiffer
    spring than the rest (see the FR3 entry)."""

    match_int_max: Optional[Sequence[float]] = None
    """Per-arm-joint clamp (mA) on the pose-match integrator, or None to
    leave it off everywhere. The integrator learns the gravity-holding
    current of the target pose model-free (JointLimitWall docstring);
    non-zero only for joints that fight gravity."""

    budget_floor: Optional[Sequence[float]] = None
    """Per-arm-joint supply-budget floor (mA), or None for no floors. When
    the summed request exceeds the wall's current budget, each joint keeps
    up to floor_i of what it requests before the excess is scaled down --
    guarantees the pitch joints their share exactly when everything pulls
    at once."""

    def __post_init__(self):
        assert len(self.joint_ids) == len(self.joint_offsets)
        assert len(self.joint_ids) == len(self.joint_signs)

    def with_grip(self, grip: str) -> "DynamixelRobotConfig":
        """Return this config adjusted for which hand holds the handle.

        The stored calibration is for the right-handed grip; holding the handle
        left-handed turns it 180deg about the handle-roll (last) joint, so
        "left" shifts that joint's zero by pi to cancel it.  The leader then
        reads the same values in either grip -- the follower's reset pose and
        run_env.py's start gate need no left-handed variants.
        """
        if grip == "right":
            return self
        if grip != "left":
            raise ValueError(f"grip must be 'right' or 'left', got {grip!r}")
        offsets = list(self.joint_offsets)
        offsets[-1] -= np.pi
        return replace(self, joint_offsets=tuple(offsets))

    def make_robot(
        self, port: str = "/dev/ttyUSB0", start_joints: Optional[np.ndarray] = None
    ) -> DynamixelRobot:
        return DynamixelRobot(
            joint_ids=self.joint_ids,
            joint_offsets=list(self.joint_offsets),
            real=True,
            joint_signs=list(self.joint_signs),
            port=port,
            gripper_config=self.gripper_config,
            start_joints=start_joints,
            joint_limits=self.joint_limits,
            servo_types=list(self.servo_types) if self.servo_types is not None else None,
        )


PORT_CONFIG_MAP: Dict[str, DynamixelRobotConfig] = {
    # Franka GELLO (FR3 build), calibrated 2026-07-15 in pose (0, 0, 0, -pi/2, 0, pi/2, 0)
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBIN516-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6, 7),
        joint_offsets=(
            -2 * np.pi / 2,
            2 * np.pi / 2,
            4 * np.pi / 2,
            1 * np.pi / 2,
            2 * np.pi / 2,
            1 * np.pi / 2,
            # measured grid value is 2*pi/2. The +pi/4 deliberately zeroes
            # joint 7 at the square-handle grip (user decision) instead of the
            # real hand's -45deg mount orientation.  This selects the
            # right-handed grip; the left-handed grip is 180deg from it, via
            # GelloAgent(grip="left") / --grip left.  (The grips were once
            # labelled the other way around -- an earlier "- np.pi" here was
            # in fact the left-handed grip.)
            # NOTE: recalibration scripts output the pure pi/2 grid — re-apply
            # this expression manually after any recalibration.
            2 * np.pi / 2 + np.pi / 4,
        ),
        joint_signs=(1, 1, 1, -1, 1, 1, 1),
        # trigger rest position drifts (173.8-185.4 deg observed); using the
        # least-open reading so a released trigger always maps to fully open
        gripper_config=(8, 174.0, 143.8),
        joint_limits=(FR3_Q_LOWER, FR3_Q_UPPER),
        # All 8 servos (7 arm + gripper) are XL330-M288-T -- see driver.py's
        # SERVO_CURRENT_LIMITS comment (read from this hardware's own Current
        # Limit register). Enables per-servo current clipping in set_current();
        # previously unset here, so that clipping was silently a no-op for
        # this arm (see issue #3's "공통 선결 과제").
        servo_types=("XL330_M288_T",) * 8,
        # Empirical per-joint gravity comp (issue #3's "③"), all-zero/off --
        # and not a knob to reach for. The one real-hardware pass
        # (gravity_gains=(0,70,0,0,70,0,0), gravity_offsets=(0.075,0,...))
        # did not behave right and was reverted. What is suspect is the model,
        # not the numbers: it approximates each joint as an independent
        # pendulum and so cannot represent the cross-coupling a real chain has
        # (gello/robots/joint_limit_wall.py). Doing it properly the FACTR/RNEA
        # way needs a leader URDF, and no CAD for this arm exists. The servos
        # are the other half -- all eight are XL330-M288-T, and match_max_current
        # below already has to ration 3.5 A of a 4 A supply across seven joints.
        # Both are prerequisites in issue #3; more tuning is not.
        gravity_gains=(0.0,) * 7,
        gravity_offsets=(0.0,) * 7,
        stiction_gain=0.0,
        # Pose-match current budget, split across the 4 A supply rather than
        # given to each joint independently -- all seven can saturate at the
        # same instant, so what matters is the sum. J2/J4 are the pitch joints
        # carrying the arm's weight against gravity and get 1 A each; the
        # rest only have to overcome their own friction. Caps may sum past
        # the wall's 2.8 A budget: the budget_floor allocation below scales
        # the non-pitch joints down first when they actually collide.
        # (2026-08-27 사용자 확정: 전반 상향 300->400, pitch 강화)
        match_max_current=(400.0, 1000.0, 400.0, 1000.0, 400.0, 400.0, 400.0),
        # kp is the real force knob (pull = kp * error, error capped at
        # match_max_lead 0.35 rad): 600 -> 210 mA max for friction-only
        # joints, 2800 -> ~980 mA for the pitch joints, meeting their cap.
        match_kp=(600.0, 2800.0, 600.0, 2800.0, 600.0, 600.0, 600.0),
        # Pitch-only integrator: learns the gravity-holding current at the
        # target pose (model-free), so J2/J4 converge instead of sagging
        # just outside match_tol. Clamped well under their 1 A caps.
        # 2026-09-01: 피치 외 관절에도 적분기를 준다. 스프링만으로는 마찰을
        # 이기지 못해 목표 근처에서 오차가 남았다 -- "정렬이 완벽하지 않다" 는
        # 관측이 이것이다. 적분기는 그 남은 오차를 시간에 걸쳐 없앤다.
        # 200 mA 는 이 관절들의 캡(400)의 절반이라 스프링 몫이 남고, 도달하지
        # 못한 채 감기면 wall 의 포화 감시(캡의 90% 4초)가 정렬을 포기시킨다.
        match_int_max=(200.0, 800.0, 200.0, 800.0, 200.0, 200.0, 200.0),
        # J2/J4 keep their first 1 A of request when the supply budget
        # saturates -- 사용자 요구: pitch 는 최소 1 A 유지.
        budget_floor=(0.0, 1000.0, 0.0, 1000.0, 0.0, 0.0, 0.0),
    ),
    # xArm
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT3M9NVB-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6, 7),
        joint_offsets=(
            3 * np.pi / 2,
            2 * np.pi / 2,
            1 * np.pi / 2,
            4 * np.pi / 2,
            -2 * np.pi / 2 + 2 * np.pi,
            3 * np.pi / 2,
            4 * np.pi / 2,
        ),
        joint_signs=(1, -1, 1, 1, 1, -1, 1),
        gripper_config=(8, 195, 152),
    ),
    # yam
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTA2U4GA-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6),
        joint_offsets=[
            0 * np.pi,
            2 * np.pi / 2,
            4 * np.pi / 2,
            6 * np.pi / 6,
            5 * np.pi / 3,
            2 * np.pi / 2,
        ],
        joint_signs=(1, -1, -1, -1, 1, 1),
        gripper_config=(
            7,
            -30,
            24,
        ),  # Reversed: now starts open (-30) and closes on press (24)
    ),
    # Left UR
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT7WBEIA-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6),
        joint_offsets=(
            0,
            1 * np.pi / 2 + np.pi,
            np.pi / 2 + 0 * np.pi,
            0 * np.pi + np.pi / 2,
            np.pi - 2 * np.pi / 2,
            -1 * np.pi / 2 + 2 * np.pi,
        ),
        joint_signs=(1, 1, -1, 1, 1, 1),
        gripper_config=(7, 20, -22),
    ),
    # Right UR
    "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FT7WBG6A-if00-port0": DynamixelRobotConfig(
        joint_ids=(1, 2, 3, 4, 5, 6),
        joint_offsets=(
            np.pi + 0 * np.pi,
            2 * np.pi + np.pi / 2,
            2 * np.pi + np.pi / 2,
            2 * np.pi + np.pi / 2,
            1 * np.pi,
            3 * np.pi / 2,
        ),
        joint_signs=(1, 1, -1, 1, 1, 1),
        gripper_config=(7, 286, 248),
    ),
}


class GelloAgent(Agent):
    def __init__(
        self,
        port: str,
        dynamixel_config: Optional[DynamixelRobotConfig] = None,
        start_joints: Optional[np.ndarray] = None,
        enable_wall: bool = True,
        grip: str = "right",
    ):
        # Ensure start_joints is a numpy array if provided
        if start_joints is not None and not isinstance(start_joints, np.ndarray):
            start_joints = np.array(start_joints)
        if dynamixel_config is not None:
            config = dynamixel_config
        else:
            assert os.path.exists(port), port
            assert port in PORT_CONFIG_MAP, f"Port {port} not in config map"
            config = PORT_CONFIG_MAP[port]
        config = config.with_grip(grip)
        # make_robot receives config.joint_limits: when set, get_joint_state
        # fixes any phantom full turn in the leader reading (see
        # wrap_into_limits), before smoothing, so a joint whose calibrated value
        # lands 2*pi out of range does not fail the start gate every power-up.
        self._robot = config.make_robot(port=port, start_joints=start_joints)

        # Optional joint-limit wall on the leader.  Only when the config carries
        # the follower's limits (e.g. the FR3 GELLO); other arms keep their
        # current behaviour untouched.  Shares the robot's driver -- the port is
        # exclusive, so a separate wall process is impossible.
        self._wall = None
        if enable_wall and config.joint_limits is not None:
            from gello.robots.joint_limit_wall import JointLimitWall

            lower, upper = config.joint_limits
            n_arm = len(config.joint_ids)
            # Use the robot's *resolved* offsets/signs: start_joints can shift
            # the offsets in DynamixelRobot.__init__, and the wall must land
            # where the follower's limit actually is.
            self._wall = JointLimitWall(
                self._robot._driver,
                lower,
                upper,
                offsets=self._robot._joint_offsets,
                signs=self._robot._joint_signs,
                n_arm=n_arm,
                # Trigger spring: auto-open return plus an exponential squeeze
                # wall starting exactly where the follower's binary gripper
                # closes, so resistance onset == grasp.
                gripper_open_close=self._robot.gripper_open_close,
                trigger_start=GRIPPER_CLOSE_AT,
                gravity_gains=config.gravity_gains,
                gravity_offsets=config.gravity_offsets,
                stiction_gain=config.stiction_gain,
                **{
                    k: np.asarray(v, dtype=float)
                    for k, v in (
                        ("match_max_current", config.match_max_current),
                        ("match_kp", config.match_kp),
                        ("match_int_max", config.match_int_max),
                        ("budget_floor", config.budget_floor),
                    )
                    if v is not None
                },
            )
            self._wall.start()
        elif enable_wall:
            print("[wall] no joint_limits for this port; leader wall disabled")

    def act(self, obs: Dict[str, np.ndarray]) -> np.ndarray:
        if self._wall is not None:
            self._wall.poll()  # re-raises if the wall thread died -> teleop dies
        return self._robot.get_joint_state()

    # ------------------------------------------------------- pose-match assist
    def set_teleop_mode(self, in_teleop: bool) -> None:
        """Forward teleop/alignment mode to the leader wall (issue #37A).

        No-op if the wall is disabled for this port.
        """
        if self._wall is not None:
            self._wall.set_teleop_mode(in_teleop)

    def start_pose_match(self, target_q: np.ndarray) -> None:
        """Begin auto-pulling the leader's arm joints onto ``target_q``
        (follower joint space, arm joints only -- same space ``act()``
        returns), via the joint-limit wall's current-writer thread. Raises if
        there is no wall (``enable_wall=False``, or this port has no
        ``joint_limits``): there is deliberately no separate control loop for
        this, since a second thread calling ``set_current`` on the same bus
        would fight the wall's.
        """
        if self._wall is None:
            raise RuntimeError(
                "pose-match assist requires the leader's joint-limit wall "
                "(enable_wall=True and joint_limits configured for this port)"
            )
        self._wall.set_match_target(np.asarray(target_q, dtype=float))

    def pose_match_status(self) -> Dict[str, Any]:
        """``{"error": max abs rad or None, "done", "engaged", "blocked", "state"}``.

        ``done`` is always True (nothing to wait for) when there is no wall
        or no match is in progress. ``engaged`` says whether the pull is
        actually energized -- the wall only engages inside its gate, so a
        match can be "in progress" while the leader is still being carried
        into range (issue #37A). ``blocked`` means the wall gave the pull up
        to protect the servos (a joint sat at its current cap); it clears on
        the next ``start_pose_match``. ``aborted_wrap`` means it gave up for
        the opposite reason -- a joint reached the point where the wall cannot
        tell which way to push, so that joint was handed to the operator
        (typically to untwist a cable). ``state`` is the explicit enum value
        from the wall (idle/armed/pulling/blocked/done); new code should
        prefer this over the boolean fields.
        """
        if self._wall is None:
            return {"error": None, "done": True,
                    "engaged": False, "blocked": False,
                    "aborted_wrap": False, "state": "idle"}
        s = self._wall.status()
        return {"error": s.get("match_error"), "done": bool(s.get("match_done")),
                "engaged": bool(s.get("match_engaged")),
                "blocked": bool(s.get("match_blocked")),
                # 케이블을 푸는 중으로 보고 wall 이 스스로 취소했는가
                "aborted_wrap": bool(s.get("match_aborted_wrap")),
                "state": s.get("match_state", "idle")}

    def cancel_pose_match(self) -> None:
        """Release the match target early (abort/interrupt). No-op if there
        is no wall or nothing was in progress."""
        if self._wall is not None:
            self._wall.set_match_target(None)

    # --------------------------------------------------------- gravity comp
    def set_gravity_comp(
        self,
        gains: Optional[np.ndarray] = None,
        offsets: Optional[np.ndarray] = None,
        stiction_gain: Optional[float] = None,
    ) -> None:
        """Live-adjust the empirical gravity-comp model (see JointLimitWall's
        docstring). Raises if there is no wall, same reasoning as
        start_pose_match."""
        if self._wall is None:
            raise RuntimeError(
                "gravity comp requires the leader's joint-limit wall "
                "(enable_wall=True and joint_limits configured for this port)"
            )
        self._wall.set_gravity_comp(gains=gains, offsets=offsets, stiction_gain=stiction_gain)

    def gravity_status(self) -> Dict[str, Any]:
        """``{"tau_g": per-joint current or None, "cur": total per-joint
        current or None, "armed": bool, "q": ..., "dq": ...}`` straight from
        the wall's latest tick; all-``None``/``False`` when there is no
        wall."""
        if self._wall is None:
            return {"tau_g": None, "cur": None, "armed": False, "q": None, "dq": None}
        s = self._wall.status()
        return {
            "tau_g": s.get("tau_g"),
            "cur": s.get("cur"),
            "armed": s.get("armed", False),
            "q": s.get("q"),
            "dq": s.get("dq"),
        }

    def close(self) -> None:
        """Clean shutdown: stops the leader wall (no-op if there is none)
        and releases the underlying Dynamixel serial port, so a later
        reconnect in this same process doesn't find the port still open."""
        if self._wall is not None:
            self._wall.stop()
            self._wall = None
        self._robot.close()
