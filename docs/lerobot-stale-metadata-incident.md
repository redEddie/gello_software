# LeRobot 업로드 사고 분석 — 낡은 메타데이터가 만든 번호 충돌

2026-08-04 ~ 08-05, `knu-physical-ai/fr3-pick-place-lerobot`

한 줄 요약: **업로드 마지막 단계에서 난 사소해 보이는 `AttributeError`가, 실제로는
데이터셋의 에피소드 번호를 겹치게 만들었다.** 에러는 증상이고, 원인은 lerobot이
읽는 git 태그가 옛 커밋에 멈춰 있었던 것이다.

---

## 1. 무엇이 보였나

전체 처리 파이프라인의 마지막 단계에서 이렇게 죽었다.

```
File "scripts/convert/convert_libero_to_lerobot.py", line 427, in main
    ds.push_to_hub(private=args.private)
File "lerobot/datasets/utils.py", line 452, in create_lerobot_dataset_card
    dataset_structure += f"```json\n{json.dumps(dataset_info.to_dict(), indent=4)}\n```\n"
AttributeError: 'dict' object has no attribute 'to_dict'
```

읽는 그대로는 "카드 만들다 실패" 정도로 보인다. 데이터는 이미 다 올라간 뒤였다.

## 2. 직접 원인

`--push-only` 경로가 `LeRobotDataset`을 정상 생성자로 만들지 않고 껍데기를 세운다.
그렇게 한 이유가 있다 — `LeRobotDataset(repo_id, root)` 생성자는 **Hub 사본을 root로
동기화하면서 방금 변환이 써놓은 메타데이터를 옛 것으로 덮어쓴다**(이전 사고). 그래서
생성자를 우회했는데, `meta.info`에 평범한 dict를 넣었다.

lerobot의 카드 생성기는 그 자리에 `DatasetInfo`(dataclass)를 기대하고 `.to_dict()`를
부른다.

```python
ds.meta = SimpleNamespace(info=json.loads(...))          # dict
...
json.dumps(dataset_info.to_dict(), indent=4)             # dict엔 없음
```

고치는 것 자체는 한 줄이다: `DatasetInfo.from_dict(info)`.

## 3. 진짜 문제는 죽은 *지점*

`push_to_hub`의 실행 순서는 이렇다.

```
create_repo → upload_folder → 카드 생성 → delete_tag → create_tag
                  ↑ 성공        ↑ 여기서 죽음      ↑ 실행되지 않음
```

즉 **데이터는 올라갔고 태그만 안 옮겨졌다.**

그런데 lerobot은 브랜치가 아니라 **`CODEBASE_VERSION`(= `v3.0`) 태그**로 데이터셋을
읽는다. 태그가 옛 커밋을 가리키면, 그 뒤의 모든 `resume()`은 **그 시점의 낡은
메타데이터**를 기준으로 삼는다.

사고 직후 실제 상태:

```
main  → 956c8fad   (info.json: 269 에피소드)
v3.0  → 1a9f5ea5   (info.json: 117 에피소드)   ← lerobot이 읽는 쪽
```

`v3.0`이 117에 멈춰 있던 이유는 그 전 사고에 있다. 그때 `meta/info.json`을 손으로
고쳐 올렸는데, `api.upload_file`은 `main`에만 커밋하고 **태그는 건드리지 않는다.**
그 씨앗이 이번에 발아했다.

## 4. 그래서 무슨 일이 벌어졌나

`resume()`은 다운로드한 메타데이터에서 누계를 이어받아 새 에피소드에 번호를 매긴다.

```
낡은 씨앗   117 + 152(새로 추가) = 269      ← 실제로 기록된 값
올바른 값   121 + 152             = 273
```

프레임 수까지 산술이 정확히 맞는다.

```
44073 − 27173(새 152개의 프레임) = 16900 = 117 에피소드 시절의 프레임 수
```

**여기까지는 개수만 틀린 문제다. 그런데 번호가 겹쳤다.**

새 에피소드가 117번부터 번호를 매기기 시작했고, Hub의 `meta/episodes`에 이미 있던
117~120번과 충돌했다.

```
에피소드 273개  /  고유 인덱스 269개
중복: [117, 118, 119, 120]   — 서로 다른 에피소드가 같은 번호를 쓴다
```

