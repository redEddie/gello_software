"""Compare curated local .hdf5 files against a published LeRobot dataset.

The pipeline needs one question answered before it can run unattended: can the
Hub copy be brought up to date by *appending*, or does it have to be rebuilt?

LeRobotDataset.resume() only ever appends -- there is no supported way to
remove an episode from a published dataset. So appending is correct exactly
when no already-pushed task has lost episodes since. If one has, the Hub copy
contains takes the operator deleted, and only a full rebuild + replace fixes
it. Getting this wrong is not visible afterwards: the dataset just quietly
contains bad demonstrations.

Counting is enough to decide. Both sides append in order and never reorder, so
a task whose local count is >= the Hub count has only grown; one whose count
dropped has had episodes removed. (A delete-then-record that lands back on the
same count is the one case counting misses -- flagged separately, see
``ambiguous``.)

개수가 못 가리는 경우는 **에피소드 길이 지문**으로 한 번 더 판별한다. 양쪽 다
에피소드별 프레임 수를 이미 기록하고 있다(.hdf5 의 demo attrs ``num_samples``,
Hub 메타 parquet 의 ``length``). 순수 append 이력이라면 Hub 의 길이 시퀀스는
로컬 시퀀스의 접두(prefix)와 정확히 일치한다 -- 20Hz 텔레옵에서 에피소드
길이가 우연히 같기는 어려우므로, 지우고 다시 찍었다면 어긋난다. 접두가
일치하면 "재압축 마커가 낡았을 뿐 손실 없음"으로 자동 판정하고, 어긋나면
개수만 볼 때보다 더 강하게(추가처럼 보이는 경우까지) 재빌드를 요구한다.

한계: 같은 위치에 우연히 같은 길이로 다시 찍힌 교체는 못 잡는다. 그리고
변환을 ``--only-success`` 로 돌렸다면 로컬 실패 에피소드가 Hub에 없어 지문이
어긋난다 -- 그 워크플로에서는 이 검증을 믿지 말 것.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import h5py

# Deleting an episode and recording another leaves the count unchanged, so the
# comparison cannot tell that apart from "nothing happened". The .hdf5 records
# how many episodes existed at the last repack, which is a second, independent
# witness: if that count differs from today's while the Hub count matches, the
# file was edited in a way counting alone would miss.
REPACK_COUNT_ATTR = "repacked_episodes"

# LeRobot 이 데이터셋을 읽을 때 사용하는 revision. lerobot 은
# CODEBASE_VERSION("v3.0") 태그로 모든 메타데이터를 조회하므로, 이 모듈도
# 같은 태그를 써야 main 과 갈라진 경우에 조용한 누락이 없다.
# dataset_sync 는 lerobot 을 직접 import 하지 않으므로(lerobot 없는 환경
# 에서도 돌아야 함) 상수만 동일 값으로 정의한다.
LEROBOT_TAG = "v3.0"


def local_tasks(data_root: str | Path) -> dict:
    """{language_instruction: {"episodes", "path", "at_repack", "lengths"}}.

    ``lengths`` 는 demo 인덱스 순서의 프레임 수 목록 -- 변환기가 에피소드를
    append 하는 순서와 같다(convert_libero_to_lerobot.py 의 demo 정렬과 동일).
    attrs 만 읽으므로 이미지 데이터는 건드리지 않는다.
    """
    out = {}
    for p in sorted(glob.glob(str(Path(data_root) / "*_demo.hdf5"))):
        try:
            with h5py.File(p, "r") as f:
                data = f["data"]
                info = data.attrs.get("problem_info")
                task = json.loads(json.loads(info)["language_instruction"]) if info else Path(p).stem
                at = data.attrs.get(REPACK_COUNT_ATTR)
                names = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
                lengths = []
                for n in names:
                    ns = data[n].attrs.get("num_samples")
                    lengths.append(int(ns) if ns is not None else -1)
                out[task] = {"episodes": len(names), "path": Path(p),
                             "paths": [Path(p)],
                             "at_repack": int(at) if at is not None else None,
                             "lengths": lengths}
        except Exception:  # noqa: BLE001
            continue
    # ---- scene-v1 파일의 기여. task = 에피소드별 instruction 이고, 변환이
    # success 만 내보내므로 개수도 success 기준이다. 같은 문장이 legacy
    # task 나 다른 scene 과 겹치면 합산한다. scene 기여가 섞인 task 는 길이
    # 지문 검증을 끈다(lengths=None) -- scene 쪽 resume 안전성은 개수
    # 산술이 아니라 변환기의 episode_uid 대조가 책임진다.
    for p in sorted(glob.glob(str(Path(data_root) / "scene_*.hdf5"))):
        try:
            from gello.scene_format import list_scene_episodes

            for ep in list_scene_episodes(Path(p)):
                if ep.get("quality_status") != "success":
                    continue
                task = ep["instruction"]
                e = out.get(task)
                if e is None:
                    out[task] = {"episodes": 1, "path": Path(p),
                                 "paths": [Path(p)], "at_repack": None,
                                 "lengths": None}
                else:
                    e["episodes"] += 1
                    if Path(p) not in e["paths"]:
                        e["paths"].append(Path(p))
                    e["lengths"] = None
        except Exception:  # noqa: BLE001 - 수집 세션이 잠근 파일 등
            continue
    return out


def hub_meta(repo_id: str) -> tuple[dict, dict, str]:
    """(counts, lengths, error) for a published LeRobot dataset.

    ``counts`` = {language_instruction: episode_count},
    ``lengths`` = {language_instruction: [프레임 수, ...] (episode_index 순)}.

    Returns ({}, {}, "") when the dataset does not exist yet -- a first push is
    a normal case, not an error. Any other failure comes back in the message so
    the caller can refuse to run rather than guess.
    """
    try:
        import pandas as pd
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import RepositoryNotFoundError, RevisionNotFoundError
    except ImportError as e:
        return {}, {}, f"의존성 없음: {e}"
    try:
        d = snapshot_download(repo_id, repo_type="dataset",
                              allow_patterns=["meta/*", "meta/**/*"],
                              revision=LEROBOT_TAG,
                              force_download=True)
    except (RepositoryNotFoundError, RevisionNotFoundError):
        # repo 가 없거나 태그가 아직 없는 신생 repo -- 빈 결과가 정상 케이스.
        return {}, {}, ""
    except Exception as e:  # noqa: BLE001
        return {}, {}, f"{type(e).__name__}: {e}"
    files = sorted(glob.glob(f"{d}/meta/episodes/**/*.parquet", recursive=True))
    if not files:
        return {}, {}, ""
    try:
        eps = pd.concat([pd.read_parquet(f) for f in files])
    except Exception as e:  # noqa: BLE001
        return {}, {}, f"메타 읽기 실패: {type(e).__name__}: {e}"
    # append 순서 = episode_index 순. parquet 파일 경계는 그 순서를 보장하지
    # 않으므로 명시적으로 정렬한다.
    if "episode_index" in eps.columns:
        eps = eps.sort_values("episode_index")
    counts: dict = {}
    lengths: dict = {}
    has_length = "length" in eps.columns
    for _, row in eps.iterrows():
        value = row["tasks"]
        names = [value] if isinstance(value, str) else list(value)
        for n in names:
            counts[n] = counts.get(n, 0) + 1
            if has_length:
                lengths.setdefault(n, []).append(int(row["length"]))
    return counts, lengths, ""


def hub_episode_uids(repo_id: str) -> tuple:
    """(uid 집합 | None, error). Hub 데이터셋의 ``meta/episode_uids.json``
    사이드카(변환기가 scene 에피소드마다 남기는 출처)를 읽는다.

    None = 사이드카가 없는 repo (legacy 수집분만 있는 데이터셋 등) -- "이
    에피소드가 올라가 있는가" 를 에피소드 단위로 판정할 수 없다는 뜻이고,
    호출자는 문장(task) 단위 판정으로 물러난다. 빈 집합과는 다르다.
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import RepositoryNotFoundError, RevisionNotFoundError
    except ImportError as e:
        return None, f"의존성 없음: {e}"
    try:
        d = snapshot_download(repo_id, repo_type="dataset",
                              allow_patterns=["meta/episode_uids.json"],
                              revision=LEROBOT_TAG,
                              force_download=True)
    except (RepositoryNotFoundError, RevisionNotFoundError):
        # repo 가 없거나 태그가 아직 없는 신생 repo -- 빈 집합이 정상 케이스.
        return set(), ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    p = Path(d) / "meta" / "episode_uids.json"
    if not p.exists():
        return None, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"사이드카 읽기 실패: {e}"
    uids: set = set()
    for e in data.values() if isinstance(data, dict) else []:
        if isinstance(e, dict) and e.get("episode_uid"):
            uids.add(str(e["episode_uid"]))
    return uids, ""


