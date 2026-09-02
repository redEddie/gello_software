"""Window skeleton and app-level settings pages for WorkspaceWindow."""
from .configure_page import build_configure
from .layout import build_bottom, build_center, build_layout, build_left, build_right
from .page_builders import PAGE_BUILDERS
from .settings_page import build_settings
from .toolbar import build_menu, build_statusbar, build_toolbar

__all__ = [
    "PAGE_BUILDERS",
    "build_bottom",
    "build_center",
    "build_configure",
    "build_layout",
    "build_left",
    "build_menu",
    "build_right",
    "build_settings",
    "build_statusbar",
    "build_toolbar",
]
