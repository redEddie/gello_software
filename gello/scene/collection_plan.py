"""수집 계획(slot plan) 로더 — 데이터셋 폴더 안의 instructions.json.

계획은 데이터셋에 귀속한다 (2026-09-04 결정): 각 데이터셋 폴더
(scene_NNN.hdf5 가 있는 곳) 안의 고정 파일명 instructions.json 이 그
데이터셋의 유일한 계획이다. 리포의 configs/collection/plans/ 에는 포맷
문서용 example.json 만 남는다. 파일명이 고정이라 dataset-identity.json
에 포인터 필드도 없다 -- 폴더에 instructions.json 이 있으면 계획 기반
수집, 없으면 자유 입력이다. 고정 파일명 상수는
gello/scene/dataset_meta.py 의 PLAN_FILENAME.

이 모듈은 그 파일을 읽고 규칙을 검증할 뿐, collected 를 계산하지
않는다 — 수집 현황은 항상 scene 파일에서 파생한다
(gello.scene.scene_format.count_by_slot, "두 개의 진실 금지").

규칙 (configs/collection/plans/README.md):
- instruction 은 따옴표 없는 순수 문장, freeze 후 불변 (고치려면 새 ID)
- instruction_id 는 **scene 마다 독립**이다: 각 scene 의 첫 instruction 이
  I000 이고 새 문장마다 하나씩 올라간다 (2026-08-13 사용자 결정). slot 의
  전역 식별자는 (scene_id, instruction_id) 쌍 -- episode_uid 가 그 형태다.
  같은 scene 안에서 같은 ID 가 다른 문장으로 쓰이는 것만 금지한다.
  "다른 scene, 같은 지시문"(배치 일반화 축)은 ID 가 아니라 문장 텍스트로
  대조해 파생한다.
- 동사는 §4 통제 집합(pick up … and place / open / close) 안 — 벗어나면
  로드는 되지만 경고를 남긴다 (freeze 리뷰가 잡을 것을 GUI 에서도 보이게)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gello.scene.instruction_grammar import lint as _grammar_lint
from gello.scene.scene_format import (
    INSTRUCTION_ID_RE,
    QUALITY_BAD_DATA,
    QUALITY_DEPRECATED,
    QUALITY_RETAKE,
    SCENE_ID_RE,
)

# 실제 수집 계획은 각 데이터셋 폴더의 instructions.json 에 둔다 (위 모듈
# docstring). 여기에는 포맷 문서용 example.json 과 README 만 남는다.
PLANS_DIR = Path(__file__).resolve().parents[2] / "configs" / "collection" / "plans"

# §4 동사 집합·문장 문법의 정본은 gello/instruction_grammar.lint 하나다.
# 예전에는 여기 동사 정규식(_ALLOWED_PATTERNS)이 따로 있었는데, 문법이 두
# 곳으로 갈리는 이중 진실이라 제거했다 (리뷰 반영 2026-08-24). 경고는
# 여전히 경고일 뿐 로드를 막지 않는다.


@dataclass(frozen=True)
class PlanSlot:
    instruction_id: str
    instruction: str
    target: int


@dataclass(frozen=True)
class ScenePlan:
    scene_id: str
    note: str
    slots: tuple


@dataclass
class CollectionPlan:
    path: Path
    version: int
    scenes: tuple
    warnings: list = field(default_factory=list)

    def scene(self, scene_id: str) -> Optional[ScenePlan]:
        return next((s for s in self.scenes if s.scene_id == scene_id), None)

    def slots_for(self, scene_id: str) -> tuple:
        sp = self.scene(scene_id)
        return sp.slots if sp is not None else ()


def list_plans(plans_dir: Path = PLANS_DIR) -> list:
    """커밋된 계획 파일 목록 (example.json 은 문서용이라 뒤로 보낸다)."""
    if not plans_dir.is_dir():
        return []
    files = sorted(plans_dir.glob("*.json"))
    return sorted(files, key=lambda p: (p.name == "example.json", p.name))


def load_plan(path: Path) -> CollectionPlan:
    """계획을 읽고 구조·규칙을 검증한다.

    구조가 틀리면(중복 ID 에 다른 문장, 따옴표 문장, ID 형식) ValueError --
    틀린 계획으로 수집하는 것이 곧 재수집이다. 동사 집합 위반은 경고로만
    남긴다(§4 는 운영 규칙이고, 예외 승인이 있을 수 있다).
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("plan_version") != 1:
        raise ValueError(f"{path.name}: plan_version 1 이 아니다")
    warnings: list = []
    scenes = []
    for s in raw.get("scenes", []):
        sid = str(s.get("scene_id", ""))
        if not SCENE_ID_RE.match(sid):
            raise ValueError(f"{path.name}: 잘못된 scene_id {sid!r}")
        slots = []
        sentence_by_id: dict = {}  # ID 는 scene 로컬 -- scene 마다 새로 센다
        for sl in s.get("slots", []):
            iid = str(sl.get("instruction_id", ""))
            instr = str(sl.get("instruction", "")).strip()
            target = int(sl.get("target", 0))
            if not INSTRUCTION_ID_RE.match(iid):
                raise ValueError(f"{path.name}: 잘못된 instruction_id {iid!r} ({sid})")
            if not instr or (instr.startswith('"') and instr.endswith('"')):
                raise ValueError(
                    f"{path.name}: {sid}/{iid} instruction 은 따옴표 없는 순수 문장이어야 한다")
            if target <= 0:
                raise ValueError(f"{path.name}: {sid}/{iid} target 은 양수여야 한다")
            prev = sentence_by_id.get(iid)
            if prev is not None and prev != instr:
                raise ValueError(
                    f"{path.name}: {sid} 안에서 {iid} 가 서로 다른 문장으로 "
                    f"쓰였다 -- {prev!r} vs {instr!r} (ID 는 scene 안에서 유일)")
            sentence_by_id[iid] = instr
            gerr = _grammar_lint(instr)
            if gerr:
                warnings.append(
                    f"{sid}/{iid}: 통일 문법(§4 동사 집합 포함) 경고 -- {gerr}: {instr!r}")
            slots.append(PlanSlot(iid, instr, target))
        scenes.append(ScenePlan(scene_id=sid, note=str(s.get("note", "")),
                                slots=tuple(slots)))
    return CollectionPlan(path=path, version=1, scenes=tuple(scenes),
                          warnings=warnings)


