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

from apps.workspace.domains.playback import PlaybackOps
from apps.workspace.domains.upload import UploadOps

__all__ = ["PlaybackOps", "UploadOps"]
