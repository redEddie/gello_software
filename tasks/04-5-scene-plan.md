refactor(workspace): 씬·계획·그리드를 SceneOps로 분리 (Phase 4-5)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

`apps/workspace/domains/scene.py` 의 `SceneOps`.
창에서는 `self.scene_ops = SceneOps(self)`.

## 옮길 것

씬 메타데이터, 수집 계획, 슬롯, 추천, 그리드·레이아웃 참조 이미지:

    _on_new_scene  _on_scene*  _scene_*  _pending_scene_*
    _on_plan*  _plan_*  _apply_session_config  _known_slots
    _slot_*  _on_slot*  _next_iid  _auto_assign_iid
    _on_recommend*  _on_rank_selected
    _on_grid*  _grid_*  _layout_*  _on_layout*  _ensure_layout_refs

약 39개로 이번 조각 중 가장 큽니다. **먼저 grep 으로 목록을 확정하고,
개수가 40개를 넘으면 그리드·레이아웃(`_grid_*`, `_layout_*`)만 남기고
나머지를 먼저 옮긴 뒤 보고에 "그리드·레이아웃은 남겼다"고 적으세요.**
한 번에 다 옮기다 실패하는 것보다 절반이 확실한 편이 낫습니다.

## 주의

- 상태는 `self.win.cameras.grid_store`, `self.win.cameras.layout_ref`,
  `self.win.session.scene_session` 등에 이미 있습니다.
- `pages/configure.py`, `pages/layout.py`, `builders/layout_tab.py` 가
  이 메서드들을 연결합니다.
- `_ensure_layout_refs` 는 `assets/libero_init_layouts.zip` 을 풉니다
  (`assets/README.md` 참고). 경로 상수 `LAYOUT_ZIP`/`LAYOUT_DIR` 은
  `collect_workspace.py` 에 있으므로, 도메인이 쓰려면
  `apps/workspace/constants.py` 로 옮기고 양쪽이 임포트하게 하세요.
- `test_scene_edit`, `test_grid_replay`, `test_recommend_register`,
  `test_plan_form`, `test_right_scene` 가 이 영역입니다.
