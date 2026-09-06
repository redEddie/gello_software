"""Toolbar, menu bar, and status bar builders for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QLabel, QMessageBox, QToolBar, QWidgetAction

from gello.data.episode_stats import TASK_DEV_LIMIT
from gello.gui.i18n import tr

from apps.workspace.shared.widgets import StatusLight
from apps.workspace.constants import ACTIVITIES, CENTER_TABS, LOG_DIR
from apps.workspace.shared.tabs import (
    is_index_only,
    lab_icon,
    show_center_tab,
)


def build_toolbar(win) -> None:
    """툴바는 **수집 흐름 고정 구획 + 현재 화면의 자주 쓰는 것**이다
    (2026-09-06 사용자 결정).

    메뉴바(전량 색인)와 역할이 다르다. 여기 있는 항목이 다른 화면에도 있는
    것은 중복이 아니라 거울이다 -- 어딘가 한 곳에서 동작을 훑을 수 있어야
    하고, 좌측 패널이 스크롤돼도 툴바로는 손이 닿아야 한다.

    고정 구획이 필요한 이유가 구체적으로 있다: 수집 도중에 파일을 미리 보러
    Dataset 화면으로 건너가는 워크플로가 실제로 있다. 그때 화면을 따라 툴바가
    통째로 바뀌면 진행 중인 에피소드를 끝낼 수단이 사라진다. 그래서 에피소드
    한 바퀴(Start → 정렬 → Save/Discard → Reset done)는 어느 화면에서든 그대로
    남는다.
    """
    tb = QToolBar(tr("주요 작업"))
    tb.setMovable(False)
    tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    win.addToolBar(tb)
    win.tool_bar = tb
    win.tb_actions = {}
    win._tb_context = []          # 화면 따라 갈리는 부분 (지웠다 다시 만든다)

    def add(key: str, text: str, slot, tip: str = "") -> QAction:
        act = QAction(text, win)
        act.setToolTip(tip or text)
        act.triggered.connect(slot)
        tb.addAction(act)
        win.tb_actions[key] = act
        return act

    # ---- 고정: 세션 ----
    add("connect", tr("▶ Connect"), win.collection.on_connect, tr("로봇에 연결하고 세션 시작"))
    add("disconnect", tr("■ Disconnect"), win.collection.on_disconnect, tr("세션 종료"))
    tb.addSeparator()
    # ---- 고정: 에피소드 한 바퀴 ----
    # 이름은 좌측 패널 버튼과 같게 쓴다 -- 다른 이름을 붙이면 같은 것인지
    # 알 수 없다 (메뉴 색인과 같은 규칙).
    add("record", tr("● Start Teleop"),
        lambda: win.collection.cmd("cmd_start_teleop"), tr("기록 시작"))
    add("match", tr("⇔ Auto-align"),
        lambda: win.collection.cmd("cmd_auto_match_pose"),
        tr("리더 자세로 로봇을 맞춥니다 (Enter)"))
    add("skip", tr("↩ Reset done"),
        lambda: win.collection.cmd("cmd_skip_reset_wait"),
        tr("리셋을 마쳤으니 다음 에피소드로 (Enter)"))
    tb.addSeparator()
    # save, not cmd -- the success flag has to be recorded for the stats
    # panel, and a toolbar button that counts differently from the side
    # panel button next to it is a bug waiting to be blamed on the stats.
    add("save", tr("✔ Save"), lambda: win.collection.save(True), tr("성공으로 끝내기"))
    add("savefail", tr("✖ Save (fail)"), lambda: win.collection.save(False),
        tr("실패로 끝내기 (Esc). 판정은 리셋 구간에서 Esc로 뒤집을 수 있습니다"))
    add("discard", tr("🗑 Discard"), lambda: win.collection.cmd("cmd_discard_episode"))
    tb.addSeparator()
    add("home", tr("⌂ Home"), lambda: win.collection.cmd("cmd_go_home"))
    # 여기서부터 화면별 구획이 붙는다.
    win._tb_context_anchor = tb.addSeparator()


def toolbar_context(win, key: str) -> list:
    """화면 ``key`` 에서 자주 쓰는 동작 [(라벨, 슬롯, 툴팁), ...].

    "자주"의 기준은 그 화면에 들어온 목적이다 -- 화면마다 한두 개면 충분하고,
    나머지는 화면 안 버튼과 메뉴 색인에 있다. 여기 많이 넣으면 고정 구획이
    묻혀서 고정으로 둔 뜻이 없어진다.
    """
    return {
        "configure": [
            (tr("새 Scene 구성..."), win.scene_ops.on_new_scene,
             tr("소품 조합과 3×3 배치를 정합니다")),
            (tr("카메라 새로고침"), win.camera_ops.refresh_cameras, ""),
        ],
        "collect": [
            (tr("다음 미수집 slot 제시"), win.scene_planning.on_next_slot,
             tr("계획에서 아직 목표를 못 채운 문장을 고릅니다")),
        ],
        "dataset": [
            (tr("새로고침"), win.dataset_ops.refresh_dataset_tree, ""),
            (tr("실패만 선택"), win.dataset_ops.on_select_failed, ""),
        ],
        "upload": [
            (tr("전체 처리"), win.upload.on_pipeline,
             tr("재압축 → 변환 → 업로드를 한 번에")),
        ],
        "stats": [
            (tr("다시 분석"), lambda: win.stats_ops.refresh_analysis(force=True), ""),
        ],
        "layout": [
            (tr("카메라 새로고침"), win.camera_ops.refresh_cameras, ""),
            (tr("3×3 격자 편집..."), win.layout_ref.on_edit_grid, ""),
        ],
        "settings": [],
    }.get(key, [])


def set_toolbar_context(win, key: str) -> None:
    """툴바의 화면별 구획을 ``key`` 의 것으로 갈아 끼운다. 고정 구획은 건드리지
    않는다 -- 다른 화면에 가 있어도 에피소드를 끝낼 수 있어야 한다."""
    tb = getattr(win, "tool_bar", None)
    if tb is None:
        return
    for act in getattr(win, "_tb_context", []):
        tb.removeAction(act)
    win._tb_context = []
    for text, slot, tip in toolbar_context(win, key):
        act = QAction(text, win)
        act.setToolTip(tip or text)
        act.triggered.connect(slot)
        tb.addAction(act)
        win._tb_context.append(act)


def _lifecycle_header(win, menu, keys) -> None:
    """메뉴 맨 위에 그 대상의 생사를 한 줄로 띄운다 (2026-09-06).

    설명이 아니라 **상태**를 적는다. 설명은 한 번 읽으면 끝이라 매번 자리를
    차지하기만 하지만, 상태는 메뉴를 여는 이유(뭔가 이상하다) 바로 그것에
    답한다. 정상이면 회색으로 조용히 있다가 문제일 때만 붉게 튄다.

    값의 정본은 상태바의 StatusLight 다 -- 여기서 따로 계산하면 상태바와
    다른 말을 하는 순간이 온다. 메뉴는 거울이다.
    """
    lab = QLabel()
    lab.setContentsMargins(24, 4, 12, 4)
    act = QWidgetAction(menu)
    act.setDefaultWidget(lab)
    menu.addAction(act)
    menu.addSeparator()

    def refresh() -> None:
        # 메뉴가 상태바보다 먼저 만들어진다 -- 처음엔 비어 있고, 메뉴를 열 때
        # (aboutToShow) 채워진다.
        lights = getattr(win, "lights", {})
        # 라벨(Node/Robot/Leader/Camera)까지 그대로 비춘다 -- Robot 메뉴처럼
        # 값이 둘이면 어느 쪽이 무엇인지 라벨 없이는 못 읽는다.
        here = [lights[k] for k in keys if k in lights]
        lab.setText(" · ".join(x.text() for x in here))
        # 정상이면 회색으로 조용히, 문제면 줄 전체가 붉게. 점만 물들이면
        # 눈에 안 들어온다 -- 메뉴를 여는 이유가 대개 그 문제다.
        worst = ("bad" if any(x.state == "bad" for x in here)
                 else "busy" if any(x.state == "busy" for x in here) else "off")
        color = {"bad": "#e74c3c", "busy": "#f39c12"}.get(worst, "#888")
        weight = "bold" if worst == "bad" else "normal"
        lab.setStyleSheet(
            f"color:{color}; font-size:11px; font-weight:{weight};")

    menu.aboutToShow.connect(refresh)
    refresh()


def build_menu(win) -> None:
    """메뉴바는 **동작의 전량 색인**이다 (2026-09-06 사용자 결정).

    규칙 세 가지:

    1. 화면(좌측 패널·중앙 탭·다이얼로그)에 있는 동작은 전부 여기에도 있다.
       색인이 불완전하면 화면에서 버튼을 뺄 수 없다 -- 빼는 순간 그 동작에
       닿을 길이 사라지기 때문이다. 화면을 줄이는 모든 작업이 이 완전성에
       기대고 있다.
    2. 색인 항목은 **정본의 이름을 그대로** 쓴다. 버튼이 ``Save (success)``
       인데 메뉴가 "성공으로 저장"이면 둘이 같은 것인지 알 수 없다.
    3. 축은 "무엇을 관리하는가"다: 로봇 노드 / 수집 상태 / scene / 데이터셋 /
       업로드 / 카메라. 옛 ``Process`` 메뉴처럼 "어떤 종류의 일인가"로 묶으면
       로봇 노드와 카메라 노드가 같은 서랍에 들어가 찾을 수 없다.

    툴바는 이것과 역할이 다르다 -- 현재 화면에서 자주 쓰는 것 + 수집 흐름
    고정 구획이라, 여기와 겹치는 것은 중복이 아니라 거울이다.
    """
    mb = win.menuBar()

    m = mb.addMenu(tr("File"))
    m.addAction(tr("데이터 저장 경로 열기..."), win.dataset_ops.browse_root)
    m.addAction(tr("로그 폴더 열기"), lambda: win.log(f"[로그] {LOG_DIR}"))
    m.addSeparator()
    m.addAction(tr("종료"), win.close)

    # 로봇 노드 관리 -- 팔과 그 프로세스에 관한 모든 것.
    m = mb.addMenu(tr("Robot"))
    _lifecycle_header(win, m, ("node", "robot"))
    m.addAction(tr("Connect"), win.collection.on_connect)
    m.addAction(tr("Disconnect"), win.collection.on_disconnect)
    m.addAction(tr("Home"), lambda: win.collection.cmd("cmd_go_home"))
    m.addSeparator()
    m.addAction(tr("노드 시작"), win.system.on_start_node)
    # [NODE DOWN] 로그가 "'노드 재시작' 버튼을 누르세요"라고 지시하는데
    # 정작 그 버튼이 없었다 (2026-09-04).
    m.addAction(tr("노드 재시작"), win.system.on_restart_node)
    m.addAction(tr("노드 종료"), win.system.on_stop_node)
    m.addSeparator()
    m.addAction(tr("시스템 튜닝 실행 (runme.sh)"), win.system.run_runme)

    # 리더암은 로봇 노드와 다른 종류의 것이다 -- 별도 프로세스가 아니라 GUI
    # 안의 스레드 + USB 장치(Dynamixel)라, 죽었을 때 되살리는 방법도 다르다
    # (노드는 재시작, 서보는 리부트). Robot 안에 섞어 두면 "노드를 재시작하면
    # 리더암도 낫나?" 라는 오해가 생긴다 (이슈 #37 C).
    m = mb.addMenu(tr("Leader"))
    _lifecycle_header(win, m, ("leader",))
    m.addAction(tr("토크 과부하 잠금 해제 (서보 리부트)"),
                win.system.on_reset_leader_protection)

    # 데이터 수집 상태 관리 -- 에피소드 하나가 도는 동안의 모든 동작.
    # 다른 화면(예: 파일 미리보기)에 가 있어도 여기서 닿을 수 있어야 한다.
    m = mb.addMenu(tr("Camera"))
    _lifecycle_header(win, m, ("camera",))
    m.addAction(tr("카메라 새로고침"), win.camera_ops.refresh_cameras)
    # 화면 버튼이 토글이라 색인도 토글을 가리킨다 (옛 "미리보기 중지"를 대체).
    m.addAction(tr("미리보기 시작/중지"), win.camera_ops.on_toggle_previews)
    m.addSeparator()
    m.addAction(tr("카메라 노드 재시작"),
                win.camera_ops.on_restart_camera_node)
    m.addAction(tr("카메라 노드 종료 (카메라 해제)"),
                win.camera_ops.on_stop_camera_node_manual)
    m.addSeparator()
    m.addAction(tr("카메라 점검 (USB 속도·프레임)"), win.system.on_check_cameras)
    m.addAction(tr("카메라 레이아웃 확인 (LIBERO 초기 배치와 비교)"),
                lambda: show_center_tab(win, "layout"))

    m = mb.addMenu(tr("Collect"))
    m.addAction(tr("다음 미수집 slot 제시"), win.scene_planning.on_next_slot)
    m.addAction(tr("slot 적용 (다음 에피소드부터)"),
                win.scene_planning.on_apply_slot)
    m.addSeparator()
    m.addAction(tr("Start Teleop"),
                lambda: win.collection.cmd("cmd_start_teleop"))
    m.addAction(tr("Auto-align (Enter)"),
                lambda: win.collection.cmd("cmd_auto_match_pose"))
    m.addAction(tr("Reset done — continue (Enter)"),
                lambda: win.collection.cmd("cmd_skip_reset_wait"))
    m.addSeparator()
    # save, not cmd -- 성공 여부가 통계에 기록되어야 한다 (툴바와 같은 이유).
    m.addAction(tr("Save (success)"), lambda: win.collection.save(True))
    m.addAction(tr("Save as fail (Esc)"), lambda: win.collection.save(False))
    m.addAction(tr("Discard"),
                lambda: win.collection.cmd("cmd_discard_episode"))
    m.addSeparator()
    m.addAction(tr("진행률 새로고침"), win.scene_planning.refresh_plan_progress)

    m = mb.addMenu(tr("Scene"))
    m.addAction(tr("새 Scene 구성..."), win.scene_ops.on_new_scene)
    m.addAction(tr("Scene 목록 새로고침"), win.scene_ops.refresh_scene_combo)
    m.addSeparator()
    m.addAction(tr("계획 편집..."), win.scene_planning.on_edit_plan)
    m.addAction(tr("새 계획..."), win.scene_planning.on_new_plan)
    m.addAction(tr("계획 삭제"), win.scene_planning.on_delete_plan)
    m.addSeparator()
    m.addAction(tr("3×3 워크스페이스 격자 편집..."), win.layout_ref.on_edit_grid)

    m = mb.addMenu(tr("Dataset"))
    m.addAction(tr("새로고침"), win.dataset_ops.refresh_dataset_tree)
    m.addAction(tr("데이터셋 폴더 선택..."), win.dataset_ops.browse_dataset_root)
    m.addSeparator()
    m.addAction(tr("구조 확인..."), win.playback_ops.on_show_structure)
    m.addAction(tr("HDF5 트리 뷰어"), win._on_hdf5_tree)
    m.addAction(tr("myHDF5 (웹)"), win.upload.on_myhdf5)
    m.addSeparator()
    m.addAction(tr("실패만 선택"), win.dataset_ops.on_select_failed)
    m.addAction(tr("튀는 것만 선택 (scene·문장 그룹 평균과 ±{d} 밖)")
                .format(d=TASK_DEV_LIMIT),
                win.dataset_ops.on_select_jerky)
    m.addSeparator()
    m.addAction(tr("선택 재판정 (성공↔실패)"), win.dataset_ops.on_relabel_selected)
    m.addAction(tr("선택 재생 (실로봇)"), win.playback_ops.on_replay_selected)
    m.addAction(tr("끝 다듬기 (Trim 탭에서)"), win.playback_ops.on_open_trim)
    m.addSeparator()
    m.addAction(tr("에피소드 삭제"), win.dataset_ops.on_delete_selected)
    m.addAction(tr("파일 삭제"), win.dataset_ops.on_delete_file)
    m.addSeparator()
    m.addAction(tr("다시 분석"),
                lambda: win.stats_ops.refresh_analysis(force=True))
    m.addAction(tr("데이터셋 구조 사용자 설정..."), win._on_schema)

    # 업로드는 Dataset 에서 떼어낸다 -- 되돌릴 수 없는 바깥 동작이라
    # 고르다가 잘못 누르는 자리에 두지 않는다.
    m = mb.addMenu(tr("Upload"))
    m.addAction(tr("전체 처리 (재압축 → 변환 → 업로드)"), win.upload.on_pipeline)
    m.addSeparator()
    m.addAction(tr("재압축 + 업로드 (자동)"), win.upload.on_hdf5_auto)
    m.addAction(tr("용량 최적화 (재압축)"), win.upload.on_repack)
    m.addAction(tr("원본 업로드..."), win.upload.on_hdf5_upload)
    m.addSeparator()
    m.addAction(tr("변환 + 업로드 (자동)"), win.upload.on_lerobot_auto)
    m.addAction(tr("이어붙이기 (새 에피소드만)"), win.upload.on_lerobot_resume)
    m.addAction(tr("HDF5 골라서 변환만..."), win.upload.on_lerobot)
    m.addAction(tr("전체 task 다시 업로드..."), win.upload.on_lerobot_reupload)
    m.addSeparator()
    m.addAction(tr("계정 확인 / 전환..."), win.upload.on_hf_accounts)

    m = mb.addMenu(tr("View"))
    for key, _icon, title, _tip in ACTIVITIES:
        m.addAction(title, lambda _c=False, k=key: win._set_activity(k))
    m.addSeparator()
    # 중앙 탭도 색인에 넣는다. 다음 단계에서 활동에 따라 탭 구성이 달라지면
    # 화면에 안 보이는 탭이 생기는데, 여기 없으면 그 탭에 닿을 길이 사라진다.
    for key, title in CENTER_TABS:
        act = m.addAction(tr(title),
                          lambda _c=False, k=key: show_center_tab(win, k))
        if is_index_only(key):
            # 표시는 아이콘 자리에 -- 글자에 붙이면 그 줄만 시작이 밀린다.
            act.setIcon(lab_icon())
    m.addSeparator()
    win.act_toggle_bottom = QAction(tr("하단 패널"), win, checkable=True, checked=True)
    win.act_toggle_bottom.triggered.connect(
        lambda on: win.bottom_tabs.setVisible(on))
    m.addAction(win.act_toggle_bottom)
    win.act_toggle_right = QAction(tr("오른쪽 패널"), win, checkable=True, checked=True)
    win.act_toggle_right.triggered.connect(
        lambda on: win.right_scroll.setVisible(on))
    m.addAction(win.act_toggle_right)

    m = mb.addMenu(tr("Help"))
    m.addAction(tr("단축키..."), lambda: QMessageBox.information(
        win, tr("단축키"),
        tr("양손이 GELLO 리더 위에 있으므로 마우스 없이 조작합니다.\n"
           "같은 키가 상태에 따라 다르게 동작합니다.\n\n"
           "  자세 정렬 중   Space        텔레옵 시작\n"
           "  기록 중        Space        성공으로 끝내기\n"
           "  기록 중        Esc          실패로 끝내기\n"
           "  기록 중        Delete       폐기\n"
           "  자세 정렬 중   Enter        자동 정렬 다시 (대략 맞춘 뒤에만)\n"
           "  리셋 대기 중   Esc          직전 에피소드 판정 뒤집기\n"
           "  리셋 대기 중   Enter        리셋 완료 — 계속\n\n"
           "지금 쓸 수 있는 키는 Collect 패널 아래에 초록색으로 표시됩니다.")))
    m.addSeparator()
    m.addAction(tr("정보"), lambda: QMessageBox.information(
        win, tr("정보"),
        tr("FR3 GELLO 데이터 수집 워크스페이스\n\n"
           "카메라는 항상 중앙에 유지됩니다. 왼쪽 아이콘 바로 패널만 바꾸세요.")))


def build_statusbar(win) -> None:
    """상태바는 **GUI 밖에서 도는 것들의 생사**만 본다 (2026-09-06 결정).

    로봇 노드와 카메라 노드는 별도 프로세스라 GUI 와 무관하게 죽을 수 있고,
    죽은 줄 모르고 조작하는 것이 이 화면에서 가장 비싼 실수다. 반면 '기록
    중'은 세션 상태이지 프로세스의 생사가 아니라 여기서 뺐다 -- 헤더 띠가
    배경색과 글자로 이미 말하고 있고, 그쪽이 훨씬 크다.
    """
    sb = win.statusBar()
    win.lights = {}
    # 리더암은 별도 프로세스가 아니라 GUI 안의 스레드 + USB 장치다
    # (joint-limit-wall). 그래도 여기 두는 이유는 같다 -- GUI 밖의 물건이라
    # 혼자 죽을 수 있고, 죽은 줄 모르고 조작하는 것이 비싸다. 특히 정렬
    # 보조가 과부하로 포기하면(blocked) 지금까지는 화면에 아무 표시가 없었다.
    for key, label in (("robot", "Robot"), ("leader", "Leader"),
                       ("camera", "Camera"), ("node", "Node")):
        light = StatusLight(label)
        sb.addWidget(light)
        win.lights[key] = light
    win.sb_right = QLabel("")
    sb.addPermanentWidget(win.sb_right)
