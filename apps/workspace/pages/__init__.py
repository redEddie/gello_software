"""Left-panel page builders for WorkspaceWindow.

Each page is a module-level function ``build_<name>(win) -> QWidget``, keyed in
PAGE_BUILDERS by the activity key. build_left walks ACTIVITIES and looks each
key up here, so a key present in one and missing from the other is a KeyError
at startup rather than a silently blank panel -- add to both or neither.
"""
from .collect import build_collect
from .configure import build_configure
from .dataset import build_dataset
from .layout import build_layout_page
from .settings import build_settings
from .stats import build_stats
from .upload import build_upload

PAGE_BUILDERS = {
    "configure": build_configure,
    "collect": build_collect,
    "dataset": build_dataset,
    "layout": build_layout_page,
    "settings": build_settings,
    "stats": build_stats,
    "upload": build_upload,
}

__all__ = [
    "PAGE_BUILDERS",
    "build_collect",
    "build_configure",
    "build_dataset",
    "build_layout_page",
    "build_settings",
    "build_stats",
    "build_upload",
]
