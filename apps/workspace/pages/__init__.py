"""Left-panel page builders for WorkspaceWindow.

Each page is a module-level function ``build_<name>(win) -> QWidget``.
The fallback dict lets pages move one at a time: once a page is exported here,
build_left uses it; otherwise it falls back to the old ``win._page_<name>()``
method until that page is also extracted.
"""
from .configure import build_configure

PAGE_BUILDERS = {
    "configure": build_configure,
}

__all__ = [
    "PAGE_BUILDERS",
    "build_configure",
]
