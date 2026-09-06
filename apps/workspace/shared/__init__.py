"""Shared widgets and helpers used by multiple workspace features."""
from .tabs import center_tab_key, show_center_tab
from .widgets import SceneInfoView, StatusLight
from .image_utils import depth_colormap, draw_depth_scale
from .sizing import relax_min_widths, shrinkable_combo

__all__ = [
    "center_tab_key",
    "show_center_tab",
    "SceneInfoView",
    "StatusLight",
    "depth_colormap",
    "draw_depth_scale",
    "relax_min_widths",
    "shrinkable_combo",
]
