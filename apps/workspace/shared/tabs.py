"""중앙 탭을 **키**로 가리키는 헬퍼.

인덱스로 탭을 가리키면 탭이 하나만 늘거나 줄어도 전부 밀리고, 그 밀림은
조용하다 -- 예외가 나는 게 아니라 엉뚱한 탭이 열린다. 활동에 따라 탭 구성이
달라지면 인덱스는 아예 쓸 수 없다.

여기(shared)에 두는 이유: 쓰는 쪽이 features/* 와 shell/* 양쪽이라,
shell 에 두면 features -> shell 순환이 된다.
"""

from __future__ import annotations

from typing import Optional


def show_center_tab(win, key: str) -> None:
    """중앙 탭을 키로 연다. 지금 안 붙어 있는 탭이면 아무 일도 하지 않는다
    (활동에 따라 탭 구성이 달라질 수 있다)."""
    w = win.center_tab_widgets.get(key)
    if w is None:
        raise KeyError(f"모르는 중앙 탭 키: {key}")
    idx = win.center_tabs.indexOf(w)
    if idx >= 0:
        win.center_tabs.setCurrentIndex(idx)


def center_tab_key(win, idx: Optional[int] = None) -> Optional[str]:
    """지금(또는 idx 번째) 중앙 탭의 키. 못 찾으면 None."""
    if idx is None:
        idx = win.center_tabs.currentIndex()
    w = win.center_tabs.widget(idx)
    for key, widget in win.center_tab_widgets.items():
        if widget is w:
            return key
    return None
