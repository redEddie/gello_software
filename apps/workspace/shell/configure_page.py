"""Configure page builder for WorkspaceWindow."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr
from gello.robots.franka_fr3 import FR3_RESET_POSES
from gello.scene.collection_plan import list_plans

from apps.workspace.shared.widgets import SceneInfoView
from apps.workspace.shared.sizing import shrinkable_combo


def build_configure(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    node = QGroupBox(tr("로봇 노드"))
    nrow = QVBoxLayout(node)
    win.node_start_btn = QPushButton(tr("노드 시작"))
    win.node_start_btn.clicked.connect(win.system.on_start_node)
    win.node_stop_btn = QPushButton(tr("노드 종료"))
    win.node_stop_btn.clicked.connect(win.system.on_stop_node)
    nrow.addWidget(win.node_start_btn)
    nrow.addWidget(win.node_stop_btn)
    col.addWidget(node)

    # ---- scene-v1 이 유일한 수집 방식이다 (2026-08-13, legacy 수집 UI
    # 제거). 파일 하나 = 책상 배치(scene) 하나, instruction 은 에피소드마다
    # 기록되고 수집 중에 바꿀 수 있다. legacy *_demo.hdf5 는 더 이상 새로
    # 만들지 않지만 변환·업로드·재생 등 데이터 관리 기능은 그대로 남는다.
    scene = QGroupBox(tr("Scene 수집 (scene-v1)"))
    win.task_box = scene  # 연습 모드 토글이 잠그는 그룹 (기존 이름 유지)
    sc_form = QFormLayout(scene)
    scene_row = QWidget()
    srow = QHBoxLayout(scene_row)
    srow.setContentsMargins(0, 0, 0, 0)
    win.scene_combo = QComboBox()
    shrinkable_combo(win.scene_combo)
    win.scene_combo.currentIndexChanged.connect(win.scene_ops.on_scene_selected)
    srow.addWidget(win.scene_combo, 1)
    win.scene_refresh_btn = QPushButton("↻")
    win.scene_refresh_btn.setToolTip(tr("scene 목록 새로고침"))
    win.scene_refresh_btn.setMaximumWidth(32)
    win.scene_refresh_btn.clicked.connect(win.scene_ops.refresh_scene_combo)
    srow.addWidget(win.scene_refresh_btn)
    sc_form.addRow(tr("Scene"), scene_row)
    win.scene_new_btn = QPushButton(tr("새 Scene 구성..."))
    win.scene_new_btn.clicked.connect(win.scene_ops.on_new_scene)
    sc_form.addRow(win.scene_new_btn)
    # 계획이 있으면 시작 문장을 여기서 고른다 -- 고르면 아래 문장·slot ID
    # 가 함께 채워진다 (세션 중 slot 패널의 계획 콤보와 같은 장치).
    win.start_plan_combo = QComboBox()
    shrinkable_combo(win.start_plan_combo)
    win.start_plan_combo.currentIndexChanged.connect(win.scene_planning.on_start_plan_pick)
    sc_form.addRow(tr("계획 문장"), win.start_plan_combo)
    win.lang_edit = QLineEdit()
    win.lang_edit.setPlaceholderText(tr("예) pick up the blue cup and place it on the blue bowl"))
    win.lang_edit.setText(win._recents.most_recent("language", ""))
    # 문장을 바꾸면 slot ID 가 자동으로 따라온다 (아는 문장=재사용,
    # 새 문장=다음 빈 ID) -- ID-문장 갈라짐 방지.
    win.lang_edit.editingFinished.connect(win.scene_ops.on_start_sentence_edited)
    sc_form.addRow(tr("시작 문장"), win.lang_edit)
    # 수집 계획 (slot plan). 계획이 있으면 Collect 의 slot 패널이 계획
    # 기반 드롭다운 + 수집 카운트로 동작한다. 없어도 자유 입력은 그대로.
    win.plan_combo = QComboBox()
    shrinkable_combo(win.plan_combo)
    win.plan_combo.addItem(tr("(계획 없음 — 자유 입력)"), None)
    for p in list_plans():
        win.plan_combo.addItem(p.name, str(p))
    last_plan = win._recents.most_recent("plan_file", "pilot.json")
    idx = win.plan_combo.findText(last_plan)
    if idx > 0:
        win.plan_combo.setCurrentIndex(idx)
    win.plan_combo.currentIndexChanged.connect(win.scene_planning.on_plan_selected)
    plan_row = QWidget()
    prow = QHBoxLayout(plan_row)
    prow.setContentsMargins(0, 0, 0, 0)
    prow.addWidget(win.plan_combo, 1)
    win.plan_edit_btn = QPushButton("✎")
    win.plan_edit_btn.setToolTip(tr("선택한 계획 파일 편집 (저장 시 규칙 검증)"))
    win.plan_edit_btn.setMaximumWidth(32)
    win.plan_edit_btn.clicked.connect(win.scene_planning.on_edit_plan)
    prow.addWidget(win.plan_edit_btn)
    plan_new_btn = QPushButton("+")
    plan_new_btn.setToolTip(tr("새 계획 파일 만들기 (이름을 정하면 빈 계획이 "
                               "생기고 바로 편집이 열립니다)"))
    plan_new_btn.setMaximumWidth(32)
    plan_new_btn.clicked.connect(win.scene_planning.on_new_plan)
    prow.addWidget(plan_new_btn)
    plan_del_btn = QPushButton("🗑")
    plan_del_btn.setToolTip(tr("선택한 계획 파일 삭제 (git 이력에는 남습니다)"))
    plan_del_btn.setMaximumWidth(32)
    plan_del_btn.clicked.connect(win.scene_planning.on_delete_plan)
    prow.addWidget(plan_del_btn)
    sc_form.addRow(tr("수집 계획"), plan_row)
    win.scene_iid_edit = QLineEdit(win._recents.most_recent("instruction_id", "I000"))
    win.scene_iid_edit.setToolTip(tr("시작 slot 의 instruction ID (예: I000). "
                                      "수집 중 Collect 페이지에서 바꿀 수 있습니다."))
    sc_form.addRow(tr("시작 slot ID"), win.scene_iid_edit)
    win.collector_edit = QLineEdit(win._recents.most_recent("collector", ""))
    win.collector_edit.setPlaceholderText(tr("수집자 식별자 (필수 attr, 예: gibeom)"))
    sc_form.addRow(tr("수집자"), win.collector_edit)
    root_row = QWidget()
    rl = QHBoxLayout(root_row)
    rl.setContentsMargins(0, 0, 0, 0)
    win.root_edit = QLineEdit(win._recents.most_recent(
        "data_root", str(Path.home() / "libero_datasets")))
    win.root_edit.editingFinished.connect(win.scene_ops.refresh_scene_combo)
    rl.addWidget(win.root_edit, 1)
    browse = QPushButton(tr("..."))
    browse.setMaximumWidth(36)
    browse.clicked.connect(win.dataset_ops.browse_root)
    rl.addWidget(browse)
    sc_form.addRow(tr("저장 경로"), root_row)
    win.scene_info = SceneInfoView()
    sc_form.addRow(win.scene_info)
    win._pending_scene_meta = None
    win.session.scene_session = False
    col.addWidget(scene)

    cam = QGroupBox(tr("카메라"))
    cform = QFormLayout(cam)
    win.agent_combo = QComboBox()
    win.wrist_combo = QComboBox()
    for c in (win.agent_combo, win.wrist_combo):
        c.setEditable(True)
        shrinkable_combo(c)
        c.currentTextChanged.connect(win.camera_ops.on_camera_changed)
    cform.addRow(tr("Agent"), win.agent_combo)
    cform.addRow(tr("Wrist"), win.wrist_combo)
    refresh = QPushButton(tr("카메라 새로고침"))
    refresh.clicked.connect(win.camera_ops.refresh_cameras)
    cform.addRow(refresh)
    win.preview_btn = QPushButton(tr("미리보기 시작"))
    win.preview_btn.clicked.connect(win.camera_ops.on_toggle_previews)
    cform.addRow(win.preview_btn)
    win.camera_hint = QLabel("")
    win.camera_hint.setStyleSheet("color:#888;")
    win.camera_hint.setWordWrap(True)
    cform.addRow(win.camera_hint)
    col.addWidget(cam)

    # "세션"이 아니라 "수집 설정": 여기 있는 것은 전부 Connect 시점에
    # 적용되는 수집 방식이다. 연습 모드도 그중 하나라 별도 "모드" 그룹을
    # 두지 않고 여기에 둔다.
    sess = QGroupBox(tr("수집 설정"))
    sform = QFormLayout(sess)
    win.no_dataset_check = QCheckBox(tr("데이터셋 없이 조작만 (연습 / 씬 세팅)"))
    win.no_dataset_check.setToolTip(tr(
        "파일을 전혀 만들지 않고 텔레옵만 합니다. 자세 게이트·카메라·프레임 "
        "카운터는 그대로 동작하고, 저장을 눌러도 버려집니다."))
    win.no_dataset_check.toggled.connect(win.dataset_ops.on_no_dataset_toggled)
    sform.addRow(win.no_dataset_check)
    win.mode_hint = QLabel("")
    win.mode_hint.setStyleSheet("color:#888;")
    win.mode_hint.setWordWrap(True)
    sform.addRow(win.mode_hint)
    win.reset_pose_combo = QComboBox()
    win.reset_pose_combo.addItems(sorted(FR3_RESET_POSES))
    if "libero" in FR3_RESET_POSES:
        win.reset_pose_combo.setCurrentText("libero")
    sform.addRow(tr("Reset pose"), win.reset_pose_combo)
    win.grip_combo = QComboBox()
    win.grip_combo.addItems(["right", "left"])
    sform.addRow(tr("Grip"), win.grip_combo)
    win.eplen_edit = QLineEdit("20")
    sform.addRow(tr("에피소드 길이(s)"), win.eplen_edit)
    win.resetwait_edit = QLineEdit("10")
    win.resetwait_edit.setEnabled(False)
    win.resetwait_edit.setToolTip(tr(
        "더 이상 사용하지 않습니다 — 리셋 대기는 시간으로 끝나지 않고 "
        "'리셋 완료' 버튼(Enter)으로만 끝납니다."))
    sform.addRow(tr("리셋 대기(s) (미사용)"), win.resetwait_edit)
    win.wall_check = QCheckBox(tr("관절 한계 벽 사용"))
    win.wall_check.setChecked(True)
    sform.addRow(win.wall_check)
    win.match_check = QCheckBox(tr("에피소드마다 리더를 리셋 포즈로 정렬"))
    win.match_check.setChecked(True)
    sform.addRow(win.match_check)
    col.addWidget(sess)
    col.addStretch()
    win.scene_ops.refresh_scene_combo()
    return w

