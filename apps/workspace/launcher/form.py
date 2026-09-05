"""마법사 폼의 세로 여백을 한 곳에서 정한다.

칸 높이를 픽셀로 박지 않는 이유: 글꼴이 바뀌면 박아 둔 값이 그대로 남아
글자가 다시 잘린다. 글꼴 높이에서 유도해, 글꼴을 바꾸면 칸도 같이 따라오게
한다.

왜 기본값으로는 모자랐나 (2026-09-05 사용자 보고): 한글은 받침 때문에 라틴
글자보다 세로로 꽉 차는데, Qt 의 기본 sizeHint 는 라틴 기준이라 위아래가
살짝 잘려 보인다. 게다가 오른쪽 칼럼의 최소 높이 합이 창보다 커서 Qt 가
칸들을 sizeHint 아래로까지 눌러 버리고 있었다 (실측: 22px 힌트가 20px 로,
한 칸은 14px 까지).
"""
from __future__ import annotations

from PyQt6.QtGui import QFontMetrics

#: 글자 위아래로 둘 여백(px). 12 는 실측으로 고른 값 -- 8 은 여전히 빠듯하고
#: 16 은 폼이 세로로 길어져 창이 1080p 화면을 넘는다.
FIELD_PADDING = 12
#: 폼 줄 사이. Qt 기본 6 은 줄이 여덟을 넘어가면 빽빽하게 읽힌다.
ROW_SPACING = 8


def roomy(*widgets) -> None:
    """입력 칸이 글자를 자르지 않을 최소 높이를 준다."""
    for w in widgets:
        w.setMinimumHeight(QFontMetrics(w.font()).height() + FIELD_PADDING)


def tidy_form(*forms) -> None:
    """폼 줄 간격을 통일한다."""
    for f in forms:
        f.setVerticalSpacing(ROW_SPACING)
        f.setHorizontalSpacing(10)
