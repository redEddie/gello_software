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
from gello.libero_format import LiberoTaskWriter
from gello.robots.franka_fr3 import FR3_RESET_POSES

GATE_RAD = 0.5  # run_env.py / gello_match_pose.py's start-gate threshold
RAMP_STEP = 0.05  # rad/tick @ 20Hz = 1.0 rad/s, same as record_dataset.py
GRIPPER_OPEN = 0.0  # GELLO/franka_fr3 convention: 0=open, 1=closed

# Fallback defaults, used only if the GUI doesn't supply a serial (e.g. a
# script driving CollectionWorker directly). The GUI itself always populates
# WorkerConfig.agent_camera_serial / wrist_camera_serial from a live device
# scan (see collect_libero_gui.py's camera combo boxes), since serials change
# whenever a camera is swapped.
AGENT_CAMERA_SERIAL = "338122300664"
WRIST_CAMERA_SERIAL = "230422272249"


@dataclass
class WorkerConfig:
    task_name: str
    language_instruction: str
    data_root: str
    robot_port: int = 6001
    hostname: str = "127.0.0.1"
    grip: str = "right"
    reset_pose: str = "libero"
    fps: int = 20
    max_episode_seconds: float = 20.0
    reset_wait_seconds: float = 10.0
    enable_wall: bool = True
    resume: bool = False
    agent_camera_serial: str = AGENT_CAMERA_SERIAL
    wrist_camera_serial: str = WRIST_CAMERA_SERIAL
    schema: DatasetSchemaConfig = field(default_factory=DatasetSchemaConfig)


class CollectionWorker(QThread):
    state_changed = pyqtSignal(str)
    frames_ready = pyqtSignal(object, object)  # agentview_rgb, eye_in_hand_rgb (np.ndarray)
    gate_status = pyqtSignal(object, object, bool)  # leader(8,), follower(8,), all_ok
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

    # ------------------------------------------------------------------ API
    # Called from the GUI (main) thread; safe because queue.Queue is thread-safe.
    def cmd_start_teleop(self) -> None:
        self._cmds.put(("start_teleop",))

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
        """Runs on this thread -- the only thread allowed to touch ``self._writer``'s
        open h5py.File. Deletion is a metadata edit, not robot I/O, so it is
        safe to service from any state (gate/recording/ramping/idle)."""
        try:
            self._writer.delete_episode(name)
            self.log_message.emit(f"[삭제] {name}")
        except Exception as e:  # noqa: BLE001
            self.log_message.emit(f"[삭제 실패] {name}: {type(e).__name__}: {e}")
        self.episode_list_changed.emit(self._writer.list_episodes())

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
            out[cam_key] = cam.read_latest()
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
        for _ in range(max_ticks):
            interrupt = self._drain_interrupt(react_to_go_home=react_to_go_home)
            if interrupt:
                return interrupt
            obs = self._get_obs()
            q = np.array([obs[k] for k in JOINT_KEYS[:7]])
            d = target_q - q
            if np.abs(d).max() < 0.02:
                return "ok"
            step = np.clip(d, -RAMP_STEP, RAMP_STEP)
            cmd = dict(zip(JOINT_KEYS, np.append(q + step, GRIPPER_OPEN).tolist()))
            self._robot.send_action(cmd)
            self._emit_frames(obs)
            time.sleep(0.05)
        return "quit"

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
            step = np.clip(d, -RAMP_STEP, RAMP_STEP)
            cmd = dict(zip(JOINT_KEYS, np.append(q_rob + step, act["gripper.pos"]).tolist()))
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
    def _pose_gate(self, timeout: float = 3600.0) -> str:
        """Blocks (emitting live deltas + frames) until leader matches reset_q.

        Returns "ok", "quit", or "go_home".
        """
        self.state_changed.emit("gate")
        deadline = time.monotonic() + timeout
        while True:
            cmd = self._poll_cmd()
            if cmd and cmd[0] == "quit":
                return "quit"
            if cmd and cmd[0] == "go_home":
                return "go_home"
            act = self._teleop.get_action()
            obs = self._get_obs()
            self._emit_frames(obs)
            q_led = np.array([act[k] for k in JOINT_KEYS[:7]])
            q_rob = np.array([obs[k] for k in JOINT_KEYS[:7]])
            delta = np.abs(q_led - q_rob)
            all_ok = bool(delta.max() <= GATE_RAD)
            self.gate_status.emit(
                self._joint_vec(act), self._joint_vec(obs), all_ok
            )
            # Auto-advance is disabled on purpose: matching alone never starts
            # recording, only an explicit Start Teleop click does -- so a
            # momentary match mid-motion can't silently kick things off.
            if cmd and cmd[0] == "start_teleop":
                if all_ok:
                    return "ok"
                self.log_message.emit(
                    f"[GATE] 아직 자세가 맞지 않습니다 (최대 차이 {delta.max():.2f} rad > {GATE_RAD} rad)"
                )
            if time.monotonic() > deadline:
                self.log_message.emit(f"[GATE] {timeout:.0f}s 시간 초과")
                return "quit"
            time.sleep(0.05)

    # -------------------------------------------------------------- episode
    def _record_episode(self) -> tuple[str, int]:
        """Returns (outcome, n_frames); outcome is "save", "discard", "quit", or "go_home"."""
        self.state_changed.emit("recording")
        self._writer.start_episode()
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
            self._writer.add_frame(
                agentview_rgb=obs["agent"],
                eye_in_hand_rgb=obs["wrist"],
                joint_positions=q[:7],
                gripper_position=obs["gripper.pos"],
                ee_pos_quat=obs["_ee_pos_quat"],
                gripper_closed=action["gripper.pos"] > 0.5,
                joint_velocities=obs["_joint_velocities"][:7],
                timestamp=time.time(),
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
        return outcome, n

    # ------------------------------------------------------------------- run
    def run(self) -> None:  # noqa: C901 - state machine, kept in one place on purpose
        try:
            self.state_changed.emit("connecting")
            self._connect()
            self._writer = LiberoTaskWriter(
                root=self.cfg.data_root,
                task_name=self.cfg.task_name,
                language_instruction=self.cfg.language_instruction,
                resume=self.cfg.resume,
                schema=self.cfg.schema,
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

        need_reset = False
        try:
            while self._running:
                try:
                    # react_to_go_home=False: this ramp already IS "go home",
                    # so a go_home click here is a no-op, not an abort.
                    self.state_changed.emit("homing")
                    if self._ramp_to(self._reset_q, react_to_go_home=False) != "ok":
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

                    demo_name = self._writer.save_episode(success=self._pending_success)
                    self._episode_count += 1
                    self.episode_saved.emit(demo_name or "?", n)
                    self.episode_list_changed.emit(self._writer.list_episodes())

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
                self._emit_session_summary()
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._ramp_to(self._reset_q, max_ticks=200)
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
                cameras={
                    # 30fps, not 60: the D405 wrist cam only supports up to
                    # 30fps at 640x480 (confirmed via direct pyrealsense2
                    # profile enumeration) -- requesting 60 makes librealsense
                    # reject the stream config, which surfaces as a
                    # misleading "device busy" ConnectionError. The recording
                    # loop itself only samples at cfg.fps (20Hz by default)
                    # via read_latest(), so 30fps capture is plenty either way.
                    "agent": RealSenseCameraConfig(
                        serial_number_or_name=self.cfg.agent_camera_serial, fps=30, width=640, height=480
                    ),
                    "wrist": RealSenseCameraConfig(
                        serial_number_or_name=self.cfg.wrist_camera_serial, fps=30, width=640, height=480
                    ),
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
