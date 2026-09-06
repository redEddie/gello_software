"""수집 세션 이력 — <state_dir>/collection_history.jsonl.

"오늘 몇 개 찍었나"는 GUI 를 닫는 순간 사라졌다. 카운터는 메모리에만 있고,
데이터셋 파일은 **몇 개가 있는가**는 알려줘도 **어느 속도로 쌓였는가**는
알려주지 않는다 (파일에는 에피소드마다 timestamp 가 있지만, 그건 저장 시각
이지 자리에 앉아 있던 시간이 아니다 -- 쉬는 시간과 씬 재배치가 통째로
빠진다). 조작자가 알고 싶어 한 것은 후자다: "이 속도가 정상인가."

그래서 한 줄 = **연결 세션 하나**(Connect -> Disconnect) 로 남긴다. 그
구간이 시작과 끝이 분명한 유일한 단위다. GUI 실행 하나는 ``run`` 이 같은
줄들의 묶음이라, "이번에 켜고 몇 개 찍었나"는 그 묶음의 합이다.

JSONL 인 이유: 세션이 끝날 때마다 한 줄 append 만 하면 되므로, 다음 세션이
쓰는 도중에 GUI 가 죽어도 앞의 줄들은 멀쩡하다. 읽을 때 깨진 줄은 건너뛴다.

여기 있는 숫자는 전부 그 세션에서 **실제로 센 것**이다. 분당 속도처럼
파생되는 값은 저장하지 않는다 (``SessionRecord.per_minute``) -- 저장하면
세는 규칙을 고칠 때 옛 줄이 조용히 다른 뜻이 된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from gello.config.paths import state_dir

HISTORY_FILENAME = "collection_history.jsonl"

#: 분당 속도를 의미 있게 계산할 수 있는 최소 세션 길이. 30초짜리 세션에서
#: 한 개를 찍으면 "분당 2개"가 되는데, 그건 속도가 아니라 반올림 오차다
#: (episode_stats 의 30초 문턱과 같은 이유).
MIN_RATE_SECONDS = 60.0


def history_path() -> Path:
    return state_dir() / HISTORY_FILENAME


@dataclass
class SessionRecord:
    """연결 세션 하나. 필드는 전부 그 세션에서 실제로 센 값이다."""

    #: GUI 실행 식별자. 같은 실행에서 여러 번 Connect 하면 이 값이 같다.
    run: str = ""
    started: str = ""
    ended: str = ""
    collector: str = ""
    #: 데이터셋 폴더 이름 (전체 경로가 아니라 -- 경로는 기계마다 다르고,
    #: 비교하고 싶은 것은 "무슨 데이터셋을 찍었나"다).
    dataset: str = ""
    scene: str = ""
    station: str = ""
    saved: int = 0
    success: int = 0
    failed: int = 0
    discarded: int = 0
    frames: int = 0
    #: 세션이 살아 있던 시간(초). 자리에 앉아 있던 시간이라 리셋·재배치·
    #: 잡담이 전부 들어 있다 -- 그래서 "정상 속도인가"에 답이 된다.
    seconds: float = 0.0

    @property
    def per_minute(self) -> float:
        """분당 저장 에피소드. 너무 짧은 세션은 0 (속도라고 부를 수 없다)."""
        if self.seconds < MIN_RATE_SECONDS:
            return 0.0
        return self.saved / (self.seconds / 60.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionRecord":
        return cls(
            run=str(d.get("run") or ""),
            started=str(d.get("started") or ""),
            ended=str(d.get("ended") or ""),
            collector=str(d.get("collector") or ""),
            dataset=str(d.get("dataset") or ""),
            scene=str(d.get("scene") or ""),
            station=str(d.get("station") or ""),
            saved=int(d.get("saved") or 0),
            success=int(d.get("success") or 0),
            failed=int(d.get("failed") or 0),
            discarded=int(d.get("discarded") or 0),
            frames=int(d.get("frames") or 0),
            seconds=float(d.get("seconds") or 0.0),
        )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_run_id() -> str:
    """GUI 실행 하나의 이름. 사람이 로그에서 알아볼 수 있게 시각 + PID."""
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"


def append_session(rec: SessionRecord, path: Path | None = None) -> None:
    """세션 한 줄을 이력 파일 끝에 붙인다.

    이력을 남기지 못하는 것이 수집을 막을 이유는 못 된다 -- 디스크가 꽉
    찼거나 권한이 없으면 조용히 넘어간다 (부르는 쪽이 로그를 남긴다).
    """
    p = path or history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")


def load_sessions(path: Path | None = None) -> list[SessionRecord]:
    """이력 전부를 오래된 것부터. 깨진 줄은 건너뛴다 -- 한 줄이 잘렸다고
    나머지 이력을 잃을 이유가 없다."""
    p = path or history_path()
    out: list[SessionRecord] = []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(SessionRecord.from_dict(d))
    return out


@dataclass
class CollectorPace:
    """수집자 한 사람의 누적. 순위표 한 줄."""

    collector: str
    sessions: int = 0
    saved: int = 0
    success: int = 0
    failed: int = 0
    seconds: float = 0.0
    datasets: set = field(default_factory=set)
    last: str = ""

    @property
    def per_minute(self) -> float:
        if self.seconds < MIN_RATE_SECONDS:
            return 0.0
        return self.saved / (self.seconds / 60.0)

    @property
    def success_rate(self) -> float:
        done = self.success + self.failed
        return self.success / done if done else 0.0


def leaderboard(rows: Iterable[SessionRecord],
                dataset: str = "") -> list[CollectorPace]:
    """수집자별로 묶어 분당 속도가 빠른 순으로.

    ``dataset`` 을 주면 그 데이터셋의 세션만 센다 -- 데이터셋마다 task 가
    다르고 한 에피소드에 드는 시간도 다르므로, 서로 다른 데이터셋의 속도를
    한 줄에 세우면 비교가 아니라 착시가 된다.

    이름이 빈 세션은 ``(이름 없음)`` 으로 묶는다. 지우지 않는 이유: 수집자
    칸을 안 채우고 찍은 세션도 시간은 실제로 썼고, 그것이 안 보이면 합계가
    맞지 않는다.
    """
    by: dict[str, CollectorPace] = {}
    for r in rows:
        if dataset and r.dataset != dataset:
            continue
        name = r.collector or "(이름 없음)"
        pace = by.setdefault(name, CollectorPace(collector=name))
        pace.sessions += 1
        pace.saved += r.saved
        pace.success += r.success
        pace.failed += r.failed
        pace.seconds += r.seconds
        if r.dataset:
            pace.datasets.add(r.dataset)
        if r.ended > pace.last:
            pace.last = r.ended
    # 속도가 같으면 많이 찍은 사람이 위로 -- 60초 미만 세션만 있는 사람은
    # 속도가 0 이라 아래로 모인다.
    return sorted(by.values(), key=lambda p: (-p.per_minute, -p.saved))


def runs(rows: Iterable[SessionRecord]) -> list[tuple[str, list[SessionRecord]]]:
    """GUI 실행 단위로 묶는다 (최근 실행이 앞). 한 실행 = "켜고 나서" 하나."""
    order: list[str] = []
    by: dict[str, list[SessionRecord]] = {}
    for r in rows:
        if r.run not in by:
            by[r.run] = []
            order.append(r.run)
        by[r.run].append(r)
    return [(k, by[k]) for k in reversed(order)]
