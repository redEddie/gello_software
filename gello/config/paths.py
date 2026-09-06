"""GUI 가 남기는 상태 파일의 뿌리 한 곳.

격자·크롭·recents·업로드 장부·썸네일·로그가 전부 여기 아래에 쌓인다. 전에는
파일마다 ``Path.home() / "libero_gui_logs"`` 를 따로 적었고, 그 탓에 **테스트가
조작자의 실사용 파일을 덮어썼다** (2026-09-06 확인).

어떻게 새어 나갔나: 격자 테스트가 ``collect_workspace.save_grid_store`` 를
패치했는데, ``features/camera/ops.py`` 는 그 함수를 모듈에서 직접 임포트해
자기 이름으로 들고 있었다. 패치는 한 이름만 가리므로 다른 이름을 통한 호출은
그대로 진짜 파일로 갔고, 조작자가 맞춰 둔 3×3 격자가 스위트를 돌릴 때마다
기본값으로 초기화됐다. 호출 하나를 막는 방식은 임포트 방식에 따라 새므로
방어가 되지 못한다 -- 뿌리를 옮기는 쪽이 어떤 경로로 부르든 막힌다.

    GELLO_STATE_DIR=/tmp/... python ...   # 이 아래로만 쓴다

tests/gui/run_all.sh 가 임시 폴더를 지정한다. 값이 없으면 평소 위치다.
"""
from __future__ import annotations

import os
from pathlib import Path

#: 이 환경변수가 있으면 상태 파일의 뿌리를 그리로 옮긴다.
STATE_DIR_ENV = "GELLO_STATE_DIR"

#: 평소 위치. 로그·격자·크롭이 한 폴더에 모여 있어 조작자가 통째로 백업하기 쉽다.
DEFAULT_STATE_DIR = Path.home() / "libero_gui_logs"


def state_dir() -> Path:
    """상태 파일의 뿌리. 환경변수가 이긴다."""
    override = os.environ.get(STATE_DIR_ENV)
    return Path(override) if override else DEFAULT_STATE_DIR
