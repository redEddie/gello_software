"""휠로 콤보·스핀 값이 바뀌지 않게 한다 (앱 전역).

Qt 기본값에서는 드롭다운 위에 커서가 있을 때 휠을 굴리면 **선택이 바뀐다**.
페이지를 스크롤하려던 손짓이 값을 바꾸는 것이고, 바뀐 줄도 모른 채 지나가기
쉽다 -- 이 앱에서는 그 값이 카메라 시리얼이나 스키마 버전이라 조용히 틀린
설정으로 수집이 시작될 수 있다 (2026-09-06 사용자 보고: "의도치 않게 자꾸
건드리게 됩니다").

막기만 하면 그 위에서는 페이지 스크롤도 멎는다. 마법사 하드웨어 페이지처럼
칼럼의 절반이 콤보인 화면에서는 그게 더 답답하므로, 삼킨 휠을 가장 가까운
스크롤 영역으로 넘겨 준다 -- 콤보 위에서도 페이지는 평소처럼 굴러간다.

값은 클릭·키보드로만 바뀐다 (포커스가 있으면 위아래 화살표가 그대로 듣는다).
열린 드롭다운 목록의 스크롤은 영향받지 않는다: 그때 휠은 콤보가 아니라 팝업
뷰로 가기 때문에 이 필터에 걸리지 않는다.

위젯마다 wheelEvent 를 재정의하지 않고 앱 필터 하나로 두는 이유는, 콤보가
30곳 가까이 흩어져 있고 새로 만드는 화면이 이 처리를 잊으면 그 화면만 옛
동작으로 돌아가기 때문이다.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollArea,
)

#: 휠을 막을 위젯. 값이 한 칸씩 굴러가는 것들이다.
GUARDED = (QComboBox, QAbstractSpinBox)


def _scroll_ancestor(widget) -> "QScrollArea | None":
    """이 위젯을 담고 있는 가장 가까운 스크롤 영역."""
    node = widget.parentWidget()
    while node is not None:
        if isinstance(node, QScrollArea):
            return node
        node = node.parentWidget()
    return None


class WheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, GUARDED):
            return False
        area = _scroll_ancestor(obj)
        if area is not None:
            # 값 대신 페이지가 움직인다. viewport 로 보내는 이유: QScrollArea
            # 자신은 휠을 viewport 에서 받아 처리한다.
            QApplication.sendEvent(area.viewport(), event)
        return True     # 콤보에는 닿지 않는다


def install_wheel_guard(app) -> WheelGuard:
    """앱에 필터를 건다. 반환값은 붙잡아 둬야 한다 -- 가비지로 사라지면
    필터도 같이 사라진다."""
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
