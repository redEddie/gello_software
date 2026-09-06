"""Collect page builder for WorkspaceWindow.

2026-09-06 개편. 이 화면은 **리더암을 두 손으로 잡은 사람이 1m 밖에서
곁눈질하는 화면**이다. 그 전제로 다시 짰다:

* **툴바·HUD 가 이미 말하는 것은 여기서 뺀다.** 에피소드 한 바퀴 버튼 7개는
  툴바 고정 구획에 같은 이름으로 있고(그쪽은 어느 화면에서든 남는다),
  상태 이름과 수집량은 카메라 위 HUD 띠가 훨씬 크게 말한다. 남은 것은
  **여기서만 알 수 있는 것**뿐이다.
* **데이터셋 전체 진행률 표를 뺐다.** scene 마다 Connect/Disconnect 하는
  운용이라, 수집 중에 다른 scene 의 숫자는 볼 일이 없다. 그 표는 ② Configure
  의 Plan 탭으로 갔다 (거기서는 "다음에 무엇을 찍을까"의 답이다).
* **지시문은 목록에서 한 줄 누르면 바로 바뀐다.** 드롭다운에서 고르고
  ID 칸·문장 칸을 확인한 뒤 [적용] 을 누르던 네 단계가 한 번으로 줄었다.
* **버튼 대신 단축키 표.** 손이 리더암에 있으면 누르는 것은 키지 버튼이
  아니다. 지금 살아 있는 키만 초록으로 밝힌다.

상자 순서는 "확정적인 것 · 자주 보는 것" 부터다 (사용자 규칙):
Instruction(이번 세션에 무엇을 찍는가) -> 지금(매 순간 변하는 것) ->
Pose gate(정렬 중에만) -> Keys(외우면 안 보는 것).
"""
from PyQt6.QtWidgets import (
    QGroupBox,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.gui.widgets import DeltaBar
from gello.gui.fonts import MONO_STACK, set_bold
from gello.gui.i18n import tr

from apps.workspace.shared.sizing import cap_rows, keep_height

#: 단축키 표 -- (키, 하는 일, 그 키가 살아 있는 상태들).
#:
#: Space 가 두 가지인 것은 상태에 따라 뜻이 갈리기 때문이다 (게이트에서는
#: 시작, 기록 중에는 성공으로 완료). 두 줄로 나누면 같은 키가 두 번 나와
#: 오히려 헷갈리므로 한 줄에 둘 다 적는다 (2026-09-06 사용자 지정).
#:
#: 키 이름은 영어(Identity), 설명은 한국어(Guide) -- i18n.py 의 계층.
#: 정본은 eventFilter 다. 여기 적힌 것과 실제로 받는 키가 갈라지면 안 되므로
#: 상태 이름도 워커의 것을 그대로 쓴다.
KEY_MAP = (
    ("Space", "녹화 시작 / 성공으로 완료", ("gate", "recording")),
    ("Esc", "실패로 완료 / 직전 판정 뒤집기", ("recording", "reset_wait")),
    ("Del", "이 에피소드 폐기", ("recording",)),
    ("Enter", "자동 정렬 / 리셋 완료 — 계속", ("gate", "reset_wait")),
)


def build_collect(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # ---------------------------------------------------------- Instruction
    # 이 세션이 무엇을 찍고 있는가. 가장 확정적이고 가장 자주 보는 것이라
    # 맨 위다. scene 세션에서만 보인다 (legacy 는 파일 하나 = task 하나).
    box = QGroupBox(tr("Instruction"))
    win.instr_box = box
    icol = QVBoxLayout(box)
    # "S001 · I001 · 3/10" -- 조작자가 요청한 그 한 줄. 정체(어느 scene 의
    # 어느 지시문)와 진척(몇 개 찍었나)이 한 줄에 있으면 더 볼 것이 없다.
    win.instr_counter = QLabel(tr("—"))
    set_bold(win.instr_counter, 16)
    win.instr_counter.setStyleSheet("color:#888;")
    icol.addWidget(win.instr_counter)
    win.instr_sentence = QLabel("")
    win.instr_sentence.setWordWrap(True)
    win.instr_sentence.setStyleSheet("color:#ddd;")
    icol.addWidget(win.instr_sentence)
    # 계획의 지시문 목록. **한 줄을 누르면 그 지시문으로 바뀐다** -- 다음
    # 에피소드부터 적용된다 (진행 중인 것은 워커가 시작 시점에 캡처했다).
    # 드롭다운 + ID 칸 + 문장 칸 + [적용] 버튼을 대체한다.
    win.instr_tree = QTreeWidget()
    win.instr_tree.setHeaderLabels([tr("ID"), tr("수집"), tr("지시문")])
    win.instr_tree.setRootIsDecorated(False)
    win.instr_tree.setColumnWidth(0, 54)
    win.instr_tree.setColumnWidth(1, 56)
    win.instr_tree.header().setSectionResizeMode(
        2, QHeaderView.ResizeMode.Stretch)
    win.instr_tree.setToolTip(tr(
        "한 줄을 누르면 그 지시문으로 바뀝니다 (다음 에피소드부터).\n"
        "진행 중인 에피소드에는 영향이 없습니다."))
    win.instr_tree.itemClicked.connect(
        lambda item, _c: win.scene_planning.on_instruction_picked(item))
    cap_rows(win.instr_tree, 6)
    icol.addWidget(win.instr_tree)
    win.instr_next_btn = QPushButton(tr("Next unfilled"))
    win.instr_next_btn.setToolTip(tr(
        "계획에서 아직 목표를 못 채운 지시문 중 번호가 가장 낮은 것으로 "
        "바꿉니다."))
    win.instr_next_btn.clicked.connect(win.scene_planning.on_next_instruction)
    icol.addWidget(win.instr_next_btn)
    win.instr_warn = QLabel("")
    win.instr_warn.setWordWrap(True)
    win.instr_warn.setStyleSheet("color:#e67e22;")
    icol.addWidget(win.instr_warn)
    box.setVisible(False)
    col.addWidget(box)

    # ----------------------------------------------------------------- 지금
    # 매 순간 변하는 것. 한국어 안내문이 맨 위다 -- "지금 무엇을 하라"는
    # 읽고 이해하는 글이라 모국어로 두고(i18n 계층), 눈에 띄어야 한다.
    now = QGroupBox(tr("지금"))
    ncol = QVBoxLayout(now)
    win.now_hint = QLabel(tr("연결하면 여기에 다음 할 일이 나옵니다."))
    win.now_hint.setWordWrap(True)
    set_bold(win.now_hint, 11)
    win.now_hint.setStyleSheet("color:#2ecc71;")
    ncol.addWidget(win.now_hint)
    # 남은 프레임 -- 에피소드가 언제 끝나는지는 여기서만 알 수 있다
    # (HUD 는 수집량을, 툴바는 동작을 말한다).
    win.ep_progress = QProgressBar()
    win.ep_progress.setFormat("%v / %m frames")
    ncol.addWidget(win.ep_progress)
    win.save_status_label = QLabel("")
    win.save_status_label.setStyleSheet("color:#888;")
    ncol.addWidget(win.save_status_label)
    win.verdict_label = QLabel("")
    win.verdict_label.setWordWrap(True)
    ncol.addWidget(win.verdict_label)
    col.addWidget(now)

    # ------------------------------------------------------------ Pose gate
    # 자세 정렬 중에만 보인다. 델타 바는 gate 루프에서만 갱신되므로
    # (worker._emit_gate_status), 그 밖의 상태에서는 낡은 값을 띄우고 있는
    # 셈이었다 -- 안 쓰는 정보가 아니라 틀린 정보였다.
    gate = QGroupBox(tr("Pose gate"))
    win.gate_box = gate
    gate.setVisible(False)
    gcol = QVBoxLayout(gate)
    win.delta_bars = []
    for i in range(8):
        bar = DeltaBar(f"J{i + 1}" if i < 7 else tr("그리퍼"))
        gcol.addWidget(bar)
        win.delta_bars.append(bar)
    win.gate_label = QLabel(tr("연결 대기 중"))
    win.gate_label.setStyleSheet("color:#888;")
    gcol.addWidget(win.gate_label)
    col.addWidget(gate)

    # ----------------------------------------------------------------- Keys
    # 버튼이 아니라 키다 -- 손이 리더암에 있으면 마우스를 쓸 수 없다. 같은
    # 동작의 버튼은 툴바 고정 구획에 그대로 있으므로 여기서는 매핑만 보인다
    # (2026-09-06 사용자 지정: "control 은 버튼을 줄이고 단축어 매핑만").
    keys = QGroupBox(tr("Keys"))
    kcol = QVBoxLayout(keys)
    kcol.setSpacing(2)
    win.key_rows = {}
    for key, what, _states in KEY_MAP:
        lab = QLabel(f"{key:<6}{what}")
        lab.setStyleSheet(f"color:#888; font-family: {MONO_STACK};")
        kcol.addWidget(lab)
        win.key_rows[key] = lab
    col.addWidget(keys)

    # 줄 수가 정해진 상자만 못박는다 -- Instruction(목록이 자란다)과
    # 지금(안내문이 접힌다)은 늘어나야 한다.
    keep_height(gate, keys)
    col.addStretch()
    return w


def set_live_keys(win, state: str) -> None:
    """지금 쓸 수 있는 키만 초록으로 밝힌다.

    옛 shortcut_hint 한 줄을 대신한다. 한 줄짜리 문장은 상태가 바뀔 때마다
    길이가 변해 아래 것들이 밀렸는데, 표는 줄 수가 고정이라 안 밀린다
    (레이아웃이 줄바꿈으로 흔들리지 않게 -- 2026-09-06 사용자 요청).
    """
    rows = getattr(win, "key_rows", None)
    if not rows:
        return
    for key, _what, states in KEY_MAP:
        live = state in states
        rows[key].setStyleSheet(
            f"font-family: {MONO_STACK};"
            + ("color:#2ecc71; font-weight:bold;" if live else "color:#666;"))
