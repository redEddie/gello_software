"""Tiny two-language (ko/en) string table backing the LIBERO collector GUI's
language toggle button (see apps/collect_workspace.py's
"언어 전환" button and Settings menu action).

Not a general i18n framework -- just enough for a foreign collaborator to
read button/dialog/status text. Deliberately NOT covering:

* The log panel (``LiberoCollectorWindow._log``) -- a running, timestamped
  record of what happened, some of it interleaved with external processes'
  own stdout (runme.sh, launch_nodes.py, convert_libero_to_lerobot.py,
  upload_to_hub.py). Translating a live log reliably would mean re-parsing
  and re-templating every line of several other scripts' Korean print()
  calls too -- not worth it for a transient log.
* Raw exception messages / other truly dynamic content passed straight
  through from Python or an external process.

Usage: call ``set_language("en"/"ko")`` from the toggle handler, then
``tr(ko_text)`` anywhere a static Korean UI string is passed to a Qt widget.
Templated strings (containing ``{placeholders}``) are translated FIRST,
then ``.format(...)``-ed by the caller -- ``tr()`` itself never formats.
"""

from __future__ import annotations

_LANG = "ko"

# ko -> en. Keys are the exact Korean strings used at call sites (including
# any trailing "..." or embedded "{placeholder}"s) -- tr() does a plain dict
# lookup, so a key here must match a call site byte-for-byte or it silently
# falls back to Korean (see tr()'s docstring).
_EN: dict[str, str] = {
    # ---- window / group boxes ----
    "FR3 + GELLO -- LIBERO 데이터 수집기": "FR3 + GELLO -- LIBERO Data Collector",
    "로봇 노드 (launch_nodes.py, pylibfranka-venv)": "Robot node (launch_nodes.py, pylibfranka-venv)",
    "세션 설정 (연결 전에만 수정 가능)": "Session settings (editable only before connecting)",
    "카메라": "Cameras",
    "자세 매칭 (leader vs follower, 게이트 {gate} rad)": "Pose matching (leader vs follower, gate {gate} rad)",
    "제어": "Controls",
    "상태": "Status",
    "데이터셋 탐색기 (저장 경로 아래의 *_demo.hdf5)": "Dataset explorer (*_demo.hdf5 under the save path)",

    # ---- config box labels ----
    "로봇 IP:": "Robot IP:",
    "Task 이름:": "Task name:",
    "언어 지시문:": "Language instruction:",
    "데이터 저장 경로:": "Data save path:",
    "Reset pose:": "Reset pose:",
    "Grip:": "Grip:",
    "에피소드 최대 길이(초):": "Max episode length (s):",
    "리셋 대기(초):": "Reset wait (s):",
    "Agentview 카메라:": "Agentview camera:",
    "Wrist 카메라:": "Wrist camera:",
    "Agentview (외부 카메라)": "Agentview (external camera)",
    "Eye-in-hand (손목 카메라)": "Eye-in-hand (wrist camera)",
    "로봇 연결 후 표시됩니다": "Shown after connecting to the robot",
    "선택 안함 -- 대기 중": "None selected -- waiting",
    "파일 / 에피소드": "File / episode",
    "프레임수": "Frames",
    "결과": "Result",
    "Action Space:": "Action space:",
    "이미지 해상도:": "Image resolution:",

    # ---- buttons ----
    "찾아보기...": "Browse...",
    "카메라 목록 새로고침": "Refresh camera list",
    "로봇 노드 시작": "Start robot node",
    "노드 재시작": "Restart node",
    "노드 중지": "Stop node",
    "로봇 연결": "Connect robot",
    "텔레옵 시작 (Space)": "Start teleop (Space)",
    "리셋 대기 건너뛰기 (Enter)": "Skip reset wait (Enter)",
    "저장 (성공) (Space)": "Save (success) (Space)",
    "저장 (실패) (Esc)": "Save (fail) (Esc)",
    "폐기 (Delete)": "Discard (Delete)",
    "홈으로 이동": "Go home",
    "세션 종료": "End session",
    "새로고침": "Refresh",
    "선택 삭제": "Delete selected",
    "선택 구조 확인...": "Inspect structure...",
    "LeRobot 변환...": "Convert to LeRobot...",
    "HDF5 업로드...": "Upload HDF5...",
    "구조 미리보기...": "Preview structure...",
    "닫기": "Close",
    "변환 시작": "Start conversion",
    "업로드 시작": "Start upload",
    "자세 매칭 자동화 (Space)": "Automate pose matching (Space)",

    # ---- checkboxes ----
    "GELLO 조인트 한계 벽(wall) 사용": "Use GELLO joint-limit wall",
    "기존 데이터셋에 이어붙이기 (--resume)": "Append to existing dataset (--resume)",
    "기본값 사용 (LIBERO 표준 구조)": "Use default (LIBERO standard schema)",
    "Action에 그리퍼 값 포함 (끄면 위 Action Space 어떤 걸 골라도 그리퍼 차원이 빠집니다)":
        "Include gripper in action (off drops the gripper dim for any Action Space above)",
    "Action 그리퍼 값을 Observation과 동일하게 0=open/1=close로 저장 "
    "(기본은 -1/+1, robosuite 컨벤션)":
        "Store action's gripper the same way as observation, 0=open/1=close "
        "(default is -1/+1, robosuite convention)",
    "Agentview RGB (외부 카메라 이미지)": "Agentview RGB (external camera image)",
    "Eye-in-hand RGB (손목 카메라 이미지)": "Eye-in-hand RGB (wrist camera image)",
    "Joint states (관절 위치)": "Joint states (joint positions)",
    "Gripper state (그리퍼 연속값)": "Gripper state (continuous value)",
    "Joint velocities (관절 속도) -- 제어루프에서 이미 계산됨, 추가 비용 없음":
        "Joint velocities -- already computed in the control loop, no extra cost",
    "Timestamp (프레임별 wall-clock 시각) -- 프레임 간격 검증용":
        "Timestamp (per-frame wall-clock time) -- for verifying frame intervals",
    "256x256 (LIBERO 기본, 정사각형 크롭)": "256x256 (LIBERO default, square-cropped)",
    "원본 해상도 유지 (리사이즈 안 함)": "Keep native resolution (no resize)",
    "성공(success=True) 에피소드만 포함 (--only-success)": "Include only success=True episodes (--only-success)",
    "처음부터 새로 만들지 않고 기존 Hub 데이터셋에 이어붙이기 (--resume) -- 다른 task를 "
    "추가할 때, 기존 데이터는 재변환/재업로드하지 않음":
        "Append to the existing Hub dataset instead of rebuilding from scratch (--resume) "
        "-- adding another task without re-converting/re-uploading the existing data",
    "변환 후 Hugging Face Hub에 바로 업로드 (--push)": "Upload to Hugging Face Hub right after converting (--push)",
    "비공개 데이터셋으로 업로드 (--private)": "Upload as a private dataset (--private)",
    "업로드 전 Hub의 기존 파일 삭제 (다른 이름으로 올렸던 예전 파일 정리용)":
        "Delete an existing file on the Hub before uploading (for cleaning up an old upload under a different name)",

    # ---- group boxes (dialogs) ----
    "저장할 Observation 필드": "Observation fields to save",
    "추가 필드 (LIBERO 표준 아님)": "Extra fields (not LIBERO-standard)",
    "Action 열 이름 (선택 -- 비워두면 기본값)": "Action column names (optional -- blank = default)",

    # ---- warning / hint labels ----
    "⚠ 이 화면의 버튼은 소프트웨어 텔레옵 루프만 멈춥니다. "
    "실제 비상정지는 로봇 하드웨어 E-stop을 사용하세요.":
        "⚠ Buttons on this screen only stop the software teleop loop. "
        "Use the robot hardware E-stop for a real emergency stop.",
    "HDF5는 삭제해도 파일 용량이 즉시 줄지 않습니다 (디스크 공간까지 "
    "회수하려면 h5repack 필요).":
        "Deleting from an HDF5 file doesn't immediately shrink it on disk "
        "(run h5repack to actually reclaim the space).",
    "⚠ 동시에 두 명이 같은 Repo ID로 --resume 변환하지 마세요 -- 같은 파일 경로를 서로 "
    "덮어써서 조용히 데이터가 유실될 수 있습니다.":
        "⚠ Don't have two people --resume-convert to the same Repo ID at once -- "
        "they can silently overwrite each other's files at the same path.",

    # ---- tooltips / placeholders ----
    "저장 경로에 이미 있는 task를 선택하면 이어서 수집 가능 (--resume 자동 체크)":
        "Picking a task already in the save path lets you continue it (--resume auto-checked)",
    "먼저 대략적으로 자세를 맞추면 활성화됩니다 (게이트 {gate} rad 이내)":
        "Enables once roughly matched by hand (within {gate} rad gate)",
    "연결된 RealSense 목록에서 선택하거나 시리얼번호를 직접 입력":
        "Pick from connected RealSense cameras, or type a serial number directly",
    "예: pick up the red block": "e.g. pick up the red block",
    "비워두면 Task 이름과 동일하게 사용": "Uses the Task name if left blank",
    "찾아보기로 선택하세요": "Pick with Browse",
    "예: knu-physical-ai/fr3-libero-teleop-lerobot": "e.g. knu-physical-ai/fr3-libero-teleop-lerobot",
    "예: knu-physical-ai/fr3-libero-teleop": "e.g. knu-physical-ai/fr3-libero-teleop",
    "예: <task>_demo_<이름>_<날짜>.hdf5 -- 비워두면 로컬 파일 이름 그대로":
        "e.g. <task>_demo_<name>_<date>.hdf5 -- keeps the local filename if left blank",

    # ---- dialog titles ----
    "데이터셋 구조 사용자 지정": "Customize Dataset Schema",
    "데이터셋 구조 미리보기": "Dataset Structure Preview",
    "LeRobot 형식으로 변환": "Convert to LeRobot Format",
    "HDF5 원본 업로드": "Upload Raw HDF5",
    "구조 확인 -- {name} (진행 중인 세션)": "Structure -- {name} (session in progress)",
    "구조 확인 -- {name} / {demo}": "Structure -- {name} / {demo}",
    "데이터 저장 경로": "Data save path",
    "변환할 .hdf5 파일": ".hdf5 file(s) to convert",
    "업로드할 .hdf5 파일": ".hdf5 file to upload",
    "변환할 .hdf5 파일 (여러 개 선택 가능, 이미 큐레이션 끝난 파일):":
        ".hdf5 file(s) to convert (multiple allowed, already curated):",
    "업로드할 .hdf5 파일 (이미 큐레이션 끝난 파일):": ".hdf5 file to upload (already curated):",
    "Repo ID:": "Repo ID:",
    "로컬 출력 경로:": "Local output path:",
    "로컬 출력 경로": "Local output path",
    "FPS:": "FPS:",
    "Repo 안 파일 이름:": "Filename in repo:",
    "삭제할 기존 파일 이름:": "Existing filename to delete:",
    "비워두면 위 'Repo 안 파일 이름'과 동일": "Same as 'Filename in repo' above if left blank",

    # ---- QMessageBox titles/bodies ----
    "이미 실행 중": "Already running",
    "변환이 이미 진행 중입니다. 로그를 확인하세요.": "A conversion is already in progress. Check the log.",
    "업로드가 이미 진행 중입니다. 로그를 확인하세요.": "An upload is already in progress. Check the log.",
    "로봇 노드가 이미 실행 중입니다.": "The robot node is already running.",
    "변환 완료": "Conversion complete",
    "LeRobot 변환이 완료되었습니다. 로그를 확인하세요.": "LeRobot conversion finished. Check the log.",
    "업로드 완료": "Upload complete",
    "HDF5 업로드가 완료되었습니다. 로그를 확인하세요.": "HDF5 upload finished. Check the log.",
    "변환 실패": "Conversion failed",
    "업로드 실패": "Upload failed",
    "exit code {code} -- 로그를 확인하세요.": "exit code {code} -- check the log.",
    "파일 필요": "File required",
    ".hdf5 파일을 하나 이상 선택하세요.": "Select at least one .hdf5 file.",
    ".hdf5 파일을 선택하세요.": "Select a .hdf5 file.",
    "Repo ID 필요": "Repo ID required",
    "Repo ID를 입력하세요.": "Enter a Repo ID.",
    "출력 경로 필요": "Output path required",
    "로컬 출력 경로를 입력하세요.": "Enter a local output path.",
    "Task 이름 필요": "Task name required",
    "Task 이름을 입력하세요.": "Enter a Task name.",
    "입력 오류": "Input error",
    "에피소드 길이/리셋 대기는 숫자여야 합니다.": "Episode length / reset wait must be numbers.",
    "카메라 선택 필요": "Camera selection required",
    "Agentview / Wrist 카메라를 모두 선택하세요.": "Select both an Agentview and a Wrist camera.",
    "카메라 중복": "Duplicate camera",
    "Agentview와 Wrist에 같은 카메라가 선택되었습니다.": "The same camera is selected for both Agentview and Wrist.",
    "카메라 해제 실패": "Failed to release camera",
    "미리보기 카메라를 제때 해제하지 못했습니다. 잠시 후 다시 시도하세요.":
        "Couldn't release the preview cameras in time. Try again shortly.",
    "카메라 목록 조회 실패: {err}": "Failed to list cameras: {err}",
    "이름이 다릅니다": "Name mismatch",
    "로컬 파일({local}) 내용을 저장소의 다른 이름({repo})으로 "
    "업로드합니다 -- 그 이름의 기존 파일이 있다면 덮어씁니다. 계속할까요?":
        "Uploading the local file ({local}) under a different name in the "
        "repo ({repo}) -- this overwrites any existing file already at that "
        "name. Continue?",
    "(선택 안함)": "(None selected)",
    "삭제 불가": "Cannot delete",
    "현재 세션이 사용 중인 파일입니다. 먼저 '세션 종료'를 누르세요.":
        "This file is in use by the current session. Click 'End session' first.",
    "세션이 종료되었습니다. 새로고침 후 다시 시도하세요.": "The session has ended. Refresh and try again.",
    "파일 전체 삭제": "Delete entire file",
    "{name}\n이 작업의 데이터 전체(모든 에피소드)가 영구 삭제됩니다. 계속할까요?":
        "{name}\nAll data for this task (every episode) will be permanently deleted. Continue?",
    "에피소드 삭제": "Delete episode",
    "{name} / {demo} 를 삭제할까요?": "Delete {name} / {demo}?",
    "선택 필요": "Selection required",
    "구조를 확인할 파일 또는 에피소드를 선택하세요.": "Select a file or episode to inspect.",
    "확인 불가": "Cannot inspect",
    "세션이 아직 완전히 연결되지 않았습니다.": "The session isn't fully connected yet.",
    "빈 파일": "Empty file",
    "에피소드가 없습니다.": "No episodes.",
    "읽기 실패": "Read failed",
    "오류": "Error",
    "세션 요약": "Session summary",
    "파일: {path}\n에피소드: {num_episodes}개 (성공 {num_success} / "
    "실패 {num_fail} / 미표시 {num_unlabeled})\n총 프레임: {total_frames} "
    "(에피소드당 {min_frames}~{max_frames} 프레임)":
        "File: {path}\nEpisodes: {num_episodes} (success {num_success} / "
        "fail {num_fail} / unlabeled {num_unlabeled})\nTotal frames: {total_frames} "
        "({min_frames}-{max_frames} frames per episode)",

    # ---- dynamic status labels ----
    "대기": "Idle",
    "연결 중...": "Connecting...",
    "홈 복귀 중": "Homing",
    "환경 리셋 대기": "Waiting for reset",
    "자세 맞추는 중": "Matching pose",
    "접근 중": "Approaching",
    "기록 중": "Recording",
    "노드: -": "Node: -",
    "에피소드: {n}": "Episode: {n}",
    "노드: 정상": "Node: OK",
    "노드: 응답 없음 (launch_nodes 재시작 필요)": "Node: not responding (restart launch_nodes)",
    "모든 조인트 일치 -- '텔레옵 시작'을 누르세요": "All joints match -- click 'Start teleop'",
    "모든 조인트 일치 -- '자세 매칭 자동화'를 눌러 정밀 정렬하세요":
        "All joints match -- click 'Automate pose matching' for fine alignment",
    "{joint} 조인트를 맞춰주세요 (차이 {diff} rad)": "Match joint {joint} (diff {diff} rad)",
    "자동 정렬 중...": "Auto-aligning...",
    "자동 정렬 중... (최대 오차 {err} rad)": "Auto-aligning... (max error {err} rad)",
    "자동 정렬 완료 -- '텔레옵 시작'을 누르세요": "Auto-alignment complete -- click 'Start teleop'",
    "기록 중: {n} 프레임 ({s}s)": "Recording: {n} frames ({s}s)",
    "환경 리셋 대기: {s}s (건너뛰려면 버튼 클릭)": "Waiting for env reset: {s}s (click button to skip)",
    "{n}대 감지됨": "{n} detected",
    "감지된 RealSense 카메라가 없습니다 (시리얼번호 직접 입력 가능)":
        "No RealSense cameras detected (you can type a serial number directly)",
    "{serial} 미리보기 연결 중...": "Connecting preview: {serial}...",
    "미리보기 실패: {msg}": "Preview failed: {msg}",
    "중지됨": "Stopped",
    "시작 중...": "Starting...",
    "실행 중 (PID {pid})": "Running (PID {pid})",
    "{n}개 에피소드": "{n} episodes",
    "불러오는 중...": "Loading...",
    "(읽기 실패: {e})": "(read failed: {e})",
    "성공": "success",
    "실패": "fail",

    # ---- 수집 현황 패널 / 상태바 (이번 task vs 누적) ----
    "수집 현황": "Progress",
    "이번 task": "This task",
    "누적": "Total",
    "저장된 에피소드": "Episodes saved",
    "버림": "Discarded",
    "총 프레임": "Frames",
    "경과 시간": "Elapsed",
    "분당 에피소드": "Episodes/min",
    "{k}: 에피소드 {t}개 (이번 +{s})": "{k}: {t} episodes (+{s} this task)",
    "{t}개  (이번 +{s})": "{t}  (+{s} this task)",
    "저장 {s}": "{s} saved",
    "데이터셋 구조: 기본 (LIBERO 표준) -- 사용자 지정...": "Dataset schema: default (LIBERO standard) -- customize...",
    "데이터셋 구조: 사용자 지정 ({action}) -- 편집...": "Dataset schema: custom ({action}) -- edit...",

    # ---- action space labels (gello/dataset_schema.py) ----
    "EE-delta (LIBERO 기본)": "EE-delta (LIBERO default)",
    "EE-pose absolute (절대 목표 pose)": "EE-pose absolute (target pose)",
    "Joint-angle delta (변화량)": "Joint-angle delta (change)",
    "Joint-angle absolute (절대 목표값)": "Joint-angle absolute (target value)",

    # ---- menu items ----
    "리더암 토크 과부하 잠금 해제": "Leader arm: clear torque overload lock",
    "\n\n서보가 토크 과부하로 잠겼습니다. 세션을 종료한 뒤 "
    "Process > 리더암 토크 과부하 잠금 해제 를 실행하세요.":
        "\n\nA servo locked itself on torque overload. End the session, then run "
        "Process > Leader arm: clear torque overload lock.",

    # ---- language toggle button itself ----
    "한국어 / English": "한국어 / English",
}


def set_language(lang: str) -> None:
    global _LANG
    _LANG = lang


def get_language() -> str:
    return _LANG


def tr(text: str) -> str:
    """Translates ``text`` if the current language is English; returns it
    unchanged for Korean, or for any string missing from the table (so an
    un-cataloged string just falls back to Korean instead of crashing)."""
    if _LANG == "en":
        return _EN.get(text, text)
    return text
