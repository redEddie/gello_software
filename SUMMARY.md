# Hub 동기화·캐시 견고화 (fix/hub-sync-hardening)

2026-08-18 리뷰 Medium 3건을 독립 커밋 3개로 수정.

## 커밋

1. `2883bb4` fix(scene_gallery): scene 에피소드 삭제 후 썸네일 캐시 무효화
   - `gello/scene_gallery.py`: `invalidate_scene_thumbs(scene_id)` 추가
   - `experiments/collect_workspace.py`: 비소유 `delete_scene_episodes` 후 및
     세션 소유 saver 삭제 완료(`episode_list_changed`) 후 썸네일 무효화
   - docstring 의 uid 전역 유일/immutable 전제를 삭제 후 재배정 사실로 갱신
   - `tests/gui/test_scene_edit.py`에 무효화 함수 테스트 추가

2. `d0a0b63` fix(hub): LeRobot 데이터셋 조회 시 revision=v3.0 태그 고정
   - `scripts/convert_libero_to_lerobot.py`: `_load_uid_sidecar` 의
     `hf_hub_download` 에 `revision=CODEBASE_VERSION` 추가
   - `gello/dataset_sync.py`: `LEROBOT_TAG="v3.0"` 정의, `hub_meta`/`hub_episode_uids`
     의 `snapshot_download` 에 `revision=LEROBOT_TAG` 추가
   - `RevisionNotFoundError`(태그 없는 신생 repo)를 빈 결과 정상 케이스로 처리
   - `tests/gui/test_dataset_sync.py` 추가, `tests/gui/run_all.sh` 에 등록

3. `0598f19` fix(dataset_sync): 혼합 task 의 at_repack 잔존 제거
   - `gello/dataset_sync.py`: legacy+scene 이 같은 task 로 합산될 때
     `at_repack` 도 `None` 으로 지우고 주석으로 이유 명시
   - `tests/gui/test_dataset_sync.py`에 혼합 가짜 파일로 `at_repack is None`
     검증 테스트 추가

## 인수 기준 검증

```bash
# py_compile (pass)
python -m py_compile scripts/convert_libero_to_lerobot.py gello/dataset_sync.py gello/scene_gallery.py

# scene 파일 자가 검증 (pass)
python scripts/check_scene_file.py --selftest

# GUI 인수 테스트: test_depth17 을 제외한 전부 OK
bash tests/gui/run_all.sh python
```

`test_depth17` 만 `ImportError: cannot import name 'DatasetInfo' from 'lerobot.datasets.utils'`
로 실패 -- 이 머신의 lerobot 0.5.0 환경 의존(기존에도 실패하던 항목).

## 제약 준수

- 로컬 커밋 3개, 푸시하지 않음.
- 파일명으로 task/instruction 판별하지 않음.
- legacy 경로 동작 불변.
