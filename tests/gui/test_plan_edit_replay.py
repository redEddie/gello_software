"""계획 편집 다이얼로그 + replay 로더 검증."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.path.insert(0, WT + "/scripts/analyze")
sys.argv = ["t"]
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402
from apps.workspace.features.scene.dialogs.plan_json_dialog import PlanJsonDialog  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="planedit_"))
plan_copy = TMP / "pilot.json"
shutil.copy(f"{WT}/configs/collection/plans/pilot.json", plan_copy)

# 1. 편집 다이얼로그: 유효한 수정 -> 저장됨
dlg = PlanJsonDialog(None, plan_copy)
data = json.loads(dlg.editor.toPlainText())
data["scenes"][0]["slots"][0]["target"] = 12
dlg.editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
dlg._save()
saved = json.loads(plan_copy.read_text())
assert saved["scenes"][0]["slots"][0]["target"] == 12
assert dlg.result() == 1 or dlg.warnings == []  # accept 됨
print("1 통과: 유효 수정 저장 (target 10->12)")

# 2. 규칙 위반(같은 scene 중복 ID) -> 저장 거부 + 오류 표시, 파일 무변경
dlg2 = PlanJsonDialog(None, plan_copy)
bad = json.loads(dlg2.editor.toPlainText())
bad["scenes"][0]["slots"].append(
    {"instruction_id": "I000", "instruction": "open the top drawer", "target": 5})
dlg2.editor.setPlainText(json.dumps(bad, ensure_ascii=False))
dlg2._save()
assert dlg2.error_label.text(), "오류 미표시"
assert json.loads(plan_copy.read_text())["scenes"][0]["slots"][0]["target"] == 12
assert len(json.loads(plan_copy.read_text())["scenes"][0]["slots"]) == len(data["scenes"][0]["slots"])  # 거부 -> 슬롯 수 불변
print("2 통과: 규칙 위반 저장 거부 --", dlg2.error_label.text()[:50])

# 3. 깨진 JSON -> 거부
dlg3 = PlanJsonDialog(None, plan_copy)
dlg3.editor.setPlainText("{ not json")
dlg3._save()
assert dlg3.error_label.text()
print("3 통과: 깨진 JSON 거부")

# 4. replay 로더: scene + legacy 양포맷 (scene 이 세션에 잠겨 있으면
#    친절한 SystemExit 가 곧 검증 대상이다)
from replay_episode import load_trajectory  # noqa: E402

# 실데이터에 의존하지 않는다 -- selftest 로 scene+legacy 파일을 만들어 검증
import subprocess  # noqa: E402

_d = Path(tempfile.mkdtemp(prefix="replay_"))
subprocess.run([sys.executable, WT + "/scripts/check/check_scene_file.py",
                "--selftest", "--keep", str(_d)], check=True, capture_output=True)
t1 = load_trajectory(_d / "scene_000.hdf5", "episode_000")
assert t1["q"].shape[1] == 7 and len(t1["grip"]) == len(t1["q"])
assert t1["source"] == "commanded_joint_states"
t2 = load_trajectory(_d / "selftest_task_demo.hdf5", "demo_0")
assert t2["q"].shape[1] == 7
# 없는 에피소드 / 없는 파일 -> 친절한 SystemExit
try:
    load_trajectory(_d / "scene_000.hdf5", "episode_999")
    raise AssertionError("없는 에피소드가 통과됨")
except SystemExit as e:
    assert "가 없습니다" in str(e) and "episode_000" in str(e), e
try:
    load_trajectory(_d / "nope.hdf5", "episode_000")
    raise AssertionError("없는 파일이 통과됨")
except SystemExit as e:
    assert "열지 못했습니다" in str(e), e
print(f"4 통과: replay 로더 scene({len(t1['q'])}f) + legacy({len(t2['q'])}f) + 친절 오류 2종")

print("\n계획 편집 + replay 로더 검증 통과")
import os  # noqa: E402

os._exit(0)
