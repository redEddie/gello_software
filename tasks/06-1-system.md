refactor(workspace): 기계 관리(노드·튜닝·서보 보호)를 SystemOps로 분리 (6-1)

`tasks/_공통.md` 를 먼저 읽으세요. `domains/upload.py` 가 본입니다.

## 왜 도메인이 아니라 따로인가

`check_tuning`, `_run_runme`, `_on_reset_leader_protection` 은 수집 기능이
아니라 **기계 관리**입니다. 어느 도메인에도 안 속해서 Phase 4 가 창에
남겼습니다. 억지로 다른 도메인에 넣지 말고 제 이름을 주세요.

## 만들 것

`apps/workspace/domains/system.py` 의 `SystemOps`.
창에서는 `self.system = SystemOps(self)`.

## 옮길 것 (약 10개, 191줄)

    check_tuning  _startup_tuning  _run_runme  _on_runme_finished
    _on_start_node  _on_stop_node  _on_node_output  _on_node_status
    _on_node_finished  _on_check_cameras  _on_reset_leader_protection

`grep -n '    def ' apps/collect_workspace.py` 로 확정하세요.

## 주의

- `_run_runme` 는 `GELLO_NO_PRIVILEGED` 가 있으면 로그만 남기고 반환합니다.
  **이 가드를 지우지 마세요** -- 사람 없는 자리에서 pkexec 비밀번호 창이
  뜨면 그대로 멈춥니다 (2026-09-01 에 실제로 막혔습니다).
- 테스트 4개가 `cw.WorkspaceWindow._startup_tuning` 을 스텁합니다. 옮기면
  그 스텁이 무력해지므로 `cw.SystemOps.startup_tuning` 으로 고치세요.
  **스텁을 지우지 마세요.**
- 프로세스 핸들은 이미 `self.win.procs.*` 에 있습니다.
- `check_tuning` 은 `@staticmethod` 인지 확인하고 그대로 유지하세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

22개 전부 통과. `test_domain_attrs` 가 `self.win.<이름>` 실재를 봅니다.
