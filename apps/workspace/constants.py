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
#
# 순서는 **수집 한 바퀴의 순서**다 (2026-09-06 사용자 결정). 조작자가 실제로
# 도는 길은 레퍼런스 배치와 비교하며 카메라를 점검하고 -> scene 을 정하고 ->
# 찍고 -> 찍은 것을 고르는 것인데, 아이콘 순서가 그 길과 달라서 매번 위아래로
# 오갔다. 아래 WORKFLOW 가 그 길의 정본이고, 여기 순서는 그것을 따른다.
ACTIVITIES = (
    ("layout", "🎯", "Layout", "레퍼런스 배치와 비교하며 카메라 점검"),
    ("configure", "⚙", "Configure", "로봇 노드·Scene·수집 설정"),
    ("collect", "🎮", "Collect", "수집 제어와 현재 상태"),
    ("dataset", "📂", "Dataset", "에피소드 목록·재생·삭제"),
    ("stats", "📊", "Statistics", "세션 통계·수집 이력"),
    ("upload", "☁", "Upload", "재압축·LeRobot 변환·업로드"),
    ("settings", "🛠", "Settings", "스키마·레이아웃"),
)

#: 수집 한 바퀴 -- (활동 키, 번호, 이 단계에서 하는 일).
#:
#: 활동 바에 있는 것 전부가 여기 있지는 않다. Statistics·Upload·Settings 는
#: 필요할 때 여는 **도구**이지 매번 지나는 단계가 아니다 -- 번호를 붙이면
#: 매 바퀴 들러야 하는 것처럼 읽힌다. 번호가 붙은 넷만이 조작자가 실제로
#: 매번 도는 길이다 (2026-09-06 사용자 서술 그대로).
WORKFLOW = (
    ("layout", "①", "카메라 점검"),
    ("configure", "②", "Scene 설정"),
    ("collect", "③", "수집"),
    ("dataset", "④", "큐레이션"),
)


def workflow_step(key: str):
    """그 활동이 수집 한 바퀴의 몇 번째 단계인가 -- (번호, 하는 일). 단계가
    아니면 None."""
    for k, mark, label in WORKFLOW:
        if k == key:
            return mark, label
    return None


def next_workflow(key: str):
    """다음 단계 -- (키, 번호, 하는 일). 마지막 단계거나 단계가 아니면 None."""
    keys = [k for k, _m, _l in WORKFLOW]
    if key not in keys:
        return None
    i = keys.index(key)
    return WORKFLOW[i + 1] if i + 1 < len(WORKFLOW) else None


#: 중앙 탭 -- (키, 제목). 키가 정본이다: 코드는 인덱스가 아니라 키로 탭을
#: 가리킨다 (인덱스는 탭이 늘거나 줄면 밀린다). 순서가 곧 표시 순서다.
CENTER_TABS = (
    ("live", "Live"),
    ("plan", "Plan"),
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
    "layout": ("live", "layout"),
    # Plan 이 앞이다 -- scene 배치를 정하는 데 카메라는 필요 없고, 필요한 것은
    # "어느 지시문이 몇 개 남았나"다 (2026-09-06 사용자 지적). live 는 남긴다:
    # 어느 활동에서든 카메라를 잃지 않는다는 것이 이 창의 설계 전제다.
    "configure": ("plan", "live"),
    "collect": ("live",),
    "dataset": ("live", "playback", "analysis", "trim", "gallery"),
    "stats": ("live", "playback", "analysis", "trim", "gallery"),
    "upload": ("live",),
    "settings": ("live",),
}

# 오른쪽 패널에서 값이 길어 좌우 배치로는 읽기 어려운 항목들.
WIDE_FIELDS = {"ds_file", "ds_task"}

# 0.5배는 접촉 순간을 한 프레임씩 볼 때, 2~3배는 긴 에피소드를 훑을 때 쓴다.
# 3배면 60Hz라 프레임을 건너뛰지 않고도 타이머만으로 낼 수 있다.
PLAYBACK_SPEEDS = (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0))

