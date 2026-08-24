# depth 수집 기능 게이팅 (fix/depth-gate)

## 배경
`gello/libero_gui_worker.py`의 `_get_obs`가 `cam.read_latest_depth()`를 호출하지만, lerobot 0.5.0의 `RealSenseCamera`에는 이 메서드가 없어 스키마에서 depth 를 켜면 세션이 `AttributeError`로 즉사했다. 팀 결정에 따라 depth 수집 기능은 코드를 남겨두고 게이트만 막았다.

## 변경 내용

### 1. 설정 파일 강제 무시 — `gello/dataset_schema.py`
- `DatasetSchemaConfig._FIXED`에 `save_agentview_depth`, `save_eye_in_hand_depth`를 추가.
- `from_json()`에서 두 플래그가 `True`로 저장돼 있어도 무시하고 `warnings.warn`으로 한 줄 알린다.
- 실제 `DatasetSchemaConfig` 인스턴스에는 항상 `False`가 들어간다.

### 2. UI 게이트 — `gello/gui_widgets.py`
- `DatasetSchemaDialog`의 depth 체크박스 2개(`save_agentview_depth`, `save_eye_in_hand_depth`)를 `setEnabled(False)`로 비활성화.
- 툴팁: "카메라 드라이버(lerobot RealSenseCamera)가 depth 읽기를 지원하지 않아 수집이 비활성화되어 있습니다".
- `_current_config()`에서 depth 필드를 항상 `False`로 강제. (체크박스를 우회하는 코드 경로도 막기 위함.)

### 3. 워커 방어 가드 — `gello/libero_gui_worker.py`
- `_get_obs()`의 depth 루프에서 각 카메라에 `read_latest_depth`가 있는지 `hasattr`로 확인.
- 없으면 해당 역할을 `_depth_roles`에서 제거하고 1회 경고 메시지를 `log_message.emit`.
- UI 게이트와 별개로 구버전 설정 파일이나 코드 경로 우회 시에도 `AttributeError`로 세션이 죽지 않는다.

### 4. 테스트 — `tests/gui/test_depth17.py`
기존 depth 저장 경로 테스트를 보존하고, 아래 항목을 추가:
- `DatasetSchemaConfig.from_json`의 depth 플래그 강제 `False` 및 경고 발생.
- `read_latest_depth`가 없는 가짜 카메라로 `_get_obs`를 돌려 예외 없이 depth 역할이 제거됨.
- `DatasetSchemaDialog`의 depth 체크박스 비활성화 및 결과 `False` 강제.
- convert import 실패 시 해당 섹션만 걸너뛴다. (lerobot 0.5.0 환경에서 정상.)

## 검증 결과
- `bash tests/gui/run_all.sh python`: 전체 OK (`test_depth17` 포함).
- `python scripts/check_scene_file.py --selftest`: 통과.

## 범위 밖 사항 (수정 안 함)
- `write_episode_payload` / 버퍼의 depth 저장 코드는 그대로 둠. (게이트만 막았을 뿐, 저장 로직은 보존.)
- `_demo.hdf5` legacy 경로 동작 불변.
- Depth/Point Cloud **뷰 탭**은 별도 경로이므로 수정하지 않음. `grep`으로 확인한 결과, `DepthCloudWorker`(`gello/gui_widgets.py`)는 `pyrealsense2`를 직접 사용하고 `read_latest_depth`를 호출하지 않는다. `experiments/collect_workspace.py`의 Depth/Point Cloud 탭도 이 워커를 사용한다.

## 커밋
- 로컬 커밋만 수행. 푸시 금지.
