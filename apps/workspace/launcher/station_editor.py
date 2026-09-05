"""스테이션 선택 + 새 스테이션 등록 폼 (런처 하드웨어 페이지의 윗부분).

두 가지 상태만 있다:

* **기존 스테이션 선택** -- 칸은 읽기 전용, 복제/삭제/저장 비활성.
* **`+ 새로 생성하기`** -- 칸 편집 가능, 세 버튼 활성.

기존 스테이션을 GUI 로 못 고치게 한 것은 실수가 아니라 결정이다
(2026-09-05): 셋업 값을 바꾸려면 YAML 을 직접 고치게 해서 **git 커밋 기록을
강제**한다. 로봇 IP 나 노드 포트가 조용히 바뀌면 "어제는 됐는데" 를 추적할
방법이 없다.

`삭제`는 **이번 마법사 세션에서 만든 것**에만 듣는다. 방금 만들었다가 마음이
바뀐 경우를 위한 것이고, 마법사를 닫으면 그 표시는 사라진다 -- 다음에 열면
그 스테이션도 "기존"이라 읽기 전용이다.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from gello.config.station import (
    CameraSpec,
    LeaderSpec,
    NodeSpec,
    RobotSpec,
    StationConfig,
    delete_station,
    list_stations,
    load_station,
    save_station,
    validate_station_name,
)
from gello.gui.i18n import tr

NEW_STATION = "\x00new"     # 드롭다운의 "+ 새로 생성하기" 항목 데이터


class StationEditor(QGroupBox):
    """스테이션 드롭다운 + 속성 칸 + 복제/삭제/저장."""

    #: 저장 버튼. 카메라 시리얼을 아는 하드웨어 페이지가 받아
    #: save_new() 를 부른다.
    save_requested = pyqtSignal()
    #: 카메라 줄이 바뀌었다 -- 하드웨어 페이지가 시리얼 줄을 맞춘다.
    cams_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(tr("스테이션"), parent)
        # 이번 세션에서 만든 스테이션 이름들. 마법사를 닫으면 사라진다 --
        # 그래서 다음에 열면 "기존"으로 취급되어 읽기 전용이 된다.
        self._mine: set[str] = set()
        col = QVBoxLayout(self)

        top = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_pick)
        top.addWidget(self.combo, 1)
        self.copy_btn = QPushButton(tr("복제"))
        self.copy_btn.setToolTip(tr("기존 스테이션의 값을 베껴 옵니다."))
        self.copy_btn.clicked.connect(self._on_copy)
        self.del_btn = QPushButton(tr("삭제"))
        self.del_btn.setToolTip(tr("이번에 만든 스테이션만 지울 수 있습니다."))
        self.del_btn.clicked.connect(self._on_delete)
        self.save_btn = QPushButton(tr("저장"))
        self.save_btn.clicked.connect(self._on_save)
        for b in (self.copy_btn, self.del_btn, self.save_btn):
            top.addWidget(b)
        col.addLayout(top)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("영문·숫자·.-_ (파일명이 됩니다)"))
        self.desc_edit = QLineEdit()
        self.ip_edit = QLineEdit()
        self.ip_edit.setToolTip(tr("FCI 주소입니다. 정책 서버 주소가 아닙니다."))
        self.pose_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.python_edit = QLineEdit()
        self.python_edit.setToolTip(tr(
            "pylibfranka 가 있는 venv. GUI 를 돌리는 인터프리터로는 로봇 "
            "노드를 띄울 수 없습니다."))
        self.leader_edit = QLineEdit()
        self.leader_edit.setPlaceholderText(tr("비우면 FTDI 장치를 자동으로 찾습니다"))
        node_row = QHBoxLayout()
        node_row.addWidget(self.host_edit, 1)
        node_row.addWidget(self.port_spin)
        for label, w in ((tr("이름"), self.name_edit),
                         (tr("설명"), self.desc_edit),
                         (tr("로봇 IP"), self.ip_edit),
                         (tr("리셋 자세"), self.pose_edit)):
            form.addRow(label, w)
        form.addRow(tr("노드 host / port"), node_row)
        form.addRow(tr("노드 python"), self.python_edit)
        form.addRow(tr("리더암 포트"), self.leader_edit)
        col.addLayout(form)

        # cam id -> 역할. 스테이션이 아는 것은 "그 자리에 어떤 역할의 카메라가
        # 있는가"뿐이다. 어느 실물이 꽂혔는지(시리얼)는 데이터셋이 정본이라
        # 여기 없다. 역할 이름은 기록에 그대로 남는 키워드를 쓴다 -- 화면에만
        # 예쁜 이름을 두면 조작자가 데이터에 뭐가 적히는지 알 수 없다.
        cam_head = QHBoxLayout()
        cam_head.addWidget(QLabel(tr("카메라 (cam id → 역할)")), 1)
        self.cam_add_btn = QPushButton("+")
        self.cam_add_btn.setMaximumWidth(32)
        self.cam_add_btn.setToolTip(tr("카메라 한 대 추가"))
        self.cam_add_btn.clicked.connect(self._on_add_cam)
        self.cam_del_btn = QPushButton("−")
        self.cam_del_btn.setMaximumWidth(32)
        self.cam_del_btn.setToolTip(tr("마지막 카메라 제거"))
        self.cam_del_btn.clicked.connect(self._on_del_cam)
        cam_head.addWidget(self.cam_add_btn)
        cam_head.addWidget(self.cam_del_btn)
        col.addLayout(cam_head)
        self.cam_form = QFormLayout()
        self.role_edits: dict[str, QLineEdit] = {}
        col.addLayout(self.cam_form)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        col.addWidget(self.msg)
        self._fields = (self.name_edit, self.desc_edit, self.ip_edit,
                        self.pose_edit, self.host_edit, self.python_edit,
                        self.leader_edit)
        self.name_edit.textChanged.connect(self._update_save_enabled)
        self.reload(select=None)

    # ------------------------------------------------------------- 목록
    def reload(self, select: "str | None") -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for s in list_stations():
            self.combo.addItem(s, s)
        self.combo.addItem(tr("+ 새로 생성하기..."), NEW_STATION)
        i = self.combo.findData(select) if select else -1
        self.combo.setCurrentIndex(i if i >= 0 else 0)
        self.combo.blockSignals(False)
        self._on_pick()

    def current_name(self) -> str:
        """지금 쓰기로 한 스테이션 이름. '새로 생성' 중이면 입력 중인 이름."""
        data = self.combo.currentData()
        if data == NEW_STATION:
            return self.name_edit.text().strip()
        return str(data or "")

    def is_creating(self) -> bool:
        return self.combo.currentData() == NEW_STATION

    # ------------------------------------------------------------- 상태
    def _on_pick(self, *_a) -> None:
        creating = self.is_creating()
        for w in self._fields:
            w.setReadOnly(not creating)
        self.port_spin.setReadOnly(not creating)
        self.port_spin.setEnabled(True)
        self.copy_btn.setEnabled(creating)
        self.save_btn.setEnabled(creating)
        # 역할은 기록에 남는 이름이라 기존 스테이션에서는 못 고친다 -- 바꾸면
        # 같은 데이터셋 안에서 같은 역할이 두 카메라를 가리키게 된다.
        for e in self.role_edits.values():
            e.setReadOnly(not creating)
        self.cam_add_btn.setEnabled(creating)
        self.cam_del_btn.setEnabled(creating)
        name = self.current_name()
        # 삭제는 '이번 세션에서 만든 것'에만 듣는다. 저장하면 드롭다운이 그
        # 이름으로 옮겨가 creating 이 꺼지는데, 거기서도 지울 수 있어야 한다
        # ("만들었다가 마음이 바뀐 경우"). 마법사를 닫으면 _mine 이 사라져
        # 그 스테이션도 다음부터는 읽기 전용이다.
        self.del_btn.setEnabled(creating or name in self._mine)
        if creating:
            if name not in self._mine:
                self._clear_fields()
            self.msg.setText(tr("새 스테이션입니다. 저장하면 "
                                "configs/stations/<이름>.yaml 이 생깁니다."))
            self.msg.setStyleSheet("color:#888;")
        else:
            self._fill_from(load_station(name) if name else StationConfig())
            self.msg.setText(tr("등록된 스테이션은 읽기 전용입니다 — 값을 바꾸려면 "
                                "YAML 을 직접 고치세요 (커밋 기록을 남기기 위해서입니다)."))
            self.msg.setStyleSheet("color:#888;")
        self._update_save_enabled()

    def _clear_fields(self) -> None:
        base = StationConfig()
        self._fill_from(base)
        self.name_edit.setText("")
        self.desc_edit.setText("")

    def _fill_from(self, cfg: StationConfig) -> None:
        self.name_edit.setText(cfg.name)
        self.desc_edit.setText(cfg.description)
        self.ip_edit.setText(cfg.robot.ip)
        self.pose_edit.setText(cfg.robot.reset_pose)
        self.host_edit.setText(cfg.node.host)
        self.port_spin.setValue(int(cfg.node.port))
        self.python_edit.setText(cfg.node.python)
        self.leader_edit.setText(cfg.leader.port or "")
        self._set_cams([(cam, cfg.cameras[cam].role) for cam in cfg.cam_ids()])

    # ------------------------------------------------------------- 카메라
    def _set_cams(self, pairs) -> None:
        """cam id -> 역할 줄을 다시 그린다. 화면이 곧 목록이다."""
        while self.cam_form.count():
            it = self.cam_form.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.role_edits = {}
        for cam, role in pairs:
            e = QLineEdit(role)
            e.setPlaceholderText(tr("기록에 남을 이름 (예: agent, wrist)"))
            e.setReadOnly(not self.is_creating())
            self.role_edits[cam] = e
            self.cam_form.addRow(cam, e)
        self.cams_changed.emit()

    def cam_roles(self) -> "dict[str, str]":
        """cam id -> 역할 (지금 화면 그대로)."""
        return {cam: e.text().strip() for cam, e in self.role_edits.items()}

    def _on_add_cam(self) -> None:
        pairs = list(self.cam_roles().items())
        n = len(pairs) + 1
        while f"cam{n}" in self.role_edits:
            n += 1
        pairs.append((f"cam{n}", ""))
        self._set_cams(pairs)

    def _on_del_cam(self) -> None:
        pairs = list(self.cam_roles().items())
        if len(pairs) <= 1:
            self.msg.setText(tr("카메라는 최소 한 대는 있어야 합니다."))
            self.msg.setStyleSheet("color:#e67e22;")
            return
        self._set_cams(pairs[:-1])

    def _update_save_enabled(self, *_a) -> None:
        if not self.is_creating():
            return
        name = self.name_edit.text().strip()
        if name and name in self._mine:
            # 방금 내가 만든 것을 다시 저장하려는 경우 -- 이름은 이미 존재하므로
            # validate 가 막는다. 지우고 다시 만들라고 안내한다.
            self.save_btn.setEnabled(False)
            self.msg.setText(tr("'{n}' 은 방금 저장했습니다. 고치려면 삭제 후 다시 "
                                "만드세요.").format(n=name))
            self.msg.setStyleSheet("color:#888;")
            return
        err = validate_station_name(name) if name else None
        self.save_btn.setEnabled(bool(name) and err is None)
        if name and err:
            self.msg.setText(err)
            self.msg.setStyleSheet("color:#e74c3c;")

    # ------------------------------------------------------------- 동작
    def _on_copy(self) -> None:
        names = list_stations()
        if not names:
            self.msg.setText(tr("베낄 스테이션이 없습니다."))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("어느 스테이션을 베낄까요?"))
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        lst.addItems(names)
        lst.setCurrentRow(0)
        lst.itemDoubleClicked.connect(lambda *_: dlg.accept())
        v.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted or lst.currentItem() is None:
            return
        src = lst.currentItem().text()
        self._fill_from(load_station(src))
        # 이름은 베끼지 않는다 -- 같은 이름은 저장이 거부되므로 빈 칸이 맞다.
        self.name_edit.setText("")
        self.msg.setText(tr("{s} 의 값을 베꼈습니다. 새 이름을 넣으세요.")
                         .format(s=src))
        self.msg.setStyleSheet("color:#888;")
        self._update_save_enabled()

    def _on_delete(self) -> None:
        name = self.current_name() or self.name_edit.text().strip()
        if name and name in self._mine:
            delete_station(name)
            self._mine.discard(name)
            self.reload(select=NEW_STATION)
            self.msg.setText(tr("{n} 을 지웠습니다.").format(n=name))
            self.msg.setStyleSheet("color:#888;")
            return
        # 아직 저장 전이면 '작성 중인 내용 버리기'
        self._clear_fields()
        self.msg.setText(tr("작성 중이던 내용을 비웠습니다."))
        self.msg.setStyleSheet("color:#888;")
        self._update_save_enabled()

    def build_config(self, cameras: "dict | None" = None) -> StationConfig:
        """현재 칸으로 StationConfig 를 만든다.

        cameras 를 주지 않으면 화면의 cam id -> 역할 표를 그대로 쓴다.
        시리얼은 여기 넣지 않는다 -- 데이터셋이 정본이다."""
        if cameras is None:
            cameras = {cam: CameraSpec(role=role)
                       for cam, role in self.cam_roles().items()}
        return StationConfig(
            name=self.name_edit.text().strip(),
            description=self.desc_edit.text().strip(),
            robot=RobotSpec(ip=self.ip_edit.text().strip(),
                            reset_pose=self.pose_edit.text().strip() or "libero"),
            node=NodeSpec(host=self.host_edit.text().strip(),
                          port=int(self.port_spin.value()),
                          python=self.python_edit.text().strip()),
            leader=LeaderSpec(port=self.leader_edit.text().strip() or None),
            cameras=cameras,
        )

    def save_new(self, cameras: "dict | None" = None) -> "str | None":
        """저장 성공이면 이름을, 실패면 None (사유는 msg 에)."""
        try:
            cfg = self.build_config(cameras)
            save_station(cfg)
        except Exception as e:  # noqa: BLE001
            self.msg.setText(f"{type(e).__name__}: {e}")
            self.msg.setStyleSheet("color:#e74c3c;")
            return None
        self._mine.add(cfg.name)
        self.reload(select=cfg.name)
        self.msg.setText(tr("{n} 을 만들었습니다. **커밋해 두세요** — 커밋 안 된 "
                            "파일이 있으면 아이콘 실행 때 자동 업데이트가 "
                            "건너뛰어집니다.").format(n=cfg.name))
        self.msg.setStyleSheet("color:#e67e22;")
        return cfg.name

    def _on_save(self) -> None:
        # 하드웨어 페이지가 받아 save_new() 를 부르고 그 결과로 화면을
        # 맞춘다 (시리얼 줄, git 경고).
        self.save_requested.emit()
