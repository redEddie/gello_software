refactor(workspace): 업로드·변환 파이프라인을 UploadOps로 분리 (Phase 4-1)

`tasks/_공통.md` 를 먼저 읽으세요. 거기 적힌 규칙이 전부 적용됩니다.

## 왜 이것부터인가

Phase 4 의 첫 조각이고, 여섯 후보 중 **밖에서 불리는 횟수가 가장 적습니다**
(3곳). 뒤의 다섯 조각이 따라 할 본을 만드는 것이 목적이니, 모양을 정성껏
잡아 주세요.

## 만들 것

`apps/workspace/domains/upload.py` 의 `UploadOps`.
창에서는 `self.upload = UploadOps(self)`.

## 옮길 것

`apps/collect_workspace.py` 의 `WorkspaceWindow` 에서 업로드·변환·재압축·
파이프라인에 해당하는 메서드 전부입니다. 이름으로 찾으세요:

    _on_pipeline  _pipeline_*  _on_lerobot  _on_repack  _on_upload*
    _on_hdf5_upload  _on_hdf5_auto  _on_myhdf5  _on_hf_accounts
    _hdf5_candidates  _hdf5_upload_selection  _all_hdf5
    _count_hdf5_episodes  _pipeline_guard  _confirm_ambiguous_idle
    repo_id_for  _check_repo

약 16개, 440줄 정도입니다. **직접 grep 해서 목록을 확정한 뒤 옮기세요** --
위 목록은 내가 정규식으로 뽑은 것이라 빠진 것이 있을 수 있습니다.
`_on_delete_*` 처럼 데이터셋 쪽 메서드는 옮기지 마세요 (Phase 4-5 입니다).

## 주의

- 하위 프로세스 핸들은 이미 `self.win.procs.*` 에 있습니다
  (`procs.convert_process`, `procs.pipeline_steps` 등). 새로 만들지 마세요.
- `PipelineDialog` 은 `scripts=` 딕셔너리를 필수 인자로 받습니다. 호출부를
  옮길 때 `CONVERT_SCRIPT` 등 모듈 상수를 어떻게 넘길지 정해야 합니다 --
  상수는 `collect_workspace.py` 에 있고 도메인은 그것을 임포트할 수 없으므로
  (화살표 역류), `apps/workspace/constants.py` 로 옮기고 양쪽이 그것을
  임포트하게 하세요.
- 스크립트 경로 상수를 옮기면 `test_ui_surface` 가 그 경로를
  `collect_workspace.py` 에서 못 찾아 실패할 수 있습니다. 그러면 그 테스트의
  경로 수집 부분을 `apps/workspace/constants.py` 도 보도록 고치세요 --
  기준선 JSON 은 고치지 마세요. 값이 같아야 합니다.
