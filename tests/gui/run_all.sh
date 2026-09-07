#!/bin/bash
# GUI 인수 테스트 일괄 실행 (offscreen, 로봇/카메라 불필요)
# 사용: bash tests/gui/run_all.sh [python]
PY="${1:-python}"
# 사람이 없는 자리(밤샘 러너, CI)에서 관리자 비밀번호 창이 뜨면 답할 사람이
# 없어 그대로 멈춘다. 리더암이 꽂힌 채 재연결/재부팅된 뒤에만 뜨므로 기계
# 상태에 따라 떴다 안 떴다 한다 -- 2026-09-01 에 실제로 막혔다.
export GELLO_NO_PRIVILEGED=1
# 이 스위트는 로봇도 카메라도 없이 돈다. 마법사 하드웨어 페이지가 미리보기를
# 위해 카메라 노드를 띄우므로, 여기서 막지 않으면 테스트가 실제 카메라를
# 붙잡아 조작자의 GUI 를 방해한다 (2026-09-05).
export GELLO_NO_CAMERA_NODE=1
# 같은 이유로 로봇 노드도 막는다 -- 데이터세트 버전 [확인] 이 노드를 직접
# 띄우므로, 여기서 막지 않으면 테스트가 FCI 를 잡는다 (2026-09-05).
export GELLO_NO_ROBOT_NODE=1
# 이 스위트는 네트워크 없이도 돌아야 한다. 앱 글꼴(D2Coding)은 캐시가 비어
# 있으면 21MB 를 받는데, 그 대기와 실패가 테스트 결과에 섞이면 안 된다.
# 캐시가 이미 있으면 그건 그대로 쓴다 -- 막는 것은 네트워크지 글꼴이 아니다.
export GELLO_NO_FONT_DOWNLOAD=1
# 테스트는 조작자의 상태 파일(격자·크롭·recents·업로드 장부·스키마 설정)을
# 절대 건드리면 안 된다. 2026-09-06 에 실제로 덮어썼다 -- 격자 테스트가
# collect_workspace.save_grid_store 를 패치했지만 features/camera/ops.py 가
# 같은 함수를 직접 임포트해 들고 있어서, 그 이름을 통한 호출이 진짜 파일로
# 갔고 조작자가 맞춰 둔 3×3 격자가 스위트를 돌릴 때마다 초기화됐다.
# 호출 하나를 패치하는 방식은 임포트 방식에 따라 새므로, 뿌리를 옮긴다.
GELLO_STATE_DIR="$(mktemp -d -t gello-test-state-XXXXXX)"
export GELLO_STATE_DIR
trap 'rm -rf "$GELLO_STATE_DIR"' EXIT
cd "$(dirname "$0")"
fail=0
for t in test_phase4a test_grid_replay test_plan_form test_right_scene \
         test_gate_reset test_plan_edit_replay test_h5view \
         test_diversity_cloud test_recommend_register test_depth17 \
         test_scene_edit test_stats_group test_relabel test_dataset_sync \
         test_hub_upload_state test_camera_node test_match_gate \
         test_app_structure test_ui_surface test_domain_attrs \
         test_episode_io test_layer_rules test_signal_slots test_instruction_counter \
         test_dataset_meta test_launcher test_key_autorepeat \
         test_station_save test_wheel_guard test_waypoint_kinematics \
         test_state_isolation test_resume_version test_workflow_gui test_quick_resume test_node_diag \
         test_collect_layout test_script_bootstrap test_relation_shape \
         test_scene_repair test_doctor_tab test_info_card \
         test_doctor_contrast test_doctor_progress test_doctor_schema test_dataset_right; do
  if QT_QPA_PLATFORM=offscreen timeout 240 "$PY" -u "$t.py" >"/tmp/$t.out" 2>&1; then
    echo "$t OK"
  else
    echo "$t FAIL"; tail -5 "/tmp/$t.out"; fail=1
  fi
done
exit $fail
