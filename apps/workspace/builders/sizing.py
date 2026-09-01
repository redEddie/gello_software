"""Low-level UI layout helpers used by workspace builders."""
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QWidget,
)


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


