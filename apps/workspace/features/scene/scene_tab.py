"""Scene 탭 껍데기 -- 구성기(SceneComposer) 하나뿐이다.

구성 자체는 scene_composer.py 가 한다. **동작 버튼은 여기 없다** --
[만들기]·[Recommend ...] 는 우측 패널(features/scene/right_panel.py)에 있다
(2026-09-07 사용자 결정, 규칙의 정본은 layout.build_right).

전에는 [이 구성으로 만들기] 가 이 탭 맨 아래, 회색 힌트 옆에 있었다. 같은
화면에 똑같이 생긴 QPushButton 이 12개(격자 9 + 추천 2 + 만들기 1)라 종착
동작이 12분의 1의 무게로 묻혔고, 조작자가 그 버튼을 못 찾아 "scene 을 만들어도
등록이 안 된다" 고 읽었다.

누르면 그 자리에서 ``scene_00N.hdf5`` 가 생긴다 (에피소드 0개) -- 계약은
ops.on_compose_done 에 적혀 있다. Connect 를 기다리지 않는다.
"""
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from apps.workspace.features.scene.scene_composer import SceneComposer


def build_scene_tab(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(6, 6, 6, 6)
    win.scene_composer = SceneComposer(w)
    col.addWidget(win.scene_composer, 1)
    return w
