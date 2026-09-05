"""카메라 노드 -- RealSense 를 독점 소유하는 별도 프로세스 (2026-08-25).

왜 별도 프로세스인가 (그날 하루의 사고 3종이 근거):
1. GIL 기아: 카메라 리더 스레드가 GUI 와 같은 프로세스면 렌더링·기록이
   GIL 을 쥐는 동안 버퍼 갱신이 밀린다 -- 같은 카메라가 단독 프로세스에선
   프레임 간격 최장 41ms, GUI 안에선 505~550ms 로 실측됐다.
2. device busy: 미리보기와 수집 worker 가 같은 장치를 번갈아 여닫는
   핸드오프(최대 12초 대기)가 필요했고, 그 틈의 경합이 ConnectionError 를
   만들었다. 이제 장치를 만지는 프로세스는 이 노드 하나다 -- GUI 미리보기,
   포인트클라우드, 수집 worker 는 전부 구독자다.
3. wedge: 파이프라인을 세션마다 여닫으면 (특히 서드파티 xHCI 에서) 스트림
   상태가 엉켜 "링크는 살고 프레임만 안 오는" 상태가 됐다. 노드는 시작할 때
   한 번만 열고, 스트림이 죽으면 스스로 hardware_reset 후 다시 연다.

구조:
- 카메라마다 캡처 스레드 1개 (pyrealsense2 직접 사용, lerobot 래퍼 없음).
- PUB(기본 tcp://*:6021): 토픽 "{serial}/color"(RGB u8 HxWx3),
  "{serial}/depth"(비정렬 raw z16 u16 HxWx1, lerobot 과 동일 의미론 --
  기존 scene 파일과 호환), "status"(1Hz json). 메시지는
  [토픽, meta json, payload] 3부.
- REP(기본 tcp://*:6022): {"cmd": "ping"} -> 상태,
  {"cmd": "aligned", "serial": s} -> 정렬(depth->color) 프레임 1쌍 + 내부
  파라미터 (포인트클라우드 표시 전용 -- 기록 경로와 분리).
- --fake: 하드웨어 없이 합성 프레임 (테스트용).

실행:
    python -m gello.comm.camera_node --cam 338122300664 --cam 230422272249

노드는 **역할(agent/wrist)을 모른다** (2026-09-05 3층 분리). 신원은 시리얼
하나뿐이다: 하드웨어 계층이 데이터세트 계층의 이름을 알면, 역할만 바꿔도
노드를 재시작해야 한다 (같은 카메라 두 대를 그대로 쓰는데도). 역할 -> 시리얼
매핑은 구독하는 쪽이 안다 (gello/scene/dataset_meta.resolve_cameras).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time

import numpy as np
import zmq

DEFAULT_PUB_PORT = 6021
DEFAULT_CTL_PORT = 6022

#: wait_for_frames 한 번의 대기 (ms). 종료 응답 속도가 이 값에 묶인다 --
#: 종료 플래그를 세워도 스레드는 이 안에 갇혀 있다.
#:
#: 500ms 로 줄여 봤으나 되돌렸다 (2026-09-05): 종료가 2,476 -> 1,835ms 로
#: 641ms 밖에 안 줄었다. 그 시간의 대부분(~1초)은 pipe.stop() 의 장치 정리라
#: 타임아웃과 무관하고, 500ms 로는 스트림을 막 연 직후 첫 프레임이 늦어
#: "프레임 없음" 이 헛되이 찍혀 첫 프레임용 예외 처리가 또 필요했다.
#: 무엇보다 노드 신원을 시리얼로 바꾼 뒤로는(Q2) 역할 변경에 재시작이 없어,
#: 이 비용은 카메라를 실제로 바꿔 꽂을 때만 든다 -- 드문 일에 상수 둘과
#: 플래그 하나를 얹을 값어치가 없다.
FRAME_WAIT_MS = 2000
#: 몇 번 연속 비면 스트림이 죽은 것으로 보고 재오픈하는가.
FRAME_TIMEOUTS_BEFORE_REOPEN = 2


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class _Publisher:
    """스레드 여럿이 쓰는 PUB 소켓. zmq 소켓은 스레드 안전이 아니므로 락으로
    직렬화한다 -- 프레임당 send 한 번이라 경합 비용은 무시할 수준."""

    def __init__(self, ctx: zmq.Context, port: int) -> None:
        self._sock = ctx.socket(zmq.PUB)
        self._sock.setsockopt(zmq.SNDHWM, 30)  # 구독자가 밀리면 새 프레임 폐기
        self._sock.bind(f"tcp://*:{port}")
        self._lock = threading.Lock()

    def send(self, topic: str, meta: dict, payload: bytes = b"") -> None:
        with self._lock:
            self._sock.send_multipart(
                [topic.encode(), json.dumps(meta).encode(), payload])


class CameraWorker(threading.Thread):
    """카메라 하나: 캡처 -> 발행 -> (요청 시) 정렬 계산 -> 죽으면 자가복구."""

    def __init__(self, serial: str, pub: _Publisher,
                 width: int = 640, height: int = 480, fps: int = 30) -> None:
        super().__init__(daemon=True, name=f"cam-{serial}")
        self.serial = serial
        self.pub = pub
        self.w, self.h, self.fps = width, height, fps
        self.running = True
        # 상태 (control/status 발행용)
        self.alive = False
        self.fps_actual = 0.0
        self.last_error = ""
        self.resets = 0
        # 정렬 요청 슬롯 (control 스레드 <-> 캡처 스레드)
        self._aligned_req = threading.Event()
        self._aligned_done = threading.Event()
        self._aligned_result: dict | None = None

    # ---------------------------------------------------------- 정렬 요청
    def request_aligned(self, timeout_s: float = 1.5) -> dict | None:
        """다음 프레임셋에서 정렬(depth->color) 결과 1쌍을 받아온다."""
        if not self.alive:
            return None
        self._aligned_done.clear()
        self._aligned_req.set()
        if not self._aligned_done.wait(timeout_s):
            self._aligned_req.clear()
            return None
        return self._aligned_result

    # ------------------------------------------------------------- 자가복구
    def _hardware_reset(self) -> None:
        import pyrealsense2 as rs

        try:
            for dev in rs.context().query_devices():
                if dev.get_info(rs.camera_info.serial_number) == self.serial:
                    dev.hardware_reset()
                    self.resets += 1
                    _log(f"{self.serial}: hardware_reset ({self.resets}번째)")
        except Exception as e:  # noqa: BLE001
            _log(f"{self.serial}: hardware_reset 실패: {e}")
        time.sleep(3.5)  # 재열거 대기

    # ------------------------------------------------------------------ run
    def run(self) -> None:  # noqa: C901
        import pyrealsense2 as rs

        first_open = True
        last_absent_log = 0.0
        while self.running:
            # 장치가 아예 없으면 pipe.start() 를 부르지 않는다: 부재 장치에
            # 대한 start() 는 GIL 을 쥔 채 ~15초 블록해서 (pyrealsense2 가
            # 이 호출에서 GIL 을 놓지 않는다) 노드 전체 -- 제어 채널 ping
            # 포함 -- 를 멈춰 세운다 (2026-08-25 실측). 존재 확인은 빠르다.
            try:
                present = {d.get_info(rs.camera_info.serial_number)
                           for d in rs.context().query_devices()}
            except Exception:  # noqa: BLE001
                present = set()
            if self.serial not in present:
                self.alive = False
                self.last_error = "장치가 USB 에 없음 (케이블 확인)"
                if time.time() - last_absent_log > 10:
                    last_absent_log = time.time()
                    _log(f"{self.serial}: {self.last_error} -- 기다리는 중")
                time.sleep(1.0)
                continue
            pipe = rs.pipeline()
            cfg = rs.config()
            cfg.enable_device(self.serial)
            cfg.enable_stream(rs.stream.color, self.w, self.h,
                              rs.format.rgb8, self.fps)
            cfg.enable_stream(rs.stream.depth, self.w, self.h,
                              rs.format.z16, self.fps)
            try:
                profile = pipe.start(cfg)
            except Exception as e:  # noqa: BLE001
                self.alive = False
                self.last_error = f"열기 실패: {e}"
                _log(f"{self.serial}: {self.last_error}")
                if not self.running:
                    return
                # busy = 다른 프로세스(VLA 정책 클라이언트, 이전 노드 등)가
                # 정당하게 쥐고 있는 상태다. 여기에 hardware_reset 을 쏘면
                # 그 프로그램의 스트림이 끊긴다 -- 기다리기만 한다.
                # busy 가 아닌 실패(wedge/케이블)만 리셋으로 복구를 시도한다.
                if "busy" in str(e).lower():
                    time.sleep(2.0)
                elif not first_open:
                    self._hardware_reset()
                else:
                    time.sleep(2.0)
                first_open = False
                continue
            first_open = False
            align = rs.align(rs.stream.color)
            scale = profile.get_device().first_depth_sensor().get_depth_scale()
            intr = profile.get_stream(rs.stream.color) \
                .as_video_stream_profile().get_intrinsics()
            self.alive = True
            self.last_error = ""
            _log(f"{self.serial}: 스트림 시작 "
                 f"({self.w}x{self.h}@{self.fps}, depth_scale={scale})")
            seq = 0
            t_fps, n_fps = time.time(), 0
            timeouts = 0
            while self.running:
                try:
                    frames = pipe.wait_for_frames(FRAME_WAIT_MS)
                    timeouts = 0
                except Exception as e:  # noqa: BLE001
                    if not self.running:
                        break          # 종료 중이면 오류가 아니다
                    timeouts += 1
                    self.last_error = f"프레임 없음: {e}"
                    _log(f"{self.serial}: {self.last_error} ({timeouts}회)")
                    if timeouts >= FRAME_TIMEOUTS_BEFORE_REOPEN:
                        break  # 스트림 사망 -> 재오픈 루프로
                    continue
                cf, df = frames.get_color_frame(), frames.get_depth_frame()
                if not cf or not df:
                    continue
                ts = time.time()
                rgb = np.asanyarray(cf.get_data())          # (H,W,3) u8
                z16 = np.asanyarray(df.get_data())          # (H,W)   u16
                self.pub.send(f"{self.serial}/color",
                              {"ts": ts, "shape": rgb.shape,
                               "dtype": "uint8", "seq": seq},
                              rgb.tobytes())
                # depth 는 lerobot read_latest_depth 와 같은 (H,W,1) 의미론
                self.pub.send(f"{self.serial}/depth",
                              {"ts": ts, "shape": (self.h, self.w, 1),
                               "dtype": "uint16", "seq": seq,
                               "depth_scale": scale},
                              z16.tobytes())
                seq += 1
                n_fps += 1
                if ts - t_fps >= 2.0:
                    self.fps_actual = n_fps / (ts - t_fps)
                    t_fps, n_fps = ts, 0
                if self._aligned_req.is_set():
                    self._aligned_req.clear()
                    try:
                        af = align.process(frames)
                        adf, acf = af.get_depth_frame(), af.get_color_frame()
                        self._aligned_result = {
                            "z16": np.asanyarray(adf.get_data()).copy(),
                            "rgb": np.asanyarray(acf.get_data()).copy(),
                            "depth_scale": scale,
                            "intrinsics": {"fx": intr.fx, "fy": intr.fy,
                                           "ppx": intr.ppx, "ppy": intr.ppy},
                        }
                    except Exception as e:  # noqa: BLE001
                        _log(f"{self.serial}: 정렬 실패: {e}")
                        self._aligned_result = None
                    self._aligned_done.set()
            self.alive = False
            self._aligned_result = None
            self._aligned_done.set()  # 대기 중인 요청을 깨운다 (결과 None)
            try:
                pipe.stop()
            except Exception:  # noqa: BLE001
                pass
            if self.running:
                # 스트림이 죽었다 -- 오늘 실측한 wedge 시나리오. 리셋 후 재오픈.
                self._hardware_reset()

    def status(self) -> dict:
        return {"serial": self.serial, "alive": self.alive,
                "fps": round(self.fps_actual, 1), "resets": self.resets,
                "error": self.last_error}


class FakeCameraWorker(CameraWorker):
    """하드웨어 없는 합성 프레임 (테스트 전용). 움직이는 세로줄 RGB +
    평면 depth. 정렬 요청은 같은 프레임을 그대로 돌려준다."""

    def _hardware_reset(self) -> None:  # noqa: D102
        time.sleep(0.1)

    def run(self) -> None:
        self.alive = True
        _log(f"{self.serial}: FAKE 스트림 시작 ({self.serial})")
        seq = 0
        base = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        base[:, :, 0] = np.linspace(0, 255, self.w, dtype=np.uint8)[None, :]
        while self.running:
            ts = time.time()
            rgb = base.copy()
            x = (seq * 7) % self.w
            rgb[:, x:x + 12, 1] = 255                      # 움직이는 초록 줄
            z16 = np.full((self.h, self.w), 800 + seq % 50, dtype=np.uint16)
            self.pub.send(f"{self.serial}/color",
                          {"ts": ts, "shape": rgb.shape,
                           "dtype": "uint8", "seq": seq}, rgb.tobytes())
            self.pub.send(f"{self.serial}/depth",
                          {"ts": ts, "shape": (self.h, self.w, 1),
                           "dtype": "uint16", "seq": seq,
                           "depth_scale": 0.001}, z16.tobytes())
            if self._aligned_req.is_set():
                self._aligned_req.clear()
                self._aligned_result = {
                    "z16": z16, "rgb": rgb, "depth_scale": 0.001,
                    "intrinsics": {"fx": 600.0, "fy": 600.0,
                                   "ppx": self.w / 2, "ppy": self.h / 2},
                }
                self._aligned_done.set()
            seq += 1
            self.fps_actual = float(self.fps)
            time.sleep(1.0 / self.fps)
        self.alive = False


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cam", action="append", default=[], metavar="ROLE:SERIAL",
                   help="예: --cam agent:338122300664 (여러 번 가능)")
    p.add_argument("--pub-port", type=int, default=DEFAULT_PUB_PORT)
    p.add_argument("--ctl-port", type=int, default=DEFAULT_CTL_PORT)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--fake", action="store_true", help="합성 프레임 (테스트)")
    p.add_argument("--die-with-parent", action="store_true",
                   help="부모(GUI)가 죽으면 커널이 SIGTERM 을 보낸다 -- "
                        "고아 노드가 카메라를 쥔 채 남지 않게")
    args = p.parse_args()
    if args.die_with_parent and sys.platform == "linux":
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(
            1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    if not args.cam:
        raise SystemExit("--cam SERIAL 이 최소 1개 필요합니다")

    ctx = zmq.Context()
    pub = _Publisher(ctx, args.pub_port)
    ctl = ctx.socket(zmq.REP)
    ctl.setsockopt(zmq.RCVTIMEO, 500)
    ctl.bind(f"tcp://*:{args.ctl_port}")

    cls = FakeCameraWorker if args.fake else CameraWorker
    workers: dict[str, CameraWorker] = {}
    for spec in args.cam:
        # 옛 형식(ROLE:SERIAL)도 받아준다 -- 뒤쪽이 시리얼이다. 노드는 역할을
        # 모르므로 앞부분은 버린다.
        serial = spec.rpartition(":")[2] or spec
        if not serial:
            raise SystemExit(f"--cam 형식 오류: {spec} (SERIAL)")
        workers[serial] = cls(serial, pub, args.width, args.height, args.fps)
        workers[serial].start()

    running = True

    def _stop(*_a) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _log(f"카메라 노드 시작: {sorted(workers)}"
         f" pub={args.pub_port} ctl={args.ctl_port}"
         + (" [FAKE]" if args.fake else ""))

    last_status = 0.0
    while running:
        now = time.time()
        if now - last_status >= 1.0:
            pub.send("status", {"ts": now, "cams": {
                r: w.status() for r, w in workers.items()}})
            last_status = now
        try:
            req = json.loads(ctl.recv())
        except zmq.error.Again:
            continue
        except Exception:  # noqa: BLE001 -- 형식 오류 요청
            ctl.send(json.dumps({"ok": False, "error": "bad request"}).encode())
            continue
        cmd = req.get("cmd")
        if cmd == "ping":
            ctl.send(json.dumps({"ok": True, "cams": {
                r: w.status() for r, w in workers.items()}}).encode())
        elif cmd == "aligned":
            # role 은 옛 이름 -- 노드는 역할을 모른다.
            w = workers.get(req.get("serial") or req.get("role", ""))
            res = w.request_aligned() if w else None
            if res is None:
                ctl.send_multipart([json.dumps(
                    {"ok": False, "error": "카메라가 살아있지 않음"}).encode()])
            else:
                ctl.send_multipart([
                    json.dumps({"ok": True,
                                "depth_scale": res["depth_scale"],
                                "intrinsics": res["intrinsics"],
                                "shape": list(res["z16"].shape)}).encode(),
                    res["z16"].tobytes(), res["rgb"].tobytes()])
        else:
            ctl.send(json.dumps({"ok": False,
                                 "error": f"unknown cmd {cmd}"}).encode())

    _log("종료 중...")
    for w in workers.values():
        w.running = False
    for w in workers.values():
        w.join(timeout=3)
    ctl.close(linger=0)
    pub._sock.close(linger=0)  # 닫지 않으면 ctx.term() 이 영원히 기다린다
    ctx.term()
    _log("종료 완료")


if __name__ == "__main__":
    main()
