"""스킬(동사×관계)별 누적 수집량 집계 + 부족 스킬 우선 지시문 랭킹.

scene 추천 개편(2026-08-26)의 "함정 3" 대응: 씬 간 거리가 아무리 멀어도
지시문이 씬에서 같은 규칙으로 결정되면 scene→task 상관이 남는다. 그래서
지시문은 씬(거리) 선택과 **분리된 단계**에서 정하되, 실행 가능한 스킬 중
지금까지 수집이 가장 적은 것을 앞세운다 — 씬이 무엇이든 "그 씬에서 할 수
있는 일 중 데이터가 부족한 일"이 우선이라, 씬 유형과 태스크의 결합이
체계적으로 흐트러진다.

집계 단위는 스킬(:data:`gello.scene.instruction_grammar.SKILLS`)이다. 문장 단위로
세면 색 조합마다 다른 문장이 되어 카운트가 흩어지고, 부족한 것은 보통
문장이 아니라 동사·관계이기 때문이다.

유일 지칭 검증은 여기서 하지 않는다 — :func:`enumerate_instructions` 가
모호 지칭 문장을 아예 생성하지 않고, 계획 등록 시 ``load_plan`` 의 lint 가
한 번 더 막는다. 거리 알고리즘과 무관하게 항상 걸려 있는 게이트다.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from gello.scene.instruction_grammar import enumerate_instructions, skill_of
from gello.scene.props import Prop
from gello.scene.scene_format import (
    SceneMetadata,
    iter_scene_files,
    list_scene_episodes,
)

#: 분류 불가 문장(정본 이전 legacy 등)의 집계 키.
UNKNOWN_SKILL = "?"


def collected_skill_counts(root: "Path | None") -> Counter:
    """데이터 루트의 모든 scene HDF5 를 훑어 스킬별 누적 에피소드 수를 센다.

    수집 중이라 잠긴 파일 등 읽기 실패는 건너뛴다 (부분 집계 > 실패).
    root 가 None 이거나 없으면 빈 Counter — 랭킹은 문장 사전순으로
    퇴화할 뿐 죽지 않는다.
    """
    counts: Counter = Counter()
    if root is None:
        return counts
    root = Path(root)
    if not root.exists():
        return counts
    for p in iter_scene_files(root):
        try:
            eps = list_scene_episodes(p)
        except Exception:  # noqa: BLE001 -- 세션이 쥔 파일 등
            continue
        for e in eps:
            counts[skill_of(str(e.get("instruction", ""))) or UNKNOWN_SKILL] += 1
    return counts


def rank_instructions(md: SceneMetadata, props: dict[str, Prop],
                      counts: Counter) -> list:
    """씬에서 실행 가능한 문장을 (스킬 누적 수집량 오름차순) 으로 랭킹한다.

    반환: [(sentence, skill, collected_count), ...]. 같은 스킬 안에서는
    문장 사전순 — 전 과정이 결정적이다. 문장 자체는
    :func:`enumerate_instructions` 산출 그대로라 유일 지칭 보장을 그대로
    물려받는다.
    """
    ranked = []
    for s in enumerate_instructions(md, props):
        sk = skill_of(s) or UNKNOWN_SKILL
        ranked.append((s, sk, int(counts.get(sk, 0))))
    ranked.sort(key=lambda t: (t[2], t[1], t[0]))
    return ranked


def format_skill_counts(counts: Counter) -> str:
    """로그·GUI 한 줄 요약: 'pick-on 132 · drawer-open 20 · ...' (적은 순)."""
    if not counts:
        return "수집 이력 없음"
    return " · ".join(f"{sk} {n}"
                      for sk, n in sorted(counts.items(),
                                          key=lambda kv: (kv[1], kv[0])))
