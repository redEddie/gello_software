"""소품 인벤토리 로더 -- configs/scenes/props.yaml 이 정본이다.

scene metadata 의 ``objects``/``distractors`` 에는 색 이름이 아니라 여기 정의된
instance ID(``OBJ-CUP-BLU-01``)가 들어간다. 같은 파란 컵 두 개를 구분할 수
없으면 "컵이 두 개인 장면"과 "위치로 지칭해야 하는 장면"을 나중에 재현할 수
없다 (Notion 프로토콜 §3).

scene 다양성 추천(객체 조합·배치 거리 계산)도 이 인벤토리를 후보 공간으로
쓴다 -- 알고리즘이 다룰 수 있는 소품은 여기 등록된 것뿐이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

PROPS_PATH = Path(__file__).resolve().parents[2] / "configs" / "scenes" / "props.yaml"

# OBJ-<종류>[-<색>]-<번호>. 토큰은 대문자·숫자, 번호는 2자리 이상.
# 색 토큰은 서랍장처럼 색으로 구분할 일이 없는 소품에서 생략된다 (OBJ-DRAWER-01).
PROP_ID_RE = re.compile(r"^OBJ-[A-Z0-9]+(-[A-Z0-9]+)?-\d{2,}$")


@dataclass(frozen=True)
class Prop:
    id: str
    category: str
    color: str
    material: str
    retired: bool = False  # 교체로 은퇴한 ID. 새 scene 에는 못 쓰지만 기존 metadata 해석용으로 남는다.
    # 뒤 물체를 가리고 팔 경로를 막는 키 큰 소품인가 (2026-09-06). 배치 규칙
    # occludes_behind / robot_clearance 가 이 플래그로 대상을 고른다 -- 규칙에
    # category 를 하드코딩하지 않기 위한 물성 표시다. 높이를 cm 로 적지 않는
    # 이유: "몇 cm 부터 가리는가"라는 두 번째 임계값을 정할 근거가 없고,
    # 필요한 사실은 "가리는가" 하나뿐이다.
    tall: bool = False


#: (해석된 경로) -> (stat 지문, 파싱 결과). props.yaml 은 정적 설정인데
#: 문법 린트가 문장마다 조회해서 창 하나 띄우는 데 193번 읽고 파싱했다
#: (2026-09-05 프로파일: WorkspaceWindow.__init__ 2.6초 중 대부분). 파일이
#: 바뀌면 지문이 달라져 무효가 되므로 정본은 여전히 파일이다.
_PROPS_CACHE: dict[str, tuple[tuple, list["Prop"]]] = {}


def load_props(path: Path = PROPS_PATH) -> list[Prop]:
    """인벤토리 전체(은퇴 포함). 파일이 깨졌으면 조용히 넘어가지 않고 던진다 --
    인벤토리가 틀린 채 수집하는 것이 곧 재수집이다."""
    key = fp = None
    try:
        st = Path(path).stat()
        key, fp = str(Path(path).resolve()), (st.st_size, st.st_mtime_ns)
        hit = _PROPS_CACHE.get(key)
        if hit is not None and hit[0] == fp:
            return list(hit[1])       # Prop 은 frozen -- 목록만 새로 준다
    except OSError:
        pass
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    entries = data.get("props") or []
    props: list[Prop] = []
    seen: set[str] = set()
    for e in entries:
        p = Prop(
            id=str(e["id"]),
            category=str(e["category"]),
            color=str(e["color"]),
            material=str(e["material"]),
            retired=bool(e.get("retired", False)),
            tall=bool(e.get("tall", False)),
        )
        if not PROP_ID_RE.match(p.id):
            raise ValueError(f"{path}: 잘못된 prop ID 형식: {p.id!r}")
        if p.id in seen:
            raise ValueError(f"{path}: 중복 prop ID: {p.id!r}")
        seen.add(p.id)
        props.append(p)
    if not props:
        raise ValueError(f"{path}: props 목록이 비어 있다")
    if key is not None:
        _PROPS_CACHE[key] = (fp, list(props))
    return props


def props_by_id(path: Path = PROPS_PATH) -> dict[str, Prop]:
    return {p.id: p for p in load_props(path)}


def active_prop_ids(path: Path = PROPS_PATH) -> set[str]:
    """새 scene 에 쓸 수 있는(은퇴하지 않은) instance ID 집합."""
    return {p.id for p in load_props(path) if not p.retired}
