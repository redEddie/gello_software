#!/bin/bash
# GUI 인수 테스트 일괄 실행 (offscreen, 로봇/카메라 불필요)
# 사용: bash tests/gui/run_all.sh [python]
PY="${1:-python}"
cd "$(dirname "$0")"
fail=0
for t in test_phase4a test_grid_replay test_plan_form test_right_scene \
         test_gate_reset test_plan_edit_replay test_h5view; do
  if QT_QPA_PLATFORM=offscreen timeout 240 "$PY" -u "$t.py" >"/tmp/$t.out" 2>&1; then
    echo "$t OK"
  else
    echo "$t FAIL"; tail -5 "/tmp/$t.out"; fail=1
  fi
done
exit $fail
