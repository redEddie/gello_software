refactor(workspace): camera 기능을 features/camera/ 로 (8-3)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

    features/camera/__init__.py     재수출 + __all__
    features/camera/ops.py          domains/camera.py
    features/camera/depth.py        domains/depth.py
    features/camera/depth_tab.py    builders/depth_tab.py
    features/camera/cloud_tab.py    builders/cloud_tab.py

뎁스와 포인트 클라우드는 카메라 데이터를 보는 방식이라 같은 기능입니다.
클래스 이름(`CameraOps`, `DepthOps`)과 창의 속성 이름
(`self.camera_ops`, `self.depth_ops`)은 그대로 두세요.

## 주의

- 프레임 경로(`on_frame*`, `tick_fps`)는 카메라 프레임마다 돕니다.
  자리만 옮기고 코드를 만지지 마세요. `win = self.win` 로 한 번만 받는
  기존 패턴을 유지하세요.
- 테스트 여러 개가 `cw.CameraOps.refresh_cameras` 와
  `cw.CameraOps.restart_previews` 를 스텁합니다 (기계 없이 돌리기 위해).
  `cw.` 로 여전히 닿는지 확인하고, 안 닿으면 임포트 경로를 고치되
  **스텁 자체를 지우지 마세요.**
- `cloud_pitch` / `cloud_yaw` 는 창이 가진 QSlider 입니다. 모델이나 도메인에
  넣지 마세요 -- 한 번 잘못 들어갔다 되돌린 적이 있습니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

`test_camera_node`, `test_depth17`, `test_diversity_cloud` 가 이 영역입니다.
