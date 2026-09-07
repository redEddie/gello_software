"""무엇이 아직 덜 찍혔나 -- 데이터셋 전체의 목표 미달 목록.

"다음에 무엇을 찍지" 에 GUI 가 답하지 못했다. 아는 것이 둘뿐이었다:

    런처의 데이터셋 목록   합계만 (계획 1210/1290, 93%)
    [다음 미수집 지시문]   지금 고른 scene **안에서만**

그래서 "어디가 몇 개 모자란가" 는 scene 을 하나씩 열어 카운터를 읽어야 했다.
파일을 전부 읽으면 몇 초에 나오는 답이다.

세는 값은 ``count_by_slot`` 의 **usable** 이다 (quality_status == success).
목표와 견주는 것은 그 값이지 파일에 있는 에피소드 수가 아니다 -- 실패로
판정한 것을 채운 것으로 세면 지우는 순간 목표가 다시 미달이 된다.

여기서 하지 않는 것: 고치기. 이 닥터의 처방은 "더 찍어라" 뿐이고, 그것은
GUI 가 조작자를 그 scene 으로 데려가는 것으로 끝난다. 파일은 건드리지
않는다 (기록 닥터와 다른 점이다).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gello.scene.collection_plan import load_plan
from gello.scene.scene_format import (
    count_by_slot,
    read_scene_metadata,
    scene_filename,
)


@dataclass(frozen=True)
class Shortfall:
    """목표를 못 채운 지시문 하나."""

    scene_id: str
    instruction_id: str
    instruction: str
    usable: int
    target: int
    #: 그 scene 파일이 아직 없으면 True (계획에만 적혀 있다).
    missing_file: bool
    #: 그 파일의 스키마 버전. 이어 찍으면 이 버전이 유지된다.
    version: str
    #: 파일을 읽지 못했다. 그러면 **수를 모르는 것이지 0개가 아니다** --
    #: 0 으로 세면 닥터가 "전부 다시 찍어라" 라고 말한다.
    unreadable: bool = False
    #: 못 읽은 사유 (사람 말). 잠김인지 깨짐인지 구별해야 다음 수가 다르다.
    reason: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.target - self.usable)


@dataclass(frozen=True)
class Progress:
    """데이터셋 전체 요약 + 미달 목록 (부족한 것부터)."""

    usable: int
    target: int
    tasks: int
    shortfalls: tuple

    @property
    def percent(self) -> int:
        return 0 if not self.target else self.usable * 100 // self.target


def scan(root: Path, plan_path: Path) -> Progress:
    """계획의 모든 지시문을 scene 파일과 대조한다.

    파일이 없는 scene 도 목록에 남긴다 -- "계획에 적었는데 아직 안 만들었다"
    는 흔한 상태이고, 그것도 "덜 찍혔다" 의 한 경우다. 지우고 시작하는 것과
    구별되어야 해서 ``missing_file`` 로 표시한다.
    """
    plan = load_plan(Path(plan_path))
    usable = target = tasks = 0
    out: list[Shortfall] = []
    for sp in plan.scenes:
        path = Path(root) / scene_filename(sp.scene_id)
        # **잠긴 파일에서 죽지 않는다.** 이 닥터는 데이터셋의 모든 scene 을
        # 여는데, 수집 중이면 그중 하나는 다른 프로세스가 쓰고 있다 (h5py 가
        # BlockingIOError 를 낸다). 그 하나 때문에 나머지 진행을 못 보면
        # 쓸모가 없다 -- 못 읽은 것은 못 읽었다고 적고 넘어간다.
        counts: dict = {}
        version = ""
        reason = ""
        if path.is_file():
            try:
                counts = count_by_slot(path)
                version = read_scene_metadata(path).dataset_version
            except OSError:
                # 다른 프로세스가 쓰는 중 -- 지금 찍고 있는 scene 이다.
                reason = "다른 프로세스가 쓰는 중"
                version = "?"
            except Exception as e:  # noqa: BLE001
                # 깨졌거나 형식이 다르다. 이것도 "0개" 가 아니다.
                reason = f"{type(e).__name__}: {e}"
                version = "?"
        for slot in sp.slots:
            n = counts.get(slot.instruction_id, {}).get("usable", 0)
            tasks += 1
            target += slot.target
            # 못 읽은 파일은 진행률 분자에 넣지 않는다. 분모에도 넣지 않으면
            # 전체 비율이 부풀어 "거의 다 됐다" 로 보인다 -- 모르는 것은
            # 모른다고 두고, 그 줄이 목록에 사유와 함께 남는다.
            if not reason:
                usable += min(n, slot.target)
            if reason or n < slot.target:
                out.append(Shortfall(
                    sp.scene_id, slot.instruction_id, slot.instruction,
                    n, slot.target, not path.is_file(), version,
                    bool(reason), reason))
    # 많이 모자란 것부터. 같으면 scene·지시문 번호 순으로 -- 결정적이어야
    # 화면이 새로고침될 때마다 줄이 튀지 않는다.
    out.sort(key=lambda s: (-s.remaining, s.scene_id, s.instruction_id))
    return Progress(usable, target, tasks, tuple(out))


def version_spread(root: Path, plan_path: Path) -> "dict[str, list[str]]":
    """스키마 버전 -> 그 버전인 scene 들. 이어 찍기의 결과를 미리 말하기 위한 것.

    이어 찍으면 그 파일의 버전이 유지된다 (SceneWriter 는 안전하지 않은 상승을
    거부한다). 지금은 그 사실을 **연결한 뒤 로그 한 줄로** 알게 된다.
    """
    plan = load_plan(Path(plan_path))
    out: dict[str, list[str]] = {}
    for sp in plan.scenes:
        path = Path(root) / scene_filename(sp.scene_id)
        if not path.is_file():
            continue
        try:
            v = read_scene_metadata(path).dataset_version
        except Exception:  # noqa: BLE001 -- 잠긴 파일도 여기서는 건너뛴다
            continue
        out.setdefault(v, []).append(sp.scene_id)
    return out
