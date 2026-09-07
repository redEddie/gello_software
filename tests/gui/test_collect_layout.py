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
   밑이며, 중앙 탭은 Instruction 이다.
7. 화면에 "slot" 이라는 낱말이 없다.
8. "다음 단계" 버튼은 없다 -- 활동 바를 누른다.
9. 수집자는 태그로 고른다 (max N, 부분 일치, 클릭하면 입력이 지워진다).
10. Instruction 탭의 줄을 누르면 scene 과 지시문이 함께 정해진다.
11. 새 Scene 은 대화상자가 아니라 Scene 탭이다.
12. 데이터 저장 경로 칸은 화면에 하나뿐이다.

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
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
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
from gello.scene.scene_format import (  # noqa: E402
    SceneMetadata,
    SceneWriter,
    list_scene_episodes,
)

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
assert win.plan_progress_tree in win.center_tab_widgets["instruction"].findChildren(
    type(win.plan_progress_tree)), "진행률 표가 Instruction 탭에 없다"
print("2. 데이터셋 전체 진행률 표는 Instruction 탭으로 이동 OK")

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

# ② 의 중앙 탭은 Instruction 이 먼저다.
assert CENTER_TABS_BY_ACTIVITY["configure"][0] == "instruction"
win._set_activity("configure")
assert center_tab_key(win) == "instruction", center_tab_key(win)
print("9. ② Configure 의 중앙 탭이 Instruction OK")

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


# ------------------------------------------------ 11. "다음 단계" 버튼은 없다
nxt = [b.text() for key in win.left_pages
       for b in win.left_stack.widget(win.left_pages[key]).findChildren(QPushButton)
       if b.text().startswith("다음:")]
assert not nxt, f"'다음 단계' 버튼이 남아 있다: {nxt} (활동 바를 누르면 된다)"
print("11. '다음 단계' 버튼 없음 OK")

# ------------------------------------------------------- 12. 수집자 태그 칸
from gello.gui.widgets.recents import COLLECTOR_MAX  # noqa: E402

picker = win.collector_edit
for name in ("gibeom", "jeonchanwook"):
    win._recents.add("collector", name)
picker.refresh()


def _tags():
    return [b.text() for b in picker.findChildren(QPushButton)]


assert set(_tags()) == {"gibeom", "jeonchanwook"}, _tags()
# 부분 일치 -- 성을 뺀 이름을 쳐도 걸린다
picker._on_typed("chanwook")
assert _tags() == ["jeonchanwook"], _tags()
# 태그를 누르면 확정되고 치던 글자는 사라진다
picker._pick("jeonchanwook")
assert picker.text() == "jeonchanwook", picker.text()
# 상한은 저장하는 파일의 상수 하나가 정한다
for i in range(COLLECTOR_MAX + 3):
    win._recents.add("collector", f"person{i:02d}")
assert len(win._recents.get("collector")) == COLLECTOR_MAX, \
    f"{COLLECTOR_MAX}명까지만 기억해야 한다: {win._recents.get('collector')}"
assert "gibeom" not in win._recents.get("collector"), "오래된 이름이 안 밀렸다"
caps = [lab.text() for lab in picker.findChildren(QLabel)
        if lab.text().startswith("max")]
assert caps == [f"max {COLLECTOR_MAX}"], caps
# 함께 뜨는 뱃지는 색이 겹치지 않는다 (같은 사람은 늘 같은 색이 우선).
from apps.workspace.shared.collector_picker import _tag_colors  # noqa: E402

roster = win._recents.get("collector")
colors = _tag_colors(roster)
assert len({c[0] for c in colors.values()}) == len(roster), \
    f"뱃지 색이 겹친다: {colors}"
assert _tag_colors(roster) == colors, "같은 명단인데 색이 달라졌다 (결정론적이어야)"
print(f"12. 수집자 태그 OK (부분 일치 · 클릭 확정 · max {COLLECTOR_MAX} · 색 {len(roster)}종)")

