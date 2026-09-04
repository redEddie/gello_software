"""스테이션 설정 -- "이 물리적 셋업이 무엇인가".

로봇 IP, ZMQ 노드 주소, 카메라 시리얼과 스트림 포맷, 리더암 USB 포트,
카메라 마운트에서 오는 크롭 보정값처럼 **하드웨어를 바꾸지 않는 한 변하지
않는 값**을 한 파일에 모은다. 이전에는 같은 시리얼이 네 곳
(gello/collect/worker.py, fr3_policy_client.py, 그리고 각각의 기본 인자)에
따로 적혀 있어서, 카메라를 교체하면 어디를 고쳐야 하는지가 grep 실력에
달려 있었다.

설정 파일과 나누어 두는 것들:

* ``~/libero_gui_logs/crop_params.json`` -- GUI Layout 패널에서 슬라이더로
  실시간 조정하는 값. 여기(스테이션)는 그 **초기값**만 준다. 커밋되는
  파일에 슬라이더를 움직일 때마다 쓰면 git diff 가 지저분해진다.
* ``~/libero_gui_logs/dataset_schema.json`` -- 무엇을 저장할지. 스테이션과
  무관하게 데이터셋마다 달라진다.
* ``gello/agents/gello_agent.py`` 의 ``PORT_CONFIG_MAP`` -- 리더암 개체별
  조인트 오프셋/부호. USB 시리얼로 키가 잡힌 장치 지문 테이블이라
  스테이션이 아니라 장치에 속한다. 여기서는 "어느 포트를 쓸지"만 고른다.

여기 들어오는 기준은 **코드가 읽어서 하드웨어를 구성하는 값**이다. 틀리면 즉시
고장나므로 썩지 않는다. 펌웨어 버전이나 일련번호처럼 아무도 읽지 않는 값은
틀려도 티가 안 나서 반드시 썩으므로(교체된 카메라의 옛 시리얼이 소스에 몇 달
남아 있던 것이 그 예), yaml 하단 ``info:`` 블록에 문서로만 둔다 -- 이 모듈은
그 블록을 파싱하지 않는다. USB 링크 속도처럼 케이블이 정하는 결과값도 설정이
아니다(``scripts/check/check_cameras.py`` 가 실측해서 경고한다).

선택:
    GELLO_STATION=<이름>   # configs/stations/<이름>.yaml
    GELLO_STATION=<경로>   # .yaml 로 끝나면 경로로 해석
기본값은 ``knu-eng7``. 파일이 없거나 PyYAML 이 없으면 경고 한 번 찍고
아래 기본값(= 통합 전 소스에 하드코딩돼 있던 값과 동일)으로 돌아간다.
설정을 못 읽었다고 로봇을 못 띄우는 쪽이 더 나쁘다.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_STATION = "knu-eng7"
STATIONS_DIR = Path(__file__).resolve().parents[2] / "configs" / "stations"


@dataclass(frozen=True)
class CameraSpec:
    """한 대의 RealSense. width/height/fps 는 librealsense 가 실제로 지원하는
    조합이어야 한다 -- D405 는 640x480 에서 30fps 가 상한이고, 60 을 넣으면
    스트림 설정이 거부되면서 "device busy" 라는 엉뚱한 에러로 나온다."""

    serial: str = ""
    model: str = ""
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass(frozen=True)
class RobotSpec:
    kind: str = "fr3"
    ip: str = "172.16.0.2"  # FCI 주소. 정책 서버 주소가 아니다.
    reset_pose: str = "libero"


@dataclass(frozen=True)
class NodeSpec:
    """로봇 노드 프로세스 -- launch_nodes.py 가 여는 ZMQ REP 소켓과,
    그걸 띄울 파이썬. pylibfranka 는 별도 venv 에만 있어서 GUI 를 돌리는
    인터프리터로는 노드를 띄울 수 없다."""

    host: str = "127.0.0.1"
    port: int = 6001
    python: str = "~/pylibfranka-venv/bin/python"

    @property
    def python_path(self) -> str:
        return str(Path(self.python).expanduser())


@dataclass(frozen=True)
class LeaderSpec:
    """GELLO 리더암. port=None 이면 /dev/serial/by-id 에서 FTDI 장치를 찾는다
    (지금까지의 동작)."""

    port: Optional[str] = None


@dataclass(frozen=True)
class StationConfig:
    name: str = DEFAULT_STATION
    description: str = ""
    robot: RobotSpec = field(default_factory=RobotSpec)
    node: NodeSpec = field(default_factory=NodeSpec)
    leader: LeaderSpec = field(default_factory=LeaderSpec)
    cameras: Dict[str, CameraSpec] = field(
        default_factory=lambda: {"agent": CameraSpec(), "wrist": CameraSpec()}
    )
    # 역할별 정사각 크롭 초기값. 실제 사용값은 crop_params.json 이 이긴다.
    crop: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: {
            "agent": {"zoom": 1.2, "x": 0, "y": 0},
            "wrist": {"zoom": 1.0, "x": 31, "y": 0},
        }
    )
    # 텔레옵/기록 루프 주파수(Hz). 카메라 fps 와 다르다 -- 카메라는 30fps 로
    # 돌고 루프가 20Hz 로 최신 프레임만 집어간다.
    fps: int = 20
    # 설정 파일을 실제로 읽었는지. False 면 위 기본값으로 돌아간 것.
    loaded_from: Optional[str] = None

    def camera(self, role: str) -> CameraSpec:
        return self.cameras.get(role, CameraSpec())

    def crop_params(self) -> Dict[str, Dict[str, float]]:
        """libero_format.default_crop_params() 가 기대하는 모양의 사본."""
        return {role: dict(vals) for role, vals in self.crop.items()}


def _station_path(name_or_path: str) -> Path:
    if name_or_path.endswith((".yaml", ".yml")) or os.sep in name_or_path:
        return Path(name_or_path).expanduser()
    return STATIONS_DIR / f"{name_or_path}.yaml"


def _camera_from(raw: Any, fallback: CameraSpec) -> CameraSpec:
    if not isinstance(raw, dict):
        return fallback
    return CameraSpec(
        serial=str(raw.get("serial", fallback.serial)),
        model=str(raw.get("model", fallback.model)),
        width=int(raw.get("width", fallback.width)),
        height=int(raw.get("height", fallback.height)),
        fps=int(raw.get("fps", fallback.fps)),
    )


def _parse(raw: dict, path: Path) -> StationConfig:
    base = StationConfig()
    robot = raw.get("robot") or {}
    node = raw.get("node") or {}
    leader = raw.get("leader") or {}
    cams = raw.get("cameras") or {}
    crop = raw.get("crop") or {}

    return StationConfig(
        name=str(raw.get("name", path.stem)),
        description=str(raw.get("description", "")),
        robot=RobotSpec(
            kind=str(robot.get("kind", base.robot.kind)),
            ip=str(robot.get("ip", base.robot.ip)),
            reset_pose=str(robot.get("reset_pose", base.robot.reset_pose)),
        ),
        node=NodeSpec(
            host=str(node.get("host", base.node.host)),
            port=int(node.get("port", base.node.port)),
            python=str(node.get("python", base.node.python)),
        ),
        leader=LeaderSpec(port=leader.get("port") or None),
        cameras={
            role: _camera_from(cams.get(role), base.camera(role))
            for role in set(base.cameras) | set(cams if isinstance(cams, dict) else {})
        },
        crop={
            role: {
                "zoom": float((crop.get(role) or {}).get("zoom", vals["zoom"])),
                "x": int((crop.get(role) or {}).get("x", vals["x"])),
                "y": int((crop.get(role) or {}).get("y", vals["y"])),
            }
            for role, vals in base.crop.items()
        },
        fps=int((raw.get("recording") or {}).get("fps", base.fps)),
        loaded_from=str(path),
    )


_cache: Dict[str, StationConfig] = {}
_warned: set = set()


def load_station(name: Optional[str] = None, *, reload: bool = False) -> StationConfig:
    """스테이션 설정을 읽는다. 절대 예외를 던지지 않는다 -- 읽기에 실패하면
    경고를 한 번 찍고 내장 기본값을 준다."""
    key = name or os.environ.get("GELLO_STATION") or DEFAULT_STATION
    if not reload and key in _cache:
        return _cache[key]

    path = _station_path(key)
    cfg = StationConfig()
    try:
        import yaml  # 두 venv 모두 설치돼 있으나 필수는 아니게 둔다

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"최상위가 매핑이 아님: {type(raw).__name__}")
        cfg = _parse(raw, path)
    except Exception as e:  # noqa: BLE001
        if key not in _warned:
            _warned.add(key)
            print(
                f"[station] '{path}' 를 읽지 못해 기본값을 씁니다 "
                f"({type(e).__name__}: {e})",
                flush=True,
            )
        cfg = replace(cfg, name=key, loaded_from=None)

    _cache[key] = cfg
    return cfg


def list_stations() -> list[str]:
    if not STATIONS_DIR.is_dir():
        return []
    return sorted(p.stem for p in STATIONS_DIR.glob("*.yaml"))


# ----------------------------------------------------------------- 쓰기
# 런처 마법사가 새 스테이션을 등록할 때만 쓴다. **기존 스테이션 수정은
# 일부러 GUI 에서 막아 두었다** -- 코드로 고치게 해서 git 커밋 기록을
# 남기려는 것이다 (2026-09-05 사용자 결정). 그래서 여기에도 "덮어쓰기"가
# 없다: save_station 은 이미 있는 이름을 거부한다.
STATION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_station_name(name: str) -> Optional[str]:
    """이름이 규칙에 맞지 않으면 이유를, 맞으면 None."""
    if not name:
        return "이름이 비어 있습니다."
    if not STATION_NAME_RE.fullmatch(name):
        return "영문·숫자로 시작하고 영문·숫자·.-_ 만 쓸 수 있습니다 (파일명이 됩니다)."
    if name in list_stations():
        return f"'{name}' 은 이미 있습니다. 다른 이름을 쓰세요."
    return None


def station_path(name: str) -> Path:
    """이 이름의 스테이션 파일 경로 (있든 없든)."""
    return STATIONS_DIR / f"{name}.yaml"


def save_station(cfg: StationConfig) -> Path:
    """새 스테이션을 YAML 로 쓴다. 이름이 이미 있으면 ValueError.

    crop 과 fps 는 쓰지 않는다 -- 크롭은 GUI 슬라이더가 따로 저장하고
    (crop_params.json), 나머지는 기본값이 정본이다. 여기 적어두면 "두 곳에
    적힌 같은 값"이 되어 갈라진다.
    """
    import yaml

    err = validate_station_name(cfg.name)
    if err:
        raise ValueError(err)
    data = {
        "name": cfg.name,
        "description": cfg.description,
        "robot": {"kind": cfg.robot.kind, "ip": cfg.robot.ip,
                  "reset_pose": cfg.robot.reset_pose},
        "node": {"host": cfg.node.host, "port": int(cfg.node.port),
                 "python": cfg.node.python},
        "leader": ({"port": cfg.leader.port} if cfg.leader.port else {}),
        "cameras": {role: {"serial": c.serial, "model": c.model,
                           "width": c.width, "height": c.height, "fps": c.fps}
                    for role, c in cfg.cameras.items()},
    }
    path = station_path(cfg.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 스테이션 설정 -- 런처 마법사가 만들었습니다.\n"
        "# 이 파일은 git 추적 대상입니다. 커밋해 두지 않으면 아이콘 실행 때\n"
        "# 자동 git pull 이 건너뛰어집니다 (작업 트리가 더럽기 때문).\n"
        "# 내용 수정은 이 파일을 직접 고치세요 -- GUI 는 일부러 막아 두었습니다.\n"
    )
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(header + yaml.safe_dump(data, allow_unicode=True,
                                           sort_keys=False),
                   encoding="utf-8")
    tmp.replace(path)
    _cache.pop(cfg.name, None)      # 다음 load_station 이 파일을 읽게
    return path


def delete_station(name: str) -> None:
    """스테이션 파일 삭제. 마법사는 **이번 세션에서 만든 것**에만 허용한다."""
    station_path(name).unlink(missing_ok=True)
    _cache.pop(name, None)