def hub_tasks(repo_id: str) -> tuple[dict, str]:
    """{language_instruction: episode_count} -- hub_meta 의 개수만 필요한 호출자용."""
    counts, _, error = hub_meta(repo_id)
    return counts, error


def plan_sync(data_root: str | Path, repo_id: str) -> dict:
    """What it would take to make ``repo_id`` match the curated local files.

    ``action`` is the recommendation:
      "up_to_date" -- nothing to do
      "resume"     -- only appends; cheap and safe
      "rebuild"    -- a pushed task lost episodes, so the Hub copy is wrong
                      until it is rebuilt and replaced
      "blocked"    -- the Hub state could not be read; refuse rather than guess
    """
    local = local_tasks(data_root)
    hub, hub_lengths, error = hub_meta(repo_id)
    rows, added, shrunk, ambiguous = [], 0, 0, []
    mismatch = 0

    def _prefix_ok(task: str, h: int) -> "bool | None":
        """Hub 의 길이 시퀀스가 로컬의 선두 h 개와 일치하는가. None = 검증 불가."""
        hl = hub_lengths.get(task)
        ll = local.get(task, {}).get("lengths")
        if not hl or ll is None or len(hl) != h or len(ll) < h or -1 in ll[:h]:
            return None
        return hl == ll[:h]

    for task in sorted(set(local) | set(hub)):
        l = local.get(task, {}).get("episodes", 0)
        h = hub.get(task, 0)
        row = {"task": task, "hub": h, "local": l, "delta": l - h, "note": ""}
        if h and l > h:
            # 추가처럼 보여도 선두가 어긋나 있으면 '지우고 더 찍은' 경우다 --
            # 이어붙이기는 스킵 개수만 세므로 엉뚱한 에피소드를 붙이게 된다.
            if _prefix_ok(task, h) is False:
                row["note"] = "이력 불일치 (선두가 Hub와 다름)"
                mismatch += 1
                ambiguous.append(task)
            else:
                added += l - h
                row["note"] = f"+{l - h} 추가"
        elif h and l < h:
            shrunk += h - l
            row["note"] = f"{h - l}개 삭제됨"
        elif not h:
            added += l
            row["note"] = "새 task"
        else:
            # 개수는 같다. 재압축 시점 개수와 다르면 '지우고 다시 찍은' 경우일
            # 수 있다 -- 길이 지문으로 한 번 더 가린다. 지문까지 일치하면
            # 마커가 낡았을 뿐이고(재압축 없이 수집->푸시를 반복한 흐름),
            # 어긋나면 진짜 편집이다.
            at = local.get(task, {}).get("at_repack")
            if at is not None and at != l:
                ok = _prefix_ok(task, h)
                if ok is True:
                    row["note"] = "일치 (이력 검증됨)"
                elif ok is False:
                    row["note"] = "이력 불일치 (편집 의심)"
                    ambiguous.append(task)
                else:
                    row["note"] = "개수 같음 (편집 흔적)"
                    ambiguous.append(task)
            else:
                row["note"] = "일치"
        rows.append(row)

    if error:
        action = "blocked"
    elif shrunk or mismatch:
        action = "rebuild"
    elif added:
        action = "resume"
    else:
        action = "up_to_date"
    return {"rows": rows, "action": action, "error": error,
            "added": added, "shrunk": shrunk, "mismatch": mismatch,
            "ambiguous": ambiguous,
            "local_total": sum(v["episodes"] for v in local.values()),
            "hub_total": sum(hub.values()),
            # 변환기에 넘길 파일 목록: legacy 정렬 + scene 정렬, 중복 제거.
            # (legacy 를 앞에 -- Hub 의 기존 순서가 legacy 선행이라 길이
            # 지문의 접두 비교가 성립한다)
            "paths": _ordered_paths(local)}


def _ordered_paths(local: dict) -> list:
    seen: set = set()
    legacy: list = []
    scene: list = []
    for v in local.values():
        for p in v.get("paths", [v["path"]]):
            s = str(p)
            if s in seen:
                continue
            seen.add(s)
            (scene if Path(s).name.startswith("scene_") else legacy).append(s)
    return sorted(legacy) + sorted(scene)
