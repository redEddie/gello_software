refactor(data): libero_format 의 순수 계산부를 분리 (5-4)

## 이번에는 부작용 없는 것만

`gello/data/libero_format.py` (1,367줄) 는 책임이 일곱 가지입니다. 그중
**파일을 쓰지 않는 순수 함수**만 이번에 옮깁니다. HDF5 를 쓰는 부분
(`LiberoEpisodeBuffer`, `write_episode_payload`, `LiberoTaskWriter`) 은
**테스트가 없습니다** -- 그것부터 만든 뒤에 손댑니다 (5-6, 5-7).

## 옮길 것

    gello/data/actions.py
        _quat_to_axis_angle  _quat_mul  _quat_conj
        compute_delta_action  compute_joint_*_action  compute_ee_*_action
    gello/data/crop.py
        default_crop_params  crop_params_path  load_crop_params
        save_crop_params  square_crop  resize_rgb
    gello/data/schema_description.py
        action_column_names  resolved_action_column_names
        describe_schema  describe_episode  schema_from_episode

`grep -n '^def \|^class ' gello/data/libero_format.py` 로 실제 이름을
확정하세요. 위 목록은 문서에서 옮긴 것이라 어긋날 수 있습니다 -- **코드를
믿고**, 어긋난 것은 보고에 적으세요.

## 하지 말 것

- `LiberoEpisodeBuffer`, `write_episode_payload`, `LiberoTaskWriter`,
  `NullTaskWriter`, `hdf5_repack_status` 는 **건드리지 마세요.**
- 재수출 껍데기를 만들지 마세요.
- 수식을 손보지 마세요. `compute_joint_absolute_action` 은 실제로 지나간
  궤적과 미묘하게 다르고, 여기서 무엇을 고치면 학습 데이터가 조용히
  바뀝니다. **자리만 옮깁니다.**

## 주의: 임포터가 21개

    grep -rln 'libero_format' --include='*.py' . | grep -v third_party

전부 확인해 새 경로로 고치세요. 임포트를 틀리면 즉시 ImportError 라 조용히
지나가지는 않지만, 21곳을 다 봐야 합니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

20개 통과. `test_depth17` 이 `schema_from_episode` 를 직접 부릅니다.

    grep -cw compute_delta_action gello/data/libero_format.py   # 0 이어야 함

## 보고

옮긴 것, 문서 목록과 코드가 어긋난 곳, 새 모듈 줄 수, `libero_format.py`
줄 수 변화, 고친 임포터 수를 한 문단으로.
