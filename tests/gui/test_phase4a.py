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
assert any("문장이 계획과 다름" in w for w in warns), warns
assert any("계획에 없는 slot I099" in w for w in warns), warns
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
win.scene_planning.refresh_slot_panel()
items = [(win.slot_plan_combo.itemText(i), win.slot_plan_combo.itemData(i))
         for i in range(win.slot_plan_combo.count())]
assert any("I000 · 1/10" in t for t, _ in items), f"카운트 표시 실패: {items}"
assert "문장이 계획과 다름" in win.slot_plan_warn.text(), "패널 불일치 경고 없음"
# 드롭다운 선택 -> 입력칸 채움
idx = next(i for i, (t, d) in enumerate(items) if d is not None)
win.slot_plan_combo.setCurrentIndex(idx)
assert win.slot_iid_edit.text() == "I000"
assert win.slot_instr_edit.text() == "pick up the blue cup and place it on the blue bowl"
# 다음 미수집 slot (I000 2/10 -> 그대로 I000)
win.slot_iid_edit.clear()
win.slot_instr_edit.clear()
win.scene_planning.on_next_slot()
assert win.slot_iid_edit.text() == "I000" and win.slot_instr_edit.text()
print("3 통과: 계획 자동선택, 카운트(2/10), 불일치 경고, 드롭다운 채움, 다음 slot 제시")

# ---- 4. 계획 없음 회귀 -- instructions.json 을 지우면 자유 입력 ----
(TMPD / "instructions.json").unlink()
win.scene_planning.on_plan_changed()
assert win.slot_plan_combo.count() == 1  # (직접 입력) 만
calls = []
class FW:
    cfg = type("C", (), {"task_name": "S000", "scene_metadata": None,
                         "scene_id": "S000"})()
    def cmd_set_slot(self, i, d):
        calls.append((i, d))
win.worker = FW()
win.slot_iid_edit.setText("I009")
win.slot_instr_edit.setText("open the top drawer")
win.scene_planning.on_apply_slot()
assert calls, "계획 없이 자유 입력 적용 실패"
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
