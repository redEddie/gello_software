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
- `instruction_id` 는 **scene 마다 독립**이다 (2026-08-13 결정): 각 scene 의
  첫 instruction 이 `I000` 이고 새 문장마다 하나씩 올라간다. slot 의 전역
  식별자는 `(scene_id, instruction_id)` 쌍 — `episode_uid` 가 그 형태다.
  같은 scene 안에서 한 ID 를 두 문장에 쓰는 것만 금지한다. "다른 scene +
  같은 instruction" 축(배치 일반화)은 ID 가 아니라 **문장 텍스트**를 대조해
  파생한다 (GUI 의 자동 배정이 문장을 보고 ID 를 채우므로 손으로 맞출
  필요 없음).
- `collected` 는 계획 파일에 넣지 않는다. 항상 scene 파일을 읽어 계산한다
  (`gello.scene.scene_format.count_by_slot`) — 계획 파일에 넣는 순간 두 개의 진실이
  생긴다.
- `target` 은 세션 안에서는 제약처럼 다룬다 — 채우지 못한 채 책상을 치우는
  것이 재수집의 가장 흔한 시작이다 (§6).

`example.json` 이 Notion §6 의 matrix 예시를 그대로 옮긴 것이다.
단 example.json 은 현 단계 §4 동사 집합 밖의 문장(위치 지칭 I004, `put`
I005)을 포함한 **향후 확장 예시**라 통일 문법 lint 대상이 아니다 — lint
기준은 실사용 계획(pilot*.json)이다 (2026-08-24 결정).
