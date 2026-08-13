# 수집 계획 (slot plan)

Scene × Instruction 조합(slot)과 목표 에피소드 수를 GUI 에 주입하는 파일.
계획은 Notion(§6 matrix)에서 관리하고, 확정(freeze)된 것을 여기로 export 해서
커밋한다 — git 이력이 곧 "언제 계획이 바뀌었나"의 기록이다.

## 스키마

```json
{
  "plan_version": 1,
  "scenes": [
    {
      "scene_id": "S000",
      "note": "컵 2 + 노란 그릇 + 서랍장 (family B+E)",
      "slots": [
        {
          "instruction_id": "I000",
          "instruction": "pick up the blue cup and place it on the yellow bowl",
          "target": 10
        }
      ]
    }
  ]
}
```

규칙:

- `instruction` 은 따옴표 없는 순수 문장. freeze 후에는 문장을 고치지 않는다 —
  고쳐야 하면 새 `instruction_id` 를 만든다 (§6 freeze 게이트).
- 같은 `instruction_id` 를 여러 scene 에서 재사용하면 "다른 scene + 같은
  instruction" 축(배치 일반화)이 자동으로 생긴다. 같은 ID 는 항상 같은 문장.
- `collected` 는 계획 파일에 넣지 않는다. 항상 scene 파일을 읽어 계산한다
  (`gello.scene_format.count_by_slot`) — 계획 파일에 넣는 순간 두 개의 진실이
  생긴다.
- `target` 은 세션 안에서는 제약처럼 다룬다 — 채우지 못한 채 책상을 치우는
  것이 재수집의 가장 흔한 시작이다 (§6).

`example.json` 이 Notion §6 의 matrix 예시를 그대로 옮긴 것이다.
