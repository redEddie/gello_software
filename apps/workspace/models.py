"""Workspace window state objects.

These hold the data that was previously scattered across WorkspaceWindow
attributes.  Phase 3-1 moves process handles and pipeline progress here;
later phases may move more.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from typing import Any

from PyQt6.QtCore import QProcess, QTimer


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
