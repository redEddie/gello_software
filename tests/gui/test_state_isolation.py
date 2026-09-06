"""테스트가 조작자의 상태 파일을 건드리지 않는지 검증.

2026-09-06 사고: 스위트를 돌릴 때마다 조작자가 카메라에 맞춰 둔 3×3 격자가
기본값으로 초기화됐다. 격자 테스트는 ``collect_workspace.save_grid_store`` 를
패치해 막고 있다고 믿었지만, ``features/camera/ops.py`` 가 같은 함수를 모듈에서
직접 임포트해 **자기 이름으로** 들고 있었다. 패치는 한 이름만 가리므로 다른
이름을 통한 호출은 그대로 홈 디렉터리로 갔다.

교훈은 "그 테스트를 고친다" 가 아니다 -- 호출부를 하나씩 막는 방식은 임포트
방식이 바뀌면 다시 샌다. 상태 파일의 **뿌리**를 옮기고(GELLO_STATE_DIR),
그것이 실제로 적용됐는지를 여기서 지킨다.

같은 위험이 격자 말고도 여섯 개 더 있었다: 크롭, recents, 업로드 장부,
썸네일, 스키마 설정, 세션 로그. 뿌리 하나를 옮기면 전부 같이 막힌다.
"""
import os
import sys
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)

from gello.config.paths import (  # noqa: E402
    DEFAULT_STATE_DIR,
    STATE_DIR_ENV,
    state_dir,
)

# --- 1) 스위트는 실사용 위치 밖에서 돌아야 한다 --------------------------
# run_all.sh 가 mktemp 로 잡아 준다. 직접 실행할 때는 이 검사를 건너뛴다.
override = os.environ.get(STATE_DIR_ENV)
if override:
    assert state_dir() == Path(override), (state_dir(), override)
    assert state_dir() != DEFAULT_STATE_DIR, \
        "GELLO_STATE_DIR 이 실사용 위치를 가리킨다 -- 조작자 파일을 덮어쓴다"
    print(f"1 통과: 상태 뿌리가 임시 폴더다 ({state_dir()})")
else:
    print("1 건너뜀: GELLO_STATE_DIR 없음 (run_all.sh 밖에서 직접 실행)")

# --- 2) 상태 파일이 전부 그 뿌리 아래로 간다 -----------------------------
# 하나라도 Path.home() 을 직접 쓰면 그 파일만 실사용 위치로 새고, 그러면
# 뿌리를 옮긴 의미가 없다. 새로 상태 파일을 만드는 사람이 잊기 쉬운 지점이라
# 목록으로 못박는다.
import gello.data.crop as crop  # noqa: E402
import gello.data.dataset_schema as ds  # noqa: E402
import gello.data.hub_upload_state as hub  # noqa: E402
import gello.gui.constants as gconst  # noqa: E402
import gello.gui.grid_overlay as go  # noqa: E402
import gello.gui.scene_gallery as gal  # noqa: E402
from apps.workspace.constants import LOG_DIR  # noqa: E402

root = state_dir()
CHECKED = {
    "격자": go.GRID_DIR,
    "격자(옛 전역)": go.LEGACY_GRID_STORE_PATH,
    "크롭": crop.CROP_PARAMS_DIR,
    "recents": gconst.RECENTS_PATH,
    "업로드 장부": hub.STATE_PATH,
    "썸네일": gal.THUMBS_DIR,
    "스키마 설정": ds.DEFAULT_CONFIG_PATH,
    "세션 로그": LOG_DIR,
}
for label, p in CHECKED.items():
    assert Path(p) == root or root in Path(p).parents, \
        f"{label} 이 상태 뿌리 밖이다: {p} (뿌리 {root})"
print(f"2 통과: 상태 파일 {len(CHECKED)}종이 모두 뿌리 아래에 있다")

# --- 3) 뿌리는 환경변수로만 바뀐다 ---------------------------------------
# 코드가 Path.home() 을 다시 하드코딩하면 override 가 무시된다.
_prev = os.environ.get(STATE_DIR_ENV)
os.environ[STATE_DIR_ENV] = "/tmp/gello-state-probe"
assert state_dir() == Path("/tmp/gello-state-probe"), state_dir()
if _prev is None:
    del os.environ[STATE_DIR_ENV]
else:
    os.environ[STATE_DIR_ENV] = _prev
assert state_dir() == (Path(_prev) if _prev else DEFAULT_STATE_DIR)
print("3 통과: 환경변수가 뿌리를 바꾸고, 지우면 평소 위치로 돌아온다")

print("\n상태 격리 검증 통과")
