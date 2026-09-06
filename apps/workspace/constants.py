"""Shared workspace constants that both collect_workspace.py and its
builders/pages need without creating circular imports."""
from pathlib import Path

from gello.config.paths import state_dir

LOG_DIR = state_dir()

# Child-process scripts used by both WorkspaceWindow and its domain modules.
# Kept here so domains can import them without creating a circular dependency
# back to collect_workspace.py.
WT_ROOT = Path(__file__).resolve().parent.parent.parent
CONVERT_SCRIPT = str(WT_ROOT / "scripts" / "convert" / "convert_libero_to_lerobot.py")
LAYOUT_ZIP = WT_ROOT / "assets" / "libero_init_layouts.zip"
LAYOUT_DIR = WT_ROOT / "assets" / "libero_init_layouts"
UPLOAD_SCRIPT = str(WT_ROOT / "scripts" / "convert" / "upload_to_hub.py")
REPACK_SCRIPT = str(WT_ROOT / "scripts" / "convert" / "repack_hdf5.py")
REPLAY_SCRIPT = str(WT_ROOT / "scripts" / "analyze" / "replay_episode.py")
CHECK_CAMERAS = str(WT_ROOT / "scripts" / "check" / "check_cameras.py")
RESET_PROTECTION = str(WT_ROOT / "scripts" / "check" / "gello_reset_protection.py")
RUNME_SCRIPT = str(WT_ROOT / "scripts" / "runme.sh")

# Activity bar entries: (key, icon, title, tooltip). Icons are emoji rather
# than a theme lookup -- an icon theme that is missing on this machine would
# leave the strip blank, and the strip is the only navigation there is.
ACTIVITIES = (
    ("configure", "⚙", "Configure", "로봇·카메라·태스크 설정"),
    ("collect", "🎮", "Collect", "수집 제어와 현재 상태"),
    ("dataset", "📂", "Dataset", "에피소드 목록·재생·삭제"),
    ("upload", "☁", "Upload", "재압축·LeRobot 변환·업로드"),
    ("stats", "📊", "Statistics", "세션 통계"),
    ("layout", "🎯", "Layout", "LIBERO 초기 배치와 카메라 비교"),
    ("settings", "🛠", "Settings", "스키마·레이아웃"),
)
#: 중앙 탭 -- (키, 제목). 키가 정본이다: 코드는 인덱스가 아니라 키로 탭을
#: 가리킨다 (인덱스는 탭이 늘거나 줄면 밀린다). 순서가 곧 표시 순서다.
CENTER_TABS = (
    ("live", "Live"),
    ("playback", "Playback"),
    ("analysis", "Analysis"),
    ("trim", "Trim"),
    ("layout", "레이아웃"),
    ("gallery", "Gallery"),
    ("cloud", "Point Cloud"),
    ("depth", "Depth"),
)

#: 활동별로 중앙에 띄우는 탭. 활동 바(왼쪽)와 중앙 탭이 같은 축이라 --
#: 수집 / 큐레이션 / 셋업·점검 -- 함께 움직인다.
#:
#: 여기 어느 줄에도 없는 탭("cloud", "depth")은 **색인 전용**이다: 화면에는
#: 안 나오고 View 메뉴로만 열리며, 열면 지금 활동에 잠깐 붙었다가 활동을
#: 옮기면 떨어진다. 구현은 멀쩡한데(세션 중 차단·미리보기 충돌 처리까지)
#: 수집 워크플로에는 없는 도구라, 표면을 늘리지 않고 남겨 두는 자리다
#: (2026-09-06 사용자 결정). 상시로 승격하려면 여기 한 줄에 키를 넣으면 된다.
#:
#: "live" 는 어느 활동에서든 남는다. 수집 도중 파일을 미리 보러 Dataset 으로
#: 건너가는 워크플로가 실제로 있고, 그때 카메라를 잃으면 안 된다 (툴바의
#: 수집 흐름 고정 구획과 같은 이유). layout.py 의 "카메라는 항상 중앙에
#: 유지된다"는 설계 의도이기도 하다.
CENTER_TABS_BY_ACTIVITY = {
    "configure": ("live",),
    "collect": ("live",),
    "dataset": ("live", "playback", "analysis", "trim", "gallery"),
    "stats": ("live", "playback", "analysis", "trim", "gallery"),
    "upload": ("live",),
    "layout": ("live", "layout"),
    "settings": ("live",),
}

# 오른쪽 패널에서 값이 길어 좌우 배치로는 읽기 어려운 항목들.
WIDE_FIELDS = {"ds_file", "ds_task"}

# 0.5배는 접촉 순간을 한 프레임씩 볼 때, 2~3배는 긴 에피소드를 훑을 때 쓴다.
# 3배면 60Hz라 프레임을 건너뛰지 않고도 타이머만으로 낼 수 있다.
PLAYBACK_SPEEDS = (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0))

