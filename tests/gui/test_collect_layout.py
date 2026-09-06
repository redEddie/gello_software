"""수집 워크플로 화면 개편 인수 (2026-09-06).

조작자가 지정한 것을 하나씩 못박는다:

1. ③ Collect 왼쪽에서 툴바·HUD 와 겹치던 것을 뺐다 (Control 버튼 7개, 상태
   글자, 데이터셋 전체 진행률 표).
2. Control 자리는 **단축키 매핑**이다. Space 는 역할이 둘이라 한 줄에 둘 다.
3. 지시문은 목록에서 **한 줄 누르면 즉시 전환** (드롭다운·ID칸·문장칸·적용
   버튼 없음).
4. 반드시 남아야 하는 것: Pose gate, 프레임 진행바, 한국어 안내문.
5. 상자 순서는 "확정적인 것 · 자주 보는 것" 부터.
6. ② Configure 에는 카메라가 없고(① 에서 끝난다), 연습 모드는 로봇 노드 바로
   밑이며, 중앙 탭은 Plan 이다.
7. 화면에 "slot" 이라는 낱말이 없다.

로봇도 카메라도 필요 없다 (offscreen).
"""
import json
import sys
import tempfile
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "apps"))

import numpy as np  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QGroupBox,
    QLabel,
    QPushButton,
)

app = QApplication.instance() or QApplication([])

import collect_workspace as cw  # noqa: E402
from apps.workspace.constants import CENTER_TABS_BY_ACTIVITY  # noqa: E402
from apps.workspace.features.collection.page import (  # noqa: E402
    KEY_MAP,
    set_live_keys,
)
from apps.workspace.shared.tabs import center_tab_key  # noqa: E402
from gello.scene.scene_format import SceneMetadata, SceneWriter  # noqa: E402

CUP, BOWL = "OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"
SENT = {"I000": "pick up the blue cup and place it on the white bowl",
        "I001": "pick up the white bowl and place it on the blue cup",
        "I002": "push the blue cup to the white bowl"}

# ------------------------------------------------------------- 데이터 한 벌
root = Path(tempfile.mkdtemp(prefix="collectui_"))
md = SceneMetadata(scene_id="S000", objects=[CUP, BOWL],
                   layout={"grid": [3, 3],
                           "placements": {CUP: {"zone": [0, 0]},
                                          BOWL: {"zone": [2, 2]}}})
w = SceneWriter(root, metadata=md)
rng = np.random.default_rng(0)
for iid, n in (("I000", 3), ("I001", 1)):
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
EPISODES = w.list_episodes()
w.close()
(root / "instructions.json").write_text(json.dumps(
    {"plan_version": 1, "scenes": [{"scene_id": "S000", "slots": [
        {"instruction_id": k, "instruction": v, "target": 3}
        for k, v in sorted(SENT.items())]}]}, ensure_ascii=False), encoding="utf-8")

cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
win = cw.WorkspaceWindow(None)
win.root_edit.setText(str(root))
win.scene_ops.refresh_scene_combo()

# ------------------------------------------------- 1. 중복되던 것이 없어졌다
gone = [n for n in ("start_btn", "match_btn", "skip_btn", "save_ok_btn",
                    "save_ng_btn", "discard_btn", "home_btn",
                    "state_label", "shortcut_hint",
                    "slot_plan_combo", "slot_iid_edit", "slot_instr_edit",
                    "slot_apply_btn", "slot_current_label")
        if hasattr(win, n)]
assert not gone, f"툴바·HUD 와 겹치던 위젯이 남아 있다: {gone}"
# 같은 동작은 툴바에 그대로 있어야 한다 -- 뺀 것이지 잃은 것이 아니다.
for key in ("record", "match", "skip", "save", "savefail", "discard", "home"):
    assert key in win.tb_actions, f"툴바에서 {key} 가 사라졌다"
print("1. 좌측 Control·상태글자 제거, 툴바에는 그대로 OK")

# 데이터셋 전체 진행률 표는 Collect 가 아니라 Plan 탭에 있다.
collect_page = win.left_stack.widget(win.left_pages["collect"])
assert win.plan_progress_tree not in collect_page.findChildren(
    type(win.plan_progress_tree)), "진행률 표가 아직 Collect 왼쪽에 있다"
assert win.plan_progress_tree in win.center_tab_widgets["plan"].findChildren(
    type(win.plan_progress_tree)), "진행률 표가 Plan 탭에 없다"
print("2. 데이터셋 전체 진행률 표는 Plan 탭으로 이동 OK")

# ------------------------------------------------------- 3. 반드시 남는 것
for name in ("gate_box", "delta_bars", "ep_progress", "now_hint"):
    assert hasattr(win, name), f"{name} 이 없다 (반드시 있어야 한다)"
assert len(win.delta_bars) == 8
assert win.now_hint.text(), "한국어 안내문이 비어 있다"
print("3. Pose gate · 프레임 진행바 · 한국어 안내문 유지 OK")

# ------------------------------------------------------------ 4. 상자 순서
boxes = [b.title() for b in collect_page.findChildren(QGroupBox)]
assert boxes == ["Instruction", "지금", "Pose gate", "Keys"], boxes
print("4. 상자 순서 OK:", " → ".join(boxes))

