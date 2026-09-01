test(data): 에피소드 쓰기 경로 왕복 검증 -- 쪼개기 전에 만드는 안전망 (5-5)

## 이 작업은 소스를 고치지 않습니다

`tests/gui/test_episode_io.py` 하나만 만듭니다. `gello/` 아래 파일은 **한 줄도
바꾸지 마세요.** 지금 코드가 하는 일을 그대로 붙잡아 두는 것이 목적입니다.

## 왜 필요한가

`write_episode_payload` 의 action_space 네 갈래 분기와 `LiberoEpisodeBuffer`,
`LiberoTaskWriter` 는 **학습 데이터를 만드는 코드인데 테스트가 없습니다**
(`scripts/check/check_scene_file.py` 는 스크립트지 테스트가 아닙니다).
다음 조각에서 이것들을 다른 파일로 옮길 텐데, 지금 상태를 붙잡아 두지 않으면
무엇이 달라졌는지 알 방법이 없습니다. 조용히 어긋난 데이터셋은 몇 주 뒤
학습이 이상할 때에야 드러납니다.

## 무엇을 검사할 것인가

로봇도 카메라도 없이, 가짜 관측을 만들어 임시 디렉터리에 HDF5 를 쓰고 되읽어
확인합니다.

1. **왕복**: `LiberoTaskWriter` 로 에피소드 2~3개를 쓰고 h5py 로 열어
   - 그룹 구조(`data/demo_N`, `obs/*`)가 `dataset_schema.SCHEMA_FIELDS` 와 맞는지
   - `metadata.attrs` 의 `dataset_version` / `schema_version` 이
     `SCHEMA_VERSION` 인지
   - 프레임 수, 각 데이터셋의 shape·dtype
2. **action_space 네 갈래**: `compute_*_action` 을 각각 대표 입력으로 불러
   결과 벡터의 **shape 와 처음 몇 값**을 고정합니다. 값은 지금 코드가 내는
   것을 그대로 기준으로 삼되, 실수 비교는 `np.allclose` 로 하고 허용오차를
   적으세요. **기대값을 손으로 계산해 넣지 마세요** -- 지금 동작을 붙잡는
   것이지 옳은지 따지는 것이 아닙니다.
3. **버퍼**: `LiberoEpisodeBuffer` 에 프레임을 넣고 비운 뒤, 저장된 길이와
   순서가 넣은 대로인지.
4. **재압축 상태**: `hdf5_repack_status` 를 압축된 파일과 안 된 파일에
   각각 불러 판정이 갈리는지.

## 반드시 지킬 것

- **임시 디렉터리에만 쓰세요** (`tempfile.mkdtemp()`). `~/libero_datasets`
  근처를 건드리면 실제 수집 데이터가 위험합니다. 끝나면 지우세요.
- 이 저장소의 다른 인수 테스트와 같은 모양으로 쓰세요: `assert` 와
  `print("N. ... OK")`, 마지막에 `print` 한 줄. pytest 를 쓰지 마세요
  (ROS 의 PYTHONPATH 때문에 이 기계에서 pytest 전체 실행이 안 됩니다).
- Qt 를 쓰지 않으므로 `os._exit(0)` 은 필요 없습니다.
- `tests/gui/run_all.sh` 목록에 `test_episode_io` 를 추가하세요 (21개가 됩니다).

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

21개 전부 통과해야 합니다.

## 보고

무엇을 붙잡았는지 항목별로, 그리고 **붙잡지 못한 것**을 반드시 적으세요.
다음 조각이 그 부분을 조심해야 하므로, 못 한 것을 아는 것이 더 중요합니다.