# ------------- 12b. 지시문은 필수다 + 안전 토글은 [리더암] 상자에 (2026-09-07)
#
# 9/6 에는 이 둘이 툴바에 있어야 했다 ("평소엔 켜 두고 쓰니 설정 화면에서
# 매번 읽을 줄이 아니다"). 9/7 에 뒤집혔다: 리더암에 관한 설정(Grip·Joint
# wall·자세 정렬)이 "수집 설정" 과 툴바에 흩어져 있어서, 상자를 "무엇에
# 대한 설정인가" 로 가르기로 했다. 그래서 셋 다 [리더암] 상자에 있다.
assert win.wall_check not in win.tool_bar.actions(), "Joint wall 이 아직 툴바에 있다"
assert win.match_check not in win.tool_bar.actions(), "자세 정렬이 아직 툴바에 있다"
for key in ("wall", "match_pose"):
    assert key not in win.tb_actions, f"툴바 색인에 {key} 가 남았다"
# "match" 는 남는다 -- 툴바의 ⇔ Auto-align **동작**이고 토글이 아니다.
assert "match" in win.tb_actions, "Auto-align 동작까지 지웠다"
leader = win.leader_box
assert win.wall_check in leader.findChildren(QCheckBox), "Joint wall 이 리더암에 없다"
assert win.match_check in leader.findChildren(QCheckBox), "자세 정렬이 리더암에 없다"
assert win.grip_combo in leader.findChildren(QComboBox), "Grip 이 리더암에 없다"
assert win.grip_combo not in win.session_box.findChildren(QComboBox), \
    "Grip 이 아직 [수집 설정] 에도 있다"
# 둘 다 기본은 켜짐이고, 값을 읽는 곳 넷이 그대로 돌아야 한다 (QCheckBox 도
# QAction 과 같은 메서드를 갖는다 -- 그래서 속성 이름을 안 바꿨다).
assert win.wall_check.isChecked() and win.match_check.isChecked()
# 세션 중에는 수집 설정과 똑같이 감춘다
win.collection.set_running(True)
assert not leader.isVisibleTo(conf), "세션 중에 리더암 상자가 남아 있다"
win.collection.set_running(False)
# 계획을 치우면 연결이 막힌다
plan_file = root / "instructions.json"
saved = plan_file.read_text(encoding="utf-8")
plan_file.unlink()
try:
    # 나머지 조건은 다 갖춰 두고 **계획만** 없앤다 -- 그래야 계획이 막은
    # 것인지 다른 빈칸이 막은 것인지 헷갈리지 않는다.
    win.collector_edit.setText("tester")
    win.scene_iid_edit.setText("I000")
    win.lang_edit.setText(SENT["I000"])
    _, _, _, err = win.scene_ops.scene_config_from_ui()
    assert err and "지시문이 없습니다" in err, err
finally:
    plan_file.write_text(saved, encoding="utf-8")
# 손으로 시작 지시문을 칠 수 없다
assert win.lang_edit.isReadOnly() and win.scene_iid_edit.isReadOnly()
# 동작은 우측 패널에 모은다 (2026-09-07) -- 가운데 탭에는 남지 않는다.
# 정본은 layout.build_right 의 주석.
plan_btns = [b.text() for b in win.center_tab_widgets["instruction"].findChildren(QPushButton)]
assert plan_btns == [], f"Instruction 탭에 아직 동작 버튼이 있다: {plan_btns}"
scene_btns = [b.text() for b in win.center_tab_widgets["scene"].findChildren(QPushButton)]
assert all(t == "" for t in scene_btns), \
    f"Scene 탭에 격자 칸(라벨 없음) 말고 다른 버튼이 있다: {scene_btns}"
right_conf = win.right_stack.widget(win.right_pages["configure"])
assert right_conf is not win.right_stack.widget(win.right_pages["collect"]), \
    "Configure 가 아직 세션 정보 페이지를 함께 쓴다"
right_btns = [b.text() for b in right_conf.findChildren(QPushButton)]
for want in ("Recommend scene...", "Recommend layout...", "지시문 편집...",
             "전체 해제", "현황 새로고침"):
    assert any(want in t for t in right_btns), \
        f"[{want}] 가 우측 패널에 없다: {right_btns}"
assert any("만들기" in t for t in right_btns), right_btns
# 버튼의 **종류**가 무게로 갈린다 (2026-09-07 조작자: "버튼들의 종류가 섞였다").
# 상자마다 색이 있는 커밋 버튼은 하나뿐이고, 조회는 동작 버튼의 무게를
# 갖지 않는다. 정본은 features/scene/right_panel.py 머리말.
# 색은 커밋 버튼만 가진다. (지금은 구성이 비어 있어 그 하나도 색이 빠져
# 있다 -- "지금은 아니다" 를 색으로도 말한다. 색이 켜지는 것은 14 에서 본다.)
colored = [b.text() for b in right_conf.findChildren(QPushButton)
           if "background-color" in b.styleSheet()]