# --------------------------------------------------------- 5. 단축키 매핑
keys = [k for k, _w, _s in KEY_MAP]
assert keys == ["Space", "Esc", "Del", "Enter"], keys
space_what = dict((k, v) for k, v, _s in KEY_MAP)["Space"]
assert "/" in space_what, f"Space 의 두 역할이 한 줄에 없다: {space_what}"
set_live_keys(win, "recording")
live = {k for k, lab in win.key_rows.items() if "2ecc71" in lab.styleSheet()}
assert live == {"Space", "Esc", "Del"}, live
set_live_keys(win, "gate")
live = {k for k, lab in win.key_rows.items() if "2ecc71" in lab.styleSheet()}
assert live == {"Space", "Enter"}, live
set_live_keys(win, "idle")
assert not any("2ecc71" in lab.styleSheet() for lab in win.key_rows.values())
print("5. 단축키 표 + 상태별 강조 OK (Space 는 한 줄에 두 역할)")


# --------------------------------------- 6. 목록 한 줄 = 즉시 전환 (버튼 없음)
class _FakeWorker:
    cfg = type("C", (), {"task_name": "S000", "scene_metadata": None,
                         "scene_id": "S000", "instruction_id": "I001",
                         "language_instruction": SENT["I001"]})()
    _slot_instruction_id = "I001"
    _slot_instruction = SENT["I001"]

    def __init__(self) -> None:
        self.calls = []

    def cmd_set_slot(self, instr, iid):
        self.calls.append((iid, instr))


fw = _FakeWorker()
win.worker = fw
win.session.scene_session = True
win.session.active_episode_cache = EPISODES
win.collection.set_running(True)
win.collection.refresh_instruction()
win.scene_planning.refresh_instruction_list()

assert not win.instr_box.isHidden(), "scene 세션인데 Instruction 상자가 없다"
assert win.instr_counter.text() == "S000 · I001 · 1/3", win.instr_counter.text()
assert win.instr_sentence.text() == SENT["I001"], win.instr_sentence.text()
rows = [win.instr_tree.topLevelItem(i)
        for i in range(win.instr_tree.topLevelItemCount())]
assert [r.text(1) for r in rows] == ["3/3", "1/3", "0/3"], [r.text(1) for r in rows]
assert rows[1].font(0).bold(), "현재 지시문 줄이 굵지 않다"
# 클릭 한 번으로 끝난다 -- 확정 버튼이 없다.
win.scene_planning.on_instruction_picked(rows[2])
assert fw.calls == [("I002", SENT["I002"])], fw.calls
assert win.right_fields["ds_task"].text() == f"I002: {SENT['I002']}"
# Next unfilled 는 번호가 가장 낮은 미완(I001, 1/3)으로 간다.
fw.calls.clear()
win.scene_planning.on_next_instruction()
assert fw.calls == [("I001", SENT["I001"])], fw.calls
print("6. 목록 한 줄 클릭 = 즉시 전환, Next unfilled OK")

# ------------------------------------------------------------ 7. ② Configure
win.worker = None
win.session.scene_session = False
win.collection.set_running(False)
conf = win.left_stack.widget(win.left_pages["configure"])
titles = [b.title() for b in conf.findChildren(QGroupBox)]
assert "카메라" not in titles, f"② 에 카메라 그룹이 남아 있다: {titles}"
layout_page = win.left_stack.widget(win.left_pages["layout"])
assert "카메라" in [b.title() for b in layout_page.findChildren(QGroupBox)], \
    "① 에 카메라 그룹이 없다 (원본이 여기여야 한다)"
assert win.agent_combo in layout_page.findChildren(type(win.agent_combo)), \
    "카메라 콤보의 원본이 ① 이 아니다"
# 연습 모드는 로봇 노드 상자 **안**이다.
node_box = next(b for b in conf.findChildren(QGroupBox) if b.title() == "로봇 노드")
assert win.no_dataset_check in node_box.findChildren(QCheckBox), \
    "연습 모드가 로봇 노드 바로 밑이 아니다"
print("7. ② 에서 카메라 제거, 연습 모드는 로봇 노드 바로 밑 OK")

# 다음 단계 버튼은 페이지 머리줄 바로 아래 (스크롤 밖, 맨 위).
for key in ("layout", "configure", "collect"):
    wrapper = win.left_stack.widget(win.left_pages[key])
    lay = wrapper.layout()
    labels = [lay.itemAt(i).widget() for i in range(lay.count())]
    assert isinstance(labels[0], QLabel), key
    assert isinstance(labels[1], QPushButton), f"{key}: 다음 버튼이 맨 위가 아니다"
    assert labels[1].text().startswith("다음:"), labels[1].text()
print("8. '다음 단계' 버튼이 각 페이지 맨 위 OK")

# ② 의 중앙 탭은 Plan 이 먼저다.
assert CENTER_TABS_BY_ACTIVITY["configure"][0] == "plan"
win._set_activity("configure")
assert center_tab_key(win) == "plan", center_tab_key(win)
print("9. ② Configure 의 중앙 탭이 Plan OK")

# --------------------------------------------- 10. 화면에 "slot" 이 없다
bad = []
for key in win.left_pages:
    page = win.left_stack.widget(win.left_pages[key])
    for kind in (QLabel, QPushButton, QGroupBox, QCheckBox):
        for wdg in page.findChildren(kind):
            text = wdg.title() if isinstance(wdg, QGroupBox) else wdg.text()
            if "slot" in text.lower():
                bad.append(f"{key}: {text!r}")
assert not bad, ("화면에 'slot' 이라는 낱말이 남아 있다 (지시문/Instruction 으로 "
                 f"통일): {bad}")
print("10. 화면에 'slot' 없음 OK")

print("\n수집 워크플로 화면 개편 인수 통과")
