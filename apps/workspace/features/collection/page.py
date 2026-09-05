"""Collect page builder for WorkspaceWindow."""
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.gui.widgets import DeltaBar
from gello.gui.i18n import tr

from apps.workspace.shared.sizing import shrinkable_combo


def build_collect(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # scene 세션 전용: 다음 에피소드부터 수행할 slot(instruction) 전환.
    # 진행 중 에피소드에는 영향이 없다 (worker 가 기록 시작 시점에 캡처).
    slot = QGroupBox(tr("Scene slot"))
    win.slot_box = slot
    sfrm = QFormLayout(slot)
    win.slot_current_label = QLabel("")
    win.slot_current_label.setWordWrap(True)
    sfrm.addRow(tr("현재"), win.slot_current_label)
    # 계획(수집 계획 파일)이 있으면 여기서 slot 을 고른다 -- 항목에 수집
    # 현황("2/10")이 붙고, 고륾면 아래 ID·문장이 채워진다. 문장을 손으로
    # 칠 때 생기는 미묘한 갈라짐(실데이터에서 실제 발생)을 막는 장치.
    win.slot_plan_combo = QComboBox()
    shrinkable_combo(win.slot_plan_combo)
    win.slot_plan_combo.currentIndexChanged.connect(win.scene_planning.on_slot_plan_pick)
    sfrm.addRow(tr("계획 slot"), win.slot_plan_combo)
    win.slot_next_btn = QPushButton(tr("다음 미수집 slot 제시"))
    win.slot_next_btn.clicked.connect(win.scene_planning.on_next_slot)
    sfrm.addRow(win.slot_next_btn)
    win.slot_iid_edit = QLineEdit()
    sfrm.addRow(tr("instruction ID"), win.slot_iid_edit)
    win.slot_instr_edit = QLineEdit()
    win.slot_instr_edit.editingFinished.connect(win.scene_planning.on_slot_sentence_edited)
    sfrm.addRow(tr("문장"), win.slot_instr_edit)
    win.slot_apply_btn = QPushButton(tr("slot 적용 (다음 에피소드부터)"))
    win.slot_apply_btn.clicked.connect(win.scene_planning.on_apply_slot)
    sfrm.addRow(win.slot_apply_btn)
    win.slot_plan_warn = QLabel("")
    win.slot_plan_warn.setWordWrap(True)
    win.slot_plan_warn.setStyleSheet("color:#e67e22;")
    sfrm.addRow(win.slot_plan_warn)
    slot.setVisible(False)
    col.addWidget(slot)

    gate = QGroupBox(tr("Pose gate"))
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

    # 버튼 이름은 영어다 (i18n.py 의 언어 계층) -- 손이 리더암에 있고 눈이
    # 로봇에 있는 상태에서 곁눈질로 누르는 것이라, 읽는 글이 아니라 기호로
    # 인식된다. 무엇을 하는 버튼인지는 툴팁이 한국어로 말한다.
    ctl = QGroupBox(tr("Control"))
    ccol = QVBoxLayout(ctl)
    win.start_btn = QPushButton(tr("Start Teleop"))
    win.start_btn.setToolTip(tr("기록을 시작합니다 (Space)."))
    win.start_btn.clicked.connect(lambda: win.collection.cmd("cmd_start_teleop"))
    # 자동 정렬은 한 번 시간 초과되면 끝이었고, 다시 걸 방법이 없어 남은 길은
    # 손으로 맞추는 것뿐이었다. 워커는 재요청을 받을 수 있으므로 버튼과 Enter
    # 둘 다 연결한다. all_ok 전에는 잠근다 -- 리더 모터로 끌어당기는 동작이라
    # 크게 어긋난 상태에서 걸면 모터에 무리가 간다(워커도 같은 조건을 재검사).
    win.match_btn = QPushButton(tr("Auto-align (Enter)"))
    win.match_btn.setEnabled(False)
    win.match_btn.clicked.connect(lambda: win.collection.cmd("cmd_auto_match_pose"))
    win.skip_btn = QPushButton(tr("Reset done — continue (Enter)"))
    win.skip_btn.setToolTip(tr(
        "물체를 제자리에 놓은 뒤 누르세요. 리셋 대기는 자동으로 끝나지 "
        "않습니다 -- 이 버튼(또는 Enter)을 눌러야 게이트로 넘어갑니다."))
    win.skip_btn.clicked.connect(lambda: win.collection.cmd("cmd_skip_reset_wait"))
    win.save_ok_btn = QPushButton(tr("Save (success)"))
    win.save_ok_btn.setStyleSheet("background-color:#2ecc71; color:white; font-weight:bold;")
    win.save_ok_btn.clicked.connect(lambda: win.collection.save(True))
    # 두 버튼 모두 에피소드를 끝낸다. 판정을 되돌리는 건 리셋 구간의
    # Esc(toggle_last_verdict)이고, 여기서는 끝내는 순간의 첫 판단만 한다.
    win.save_ng_btn = QPushButton(tr("Save as fail (Esc)"))
    win.save_ng_btn.clicked.connect(lambda: win.collection.save(False))
    win.discard_btn = QPushButton(tr("Discard"))
    win.discard_btn.setToolTip(tr("이 에피소드를 저장하지 않고 버립니다 (Del)."))
    win.discard_btn.setStyleSheet("background-color:#e74c3c; color:white;")
    win.discard_btn.clicked.connect(lambda: win.collection.cmd("cmd_discard_episode"))
    win.home_btn = QPushButton(tr("Home"))
    win.home_btn.setToolTip(tr("로봇을 홈 자세로 되돌립니다."))
    win.home_btn.clicked.connect(lambda: win.collection.cmd("cmd_go_home"))
    for b in (win.start_btn, win.match_btn, win.skip_btn, win.save_ok_btn,
              win.save_ng_btn, win.discard_btn, win.home_btn):
        ccol.addWidget(b)
    col.addWidget(ctl)

    prog = QGroupBox(tr("Progress"))
    pcol = QVBoxLayout(prog)
    # 현재 (scene, instruction) 의 HDF5 실측 누계/계획 target -- GUI 를 켠
    # 순간 누계가 아니다 (issue #38). 조작자가 리더암을 잡은 거리에서 읽어야
    # 하므로 주변 라벨보다 크게. 갱신은 CollectionOps.refresh_slot_counter 가
    # 전담 -- 프레임마다 부르지 않는다 (HDF5 를 연다).
    win.slot_counter = QLabel(tr("—"))
    win.slot_counter.setFont(QFont("", 16, QFont.Weight.Bold))
    win.slot_counter.setStyleSheet("color:#888;")
    pcol.addWidget(win.slot_counter)
    # 데이터셋 전체 진행률 (계획 × scene 파일 실측) -- 수집 중에 보는 정보는
    # 수집 화면에 있어야 한다 (2026-09-04: Statistics 에서 이동). 갱신은
    # CollectionOps.refresh_slot_counter 에 얹혀 저장/삭제/slot 변경 때
    # 자동으로 일어난다.
    win.plan_progress_label = QLabel("")
    win.plan_progress_label.setStyleSheet("color:#888;")
    win.plan_progress_label.setWordWrap(True)
    pcol.addWidget(win.plan_progress_label)
    win.plan_progress_tree = QTreeWidget()
    win.plan_progress_tree.setHeaderLabels(
        [tr("scene / slot"), tr("수집"), tr("목표"), tr("문장")])
    win.plan_progress_tree.setRootIsDecorated(True)
    win.plan_progress_tree.header().setSectionResizeMode(
        3, QHeaderView.ResizeMode.Stretch)
    win.plan_progress_tree.setMinimumHeight(140)
    pcol.addWidget(win.plan_progress_tree)
    prow = QHBoxLayout()
    prow.addStretch(1)
    plan_refresh = QPushButton(tr("진행률 새로고침"))
    plan_refresh.clicked.connect(win.scene_planning.refresh_plan_progress)
    prow.addWidget(plan_refresh)
    pcol.addLayout(prow)
    win.ep_progress = QProgressBar()
    win.ep_progress.setFormat("%v / %m frames")
    pcol.addWidget(win.ep_progress)
    win.state_label = QLabel(tr("대기"))
    win.state_label.setFont(QFont("", 11, QFont.Weight.Bold))
    pcol.addWidget(win.state_label)
    win.save_status_label = QLabel("")
    win.save_status_label.setStyleSheet("color:#888;")
    pcol.addWidget(win.save_status_label)
    win.verdict_label = QLabel("")
    win.verdict_label.setWordWrap(True)
    pcol.addWidget(win.verdict_label)
    win.shortcut_hint = QLabel("")
    win.shortcut_hint.setStyleSheet(
        "color:#2ecc71; font-family:monospace; font-weight:bold;")
    win.shortcut_hint.setWordWrap(True)
    pcol.addWidget(win.shortcut_hint)
    col.addWidget(prog)
    col.addStretch()
    return w
