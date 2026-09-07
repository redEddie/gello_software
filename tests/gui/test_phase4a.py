"""Phase 4a 인수 테스트: 계획 로더 + slot 드롭다운/카운트/다음 slot/불일치 경고."""
import atexit
import json
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/apps")
sys.argv = ["t"]

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from gello.scene.collection_plan import (  # noqa: E402
    check_scene_against_plan,
    list_plans,
    load_plan,
)
from gello.scene.scene_format import list_scene_episodes  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="p4a_"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

# ---- 1. 로더: 합성 계획 + 규칙 검증 ----
# 실제 계획 파일은 데이터셋 폴더로 옮겼다 (instructions.json, 2026-09-04) --
# 테스트는 라이브 데이터에 기대지 않고 합성 계획을 만든다.
pilot = TMP / "pilot.json"
pilot.write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": "I000",
         "instruction": "pick up the blue cup and place it on the blue bowl",
         "target": 10},
        {"instruction_id": "I001",
         "instruction": "pick up the white cup and place it on the blue bowl",
         "target": 10}]},
    {"scene_id": "S001", "slots": [
        {"instruction_id": "I000", "instruction": "open the top drawer",
         "target": 10}]}]}, ensure_ascii=False), encoding="utf-8")
plan = load_plan(pilot)
assert plan.version == 1 and len(plan.scenes) >= 2
assert plan.slots_for("S000")[0].instruction_id == "I000"
assert len(plan.slots_for("S001")) >= 1 and plan.slots_for("S001")[0].instruction_id == "I000"
assert not plan.warnings, f"합성 계획이 동사 규칙 위반: {plan.warnings}"
# 리포 plans/ 에는 포맷 문서용 example.json 만 남는다
names = [p.name for p in list_plans()]
assert names == ["example.json"], names
bad1 = TMP / "bad1.json"
bad1.write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": "I000", "instruction": "push the cup left", "target": 5}]}]}))
w = load_plan(bad1).warnings
assert w and "동사" in w[0], w
# scene 이 다르면 같은 ID 에 다른 문장 허용 (ID 는 scene 로컬 -- 2026-08-13 결정)
ok2 = TMP / "ok2.json"
ok2.write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": "I000", "instruction": "open the top drawer", "target": 5}]},
    {"scene_id": "S001", "slots": [
        {"instruction_id": "I000", "instruction": "close the top drawer", "target": 5}]}]}))
p2 = load_plan(ok2)
assert p2.slots_for("S000")[0].instruction != p2.slots_for("S001")[0].instruction
# 같은 scene 안에서의 중복 ID 는 거부
bad2 = TMP / "bad2.json"
bad2.write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": "I000", "instruction": "open the top drawer", "target": 5},
        {"instruction_id": "I000", "instruction": "close the top drawer", "target": 5}]}]}))
try:
    load_plan(bad2)
    raise AssertionError("같은 scene 내 중복 ID 가 통과됨")
except ValueError as e:
    assert "유일" in str(e) or "서로 다른 문장" in str(e)
print("1 통과: 계획 로드, 동사 경고, scene 간 ID 재사용 허용, scene 내 중복 거부")

# ---- 2. 계획-파일 불일치 감지 (합성 에피소드 -- 실파일은 수집 중 변함) ----
eps = [
    {"name": "episode_000", "instruction_id": "I000",
     "instruction": "pick up the blue cup and place it on the blue bowl"},
    {"name": "episode_001", "instruction_id": "I000",
     "instruction": "pick up the blue cup and place it on the white bowl"},
    {"name": "episode_002", "instruction_id": "I099",
     "instruction": "open the top drawer"},
]
warns = check_scene_against_plan(plan, "S000", eps)
assert any("문장이 지시문과 다름" in w for w in warns), warns
assert any("지시문에 없는 slot I099" in w for w in warns), warns
print("2 통과: ID-문장 갈라짐 + 계획 밖 slot 감지 --", len(warns), "건")

# ---- 3. GUI: 드롭다운/카운트/다음 slot ----
import collect_workspace as cw  # noqa: E402

cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
cw.SystemOps.startup_tuning = lambda self: None   # pkexec 비밀번호 창 차단
cw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win = cw.WorkspaceWindow(None)
# 계획은 데이터셋 폴더의 instructions.json 에 귀속된다 -- 임시 데이터셋 폴더에
# 계획을 두고 저장 경로를 그쪽으로 돌린다 (드롭다운 선택은 폐지, 2026-09-04).
# scene 세션을 흉내 -- 세션 중엔 파일이 잠기므로(HDF5 잠금) 워커 cfg 의
# scene_id + saver 캐시로 계산한다. 파일은 경로로만 쓰이고 아래에서 캐시를
# 직접 주입하므로 빈 파일이면 충분하다. (예전엔 실제 scene_000.hdf5 를
# 통째로 복사했는데 -- 읽지도 않는 8.9GB 를 -- 정리 코드도 없어서 테스트
# 실행마다 /tmp 에 쌓였고, 35회 누적 233GB 로 NVMe 를 가득 채워 실수집의
# HDF5 쓰기가 ENOSPC 로 죽는 사고가 났다. 2026-08-26)

