# GUI 인수 테스트 (offscreen)

로봇·카메라 없이 도는 수집 GUI 회귀 테스트. QMessageBox/카메라 열거는
스텁하고, scene 파일은 합성하거나 잠금(수집 세션 중)을 허용하도록 짜여
있다 -- 수집이 돌아가는 중에도 실행할 수 있다.

```bash
bash tests/gui/run_all.sh ~/lerobot-venv/bin/python
```

| 파일 | 검증 |
|---|---|
| test_phase4a | 계획 로더 규칙(scene 로컬 ID), slot 패널 카운트/경고/다음 slot |
| test_grid_replay | 3×3 격자 계산·편집·오버레이, 실로봇 재생 가드/명령행 |
| test_plan_form | 계획 폼(자동 ID·번호 보존·검증 게이트), 시작 문장 드롭다운 |
| test_right_scene | 오른쪽 패널 scene 배치도 |
| test_gate_reset | 게이트 자동정렬 범위 조건, Start 잠금, 리셋 중 프레임 |
| test_plan_edit_replay | JSON 원문 편집기, replay 로더 양포맷 |
| test_scene_edit | scene 삭제 후 renumber(그룹·episode_id·slot E·uid), 트림 양포맷, GUI 혼합 삭제+확인창, 검사기 불변식 |
| test_stats_group | Analysis 그룹 = (scene, 문장): 같은 문장도 scene 별 분리, legacy 는 문장 단위 |
| test_dataset_meta | dataset-identity.json 왕복, discover_datasets(부모 스캔·dedupe·legacy), plan_progress 실측 |
| test_launcher | 런처 마법사: 모드 버튼 2개(Cancel/Next 없음), 분기, 새 데이터셋 생성+설정 복사, legacy identity 자동 생성, apply_result env/recents |
