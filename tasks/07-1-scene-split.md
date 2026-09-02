refactor(workspace): SceneOps 845줄을 셋으로 나눈다 (7-1)

`tasks/_공통.md` 를 먼저 읽으세요. 이번엔 폴더를 옮기지 않습니다 --
`apps/workspace/domains/scene.py` 를 같은 자리에서 셋으로 가르기만 합니다.

## 왜 셋인가 (측정한 근거)

    layout_ref  16개 / 161줄   그룹 밖 형제 호출 0회   <- 완전한 잎사귀
    planning    19개 / 396줄   ops 를 9회 부름
    ops         12개 / 199줄   planning 을 5회 부름

`layout_ref` 는 아무도 되부르지 않으니 그냥 떼면 됩니다.
`planning` 과 `ops` 는 서로 부르므로, 나눈 뒤에는 이 저장소가 이미 쓰는
방식으로 서로를 부릅니다: `self.win.scene_ops.session_scene_id()`
(`domains/collection.py` 가 `self.win.stats_ops.bump(...)` 를 부르는 것과 같습니다.)

## 만들 것

`apps/workspace/domains/scene.py` 를 지우고 패키지로 바꿉니다:

    apps/workspace/domains/scene/__init__.py     세 클래스 재수출 + __all__
    apps/workspace/domains/scene/ops.py          SceneOps
    apps/workspace/domains/scene/planning.py     ScenePlanningOps
    apps/workspace/domains/scene/layout_ref.py   LayoutRefOps

창에서는:

    self.scene_ops = SceneOps(self)
    self.scene_planning = ScenePlanningOps(self)
    self.layout_ref = LayoutRefOps(self)

## 어느 메서드가 어디로

    layout_ref.py  layout_update_role  layout_reload  ensure_layout_refs
                   layout_show  layout_blink_toggled  layout_toggle_play
                   layout_step  layout_refilter  layout_blink_tick
                   layout_alpha_changed  layout_rerender
                   layout_blink_interval_changed  layout_apply_interval
                   on_grid_alpha  on_grid_alpha_done  on_edit_grid

    planning.py    refresh_plan_progress  refresh_start_plan_combo
                   on_apply_slot  refresh_slot_panel  auto_assign_iid
                   known_slots  on_new_plan  on_next_slot  on_delete_plan
                   session_slot_counts  on_edit_plan  refresh_plan_combo
                   current_plan  on_rank_selected  on_plan_selected
                   on_start_plan_pick  on_slot_sentence_edited
                   on_slot_plan_pick  next_iid

    ops.py         나머지 전부 (on_scene_selected, scene_config_from_ui,
                   apply_session_config, refresh_scene_combo, on_new_scene,
                   configure_scene_id, set_right_scene, session_scene_id,
                   selected_scene_path, scene_session_file,
                   on_start_sentence_edited)

**실제 파일의 메서드 목록과 위가 어긋나면 코드를 믿고 보고에 적으세요.**

## 호출부 고치기

`win.scene_ops.<이름>` 을 쓰는 곳이 빌더·페이지·다른 도메인에 흩어져 있습니다.
옮긴 메서드는 새 객체 이름으로 바꿔야 합니다:

    grep -rn 'scene_ops\.' apps/ tests/ --include='*.py'

`_공통.md` 대로 옮긴 뒤 잔여 참조를 세세요.

## 주의

- 상태는 전부 창에 있습니다 (`self.win.cameras.grid_store` 등). 세 클래스
  어디에도 새 상태를 만들지 마세요 -- 지금 세 그룹의 전용 상태는 0개입니다.
- `_공통.md` 의 "테스트를 통과시키려고 구조를 바꾸지 말 것" 을 지키세요.
- 클래스 이름이 길어 보여도 `XOps` 규칙을 지키세요. 도메인 10개가 전부
  그 모양입니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 전부 통과. `test_domain_attrs` 가 `self.win.<이름>` 실재를 봅니다.

## 보고

세 파일의 줄 수, 서로 부르는 자리가 몇 곳이 됐는지, 고친 호출부 파일 수.
