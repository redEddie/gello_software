"""Dataset page builder for WorkspaceWindow."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gello.data.episode_stats import TASK_DEV_LIMIT
from gello.gui.constants import TODO_MARK
from gello.gui.i18n import tr

from apps.dialogs._widgets import mark_todo


def build_dataset(win) -> QWidget:
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 0)
    # 이 페이지 전용 폴더 선택 -- 수집 저장 경로(root_edit)와 독립적으로
    # 다른 폴더(예: old_data/)를 훑어볼 수 있다. 초기값은 수집 경로.
    dr = QHBoxLayout()
    win.dataset_root_edit = QLineEdit(
        win.root_edit.text() if hasattr(win, "root_edit")
        else str(Path.home() / "libero_datasets"))
    win.dataset_root_edit.editingFinished.connect(win.dataset_ops.refresh_dataset_tree)
    dr.addWidget(win.dataset_root_edit, 1)
    dbrowse = QPushButton(tr("..."))
    dbrowse.setMaximumWidth(36)
    dbrowse.clicked.connect(win.dataset_ops.browse_dataset_root)
    dr.addWidget(dbrowse)
    col.addLayout(dr)
    search = QLineEdit()
    search.setPlaceholderText(f"{tr('에피소드 검색')} ({TODO_MARK})")
    mark_todo(search, tr("검색/필터는 아직 없습니다."))
    col.addWidget(search)
    win.dataset_tree = QTreeWidget()
    win.dataset_tree.setColumnCount(3)
    win.dataset_tree.setHeaderLabels([tr("파일 / 에피소드"), tr("프레임"), tr("결과")])
    win.dataset_tree.setColumnWidth(0, 300)
    # 큐레이션은 실패 여러 개를 한 번에 지우는 작업이다.
    win.dataset_tree.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection)
    win.dataset_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    win.dataset_tree.itemSelectionChanged.connect(win.dataset_ops.on_dataset_selection)
    col.addWidget(win.dataset_tree, 1)
    # 파일 삭제는 여기 없다. 에피소드 삭제 바로 옆에 두었더니 실제로 오클릭이
    # 났고, 한 번에 태스크 하나가 통째로 날아간다. 되돌릴 수 없는 조작은
    # 한 단계 더 들어가야 닿도록 Dataset 메뉴에만 둔다.
    #
    # 두 줄로 나누고 삭제만 떼어놓는 이유는 폭이 아니라 종류다. 위 네 개는
    # 읽거나 고르기만 하고, 아래 하나만 파일을 바꾼다 -- 한 줄에 다섯 개가
    # 나란히 있으면 그 차이가 라벨 글자에만 남는다.
    for pair in ((("새로고침", win.dataset_ops.refresh_dataset_tree,
                   "데이터 폴더를 다시 읽어 목록을 새로 그립니다."),
                  ("구조 확인", win.playback_ops.on_show_structure,
                   "선택한 *파일*의 에피소드 수·용량·이미지 압축·재압축 이력과\n"
                   "첫 에피소드의 데이터 구조를 보여줍니다.")),
                 (("HDF5 트리 뷰어", win._on_hdf5_tree,
                   "선택한 파일의 전체 내부 구조(그룹/데이터셋/attrs)를\n"
                   "트리로 탐색합니다. 데이터셋을 클릭하면 shape·dtype·압축과\n"
                   "이미지 미리보기/값 미리보기가 나옵니다 (myHDF5 스타일)."),
                  ("myHDF5 (웹)", win.upload.on_myhdf5,
                   "브라우저에서 myhdf5.hdfgroup.org 를 엽니다.\n"
                   "파일을 창에 끌어다 놓으면 같은 구조를 웹에서 봅니다.")),
                 (("실패만 선택", win.dataset_ops.on_select_failed,
                   "success=False 로 표시된 에피소드를 모두 선택합니다.\n"
                   "선택만 하고 지우지 않습니다."),
                  ("튀는 것만 선택", win.dataset_ops.on_select_jerky,
                   "같은 (scene·문장) 그룹 평균과 ±{d} 넘게 차이 나는 에피소드를 모두 선택합니다.\n"
                   "선택만 하고 지우지 않습니다. (Analysis 탭과 같은 기준)"))):
        row = QHBoxLayout()
        for text, slot, tip in pair:
            b = QPushButton(tr(text))
            b.setToolTip(tr(tip).format(d=TASK_DEV_LIMIT))
            b.clicked.connect(slot)
            row.addWidget(b)
        col.addLayout(row)

    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setStyleSheet("color:#444;")
    col.addWidget(line)

    trim_btn = QPushButton(tr("끝 다듬기 (Trim 탭에서)"))
    trim_btn.setToolTip(tr(
        "선택한 에피소드를 Trim 탭에서 엽니다.\n"
        "저장 키를 누를 때 흔들린 마지막 몇 프레임을 잘라냅니다."))
    trim_btn.clicked.connect(win.playback_ops.on_open_trim)
    col.addWidget(trim_btn)

    relabel_btn = QPushButton(tr("선택 재판정 (성공↔실패)"))
    relabel_btn.setToolTip(tr(
        "scene 에피소드 전용. 선택한 에피소드의 quality_status 를 성공↔실패로 "
        "뒤집습니다.\nscene 체계에서 삭제를 대신하는 큐레이션 수단입니다 -- "
        "변환은 success 만 내보냅니다.\nbad_data 등 다른 상태는 건드리지 않습니다."))
    relabel_btn.clicked.connect(win.dataset_ops.on_relabel_selected)
    col.addWidget(relabel_btn)

    # 재생 중에는 이 버튼 자체가 '■ 재생 중단' 으로 바뀐다 -- 별도 중단
    # 버튼은 화면 밖으로 밀려 안 보이는 일이 있었다.
    win.replay_btn = QPushButton(tr("선택 재생 (실로봇)"))
    win.replay_btn.setToolTip(tr(
        "기록된 관절 명령을 같은 주기로 다시 보내 에피소드를 실로봇에서 "
        "재현합니다.\n로봇 노드가 켜져 있어야 하고, 로봇이 실제로 "
        "움직입니다. 주변을 비우세요.\n재생 중에는 이 버튼이 '재생 중단'"
        "이 됩니다 (중단 시 로봇은 현재 포즈 유지)."))
    win.replay_btn.clicked.connect(win.playback_ops.on_replay_selected)
    col.addWidget(win.replay_btn)

    del_btn = QPushButton(tr("선택한 에피소드 삭제"))
    del_btn.setToolTip(tr(
        "선택한 에피소드를 .hdf5 에서 실제로 지웁니다 (실패·튀는 궤적 큐레이션).\n"
        "legacy/scene 모두 삭제 후 번호를 다시 매깁니다 (scene 은 slot E번호와 "
        "uid 도 재부여).\nHub 에 이미 올라간 에피소드면 전체 재빌드가 필요합니다."
        "\n되돌릴 수 없습니다. 수집 중이 아닌 "
        "파일이면 세션 없이도 삭제됩니다. 파일 통째 삭제는 Dataset 메뉴에."))
    del_btn.setStyleSheet("background-color:#c0392b; color:white; padding:6px;")
    del_btn.clicked.connect(win.dataset_ops.on_delete_selected)
    col.addWidget(del_btn)

    # 빈 채로 시작한다. 고정 안내문은 매번 같은 말을 차지하기만 했고, 정작
    # 알아야 할 것("N개 선택됨")은 누른 뒤에만 생긴다.
    win.dataset_hint = QLabel("")
    win.dataset_hint.setStyleSheet("color:#888;")
    win.dataset_hint.setWordWrap(True)
    col.addWidget(win.dataset_hint)
    return w

