feat(ui): 현재 task 의 n/target 을 상시 표시한다 (9-2, 이슈 #38)

`tasks/_공통.md` 를 먼저 읽으세요.

## 문제

지금 수집 화면의 카운터는 **GUI 를 켠 순간부터의 누계**다. 조작자가 실제로
알아야 하는 것은 "지금 이 task 가 목표 대비 몇 개인가" 인데 그것을 알 수 없다.
GUI 를 재시작하면 0 으로 돌아가고, 에피소드를 지워도 줄지 않는다.

## 만들 것

수집 화면에 현재 (scene, instruction) 의 **수집 누계 / 계획 target** 을
상시 표시한다. 예: `S003 · I003 · 7/10`.

## 데이터는 이미 있다 -- 새로 세지 마세요

    gello/scene/scene_format.py  count_by_slot(path)
        -> {instruction_id: {"total": n, "usable": n}}
        usable 은 quality_status == "success" 만 센다. **target 과 비교하는
        것은 usable 이다** (실패한 테이크는 목표에 안 들어간다).

    plan.slots_for(scene_id) -> [PlanSlot(instruction_id, instruction, target)]

`apps/workspace/features/scene/ops.py` 의 `refresh_scene_combo` 근처에 이미
이 둘을 함께 읽어 문자열을 만드는 코드가 있다 (오른쪽 scene 패널). 그것을
참고하되 **그 코드를 옮기거나 고치지 마세요** -- 표시 자리가 다릅니다.

## 어디에 붙이나

`apps/workspace/features/collection/page.py` 의 수집 패널. `ep_progress`
(프레임 진행 막대) 위에 한 줄 라벨을 두세요. 이름은 `win.slot_counter`.

폰트는 주변보다 크게 (조작자가 리더암을 잡은 거리에서 읽어야 합니다).
target 에 도달하면 색으로 표시하세요 (초록). 넘으면 그대로 두되 숫자는
정확히 보여주세요 (11/10 처럼).

## 언제 갱신하나

**HDF5 실측이 정본입니다.** 다음 시점에 다시 읽으세요:

    scene 선택이 바뀔 때        (SceneOps)
    instruction/slot 선택이 바뀔 때 (ScenePlanningOps)
    에피소드를 저장한 뒤          (CollectionOps 의 저장 완료 자리)
    에피소드를 삭제한 뒤          (DatasetOps)
    수집 세션을 시작/종료할 때

갱신 함수 하나를 `CollectionOps` 에 두고(`refresh_slot_counter`), 위 자리에서
그것을 부르세요. **각자 세지 말고 한 곳에서만 셉니다.**

## 주의

- `count_by_slot` 은 HDF5 를 엽니다. 프레임마다 부르면 안 됩니다 -- 위에
  적은 시점에만 부르세요.
- 다른 프로세스가 파일을 쓰는 중이면 `BlockingIOError` 가 납니다.
  `scene/ops.py` 가 그것을 어떻게 처리하는지 보고 같은 방식으로 하세요
  (카운터를 비우고 넘어가되, 예외로 GUI 가 죽지 않게).
- scene 이 선택되지 않았거나 계획에 그 slot 이 없으면 target 이 없습니다.
  그때는 누계만 보여주세요 (`7` 처럼). 0/0 이나 7/None 같은 것을 보이지 마세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

23개 통과. 그리고 **테스트를 하나 추가하세요**: `tests/gui/test_slot_counter.py`

  - selftest 로 만든 scene 파일에 성공 2개 + 실패 1개를 넣고
    `count_by_slot` 이 usable=2, total=3 을 주는지
  - target 이 있는 계획과 맞춰 문자열이 `2/10` 형태가 되는지
  - target 이 없을 때 누계만 나오는지
  - **에피소드를 지운 뒤 숫자가 줄어드는지** (GUI 누계였다면 안 줄어든다 --
    이 검사가 이슈 #38 의 핵심이다)

  로봇도 화면도 없이 돌아야 합니다. `tests/gui/run_all.sh` 목록에 넣으세요.

## 보고

붙인 자리, 갱신 시점 목록, 새 테스트가 무엇을 붙잡는지.