assert not [t for t in colored if "만들기" not in t], \
    f"커밋이 아닌 버튼에 색이 있다: {colored}"
peek = [b for b in right_conf.findChildren(QPushButton)
        if "현황 새로고침" in b.text()]
assert peek and "border:none" in peek[0].styleSheet(), \
    "조회 버튼이 동작 버튼과 같은 무게다"
# 구분선이 커밋과 도우미를 가른다
from PyQt6.QtWidgets import QFrame  # noqa: E402

rules = [f for f in right_conf.findChildren(QFrame)
         if f.frameShape() == QFrame.Shape.HLine]
assert len(rules) >= 2, f"커밋/도우미를 가르는 구분선이 없다 ({len(rules)})"
print("12b. 계획 필수 + 안전 토글 툴바 이동 + 우측 패널에 종류별로 OK")

# ------------------------------- 13. Instruction 탭 줄 = scene + 지시문 설정
win._set_activity("configure")
win.scene_planning.refresh_plan_progress()
tree = win.plan_progress_tree
top = tree.topLevelItem(0)
assert top is not None and top.childCount() >= 2, "계획 표가 비었다"
win.scene_planning.on_plan_row_picked(top.child(1))     # I001
assert win.scene_combo.currentData() == "S000", win.scene_combo.currentData()
assert win.scene_iid_edit.text() == "I001", win.scene_iid_edit.text()
assert win.lang_edit.text() == SENT["I001"], win.lang_edit.text()
# 머리줄(scene)은 고를 것이 없다 -- 아무 일도 일어나지 않는다
win.scene_iid_edit.setText("I000")
win.scene_planning.on_plan_row_picked(top)
assert win.scene_iid_edit.text() == "I000"
# 수집 중에는 시작 설정을 바꾸지 않는다
win.worker = fw
win.scene_planning.on_plan_row_picked(top.child(1))
assert win.scene_iid_edit.text() == "I000", "세션 중에 시작 설정이 바뀌었다"
win.worker = None
print("13. Instruction 탭 줄 클릭 = scene + 지시문 OK (세션 중엔 잠김)")

# ---------------------- 14. 새 Scene = 탭 + **누르는 즉시 파일** (여러 개)
import importlib.util  # noqa: E402

assert importlib.util.find_spec(
    "apps.workspace.features.scene.dialogs.new_scene_dialog") is None, \
    "새 Scene 대화상자가 아직 있다 (탭으로 옮겼다)"
assert not hasattr(win, "_pending_scene_meta"), \
    "'대기 구성' 이 아직 있다 -- 만들면 곧바로 파일이어야 한다"
win.scene_ops.on_new_scene()
assert center_tab_key(win) == "scene", center_tab_key(win)
assert "S001" in win.scene_composer.title_label.text(), \
    win.scene_composer.title_label.text()


def _compose(objs, zones):
    comp = win.scene_composer
    for i in range(comp.prop_list.count()):
        it = comp.prop_list.item(i)
        it.setCheckState(Qt.CheckState.Checked
                         if it.data(Qt.ItemDataRole.UserRole) in objs
                         else Qt.CheckState.Unchecked)
    comp._placements = dict(zones)
    comp._refresh()
    assert win.scene_clear_btn.isEnabled(), "체크가 있는데 전체 해제가 죽어 있다"
    assert "scene_" in win.scene_save_state.text(), win.scene_save_state.text()
    assert "scene_" in win.scene_create_btn.toolTip(), win.scene_create_btn.toolTip()
    assert win.scene_create_btn.isEnabled(), \
        f"유효한 구성인데 만들기가 죽어 있다: {win.scene_create_btn.toolTip()}"
    assert "background-color" in win.scene_create_btn.styleSheet(), \
        "만들 수 있는데 커밋 버튼에 색이 없다"
    win.scene_ops.on_compose_done()


# 아무것도 안 골랐으면 [만들기] 는 못 누르고, **왜 못 누르는지**를 말한다
# (누르게 해 놓고 대화상자로 거절하지 않는다 -- 2026-09-07).
for i in range(win.scene_composer.prop_list.count()):
    win.scene_composer.prop_list.item(i).setCheckState(Qt.CheckState.Unchecked)