TMPD = Path(tempfile.mkdtemp(prefix="p4a_s_"))
atexit.register(shutil.rmtree, TMPD, ignore_errors=True)
scene_copy = TMPD / "scene_000.hdf5"
scene_copy.touch()
shutil.copy(pilot, TMPD / "instructions.json")
win.root_edit.setText(str(TMPD))
win.scene_ops.refresh_scene_combo()


class FakeW:
    cfg = type("C", (), {"scene_metadata": None, "scene_id": "S000",
                         "task_name": "S000"})()


win.worker = FakeW()
win.session.scene_session = True
win.session.active_file_path = scene_copy
# 세션 캐시를 합성으로 주입 (파일 잠금 상황과 동일한 경로)
win.session.active_episode_cache = [
    {"name": "episode_000", "instruction_id": "I000",
     "instruction": "pick up the blue cup and place it on the blue bowl",
     "quality_status": "success", "num_samples": 100, "success": True,
     "episode_id": 0, "episode_uid": "EP-S000-I000-E000", "collector": "t",
     "timestamp": ""},
    {"name": "episode_001", "instruction_id": "I000",
     "instruction": "pick up the blue cup and place it on the white bowl",
     "quality_status": "failed", "num_samples": 100, "success": False,
     "episode_id": 1, "episode_uid": "EP-S000-I000-E001", "collector": "t",
     "timestamp": ""},
]
# 드롭다운은 없어졌다 (2026-09-06) -- 목록이 그 자리다. 한 줄 = 한 지시문,
# 누르면 바로 전환. 세션 중에만 보이므로 여기서는 워커를 흉내 낸다.
calls = []


class FW:
    cfg = type("C", (), {"task_name": "S000", "scene_metadata": None,
                         "scene_id": "S000", "instruction_id": "I000",
                         "language_instruction":
                             "pick up the blue cup and place it on the blue bowl"})()

    def cmd_set_slot(self, i, d):
        calls.append((i, d))


win.worker = FW()
win.session.scene_session = True
win.scene_planning.refresh_instruction_list()
rows = [win.instr_tree.topLevelItem(i) for i in range(win.instr_tree.topLevelItemCount())]
# ID 칸에는 현재 표시("▸")가 붙으므로 정본은 UserRole 이다.
items = [(r.data(0, Qt.ItemDataRole.UserRole)[0], r.text(1), r.text(2))
         for r in rows]
assert any(iid == "I000" and cnt == "1/10" for iid, cnt, _ in items), \
    f"카운트 표시 실패: {items}"
assert "문장이 지시문과 다름" in win.instr_warn.text(), "패널 불일치 경고 없음"
# 한 줄을 누르면 곧바로 워커에 전달된다 (적용 버튼 없음)
win.scene_planning.on_instruction_picked(rows[0])
assert calls and calls[-1][1] == "I000", calls
assert win.right_fields["ds_task"].text().startswith("I000: ")
# 다음 미수집 (I000 1/10 -> 그대로 I000)
calls.clear()
win.scene_planning.on_next_instruction()
assert calls and calls[-1][1] == "I000", calls
print("3 통과: 계획 목록 카운트(1/10), 불일치 경고, 한 줄 클릭=즉시 전환, 다음 미수집")

# ---- 4. 계획 없음 회귀 -- instructions.json 을 지우면 파일의 지시문만 ----
(TMPD / "instructions.json").unlink()
win.scene_planning.on_plan_changed()
win.scene_planning.refresh_instruction_list()
# 계획이 없으면 파일에 이미 있는 지시문만 나온다 (새 문장은 계획을 먼저)
seen = {win.instr_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0]
        for i in range(win.instr_tree.topLevelItemCount())}
assert seen == {"I000"}, seen
calls.clear()
win.scene_planning.apply_instruction("I009", "open the top drawer")
assert calls, "계획 없이 적용 실패"
# 오른쪽 패널 Dataset '태스크' 가 적용 즉시 현재 slot 으로 바뀐다
assert win.right_fields["ds_task"].text() == "I009: open the top drawer"
# 패널 갱신 경로도 워커의 현재 slot 을 읽는다 (연결 시점 설정이 아니라)
FW._slot_instruction = "close the top drawer"
FW._slot_instruction_id = "I010"
FW.cfg.language_instruction = "stale first sentence"
FW.cfg.instruction_id = "I000"
FW.cfg.schema = type("S", (), {"action_space": "joint_absolute",
                               "gripper_action_match_obs": True, "image_size": None})()
FW.cfg.fps = 20
win.dataset_ops.update_dataset_panel()
assert win.right_fields["ds_task"].text() == "I010: close the top drawer", \
    win.right_fields["ds_task"].text()
print("4 통과: 계획 없음 자유 입력 회귀 없음 + 오른쪽 패널 태스크가 현재 slot 반영")

print("\nPhase 4a 인수 테스트 전부 통과")
# os._exit 는 atexit 을 건너뛴다 -- 임시 폴더는 여기서 직접 지운다.
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(TMPD, ignore_errors=True)
import os  # noqa: E402

os._exit(0)
