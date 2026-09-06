"""빠른 재개(⚡ Quick resume)의 고르기 규칙 + homing 속도 상한 (2026-09-06).

조작자가 준 규칙 그대로 못박는다: **scene 은 번호가 가장 높은 것, task 는
번호가 낮은 순** (아직 목표를 못 채운 것 중에서).

homing 쪽은 같은 날 나온 다른 보고다 -- "가끔 너무 빠르게 움직여 반사가
난다". EE 경로 homing 이 tick 당 관절 이동을 묶지 않고 있었다.

로봇도 카메라도 필요 없다 (offscreen). scene 파일은 SceneWriter 로 만든다.
"""
import json
import sys
import tempfile
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit  # noqa: E402

app = QApplication.instance() or QApplication([])

from apps.workspace.features.scene.planning import ScenePlanningOps  # noqa: E402
from gello.scene.scene_format import SceneMetadata, SceneWriter  # noqa: E402

CUP, BOWL = "OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"
SENT = {"I000": "pick up the blue cup and place it on the white bowl",
        "I001": "pick up the white bowl and place it on the blue cup",
        "I002": "push the blue cup to the white bowl"}


def _write_scene(root: Path, sid: str, per_slot: dict) -> None:
    """scene 파일 하나 -- {instruction_id: 성공 에피소드 수}. 이미 있으면 이어 쓴다."""
    md = SceneMetadata(scene_id=sid, objects=[CUP, BOWL],
                       layout={"grid": [3, 3],
                               "placements": {CUP: {"zone": [0, 0]},
                                              BOWL: {"zone": [2, 2]}}})
    exists = (root / f"scene_{int(sid[1:]):03d}.hdf5").exists()
    # resume 이면 metadata 는 파일이 정본이다 (둘 다 주면 SceneWriter 가 막는다).
    w = (SceneWriter(root, scene_id=sid, resume=True) if exists
         else SceneWriter(root, metadata=md))
    rng = np.random.default_rng(0)
    for iid, n in sorted(per_slot.items()):
        for _ in range(n):
            w.start_episode()
            q = np.zeros(7, np.float32)
            for _ in range(6):
                q = q + 0.01
                w.add_frame(
                    agentview_rgb=rng.integers(0, 255, (16, 16, 3), dtype=np.uint8),
                    eye_in_hand_rgb=rng.integers(0, 255, (16, 16, 3), dtype=np.uint8),
                    joint_positions=q, gripper_position=0.0, ee_pos_quat=np.zeros(7),
                    gripper_closed=False, commanded_joint_positions=q,
                    commanded_gripper=0.0)
            w.save_buffer(w.detach_buffer(), instruction=SENT[iid],
                          instruction_id=iid, success=True, collector="t")
    w.close()