def check_scene_against_plan(plan: CollectionPlan, scene_id: str,
                             episodes: list) -> list:
    """scene 파일의 에피소드가 계획과 어긋난 곳을 찾는다.

    반환: 경고 문자열 목록. 잡는 것:
    - 계획에 있는 ID 인데 파일의 문장이 계획과 다름 (ID-문장 규칙 위반 --
      실데이터에서 실제로 발생했다: I000 에 두 문장)
    - 계획에 없는 ID 로 기록된 에피소드 (계획 밖 수집)
    """
    out: list = []
    slots = {s.instruction_id: s.instruction for s in plan.slots_for(scene_id)}
    seen: set = set()
    for ep in episodes:
        # 큐레이션에서 뺀 에피소드(bad_data 등)는 계획 대조 대상이 아니다 --
        # 계획 밖 slot 을 폐기 처리하면 경고도 함께 사라져야 한다.
        if ep.get("quality_status") in (QUALITY_BAD_DATA, QUALITY_RETAKE, QUALITY_DEPRECATED):
            continue
        iid = ep.get("instruction_id", "")
        instr = ep.get("instruction", "")
        key = (iid, instr)
        if key in seen:
            continue
        seen.add(key)
        if iid in slots and instr != slots[iid]:
            out.append(f"{ep.get('name', '?')}: {iid} 문장이 계획과 다름 -- "
                       f"파일 {instr!r} vs 계획 {slots[iid]!r}")
        elif iid not in slots:
            out.append(f"{ep.get('name', '?')}: 계획에 없는 slot {iid} ({instr!r})")
    return out
