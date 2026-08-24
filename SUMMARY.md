# 작업 요약: 수집 세션 중 에피소드 재판정 안정화

## 문제
`experiments/collect_workspace.py`의 `_relabel_episodes`가 세션이 쥔(owned)
scene 파일도 GUI 스레드에서 `h5py.File(path, "r" if owned else "a")`로 다시
열었다. HDF5는 같은 프로세스에서 쓰기 중인 파일의 재오픈을 거부하므로,
재판정이 상태(리셋 대기/게이트/approach/기록 중)에 따라 우연히 되거나
예외로 끝났다.

## 해결
`_relabel_episodes`를 상태와 무관하게 동작하도록 고쳤다.

- **owned 파일**: `h5py.File`을 재오픈하지 않는다.
  - 판정값은 `self.active_episode_cache`(EpisodeSaver의
    `episode_list_changed`가 채우는 요약 캐시)에서 읽는다.
  - 쓰기는 기존 saver 큐 명령 `worker.cmd_set_episode_success`로 보내,
    EpisodeSaver가 `SceneWriter.set_quality_status` 또는
    `LiberoTaskWriter.set_episode_success`를 통해 이미 연 파일 핸들을 재사용한다.
  - 캐시에 없는 이름은 사유별("세션 캐시에 없음" / "success·failed 아님")로
    "건너뜀" 집계하고 로그를 남긴다(조용한 실패 금지).
- **비소유 파일**: `h5py.File(path, "a")`로 열어 직접 수정한다. scene 전용 --
  호출 경로(`_on_relabel_selected` scene 필터, scene 전용 Gallery)가 scene
  파일만 넘기므로 legacy 분기는 두지 않는다 (리뷰 반영 2026-08-24: 초판이
  신설했던 legacy 분기는 도달 불가 죽은 코드 + 필드 규약 불일치라 제거).
- UI 갱신: saver의 `episode_list_changed`가 오면 트리가 갱신되므로
  별도의 낙관적 갱신은 넣지 않았다. 버튼 피드백(로그 한 줄)은 유지한다.

## 변경 파일
- `experiments/collect_workspace.py`: `_relabel_episodes` 재구현.
- `tests/gui/test_relabel.py`: 신규 테스트.
- `tests/gui/run_all.sh`: `test_relabel` 추가.

## 테스트 결과
- `bash tests/gui/run_all.sh python`: `test_depth17`(lerobot 0.5.0 환경
  의존)을 제외한 전체 OK.
- `python scripts/check_scene_file.py --selftest`: 통과.
- `tests/gui/test_relabel.py` 상세:
  - owned scene 파일 재판정 시 `h5py.File` 호출 없이 saver 큐에 명령 전달.
  - 캐시에 없는 이름은 건너뜀 집계 + 로그 문구 직접 단언, 예외 없음.
  - 비소유 scene 파일 직접 수정 회귀.

## 커밋
- `d4b47f2 fix: 수집 세션 중 owned 파일 재판정이 h5py 재오픈 없이 동작`
- 브랜치: `fix/session-relabel`
- 푸시하지 않음(로컬 커밋만).
