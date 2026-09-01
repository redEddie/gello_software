refactor(workspace): 하위 프로세스 상태를 ProcessRegistry로 분리 (Phase 3-1)

## 목표

`WorkspaceWindow` 에 흩어진 QProcess 핸들과 파이프라인 진행 상태를
`apps/workspace/models.py` 의 `ProcessRegistry` 하나로 묶습니다.
Phase 3 의 첫 조각이므로, 뒤 세 조각이 따라 할 본을 만드는 것이 목적입니다.

## 옮길 것 (이것만, 정확히)

    node_process  camera_node_process  convert_process  repack_process
    upload_process  replay_process  runme_process  _reset_protection_process
    _pipeline_proc  _pipeline_steps  _pipeline_results  _pipeline_t0
    _pipeline_step_t0

`repo_warn` 은 이름이 비슷하지만 Qt 위젯입니다. 옮기지 마세요.

## 방법

1. `apps/workspace/models.py` 를 새로 만들고 `ProcessRegistry` 를 정의합니다.
   dataclass 로 하되 기본값은 `WorkspaceWindow.__init__` 에 지금 있는 값을
   그대로 씁니다 (대부분 `None`, 리스트는 `field(default_factory=list)`).
2. `WorkspaceWindow.__init__` 에서 `self.procs = ProcessRegistry()` 를 만들고,
   위 13개의 초기화 줄을 지웁니다.
3. 모든 참조를 `self.procs.<이름>` 으로 바꿉니다. 밑줄로 시작하던 것은
   레지스트리 안에서는 밑줄을 뗍니다 (`_pipeline_steps` -> `procs.pipeline_steps`).
4. `apps/workspace/` 안(빌더·페이지)에서도 같은 이름을 만지는 곳이 있으면
   같이 고칩니다. 반드시 grep 으로 확인하세요:

       grep -rn '\b\(node_process\|camera_node_process\|...\)\b' apps/ tests/

5. 여러 곳에 흩어진 "지금 뭔가 돌고 있나" 판정이 있으면
   `ProcessRegistry.any_running()` 같은 메서드로 모읍니다. 없으면 만들지
   마세요 -- 없는 추상을 미리 만들지 않습니다.

## 하지 말 것

- 동작을 바꾸지 마세요. 이번 작업은 **이름을 옮기는 것**이지 재설계가 아닙니다.
  QProcess 를 누가 소유하는지, 언제 시작하고 죽이는지는 그대로 둡니다.
- 메서드는 옮기지 마세요. 상태만 옮깁니다 (메서드 이동은 Phase 4 입니다).
- Qt 위젯을 모델에 넣지 마세요.
- `apps/workspace/models.py` 가 `apps/collect_workspace.py` 를 임포트하면
  안 됩니다 (화살표 역류 -- 테스트가 잡습니다).

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

19개 전부 통과해야 합니다. 개별 테스트만 골라 돌리지 마세요.
특히 `test_app_structure` 가 고아 임포트와 화살표 방향을, `test_ui_surface`
가 메뉴·툴바·스크립트 경로를 봅니다.

옮긴 뒤에는 원본에 남은 것이 없는지 세어보세요:

    grep -cw node_process apps/collect_workspace.py   # 0 이어야 함

## 보고

무엇을 옮겼는지, `models.py` 가 몇 줄인지, `collect_workspace.py` 가 몇 줄
줄었는지, 그리고 위 grep 결과를 한 문단으로 보고하세요.
