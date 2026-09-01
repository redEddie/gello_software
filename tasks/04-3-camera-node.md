refactor(workspace): 카메라 노드·미리보기를 CameraOps로 분리 (Phase 4-3)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

`apps/workspace/domains/camera.py` 의 `CameraOps`.
창에서는 `self.camera_ops = CameraOps(self)`.

**이름 주의**: `self.cameras` 는 이미 `CameraState`(데이터)입니다.

## 옮길 것 -- 이번에는 카메라 노드와 미리보기만

뎁스·포인트클라우드는 **다음 조각(4-4)** 입니다. 이번에는 이것만:

    _refresh_cameras  _restart_previews  _stop_previews_async
    _on_camera_node*  _on_restart_camera_node  _on_stop_camera_node_manual
    _start_camera_node  _stop_camera_node  _camera_node_*
    _on_frame*  _tick_fps  _combo_serial  _on_stream_*  _live_*

`grep -n '    def .*\(camera\|preview\|frame\|fps\|stream\|live\)' \
 apps/collect_workspace.py` 로 확정하되, 이름에 `depth`/`cloud` 가 들어간 것은
남겨 두세요.

## 주의: 프레임 경로는 뜨겁습니다

`_on_frame*` 과 `_tick_fps` 는 카메라 프레임마다 돕니다. 속성 접근 한 번이
늘어나도 보입니다 (2026-08-31 에 오차 게이지가 느려진 원인이 이 종류).
메서드 앞에서 `win = self.win`, `cams = win.cameras` 로 한 번만 받고
그 뒤로는 지역 변수를 쓰세요.

## 주의: 테스트가 이 메서드들을 스텁합니다

`test_phase4a`, `test_gate_reset`, `test_stats_group`, `test_relabel`,
`test_domain_attrs` 가 다음처럼 창을 만들기 전에 스텁합니다:

    cw.WorkspaceWindow._refresh_cameras = lambda self: None
    cw.WorkspaceWindow._restart_previews = lambda self: None

이 둘이 도메인으로 가면 그 스텁이 아무 일도 하지 않게 되어, 테스트가 진짜
카메라를 열려 들 수 있습니다. 다섯 테스트의 스텁을 새 자리에 맞게 고치세요
(예: `cw.CameraOps.refresh_cameras = lambda self: None` -- 도메인을
`collect_workspace` 가 임포트하므로 `cw.` 로 닿습니다).
**스텁을 지우지 마세요.** 지우면 기계 없는 자리에서 테스트가 멈춥니다.