win.scene_composer._placements = {}
win.scene_composer._refresh()
assert not win.scene_create_btn.isEnabled(), "빈 구성인데 만들기가 눌린다"
assert win.scene_create_btn.toolTip().strip(), "왜 못 누르는지 말하지 않는다"
# 라벨에 scene 번호가 없다 -- 번호는 고르는 것이 아니라 자동으로 붙는다
# (2026-09-07 조작자 지적).
assert "S001" not in win.scene_create_btn.text(), win.scene_create_btn.text()
# 저장 여부는 늘 한 줄로 말한다 (이 탭에 [저장] 은 따로 없다)
assert "아직" in win.scene_save_state.text(), win.scene_save_state.text()
assert not win.scene_clear_btn.isEnabled(), "체크가 없는데 전체 해제가 눌린다"

RED = "OBJ-CUP-RED-01"
_compose([CUP, BOWL], {CUP: [0, 1], BOWL: [2, 0]})
assert (root / "scene_001.hdf5").exists(), "만들었는데 파일이 없다"
assert len(list_scene_episodes(root / "scene_001.hdf5")) == 0, "빈 scene 이어야 한다"
assert win.scene_combo.currentData() == "S001", win.scene_combo.currentData()
# 연달아 또 하나 -- 미리 여러 개를 짜 두는 것이 이 화면의 용도다
assert "S002" in win.scene_composer.title_label.text(), \
    win.scene_composer.title_label.text()
_compose([CUP, BOWL, RED], {CUP: [0, 2], BOWL: [2, 1], RED: [1, 1]})
made = sorted(p.name for p in root.glob("scene_*.hdf5"))
assert made == ["scene_000.hdf5", "scene_001.hdf5", "scene_002.hdf5"], made
# 만든 scene 은 **계획에도 함께** 들어간다 -- 입구가 하나여야 한다.
plan_now = json.loads((root / "instructions.json").read_text(encoding="utf-8"))
in_plan = {sc["scene_id"] for sc in plan_now["scenes"]}
assert {"S000", "S001", "S002"} <= in_plan, in_plan
assert [sc for sc in plan_now["scenes"] if sc["scene_id"] == "S001"][0]["slots"] == [], \
    "새 scene 의 지시문은 비어 있어야 한다 (무엇을 시킬지는 사람이 정한다)"
# 그리고 지시문이 없다는 것이 연결 거부 문구로 정확히 나온다
win.scene_combo.setCurrentIndex(
    [i for i in range(win.scene_combo.count())
     if win.scene_combo.itemData(i) == "S001"][0])
_, _, _, err = win.scene_ops.scene_config_from_ui()
assert err and "지시문이 없습니다" in err, err
# 만든 뒤에도 체크는 남는다 (한 소품만 바꿔 변종을 짜는 길). 처음부터 다시
# 짤 때 15개를 하나씩 끄지 않도록 [전체 해제] 가 있다 (2026-09-07 조작자).
assert win.scene_composer.checked_count() == 3, "만든 뒤 체크가 사라졌다"
win.scene_clear_btn.click()
assert win.scene_composer.checked_count() == 0, "전체 해제가 안 지운다"
assert win.scene_composer._placements == {}, "배치가 남았다"
assert not win.scene_create_btn.isEnabled(), "다 지웠는데 만들기가 켜져 있다"
assert not win.scene_clear_btn.isEnabled(), "다 지웠는데 전체 해제가 켜져 있다"
print("14. Scene 탭 = 파일 + 계획 항목, 여러 개 + 전체 해제 OK:", made)

# -------------------------------------------- 15. 데이터 저장 경로는 하나다
assert not hasattr(win, "dataset_root_edit"), "경로 칸이 아직 둘이다"
conf_edits = [e for e in conf.findChildren(type(win.root_edit))]
assert win.root_edit not in conf_edits, "② 에 저장 경로 칸이 남아 있다"
ds_page = win.left_stack.widget(win.left_pages["dataset"])
assert win.root_edit in ds_page.findChildren(type(win.root_edit)), \
    "저장 경로 칸이 Dataset 페이지에 없다"
print("15. 데이터 저장 경로 칸은 화면에 하나 OK")

print("\n수집 워크플로 화면 개편 인수 통과")
