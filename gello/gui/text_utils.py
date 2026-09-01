"""Pure text/stream helpers used by the collector GUIs.

Split out of gello/gui/gui_widgets.py so subprocess log filtering and repo-id
validation can be imported without dragging in the full widget collection.
"""

from __future__ import annotations

import re
import time

_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def repo_id_error(repo_id: str) -> "str | None":
    """Why `repo_id` is not a usable Hub id, or None if it is.

    Checked before anything is stored or run because the failure it prevents is
    slow and confusing: an id with a bad namespace passes every local step and
    dies at the very end with `403 ... rights to create a dataset under the
    namespace "r"`, after (in one real run) 15.6 minutes of repacking. And a
    typo that reaches Recents becomes the default for the automatic buttons,
    so the same failure repeats without anyone retyping it.
    """
    if not repo_id:
        return "Repo ID를 입력하세요."
    if "/" not in repo_id:
        return f"'{repo_id}' 에 네임스페이스가 없습니다. <조직 또는 사용자>/<이름> 형식이어야 합니다."
    if repo_id.count("/") > 1:
        return f"'{repo_id}' 에 '/' 가 너무 많습니다. <네임스페이스>/<이름> 하나뿐이어야 합니다."
    if not _REPO_ID_RE.match(repo_id):
        return f"'{repo_id}' 는 사용할 수 없는 형식입니다 (영문/숫자로 시작, 나머지는 영문·숫자·. _ - )."
    ns = repo_id.split("/")[0]
    if len(ns) < 2:
        return f"네임스페이스 '{ns}' 가 너무 짧습니다 — 오타로 보입니다."
    return None



_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")
_PROGRESS_RE = re.compile(r"\d+%\|")


def is_progress_line(line: str) -> bool:
    """tqdm 진행률 줄인가. 로그에 쌓지 않고 한 줄을 갱신하는 데 쓴다."""
    return bool(_PROGRESS_RE.search(line))


def clean_stream_lines(data: str, state: dict, every_s: float = 3.0) -> list[str]:
    """Split subprocess output into log-worthy lines, de-spamming progress."""
    out = []
    for raw in _ANSI_RE.sub("\n", data).splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _PROGRESS_RE.search(line):
            now = time.monotonic()
            # Always keep a finished bar; throttle the rest.
            done = line.lstrip().startswith("100%") or "100%|" in line
            if not done and now - state.get("t", 0.0) < every_s:
                continue
            if done and state.get("last_done") == line:
                continue
            state["t"] = now
            if done:
                state["last_done"] = line
        out.append(line)
    return out


