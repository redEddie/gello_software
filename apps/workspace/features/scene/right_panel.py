"""② Configure 의 우측 패널 -- 이 화면에서 **할 수 있는 일**.

우측 패널은 활동탭마다 자기 구현을 갖고, 그 구현은 **동작을 모아 두는
자리**다 (2026-09-07 사용자 결정, 규칙의 정본은 layout.build_right).

왜 정보가 아니라 동작인가: 정보만 있는 패널은 상호작용할 것이 없어서 결국
아무도 안 본다 -- 그러면 "안 보니까 정보를 더 잘 놓자" 가 아니라 "볼 이유를
주자" 가 답이다. 실제로 이 화면의 종착 동작인 [만들기] 는 격자 칸 버튼 9개와
똑같이 생긴 채 탭 맨 아래에 있었고, 조작자는 그 버튼을 못 찾아 "scene 을
만들어도 등록이 안 된다" 고 읽었다 (2026-09-07). 버튼이 늘 같은 자리에 있으면
그 오독이 생기지 않는다.

여기 없는 것: 격자 칸 9개, 재생 ◀▶, 경로 [...] 같은 **직접 조작**. 그것들은
"일" 이 아니라 위젯을 만지는 것이라 만지는 자리에 남는다.
"""
from PyQt6.QtWidgets import (
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr

#: 종착 동작 하나에만 색을 준다. 색이 둘 이상이면 그 순간 아무것도 안
#: 도드라진다. 파괴적 종착 동작이 붉은 것과 짝 (trim_tab 의 [확정]).
_PRIMARY = ("background-color:#2d7d46; color:white; padding:6px;"
            "font-weight:bold;")


def build_configure_right(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # --- Scene 짜기 ---------------------------------------------------
    box = QGroupBox(tr("Scene 짜기"))
    bv = QVBoxLayout(box)
    bv.setContentsMargins(6, 6, 6, 6)

    win.scene_create_btn = QPushButton("")
    win.scene_create_btn.setStyleSheet(_PRIMARY)
    win.scene_create_btn.setMinimumHeight(36)
    win.scene_create_btn.clicked.connect(win.scene_ops.on_compose_done)
    bv.addWidget(win.scene_create_btn)

    # Action 계층은 영어 (i18n.py 정책).
    rec = QPushButton(tr("Recommend scene..."))
    rec.setToolTip(tr(
        "기존 scene 들과 가장 다른 소품 조합·배치 3안을 추천받아\n"
        "체크·배치를 자동으로 채웁니다 (#33, 다양성 최대화)."))
    rec.clicked.connect(win.scene_composer.open_recommend_scene)
    bv.addWidget(rec)

    win.scene_layout_btn = QPushButton(tr("Recommend layout..."))
    win.scene_layout_btn.clicked.connect(win.scene_composer.open_recommend_layout)
    bv.addWidget(win.scene_layout_btn)

    # 만든 결과("scene_002.hdf5 를 만들고 계획에 S002 를 넣었습니다")가 뜨는
    # 자리. 누른 자리에서 결과를 읽는 편이 중앙 탭 아래로 눈을 옮기는 것보다
    # 낫다 -- 누른 뒤에는 Instruction 탭으로 넘어가 그 탭이 안 보인다.
    win.scene_compose_hint = QLabel("")
    win.scene_compose_hint.setStyleSheet("color:#888;")
    win.scene_compose_hint.setWordWrap(True)
    bv.addWidget(win.scene_compose_hint)
    col.addWidget(box)

    # --- 계획 ---------------------------------------------------------
    pbox = QGroupBox(tr("계획"))
    pv = QVBoxLayout(pbox)
    pv.setContentsMargins(6, 6, 6, 6)
    edit = QPushButton(tr("계획 편집..."))
    edit.setToolTip(tr("이 데이터셋의 지시문과 목표 개수를 고칩니다 "
                       "(저장할 때 규칙을 검사합니다).\n"
                       "계획이 없으면 만들고 엽니다."))
    edit.clicked.connect(win.scene_planning.on_edit_plan)
    pv.addWidget(edit)
    refresh = QPushButton(tr("현황 새로고침"))
    refresh.setToolTip(tr(
        "계획의 모든 scene 파일을 다시 읽습니다 (파일 수에 비례해 몇백 ms)."))
    refresh.clicked.connect(win.scene_planning.refresh_plan_progress)
    pv.addWidget(refresh)
    col.addWidget(pbox)

    col.addStretch(1)

    # 구성이 바뀔 때마다 버튼 상태를 다시 묻는다. 못 누르는 이유는 툴팁이
    # 말한다 -- composer 가 그 문장까지 준다.
    win.scene_composer.changed.connect(
        lambda: sync_configure_right(win))
    sync_configure_right(win)
    return w


def sync_configure_right(win) -> None:
    """composer 의 지금 상태를 버튼 셋에 반영한다."""
    comp = getattr(win, "scene_composer", None)
    if comp is None:
        return
    ok, tip = comp.create_button_state()
    btn = win.scene_create_btn
    btn.setText(tr("✚ {sid} 만들기").format(sid=comp.scene_id))
    btn.setEnabled(ok)
    btn.setToolTip(tip)
    # 못 누르는 동안에는 색을 빼서 "지금은 아니다" 를 색으로도 말한다.
    btn.setStyleSheet(_PRIMARY if ok else "padding:6px;")
    ok, tip = comp.layout_button_state()
    win.scene_layout_btn.setEnabled(ok)
    win.scene_layout_btn.setToolTip(tip)
