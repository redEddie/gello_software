"""카메라 노드 구독 클라이언트 (2026-08-25, gello/camera_node.py 의 짝).

NodeCamera 는 lerobot RealSenseCamera 의 read_latest / read_latest_depth
호출부와 호환되는 API 를 제공한다 -- 수집 worker(gello/collect/worker.py) 와 GUI
미리보기가 코드 변경 최소로 노드 구독으로 갈아탈 수 있게. 프레임 대신
"최신 프레임의 나이" 계약(TimeoutError)도 그대로 유지한다.

동작: SUB 소켓 하나로 "{role}/color" 와 "{role}/depth" 를 구독하고,
read_* 호출 때마다 큐를 논블로킹으로 비우면서(drain) 최신 프레임만 캐시에
남긴다. 나이는 노드가 캡처 시점에 찍은 time.time() 기준 -- 같은 호스트라
시계가 같다. 스레드 하나에서만 쓰는 것을 전제로 한다 (worker 루프 또는
미리보기 스레드가 각자 인스턴스를 만든다).
"""

from __future__ import annotations

import json
import time

import numpy as np
import zmq

from gello.comm.camera_node import DEFAULT_CTL_PORT, DEFAULT_PUB_PORT

_CONNECT_HELP = (
    "카메라 노드가 응답하지 않습니다 (tcp://{host}:{pub}). GUI 가 자동으로 "
    "띄우는 프로세스인데 죽었을 수 있습니다 -- 로그의 [카메라노드] 줄을 "
    "확인하고, Camera 메뉴 > 카메라 노드 재시작을 누르세요."
)


def node_ping(host: str = "127.0.0.1", ctl_port: int = DEFAULT_CTL_PORT,
              timeout_ms: int = 1500) -> dict | None:
    """제어 채널 ping. 응답 없으면 None (노드 죽음/미실행)."""
    ctx = zmq.Context.instance()
    s = ctx.socket(zmq.REQ)
    s.setsockopt(zmq.RCVTIMEO, timeout_ms)
    s.setsockopt(zmq.SNDTIMEO, timeout_ms)
    s.setsockopt(zmq.LINGER, 0)
    try:
        s.connect(f"tcp://{host}:{ctl_port}")
        s.send(json.dumps({"cmd": "ping"}).encode())
        return json.loads(s.recv())
    except zmq.error.Again:
        return None
    finally:
        s.close(linger=0)


def fetch_aligned(role: str, host: str = "127.0.0.1",
                  ctl_port: int = DEFAULT_CTL_PORT,
                  timeout_ms: int = 2500) -> dict | None:
    """포인트클라우드 표시용: 정렬(depth->color) 프레임 1쌍 + 내부 파라미터.
    반환: {"z": (H,W) float32 m, "rgb": (H,W,3) u8, "intrinsics": {...}}
    또는 None (노드/카메라 죽음)."""
    ctx = zmq.Context.instance()
    s = ctx.socket(zmq.REQ)
    s.setsockopt(zmq.RCVTIMEO, timeout_ms)
    s.setsockopt(zmq.SNDTIMEO, timeout_ms)
    s.setsockopt(zmq.LINGER, 0)
    try:
        s.connect(f"tcp://{host}:{ctl_port}")
        s.send(json.dumps({"cmd": "aligned", "role": role}).encode())
        parts = s.recv_multipart()
        meta = json.loads(parts[0])
        if not meta.get("ok") or len(parts) < 3:
            return None
        h, w = meta["shape"][:2]
        z16 = np.frombuffer(parts[1], dtype=np.uint16).reshape(h, w)
        rgb = np.frombuffer(parts[2], dtype=np.uint8).reshape(h, w, 3)
        return {"z": z16.astype(np.float32) * float(meta["depth_scale"]),
                "rgb": rgb, "intrinsics": meta["intrinsics"]}
    except zmq.error.Again:
        return None
    finally:
        s.close(linger=0)


