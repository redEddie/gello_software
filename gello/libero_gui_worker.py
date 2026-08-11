"""Background QThread driving GELLO teleop + LIBERO-format recording for the PyQt GUI.

Ports the proven control flow from ``experiments/record_dataset.py`` (home ->
pose-gate -> approach-ramp -> record, plus robot-node death/reconnect
handling) from its blocking-loop/KeyPoller CLI shape into a
command-queue-in / Qt-signal-out worker thread, so a GUI can drive it without
freezing on robot I/O. The state machine and constants (``GATE_RAD``,
``RAMP_STEP``) are unchanged from that script; only the I/O boundary moved.

Run inside ``lerobot-venv`` (has ``gello``, ``dynamixel-sdk``, ``pyrealsense2``,
``lerobot``). Requires ``experiments/launch_nodes.py --robot fr3`` already
running in ``pylibfranka-venv``.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import zmq
from PyQt6.QtCore import QThread, pyqtSignal

from gello.dataset_schema import DatasetSchemaConfig
from gello.lerobot_plugin import (
    JOINT_KEYS,
    FR3ZMQRobot,
    FR3ZMQRobotConfig,
    GelloFR3Teleop,
    GelloFR3TeleopConfig,
)
from gello.libero_format import LiberoTaskWriter, NullTaskWriter
from gello.robots.franka_fr3 import FR3_RESET_POSES
from gello.station import load_station

GATE_RAD = 0.5  # run_env.py / gello_match_pose.py's start-gate threshold
# rad/tick @ 20Hz. The FR3 driver's reference filter saturates at 1.0 rad/s
# regardless, so this only has to be large enough not to be the binding
# constraint -- 0.10 lets the filter reach ~0.91 rad/s (0.05 gave ~0.80).
RAMP_STEP = 0.10
GRIPPER_OPEN = 0.0  # GELLO/franka_fr3 convention: 0=open, 1=closed

# ---- EE 경로 homing ----
# 관절 직선 보간 homing 은 파지 직후처럼 EE 가 낮을 때 베이스가 돌면서
# 테이블 높이를 수평으로 쓸고 지나간다. 대신 "수직으로 들어올린 뒤 홈 EE
# 포즈까지 직선" 경로를 IK 로 풀어 관절 웨이포인트를 만든다. IK 실패나
# 관절 점프가 크면 기존 관절 램프로 폴백 -- homing 이 안 되는 것보다는
# 예전처럼 무섭게라도 돌아가는 쪽이 낫다.
HOME_LIFT_M = 0.10       # 1단계: 현재 포즈에서 수직 리프트 높이
HOME_EE_STEP_M = 0.010   # tick 당 EE 이동 (20Hz -> 0.2 m/s)
HOME_ROT_STEP_RAD = 0.05  # tick 당 EE 회전 (20Hz -> 1.0 rad/s)
HOME_MAX_DQ = 0.35       # 연속 웨이포인트 관절 점프 상한 -- 초과 시 폴백

# Fallback defaults, used only if the GUI doesn't supply a serial (e.g. a
# script driving CollectionWorker directly). The GUI itself always populates
# WorkerConfig.agent_camera_serial / wrist_camera_serial from a live device
# scan (see experiments/collect_workspace.py's agent_combo/wrist_combo),
# since serials change
# whenever a camera is swapped.
_STATION = load_station()
AGENT_CAMERA_SERIAL = _STATION.camera("agent").serial
WRIST_CAMERA_SERIAL = _STATION.camera("wrist").serial


class EpisodeSaver(QThread):
    """Owns ALL h5py-file-touching writer calls, serialized through one queue.

    h5py is not thread-safe, so once this thread starts, save_buffer /
    delete_episode / list_episodes go ONLY through here. The worker keeps
    buffer-only calls (add_frame / start_episode / discard_episode) and hands
    a detached buffer over for saving -- recording the next episode overlaps
    with compressing/writing the previous one, so homing/preview never stall
    on an episode commit.
    """

    episode_saved = pyqtSignal(str, int)      # demo_name, n_frames
    episode_list_changed = pyqtSignal(list)
    save_status = pyqtSignal(str)             # ""=idle; else short status text
    log_message = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._writer = None  # set by CollectionWorker.run() before start()
        self._q: "queue.Queue[tuple]" = queue.Queue()

    def set_writer(self, writer) -> None:
        self._writer = writer

    def enqueue_save(self, buf, success) -> None:
        self._q.put(("save", buf, success))

    def enqueue_delete(self, name: str) -> None:
        self._q.put(("delete", name))

    def enqueue_set_success(self, name: str, success: bool) -> None:
        """Re-label an already-saved episode. Goes through the same queue as
        the save itself, so a toggle sent while that save is still running is
        applied after it rather than racing it."""
        self._q.put(("set_success", name, success))

    def finish(self) -> None:
        """Drain the queue, then exit run(). Caller must wait() afterwards."""
        self._q.put(("stop",))

    def run(self) -> None:
        while True:
            item = self._q.get()
            if item[0] == "stop":
                break
            try:
                if item[0] == "save":
                    _, buf, success = item
                    n = len(buf)
                    waiting = self._q.qsize()
                    self.save_status.emit(
                        f"저장 중... {n}프레임" + (f" (+대기 {waiting})" if waiting else ""))
                    t0 = time.monotonic()
                    name = self._writer.save_buffer(buf, success=success)
                    dt = time.monotonic() - t0
                    if name:
                        self.episode_saved.emit(name, n)
                        self.log_message.emit(f"[저장] {name} ({n} 프레임, {dt:.1f}s, 백그라운드)")
                    self.episode_list_changed.emit(self._writer.list_episodes())
                    self.save_status.emit("")
                elif item[0] == "delete":
                    name = item[1]
                    self._writer.delete_episode(name)
                    self.log_message.emit(f"[삭제] {name}")
                    self.episode_list_changed.emit(self._writer.list_episodes())
                elif item[0] == "set_success":
                    _, name, success = item
                    self._writer.set_episode_success(name, success)
                    self.log_message.emit(
                        f"[판정] {name} -> {'성공' if success else '실패'}")
                    self.episode_list_changed.emit(self._writer.list_episodes())
            except Exception as e:  # noqa: BLE001
                self.log_message.emit(f"[저장 스레드 오류] {type(e).__name__}: {e}")
                self.save_status.emit("")


@dataclass
class WorkerConfig:
    task_name: str
    language_instruction: str
    data_root: str
    robot_port: int = _STATION.node.port
    hostname: str = _STATION.node.host
    grip: str = "right"
    reset_pose: str = "libero"
    fps: int = _STATION.fps
    max_episode_seconds: float = 20.0
    reset_wait_seconds: float = 10.0
    enable_wall: bool = True
    # True: teleoperate without creating any .hdf5 at all -- scene setup,
    # camera framing, letting someone try the leader. Everything else behaves
    # identically (pose gate, live view, frame counter); saving is accepted
    # and dropped. See gello/libero_format.py's NullTaskWriter.
    no_dataset: bool = False
    # True: pull the leader onto the follower's reset pose at the start of
    # every episode, so each one begins from an identical joint configuration.
    # False: the operator aligns by hand, so the starting pose varies
    # episode-to-episode -- deliberate variation, not sloppiness.
    auto_match_pose: bool = True
    resume: bool = False
    agent_camera_serial: str = AGENT_CAMERA_SERIAL
    wrist_camera_serial: str = WRIST_CAMERA_SERIAL
    schema: DatasetSchemaConfig = field(default_factory=DatasetSchemaConfig)
    # 카메라별 정사각 크롭 정렬 (GUI Layout 페이지에서 조정). None 이면 기본값.
    # 에피소드마다 attrs["crop_params"] 로 찍힌다.
    crop_params: dict | None = None


class CollectionWorker(QThread):
    state_changed = pyqtSignal(str)
    frames_ready = pyqtSignal(object, object)  # agentview_rgb, eye_in_hand_rgb (np.ndarray)
    gate_status = pyqtSignal(object, object, bool)  # leader(8,), follower(8,), all_ok
    pose_match_status = pyqtSignal(float, bool)  # max joint error (rad), done
    episode_progress = pyqtSignal(int, float)  # n_frames, seconds
    episode_saved = pyqtSignal(str, int)  # demo_name, n_frames
    episode_discarded = pyqtSignal(int)  # n_frames
    reset_countdown = pyqtSignal(float)  # seconds remaining
    log_message = pyqtSignal(str)
    node_status = pyqtSignal(bool)  # True=ok, False=down
    fatal_error = pyqtSignal(str)
    connected = pyqtSignal(int, str)  # starting episode count, active .hdf5 path
    episode_list_changed = pyqtSignal(list)  # LiberoTaskWriter.list_episodes()
    session_summary = pyqtSignal(dict)  # emitted once, right before the file closes

    def __init__(self, config: WorkerConfig) -> None:
        super().__init__()
        self.cfg = config
        self._cmds: "queue.Queue[tuple]" = queue.Queue()
        self._running = True
        self._robot: Optional[FR3ZMQRobot] = None
        self._teleop: Optional[GelloFR3Teleop] = None
        self._writer: Optional[LiberoTaskWriter] = None
        self._reset_q = FR3_RESET_POSES[self.cfg.reset_pose]
        self._episode_count = 0
        # GUI 스레드에서 시그널을 미리 connect할 수 있도록 여기서 생성;
        # writer 주입/start()는 run()에서 (h5py 접근 직렬화는 saver가 소유).
        self.saver = EpisodeSaver()
        # Stale-frame bookkeeping, see _get_obs.
        self._cam_last_fp: dict = {}
        self._cam_stale: dict = {}
        self._cam_stale_run: dict = {}
        self._cam_stale_max_run: dict = {}

    # ------------------------------------------------------------------ API
    # Called from the GUI (main) thread; safe because queue.Queue is thread-safe.
    def cmd_start_teleop(self) -> None:
        self._cmds.put(("start_teleop",))

    def cmd_auto_match_pose(self) -> None:
        self._cmds.put(("auto_match_pose",))

    def cmd_save_episode(self, success: Optional[bool]) -> None:
        self._cmds.put(("save_episode", success))

    def cmd_discard_episode(self) -> None:
        self._cmds.put(("discard_episode",))

    def cmd_skip_reset_wait(self) -> None:
        self._cmds.put(("skip_reset_wait",))

    def cmd_quit(self) -> None:
        self._cmds.put(("quit",))

    def cmd_go_home(self) -> None:
        self._cmds.put(("go_home",))

    def cmd_set_episode_success(self, name: str, success: bool) -> None:
        self.saver.enqueue_set_success(name, success)

    def cmd_delete_episode(self, name: str) -> None:
        self._cmds.put(("delete_episode", name))

    def current_schema(self) -> Optional[DatasetSchemaConfig]:
        """The effective schema this session's writer is currently using,
        or None if not connected yet. ``DatasetSchemaConfig`` is a plain,
        immutable-after-construction dataclass (never mutated once the
        writer is built), so reading it from the GUI thread is safe --
        unlike the writer's open h5py.File, which only this worker thread
        may touch (see _handle_delete_episode's docstring)."""
        return self._writer.schema if self._writer is not None else None

    # --------------------------------------------------------------- helpers
    def _handle_delete_episode(self, name: str) -> None:
        """File-touching ops are serialized on the saver thread (h5py is not
        thread-safe); pending saves queued before this delete commit first."""
        self.saver.enqueue_delete(name)

    def _poll_cmd(self, block: bool = False, timeout: float = 0.0) -> Optional[tuple]:
        """Pops queued commands, servicing ``delete_episode`` inline (it doesn't
        belong to any particular state), and returns the newest remaining
        state-machine command (start_teleop/save/discard/skip/quit), if any.
        """
        result = None
        try:
            while True:
                cmd = self._cmds.get(block=block, timeout=timeout)
                block = False  # only the first get() honors block/timeout
                if cmd[0] == "delete_episode":
                    self._handle_delete_episode(cmd[1])
                    continue
                result = cmd  # last one wins if several piled up
        except queue.Empty:
            pass
        return result

    def _drain_interrupt(self, react_to_go_home: bool = True) -> Optional[str]:
        """Non-blocking: services ``delete_episode`` inline, reports whether
        ``quit`` or ``go_home`` was queued (``quit`` wins if both arrived).
        Any other command type is meaningless mid-ramp and is intentionally
        dropped (the GUI disables those buttons during ramps).

        ``react_to_go_home=False`` is for the ramp that's already heading
        home (the top-of-loop homing ramp, and the final teardown ramp) --
        a go_home click there is a no-op, not an abort, since we're already
        doing what it asked for.
        """
        quit_seen = False
        go_home_seen = False
        try:
            while True:
                cmd = self._cmds.get_nowait()
                if cmd[0] == "delete_episode":
                    self._handle_delete_episode(cmd[1])
                elif cmd[0] == "quit":
                    quit_seen = True
                elif cmd[0] == "go_home":
                    go_home_seen = True
        except queue.Empty:
            pass
        if quit_seen:
            return "quit"
        if go_home_seen and react_to_go_home:
            return "go_home"
        return None

    def _drain_match_interrupt(self) -> Optional[str]:
        """Like ``_drain_interrupt``, but for ``_auto_match_pose``'s loop
        specifically: a queued ``start_teleop`` there is NOT meaningless --
        the operator is allowed to start teleop mid-align (see issue #8
        follow-up), which aborts the pull rather than silently dropping the
        click. ``quit``/``go_home`` still win over a same-batch
        ``start_teleop`` (same precedence as ``_drain_interrupt``).
        """
        quit_seen = False
        go_home_seen = False
        start_seen = False
        try:
            while True:
                cmd = self._cmds.get_nowait()
                if cmd[0] == "delete_episode":
                    self._handle_delete_episode(cmd[1])
                elif cmd[0] == "quit":
                    quit_seen = True
                elif cmd[0] == "go_home":
                    go_home_seen = True
                elif cmd[0] == "start_teleop":
                    start_seen = True
        except queue.Empty:
            pass
        if quit_seen:
            return "quit"
        if go_home_seen:
            return "go_home"
        if start_seen:
            return "start_teleop"
        return None

    def _emit_frames(self, obs: dict) -> None:
        agent = obs.get("agent")
        wrist = obs.get("wrist")
        if agent is not None and wrist is not None:
            self.frames_ready.emit(agent, wrist)

    def _joint_vec(self, d: dict) -> np.ndarray:
        return np.array([d[k] for k in JOINT_KEYS], dtype=float)

    def _get_obs(self) -> dict:
        """Like ``FR3ZMQRobot.get_observation()`` but also carries ``ee_pos_quat``
        and ``joint_velocities``.

        The lerobot-facing ``get_observation()`` only forwards the
        ``JOINT_KEYS``-shaped dict (it feeds LeRobot's ``observation_features``
        schema, which record_dataset.py relies on and this module must not
        change). The LIBERO writer's optional fields need the Cartesian pose
        and joint velocities too, so this pulls the same raw ZMQ observation
        once and keeps all of it -- joint_velocities is cheap (the control
        loop already computes it every tick) and only gets buffered/written
        if the active DatasetSchemaConfig asks for it.
        """
        raw = self._robot._client.get_observations()
        pos = np.asarray(raw["joint_positions"], dtype=float)
        out: dict = dict(zip(JOINT_KEYS, pos.tolist()))
        out["_ee_pos_quat"] = np.asarray(raw["ee_pos_quat"], dtype=float)
        out["_joint_velocities"] = np.asarray(raw["joint_velocities"], dtype=float)
        for cam_key, cam in self._robot.cameras.items():
            frame = cam.read_latest()
            # read_latest() is non-blocking by design: it hands back whatever
            # is in the buffer and only raises once that is older than
            # max_age_ms (500 by default). At 20 Hz that means a stalled
            # camera silently repeats the SAME image for up to ten ticks while
            # the joint states beside it keep updating -- a frozen wrist view
            # paired with a moving arm, which is exactly the temporal
            # misalignment a policy must not be trained on. Nothing upstream
            # reports it, so count it here: identical consecutive frames are
            # tallied per camera and surfaced with the episode.
            fp = hash(frame[::37, ::37].tobytes())
            if fp == self._cam_last_fp.get(cam_key):
                self._cam_stale[cam_key] = self._cam_stale.get(cam_key, 0) + 1
                run = self._cam_stale_run.get(cam_key, 0) + 1
                self._cam_stale_run[cam_key] = run
                self._cam_stale_max_run[cam_key] = max(
                    run, self._cam_stale_max_run.get(cam_key, 0)
                )
                # One repeat is a rounding artifact; a run of them is a stall.
                if run == 3:
                    self.log_message.emit(
                        f"[카메라] {cam_key} 프레임이 {run}틱 연속 동일 -- "
                        "정지(stall) 의심, 이 에피소드는 버리는 것을 고려하세요"
                    )
            else:
                self._cam_stale_run[cam_key] = 0
            self._cam_last_fp[cam_key] = fp
            out[cam_key] = frame
        return out

    # ------------------------------------------------------------------ ramp
    def _ramp_to(
        self, target_q: np.ndarray, max_ticks: int = 600, react_to_go_home: bool = True
    ) -> str:
        """Returns "ok", "quit", or "go_home" (go_home only possible when
        react_to_go_home). Running out of ticks without converging is
        treated like "quit" -- same abort-the-session behavior as before.

        Always commands the gripper OPEN (GRIPPER_OPEN), not whatever it
        currently is: this is the homing/reset ramp (called at the top of
        every loop iteration and during final teardown), so an episode that
        ended with the gripper closed must not leave it closed through
        reset -- it previously echoed back obs["gripper.pos"] every tick,
        which is a no-op command (send whatever it already is), so a closed
        gripper just silently stayed closed through "homing". The command
        only needs to be set once -- FrankaFR3Robot's gripper thread drives
        toward _gripper_target continuously in the background, independent
        of whether new joint commands keep arriving -- but resending it
        every tick here is harmless and keeps this loop's shape unchanged.
        """
        q_cmd = None
        for _ in range(max_ticks):
            interrupt = self._drain_interrupt(react_to_go_home=react_to_go_home)
            if interrupt:
                return interrupt
            obs = self._get_obs()
            q = np.array([obs[k] for k in JOINT_KEYS[:7]])
            if np.abs(target_q - q).max() < 0.02:
                return "ok"
            # Integrate the *commanded* position instead of re-anchoring it to
            # the measured one each tick (see _advance_cmd).
            if q_cmd is None:
                q_cmd = q.copy()
            q_cmd = self._advance_cmd(q_cmd, target_q)
            cmd = dict(zip(JOINT_KEYS, np.append(q_cmd, GRIPPER_OPEN).tolist()))
            self._robot.send_action(cmd)
            self._emit_frames(obs)
            time.sleep(0.05)
        return "quit"

    def _home_trajectory(self, q_now: np.ndarray) -> "list[np.ndarray] | None":
        """수직 +HOME_LIFT_M 리프트 -> 홈 EE 포즈 직선의 관절 웨이포인트.

        tick 당 하나씩 실행되도록 EE 스텝 크기로 샘플링하고, IK 는 직전 해를
        시드로 체인해 자세(널스페이스)가 튀지 않게 한다. 마지막 웨이포인트의
        EE 는 홈 포즈와 같지만 관절은 reset_q 와 널스페이스만큼 다를 수 있다
        -- 호출자가 끝에서 _ramp_to(reset_q) 로 수렴시킨다 (EE 는 이미 홈이라
        그 잔차 이동은 제자리 자세 조정이다).

        None 반환 = 만들 수 없음(임포트 실패, IK 발산, 관절 점프 초과).
        """
        try:
            # fr3_kinematics 는 experiments/ 에 있다. GUI/클라이언트 모두
            # experiments/ 의 스크립트로 실행되어 sys.path 에 이미 있다.
            import fr3_kinematics as K
        except ImportError:
            return None
        try:
            q = np.asarray(q_now, dtype=np.float64).copy()
            T_now = K.fk(q)
            T_home = K.fk(np.asarray(self._reset_q, dtype=np.float64))
            wps: list = []

            def _solve(T_target: np.ndarray, q_seed: np.ndarray):
                q_next = K.ik(T_target, q_seed)
                T_got = K.fk(q_next)
                # IK 미수렴(잔차 5mm 초과)이나 큰 관절 점프는 실패로 취급
                if np.linalg.norm(T_got[:3, 3] - T_target[:3, 3]) > 0.005:
                    return None
                if np.abs(q_next - q_seed).max() > HOME_MAX_DQ:
                    return None
                return q_next

            # 1단계: 수직 리프트 (자세 유지, z 만 상승)
            n_lift = max(1, int(np.ceil(HOME_LIFT_M / HOME_EE_STEP_M)))
            for i in range(1, n_lift + 1):
                T = T_now.copy()
                T[2, 3] = T_now[2, 3] + HOME_LIFT_M * i / n_lift
                q = _solve(T, q)
                if q is None:
                    return None
                wps.append(q)

            # 2단계: 리프트 포즈 -> 홈 포즈 직선 (위치 lerp + 회전 slerp)
            T_lift = T_now.copy()
            T_lift[2, 3] += HOME_LIFT_M
            p0, p1 = T_lift[:3, 3], T_home[:3, 3]
            R0 = T_lift[:3, :3]
            aa = K._rot_to_axis_angle(T_home[:3, :3] @ R0.T)
            n = max(1,
                    int(np.ceil(np.linalg.norm(p1 - p0) / HOME_EE_STEP_M)),
                    int(np.ceil(np.linalg.norm(aa) / HOME_ROT_STEP_RAD)))
            for i in range(1, n + 1):
                s = i / n
                T = np.eye(4)
                T[:3, 3] = p0 + (p1 - p0) * s
                T[:3, :3] = K.axis_angle_to_rot(aa * s) @ R0
                q = _solve(T, q)
                if q is None:
                    return None
                wps.append(q)
            return wps
        except Exception:  # noqa: BLE001 - 어떤 실패든 폴백이 정답
            return None

    def _ramp_home(self, max_ticks: int = 600, react_to_go_home: bool = True) -> str:
        """EE 경로(리프트 -> 직선) homing. 실패 시 기존 관절 램프로 폴백.

        반환 계약은 _ramp_to 와 동일: "ok" / "quit" / "go_home".
        """
        obs = self._get_obs()
        q_now = np.array([obs[k] for k in JOINT_KEYS[:7]])
        if np.abs(self._reset_q - q_now).max() < 0.02:
            return "ok"
        wps = self._home_trajectory(q_now)
        if wps is None:
            self.log_message.emit(
                "[HOME] EE 경로 생성 실패 -- 관절 램프로 폴백합니다")
            return self._ramp_to(self._reset_q, max_ticks=max_ticks,
                                 react_to_go_home=react_to_go_home)
        for q_cmd in wps:
            interrupt = self._drain_interrupt(react_to_go_home=react_to_go_home)
            if interrupt:
                return interrupt
            obs = self._get_obs()
            cmd = dict(zip(JOINT_KEYS, np.append(q_cmd, GRIPPER_OPEN).tolist()))
            self._robot.send_action(cmd)
            self._emit_frames(obs)
            time.sleep(0.05)
        # EE 는 홈 포즈에 도착. 남은 널스페이스/추종 잔차를 관절 램프로 수렴.
        return self._ramp_to(self._reset_q, max_ticks=max_ticks,
                             react_to_go_home=react_to_go_home)

    @staticmethod
    def _advance_cmd(q_cmd: np.ndarray, target_q: np.ndarray) -> np.ndarray:
        """Move the commanded position one ``RAMP_STEP`` toward ``target_q``.

        Both ramps used to command ``measured + clip(target - measured)``,
        re-anchoring to the encoder every tick. That looks like it asks for
        RAMP_STEP/dt = 1.0 rad/s, but the follower sits behind a
        critically-damped reference filter (``franka_fr3.py``): the filter
        only closes part of a 0.05 rad gap per tick, and re-anchoring throws
        away the rest instead of letting the target run ahead. Simulating the
        real filter, the arm actually crept at **0.23 rad/s** -- a 1 rad move
        took 4.4 s. Integrating the command instead lets the filter saturate
        at its own limit and the same move takes 1.25 s (0.80 rad/s), a 3.5x
        speedup with no change to what the driver is allowed to do.

        (Same failure mode as the action-space bug: never feed a low-pass
        filter its own output back as the setpoint.)
        """
        return q_cmd + np.clip(target_q - q_cmd, -RAMP_STEP, RAMP_STEP)

    def _approach_ramp(self, timeout: float = 3600.0) -> str:
        """Blocks (emitting frames) until the follower actually reaches the
        leader's pose. Returns "ok", "quit", or "go_home".

        Used to give up after a fixed 100 ticks (5s) and proceed to
        recording regardless of whether it had actually converged -- if the
        leader had drifted since the pose gate (GATE_RAD there is a loose
        0.5 rad) or the operator kept moving, recording could start well
        before the follower caught up, which looked like "the robot is
        still slowly catching up right after recording starts, so you have
        to hold still." Waiting for a real convergence (like _pose_gate
        already does) means recording never starts out of sync; the
        `timeout` is just a safety backstop, not a normal exit path.
        """
        deadline = time.monotonic() + timeout
        last_log = 0.0
        q_cmd = None
        while True:
            interrupt = self._drain_interrupt()
            if interrupt:
                return interrupt
            obs = self._get_obs()
            act = self._teleop.get_action()
            q_rob = np.array([obs[k] for k in JOINT_KEYS[:7]])
            q_led = np.array([act[k] for k in JOINT_KEYS[:7]])
            d = q_led - q_rob
            self._emit_frames(obs)
            if np.abs(d).max() < RAMP_STEP:
                return "ok"
            # Integrated command, not measured+step -- see _advance_cmd. The
            # target here is live (the operator may still be moving), so the
            # commanded position is re-seeded from measurement only on entry.
            if q_cmd is None:
                q_cmd = q_rob.copy()
            q_cmd = self._advance_cmd(q_cmd, q_led)
            cmd = dict(zip(JOINT_KEYS, np.append(q_cmd, act["gripper.pos"]).tolist()))
            self._robot.send_action(cmd)
            now = time.monotonic()
            if now - last_log > 2.0:
                last_log = now
                self.log_message.emit(f"[접근] 리더에 맞추는 중 (최대 차이 {np.abs(d).max():.2f} rad)")
            if now > deadline:
                self.log_message.emit(f"[접근] {timeout:.0f}s 시간 초과 -- 세션 종료")
                return "quit"
            time.sleep(0.05)

    # ------------------------------------------------------------------ gate
    def _emit_gate_status(self) -> tuple[np.ndarray, bool]:
        """Reads leader+follower, emits frames + gate_status (drives the
        GUI's live per-joint delta bars), and returns (delta, all_ok) for the
        caller's own branching. Shared by _pose_gate's main loop and
        _auto_match_pose's loop -- the delta bars keep updating live during
        auto-align too, not just manual matching (issue #8 follow-up)."""
        act = self._teleop.get_action()
        obs = self._get_obs()
        self._emit_frames(obs)
        q_led = np.array([act[k] for k in JOINT_KEYS[:7]])
        q_rob = np.array([obs[k] for k in JOINT_KEYS[:7]])
        delta = np.abs(q_led - q_rob)
        all_ok = bool(delta.max() <= GATE_RAD)
        self.gate_status.emit(self._joint_vec(act), self._joint_vec(obs), all_ok)
        return delta, all_ok

    def _pose_gate(self, timeout: float = 3600.0) -> str:
        """Blocks (emitting live deltas + frames) until leader matches reset_q.

        Returns "ok", "quit", or "go_home".
        """
        self.state_changed.emit("gate")
        deadline = time.monotonic() + timeout
        if self.cfg.auto_match_pose:
            # Pull the leader onto the reset pose before handing control back,
            # so every episode starts from the same joint configuration.
            outcome = self._auto_match_pose()
            if outcome in ("quit", "go_home"):
                return outcome
            if outcome == "start_teleop":
                self._teleop.cancel_pose_match()
                return "ok"
        try:
            while True:
                cmd = self._poll_cmd()
                if cmd and cmd[0] == "quit":
                    return "quit"
                if cmd and cmd[0] == "go_home":
                    return "go_home"
                delta, all_ok = self._emit_gate_status()
                # Auto-advance is disabled on purpose: matching alone never starts
                # recording, only an explicit Start Teleop click does -- so a
                # momentary match mid-motion can't silently kick things off.
                if cmd and cmd[0] == "start_teleop":
                    if all_ok:
                        return "ok"
                    self.log_message.emit(
                        f"[GATE] 아직 자세가 맞지 않습니다 (최대 차이 {delta.max():.2f} rad > {GATE_RAD} rad)"
                    )
                if cmd and cmd[0] == "auto_match_pose":
                    # Motor-protection precondition: only ever pull via the
                    # leader's own motors once the loose manual gate already
                    # passed, same all_ok this state already computes every
                    # tick -- the GUI also gates the button on this, but re-check
                    # here too in case a click and a pose drift raced.
                    if not all_ok:
                        self.log_message.emit(
                            f"[자동정렬] 먼저 대략적으로 자세를 맞춰주세요 "
                            f"(최대 차이 {delta.max():.2f} rad > {GATE_RAD} rad)"
                        )
                    else:
                        outcome = self._auto_match_pose()
                        if outcome in ("quit", "go_home"):
                            return outcome
                        if outcome == "start_teleop":
                            # Operator started teleop mid-align -- the pull
                            # is already released (see _auto_match_pose), so
                            # just honor it like a normal Start Teleop click.
                            return "ok"
                        # outcome == "ok": converged, and per _auto_match_pose's
                        # contract the leader is left TORQUE-HELD at the target
                        # (not released) -- the loop just keeps looping, still
                        # in "gate", holding the matched pose until the operator
                        # actually clicks Start Teleop (or quits/goes home). The
                        # `finally` below releases it whichever way this
                        # function ends up returning.
                if time.monotonic() > deadline:
                    self.log_message.emit(f"[GATE] {timeout:.0f}s 시간 초과")
                    return "quit"
                time.sleep(0.05)
        finally:
            # Single release point for every way out of the gate state:
            # Start Teleop was clicked (leader must be free again for actual
            # teleop), or we're heading home/quitting. No-op if nothing was
            # being held (e.g. the operator never used auto-match, or
            # _auto_match_pose already released it on its own timeout/abort
            # path below).
            self._teleop.cancel_pose_match()

    def _auto_match_pose(self, timeout: float = 15.0) -> str:
        """Drives the GELLO leader's own motors to pull it the rest of the
        way onto ``self._reset_q`` -- an automated version of the manual
        nudging the operator otherwise does by hand before every episode
        (see GitHub issue #8). Only reachable once the loose manual gate
        (GATE_RAD) already passed, so this only ever closes a <= GATE_RAD
        gap, never drives across the leader's full range.

        On convergence, deliberately does NOT release the hold -- the leader
        stays torque-held at the target so it can't drift again before the
        operator actually starts teleop (_pose_gate's `finally` is what
        releases it, right as the gate state is left one way or another).
        A timeout (never converged) does release here, falling back to
        manual matching in the gate loop that's still running. Starting
        teleop is also allowed mid-align (the operator doesn't have to wait
        out the full pull): a queued ``start_teleop`` aborts it, releasing
        the hold immediately -- once real teleop is about to take over,
        holding the align force serves no purpose (_approach_ramp handles
        whatever residual gap is left).

        Returns "ok" (converged-and-held, timed-out-and-released, or the
        assist isn't available so there's nothing to do -- all three just
        resume the caller's gate loop), "start_teleop" (aborted-and-released,
        caller should proceed to start teleop), "quit", or "go_home" (both
        release before returning, since either leaves the gate state for
        good).
        """
        try:
            self._teleop.start_pose_match(self._reset_q)
        except RuntimeError as e:
            self.log_message.emit(f"[자동정렬] 사용 불가: {e}")
            return "ok"
        self.log_message.emit("[자동정렬] 시작...")
        deadline = time.monotonic() + timeout
        while True:
            interrupt = self._drain_match_interrupt()
            if interrupt:
                self._teleop.cancel_pose_match()
                if interrupt == "start_teleop":
                    self.log_message.emit("[자동정렬] 텔레옵 시작으로 중단")
                return interrupt
            self._emit_gate_status()  # keep the delta bars live during the pull
            status = self._teleop.pose_match_status()
            err = status["error"]
            done = bool(status["done"])
            self.pose_match_status.emit(float(err) if err is not None else 0.0, done)
            if done:
                self.log_message.emit("[자동정렬] 완료 -- 텔레옵 시작 전까지 자세를 유지합니다")
                return "ok"
            if time.monotonic() > deadline:
                self.log_message.emit(f"[자동정렬] {timeout:.0f}s 시간 초과 -- 수동으로 조정하세요")
                self.pose_match_status.emit(float(err) if err is not None else 0.0, True)
                self._teleop.cancel_pose_match()
                return "ok"
            time.sleep(0.02)

    # -------------------------------------------------------------- episode
    def _record_episode(self) -> tuple[str, int]:
        """Returns (outcome, n_frames); outcome is "save", "discard", "quit", or "go_home"."""
        self.state_changed.emit("recording")
        self._writer.start_episode()
        self._cam_stale = {}  # per-episode, see _get_obs
        self._cam_stale_run = {}
        self._cam_stale_max_run = {}
        self._pending_success: Optional[bool] = None
        budget = 1.0 / self.cfg.fps
        max_frames = int(self.cfg.max_episode_seconds * self.cfg.fps)
        t_next = time.monotonic()
        n = 0
        outcome = "save"
        for i in range(max_frames):
            cmd = self._poll_cmd()
            if cmd:
                if cmd[0] == "discard_episode" or cmd[0] == "quit":
                    outcome = "discard" if cmd[0] == "discard_episode" else "quit"
                    break
                if cmd[0] == "go_home":
                    outcome = "go_home"
                    break
                if cmd[0] == "save_episode":
                    outcome = "save"
                    self._pending_success = cmd[1]
                    break

            action = self._teleop.get_action()
            self._robot.send_action(action)
            obs = self._get_obs()

            q = self._joint_vec(obs)
            q_cmd = self._joint_vec(action)
            self._writer.add_frame(
                agentview_rgb=obs["agent"],
                eye_in_hand_rgb=obs["wrist"],
                joint_positions=q[:7],
                gripper_position=obs["gripper.pos"],
                ee_pos_quat=obs["_ee_pos_quat"],
                gripper_closed=action["gripper.pos"] > 0.5,
                joint_velocities=obs["_joint_velocities"][:7],
                timestamp=time.time(),
                commanded_joint_positions=q_cmd[:7],
                commanded_gripper=float(action["gripper.pos"]),
            )
            self._emit_frames(obs)
            n = i + 1
            t_next += budget
            self.episode_progress.emit(n, n / self.cfg.fps)
            time.sleep(max(0.0, t_next - time.monotonic()))
        else:
            # Loop ran to completion without an explicit save/discard/quit/
            # go_home -- the operator let it hit max_episode_seconds instead
            # of clicking a save button. Auto-save as a labeled failure
            # (not unlabeled) so it's obviously not a clean success.
            outcome = "save"
            self._pending_success = False
            self.log_message.emit(
                f"[EP] 에피소드 최대 길이({self.cfg.max_episode_seconds:.0f}s) 초과 -- 실패로 자동 저장"
            )
        # Report camera stalls with the episode, while the operator can still
        # act on it: a frozen image paired with moving joint states is not
        # something the saved file makes obvious later.
        # Only a *run* of identical frames means the camera stalled. Isolated
        # repeats are the 30 fps camera being sampled at 20 Hz -- every
        # episode has one or two and they carry no information, so reporting
        # them just trains the operator to ignore this line. A percentage
        # threshold is useless here too: 1 repeat in a 3-frame episode is 33%
        # and means nothing.
        stalls = {k: v for k, v in self._cam_stale_max_run.items() if v >= 3}
        if stalls and n:
            detail = ", ".join(
                f"{k} 최장 {v}틱({v/self.cfg.fps*1000:.0f} ms), 총 {self._cam_stale.get(k, 0)}프레임"
                for k, v in sorted(stalls.items())
            )
            self.log_message.emit(
                f"[카메라] 정지 감지: {detail} / 전체 {n}프레임  ← 폐기 권장"
            )
        return outcome, n

    # ------------------------------------------------------------------- run
    def run(self) -> None:  # noqa: C901 - state machine, kept in one place on purpose
        try:
            self.state_changed.emit("connecting")
            self._connect()
            if self.cfg.no_dataset:
                self._writer = NullTaskWriter(schema=self.cfg.schema)
            else:
                self._writer = LiberoTaskWriter(
                    root=self.cfg.data_root,
                    task_name=self.cfg.task_name,
                    language_instruction=self.cfg.language_instruction,
                    resume=self.cfg.resume,
                    schema=self.cfg.schema,
                    crop_params=self.cfg.crop_params,
                )
            self._writer.record_session_config(
                reset_pose=self.cfg.reset_pose,
                grip=self.cfg.grip,
                enable_wall=self.cfg.enable_wall,
                max_episode_seconds=self.cfg.max_episode_seconds,
                reset_wait_seconds=self.cfg.reset_wait_seconds,
            )
        except Exception as e:  # noqa: BLE001
            # Covers both robot/camera/GELLO connect failures and writer
            # creation failing (e.g. task file exists without --resume) --
            # either way, undo whatever hardware DID connect before returning.
            #
            # zmq.Again is by far the most common one and its own message
            # ("Resource temporarily unavailable") says nothing about what
            # actually happened: the ZMQ request to the robot node timed out.
            # Name the cause and the fix instead of the errno.
            if isinstance(e, zmq.error.Again):
                self.fatal_error.emit(
                    f"연결 실패: 로봇 노드가 응답하지 않습니다 (ZMQ 타임아웃, "
                    f"{self.cfg.hostname}:{self.cfg.robot_port}).\n"
                    "'노드 시작' 버튼으로 launch_nodes.py를 띄웠는지, 이미 떠 있다면 "
                    "제어 루프가 죽지 않았는지(리플렉스 abort) 확인하세요. "
                    "'노드 재시작'이 보통 해결합니다."
                )
            else:
                self.fatal_error.emit(f"연결 실패: {type(e).__name__}: {e}")
            for cleanup in (
                getattr(self._teleop, "disconnect", None),
                getattr(self._robot, "disconnect", None),
            ):
                if cleanup is not None:
                    try:
                        cleanup()
                    except Exception:  # noqa: BLE001
                        pass
            self.state_changed.emit("idle")
            return

        self._episode_count = self._writer.num_episodes
        self.connected.emit(self._episode_count, str(self._writer.path))
        self.episode_list_changed.emit(self._writer.list_episodes())
        # 이 시점 이후 파일을 만지는 호출(save/delete/list)은 전부 saver 스레드로.
        self.saver.set_writer(self._writer)
        self.saver.start()

        need_reset = False
        try:
            while self._running:
                try:
                    # react_to_go_home=False: this ramp already IS "go home",
                    # so a go_home click here is a no-op, not an abort.
                    self.state_changed.emit("homing")
                    if self._ramp_home(react_to_go_home=False) != "ok":
                        break

                    if need_reset:
                        r = self._reset_wait()
                        if r == "quit":
                            break
                        if r == "go_home":
                            continue

                    g = self._pose_gate()
                    if g == "quit":
                        break
                    if g == "go_home":
                        continue

                    self.state_changed.emit("approach")
                    a = self._approach_ramp()
                    if a == "quit":
                        break
                    if a == "go_home":
                        continue

                    outcome, n = self._record_episode()
                    need_reset = True

                    if outcome == "go_home":
                        self.episode_discarded.emit(n)
                        self.log_message.emit("[EP] 홈 이동 요청으로 에피소드 폐기")
                        continue
                    if outcome == "discard":
                        self.episode_discarded.emit(n)
                        continue
                    if n < 2:
                        self.log_message.emit("[EP] 프레임이 너무 적어 저장하지 않음")
                        continue

                    # 버퍼를 떼어 백그라운드 저장으로 넘기고 즉시 홈 복귀 진행.
                    # episode_saved/episode_list_changed는 saver가 emit.
                    self.saver.enqueue_save(self._writer.detach_buffer(), self._pending_success)
                    self._episode_count += 1

                    if outcome == "quit":
                        break
                except (zmq.ZMQError, RuntimeError):
                    # Two distinct failures land here, both needing the same
                    # recovery: (a) the robot node process died/dropped off
                    # the network (zmq.ZMQError), or (b) the process is
                    # still up but FrankaFR3Robot's 1kHz control thread died
                    # on its own -- e.g. a reflex abort -- while the ZMQ
                    # server kept answering requests normally. (b) used to
                    # be invisible: get_observations() just kept returning
                    # the last position forever, so nothing here ever
                    # errored and the GUI looked "frozen" after some number
                    # of steps with no explanation. franka_fr3.py now raises
                    # once the control thread reports itself dead, which
                    # surfaces here as a RuntimeError via ZMQClientRobot.
                    # Same recovery contract as record_dataset.py either
                    # way: discard whatever episode was in flight, wait for
                    # the node to come back, then resume from home.
                    self._writer.discard_episode()
                    self.node_status.emit(False)
                    self.log_message.emit(
                        "[NODE DOWN] robot node 무응답 또는 제어 루프 다운 -- "
                        "자동 복구 안 되면 '노드 재시작' 버튼을 누르세요"
                    )
                    if not self._wait_node_recovery():
                        break
                    self.node_status.emit(True)
                    need_reset = True
        except Exception as e:  # noqa: BLE001
            self.fatal_error.emit(f"{type(e).__name__}: {e}")
        finally:
            try:
                self._writer.discard_episode()
                # 대기 중인 백그라운드 저장을 모두 커밋한 뒤에야 요약/close 가능
                # (saver 종료 후에는 이 스레드가 파일을 만져도 경합 없음).
                if self.saver.isRunning():
                    self.saver.finish()
                    self.saver.wait(60000)
                self._emit_session_summary()
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._ramp_home(max_ticks=200)
            except Exception:  # noqa: BLE001
                pass
            for cleanup in (
                getattr(self._teleop, "disconnect", None),
                getattr(self._robot, "disconnect", None),
            ):
                if cleanup is not None:
                    try:
                        cleanup()
                    except Exception:  # noqa: BLE001
                        pass
            self.state_changed.emit("idle")

    def _connect(self) -> None:
        from lerobot.cameras.realsense import RealSenseCameraConfig

        self._robot = FR3ZMQRobot(
            FR3ZMQRobotConfig(
                id="fr3",
                host=self.cfg.hostname,
                port=self.cfg.robot_port,
                # 스트림 포맷은 스테이션 설정에서 온다
                # (configs/stations/<이름>.yaml 의 cameras.<역할>). 시리얼만은
                # WorkerConfig 를 따르는데, GUI 가 라이브 장치 스캔 결과로
                # 채워 주기 때문이다 -- 카메라를 바꿔 끼우면 시리얼이 바뀐다.
                #
                # 30fps 이지 60 이 아닌 이유: D405 는 640x480 에서 30fps 가
                # 상한이고(pyrealsense2 프로파일 열거로 확인), 60 을 요구하면
                # librealsense 가 스트림 설정을 거부하면서 엉뚱하게 "device
                # busy" ConnectionError 로 나온다. 기록 루프는 어차피
                # read_latest() 로 cfg.fps(기본 20Hz)에서만 집어가므로 30fps
                # 캡처로 충분하다.
                cameras={
                    role: RealSenseCameraConfig(
                        serial_number_or_name=serial,
                        fps=_STATION.camera(role).fps,
                        width=_STATION.camera(role).width,
                        height=_STATION.camera(role).height,
                    )
                    for role, serial in (
                        ("agent", self.cfg.agent_camera_serial),
                        ("wrist", self.cfg.wrist_camera_serial),
                    )
                },
            )
        )
        self._teleop = GelloFR3Teleop(
            GelloFR3TeleopConfig(id="gello", enable_wall=self.cfg.enable_wall, grip=self.cfg.grip)
        )
        self._robot.connect()
        self._teleop.connect()

    def _reset_wait(self) -> str:
        """Returns "ok", "quit", or "go_home"."""
        self.state_changed.emit("reset_wait")
        end = time.monotonic() + self.cfg.reset_wait_seconds
        while True:
            remain = end - time.monotonic()
            if remain <= 0:
                return "ok"
            cmd = self._poll_cmd()
            if cmd:
                if cmd[0] == "skip_reset_wait":
                    return "ok"
                if cmd[0] == "quit":
                    return "quit"
                if cmd[0] == "go_home":
                    return "go_home"
            self.reset_countdown.emit(max(0.0, remain))
            time.sleep(0.1)

    def _wait_node_recovery(self) -> bool:
        while True:
            cmd = self._poll_cmd()
            if cmd and cmd[0] == "quit":
                return False
            try:
                self._robot.reconnect_node()
            except Exception:  # noqa: BLE001
                time.sleep(2.0)
                continue
            self.log_message.emit("[NODE OK] 재연결 완료")
            return True

    def stop(self) -> None:
        self._running = False
        self.cmd_quit()

    def _emit_session_summary(self) -> None:
        episodes = self._writer.list_episodes()
        n_success = sum(1 for e in episodes if e["success"] is True)
        n_fail = sum(1 for e in episodes if e["success"] is False)
        n_unlabeled = sum(1 for e in episodes if e["success"] is None)
        frames = [e["num_samples"] for e in episodes]
        self.session_summary.emit(
            {
                "path": str(self._writer.path),
                "num_episodes": len(episodes),
                "total_frames": sum(frames),
                "min_frames": min(frames) if frames else 0,
                "max_frames": max(frames) if frames else 0,
                "num_success": n_success,
                "num_fail": n_fail,
                "num_unlabeled": n_unlabeled,
            }
        )
