"""Activity key -> left-panel page builder mapping."""
from apps.workspace.features.collection import build_collect
from apps.workspace.features.dataset.page import build_dataset
from apps.workspace.features.scene.layout_page import build_layout_page
from apps.workspace.features.stats import build_stats
from apps.workspace.features.upload import build_upload

from .configure_page import build_configure
from .settings_page import build_settings

PAGE_BUILDERS = {
    "configure": build_configure,
    "collect": build_collect,
    "dataset": build_dataset,
    "layout": build_layout_page,
    "settings": build_settings,
    "stats": build_stats,
    "upload": build_upload,
}

__all__ = ["PAGE_BUILDERS"]
