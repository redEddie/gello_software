"""카메라 미리보기 칼럼 — 하드웨어 페이지의 오른쪽 절반.

설정(왼쪽)과 미리보기(오른쪽)를 아예 다른 칼럼으로 나눈다. 아래로 쌓으면
모니터가 16:9 로 옆으로 긴 것을 못 쓰고, 무엇보다 "여기는 설정하는 곳,
저기는 보는 곳"이 한눈에 안 갈린다 (2026-09-05 사용자 결정).

역할 이름은 그림 아래 별도 라벨이 아니라 **그림 위 오버레이**다. 아래에
두면 라벨과 그림의 폭이 달라지는 순간 정렬이 어긋나 어느 캡션이 어느
그림의 것인지 애매해졌다 -- 실제로 그림이 셀 안에서 왼쪽으로 치우쳐
그렇게 됐다. 오버레이는 그림에 붙어 다니므로 어긋날 수가 없다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from gello.gui.widgets import VideoView
from gello.gui.i18n import tr


class PreviewCell(QWidget):
    """미리보기 한 칸 — VideoView 위에 역할 이름을 겹쳐 그린다."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        # 같은 grid 칸에 둘을 넣으면 겹친다 -- 좌표를 손으로 계산하지 않아도
        # 되고, 창 크기가 바뀌어도 따라간다.
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        self.view = VideoView()
        self.view.setMinimumSize(160, 120)
        # 정사각 크롭 가이드는 끈다. 여기서 볼 것은 "어느 쪽이 손목인가"
        # 하나뿐이라, 좌우를 어둡게 덮으면 판별만 어려워진다. 프레이밍은
        # 워크스페이스 Layout 패널에서 맞춘다.
        self.view.set_square_guide(False)
        self.view.setText(tr("대기"))
        grid.addWidget(self.view, 0, 0)
        self.caption = QLabel(title)
        self.caption.setStyleSheet(
            "background: rgba(0,0,0,160); color:#fff; font-weight:bold;"
            " padding:2px 8px; border-radius:3px;")
        grid.addWidget(self.caption, 0, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)


class CameraPreviewColumn(QGroupBox):
    """역할별 미리보기를 세로로 쌓은 칼럼."""

    def __init__(self, roles, parent=None) -> None:
        super().__init__(tr("미리보기"), parent)
        col = QVBoxLayout(self)
        self.cells: dict[str, PreviewCell] = {}
        for role, title in roles:
            cell = PreviewCell(title)
            col.addWidget(cell, 1)
            self.cells[role] = cell

    def views(self) -> "dict[str, VideoView]":
        """role -> VideoView. 프레임을 넣는 쪽은 셀 구조를 몰라도 된다."""
        return {role: cell.view for role, cell in self.cells.items()}
