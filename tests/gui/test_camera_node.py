"""카메라 노드/클라이언트 검증 (2026-08-25 3-프로세스 분리).

--fake 노드를 서브프로세스로 띄워 실제 ZMQ 경로로 검증한다:
발행/구독, 나이 계약(TimeoutError), depth 의미론, ping, 정렬 요청,
시리얼 불일치 거부, 노드 사망 시 실패 모드."""
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])  # 리포 루트
sys.path.insert(0, WT)

from gello.comm.camera_client import NodeCamera, fetch_aligned, node_ping  # noqa: E402

PUB, CTL = 16021, 16022  # 실제 기본 포트와 겹치지 않게

node = subprocess.Popen(
    [sys.executable, "-m", "gello.comm.camera_node",
     "--cam", "FAKE-A", "--cam", "FAKE-W",
     "--pub-port", str(PUB), "--ctl-port", str(CTL), "--fake"],
    cwd=WT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    # ---- 1. ping ----
    info = None
    for _ in range(40):
        info = node_ping(ctl_port=CTL, timeout_ms=500)
        if info:
            break
        time.sleep(0.2)
    assert info and info["ok"], info
    # 노드의 신원은 시리얼이다 -- 역할(agent/wrist)은 데이터세트 계층의
    # 이름이라 전송 계층이 알지 않는다 (2026-09-05 3층 분리).
    assert info["cams"]["FAKE-A"]["serial"] == "FAKE-A"
    assert info["cams"]["FAKE-W"]["alive"] is True
    assert "agent" not in info["cams"], "노드가 역할을 알면 안 된다"
    print("1. ping/상태 OK")

    # ---- 2. connect + read_latest (RGB, 갱신 확인) ----
    cam = NodeCamera("FAKE-A", pub_port=PUB, ctl_port=CTL)
    cam.connect()
    f1 = cam.read_latest(max_age_ms=500)
    assert f1.shape == (480, 640, 3) and f1.dtype == np.uint8
    time.sleep(0.15)
    f2 = cam.read_latest(max_age_ms=500)
    assert not np.array_equal(f1, f2), "프레임이 갱신되지 않음"
    print("2. color 구독/갱신 OK")

    # ---- 3. depth: lerobot 과 같은 (H,W,1) u16 ----
    d = cam.read_latest_depth(max_age_ms=500)
    assert d.shape == (480, 640, 1) and d.dtype == np.uint16
    assert 700 < int(d[0, 0, 0]) < 1000
    print("3. depth 의미론 OK")

    # ---- 4~5. 노드가 안 여는 시리얼 -> ConnectionError ----
    for missing in ("OTHER", "agent"):
        try:
            NodeCamera(missing, pub_port=PUB, ctl_port=CTL).connect()
            raise AssertionError(f"없는 카메라({missing})를 거부하지 않음")
        except ConnectionError as e:
            assert missing in str(e), str(e)
    print("4~5. 안 여는 시리얼 거부 OK (역할 이름도 시리얼이 아니라 거부)")

    # ---- 6. 정렬 요청 (포인트클라우드 경로) ----
    al = fetch_aligned("FAKE-A", ctl_port=CTL)
    assert al is not None
    assert al["z"].shape == (480, 640) and al["z"].dtype == np.float32
    assert 0.7 < float(al["z"][0, 0]) < 1.0        # 800mm * 0.001
    assert al["rgb"].shape == (480, 640, 3)
    assert al["intrinsics"]["fx"] == 600.0
    print("6. 정렬 요청 OK")

    # ---- 7. 노드 사망 -> read 는 TimeoutError, ping 은 None ----
    node.terminate()
    node.wait(timeout=5)
    time.sleep(0.7)
    cam.read_latest(max_age_ms=60000)              # 캐시는 아직 있다 (나이 큼)
    try:
        cam.read_latest(max_age_ms=300)
        raise AssertionError("죽은 노드에서 too old 가 나지 않음")
    except TimeoutError as e:
        assert "too old" in str(e)
    assert node_ping(ctl_port=CTL, timeout_ms=400) is None
    print("7. 노드 사망 실패 모드 OK")

    cam.disconnect()
    assert not cam.is_connected
finally:
    if node.poll() is None:
        node.kill()
    out = node.stdout.read().decode(errors="replace")
    print("--- 노드 로그 ---")
    print(out[-600:])

print("\n카메라 노드/클라이언트 검증 통과")
