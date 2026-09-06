"""Scene 탭 껍데기 -- 구성기(SceneComposer) + [이 구성으로 시작].

구성 자체는 scene_composer.py 가 한다. 여기 있는 것은 "다 짰다"를 알리는
버튼 하나뿐이다: 누르면 규칙을 검사하고, 통과하면 Configure 의 **대기 scene**
으로 얹는다. 실제 파일은 그때 만들지 않는다 -- Connect 할 때 SceneWriter 가
만든다 (전 대화상자와 같은 계약).
"""
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gello.gui.i18n import tr

from apps.workspace.features.scene.scene_composer import SceneComposer


def build_scene_tab(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(6, 6, 6, 6)

    win.scene_composer = SceneComposer(w)
    col.addWidget(win.scene_composer, 1)

    row = QHBoxLayout()
    win.scene_compose_hint = QLabel("")
    win.scene_compose_hint.setStyleSheet("color:#888;")
    win.scene_compose_hint.setWordWrap(True)
    row.addWidget(win.scene_compose_hint, 1)
    apply_btn = QPushButton(tr("이 구성으로 만들기"))
    apply_btn.setToolTip(tr(
        "누르는 즉시 scene 파일이 만들어집니다 (에피소드 0개).\n"
        "여러 개를 미리 만들어 두고 나중에 골라 찍을 수 있습니다.\n"
        "잘못 만들었으면 Dataset 의 [파일 삭제] 로 지웁니다."))
    apply_btn.clicked.connect(win.scene_ops.on_compose_done)
    row.addWidget(apply_btn)
    col.addLayout(row)
    return w
