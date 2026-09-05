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

import time
import traceback
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from gello.gui.fonts import ensure_font  # noqa: E402


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
    # 워크스페이스는 아직 역할 이름으로 카메라를 찾는다 (agent_serial /
    # wrist_serial). cam id -> 역할 -> 시리얼 로 옮겨 적어 다리를 놓는다.
    # 노드를 시리얼 기준으로 바꾸면(설계 중) 이 다리는 없어진다.
    for cam, serial in (result.cameras or {}).items():
        role = (result.cam_roles or {}).get(cam, "")
        if role and serial:
            recents.add(f"{role}_serial", serial)


def install_excepthook(log_path) -> None:
    """마법사 단계의 처리되지 않은 오류를 파일에 남긴다.

    PyQt 는 슬롯을 빠져나온 파이썬 예외에 qFatal() -- abort() -- 을 부른다.
    트레이스백은 stderr 로 가는데 아이콘 실행에는 stderr 가 없으니, 조작자
    입장에서는 **창이 아무 말 없이 사라진다**. 워크스페이스에는 이 훅이
    있었지만 마법사에는 없어서, 2026-09-05 의 스테이션 저장 크래시는 재현부터
    해야 원인을 알 수 있었다.

    오류 창은 띄우지 않는다 (2026-09-05 사용자 결정): 마법사는 아직 아무것도
    시작하지 않은 단계라 조용히 닫히는 편이 낫고, 무엇이 있었는지는 로그가
    말한다. 훅이 있으면 abort 대신 정상 종료 경로를 타므로 카메라·로봇 노드
    정리(reject -> cleanup)도 제대로 돈다.
    """
    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", buffering=1) as f:
                f.write(f"\n[{time.strftime('%H:%M:%S')}] [예외] 마법사에서 "
                        f"처리되지 않은 오류\n{text}\n")
        except OSError:
            pass
        print(text, file=sys.stderr, flush=True)

    sys.excepthook = hook


def main() -> None:
    from apps.workspace.constants import LOG_DIR  # noqa: E402

    install_excepthook(
        LOG_DIR / f"launcher_{time.strftime('%Y%m%d_%H%M%S')}.log")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    ensure_font(app)

    from apps.workspace.launcher import LauncherWizard  # noqa: E402

    wiz = LauncherWizard()
    if wiz.exec() != QDialog.DialogCode.Accepted or wiz.result() is None:
        sys.exit(0)     # 창 닫기 = 종료

    apply_result(wiz.result())

    from apps import collect_workspace  # noqa: E402  -- 지연 임포트 (위 참조)

    res = wiz.result()
    collect_workspace.main(app=app, camera_node=res.camera_node,
                           camera_node_spec=res.camera_node_spec,
                           robot_node=res.robot_node,
                           schema_version=res.schema_version)


if __name__ == "__main__":
    main()
