"""수집 런처 진입점 — 마법사를 먼저 띄우고, 끝나면 워크스페이스를 연다.

Run inside lerobot-venv::

    (pylibfranka-venv) python scripts/launch/launch_nodes.py --robot fr3   # terminal 1
    (lerobot-venv)     python apps/collect_launcher.py                     # terminal 2

데스크톱 아이콘은 이 파일을 실행해야 한다. 마법사가 고른 station 이
collect_workspace 의 모듈 레벨 load_station() 보다 먼저 적용돼야 하므로,
collect_workspace 임포트를 마법사가 끝난 뒤로 미룬다 (deferred import).
마법사를 건너뛰고 워크스페이스를 바로 열려면 예전처럼
apps/collect_workspace.py 를 직접 실행하면 된다.
"""

from __future__ import annotations

import os

# collect_workspace 와 같은 이유 -- numpy/cv2/h5py 임포트보다 먼저 와야 한다
# (launcher 패키지가 scene_format 을 통해 numpy 를 끌어오므로 여기서도 필요).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402


def apply_result(result, recents=None) -> None:
    """마법사 결과를 환경에 반영한다 — station env + recents pre-write.

    station 은 collect_workspace 임포트(모듈 레벨 load_station) 전에 확정해야
    하므로, 이 함수는 반드시 `from apps import collect_workspace` 보다 먼저
    불러야 한다.
    """
    if result.station:
        os.environ["GELLO_STATION"] = result.station
    if recents is None:
        from gello.gui.widgets import Recents

        recents = Recents()
    recents.add("data_root", str(result.dataset_root))
    if result.agent_serial:
        recents.add("agent_serial", result.agent_serial)
    if result.wrist_serial:
        recents.add("wrist_serial", result.wrist_serial)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from apps.workspace.launcher import LauncherWizard  # noqa: E402

    wiz = LauncherWizard()
    if wiz.exec() != QDialog.DialogCode.Accepted or wiz.result() is None:
        sys.exit(0)     # 창 닫기 = 종료

    apply_result(wiz.result())

    from apps import collect_workspace  # noqa: E402  -- 지연 임포트 (위 참조)

    collect_workspace.main(app=app)


if __name__ == "__main__":
    main()
