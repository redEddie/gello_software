"""Toolbar, menu bar, and status bar builders for WorkspaceWindow."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QLabel, QMessageBox, QToolBar

from gello.data.episode_stats import TASK_DEV_LIMIT
from gello.gui.gui_widgets import TODO_MARK
from gello.gui.i18n import tr

from apps.dialogs._widgets import StatusLight
from apps.workspace.constants import ACTIVITIES, LOG_DIR


def build_toolbar(win) -> None:
    tb = QToolBar(tr("주요 작업"))
    tb.setMovable(False)
    tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    win.addToolBar(tb)
    win.tb_actions = {}

    def add(key: str, text: str, slot, tip: str = "") -> QAction:
        act = QAction(text, win)
        act.setToolTip(tip or text)
        act.triggered.connect(slot)
        tb.addAction(act)
        win.tb_actions[key] = act
        return act

    add("connect", tr("▶ Connect"), win._on_connect, tr("로봇에 연결하고 세션 시작"))
    add("disconnect", tr("■ Disconnect"), win._on_disconnect, tr("세션 종료"))
    tb.addSeparator()
    add("record", tr("● Record"), lambda: win._cmd("cmd_start_teleop"), tr("기록 시작"))
    # _save, not _cmd -- the success flag has to be recorded for the stats
    # panel, and a toolbar button that counts differently from the side
    # panel button next to it is a bug waiting to be blamed on the stats.
    add("save", tr("✔ Save"), lambda: win._save(True), tr("성공으로 끝내기"))
    add("savefail", tr("✖ Save (fail)"), lambda: win._save(False),
        tr("실패로 끝내기 (Esc). 판정은 리셋 구간에서 Esc로 뒤집을 수 있습니다"))
    add("discard", tr("🗑 Discard"), lambda: win._cmd("cmd_discard_episode"))
    tb.addSeparator()
    add("home", tr("⌂ Home"), lambda: win._cmd("cmd_go_home"))
    add("refresh_cam", tr("⟳ Camera"), win._refresh_cameras)
    tb.addSeparator()
    add("upload", tr("☁ Upload"), lambda: win._set_activity("upload"))



def build_menu(win) -> None:
    mb = win.menuBar()

    m = mb.addMenu(tr("File"))
    m.addAction(tr("데이터 저장 경로 열기..."), win._browse_root)
    m.addAction(tr("로그 폴더 열기"), lambda: win.log(f"[로그] {LOG_DIR}"))
    m.addSeparator()
    m.addAction(tr("종료"), win.close)

    m = mb.addMenu(tr("Dataset"))
    m.addAction(tr("새로고침"), win._refresh_dataset_tree)
    m.addAction(tr("실패만 선택"), win._on_select_failed)
    m.addAction(tr("튀는 것만 선택 (scene·문장 그룹 평균과 ±{d} 밖)")
                .format(d=TASK_DEV_LIMIT),
                win._on_select_jerky)
    m.addAction(tr("에피소드 삭제"), win._on_delete_selected)
    m.addAction(tr("파일 삭제"), win._on_delete_file)
    m.addAction(tr("구조 확인..."), win.playback_ops.on_show_structure)
    m.addSeparator()
    m.addAction(tr("용량 최적화 (재압축)..."), win.upload.on_repack)
    m.addAction(tr("LeRobot 변환/업로드..."), win.upload.on_lerobot)
    m.addSeparator()
    m.addAction(tr("전체 처리 (재압축 → 변환 → 업로드)..."), win.upload.on_pipeline)
    m.addAction(tr("HDF5 업로드..."), win.upload.on_hdf5_upload)

    m = mb.addMenu(tr("Robot"))
    m.addAction(tr("노드 시작"), win._on_start_node)
    m.addAction(tr("노드 종료"), win._on_stop_node)
    m.addSeparator()
    m.addAction(tr("연결"), win._on_connect)
    m.addAction(tr("세션 종료"), win._on_disconnect)
    m.addAction(tr("홈으로"), lambda: win._cmd("cmd_go_home"))

    m = mb.addMenu(tr("Camera"))
    m.addAction(tr("새로고침"), win._refresh_cameras)
    m.addAction(tr("미리보기 중지"), win._stop_previews_async)
    m.addAction(tr("카메라 노드 재시작"),
                win._on_restart_camera_node)
    m.addAction(tr("카메라 노드 종료 (카메라 해제)"),
                win._on_stop_camera_node_manual)

    m = mb.addMenu(tr("View"))
    for key, _icon, title, _tip in ACTIVITIES:
        m.addAction(title, lambda _c=False, k=key: win._set_activity(k))
    m.addSeparator()
    win.act_toggle_bottom = QAction(tr("하단 패널"), win, checkable=True, checked=True)
    win.act_toggle_bottom.triggered.connect(
        lambda on: win.bottom_tabs.setVisible(on))
    m.addAction(win.act_toggle_bottom)
    win.act_toggle_right = QAction(tr("오른쪽 패널"), win, checkable=True, checked=True)
    win.act_toggle_right.triggered.connect(
        lambda on: win.right_scroll.setVisible(on))
    m.addAction(win.act_toggle_right)

    m = mb.addMenu(tr("Tools"))
    m.addAction(tr("시스템 튜닝 실행 (runme.sh)"), win._run_runme)
    m.addAction(tr("카메라 점검 (USB 속도·프레임)"), win._on_check_cameras)
    m.addAction(tr("리더암 서보 보호 해제 (재부팅)"),
                win._on_reset_leader_protection)
    m.addAction(tr("Hugging Face 계정..."), win.upload.on_hf_accounts)
    m.addSeparator()
    m.addAction(tr("데이터셋 구조 사용자 설정..."), win._on_schema)
    m.addAction(f'{tr("언어 전환")} ({TODO_MARK})').setEnabled(False)

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
    sb = win.statusBar()
    win.lights = {}
    for key, label in (("robot", "Robot"), ("camera", "Camera"),
                       ("recording", "Recording"), ("node", "Node")):
        light = StatusLight(label)
        sb.addWidget(light)
        win.lights[key] = light
    win.sb_right = QLabel("")
    sb.addPermanentWidget(win.sb_right)
