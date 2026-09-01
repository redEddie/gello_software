"""Upload page builder for WorkspaceWindow."""
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.gui.gui_widgets import TODO_MARK, hf_account
from gello.gui.i18n import tr

from apps.dialogs._widgets import mark_todo


def build_upload(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)
    acct_text, acct_color = hf_account()
    win.hf_label = QLabel(acct_text)
    win.hf_label.setStyleSheet(f"color:{acct_color}; font-weight:bold;")
    win.hf_label.setWordWrap(True)
    col.addWidget(win.hf_label)
    # 이 PC는 공용이라 '누구로 올라가는가'가 매번 다를 수 있다. 확인과 전환을
    # 업로드 버튼 바로 위에 둔다 -- 올린 뒤 커밋 기록에서 알게 되면 늦는다.
    acct_btn = QPushButton(tr("계정 확인 / 전환..."))
    acct_btn.setToolTip(tr("이 PC는 공용입니다. 지금 어떤 토큰으로 올라가는지 "
                           "확인하고, 다른 사람 계정으로 바꿉니다."))
    acct_btn.clicked.connect(win.upload.on_hf_accounts)
    col.addWidget(acct_btn)

    # Repo ID 를 패널 밖으로 꺼내둔다. 다이얼로그 안에만 있을 때는 오타가
    # Recents 에 저장돼도 아무데도 보이지 않고, 자동 버튼이 그걸 그대로 다시
    # 쓴다 -- 실제로 'r/lerobot' 이 저장된 채 재압축 15.6분을 돌고 마지막
    # 업로드에서 403 으로 죽었다. 여기 있으면 누르기 전에 눈에 띈다.
    win.repo_edits = {}
    form = QFormLayout()
    form.setContentsMargins(0, 4, 0, 0)
    for key, label, tip in (
        ("repo_id", tr("LeRobot repo"),
         tr("변환본이 올라갈 저장소. <조직 또는 사용자>/<이름>")),
        ("hdf5_repo_id", tr("HDF5 repo"),
         tr("원본 .hdf5 가 올라갈 저장소. 변환본과 별개입니다.")),
    ):
        e = QLineEdit(win._recents_valid_repo(key))
        e.setPlaceholderText(tr("<조직 또는 사용자>/<이름>"))
        e.setToolTip(tip)
        e.textChanged.connect(win._on_repo_edited)
        win.repo_edits[key] = e
        form.addRow(QLabel(label), e)
    col.addLayout(form)
    win.repo_warn = QLabel("")
    win.repo_warn.setStyleSheet("color:#e67e22;")
    win.repo_warn.setWordWrap(True)
    col.addWidget(win.repo_warn)
    win._on_repo_edited()

    # 세 묶음으로 나눈다. 위에서 아래로 갈수록 범위가 좁아진다 --
    # 전부 / 원본(HDF5)만 / 변환본(LeRobot)만. 묶음마다 첫 줄이 "자동"이고
    # 그 아래가 같은 일을 쪼갠 수동 단계라, 어느 버튼이 어느 버튼을 포함하는지
    # 위치만 봐도 읽힌다.
    pipe_btn = win._upload_button(
        col, tr("전체 처리 (재압축 → 변환 → 업로드)"),
        tr("Hub과 로컬을 대조해 필요한 것만 순서대로 실행합니다.\n"
           "재압축 → LeRobot 변환 → LeRobot 업로드까지 한 번에.\n"
           "확인 창에서 시작을 누르면 끝까지 무인으로 진행합니다."),
        win.upload.on_pipeline, primary=True, color="#2ecc71")

    col.addSpacing(14)
    hdf5_box = QGroupBox(tr("HDF5 원본"))
    hcol = QVBoxLayout(hdf5_box)
    hcol.setSpacing(6)
    win._upload_button(
        hcol, tr("재압축 + 업로드 (자동)"),
        tr("아래 두 단계를 순서대로 실행합니다.\n"
           "재압축이 필요한 파일만 골라 줄인 뒤, 원본 .hdf5 를 Hub에 올립니다."),
        win.upload.on_hdf5_auto, primary=True, color="#9b59b6")
    win._upload_button(
        hcol, tr("용량 최적화 (재압축)"),
        tr("lzf 압축으로 .hdf5 크기를 줄입니다. 내용은 그대로입니다.\n"
           "이미 재압축된 파일은 건너뜁니다."),
        win.upload.on_repack)
    win._upload_button(
        hcol, tr("원본 업로드..."),
        tr("큐레이션이 끝난 .hdf5 를 그대로 Hub에 올립니다.\n"
           "변환본(LeRobot)과는 별개의 저장소입니다."),
        win.upload.on_hdf5_upload)
    col.addWidget(hdf5_box)

    col.addSpacing(14)
    lerobot_box = QGroupBox(tr("LeRobot 변환본"))
    lcol = QVBoxLayout(lerobot_box)
    lcol.setSpacing(6)
    win._upload_button(
        lcol, tr("변환 + 업로드 (자동)"),
        tr("전체를 처음부터 다시 만들어 Hub을 통째로 교체합니다.\n"
           "이어붙이기(resume)를 쓰지 않으므로, 큐레이션에서 지운 에피소드가 "
           "Hub에서도 사라집니다.\n실행 전에 항상 확인 창을 띄웁니다."),
        win.upload.on_lerobot_auto, primary=True, color="#3498db")
    win._upload_button(
        lcol, tr("이어붙이기 (새 에피소드만)"),
        tr("Hub과 대조해 새로 추가된 에피소드만 변환해 이어붙입니다.\n"
           "5~10개씩 추가 수집한 날은 전체 재빌드 대신 이걸로 몇 분이면 "
           "끝납니다.\n에피소드를 삭제·편집한 흔적이 있으면 안전하게 거부하고 "
           "전체 재빌드를 안내합니다."),
        win.upload.on_lerobot_resume, primary=True, color="#1abc9c")
    win._upload_button(
        lcol, tr("HDF5 골라서 변환만..."),
        tr("올리지 않고 로컬에만 변환합니다.\n"
           "결과를 눈으로 확인한 뒤 아래 버튼으로 올리세요."),
        win.upload.on_lerobot)
    win._upload_button(
        lcol, tr("전체 task 다시 업로드..."),
        tr("이미 변환해둔 로컬 결과를 Hub에 통째로 교체 업로드합니다 "
           "(재변환 없음).\n로컬에 없는 원격 파일도 함께 지우므로, 큐레이션으로 "
           "삭제한 에피소드가 Hub에 남지 않습니다."),
        win.upload.on_lerobot_reupload)
    col.addWidget(lerobot_box)

    col.addSpacing(10)
    note = QLabel(tr(
        "'변환 + 업로드 (자동)'은 전체를 새로 만들어 교체합니다 — 큐레이션으로 "
        "지운 에피소드를 Hub에서도 없애는 유일한 방법입니다. 추가만 한 날은 "
        "'이어붙이기'가 새 에피소드만 변환해서 훨씬 빠릅니다."))
    note.setStyleSheet("color:#888;")
    note.setWordWrap(True)
    col.addWidget(note)
    qbox = QGroupBox(f"{tr('업로드 큐 / 이력')} ({TODO_MARK})")
    qcol = QVBoxLayout(qbox)
    qcol.addWidget(QLabel(tr("업로드는 현재 한 번에 하나씩, 로그 탭으로만 확인합니다.")))
    mark_todo(qbox, tr("큐잉과 이력 보관은 아직 없습니다."))
    col.addWidget(qbox)
    col.addStretch()
    return w

