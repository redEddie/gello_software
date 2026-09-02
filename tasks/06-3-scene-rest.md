refactor(workspace): 레이아웃 참조·계획 진척을 SceneOps로 마저 옮긴다 (6-3)

`tasks/_공통.md` 를 먼저 읽으세요.

## 옮길 것 (약 18개, 248줄) -- 새 도메인이 아니라 기존 SceneOps 로

    _refresh_plan_progress(58)  _layout_update_role(31)  _layout_reload(28)
    _ensure_layout_refs(26)  _layout_show(20)  그 외 _layout_* 전부

`grep -n '    def .*\(layout\|plan\|scene\|slot\|prop\)' apps/collect_workspace.py`
로 확정하세요. **`_build_layout` 계열은 창 골격이라 건드리지 마세요** --
`apps/workspace/builders/layout.py` 는 center/left/right/bottom 을 만드는
파일이고 LIBERO 레이아웃과 무관합니다. 이름이 겹칠 뿐입니다.

## 주의

- `_ensure_layout_refs` 는 `assets/libero_init_layouts.zip` 을 풉니다.
  `LAYOUT_ZIP` / `LAYOUT_DIR` 상수가 `collect_workspace.py` 에 있으면
  `apps/workspace/constants.py` 로 옮기고 양쪽이 임포트하게 하세요
  (도메인은 collect_workspace 를 임포트할 수 없습니다).
- `assets/README.md` 가 이 동작을 설명합니다. 동작을 바꾸면 문서와
  어긋나므로 자리만 옮기세요.
- 상태는 `self.win.cameras.layout_ref`, `self.win.cameras.grid_store` 입니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

`test_ui_surface` 가 스크립트 경로 상수를 `collect_workspace.py` 와
`apps/workspace/constants.py` 양쪽에서 찾습니다. 상수를 옮겨도 값이 같으면
통과합니다 -- **기준선 JSON 값을 바꾸지 마세요.**
