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
    """그 탭을 띄우는 활동. 어디에나 있는 탭("live")과 어느 활동에도 없는
    색인 전용 탭은 None."""
    owners = [a for a, keys in CENTER_TABS_BY_ACTIVITY.items() if key in keys]
    if len(owners) == len(CENTER_TABS_BY_ACTIVITY):
        return None                      # 모든 활동에 있다 -- 옮길 필요 없음
    return owners[0] if owners else None


def is_index_only(key: str) -> bool:
    """어느 활동에도 안 붙는 탭인가 (View 메뉴로만 열리는 것)."""
    return not any(key in keys for keys in CENTER_TABS_BY_ACTIVITY.values())


#: 색인 전용 탭의 실험실 표시. 활동 바(⚙ 🎮 📂)와 툴바(▶ ✔ 🗑)가 이미
#: 이모지를 기호로 쓰고 있어 어휘가 낯설지 않다. U+2697 ALEMBIC 은 유니코드의
#: '플라스크'다 (삼각플라스크 전용 이모지는 없다). 변이선택자 FE0F 를 붙여야
#: 컬러로 그려진다 -- 없으면 단색 선화가 되어 다른 기호들과 톤이 어긋난다.
LAB_MARK = "⚗️"


def lab_icon():
    """실험실 표시를 QIcon 으로 (메뉴용).

    메뉴에서는 글자 앞에 이모지를 붙이지 않는다. Qt 메뉴는 왼쪽에
    체크표시용 여백을 잡아 두는데, 이모지를 글자에 넣으면 그 여백 **뒤**에
    붙어서 그 줄만 글자 시작이 두 칸 밀린다. 아이콘으로 주면 이모지가 그
    여백 안에 들어가고 글자 시작이 다른 줄과 맞는다 (2026-09-06 사용자 지적).
    """
    global _LAB_ICON
    if _LAB_ICON is None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont, QIcon, QPainter, QPixmap
        size = 16
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        font = QFont()
        font.setPointSizeF(size * 0.72)
        painter.setFont(font)
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, LAB_MARK)
        painter.end()
        _LAB_ICON = QIcon(pm)
    return _LAB_ICON


_LAB_ICON = None


def tab_title(key: str) -> str:
    """**탭**에 쓸 제목. 색인 전용이면 실험실 표시를 앞에 붙인다 (탭에는
    아이콘 여백이 없어 접두사가 맞다).

    표시를 CENTER_TABS 의 제목에 손으로 써넣지 않는 이유: 그러면 사실이
    두 곳(배분표와 제목)에 적힌다. 여기서 파생하면 활동에 넣는 순간
    표시가 저절로 사라지고, 빼는 순간 저절로 붙는다.
    """
    title = dict(CENTER_TABS).get(key, key)
    return f"{LAB_MARK} {tr(title)}" if is_index_only(key) else tr(title)


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
        elif is_index_only(key):
            # 색인 전용 탭 -- 지금 활동 끝에 잠깐 붙인다. 활동을 옮기면
            # set_center_tabs 의 제거 루프가 알아서 떼어 낸다.
            win.center_tabs.addTab(w, tab_title(key))
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
                tabs.insertTab(pos, w, tab_title(key))
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
