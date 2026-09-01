"""Workspace window state objects.

These hold the data that was previously scattered across WorkspaceWindow
attributes.  Phase 3-1 moves process handles and pipeline progress here;
Phase 3-4 moves session/episode state here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QProcess, QTimer


def _new_stats() -> dict:
    """수집 카운터 한 벌. 이번 task 용과 누적용이 같은 모양이라 같은 곳에서 만든다."""
    return {"saved": 0, "success": 0, "failed": 0, "discarded": 0,
            "frames": 0, "t0": time.monotonic()}


@dataclass
class ProcessRegistry:
    """QProcess handles and pipeline progress for WorkspaceWindow."""

    node_process: QProcess | None = None
    camera_node_process: QProcess | None = None
    convert_process: QProcess | None = None
    repack_process: QProcess | None = None
    upload_process: QProcess | None = None
    replay_process: QProcess | None = None
    runme_process: QProcess | None = None
    reset_protection_process: QProcess | None = None

    pipeline_proc: QProcess | None = None
    pipeline_steps: list = field(default_factory=list)
    pipeline_results: list = field(default_factory=list)
    pipeline_t0: float = 0.0
    pipeline_step_t0: float = 0.0


@dataclass
class PlaybackState:
    """Trim and playback state for WorkspaceWindow."""

    trim_key: tuple | None = None
    trim_n: int = 0
    trim_n_pending: int = 0
    trim_frames: dict = field(default_factory=lambda: {"agent": None, "wrist": None})
    trim_loader: Any | None = None
    trim_timer: QTimer | None = None
    trim_series: Any | None = None
    trim_tab_index: int = 0

    play_key: tuple | None = None
    play_timer: QTimer | None = None
    play_frames: dict = field(default_factory=lambda: {"agent": None, "wrist": None})
    play_loader: Any | None = None

    layout_playing: bool = True


@dataclass
class CameraState:
    """Camera, depth, and point-cloud state for WorkspaceWindow."""

    camera_node_spec: str = ""
    camera_node_user_stopped: bool = False
    camera_node_crashes: list = field(default_factory=list)

    last_cam_frame: dict = field(default_factory=dict)
    stream_states: dict = field(default_factory=dict)
    live_maximized: "str | None" = None

    fps_count: int = 0
    fps_value: float = 0.0
    fps_timer: Any | None = None

    depth_consumer: "str | None" = None
    depth_img: Any | None = None
    depth_cursor: "tuple | None" = None

    cloud_worker: Any | None = None
    cloud_pts: Any | None = None
    cloud_rgb: Any | None = None
    cloud_serial: str = ""

    cloud_pitch: Any | None = None
    cloud_yaw: Any | None = None

    crop_params: dict = field(default_factory=dict)
    grid_store: dict = field(default_factory=dict)
    layout_ref: dict = field(default_factory=dict)


@dataclass
class SessionState:
    """Session, episode, and collection-count state for WorkspaceWindow.

    Phase 3-4 deliberately moves only the scalar/list/dict fields; the
    CollectionWorker handle (``worker``) and Qt widgets stay on the window.
    ``session`` and ``cumulative`` are separate dict instances so that task
    counters never leak into the cumulative counters.
    """

    # per-connect task counters and cumulative counters
    session: dict = field(default_factory=_new_stats)
    cumulative: dict = field(default_factory=_new_stats)

    # analysis / episode-stat rows
    stats: list = field(default_factory=list)

    # scene/no-dataset session bookkeeping
    scene_session: bool = False
    no_dataset_session: bool = False
    episodes_at_connect: int = 0

    # currently active scene file and its cached episode list
    active_file_path: Path | None = None
    active_episode_cache: list | None = None

    # last-saved episode verdict + pending toggles
    last_saved_name: str | None = None
    last_saved_success: bool = True
    pending_verdict_toggle: bool = False
    pending_success: bool | None = None

    # worker state mirror (updated from worker signals, not read directly)
    current_state: str = "idle"
    gate_ok: bool = False
