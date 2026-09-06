"""Configure page builder for WorkspaceWindow."""

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

from apps.workspace.shared.collector_picker import CollectorPicker
from apps.workspace.shared.widgets import SceneInfoView
from apps.workspace.shared.sizing import keep_height, shrinkable_combo


def build_configure(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)

    # 이 화면에서 가장 많이 누르는 버튼이라 맨 위다 (위에서 아래로 "많이
    # 누르는 순서 · 확정적인 순서" -- 2026-09-06 사용자 규칙). 반사로 노드가
    # 죽으면 조작자는 이 화면으로 돌아오고, 그때 할 일이 늘 같다: 노드 띄우기
    # → 가장 최근 scene → 가장 낮은 미완 지시문 → Connect. 규칙의 정본은
    # ScenePlanningOps.pick_resume_slot 이다.
    quick = QPushButton(tr("⚡ Quick resume"))
    quick.setToolTip(tr(
        "가장 번호가 높은 scene 과 아직 목표를 못 채운 가장 낮은 slot 을 골라,\n"
        "로봇 노드가 준비되면 바로 연결합니다.\n"
        "첫 scene 을 만들거나 계획에 없는 문장을 쓰는 것은 사람이 정합니다."))
    quick.clicked.connect(win.collection.on_quick_start)
    col.addWidget(quick)

    # 수집자는 켤 때마다 손대는 칸이라 위에 온다 (2026-09-06 사용자).
    # 아는 이름은 태그로 고르고, 처음 보는 이름은 그냥 친다.
    who = QGroupBox(tr("수집자"))
    wcol = QVBoxLayout(who)
    wcol.setContentsMargins(6, 6, 6, 6)
    win.collector_edit = CollectorPicker(win._recents)
    wcol.addWidget(win.collector_edit)
    col.addWidget(who)

    node = QGroupBox(tr("로봇 노드"))
    nrow = QVBoxLayout(node)
    win.node_start_btn = QPushButton(tr("노드 시작"))
    win.node_start_btn.clicked.connect(win.system.on_start_node)
    win.node_stop_btn = QPushButton(tr("노드 종료"))
    win.node_stop_btn.clicked.connect(win.system.on_stop_node)
    nrow.addWidget(win.node_start_btn)
    nrow.addWidget(win.node_stop_btn)
    # 연습 모드는 로봇 노드 **바로 밑**이다 (2026-09-06 사용자 지정).
    # "파일을 남길 것인가"는 아래 Scene 설정 전부를 무의미하게 만드는
    # 결정이라, 그것들을 다 채운 뒤 맨 아래에서 만나는 것이 순서가 거꾸로였다.
    win.no_dataset_check = QCheckBox(tr("데이터셋 없이 조작만 (연습 / 씬 세팅)"))
    win.no_dataset_check.setToolTip(tr(
        "파일을 전혀 만들지 않고 텔레옵만 합니다. 자세 게이트·카메라·프레임 "
        "카운터는 그대로 동작하고, 저장을 눌러도 버려집니다."))
    win.no_dataset_check.toggled.connect(win.dataset_ops.on_no_dataset_toggled)
    nrow.addWidget(win.no_dataset_check)
    win.mode_hint = QLabel("")
    win.mode_hint.setStyleSheet("color:#888;")
    win.mode_hint.setWordWrap(True)
    nrow.addWidget(win.mode_hint)
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
    # 사람이 고른 것만 -- 목록을 다시 채울 때는 오지 않는다.
    win.scene_combo.activated.connect(win.scene_ops.on_scene_activated)
    srow.addWidget(win.scene_combo, 1)
    win.scene_refresh_btn = QPushButton("↻")
    win.scene_refresh_btn.setToolTip(tr("scene 목록 새로고침"))
    win.scene_refresh_btn.setMaximumWidth(32)
    win.scene_refresh_btn.clicked.connect(win.scene_ops.refresh_scene_combo)
    srow.addWidget(win.scene_refresh_btn)
    sc_form.addRow(tr("Scene"), scene_row)
    # "새 Scene 구성..." 버튼을 뺐다 (2026-09-06) -- 드롭다운 맨 위의
    # "— 새 Scene (Sxxx) —" 를 고르면 Scene 탭이 열린다. 같은 일을 하는
    # 입구가 바로 옆에 둘 있을 이유가 없다.
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
    # instructions.json 을 화면에 적지 않는다 (2026-09-06). 지시문은
    # 그 파일 하나로 통일됐으므로 "어느 파일인가"는 더 이상 고를 것이
    # 아니고, 편집은 Instruction 탭이 맡는다.
    win.scene_iid_edit = QLineEdit(win._recents.most_recent("instruction_id", "I000"))
    win.scene_iid_edit.setToolTip(tr("시작 지시문의 ID (예: I000). "
                                      "수집 중 Collect 페이지에서 바꿀 수 있습니다."))
    sc_form.addRow(tr("시작 지시문 ID"), win.scene_iid_edit)
    # 저장 경로 칸을 뺐다 (2026-09-06). 그 값은 런처 마법사가 정하고,
    # 화면에서 고치는 자리는 Dataset 페이지 하나다 (File 메뉴도 같은 위젯을
    # 고친다). 지금 어디에 쌓이는지는 상태바가 늘 비추고 있다.
    win.scene_info = SceneInfoView()
    sc_form.addRow(win.scene_info)
    win._pending_scene_meta = None
    win.session.scene_session = False
    col.addWidget(scene)

    # 카메라 그룹은 ① Layout 으로 갔다 (2026-09-06). 카메라 점검은 앞
    # 단계에서 끝나는 일이라 Scene 을 정하는 화면에 있을 이유가 없었고,
    # 두 곳에 같은 콤보를 두느라 서로 복사하는 코드까지 있었다.

    # "세션"이 아니라 "수집 설정": 여기 있는 것은 전부 Connect 시점에
    # 적용되는 수집 방식이다. 연습 모드도 그중 하나라 별도 "모드" 그룹을
    # 두지 않고 여기에 둔다.
    sess = QGroupBox(tr("수집 설정"))
    win.session_box = sess          # 세션 중에는 감춘다 (set_running)
    sform = QFormLayout(sess)
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
    # 줄 수가 정해진 상자는 세로로 안 늘어나게 (sizing.keep_height 참고).
    # Scene 수집은 예외 -- 계획 설명 라벨이 접히면서 높이가 는다.
    keep_height(who, node, sess)
    col.addStretch()
    win.scene_ops.refresh_scene_combo()
    return w

