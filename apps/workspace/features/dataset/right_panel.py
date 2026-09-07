"""Dataset 의 우측 패널 -- 고른 에피소드의 값과 그 scene 의 배치.

왼쪽 목록의 열(프레임·결과·수집자)은 패널이 좁으면 잘린다. 열을 없애자는
것이 아니라 -- 여럿을 훑을 때는 목록이 맞다 -- **고른 한 줄은 오른쪽에서
온전히 읽히게** 한다 (2026-09-07 조작자). 같은 값이 두 군데 나오지만, 두
자리가 하는 일이 다르다: 목록은 고르는 자리, 오른쪽은 읽는 자리다.

배치도는 **고른 scene 의 것**이다. 전에는 "Scene 배치 (수집 중)" 이라 세션이
없으면 늘 비어 있었는데, 큐레이션 중에 "이 에피소드가 어떤 배치였지" 를 묻는
일이 잦다 -- 그때 Doctor 나 Configure 로 건너가야 했다.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from apps.workspace.shared.collapsible import CollapsibleBox
from apps.workspace.shared.info import InfoCard, ZoneMap
from gello.gui.i18n import tr

#: 기준 사진 가로 상한 (닥터와 같은 값).
PHOTO_W = 320


def build_dataset_right(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    box = CollapsibleBox(tr("Episode"))
    win.ds_episode_card = InfoCard()
    box.body.addWidget(win.ds_episode_card)
    col.addWidget(box)

    box = CollapsibleBox(tr("Scene"))
    win.ds_scene_card = InfoCard()
    box.body.addWidget(win.ds_scene_card)
    win.ds_scene_zones = ZoneMap()
    box.body.addWidget(win.ds_scene_zones)
    win.ds_scene_photo = QLabel(tr("기준 사진 없음"))
    win.ds_scene_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    win.ds_scene_photo.setStyleSheet(
        "background:#e4e4e4; color:#444; border:1px solid #949494;")
    box.body.addWidget(win.ds_scene_photo)
    col.addWidget(box)

    box = CollapsibleBox(tr("File"), open_=False)
    # 파일 단위 값은 접어 둔다 -- 에피소드를 훑는 동안에는 안 바뀌고, 펴 두면
    # 정작 바뀌는 값(위 Episode)이 화면 밖으로 밀린다.
    win.ds_file_card = InfoCard()
    box.body.addWidget(win.ds_file_card)
    col.addWidget(box)

    col.addStretch(1)
    return w