이게 결정적이다. `total_episodes`를 273으로 고쳐 적으면 **개수는 맞아 보이지만 충돌은
그대로 남는다.** 오히려 정직한 오류가 감춰져서 더 나쁘다.

## 5. 왜 알아채기 어려웠나

- Hub 웹 화면에 태그 상태가 드러나지 않는다. 직접 물어봐야 안다.
- 데이터 파일은 정상적으로 다 올라가 있었다. task별 개수도 로컬과 일치했다.
- 어긋난 것은 `meta/info.json` 숫자와 `episode_index` 뿐이고, 둘 다 파일을 열어
  세어봐야 보인다.
- 에러 메시지는 카드 생성 실패만 말했다. 태그 얘기는 어디에도 없다.

## 6. 고친 것

같은 일이 다시 일어나지 않도록 네 겹으로 막았다.

### (1) 크래시 자체
`DatasetInfo.from_dict(info)` — 카드 생성기가 기대하는 타입을 넘긴다.

### (2) 구조 무결성 검사 — `check_integrity()`
`episode_index`가 중복 없이 `0..n-1` 연속인지 확인한다. 어긋나면 **치명적으로
중단**한다. 이건 메타데이터 보정으로 고칠 수 없고 재빌드만이 답이므로, 고쳐진
척하지 않는다. 변환 직후와 업로드 직전 두 번 돈다.

```
데이터셋이 구조적으로 깨져 있어 중단합니다:
  - episode_index 중복 4개 [117, 118, 119, 120] -- 서로 다른 에피소드가 같은 번호를 씁니다
  - episode_index 가 0..272 연속이 아닙니다 (범위 0~268, 273개)

/home/franka/lerobot_upload 를 지우고 --resume 없이 전체를 다시 만드세요.
```

### (3) 개수 보정 — `repair_metadata()`
`meta/episodes/*.parquet`을 **권위**로 `info.json`과 `stats.json`을 다시 계산한다.
에피소드당 한 행씩 길이와 통계를 갖고 있어 이게 유일한 진실이다. 낡은 씨앗이
변환을 넘어 살아남지 못한다.

### (4) 태그 검증 — `_verify_tag()`
업로드 뒤 `v3.0` 태그가 방금 커밋을 가리키는지 확인하고, 아니면 **종료 코드 1**로
끝낸다. 경고만 찍으면 무인 실행에서 스크롤을 타고 사라진다. `--replace` 경로는
`upload_folder`를 직접 쓰므로 거기서도 태그를 옮기도록 했다.

## 7. 이번 복구

로컬 변환본은 `episode_index`가 충돌해 있어 **패치가 아니라 재빌드**가 필요하다.

```bash
rm -rf ~/lerobot_upload
python scripts/convert/convert_libero_to_lerobot.py ~/libero_datasets/*_demo.hdf5 \
    --repo-id knu-physical-ai/fr3-pick-place-lerobot \
    --root ~/lerobot_upload                      # --resume 없이
python scripts/convert/convert_libero_to_lerobot.py \
    --repo-id knu-physical-ai/fr3-pick-place-lerobot \
    --root ~/lerobot_upload --push-only --replace --no-private
```

`--replace`가 필요한 이유: `push_to_hub`는 사라진 파일을 지우지 않으므로, 그냥
올리면 옛 청크(충돌하던 것 포함)가 원격에 남는다.

GUI에서는 `전체 처리`를 열어 **전체 재빌드**를 고르면 같은 일이 순서대로 실행된다.

## 8. 남는 교훈

**Hub 데이터셋을 손으로 고치면 안 된다.** `api.upload_file`로 메타데이터를 직접
고치는 것은 `main`만 바꾸고 태그를 남겨두므로, lerobot이 보는 세계와 실제가 갈라진다.
고쳐야 한다면 로컬을 고친 뒤 정상 업로드 경로로 올려서 태그가 함께 움직이게 한다.

**"개수가 맞다"는 무결성이 아니다.** 이번에 개수만 봤다면 `repair_metadata()`가
273으로 고쳐놓고 끝났을 것이고, 중복된 번호 4개는 학습 데이터에 그대로 들어갔을
것이다. 구조는 따로 검사해야 한다.

**긴 파이프라인의 마지막 단계는 특히 위험하다.** 앞이 다 성공한 뒤에 죽으면
"거의 다 됐다"로 읽히지만, 실제로는 커밋되지 않은 중간 상태가 남는다. 이번 경우
그게 태그였다.
