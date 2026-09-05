"""휠이 콤보·스핀 값을 바꾸지 않고 페이지를 스크롤하는지 검증.

Qt 기본값에서는 드롭다운 위에서 휠을 굴리면 선택이 바뀐다. 페이지를
스크롤하려던 손짓이 값을 바꾸고, 바뀐 줄도 모르고 지나간다 -- 이 앱에서는
그 값이 카메라 시리얼이나 스키마 버전이라 조용히 틀린 설정으로 수집이
시작될 수 있다 (2026-09-06 사용자 보고).

막기만 해서는 안 된다: 그러면 콤보 위에서 페이지 스크롤이 멎어, 칼럼의
절반이 콤보인 화면은 더 답답해진다. 값은 안 바뀌고 페이지는 굴러가는 것이
계약이라 둘 다 본다.
"""
import sys
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)
sys.argv = ["t"]

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

app = QApplication(sys.argv)

from gello.gui.wheel_guard import install_wheel_guard  # noqa: E402


def wheel(widget, dy=-120):
    """widget 위에서 휠을 한 칸 굴린다."""
    ev = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))),
                     QPoint(0, dy), QPoint(0, dy),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)
    app.processEvents()


def build():
    """스크롤이 실제로 생길 만큼 긴 폼 + 그 안의 콤보/스핀."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    inner = QWidget()
    col = QVBoxLayout(inner)
    combo = QComboBox()
    combo.addItems([f"item{i}" for i in range(10)])
    combo.setCurrentIndex(5)
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    col.addWidget(combo)
    col.addWidget(spin)
    for _ in range(60):                      # 뷰포트보다 길게
        col.addWidget(QWidget())
    area.setWidget(inner)
    area.resize(300, 200)
    area.show()
    app.processEvents()
    return area, combo, spin


# --- 1) 가드가 없으면 값이 바뀐다 (기본 Qt 동작 확인) ---------------------
# 이 줄이 없으면 아래 2번이 "원래 안 바뀌는 것"을 보고도 통과할 수 있다.
area, combo, spin = build()
wheel(combo)
wheel(spin)
assert combo.currentIndex() != 5 or spin.value() != 50, \
    "가드 없이도 값이 안 바뀐다 -- 이 테스트는 아무것도 증명하지 못한다"
print("1 통과: 가드가 없으면 휠이 값을 바꾼다 (Qt 기본 동작)")

# --- 2) 가드를 걸면 값이 그대로다 -----------------------------------------
guard = install_wheel_guard(app)             # noqa: F841 -- 살려 둬야 한다
area, combo, spin = build()
for _ in range(3):
    wheel(combo)
    wheel(spin)
assert combo.currentIndex() == 5, f"휠이 콤보 값을 바꿨다: {combo.currentIndex()}"
assert spin.value() == 50, f"휠이 스핀 값을 바꿨다: {spin.value()}"
print("2 통과: 휠이 콤보·스핀 값을 바꾸지 않는다")

# --- 3) 그래도 페이지는 스크롤된다 ----------------------------------------
bar = area.verticalScrollBar()
assert bar.maximum() > 0, "스크롤이 안 생기는 폼이라 3번을 볼 수 없다"
bar.setValue(0)
app.processEvents()
for _ in range(3):
    wheel(combo)
assert bar.value() > 0, \
    f"콤보 위에서 페이지가 안 굴러간다 (스크롤 {bar.value()}) -- 막기만 했다"
print(f"3 통과: 콤보 위에서도 페이지가 스크롤된다 ({bar.value()}px)")

# --- 4) 키보드로는 여전히 바꿀 수 있다 ------------------------------------
# 휠만 막는 것이지 값을 잠그는 것이 아니다.
combo.setFocus()
combo.setCurrentIndex(5)
QApplication.sendEvent(combo, __import__("PyQt6.QtGui", fromlist=["QKeyEvent"])
                       .QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down,
                                  Qt.KeyboardModifier.NoModifier))
app.processEvents()
assert combo.currentIndex() == 6, f"화살표로도 못 바꾼다: {combo.currentIndex()}"
print("4 통과: 포커스가 있으면 화살표로는 그대로 바뀐다")

# --- 5) 열린 드롭다운 목록은 여전히 스크롤된다 ----------------------------
# 필터는 QComboBox 자신에게 온 휠만 막는다. 팝업이 열려 있으면 휠은 콤보가
# 아니라 팝업 뷰(QAbstractItemView)로 가므로 걸리지 않는다 -- 항목이 많은
# 목록을 못 굴리면 그게 더 큰 문제다.
long_combo = QComboBox()
long_combo.addItems([f"row{i}" for i in range(200)])
long_combo.show()
app.processEvents()
long_combo.showPopup()
app.processEvents()
view = long_combo.view()
vbar = view.verticalScrollBar()
assert vbar.maximum() > 0, "팝업에 스크롤이 없어 5번을 볼 수 없다"
vbar.setValue(0)
app.processEvents()
wheel(view.viewport())
assert vbar.value() > 0, f"열린 드롭다운이 안 굴러간다 (스크롤 {vbar.value()})"
long_combo.hidePopup()
print(f"5 통과: 열린 드롭다운 목록은 그대로 스크롤된다 ({vbar.value()})")

print("\n휠 가드 검증 통과")
