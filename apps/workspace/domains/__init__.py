"""Domain operations lifted out of WorkspaceWindow (Phase 4).

Each domain is a class constructed with the window and holding it as ``self.win``.
The window creates one of each in ``__init__`` and builders connect to
``win.<domain>.<method>``. The arrow points one way: a domain reaches into the
window, the window never imports a domain's internals.

Moving a method here is textual (``self.x`` -> ``self.win.x``), and a slip in that
substitution only shows up when someone clicks the button -- which is why
tests/gui/test_domain_attrs.py checks every ``self.win.<name>`` against a real
window instance.
"""

from apps.workspace.features.camera import CameraOps, DepthOps
from apps.workspace.domains.collection import CollectionOps
from apps.workspace.features.dataset import DatasetOps
from apps.workspace.features.gallery import GalleryOps
from apps.workspace.features.playback import PlaybackOps
from apps.workspace.features.scene import LayoutRefOps, SceneOps, ScenePlanningOps
from apps.workspace.domains.stats import StatsOps
from apps.workspace.domains.system import SystemOps
from apps.workspace.features.upload import UploadOps

__all__ = ["CameraOps", "CollectionOps", "DatasetOps", "DepthOps", "GalleryOps", "LayoutRefOps", "PlaybackOps", "SceneOps", "ScenePlanningOps", "StatsOps", "SystemOps", "UploadOps"]
