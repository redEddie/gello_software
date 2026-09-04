"""수집 런처 마법사 — 아이콘 클릭 시 워크스페이스보다 먼저 뜨는 창.

설치 마법사처럼 단계를 밟는다:

    모드 선택 (버튼 2개만)  ->  이어서 하기 / 새 데이터세트  ->  하드웨어

Cancel 버튼은 없다 (NoCancelButton) -- 창 닫기(X)가 곧 종료다. 첫 페이지는
Next 도 숨겨서 "이어서 하기"/"새 데이터세트" 버튼 2개만 보인다.

Finish 하면:
- 새 데이터세트: 폴더 + dataset-identity.json + (선택 시) instructions.json
  복사본을 만든다.
- 이어서 하기: 메타가 없는 legacy 폴더면 폴더명으로 dataset-identity.json
  을 만들어 준다.
- 결과(LaunchResult)는 apps/collect_launcher.py 가 recents/env 에 반영하고
  워크스페이스를 연다.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QMessageBox, QWizard

from gello.scene.dataset_meta import (
    DatasetIdentity,
    load_identity,
    plan_path,
    save_identity,
)
from gello.gui.i18n import tr

from apps.workspace.launcher.pages import (
    PAGE_CONTINUE,
    PAGE_HW,
    PAGE_MODE,
    PAGE_NEW,
    ContinuePage,
    HardwarePage,
    ModePage,
    NewDatasetPage,
)


@dataclass
class LaunchResult:
    """Finish 가 확정한 시작 설정."""
    mode: str                     # "continue" | "new"
    dataset_root: Path
    station: str
    agent_serial: str
    wrist_serial: str
    identity: DatasetIdentity
    # 하드웨어 페이지가 미리보기를 위해 띄운 카메라 노드. 워크스페이스가
    # 이어서 쓴다 -- 여기서 죽였다가 창이 다시 띄우면 카메라를 두 번 여는
    # 셈이라 느리고, 겹치면 포트 6021 충돌로 죽는다.
    camera_node: object = None
    camera_node_spec: str = ""


class LauncherWizard(QWizard):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mode: Optional[str] = None
        self._result: Optional[LaunchResult] = None
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoCancelButton, True)
        self.setWindowTitle(tr("FR3 GELLO 데이터 수집"))
        self.setPage(PAGE_MODE, ModePage())
        self.setPage(PAGE_CONTINUE, ContinuePage())
        self.setPage(PAGE_NEW, NewDatasetPage())
        self.setPage(PAGE_HW, HardwarePage())
        self.setStartId(PAGE_MODE)
        self.currentIdChanged.connect(self._on_page)
        self._on_page(PAGE_MODE)
        self.resize(640, 520)

    # 첫 페이지에는 모드 버튼 2개만 보인다 -- Back/Next 는 중복이라 숨긴다.
    def _on_page(self, page_id: int) -> None:
        for btn in (QWizard.WizardButton.BackButton,
                    QWizard.WizardButton.NextButton):
            self.button(btn).setVisible(page_id != PAGE_MODE)

    def result(self) -> Optional[LaunchResult]:
        return self._result

    def accept(self) -> None:  # noqa: N802 - Qt override
        try:
            self._result = self._build_result()
        except OSError as e:
            QMessageBox.warning(self, tr("데이터셋 준비 실패"), str(e))
            return
        super().accept()

    def reject(self) -> None:  # noqa: N802 - Qt override
        # 창을 닫으면 종료다 -- 미리보기용으로 띄운 카메라 노드를 남기면
        # 카메라를 쥔 프로세스가 주인 없이 떠돈다.
        self.page(PAGE_HW).cleanup()
        super().reject()

    def _build_result(self) -> LaunchResult:
        hw: HardwarePage = self.page(PAGE_HW)  # type: ignore[assignment]
        agent, wrist = hw.cameras()
        today = time.strftime("%Y-%m-%d")
        if self.mode == "new":
            pg: NewDatasetPage = self.page(PAGE_NEW)  # type: ignore[assignment]
            root = pg.target_path()
            ident = DatasetIdentity(
                name=pg.name_edit.text().strip(),
                concept=pg.concept_edit.toPlainText().strip(),
                created=today)
            root.mkdir(parents=True, exist_ok=False)
            save_identity(root, ident)
            src = pg.copy_source()
            if src is not None and plan_path(src).is_file():
                shutil.copy2(plan_path(src), plan_path(root))
        else:
            pg2: ContinuePage = self.page(PAGE_CONTINUE)  # type: ignore[assignment]
            root = pg2.selected_path()
            if root is None:
                raise OSError(tr("선택된 데이터셋이 없습니다."))
            ident = load_identity(root)
            if ident is None:
                # 메타 없는 legacy 폴더 -- 폴더명으로 identity 를 만들어 준다
                ident = DatasetIdentity(name=root.name, created=today)
                save_identity(root, ident)
        node, node_key = hw.take_node()
        return LaunchResult(mode=self.mode or "continue",
                            dataset_root=root,
                            station=hw.station(),
                            agent_serial=agent,
                            wrist_serial=wrist,
                            identity=ident,
                            camera_node=node,
                            camera_node_spec=node_key)
