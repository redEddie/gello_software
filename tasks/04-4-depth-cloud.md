refactor(workspace): 뎁스·포인트클라우드를 DepthOps로 분리 (Phase 4-4)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

`apps/workspace/domains/depth.py` 의 `DepthOps`.
창에서는 `self.depth_ops = DepthOps(self)`.

## 옮길 것

    _on_depth*  _depth_*  _render_cloud  _on_cloud*  _cloud_*
    _depth_role_combo  _on_metric_help (뎁스 눈금 설명이면)

`grep -n '    def .*\(depth\|cloud\)' apps/collect_workspace.py` 로 확정하세요.
4-3 에서 나간 카메라 노드·미리보기 메서드는 건드리지 마세요.

## 주의

- 상태는 `self.win.cameras.*` 입니다 (`depth_img`, `cloud_pts`, `cloud_worker` 등).
- `cloud_pitch` / `cloud_yaw` 는 **QSlider 위젯이고 창에 있습니다**
  (`self.win.cloud_pitch`). 모델에 넣지 마세요 -- 한 번 잘못 들어갔다가
  되돌린 적이 있습니다.
- `builders/cloud_tab.py`, `builders/depth_tab.py` 가 `win._render_cloud` 등을
  연결합니다. 전부 `win.depth_ops.*` 로 고치세요.
- `test_depth17`, `test_diversity_cloud` 가 이 영역을 직접 만집니다
  (`win._depth_img`, `win._cloud_worker` 는 이미 `win.cameras.*` 로 바뀌었고,
  `win._render_cloud()` 같은 호출은 이번에 바뀝니다). 테스트의 assert 를
  지우지 말고 호출 경로만 고치세요.
