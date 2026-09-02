"""Shared workspace constants that both collect_workspace.py and its
builders/pages need without creating circular imports."""
from pathlib import Path

LOG_DIR = Path.home() / "libero_gui_logs"

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
    ("settings", "🛠", "Settings", "언어·스키마"),
)
# 오른쪽 패널에서 값이 길어 좌우 배치로는 읽기 어려운 항목들.
WIDE_FIELDS = {"ds_file", "ds_task"}

# 0.5배는 접촉 순간을 한 프레임씩 볼 때, 2~3배는 긴 에피소드를 훑을 때 쓴다.
# 3배면 60Hz라 프레임을 건너뛰지 않고도 타이머만으로 낼 수 있다.
PLAYBACK_SPEEDS = (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0))

