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

## 버튼의 종류 (2026-09-07 조작자 지적: "버튼들의 종류가 섞였다")

상자는 **대상**으로 나눈다 (Scene / 지시문). 종류로 또 쪼개면 상자만 는다.
대신 한 상자 안에서 종류가 **무게와 자리**로 읽히게 한다:

    커밋   누르면 디스크가 바뀐다. 되돌리려면 따로 지워야 한다.
           -> 상자 맨 위, 색이 있다. **상자마다 하나뿐**이다.
    ─────  구분선. 이 아래는 아무것도 안 남긴다.
    도우미 지금 짜는 중인 것만 건드린다. 파일은 안 생긴다.
           -> 보통 버튼.
    조회   아무것도 안 바꾼다. 다시 읽을 뿐이다.
           -> 작고 밋밋하게, 맨 아래.

배울 규칙이 하나다: **색이 있으면 파일이 바뀐다. 구분선 아래는 안 남는다.**
"""
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.shared.info import WrapLabel
from gello.gui.i18n import tr

#: 종착 동작 하나에만 색을 준다. 색이 둘 이상이면 그 순간 아무것도 안
#: 도드라진다. 파괴적 종착 동작이 붉은 것과 짝 (trim_tab 의 [확정]).
_PRIMARY = ("background-color:#2d7d46; color:white; padding:6px;"
            "font-weight:bold;")
#: 조회 버튼 -- 아무것도 안 바꾸므로 동작 버튼의 무게를 주지 않는다.
_PEEK = "border:none; color:#666; text-align:left; padding:2px 0;"


def _rule() -> QFrame:
    """구분선 -- 위는 커밋, 아래는 아무것도 안 남기는 것."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#d0d0d0;")
    return f


def build_configure_right(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # --- Scene ---------------------------------------------------------
    box = QGroupBox(tr("Scene"))
    bv = QVBoxLayout(box)
    bv.setContentsMargins(6, 6, 6, 6)
    # 스타일시트로 padding 을 준 버튼은 레이아웃이 잡아 준 자리보다 크게
    # 그려진다 -- 간격을 안 주면 바로 밑 라벨의 첫 줄이 덮인다 (실측).
    bv.setSpacing(6)

    # 라벨에 scene 번호를 넣지 않는다 (2026-09-07 조작자 지적: "+ S000
    # 만들기는 동작이 이해가 안 간다"). 번호는 조작자가 고르는 것이 아니라
    # 자동으로 붙는 것이라, 동작 버튼에 있으면 "어느 것을 만들지 고르는"
    # 것처럼 읽힌다. 몇 번이 될지는 아래 상태 줄과 툴팁이 말한다.
    win.scene_create_btn = QPushButton(tr("✚ 새 Scene 만들기"))
    win.scene_create_btn.setStyleSheet(_PRIMARY)
    win.scene_create_btn.setMinimumHeight(36)
    win.scene_create_btn.clicked.connect(win.scene_ops.on_compose_done)
    bv.addWidget(win.scene_create_btn)

    # 이 탭에는 [저장] 이 따로 없다 -- 위 버튼이 곧 저장이다. 그 사실을
    # 늘 이 줄이 말한다 (composer.save_state_text 가 정본).
    win.scene_save_state = WrapLabel("")
    win.scene_save_state.setStyleSheet("color:#555;")
    bv.addWidget(win.scene_save_state)

    # 만든 결과("scene_002.hdf5 를 만들고 계획에 S002 를 넣었습니다")가 뜨는
    # 자리. 누른 자리에서 결과를 읽는 편이 중앙 탭 아래로 눈을 옮기는 것보다
    # 낫다 -- 누른 뒤에는 Instruction 탭으로 넘어가 그 탭이 안 보인다.
    win.scene_compose_hint = WrapLabel("")
    win.scene_compose_hint.setStyleSheet("color:#888;")
    bv.addWidget(win.scene_compose_hint)

    # 여기부터는 아무것도 안 남긴다 -- 지금 짜는 중인 구성만 건드린다.
    bv.addWidget(_rule())
    helper = QLabel(tr("도우미 — 파일은 생기지 않습니다"))
    helper.setStyleSheet("color:#888; font-size:11px;")
    bv.addWidget(helper)

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

    # 추천이 체크를 통째로 갈아 끼우듯, 이것도 통째로 지운다 -- 그래서
    # 격자 칸(직접 조작)이 아니라 여기다.
    win.scene_clear_btn = QPushButton(tr("전체 해제"))
    win.scene_clear_btn.clicked.connect(win.scene_composer.clear)
    bv.addWidget(win.scene_clear_btn)
    col.addWidget(box)

    # --- 지시문 --------------------------------------------------------
    pbox = QGroupBox(tr("지시문"))
    pv = QVBoxLayout(pbox)
    pv.setContentsMargins(6, 6, 6, 6)
    pv.setSpacing(6)
    edit = QPushButton(tr("지시문 편집..."))
    edit.setToolTip(tr("이 데이터셋의 지시문과 목표 개수를 고칩니다 "
                       "(저장할 때 규칙을 검사합니다).\n"
                       "지시문이 없으면 만들고 엽니다."))
    edit.clicked.connect(win.scene_planning.on_edit_plan)
    pv.addWidget(edit)
    # 조회 -- 아무것도 안 바꾼다. 위의 편집(커밋)과 같은 무게로 보이면 안 된다.
    pv.addWidget(_rule())
    refresh = QPushButton(tr("현황 새로고침 ↻"))
    refresh.setStyleSheet(_PEEK)
    refresh.setToolTip(tr(
        "지시문에 적힌 모든 scene 파일을 다시 읽습니다 (파일 수에 비례해 몇백 ms). "
        "바뀌는 것은 없습니다."))
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
    btn.setEnabled(ok)
    btn.setToolTip(tip)
    # 못 누르는 동안에는 색을 빼서 "지금은 아니다" 를 색으로도 말한다.
    btn.setStyleSheet(_PRIMARY if ok else "padding:6px;")
    win.scene_save_state.setText(comp.save_state_text())
    ok, tip = comp.layout_button_state()
    win.scene_layout_btn.setEnabled(ok)
    win.scene_layout_btn.setToolTip(tip)
    n = comp.checked_count()
    win.scene_clear_btn.setEnabled(bool(n))
    win.scene_clear_btn.setToolTip(
        tr("체크한 물체 {n}개와 그 배치를 모두 지웁니다 (설명 칸은 둡니다).")
        .format(n=n) if n else tr("체크한 물체가 없습니다."))