class NodeCamera:
    """role 하나("agent"/"wrist")의 최신 프레임 구독자."""

    def __init__(self, role: str, serial: str | None = None,
                 host: str = "127.0.0.1", pub_port: int = DEFAULT_PUB_PORT,
                 ctl_port: int = DEFAULT_CTL_PORT) -> None:
        self.role = role
        self.serial = serial  # 지정 시 connect 에서 노드의 role 시리얼과 대조
        self.host = host
        self.pub_port = pub_port
        self.ctl_port = ctl_port
        self._ctx: zmq.Context | None = None
        self._sub: zmq.Socket | None = None
        self._latest: dict[str, tuple[float, np.ndarray]] = {}  # kind -> (ts, arr)
        self.depth_scale: float | None = None  # depth meta 에서 채워진다 (m/단위)

    def __repr__(self) -> str:
        return f"NodeCamera({self.role}:{self.serial or '?'})"

    # ------------------------------------------------------------ lifecycle
    @property
    def is_connected(self) -> bool:
        return self._sub is not None

    def connect(self, warmup_s: float = 4.0) -> None:
        """구독 시작 + 첫 color 프레임 대기. 노드가 없거나 이 role 의 시리얼이
        다르면 ConnectionError -- worker 가 세션 시작 시 바로 알아채게."""
        info = node_ping(self.host, self.ctl_port)
        if info is None:
            raise ConnectionError(_CONNECT_HELP.format(
                host=self.host, pub=self.pub_port))
        cam = info.get("cams", {}).get(self.role)
        if cam is None:
            raise ConnectionError(
                f"카메라 노드에 '{self.role}' 카메라가 없습니다 "
                f"(노드 구성: {sorted(info.get('cams', {}))}). "
                "카메라 노드를 재시작하세요.")
        if self.serial and cam.get("serial") != self.serial:
            raise ConnectionError(
                f"카메라 노드의 {self.role} 는 {cam.get('serial')} 인데 "
                f"GUI 선택은 {self.serial} 입니다. 카메라 선택을 바꿨으면 "
                "카메라 노드를 재시작하세요.")
        self._ctx = zmq.Context()
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, 12)
        self._sub.connect(f"tcp://{self.host}:{self.pub_port}")
        for kind in ("color", "depth"):
            self._sub.setsockopt(zmq.SUBSCRIBE,
                                 f"{self.role}/{kind}".encode())
        # 워밍업: 첫 color 가 올 때까지 (PUB/SUB 조인에 수백 ms 걸릴 수 있다)
        t_end = time.time() + warmup_s
        while time.time() < t_end:
            self._drain(poll_ms=200)
            if "color" in self._latest:
                return
        self.disconnect()
        raise ConnectionError(
            f"카메라 노드는 응답하지만 {self.role} 프레임이 {warmup_s:.0f}초 "
            f"내에 오지 않았습니다 (노드 상태: {cam}). 카메라가 살아나기를 "
            "기다리거나 케이블을 확인하세요.")

    def disconnect(self) -> None:
        if self._sub is not None:
            self._sub.close(linger=0)
            self._sub = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None
        self._latest.clear()

    # ---------------------------------------------------------------- reads
    def _drain(self, poll_ms: int = 0) -> None:
        """큐에 쌓인 메시지를 전부 소비해 최신만 남긴다. poll_ms > 0 이면
        비어 있을 때 그만큼 새 메시지를 기다린다."""
        assert self._sub is not None
        got = False
        while True:
            try:
                topic, meta, payload = self._sub.recv_multipart(zmq.NOBLOCK)
            except zmq.error.Again:
                if got or poll_ms <= 0 or not self._sub.poll(poll_ms):
                    return
                continue
            got = True
            m = json.loads(meta)
            kind = topic.decode().split("/", 1)[1]
            arr = np.frombuffer(payload, dtype=m["dtype"]) \
                .reshape(m["shape"])
            self._latest[kind] = (m["ts"], arr)
            if kind == "depth" and "depth_scale" in m:
                self.depth_scale = float(m["depth_scale"])

    def _read(self, kind: str, max_age_ms: int) -> np.ndarray:
        if self._sub is None:
            raise RuntimeError(f"{self} is not connected")
        self._drain()
        ent = self._latest.get(kind)
        age_ms = (time.time() - ent[0]) * 1e3 if ent else float("inf")
        if age_ms > max_age_ms:
            # 큐가 말랐다 -- 짧게 기다려 fresh 프레임에 기회를 준다 (노드
            # 재시작·일시 정지 직후 첫 read 가 바로 죽지 않게).
            self._drain(poll_ms=min(300, max_age_ms))
            ent = self._latest.get(kind)
            age_ms = (time.time() - ent[0]) * 1e3 if ent else float("inf")
        if ent is None:
            raise TimeoutError(f"{self} 에 {kind} 프레임이 아직 없습니다 "
                               "(노드가 재시작 중일 수 있음)")
        if age_ms > max_age_ms:
            raise TimeoutError(
                f"{self} latest {kind} frame is too old: {age_ms:.1f} ms "
                f"(max allowed: {max_age_ms} ms)")
        return ent[1]

    def read_latest(self, max_age_ms: int = 500) -> np.ndarray:
        """최신 RGB (H,W,3) u8. lerobot read_latest 와 같은 계약."""
        return self._read("color", max_age_ms)

    def read_latest_depth(self, max_age_ms: int = 500) -> np.ndarray:
        """최신 raw depth z16 (H,W,1) u16 -- lerobot read_latest_depth 와
        같은 의미론 (비정렬, 기존 scene 파일과 호환)."""
        return self._read("depth", max_age_ms)
