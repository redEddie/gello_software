#!/bin/bash
# GUI 인수 테스트 일괄 실행 (offscreen, 로봇/카메라 불필요)
# 사용: bash tests/gui/run_all.sh [python]
PY="${1:-python}"
# 사람이 없는 자리(밤샘 러너, CI)에서 관리자 비밀번호 창이 뜨면 답할 사람이
# 없어 그대로 멈춘다. 리더암이 꽂힌 채 재연결/재부팅된 뒤에만 뜨므로 기계
# 상태에 따라 떴다 안 떴다 한다 -- 2026-09-01 에 실제로 막혔다.
export GELLO_NO_PRIVILEGED=1
cd "$(dirname "$0")"
fail=0
for t in test_phase4a test_grid_replay test_plan_form test_right_scene \
         test_gate_reset test_plan_edit_replay test_h5view \
         test_diversity_cloud test_recommend_register test_depth17 \
         test_scene_edit test_stats_group test_relabel test_dataset_sync \
         test_hub_upload_state test_camera_node test_match_gate \
         test_app_structure test_ui_surface test_domain_attrs; do
  if QT_QPA_PLATFORM=offscreen timeout 240 "$PY" -u "$t.py" >"/tmp/$t.out" 2>&1; then
    echo "$t OK"
  else
    echo "$t FAIL"; tail -5 "/tmp/$t.out"; fail=1
  fi
done
exit $fail
