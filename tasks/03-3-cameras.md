refactor(workspace): 카메라·뎁스·클라우드 상태를 CameraState로 분리 (Phase 3-3)

## 먼저 읽을 것

`apps/workspace/models.py` 의 `ProcessRegistry`(3-1)와 `PlaybackState`(3-2).
같은 모양으로 만드세요.

## 옮길 것 (이것만, 정확히)

    _camera_node_spec  _camera_node_user_stopped  _camera_node_crashes
    _last_cam_frame  _stream_states  _live_maximized
    _fps_count  _fps_value  _fps_timer
    _depth_consumer  _depth_img  _depth_cursor
    _cloud_worker  _cloud_pts  _cloud_rgb  _cloud_serial
    cloud_pitch  cloud_yaw
    _crop_params  _grid_store  _layout_ref

`camera_node_process` 는 3-1 에서 이미 `procs` 로 갔습니다. 다시 옮기지 마세요.
`crop_agent_x / crop_agent_y / crop_agent_zoom / crop_wrist_*` 는 Qt 위젯
(슬라이더·스핀박스)입니다. 옮기지 마세요 -- `_crop_params` 만 데이터입니다.
`cloud_status`, `depth_status` 도 위젯입니다.

## 방법

1. `apps/workspace/models.py` 에 `CameraState` 를 추가합니다.
2. `WorkspaceWindow.__init__` 에서 `self.cameras = CameraState()` 를 만들고
   초기화 줄을 지웁니다.
3. 모든 참조를 `self.cameras.<이름>` 으로 바꿉니다. 앞의 밑줄은 뗍니다.
4. 빌더와 페이지가 `_crop_params`, `_grid_store` 를 직접 만집니다
   (`pages/layout.py`, `builders/layout.py` 등). 전부 grep 으로 찾아 고치세요:

       grep -rn '_crop_params\|_grid_store\|_layout_ref\|_depth_\|_cloud_' apps/ tests/

## 주의: 라이브 루프

`_last_cam_frame`, `_fps_count`, `_stream_states` 는 카메라 프레임이 올 때마다
갱신됩니다. **속성 하나가 늘어난 만큼의 비용도 이 경로에서는 눈에 보입니다**
(2026-08-31 에 게이지가 느려진 원인이 이런 종류였습니다). 그러니 이 세 개는
프레임마다 도는 함수 안에서 지역 변수로 한 번만 읽고 쓰도록,
`self.cameras.x` 를 반복해서 쓰지 말고 루프 앞에서 `cams = self.cameras` 로
한 번 받아 쓰세요.

## 하지 말 것

- 동작을 바꾸지 마세요. 프레임 주기, 뎁스 컬러맵, 포인트 클라우드 계산 전부
  그대로입니다.
- 메서드는 옮기지 마세요 (Phase 4).
- Qt 위젯을 모델에 넣지 마세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

19개 전부 통과해야 합니다. `test_camera_node`, `test_depth17`,
`test_diversity_cloud`, `test_grid_replay` 가 이 영역입니다.

## 보고

옮긴 항목, 위젯이라 제외한 항목, 라이브 루프에서 지역 변수로 받은 곳,
줄 수 변화를 한 문단으로.
