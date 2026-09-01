"""Shared workspace constants that both collect_workspace.py and its
builders/pages need without creating circular imports."""
from pathlib import Path

LOG_DIR = Path.home() / "libero_gui_logs"

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
