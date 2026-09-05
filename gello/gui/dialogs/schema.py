from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gello.data.dataset_schema import DatasetSchemaConfig
from gello.data.schema_description import describe_schema
from gello.gui.fonts import MONO_STACK
from gello.gui.i18n import tr


class DatasetSchemaDialog(QDialog):
    """Lets the operator pick the action space and which obs fields get
    written, before connecting.

    There is deliberately no "use LIBERO defaults" master switch: it used to
    override every control here at write time, so a session set to
    ``joint_absolute`` silently wrote ``ee_delta`` instead. A bare
    ``DatasetSchemaConfig()`` already *is* the LIBERO default, so leaving the
    controls alone achieves the same thing visibly.
    """

    _OBS_FIELDS = [
        ("save_agentview_rgb", "Agentview RGB (외부 카메라 이미지)"),
        ("save_eye_in_hand_rgb", "Eye-in-hand RGB (손목 카메라 이미지)"),
        ("save_joint_states", "Joint states (관절 위치)"),
        ("save_gripper_states", "Gripper state (그리퍼 연속값)"),
        ("save_ee_states", "EE states (EE pos + orientation)"),
        ("save_ee_pos", "EE position"),
        ("save_ee_ori", "EE orientation (axis-angle)"),
    ]
    # QComboBox itemData 는 int 는 왕복하지만 Python None 은 왕복하지 않는다.
    _IMAGE_SIZE_NATIVE = -1

    _EXTRA_FIELDS = [
        ("save_joint_velocities", "Joint velocities (관절 속도) -- 제어루프에서 이미 계산됨, 추가 비용 없음"),
        ("save_timestamp", "Timestamp (프레임별 wall-clock 시각) -- 프레임 간격 검증용"),
        ("save_agentview_depth",
         "Agentview depth (uint16 mm, 무손실) -- 에피소드당 +수십 MB, USB 대역 주의 (#17)"),
        ("save_eye_in_hand_depth",
         "Eye-in-hand depth (uint16 mm, 무손실) -- D405 근거리 정밀 depth (#17)"),
    ]
    def __init__(self, parent: QWidget, cfg: DatasetSchemaConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("데이터셋 구조 사용자 설정"))
        layout = QVBoxLayout(self)

        # Action 쪽은 이 다이얼로그에서 고를 수 없다. 액션 공간·그리퍼 규약·열
        # 이름이 파일마다 갈리면 한 데이터셋 안에서 조용히 호환되지 않는 파일이
        # 생기고, 그걸 잡아주는 장치가 지금 없다(issue #12). 값 자체는
        # DatasetSchemaConfig 의 FIXED_* 로 박혀 있고, 여기서는 무엇으로
        # 고정돼 있는지만 보여준다.
        fixed = QGroupBox(tr("Action 구조 (고정 -- 변경 불가)"))
        fixed_layout = QVBoxLayout(fixed)
        fixed_note = QLabel(tr(
            "Action Space: joint_absolute — 관절 절대각 7 + 그리퍼\n"
            "그리퍼: 0=open / 1=close 이진값 "
            "(Observation 의 gripper_states 는 0~1 연속값)\n"
            "열 이름: joint1.pos .. joint7.pos, gripper.pos "
            "— Observation 과 동일"))
        fixed_note.setWordWrap(True)
        fixed_note.setStyleSheet("color:#888;")
        fixed_layout.addWidget(fixed_note)
        layout.addWidget(fixed)

        self.field_checks: dict[str, QCheckBox] = {}

        obs_box = QGroupBox(tr("저장할 Observation 필드"))
        obs_layout = QVBoxLayout(obs_box)

        # None 은 QComboBox itemData 로 왕복하지 않아 센티널로 저장한다.
        image_size_row = QHBoxLayout()
        image_size_row.addWidget(QLabel(tr("이미지 해상도:")))
        self.image_size_combo = QComboBox()
        self.image_size_combo.addItem(tr("256x256, 정사각 크롭"), 256)
        self.image_size_combo.addItem(tr("480x480, 정사각 크롭"), 480)
        self.image_size_combo.addItem(
            tr("640x480, 크롭 없음 (카메라 원본)"), self._IMAGE_SIZE_NATIVE)
        target = cfg.image_size if cfg.image_size is not None else self._IMAGE_SIZE_NATIVE
        idx = self.image_size_combo.findData(target)
        if idx >= 0:
            self.image_size_combo.setCurrentIndex(idx)
        image_size_row.addWidget(self.image_size_combo, 1)
        obs_layout.addLayout(image_size_row)
        size_note = QLabel(tr(
            "에피소드 200프레임 기준 저장 시간 1.0 / 3.6 / 5.0초, "
            "파일 79 / 277 / 370MB"))
        size_note.setStyleSheet("color:#888;")
        size_note.setWordWrap(True)
        obs_layout.addWidget(size_note)

        for attr, label in self._OBS_FIELDS:
            cb = QCheckBox(tr(label))
            cb.setChecked(getattr(cfg, attr))
            self.field_checks[attr] = cb
            obs_layout.addWidget(cb)
        layout.addWidget(obs_box)

        extra_box = QGroupBox(tr("추가 필드 (LIBERO 표준 아님)"))
        extra_layout = QVBoxLayout(extra_box)
        for attr, label in self._EXTRA_FIELDS:
            cb = QCheckBox(tr(label))
            cb.setChecked(getattr(cfg, attr))
            self.field_checks[attr] = cb
            extra_layout.addWidget(cb)
            # depth 수집은 lerobot 0.5.0 RealSenseCamera 가 read_latest_depth 를
            # 지원하지 않아 당분간 비활성화. 코드는 남겨두고 UI/플래그만 막는다.
            if attr in ("save_agentview_depth", "save_eye_in_hand_depth"):
                # 결과는 항상 False 이므로 표시도 False -- True 설정이 들어와도
                # "체크된 채 비활성" 으로 표시-결과가 어긋나게 두지 않는다.
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setToolTip(tr(
                    "카메라 드라이버(lerobot RealSenseCamera)가 depth 읽기를 "
                    "지원하지 않아 수집이 비활성화되어 있습니다"))
        layout.addWidget(extra_box)

        self._editable_widgets = [obs_box, extra_box]

        # Not in _editable_widgets on purpose -- always clickable, since
        # describe_schema() resolves
        # .effective() internally and will just show the LIBERO default
        # structure in that case (still a useful confirmation).
        preview_btn = QPushButton(tr("구조 미리보기..."))
        preview_btn.clicked.connect(self._show_preview)
        layout.addWidget(preview_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _current_config(self) -> DatasetSchemaConfig:
        """The config implied by the dialog's current widget state --
        regardless of whether OK has been clicked yet. Shared by
        result_config() (on accept) and _show_preview() (live, before
        committing to anything).
        """
        # Observation 쪽만 위젯에서 읽는다. Action 쪽은 dataclass 기본값이
        # 곧 고정값이므로 아무것도 넘기지 않는 것이 그대로 고정을 뜻한다.
        kwargs = {attr: cb.isChecked() for attr, cb in self.field_checks.items()}
        raw = self.image_size_combo.currentData()
        kwargs["image_size"] = None if raw == self._IMAGE_SIZE_NATIVE else raw
        # depth 수집은 드라이버 미지원으로 비활성화되어 있으나, 다이얼로그에서
        # 체크 상태가 어떻든 저장 시 강제 Off 로 막는다 (fix/depth-gate).
        kwargs["save_agentview_depth"] = False
        kwargs["save_eye_in_hand_depth"] = False
        return DatasetSchemaConfig(**kwargs)

    def result_config(self) -> DatasetSchemaConfig:
        return self._current_config()

    def _show_preview(self) -> None:
        text = describe_schema(self._current_config())
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("데이터셋 구조 미리보기"))
        layout = QVBoxLayout(dlg)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setStyleSheet(f"font-family: {MONO_STACK};")
        view.setMinimumSize(480, 360)
        layout.addWidget(view)
        close_btn = QPushButton(tr("닫기"))
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()
