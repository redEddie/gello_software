"""Left-panel page builders for WorkspaceWindow.

Each page is a module-level function ``build_<name>(win) -> QWidget``.
The fallback dict lets pages move one at a time: once a page is exported here,
build_left uses it; otherwise it falls back to the old ``win._page_<name>()``
method until that page is also extracted.
"""
from .collect import build_collect
from .configure import build_configure
from .dataset import build_dataset
from .layout import build_layout_page
from .stats import build_stats
from .upload import build_upload

PAGE_BUILDERS = {
    "configure": build_configure,
    "collect": build_collect,
    "dataset": build_dataset,
    "layout": build_layout_page,
    "stats": build_stats,
    "upload": build_upload,
}

__all__ = [
    "PAGE_BUILDERS",
    "build_collect",
    "build_configure",
    "build_dataset",
    "build_layout_page",
    "build_stats",
    "build_upload",
]
