refactor(workspace): system 을 features/system/ 으로 (8-5)

`tasks/_공통.md` 를 먼저 읽으세요. 이번 조각으로 `domains/` 가 비어야 합니다.

## 만들 것

    features/system/__init__.py   재수출 + __all__
    features/system/ops.py        domains/system.py

## 옮긴 뒤 확인

`apps/workspace/domains/` 에 `__init__.py` 말고 무엇이 남았는지 세세요.
비었으면 `domains/__init__.py` 도 지우고 폴더를 없애세요. 남은 것이 있으면
**지우지 말고** 무엇이 왜 남았는지 보고에 적으세요.

## 주의

- `_run_runme` 의 `GELLO_NO_PRIVILEGED` 가드를 지우지 마세요. 사람 없는
  자리에서 pkexec 비밀번호 창이 뜨면 그대로 멈춥니다.
- 테스트 4개가 `cw.SystemOps.startup_tuning` 을 스텁합니다. `cw.` 로 닿는지
  확인하고, 안 닿으면 경로만 고치되 **스텁을 지우지 마세요.**
- `check_tuning` 이 `@staticmethod` 면 그대로 두세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python
