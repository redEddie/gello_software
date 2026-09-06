"""중앙 탭을 **키**로 가리키는 헬퍼.

인덱스로 탭을 가리키면 탭이 하나만 늘거나 줄어도 전부 밀리고, 그 밀림은
조용하다 -- 예외가 나는 게 아니라 엉뚱한 탭이 열린다. 활동에 따라 탭 구성이
달라지면 인덱스는 아예 쓸 수 없다.

여기(shared)에 두는 이유: 쓰는 쪽이 features/* 와 shell/* 양쪽이라,
shell 에 두면 features -> shell 순환이 된다.
"""

from __future__ import annotations

from typing import Optional

from gello.gui.i18n import tr

from apps.workspace.constants import CENTER_TABS, CENTER_TABS_BY_ACTIVITY


def activity_for_tab(key: str) -> Optional[str]:
    """그 탭을 띄우는 활동. "live" 처럼 어디에나 있는 탭은 None."""
    owners = [a for a, keys in CENTER_TABS_BY_ACTIVITY.items() if key in keys]
    if len(owners) == len(CENTER_TABS_BY_ACTIVITY):
        return None                      # 모든 활동에 있다 -- 옮길 필요 없음
    return owners[0] if owners else None


def show_center_tab(win, key: str) -> None:
    """중앙 탭을 키로 연다.

    지금 활동에 그 탭이 없으면 **그 탭을 띄우는 활동으로 옮긴 뒤** 연다.
    안 그러면 View 메뉴(색인)에서 고른 탭이 조용히 아무 일도 안 하게 된다.
    """
    w = win.center_tab_widgets.get(key)
    if w is None:
        raise KeyError(f"모르는 중앙 탭 키: {key}")
    if win.center_tabs.indexOf(w) < 0:
        owner = activity_for_tab(key)
        if owner is not None:
            win._set_activity(owner)
    idx = win.center_tabs.indexOf(w)
    if idx >= 0:
        win.center_tabs.setCurrentIndex(idx)


def set_center_tabs(win, activity: str) -> None:
    """활동에 맞는 탭만 남긴다. 위젯은 지우지 않고 떼었다 붙이므로 상태(재생
    위치·선택·스크롤)가 보존된다.

    떼고 붙이는 동안 currentChanged 를 막는다 -- 그 핸들러는 레이아웃 탭이
    현재가 되면 활동을 바꾸므로, 재구성 중간 상태에서 불리면 활동 전환이
    자기를 다시 부른다.

    탭과 활동은 서로를 끈다 (탭을 고르면 활동이 따라가고, 활동을 바꾸면 탭
    구성이 바뀐다). 양방향이라 재진입 가드가 필요하다 -- 없으면 레이아웃
    탭을 누르는 순간 RecursionError 다 (실측).
    """
    if getattr(win, "_syncing_center_tabs", False):
        return
    win._syncing_center_tabs = True
    try:
        _sync(win, activity)
    finally:
        win._syncing_center_tabs = False


def _sync(win, activity: str) -> None:
    want = CENTER_TABS_BY_ACTIVITY.get(activity, ("live",))
    cur = center_tab_key(win)
    tabs = win.center_tabs
    tabs.blockSignals(True)
    try:
        for key, w in win.center_tab_widgets.items():
            idx = tabs.indexOf(w)
            if key not in want and idx >= 0:
                tabs.removeTab(idx)
                w.hide()                 # removeTab 은 부모를 떼기만 한다
        pos = 0
        for key, title in CENTER_TABS:
            if key not in want:
                continue
            w = win.center_tab_widgets[key]
            idx = tabs.indexOf(w)
            if idx < 0:
                # show() 를 부르지 않는다 -- 어느 페이지를 보일지는 QTabWidget
                # 이 정한다. 직접 부르면 현재가 아닌 페이지까지 보여서 내용이
                # 겹쳐 보인다 (2026-09-06 렌더에서 실제로 그랬다).
                tabs.insertTab(pos, w, tr(title))
            elif idx != pos:
                tabs.tabBar().moveTab(idx, pos)
            pos += 1
    finally:
        tabs.blockSignals(False)
    # 보던 탭이 남아 있으면 그대로, 없어졌으면 카메라로 돌아간다.
    target = cur if cur in want else "live"
    w = win.center_tab_widgets[target]
    idx = tabs.indexOf(w)
    if idx >= 0 and idx != tabs.currentIndex():
        tabs.setCurrentIndex(idx)
    # 막아 둔 사이의 부수효과(깊이 소비자·하단 패널)를 한 번에 맞춘다.
    # 보던 탭이 그대로면 바뀐 것이 없으므로 부르지 않는다.
    if center_tab_key(win) != cur:
        win._on_center_tab_changed(tabs.currentIndex())


def center_tab_key(win, idx: Optional[int] = None) -> Optional[str]:
    """지금(또는 idx 번째) 중앙 탭의 키. 못 찾으면 None."""
    if idx is None:
        idx = win.center_tabs.currentIndex()
    w = win.center_tabs.widget(idx)
    for key, widget in win.center_tab_widgets.items():
        if widget is w:
            return key
    return None
