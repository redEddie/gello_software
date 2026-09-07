"""파일에 찍힌 버전과 그 파일의 내용이 맞는가.

scene 파일은 자기 스키마 버전을 metadata 에 적어 둔다. 그 버전이 내용과
어긋나면 검증이 통째로 실패하는데, 지금은 터미널에서 check_scene_file.py 를
돌려야만 보인다. S015 가 그랬다 -- knu-1.1.0 으로 찍혀 있는데 그 버전이
요구하는 필드가 없어서 40 에피소드가 전부 검증 실패였고, 고치는 데 손으로 짠
스크립트가 필요했다.

**내리기는 무손실이다.** 검증기가 여분의 필드를 문제 삼지 않으므로
(check_scene_file.py 의 "여분의 필드는 문제 삼지 않는다"), 내용이 목표 버전을
넘어서도 그 버전으로 설명할 수 있다. 데이터를 버려야 하는 것은 **올릴 때**뿐
이다 -- 새 버전이 요구하는 필드가 없는 에피소드는 지우는 길밖에 없다.

**그래서 버전 통일을 권하지 않는다.** 1.2.0 파일을 1.0.0 으로 내리면 힘·토크
필드는 파일에 그대로 있는데 버전이 그 존재를 더 이상 알리지 않는다. 버전을
보고 읽는 소비자는 있는 데이터를 안 읽게 된다. 섞여 있는 것 자체는 이미
처리된다 (변환기는 여분 필드를 허용하고, SceneWriter 는 안전하지 않은 버전
상승을 거부한다).

이 모듈이 하는 일은 셋이다: 분포를 보여주고, 데이터세트 버전과 내용의 어긋남을 찾고,
**안전한 버전 변경만** 제안한다. 내용이 만족하지 못하는 버전을 찍는 길은
두지 않는다 -- 그것이 knu-1.1.0 사고를 만든 조작이다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import h5py

from gello.data.dataset_schema import (
    SCHEMA_FIELDS,
    normalize_schema_version,
    schema_version_key,
)
from gello.scene.scene_format import read_scene_metadata


@dataclass(frozen=True)
class Diagnosis:
    """한 scene 파일의 버전 진단."""

    scene_id: str
    #: 파일에 찍힌 버전.
    stamped: str
    #: 내용이 실제로 만족하는 **가장 높은** 버전. 하나도 못 만족하면 "".
    satisfied: str
    episodes: int
    #: 그 버전이 요구하는데 없는 필드 -> 그 필드가 없는 에피소드 수.
    #: metadata attr 은 에피소드 수 대신 -1 로 적는다 (파일 전체의 사실이다).
    missing: dict
    #: 못 읽었으면 사유. 그러면 나머지 값은 뜻이 없다.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.missing

    @property
    def can_restamp(self) -> bool:
        """내용이 만족하는 버전이 있으면 그것으로 다시 찍을 수 있다."""
        return bool(self.satisfied) and self.satisfied != self.stamped


def _versions() -> list:
    """낮은 것부터. 별칭은 정규화된 이름으로만 다룬다."""
    return sorted(SCHEMA_FIELDS, key=schema_version_key)


def _missing_for(f: h5py.File, version: str) -> dict:
    """그 버전 기준으로 빠진 것. {필드: 그것이 없는 에피소드 수}.

    metadata attr 은 파일 전체의 사실이라 개수 대신 -1 을 적는다 -- 에피소드
    하나하나의 문제가 아니라 파일 하나의 문제이고, 고치는 방법도 다르다
    (지우는 것이 아니라 값을 채운다).
    """
    need = SCHEMA_FIELDS[version]
    out: dict = {}
    meta = f["metadata"]
    for attr in need.get("metadata_attrs", ()):
        if attr not in meta.attrs:
            out[f"metadata/{attr}"] = -1
    eps = [k for k in f if k.startswith("episode")]
    for name in eps:
        g = f[name]
        for ds in need.get("episode_datasets", ()):
            if ds not in g:
                out[ds] = out.get(ds, 0) + 1
        obs = g.get("obs")
        for ds in need.get("obs_datasets", ()):
            if obs is None or ds not in obs:
                out[f"obs/{ds}"] = out.get(f"obs/{ds}", 0) + 1
        for attr in need.get("episode_attrs", ()):
            if attr not in g.attrs:
                out[f"attrs/{attr}"] = out.get(f"attrs/{attr}", 0) + 1
    return out


def diagnose(path: Path) -> Diagnosis:
    """이 파일의 데이터세트 버전과 내용을 대조한다."""
    try:
        md = read_scene_metadata(Path(path))
    except OSError as e:
        return Diagnosis(Path(path).stem, "", "", 0, {},
                         f"다른 프로세스가 쓰는 중 ({e.__class__.__name__})")
    except Exception as e:  # noqa: BLE001
        return Diagnosis(Path(path).stem, "", "", 0, {}, f"{type(e).__name__}: {e}")

    stamped = normalize_schema_version(md.dataset_version)
    try:
        with h5py.File(Path(path), "r") as f:
            eps = sum(1 for k in f if k.startswith("episode"))
            missing = _missing_for(f, stamped) if stamped in SCHEMA_FIELDS else {}
            # 내용이 만족하는 **가장 높은** 버전. 높은 것부터 보고 처음
            # 통과하는 것을 쓴다.
            satisfied = ""
            for v in reversed(_versions()):
                if not _missing_for(f, v):
                    satisfied = v
                    break
    except OSError as e:
        return Diagnosis(md.scene_id, stamped, "", 0, {},
                         f"다른 프로세스가 쓰는 중 ({e.__class__.__name__})")
    except Exception as e:  # noqa: BLE001
        return Diagnosis(md.scene_id, stamped, "", 0, {},
                         f"{type(e).__name__}: {e}")
    if stamped not in SCHEMA_FIELDS:
        missing = {f"(모르는 버전 {stamped})": -1}
    return Diagnosis(md.scene_id, stamped, satisfied, eps, missing)


def restamp(path: Path, version: str) -> None:
    """데이터세트 버전을 다시 적는다. **내용이 만족하는 버전만** 받는다.

    내용이 못 따라가는 버전을 찍는 길은 두지 않는다 -- 그것이 knu-1.1.0
    사고를 만든 조작이고, 한 번으로 스키마 버전 하나가 폐기되고 scene 하나가
    재수집됐다. 버튼으로 닿을 수 있게 두지 않는다.
    """
    version = normalize_schema_version(version)
    if version not in SCHEMA_FIELDS:
        raise ValueError(f"모르는 스키마 버전: {version}")
    with h5py.File(Path(path), "r+") as f:
        bad = _missing_for(f, version)
        if bad:
            raise ValueError(
                f"{version} 이 요구하는 것이 없다: "
                + ", ".join(sorted(bad)[:4])
                + (" ..." if len(bad) > 4 else ""))
        f["metadata"].attrs["dataset_version"] = version


def known_payload(root: Path) -> "tuple[float, list] | None":
    """이 데이터셋의 다른 scene 에 적힌 부하 모델. 없으면 None.

    사후에 채울 때 어디서 값을 가져오는가 -- 로봇에 물어보는 것이 정본이지만
    노드가 안 떠 있을 수 있고, **같은 리그에서 같은 시기에 찍은 파일에 그 값이
    이미 적혀 있다**. 그것을 기본값으로 제시하고 사람이 확인한다.

    추측이 아니다. 그 파일들은 그 값으로 수집됐고 그 사실이 파일에 남아 있다.
    여러 값이 섞여 있으면 None -- 그때는 어느 것이 맞는지 파일이 말해 주지
    않으므로 사람이 직접 넣어야 한다.
    """
    from gello.scene.scene_format import iter_scene_files

    seen: dict = {}
    for path in iter_scene_files(Path(root)):
        try:
            md = read_scene_metadata(path)
        except Exception:  # noqa: BLE001
            continue
        if md.payload_mass is None:
            continue
        # float32 로 저장된 파일이 있어 소수 6자리에서 끊어 같은 값으로 본다.
        key = (round(float(md.payload_mass), 6),
               tuple(round(float(x), 6) for x in (md.payload_com or [])))
        seen[key] = seen.get(key, 0) + 1
    if len(seen) != 1:
        return None
    (mass, com), _n = next(iter(seen.items()))
    return mass, list(com)


def known_reset_pose(root: Path) -> "tuple[str, list] | None":
    """이 데이터셋의 다른 scene 에 적힌 리셋 자세. 없거나 섞였으면 None.

    known_payload 와 같은 근거다 -- 같은 리그에서 같은 시기에 찍은 파일에
    남아 있는 사실이지 추측이 아니다. 파일에 하나도 없으면 station 설정에서
    가져오는 것은 **호출하는 쪽**이 한다 (그것은 지금 설정이라 출처가 다르고,
    화면이 그 차이를 말해야 한다).
    """
    from gello.scene.scene_format import iter_scene_files

    seen: dict = {}
    for path in iter_scene_files(Path(root)):
        try:
            md = read_scene_metadata(path)
        except Exception:  # noqa: BLE001
            continue
        if not (md.reset_pose and md.reset_qpos):
            continue
        key = (str(md.reset_pose),
               tuple(round(float(x), 9) for x in md.reset_qpos))
        seen[key] = seen.get(key, 0) + 1
    if len(seen) != 1:
        return None
    (name, qpos), _n = next(iter(seen.items()))
    return name, list(qpos)


def fill_reset_pose(path: Path, name: str, qpos: list) -> str:
    """리셋 자세를 채우고, 그러고 나서 만족하는 버전으로 다시 적는다."""
    from gello.data.dataset_schema import META_RESET_POSE, META_RESET_QPOS

    import json as _json

    if not name or not qpos or len(qpos) != 7:
        raise ValueError("리셋 자세는 이름 하나와 7관절 값이 필요하다")
    with h5py.File(Path(path), "r+") as f:
        f["metadata"].attrs[META_RESET_POSE] = str(name)
        f["metadata"].attrs[META_RESET_QPOS] = _json.dumps(
            [float(x) for x in qpos])
    after = diagnose(Path(path))
    if after.satisfied and after.satisfied != after.stamped:
        restamp(Path(path), after.satisfied)
        return after.satisfied
    return after.stamped


def fill_payload(path: Path, mass: float, com: list) -> str:
    """부하 모델을 채우고, 그러고 나서 만족하는 버전으로 다시 찍는다.

    채우기와 버전 변경을 한 연산으로 묶는다 -- 따로 두면 "채웠는데 버전은
    그대로" 인 파일이 남고, 그것은 고치기 전과 똑같이 검증에 걸린다.

    돌려주는 값은 새로 찍힌 버전.
    """
    from gello.data.dataset_schema import META_PAYLOAD_COM, META_PAYLOAD_MASS

    import json as _json

    if mass is None or not com or len(com) != 3:
        raise ValueError("부하 모델은 질량 하나와 무게중심 셋(x, y, z)이 필요하다")
    with h5py.File(Path(path), "r+") as f:
        f["metadata"].attrs[META_PAYLOAD_MASS] = float(mass)
        f["metadata"].attrs[META_PAYLOAD_COM] = _json.dumps(
            [float(x) for x in com])
    after = diagnose(Path(path))
    if after.satisfied and after.satisfied != after.stamped:
        restamp(Path(path), after.satisfied)
        return after.satisfied
    return after.stamped


#: 리셋 자세에서 이만큼 벗어나면 짚어 본다.
#:
#: **±5도** 다 (2026-09-07 사용자). 초기 자세를 조금씩 흔들어 찍는 운용을
#: 감안한 값이다 -- 그 흔들림까지 잡으면 목록이 잡음으로 덮인다.
#:
#: 실측(fr3-tabletop 1199 에피소드)에서 대부분은 0.001 rad 안에 들어오고,
#: 벗어난 것은 2~4도짜리 넷과 **42.5도·54.1도짜리 둘**이었다. 5도 문턱은
#: 앞의 넷을 통과시키고 뒤의 둘만 남긴다 -- 그 둘은 리셋이 안 된 채 찍힌
#: 것이고, 스키마도 지시문도 개수도 맞아서 다른 어떤 검사에도 안 걸린다.
RESET_TOLERANCE_DEG = 5.0
RESET_TOLERANCE = math.radians(RESET_TOLERANCE_DEG)


def reset_drift(path: Path, expected: "list | None" = None) -> "dict | None":
    """적힌 리셋 자세와 **실제로 찍힌 첫 프레임**을 맞대어 본다.

    이것이 진짜 대조다. station 설정과 파일 metadata 를 맞대는 것은 장부끼리
    맞추는 것이라, 설정이 나중에 바뀌면 옛 파일이 원래 달라도 되는데 오탐이
    난다 (2026-09-07 사용자 지적). 파일이 "여기서 출발했다" 고 적어 두고
    데이터는 다른 데서 시작하는 것 -- 그것만이 어긋남이다.

    ``expected`` 를 주지 않으면 파일에 적힌 ``reset_qpos`` 를 쓴다. 그것도
    없으면 None (비교할 기준이 없다 -- 지금 station 값을 갖다 쓰는 것은
    **부르는 쪽**이 정할 일이고, 그때는 출처가 다르다고 화면이 말해야 한다).

    돌려주는 것: {"checked": n, "worst": rad, "over": [(에피소드, rad), ...]}
    """
    import numpy as np

    md = read_scene_metadata(Path(path))
    ref = expected if expected is not None else md.reset_qpos
    if not ref:
        return None
    ref = np.asarray([float(x) for x in ref], dtype=float)

    checked = 0
    worst = 0.0
    over: list = []
    with h5py.File(Path(path), "r") as f:
        for name in sorted(k for k in f if k.startswith("episode")):
            js = f[name].get("obs/joint_states")
            if js is None or len(js) == 0:
                continue
            d = float(np.abs(np.asarray(js[0], dtype=float) - ref).max())
            checked += 1
            worst = max(worst, d)
            if d > RESET_TOLERANCE:
                over.append((name, d))
    return {"checked": checked, "worst": worst, "over": over}
