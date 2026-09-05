"""화면을 짜는 규칙을 한 곳에. 런처와 워크스페이스가 같이 쓴다.

여기 모으는 이유: 같은 실수가 화면마다 새로 나기 때문이다. 2026-09-05~06 에
런처에서 실제로 겪은 것들이고, 워크스페이스 화면을 손볼 때도 그대로 적용한다.

* **칸 높이는 글꼴에서 유도한다** (``roomy``). 픽셀로 박으면 글꼴이 바뀔 때
  그 값이 남아 글자가 잘린다. 한글은 받침 때문에 Qt 의 라틴 기준 sizeHint
  로는 위아래가 빠듯하다.
* **길어질 수 있는 화면은 스크롤에 넣는다** (``scrollable``). QVBoxLayout 은
  자리가 모자라면 위젯을 minimumSizeHint **아래로까지** 줄여서, 입력 칸들이
  서로 겹쳐 보인다. 창을 키우는 것으로는 못 막는다 -- 항목 수에 상한이 없다.
* **가로 스크롤은 쓰지 않는다.** 폭이 모자라면 줄어들고 말줄임한다
  (``relax_min_widths``, ``shrinkable_combo``).

휠로 콤보·스핀 값이 바뀌지 않게 하는 것은 앱 전역 필터가 맡는다
(``gello.gui.wheel_guard``) -- 화면마다 챙길 필요가 없다.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

#: 글자 위아래로 둘 여백(px). 12 는 실측으로 고른 값 -- 8 은 여전히 빠듯하고
#: 16 은 폼이 세로로 길어져 창이 1080p 화면을 넘는다.
FIELD_PADDING = 12
#: 폼 줄 사이. Qt 기본 6 은 줄이 여덟을 넘어가면 빽빽하게 읽힌다.
ROW_SPACING = 8


def roomy(*widgets) -> None:
    """입력 칸이 글자를 자르지 않을 최소 높이를 준다 (글꼴 높이 + 여백)."""
    for w in widgets:
        w.setMinimumHeight(QFontMetrics(w.font()).height() + FIELD_PADDING)


def tidy_form(*forms) -> None:
    """폼 줄 간격을 통일한다."""
    for f in forms:
        f.setVerticalSpacing(ROW_SPACING)
        f.setHorizontalSpacing(10)


def scrollable(inner: QWidget) -> QScrollArea:
    """내용이 창보다 길어지면 세로 스크롤이 생기게 감싼다.

    가로 스크롤은 끈다 -- 폭이 모자라면 콤보가 말줄임으로 줄어드는 편이
    (shrinkable_combo) 옆으로 미는 것보다 낫다. 테두리도 끈다: 상자들이 이미
    자기 테두리를 갖고 있어 한 겹 더 그리면 중첩돼 보인다.
    """
    area = QScrollArea()
    area.setWidgetResizable(True)      # 내용이 짧으면 뷰포트를 채운다
    area.setWidget(inner)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


def relax_min_widths(root: QWidget) -> None:
    """좌측 패널은 가로 스크롤이 없으므로 자식들이 패널 폭에 맞춰 줄어들 수
    있어야 한다. 버튼·체크박스·라디오는 텍스트 전체 폭을 최소로 고집하는
    기본 정책이라 좁은 패널에서 페이지를 잘리게 만든다 -- 수평 최소를 풀어
    좁아지면 글자가 생략되는 쪽을 택한다 (2026-08-13 사용자 결정: 200px
    수준까지 축소 허용, ... 요약 표시 허용). '...' 찾아보기처럼 명시적으로
    고정폭을 준 위젯은 건드리지 않는다."""
    for w in root.findChildren(QWidget):
        if isinstance(w, (QPushButton, QCheckBox, QRadioButton)):
            if w.maximumWidth() >= 16777215:  # 명시 고정폭은 존중
                sp = w.sizePolicy()
                sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                w.setSizePolicy(sp)
    # 폼의 '라벨+입력 나란히' 배치도 최소 폭을 만든다 -- 좁아지면 입력칸이
    # 라벨 아래로 내려가게 해서 폭 하한을 더 낮춘다.
    for f in root.findChildren(QFormLayout):
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # 긴 안내문 라벨이 wordWrap 없이 폭을 강제하는 경우가 페이지마다 하나씩
    # 숨어 있다(업로드 큐 안내문 등). 일괄 줄바꿈 -- 단, 수평 Ignored 정책
    # 라벨(SceneInfoView 의 격자처럼 일부러 줄바꿈을 막은 것)은 제외.
    for lb in root.findChildren(QLabel):
        if lb.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Ignored:
            lb.setWordWrap(True)
            # wordWrap 만으로는 QFormLayout 이 높이를 한 줄치로 줘서 두 줄째가
            # 잘린다(오른쪽 패널 WIDE_FIELDS 에서 이미 확인된 Qt 동작).
            # heightForWidth 를 켜야 접힌 만큼 세로가 확보된다.
            sp = lb.sizePolicy()
            sp.setHeightForWidth(True)
            sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
            lb.setSizePolicy(sp)






def shrinkable_combo(c: QComboBox) -> None:
    """항목 텍스트(카메라 이름, scene 설명 등)가 길어도 콤보가 패널 폭에 맞춰
    줄어들 수 있게 한다. 기본 정책은 가장 긴 항목만큼 최소 폭을 요구해서,
    좁은 좌측 패널에서 페이지 전체가 오른쪽으로 잘려 나갔다 (가로 스크롤을
    쓰지 않는다는 원칙과 충돌). 펼친 목록은 전체 텍스트를 그대로 보여준다."""
    c.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(6)


