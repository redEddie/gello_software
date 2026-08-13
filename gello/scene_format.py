"""Scene 기반 HDF5 저장 (scene-v1).

파일 하나 = scene 하나(책상 위의 한 가지 물리적 배치). 한 파일 안에 서로 다른
instruction 의 에피소드가 공존한다. 파일명에는 scene ID 만 들어가고,
instruction 은 **episode attrs 안에만** 존재한다 -- legacy v0 에서 파일명이
사실상 source of truth 라서 문장을 고칠 때마다 파일이 갈라지던 사고(Notion
프로토콜 §A)를 구조적으로 없애는 것이 이 포맷의 목적이다.

    scene_012.hdf5
    ├── metadata                    <- scene 단위. 그룹 attrs + 기준 사진
    │   attrs: scene_id, objects(JSON), layout(JSON), distractors(JSON),
    │          station, dataset_version, created, next_episode_idx
    │   └── reference_image         <- (H, W, 3) uint8 정면 사진 (배치 재현용)
    ├── episode_000
    │   ├── obs/..., actions, rewards, dones   <- legacy demo_N 과 동일 페이로드
    │   attrs: scene_id, instruction_id, episode_id, episode_uid,
    │          instruction(따옴표 없는 순수 문자열), success, quality_status,
    │          collector, timestamp,
    │          num_samples, action_space, gripper_action_convention,
    │          action_column_names, crop_params, station
    └── episode_001

legacy(``<task>_demo.hdf5``, gello/libero_format.py)와의 의도적 차이:

- ``problem_info``/``env_args`` 스텁을 쓰지 않는다. 이 저장소 안에 env_args 를
  읽는 코드는 없고(2026-08 조사), problem_info 소비자는 전부 새 포맷 지원으로
  고친다. 외부 LIBERO/robomimic 리더 호환은 포기한다 -- 결정 사항.
- instruction 은 따옴표로 감싸지 않는다. legacy 는 ``f'"{...}"'`` 로 감싸
  저장했고 읽는 쪽이 두 가지 방식으로 벗기고 있었다.
- 에피소드는 immutable: 삭제도 재번호도 없다. 불량은 ``quality_status`` 로만
  표시하고 실제 제외는 배포 단계 필터링에서 한다. 그래서 legacy 의
  ``renumber_episodes`` 에 해당하는 것이 없고 ``next_episode_idx`` 는 단조
  증가만 한다 -- episode ID 재사용 금지가 여기서 강제된다.
- 에피소드 안쪽 페이로드(obs/actions/rewards/dones 와 provenance attrs)는
  legacy 와 완전히 같다 (:func:`gello.libero_format.write_episode_payload`
  공유). 변환기가 두 포맷의 에피소드 내부를 같은 코드로 읽게 하기 위해서다.

layout 은 격자 존 기반 구조화 JSON 이다 (결정 사항 -- scene 다양성 추천이
배치 거리를 계산할 수 있어야 한다)::

    {
      "grid": [3, 3],                       # [rows, cols] 작업공간 분할
      "placements": {                       # instance ID -> 존 [row, col]
        "OBJ-CUP-BLU-01": {"zone": [0, 2]},
        "OBJ-BOWLS-YEL-01": {"zone": [1, 1]}
      },
      "relations": [                        # 선택: 존만으로 표현 안 되는 관계
        ["OBJ-CUP-BLU-01", "next_to", "OBJ-BOWLS-YEL-01"]
      ]
    }

존은 로봇 기준 작업공간을 카메라(agentview) 프레임에서 rows x cols 로 나눈
것이고, [0, 0] 이 왼쪽 위다. cm 좌표는 요구하지 않는다 -- 기준 위치에서 수 cm
흔드는 controlled variation(§4)은 같은 존 안의 이동이다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import h5py
import numpy as np

from gello.dataset_schema import DatasetSchemaConfig
from gello.libero_format import (
    LiberoEpisodeBuffer,
    _mark_close_on_exec,
    default_crop_params,
    write_episode_payload,
)

SCENE_DATASET_VERSION = "scene-v1"

# ---------------------------------------------------------------------- 품질
QUALITY_SUCCESS = "success"
QUALITY_FAILED = "failed"
QUALITY_BAD_DATA = "bad_data"      # 데이터 자체가 불량 (프레임 stall, 크롭 사고 등)
QUALITY_RETAKE = "retake"          # 다시 찍기로 한 것 -- 새 에피소드가 대체한다
QUALITY_DEPRECATED = "deprecated"  # 규칙 변경 등으로 배포에서 제외
QUALITY_STATUSES = (
    QUALITY_SUCCESS,
    QUALITY_FAILED,
    QUALITY_BAD_DATA,
    QUALITY_RETAKE,
    QUALITY_DEPRECATED,
)

# ------------------------------------------------------------------- ID 규칙
SCENE_ID_RE = re.compile(r"^S(\d{3,})$")
INSTRUCTION_ID_RE = re.compile(r"^I(\d{3,})$")
# 파일명은 scene ID 에서 기계적으로 파생된다. 이 정규식은 scene ID 할당과 파일
# 목록에만 쓴다 -- 파일명에서 task/instruction 을 판별하는 용도가 아니다(그건
# metadata 가 정본이고, 이 포맷에는 애초에 파일명에 instruction 이 없다).
SCENE_FILE_RE = re.compile(r"^scene_(\d{3,})\.hdf5$")
EPISODE_GROUP_RE = re.compile(r"^episode_(\d{3,})$")


def scene_filename(scene_id: str) -> str:
    m = SCENE_ID_RE.match(scene_id)
    if not m:
        raise ValueError(f"잘못된 scene ID: {scene_id!r} (예: 'S000')")
    return f"scene_{int(m.group(1)):03d}.hdf5"


def iter_scene_files(root: Path) -> list[Path]:
    """``root`` 아래 scene 파일들을 번호순으로. legacy ``*_demo.hdf5`` 와는
    글롭이 겹치지 않아 두 포맷이 같은 디렉터리에 있어도 서로 안 보인다."""
    root = Path(root)
    out = [p for p in root.glob("scene_*.hdf5") if SCENE_FILE_RE.match(p.name)]
    out.sort(key=lambda p: int(SCENE_FILE_RE.match(p.name).group(1)))
    return out


def next_scene_id(root: Path) -> str:
    """새 scene 에 줄 다음 ID. 기존 파일 번호의 max+1 -- 중간에 지워진 번호가
    있어도 재사용하지 않는다 (scene ID 도 episode ID 처럼 재사용 금지)."""
    nums = [int(SCENE_FILE_RE.match(p.name).group(1)) for p in iter_scene_files(root)]
    return f"S{max(nums, default=-1) + 1:03d}"


def episode_uid(scene_id: str, instruction_id: str, episode_idx: int) -> str:
    """``EP-S012-I003-E007`` -- HDF5, 수집 로그, QA 기록, Hub manifest,
    evaluation 결과에서 전부 이 하나의 이름을 쓴다 (§2)."""
    return f"EP-{scene_id}-{instruction_id}-E{episode_idx:03d}"


# ------------------------------------------------------------ scene metadata
@dataclass
class SceneMetadata:
    """파일당 1회 기록되는 scene 정의. ``objects``/``distractors`` 는 색 이름이
    아니라 configs/props.yaml 의 instance ID 다."""

    scene_id: str
    objects: list[str]
    layout: dict
    distractors: list[str] = field(default_factory=list)
    station: str = ""
    dataset_version: str = SCENE_DATASET_VERSION
    created: str = ""

    def validate(self, known_prop_ids: Optional[set[str]] = None) -> None:
        """구조가 틀린 metadata 로 파일을 만드는 것을 생성 시점에 막는다.
        ``known_prop_ids`` 를 주면(보통 gello.props.active_prop_ids()) 인벤토리에
        없는 ID 도 잡는다 -- 안 주면 형식 검사만 한다."""
        if not SCENE_ID_RE.match(self.scene_id):
            raise ValueError(f"잘못된 scene ID: {self.scene_id!r} (예: 'S000')")
        if not self.objects:
            raise ValueError("objects 가 비어 있다 -- scene 에는 물체가 최소 1개 필요하다")
        placed = list(self.objects) + list(self.distractors)
        dup = {o for o in placed if placed.count(o) > 1}
        if dup:
            raise ValueError(f"objects/distractors 에 중복 instance ID: {sorted(dup)}")
        for oid in placed:
            if not oid.startswith("OBJ-"):
                raise ValueError(
                    f"instance ID 가 아니다: {oid!r} -- 색 이름이 아니라 "
                    "configs/props.yaml 의 OBJ-* ID 를 적는다 (§3)"
                )
            if known_prop_ids is not None and oid not in known_prop_ids:
                raise ValueError(f"인벤토리에 없는 instance ID: {oid!r} (configs/props.yaml)")

        grid = self.layout.get("grid")
        if (
            not isinstance(grid, (list, tuple))
            or len(grid) != 2
            or not all(isinstance(g, int) and g > 0 for g in grid)
        ):
            raise ValueError(f"layout.grid 는 [rows, cols] 양의 정수 2개여야 한다: {grid!r}")
        placements = self.layout.get("placements")
        if not isinstance(placements, dict) or not placements:
            raise ValueError("layout.placements 가 비어 있다 -- 모든 물체의 존을 기록한다")
        for oid, spec in placements.items():
            if oid not in placed:
                raise ValueError(f"layout.placements 의 {oid!r} 가 objects/distractors 에 없다")
            zone = (spec or {}).get("zone")
            if (
                not isinstance(zone, (list, tuple))
                or len(zone) != 2
                or not all(isinstance(z, int) for z in zone)
                or not (0 <= zone[0] < grid[0] and 0 <= zone[1] < grid[1])
            ):
                raise ValueError(f"{oid} 의 zone 이 격자를 벗어났다: {zone!r} (grid={grid})")
        missing = [o for o in placed if o not in placements]
        if missing:
            raise ValueError(f"layout.placements 에 존이 없는 물체: {missing}")
        for rel in self.layout.get("relations", []):
            if not (isinstance(rel, (list, tuple)) and len(rel) == 3):
                raise ValueError(f"relations 항목은 [주어, 관계, 목적어] 3개여야 한다: {rel!r}")


def _read_metadata(meta: h5py.Group) -> SceneMetadata:
    return SceneMetadata(
        scene_id=str(meta.attrs["scene_id"]),
        objects=json.loads(meta.attrs["objects"]),
        layout=json.loads(meta.attrs["layout"]),
        distractors=json.loads(meta.attrs.get("distractors", "[]")),
        station=str(meta.attrs.get("station", "")),
        dataset_version=str(meta.attrs.get("dataset_version", "")),
        created=str(meta.attrs.get("created", "")),
    )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _episode_summary(name: str, grp: h5py.Group) -> dict:
    success = grp.attrs.get("success")
    return {
        "name": name,
        "episode_id": int(grp.attrs["episode_id"]),
        "episode_uid": str(grp.attrs["episode_uid"]),
        "instruction_id": str(grp.attrs["instruction_id"]),
        "instruction": str(grp.attrs["instruction"]),
        "quality_status": str(grp.attrs["quality_status"]),
        "success": None if success is None else bool(success),
        "collector": str(grp.attrs.get("collector", "")),
        "timestamp": str(grp.attrs.get("timestamp", "")),
        "num_samples": int(grp.attrs["num_samples"]),
    }


# ------------------------------------------------------------------- writer
class SceneWriter:
    """Owns one ``scene_XXX.hdf5``: one file per scene, one ``episode_NNN``
    per demonstration, instruction 은 에피소드마다 다를 수 있다.

    legacy :class:`~gello.libero_format.LiberoTaskWriter` 와 같은 스레드 규칙:
    파일을 만지는 호출(save_buffer / set_quality_status / list_episodes /
    close)은 호출자가 한 스레드로 직렬화한다 (GUI 에서는 EpisodeSaver).

    새 scene::

        meta = SceneMetadata(scene_id=next_scene_id(root), objects=[...], layout={...})
        w = SceneWriter(root, metadata=meta)

    기존 scene 이어찍기::

        w = SceneWriter(root, scene_id="S012", resume=True)   # metadata 는 파일에서

    instruction 은 저장 시점에 명시적으로 받는다 -- writer 상태로 들고 있지
    않는다. 저장이 백그라운드 스레드라, 조작자가 다음 slot 으로 넘어간 뒤에
    직전 에피소드가 저장되는 경합에서 "그 에피소드가 실제로 수행한 문장"이
    찍혀야 하기 때문이다 (crop_params 를 buffer 에 싣는 것과 같은 이유).
    """

    def __init__(
        self,
        root: Path,
        scene_id: Optional[str] = None,
        metadata: Optional[SceneMetadata] = None,
        resume: bool = False,
        schema: Optional[DatasetSchemaConfig] = None,
        crop_params: Optional[dict] = None,
        collector: str = "",
        known_prop_ids: Optional[set[str]] = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.schema = schema or DatasetSchemaConfig()
        self.crop_params = crop_params or default_crop_params()
        self.collector = collector
        self._buffer = LiberoEpisodeBuffer(self.schema, self.crop_params)

        if resume:
            if metadata is not None:
                raise ValueError("resume=True 면 metadata 는 파일에서 읽는다 -- 둘 다 주지 않는다")
            if scene_id is None:
                raise ValueError("resume=True 면 scene_id 가 필요하다")
            self.path = self.root / scene_filename(scene_id)
            if not self.path.exists():
                raise FileNotFoundError(f"{self.path} 가 없다 -- 새 scene 이면 metadata 를 주고 resume=False 로")
            self._file = h5py.File(self.path, "a")
            _mark_close_on_exec(self._file)
            self._meta = self._file["metadata"]
            self.metadata = _read_metadata(self._meta)
            if self.metadata.scene_id != scene_id:
                # 파일명이 아니라 내부 metadata 가 정본이다. 어긋났다는 것은
                # 파일이 손으로 rename 됐다는 뜻이므로 조용히 진행하지 않는다.
                raise ValueError(
                    f"{self.path.name} 내부 scene_id 는 {self.metadata.scene_id!r} 다 "
                    f"(요청: {scene_id!r}) -- 파일명이 아니라 metadata 를 믿는다"
                )
        else:
            if metadata is None:
                raise ValueError("새 scene 에는 metadata 가 필요하다")
            if scene_id is not None and scene_id != metadata.scene_id:
                raise ValueError(f"scene_id 인자({scene_id!r})와 metadata.scene_id({metadata.scene_id!r})가 다르다")
            metadata.validate(known_prop_ids=known_prop_ids)
            self.path = self.root / scene_filename(metadata.scene_id)
            if self.path.exists():
                raise FileExistsError(
                    f"{self.path} already exists; pass resume=True to append episodes."
                )
            self._file = h5py.File(self.path, "a")
            _mark_close_on_exec(self._file)
            self._meta = self._file.create_group("metadata")
            self.metadata = metadata
            if not metadata.created:
                metadata.created = _now_iso()
            self._meta.attrs["scene_id"] = metadata.scene_id
            self._meta.attrs["objects"] = json.dumps(metadata.objects, ensure_ascii=False)
            self._meta.attrs["layout"] = json.dumps(metadata.layout, ensure_ascii=False)
            self._meta.attrs["distractors"] = json.dumps(metadata.distractors, ensure_ascii=False)
            self._meta.attrs["station"] = metadata.station
            self._meta.attrs["dataset_version"] = metadata.dataset_version
            self._meta.attrs["created"] = metadata.created
            self._meta.attrs["next_episode_idx"] = 0

        if "next_episode_idx" not in self._meta.attrs:
            # 정상 파일에는 항상 있다. 없다면 손상이므로 기존 이름에서 복원하되
            # max+1 -- 어떤 경우에도 번호를 재사용하지 않는다.
            existing = [
                int(EPISODE_GROUP_RE.match(k).group(1))
                for k in self._file.keys()
                if EPISODE_GROUP_RE.match(k)
            ]
            self._meta.attrs["next_episode_idx"] = max(existing, default=-1) + 1
        self._file.flush()

    # ------------------------------------------------------- 버퍼 (legacy 미러)
    def start_episode(self) -> None:
        self._buffer.clear()

    def add_frame(self, **kwargs: Any) -> None:
        self._buffer.add_frame(**kwargs)

    def discard_episode(self) -> None:
        self._buffer.clear()

    def detach_buffer(self) -> LiberoEpisodeBuffer:
        buf = self._buffer
        self._buffer = LiberoEpisodeBuffer(self.schema, self.crop_params)
        return buf

    # ------------------------------------------------------------- 기준 사진
    def set_reference_image(self, img: np.ndarray) -> None:
        """scene 정면 사진 (H, W, 3) uint8. Scene Sheet 의 "사진 1장 필수"(§6)를
        파일 안에 넣는다 -- 배치 재현과 갤러리 대표 이미지가 이걸 쓴다.
        수집 시작 전 다시 찍을 수 있게 덮어쓰기는 허용한다."""
        arr = np.asarray(img)
        if arr.ndim != 3 or arr.shape[2] != 3 or arr.dtype != np.uint8:
            raise ValueError(f"(H, W, 3) uint8 이어야 한다: shape={arr.shape}, dtype={arr.dtype}")
        if "reference_image" in self._meta:
            del self._meta["reference_image"]
        self._meta.create_dataset("reference_image", data=arr, compression="lzf")
        self._file.flush()

    # ----------------------------------------------------------------- 저장
    def save_buffer(
        self,
        buf: LiberoEpisodeBuffer,
        *,
        instruction: str,
        instruction_id: str,
        success: Optional[bool] = None,
        quality_status: Optional[str] = None,
        collector: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Optional[str]:
        """Commits one detached episode buffer as the next ``episode_NNN``.

        instruction 관련 규칙:
        - ``instruction`` 은 따옴표 없는 순수 문장. legacy 습관으로 감싸진
          문자열이 들어오면 조용히 저장하지 않고 거부한다.
        - ``quality_status`` 를 안 주면 ``success`` 에서 파생한다. 라벨이 아예
          없는 에피소드는 거부한다 -- 배포 필터링이 quality_status 만 보므로,
          라벨 없는 에피소드는 나중에 아무도 판정할 수 없다.

        Returns the group name, or None if the buffer was empty.
        """
        n = len(buf)
        if n < 2:
            buf.clear()
            return None

        if not INSTRUCTION_ID_RE.match(instruction_id):
            raise ValueError(f"잘못된 instruction ID: {instruction_id!r} (예: 'I000')")
        instruction = str(instruction).strip()
        if not instruction:
            raise ValueError("instruction 이 비어 있다")
        if len(instruction) >= 2 and instruction[0] == '"' and instruction[-1] == '"':
            raise ValueError(
                f"instruction 이 따옴표로 감싸져 있다: {instruction!r} -- "
                "scene 포맷은 순수 문자열만 저장한다 (legacy 의 f'\"{...}\"' 관례를 가져오지 않는다)"
            )
        if quality_status is None:
            if success is None:
                raise ValueError(
                    "success 나 quality_status 중 하나는 있어야 한다 -- "
                    "scene 포맷은 라벨 없는 에피소드를 허용하지 않는다"
                )
            quality_status = QUALITY_SUCCESS if success else QUALITY_FAILED
        if quality_status not in QUALITY_STATUSES:
            raise ValueError(f"잘못된 quality_status: {quality_status!r} (허용: {QUALITY_STATUSES})")

        idx = int(self._meta.attrs["next_episode_idx"])
        self._meta.attrs["next_episode_idx"] = idx + 1
        name = f"episode_{idx:03d}"
        grp = self._file.create_group(name)
        write_episode_payload(grp, buf, self.schema, success=success)

        sid = self.metadata.scene_id
        grp.attrs["scene_id"] = sid
        grp.attrs["instruction_id"] = instruction_id
        grp.attrs["episode_id"] = idx
        grp.attrs["episode_uid"] = episode_uid(sid, instruction_id, idx)
        grp.attrs["instruction"] = instruction
        grp.attrs["quality_status"] = quality_status
        grp.attrs["collector"] = self.collector if collector is None else collector
        grp.attrs["timestamp"] = timestamp or _now_iso()

        self._file.flush()
        buf.clear()
        return name

    def set_quality_status(self, name: str, status: str) -> None:
        """QA 재판정. 에피소드에서 바뀔 수 있는 것은 이 attr 과 success 뿐이다 --
        프레임·번호·instruction 은 immutable 이고, 삭제는 존재하지 않는다."""
        if status not in QUALITY_STATUSES:
            raise ValueError(f"잘못된 quality_status: {status!r} (허용: {QUALITY_STATUSES})")
        if name not in self._file or not EPISODE_GROUP_RE.match(name):
            raise KeyError(f"{name!r} not found in {self.path}")
        self._file[name].attrs["quality_status"] = status
        self._file[name].attrs["success"] = status == QUALITY_SUCCESS
        self._file.flush()

    # ----------------------------------------------------------------- 조회
    @property
    def num_episodes(self) -> int:
        return sum(1 for k in self._file.keys() if EPISODE_GROUP_RE.match(k))

    def list_episodes(self) -> list[dict]:
        """에피소드 요약을 번호순으로 -- 갤러리·slot 카운트가 쓰는 형태."""
        items = [
            _episode_summary(k, self._file[k])
            for k in self._file.keys()
            if EPISODE_GROUP_RE.match(k)
        ]
        items.sort(key=lambda d: d["episode_id"])
        return items

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "SceneWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ------------------------------------------------------------------- 읽기
def read_scene_metadata(path: Path) -> SceneMetadata:
    with h5py.File(path, "r") as f:
        return _read_metadata(f["metadata"])


def read_reference_image(path: Path) -> Optional[np.ndarray]:
    with h5py.File(path, "r") as f:
        ds = f["metadata"].get("reference_image")
        return None if ds is None else ds[...]


def list_scene_episodes(path: Path) -> list[dict]:
    """파일을 열지 않고 있는(writer 없는) 호출자용 에피소드 요약. attrs 만
    읽으므로 이미지 청크는 건드리지 않는다."""
    with h5py.File(path, "r") as f:
        items = [
            _episode_summary(k, f[k]) for k in f.keys() if EPISODE_GROUP_RE.match(k)
        ]
    items.sort(key=lambda d: d["episode_id"])
    return items


def count_by_slot(path: Path) -> dict[str, dict[str, int]]:
    """slot(=instruction_id)별 수집 현황: ``{instruction_id: {"total", "usable"}}``.
    usable 은 quality_status == "success" 만 센다 -- 계획 파일의 target 과
    비교하는 것은 이 값이다 ("collected 는 파일을 읽어 계산한다", §11)."""
    out: dict[str, dict[str, int]] = {}
    for ep in list_scene_episodes(path):
        slot = out.setdefault(ep["instruction_id"], {"total": 0, "usable": 0})
        slot["total"] += 1
        if ep["quality_status"] == QUALITY_SUCCESS:
            slot["usable"] += 1
    return out
