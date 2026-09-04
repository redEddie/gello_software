"""데이터셋 레벨 신원 정보 — <데이터셋 폴더>/dataset-identity.json.

데이터셋은 scene_NNN.hdf5 가 모인 폴더일 뿐이라, "이 폴더가 무슨
데이터셋인가"(이름·컨셉·HF repo)를 담을 곳이 없었다. 이 모듈이 그 메타
파일을 읽고 쓴다. 수집 계획(자연어 지시문·target)은 여기 넣지 않는다 --
그것은 같은 폴더의 instructions.json 이고, 정본은
gello/scene/collection_plan.py 가 읽는다 (목적별 분리, 2026-09-04 결정).

수명 주기가 다르다: identity 는 데이터셋 생성 때 한 번 쓰이고 거의 안
바뀌고, instructions.json 은 수집 중 수시로 편집된다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

from gello.data.dataset_schema import SCHEMA_VERSION
from gello.scene.collection_plan import load_plan
from gello.scene.scene_format import (
    count_by_slot,
    iter_scene_files,
    list_scene_episodes,
    scene_filename,
)

IDENTITY_FILENAME = "dataset-identity.json"
IDENTITY_FORMAT = "knu-dataset-identity/1"

# 수집 계획 파일의 고정 파일명 컨벤션. 폴더에 이 파일이 있으면 계획 기반
# 수집, 없으면 자유 입력이다 -- 포인터 필드를 두지 않는다.
PLAN_FILENAME = "instructions.json"

DEFAULT_DATASETS_PARENT = Path.home() / "libero_datasets"
DEFAULT_HF_NAMESPACE = "knu-physical-ai"

# HF repo name 과 같은 규칙 (dataset 이름 = 폴더명 = repo 명의 이름 부분).
DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_dataset_name(name: str) -> "str | None":
    """데이터셋 이름이 규칙에 맞지 않으면 이유를, 맞으면 None 을 돌려준다."""
    if not name:
        return "이름이 비어 있습니다."
    if not DATASET_NAME_RE.fullmatch(name):
        return ("영문·숫자로 시작하고 영문·숫자·.-_ 만 쓸 수 있습니다 "
                "(HF repo 이름 규칙).")
    return None


@dataclass
class DatasetIdentity:
    name: str
    hf_repo: str = ""
    concept: str = ""
    created: str = ""
    schema_version: str = SCHEMA_VERSION
    # 이 데이터셋을 모은 물리 셋업 (configs/stations/<이름>.yaml). 런처가
    # "이어서 하기" 때 기본 선택으로 되살린다 -- 스테이션이 여럿인 곳에서
    # 엉뚱한 로봇 IP 로 붙는 것을 막는다. 옛 데이터셋에는 없으므로 빈 문자열.
    station: str = ""

    def __post_init__(self) -> None:
        if not self.hf_repo:
            self.hf_repo = f"{DEFAULT_HF_NAMESPACE}/{self.name}"

    def to_dict(self) -> dict:
        return {"format": IDENTITY_FORMAT, **asdict(self)}

    @classmethod
    def from_dict(cls, d: dict, fallback_name: str = "") -> "DatasetIdentity":
        return cls(
            name=str(d.get("name") or fallback_name),
            hf_repo=str(d.get("hf_repo") or ""),
            concept=str(d.get("concept") or ""),
            created=str(d.get("created") or ""),
            schema_version=str(d.get("schema_version") or SCHEMA_VERSION),
            station=str(d.get("station") or ""),
        )


def identity_path(root: Path) -> Path:
    return Path(root) / IDENTITY_FILENAME


def plan_path(root: Path) -> Path:
    """이 데이터셋의 수집 계획 파일 경로 (있든 없든). 고정 파일명 컨벤션."""
    return Path(root) / PLAN_FILENAME


def load_identity(root: Path) -> Optional[DatasetIdentity]:
    """identity 를 읽는다. 없거나 깨졌으면 None — 메타가 없다고 수집을
    막을 일은 없다."""
    try:
        d = json.loads(identity_path(root).read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None
        return DatasetIdentity.from_dict(d, fallback_name=Path(root).name)
    except (OSError, ValueError):
        return None


def save_identity(root: Path, ident: DatasetIdentity) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity_path(root).write_text(
        json.dumps(ident.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


@dataclass
class DatasetEntry:
    """discover_datasets() 가 돌려주는, 발견된 데이터셋 하나의 요약."""
    path: Path
    identity: Optional[DatasetIdentity]
    scene_files: int
    episodes: int
    mtime: float = 0.0

    @property
    def name(self) -> str:
        return self.identity.name if self.identity else self.path.name


def _summarize(root: Path) -> DatasetEntry:
    scenes = iter_scene_files(root)
    episodes = 0
    mtime = 0.0
    for p in scenes:
        try:
            mtime = max(mtime, p.stat().st_mtime)
            episodes += len(list_scene_episodes(p))
        except Exception:  # noqa: BLE001 -- 잠긴 파일 등은 건너뛴다
            continue
    ident = load_identity(root)
    if ident is not None:
        try:
            mtime = max(mtime, identity_path(root).stat().st_mtime)
        except OSError:
            pass
    return DatasetEntry(path=root, identity=ident,
                        scene_files=len(scenes), episodes=episodes,
                        mtime=mtime)


def _looks_like_dataset(d: Path) -> bool:
    return identity_path(d).is_file() or bool(iter_scene_files(d))


def discover_datasets(candidate_roots: Iterable[Path]) -> list[DatasetEntry]:
    """후보 경로들에서 데이터셋 폴더를 찾는다.

    후보가 dataset-identity.json 이나 scene_*.hdf5 를 직접 들고 있으면 그
    자체가 데이터셋이고, 아니면 직계 자식들을 본다 (~/libero_datasets 같은
    부모 폴더를 넘기는 경우). 최신 수정일 순으로 돌려준다.
    """
    found: dict[Path, Path] = {}
    for c in candidate_roots:
        c = Path(c).expanduser()
        if not c.is_dir():
            continue
        if _looks_like_dataset(c):
            found.setdefault(c.resolve(), c)
            continue
        try:
            children = sorted(c.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and _looks_like_dataset(child):
                found.setdefault(child.resolve(), child)
    entries = []
    for path in found.values():
        try:
            entries.append(_summarize(path))
        except Exception:  # noqa: BLE001 -- 읽기 실패한 폴더는 건너뛴다
            continue
    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries


def plan_progress(root: Path) -> "tuple[int, int] | None":
    """이 데이터셋의 계획 대비 수집량 (usable 합, target 합). 계획
    (instructions.json)이 없거나 로드 실패면 None.

    마법사 목록 요약용 -- 세션 캐시는 다루지 않고 항상 파일 실측이다
    (count_by_slot, "두 개의 진실 금지").
    """
    pp = plan_path(root)
    if not pp.is_file():
        return None
    try:
        plan = load_plan(pp)
    except Exception:  # noqa: BLE001 -- 깨진 계획은 요약을 막지 않는다
        return None
    done = total = 0
    for sp in plan.scenes:
        total += sum(s.target for s in sp.slots)
        f = Path(root) / scene_filename(sp.scene_id)
        if not f.exists():
            continue
        try:
            counts = count_by_slot(f)
        except Exception:  # noqa: BLE001 -- 잠긴 파일 등
            continue
        done += sum(min(counts.get(s.instruction_id, {}).get("usable", 0),
                        s.target) for s in sp.slots)
    return done, total
