"""Most-recently-used values persisted as JSON."""

from __future__ import annotations

import json
from pathlib import Path

from gello.gui.constants import RECENTS_PATH

_RECENTS_MAX = 8


class Recents:
    """Most-recently-used values per field key, persisted as JSON.

    Never raises: a corrupt or unwritable file just means "no history", which
    must not be able to stop the GUI from starting or a conversion from running.

    The default path is read at module level rather than embedded as a class
    default so tests can patch ``gello.gui.widgets.recents.RECENTS_PATH`` before
    instantiating ``Recents`` and avoid polluting the real GUI history file
    (~/libero_gui_logs/recent_inputs.json). See tests/gui/test_hub_upload_state.py.
    """

    def __init__(self, path: "Path | None" = None) -> None:
        self._path = path if path is not None else RECENTS_PATH
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str) -> list[str]:
        v = self._data.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []

    def most_recent(self, key: str, fallback: str = "") -> str:
        v = self.get(key)
        return v[0] if v else fallback

    def add(self, key: str, value: str) -> None:
        value = (value or "").strip()
        if not value:
            return
        cur = [v for v in self.get(key) if v != value]
        self._data[key] = [value] + cur[: _RECENTS_MAX - 1]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # history is a convenience, never a hard failure
