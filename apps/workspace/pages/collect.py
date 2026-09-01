"""Collect page builder for WorkspaceWindow."""
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.gui_widgets import DeltaBar
from gello.gui.i18n import tr

from apps.workspace.builders.sizing import shrinkable_combo


def build_collect(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # scene 세션 전용: 다음 에피소드부터 수행할 slot(instruction) 전환.
    # 진행 중 에피소드에는 영향이 없다 (worker 가 기록 시작 시점에 캡처).
    slot = QGroupBox(tr("Scene slot — 현재 instruction"))
    win.slot_box = slot
    sfrm = QFormLayout(slot)
    win.slot_current_label = QLabel("")
    win.slot_current_label.setWordWrap(True)
    sfrm.addRow(tr("현재"), win.slot_current_label)
    # 계획(수집 계획 파일)이 있으면 여기서 slot 을 고른다 -- 항목에 수집
    # 현황("2/10")이 붙고, 고르면 아래 ID·문장이 채워진다. 문장을 손으로
    # 칠 때 생기는 미묘한 갈라짐(실데이터에서 실제 발생)을 막는 장치.
    win.slot_plan_combo = QComboBox()
    shrinkable_combo(win.slot_plan_combo)
    win.slot_plan_combo.currentIndexChanged.connect(win.scene_ops.on_slot_plan_pick)
    sfrm.addRow(tr("계획 slot"), win.slot_plan_combo)
    win.slot_next_btn = QPushButton(tr("다음 미수집 slot 제시"))
    win.slot_next_btn.clicked.connect(win.scene_ops.on_next_slot)
    sfrm.addRow(win.slot_next_btn)
    win.slot_iid_edit = QLineEdit()
    sfrm.addRow(tr("instruction ID"), win.slot_iid_edit)
    win.slot_instr_edit = QLineEdit()
    win.slot_instr_edit.editingFinished.connect(win.scene_ops.on_slot_sentence_edited)
    sfrm.addRow(tr("문장"), win.slot_instr_edit)
    win.slot_apply_btn = QPushButton(tr("slot 적용 (다음 에피소드부터)"))
    win.slot_apply_btn.clicked.connect(win.scene_ops.on_apply_slot)
    sfrm.addRow(win.slot_apply_btn)
    win.slot_plan_warn = QLabel("")
    win.slot_plan_warn.setWordWrap(True)
    win.slot_plan_warn.setStyleSheet("color:#e67e22;")
    sfrm.addRow(win.slot_plan_warn)
    slot.setVisible(False)
    col.addWidget(slot)

    gate = QGroupBox(tr("리더 자세 게이트"))
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

    ctl = QGroupBox(tr("제어"))
    ccol = QVBoxLayout(ctl)
    win.start_btn = QPushButton(tr("Start Teleop (기록 시작)"))
    win.start_btn.clicked.connect(lambda: win.collection.cmd("cmd_start_teleop"))
    # 자동 정렬은 한 번 시간 초과되면 끝이었고, 다시 걸 방법이 없어 남은 길은
    # 손으로 맞추는 것뿐이었다. 워커는 재요청을 받을 수 있으므로 버튼과 Enter
    # 둘 다 연결한다. all_ok 전에는 잠근다 -- 리더 모터로 끌어당기는 동작이라
    # 크게 어긋난 상태에서 걸면 모터에 무리가 간다(워커도 같은 조건을 재검사).
    win.match_btn = QPushButton(tr("자동 정렬 다시 (Enter)"))
    win.match_btn.setEnabled(False)
    win.match_btn.clicked.connect(lambda: win.collection.cmd("cmd_auto_match_pose"))
    win.skip_btn = QPushButton(tr("리셋 완료 — 계속 (Enter)"))
    win.skip_btn.setToolTip(tr(
        "물체를 제자리에 놓은 뒤 누르세요. 리셋 대기는 자동으로 끝나지 "
        "않습니다 -- 이 버튼(또는 Enter)을 눌러야 게이트로 넘어갑니다."))
    win.skip_btn.clicked.connect(lambda: win.collection.cmd("cmd_skip_reset_wait"))
    win.save_ok_btn = QPushButton(tr("저장 (성공)"))
    win.save_ok_btn.setStyleSheet("background-color:#2ecc71; color:white; font-weight:bold;")
    win.save_ok_btn.clicked.connect(lambda: win.collection.save(True))
    # 두 버튼 모두 에피소드를 끝낸다. 판정을 되돌리는 건 리셋 구간의
    # Esc(toggle_last_verdict)이고, 여기서는 끝내는 순간의 첫 판단만 한다.
    win.save_ng_btn = QPushButton(tr("실패로 끝내기 (Esc)"))
    win.save_ng_btn.clicked.connect(lambda: win.collection.save(False))
    win.discard_btn = QPushButton(tr("버리기"))
    win.discard_btn.setStyleSheet("background-color:#e74c3c; color:white;")
    win.discard_btn.clicked.connect(lambda: win.collection.cmd("cmd_discard_episode"))
    win.home_btn = QPushButton(tr("홈으로"))
    win.home_btn.clicked.connect(lambda: win.collection.cmd("cmd_go_home"))
    for b in (win.start_btn, win.match_btn, win.skip_btn, win.save_ok_btn,
              win.save_ng_btn, win.discard_btn, win.home_btn):
        ccol.addWidget(b)
    col.addWidget(ctl)

    prog = QGroupBox(tr("진행"))
    pcol = QVBoxLayout(prog)
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