def _plan(root: Path, targets: dict) -> None:
    """{scene_id: {iid: target}} -> instructions.json"""
    doc = {"plan_version": 1, "scenes": [
        {"scene_id": sid,
         "slots": [{"instruction_id": iid, "instruction": SENT[iid], "target": t}
                   for iid, t in sorted(slots.items())]}
        for sid, slots in sorted(targets.items())]}
    (root / "instructions.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


class _Stub:
    """pick_resume_slot 이 창에서 쓰는 것은 저장 경로와 로그뿐이다."""

    def __init__(self, root: Path) -> None:
        self.root_edit = QLineEdit(str(root))
        self.lines: list = []

    def log(self, msg) -> None:
        self.lines.append(str(msg))


def _ops(root: Path) -> ScenePlanningOps:
    return ScenePlanningOps(_Stub(root))


# ------------------------------------------------- 1. scene 은 번호가 높은 것
root = Path(tempfile.mkdtemp(prefix="quickresume_"))
_write_scene(root, "S000", {"I000": 2})
_write_scene(root, "S001", {"I000": 2})
_write_scene(root, "S002", {"I000": 3, "I001": 1})
_plan(root, {"S000": {"I000": 5},
             "S001": {"I000": 5},
             # S002 는 I000 이 목표를 채웠고 I001 이 모자란다
             "S002": {"I000": 3, "I001": 5, "I002": 5}})

sid, iid, instr, note = _ops(root).pick_resume_slot()
assert sid == "S002", f"가장 번호 높은 scene 이 아니다: {sid}"
assert iid == "I001", f"미완 slot 중 가장 낮은 번호가 아니다: {iid} ({note})"
assert instr == SENT["I001"], instr
print(f"1. 가장 최근 scene · 가장 낮은 미완 slot OK: {sid} · {note}")

# I001 을 채우면 다음으로 낮은 미완(I002)이 나와야 한다 -- "낮은 순"이지
# "항상 I000"이 아니다.
_write_scene(root, "S002", {"I001": 5})   # append (같은 scene_id = 같은 파일)
sid, iid, _instr, note = _ops(root).pick_resume_slot()
assert (sid, iid) == ("S002", "I002"), (sid, iid, note)
print(f"2. 채운 slot 은 건너뛰고 다음으로 낮은 것 OK: {iid}")

# 전부 채우면 막지 않고 가장 낮은 ID 로 이어 찍는다 (판단이 필요한 자리).
_write_scene(root, "S002", {"I002": 5})
sid, iid, _instr, note = _ops(root).pick_resume_slot()
assert (sid, iid) == ("S002", "I000"), (sid, iid)
assert "모두 목표" in note, note
print(f"3. 전부 채운 scene 은 막지 않고 알린다 OK: {note}")

# ------------------------------------------------------- 2. 계획 없는 데이터셋
root2 = Path(tempfile.mkdtemp(prefix="quicknoplan_"))
_write_scene(root2, "S000", {"I000": 1})
_write_scene(root2, "S001", {"I002": 1, "I001": 1})
sid, iid, instr, note = _ops(root2).pick_resume_slot()
assert (sid, iid) == ("S001", "I001"), (sid, iid, note)
assert instr == SENT["I001"]
print(f"4. 계획 없으면 파일의 slot 중 가장 낮은 것 OK: {sid} · {iid}")

# ------------------------------------------------- 3. 고를 수 없으면 멈추고 말한다
empty = Path(tempfile.mkdtemp(prefix="quickempty_"))
sid, iid, instr, note = _ops(empty).pick_resume_slot()
assert sid is None and iid is None, (sid, iid)
assert "새 Scene" in note, note
print("5. 첫 scene 은 사람이 정한다 (추측하지 않는다) OK")

# 번호 순서가 문자열이 아니라 숫자여야 한다 (I009 < I010)
order = ScenePlanningOps._iid_order
assert order("I009") < order("I010") < order("I100")
assert order("I000") < order("I009")
assert order("nope") > order("I999"), "형식이 아닌 ID 는 맨 뒤"
print("6. slot 번호를 숫자로 센다 OK (I009 < I010)")


# ------------------------------------------------------- 4. homing 속도 상한
from gello.collect.worker import CollectionWorker, HOME_TICK_DQ  # noqa: E402
from gello.robots import fr3_kinematics as K  # noqa: E402
from gello.robots.franka_fr3 import FR3_RESET_POSES  # noqa: E402

reset_q = np.array(FR3_RESET_POSES["libero"][:7], dtype=float)
w = CollectionWorker.__new__(CollectionWorker)
w._reset_q = reset_q

# _densify 자체: 모양은 지키고 시간만 늘린다.
start = np.zeros(7)
wps = [np.full(7, 0.5), np.full(7, 0.5) + np.array([0.3, 0, 0, 0, 0, 0, 0])]
dense = CollectionWorker._densify(start, wps, max_dq=0.06)
steps = np.abs(np.diff(np.vstack([start] + dense), axis=0)).max(axis=1)
assert steps.max() <= 0.06 + 1e-12, steps.max()
assert np.allclose(dense[-1], wps[-1]), "끝점이 바뀌면 홈에 도착하지 않는다"
for q in wps:
    assert any(np.allclose(d, q) for d in dense), "원래 웨이포인트가 경로에 남아야 한다"
print("7. _densify 는 끝점과 경유점을 지키고 tick 당 이동만 자른다 OK")

# 실제 홈 경로: 무작위 시작 자세에서도 tick 당 상한을 넘지 않는다.
rng = np.random.default_rng(0)
worst, made = 0.0, 0
for _ in range(60):
    q0 = np.clip(reset_q + rng.uniform(-0.9, 0.9, 7),
                 K.FR3_Q_MIN + 0.1, K.FR3_Q_MAX - 0.1)
    path = w._home_trajectory(q0)
    if path is None:
        continue
    made += 1
    worst = max(worst, np.abs(np.diff(np.vstack([q0] + list(path)),
                                      axis=0)).max())
assert made > 40, f"경로를 거의 못 만들었다 ({made}/60) -- 다른 것이 깨졌다"
assert worst <= HOME_TICK_DQ + 1e-9, (
    f"homing 이 tick 당 {worst:.3f} rad ({worst * 20:.1f} rad/s) 를 명령한다 -- "
    f"드라이버 상한은 1.5 rad/s 다")
print(f"8. homing tick 당 관절 이동 {worst:.4f} rad ({worst * 20:.2f} rad/s) "
      f"<= {HOME_TICK_DQ} OK ({made}/60 경로)")

print("\n빠른 재개 + homing 속도 인수 통과")
