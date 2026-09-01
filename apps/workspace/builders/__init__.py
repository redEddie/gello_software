"""UI builders for WorkspaceWindow.

Each builder takes the window and hangs the widgets it makes onto it -- the
same thing the methods did before they moved out, so the move stays checkable
against the original. Nothing here imports collect_workspace: the arrow points
one way (window -> builders) and tests/gui/test_dialog_modules.py is where that
gets nailed down as the rest of WorkspaceWindow follows.
"""
from .analysis_tab import build_analysis_tab
from .cloud_tab import build_cloud_tab
from .depth_tab import build_depth_tab
from .gallery_tab import build_gallery_tab
from .layout import build_bottom, build_center, build_layout, build_left, build_right
from .layout_tab import build_layout_tab
from .toolbar import build_menu, build_statusbar, build_toolbar
from .trim_tab import build_trim_tab

__all__ = [
    "build_analysis_tab",
    "build_bottom",
    "build_center",
    "build_cloud_tab",
    "build_depth_tab",
    "build_gallery_tab",
    "build_layout",
    "build_layout_tab",
    "build_left",
    "build_menu",
    "build_right",
    "build_statusbar",
    "build_toolbar",
    "build_trim_tab",
]
