"""FR3 policy client — runs on the FR3 controller computer (lerobot-venv).

Streams observations to the GPU policy server (mamba-embeddingvla
real_deploy/fr3_policy_server.py) and executes absolute joint-angle actions on
the robot. The only heavy compute here is IK (analytic, ~4 ms) for EE ckpts.

Data path per replan cycle (default 0.40 s = 8 executed steps @ 20 Hz):
  FR3ZMQRobot.get_observation()             joints 7 rad + gripper 0..1, 2x 640x480 RGB
    -> resize_rgb (libero_format)           square-crop 480^2 -> 256^2, crop_params 적용
    -> base64 JSON POST /predict            ~0.4 MB/request, on a background thread
    <- {"actions": [[dim] x 10]}            dim=7 EE-delta (ee ckpt) or 8 joint (joint ckpt)
  then every control tick (main thread, no network):
    q_meas = get_observation()              latest measured joints
    -> raw_to_joint (fr3_kinematics)        ee: re-anchor delta to q_meas + IK -> joint8
       (joint ckpt: pass chunk[idx] through)
    -> send_action at 20 Hz                 with per-step |dq| safety clamp

The per-tick re-anchor to the LATEST measured pose matches the training label
(each frame's EE-delta is relative to that frame's measured pose), so tracking
lag never accumulates. IK stays in the CONTROL thread (fresh q_meas); /predict
stays on the background thread. This is the client half of the server's
/predict + /step split — ee_step_to_joint is byte-identical to the server's /step.

Chunk boundaries do not stall the arm.  Inference for the next chunk is fired
CHUNK_LEAD ticks before the current one runs out, and the arriving chunk's
leading indices -- whose timestamps have already passed -- are dropped rather
than commanded.  See CHUNK_LEAD.

Prerequisites:
  * robot node running:  (pylibfranka-venv) python experiments/launch_nodes.py --robot fr3
  * policy server running on the GPU machine (see SERVER_URL)

Comm test WITHOUT the robot (synthetic obs, checks server round-trip + latency):
  python experiments/fr3_policy_client.py --dry-run

Real run:
  python experiments/fr3_policy_client.py [--instruction "..."] [--max-seconds 30]
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fr3_kinematics import ee_step_to_joint, fk, compute_proprio_single  # noqa: E402  (mamba real_deploy copy)

from gello.station import load_station

# 로봇/카메라/주파수는 스테이션 설정에서 온다 -- 수집 GUI 와 같은 파일을 읽으므로
# "수집 GUI와 동일" 이 주석이 아니라 구조로 보장된다.
# configs/stations/<이름>.yaml, GELLO_STATION 으로 선택.
STATION = load_station()

# ───────────────────────── CONFIG (edit me) ─────────────────────────
SERVER_URL = "http://155.230.189.77:8080"  # GPU 머신 IP:port (같은 /22 서브넷, RTT 0.24ms)
ROBOT_PORT = STATION.node.port             # launch_nodes.py ZMQ port
HOSTNAME = STATION.node.host
AGENT_CAMERA_SERIAL = STATION.camera("agent").serial
WRIST_CAMERA_SERIAL = STATION.camera("wrist").serial
FPS = STATION.fps                          # 학습 데이터와 동일 (20 Hz)
EXEC_HORIZON = 10                          # 청크 중 쓸 최대 개수 (10=full, 서버 청크와 동일)
# 청크 경계 정지를 없애는 겹치기 실행. 정책은 "인덱스 i = 관측시각 + i·dt"로 학습돼
# 있으므로, 그 약속을 벽시계와 맞추기만 하면 된다 — 재학습 불필요한 클라이언트 장부 정리다.
#   청크 끝 K틱 전에 관측을 떠서 백그라운드 추론 → 도착한 청크의 앞 K개는 버리고 K번째부터 실행
# 앞 K개를 버리는 게 핵심이다. 미리 뜬 관측으로 만든 인덱스 0..K-1은 *이미 지나간 시각*의
# 목표라, 그대로 쏘면 과거 목표를 명령하는 꼴이고 이는 데이터 쪽에서 잡아낸 후퇴(지연)
# 메커니즘을 타이밍 층에서 그대로 재현한다.
# K·50ms가 추론 예산이다 (2026-08-04 실측: 평균 34.9 / p95 64.4 / 최대 98.8 ms):
#   K=2 → 100ms 예산(p95 커버), 10개 중 8개 실행, 재계획 0.40s
#   K=3 → 150ms 예산(최대치 커버), 7개 실행, 0.35s
# 비용은 반응 지연 — 정책이 K틱 오래된 장면을 보므로 돌발 상황에 그만큼 늦게 반응한다.
# K=0이면 예전 순차 동작(경계에서 팔이 89ms 정지 → 0.5s 주기 2Hz 흔들림)으로 돌아간다.
CHUNK_LEAD = 2
RESET_POSE = "libero"                      # FR3_RESET_POSES key (수집 세션과 동일해야 함)
# 학습된 4개 태스크 (다른 문장을 주면 분포 밖 — 2026-08-03 수집분 117 에피소드):
#   pick up the {blue|white} cup and place it on the {blue|yellow} bowl
DEFAULT_INSTRUCTION = "pick up the white cup and place it on the yellow bowl"
RAMP_STEP = 0.05                           # rad/tick @20Hz — 홈 복귀 램프 (수집기와 동일)
# 안전 클램프: 스텝당 "명령 목표 - 측정 위치" 최대 괴리.
# 이건 속도 제한이 아니다 — 실제 속도/가속/저크 제한은 로봇 노드의 레퍼런스 필터
# (v_max 1.0 rad/s, a_max 4.0 rad/s^2, 1 kHz)가 하고, 이 값과 무관하게 항상 건다.
# 여기서 하는 일은 "명령이 실측보다 얼마나 앞서 나갈 수 있는가"의 상한이며,
# 리더 명령 액션 공간에서는 그 앞섬 자체가 신호다(리더가 팔로워를 끌고 가는 힘).
# 학습 데이터 실측: p95 0.254 / p99 0.395 / p99.9 0.707 rad.
# 0.15이면 프레임의 20%가 잘려나가 — 우리가 고친 지연 버그를 클라이언트에서 재현한다.
MAX_STEP_RAD = 0.50                        # p99.5(0.469) 통과, 폭주 액션은 여전히 차단
GRIPPER_OPEN = 0.0
# ─────────────────────────────────────────────────────────────────────


def _b64(img: np.ndarray) -> dict:
    img = np.ascontiguousarray(img, dtype=np.uint8)
    return {"base64": base64.b64encode(img.tobytes()).decode(),
            "shape": list(img.shape), "dtype": "uint8"}


def raw_to_joint(d: np.ndarray, q_meas: np.ndarray, ee_mode: bool) -> np.ndarray:
    """One raw /predict chunk action -> absolute joint action [8] (client-side /step).

    ee: re-anchor the EE-delta to the LATEST measured pose + IK. joint: passthrough.
    Identical to the server's step(); the difference is q_meas is fresh here."""
    return ee_step_to_joint(d, q_meas[:7]) if ee_mode else np.asarray(d, dtype=float)


def dry_run(url: str, instruction: str, n: int = 5) -> None:
    """Server round-trip test with synthetic obs — robot/cameras NOT required.

    Exercises the full client path: /predict then local step on each chunk row."""
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)
    state = np.array([0.0, -0.161, 0.0, -2.445, 0.0, 2.227, 0.785, 0.0])  # libero reset
    print(f"[dry-run] POST {url}/reset ... (서버가 없으면 여기서 최대 60초 대기)")
    r = requests.post(f"{url}/reset", json={"instruction": instruction}, timeout=60)
    r.raise_for_status()
    print(f"[dry-run] /reset ok: {r.json()}")
    for i in range(n):
        payload = {
            "observation.state": state.tolist(),
            "observation.images.agent": _b64(img),
            "observation.images.wrist": _b64(img),
        }
        t0 = time.perf_counter()
        r = requests.post(f"{url}/predict", json=payload, timeout=60)
        r.raise_for_status()
        ms = (time.perf_counter() - t0) * 1000
        chunk = np.asarray(r.json()["actions"], dtype=float)  # [K, dim]
        ee_mode = chunk.shape[1] == 7
        a0 = raw_to_joint(chunk[0], state, ee_mode)  # local step, q_meas = reset pose
        mode = "EE-delta+local-step" if ee_mode else "joint-absolute"
        print(f"[dry-run] /predict #{i}: {chunk.shape[0]}x{chunk.shape[1]} ({mode}), "
              f"round-trip {ms:.1f} ms | step->joints(rad)={np.round(a0[:7], 3)} grip={a0[7]:.2f}")
    print("[dry-run] OK — comm path + local step verified.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=SERVER_URL)
    ap.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    ap.add_argument("--dry-run", action="store_true",
                    help="server comm test with synthetic obs (no robot needed)")
    ap.add_argument("--max-seconds", type=float, default=30.0)
    ap.add_argument("--exec-horizon", type=int, default=EXEC_HORIZON,
                    help="청크 중 쓸 최대 개수 (긴 청크를 더 자주 재계획하고 싶을 때만 줄인다)")
    ap.add_argument("--lead-ticks", type=int, default=CHUNK_LEAD, metavar="K",
                    help="청크 끝 K틱 전에 미리 추론하고 도착 청크의 앞 K개를 버린다 "
                         "(0=예전 순차 동작, 경계 정지 발생)")
    ap.add_argument("--proprio", action="store_true",
                    help="UMI proprioception을 계산해 /predict에 첨부 (use_eef_proprio 모델).")
    args = ap.parse_args()

    # 첫 출력까지 조용한 구간(카메라/로봇/서버 연결)이 길다 — 시작 즉시 설정을 보여준다.
    print(f"[client] server {args.server} | instruction {args.instruction!r}")
    print(f"[client] exec-horizon {args.exec_horizon}, lead-ticks {args.lead_ticks}, "
          f"max-seconds {args.max_seconds}" + (" (dry-run)" if args.dry_run else ""))

    if args.dry_run:
        dry_run(args.server, args.instruction)
        return

    from lerobot.cameras.realsense import RealSenseCameraConfig

    from gello.lerobot_plugin import JOINT_KEYS, FR3ZMQRobot, FR3ZMQRobotConfig
    from gello.libero_format import load_crop_params, resize_rgb
    from gello.robots.franka_fr3 import FR3_RESET_POSES

    # 정책 입력의 프레이밍은 학습 데이터와 같아야 한다. 수집기가 쓰는 것과
    # 같은 crop_params.json 을 읽는다 -- 예전에는 resize_rgb 를 인자 없이
    # 불러서 zoom=1.0/x=0 고정이었고, 그건 agent zoom 1.2 / wrist +31px 로
    # 찍힌 데이터와 프레이밍이 어긋난다.
    #
    # 체크포인트가 *다른* 크롭으로 학습됐다면 그 값을 써야 한다. 각
    # 에피소드의 실제 값은 hdf5 의 attrs["crop_params"] 에 남아 있다.
    crop = load_crop_params()
    print(f"[crop] agent={crop['agent']} wrist={crop['wrist']}", flush=True)

    def _crop_resize(img, role: str):
        p = crop[role]
        return resize_rgb(img, zoom=p["zoom"], x_shift=p["x"], y_shift=p["y"])

    print(f"[client] connecting robot(ZMQ {HOSTNAME}:{ROBOT_PORT}) + RealSense "
          f"agent={AGENT_CAMERA_SERIAL} wrist={WRIST_CAMERA_SERIAL} ... "
          f"(카메라가 안 붙어 있으면 여기서 멈춘다)")
    robot = FR3ZMQRobot(FR3ZMQRobotConfig(
        id="fr3", host=HOSTNAME, port=ROBOT_PORT,
        cameras={
            role: RealSenseCameraConfig(
                serial_number_or_name=serial,
                fps=STATION.camera(role).fps,
                width=STATION.camera(role).width,
                height=STATION.camera(role).height)
            for role, serial in (("agent", AGENT_CAMERA_SERIAL),
                                 ("wrist", WRIST_CAMERA_SERIAL))
        }))
    robot.connect()
    print("[client] robot + cameras connected.")
    reset_q = FR3_RESET_POSES[RESET_POSE]

    def joints(obs) -> np.ndarray:
        return np.array([obs[k] for k in JOINT_KEYS[:7]])

    def command(q7: np.ndarray, grip: float) -> None:
        robot.send_action(dict(zip(JOINT_KEYS, np.append(q7, grip).tolist())))

    session = requests.Session()   # keep-alive — 매 요청 TCP 핸드셰이크 제거
    predict_ms: list[float] = []

    def predict(obs: dict) -> np.ndarray:
        """POST /predict. 워커 스레드에서 돈다 — ZMQ 소켓도 카메라도 만지지 않는다.

        정책 raw 청크만 받는다(EE-delta[7] or joint[8]). delta->joint 재앵커+IK는
        제어 스레드가 매 틱 fresh q_meas로 하므로 여기서 하지 않는다.
        관측 읽기(get_observation)는 반드시 제어 스레드에 남겨야 한다: ZMQ 소켓은
        스레드 안전하지 않다. 여기서는 이미 읽어둔 obs만 다룬다.
        resize+base64(실측 4.2ms)도 여기서 하므로 제어 틱 예산 50ms를 쓰지 않는다.
        obs의 이미지는 카메라 스레드가 매 프레임 새 배열로 갈아끼우므로(rebind이지
        in-place 쓰기가 아니다) 참조를 그대로 넘겨도 프레임이 찢어지지 않는다.
        """
        payload = {
            "observation.state": [float(obs[k]) for k in JOINT_KEYS],
            "observation.images.agent": _b64(_crop_resize(obs["agent"], "agent")),
            "observation.images.wrist": _b64(_crop_resize(obs["wrist"], "wrist")),
        }
        if args.proprio:
            q_cur = np.array([obs[k] for k in JOINT_KEYS[:7]], dtype=float)
            grip_cur = float(obs[JOINT_KEYS[7]])
            q_prev, grip_prev = q_hist[-2] if len(q_hist) >= 2 else (q_cur, grip_cur)
            payload["proprio"] = compute_proprio_single(
                q_cur, q_prev, start_q, grip_cur, grip_prev).tolist()
        t0 = time.perf_counter()
        r = session.post(f"{args.server}/predict", json=payload, timeout=60)
        r.raise_for_status()
        predict_ms.append((time.perf_counter() - t0) * 1000)
        return np.asarray(r.json()["actions"], dtype=float)  # [10, dim] raw

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="predict")

    try:
        # ── 홈 복귀 램프 (수집기 _ramp_to와 동일 상수) ──
        print(f"[client] ramping to reset pose '{RESET_POSE}' ...")
        for _ in range(600):
            obs = robot.get_observation()
            q = joints(obs)
            d = reset_q - q
            if np.abs(d).max() < 0.02:
                break
            command(q + np.clip(d, -RAMP_STEP, RAMP_STEP), GRIPPER_OPEN)
            time.sleep(1.0 / FPS)
        else:
            raise RuntimeError("reset ramp did not converge")
        print("[client] at reset pose.")
        from collections import deque
        q_hist = deque(maxlen=8)                       # (q7, grip) 20Hz 히스토리 (proprio 0.1s전)
        start_q = joints(robot.get_observation()) if args.proprio else None

        print(f"[client] POST {args.server}/reset ... (서버가 없으면 여기서 최대 60초 대기)")
        r = requests.post(f"{args.server}/reset",
                          json={"instruction": args.instruction}, timeout=60)
        r.raise_for_status()
        print(f"[client] /reset ok: {r.json()['instruction']!r}")

        dt = 1.0 / FPS
        K = max(0, args.lead_ticks)
        pending = None      # 진행 중인 백그라운드 추론 (Future)
        t_obs = 0.0         # pending을 만든 관측을 뜬 시각 — 청크 인덱스 0의 기준 시각
        n_replans = n_late = 0

        # 부트스트랩: 첫 청크는 겹칠 이전 청크가 없어 블로킹으로 받는다.
        # 팔이 홈 자세에서 정지 중이라 이 한 번의 정지는 무해하다.
        chunk = predict(robot.get_observation())
        ee_mode = chunk.shape[1] == 7   # 7=EE-delta(클라 재앵커+IK), 8=joint(passthrough)
        idx = 0
        n_replans = 1
        if len(chunk) < args.exec_horizon:
            # 서버 청크가 요청보다 짧으면 슬라이스가 조용히 잘린다 -- 실제 재계획
            # 주기가 의도와 달라지므로 한 번은 눈에 보이게 알린다.
            print(f"[client] 주의: 서버 청크 {len(chunk)}개 < exec-horizon "
                  f"{args.exec_horizon} — 실제로 쓰는 건 {len(chunk)}개")
        print(f"[client] chunk {chunk.shape} mode={'ee' if ee_mode else 'joint'}, "
              f"lead {K}틱(추론 예산 {K*1000//FPS} ms), "
              f"재계획 {(min(len(chunk), args.exec_horizon) - K) / FPS:.2f}s")

        deadline = time.monotonic() + args.max_seconds
        t_next = time.monotonic()
        while time.monotonic() < deadline:
            horizon = min(len(chunk), args.exec_horizon)

            # (1) 청크 소진 → 교체. 관측시각으로부터 실제로 흐른 틱 수만큼 앞을 버린다.
            #     nominal이면 skip == K지만, 추론이 예산을 넘겼으면 그만큼 더 버린다 —
            #     "인덱스 i = 관측시각 + i·dt"를 벽시계에 맞추는 것이 규칙이고 K는 그 결과일 뿐.
            if idx >= horizon:
                if pending is None:                 # K=0 또는 예외 경로 — 예전 순차 동작
                    t_obs = time.monotonic()
                    chunk = predict(robot.get_observation())
                else:
                    if not pending.done():
                        n_late += 1                 # 예산 초과 — 여기서 경계 정지가 난다
                    chunk = pending.result()
                    pending = None
                n_replans += 1
                horizon = min(len(chunk), args.exec_horizon)
                skip = round((time.monotonic() - t_obs) / dt)
                if skip >= horizon:
                    print(f"[client] 경고: 추론({predict_ms[-1]:.0f} ms)이 청크 길이를 넘겼다 "
                          f"(skip {skip} >= {horizon}) — 마지막 액션만 실행한다. "
                          f"--lead-ticks를 올리거나 --exec-horizon을 늘려라.")
                idx = min(max(skip, 0), horizon - 1)
                if n_replans % 4 == 1:
                    print(f"[client] replan #{n_replans}: predict {predict_ms[-1]:.0f} ms, "
                          f"chunk {chunk.shape}, skip {skip}")

            # (2) 끝 K틱 전 → 다음 추론을 미리 띄운다. 추론이 도는 동안 남은 K틱이
            #     계속 팔을 먹이므로 경계에서 명령이 끊기지 않는다.
            if pending is None and K > 0 and idx >= horizon - K:
                t_obs = time.monotonic()
                obs = robot.get_observation()       # ZMQ는 제어 스레드에서만
                pending = pool.submit(predict, obs)

            # (3) 한 틱 실행 — 매 틱 최신 측정 pose에 재앵커(ee) / passthrough(joint)
            _o = robot.get_observation()
            q_meas = joints(_o)
            if args.proprio:
                q_hist.append((q_meas, float(_o[JOINT_KEYS[7]])))
            a = raw_to_joint(chunk[idx], q_meas, ee_mode)   # 클라측 /step (fresh anchor)
            idx += 1
            # 안전 클램프: 목표가 측정치에서 MAX_STEP_RAD 이상 벗어나지 않게
            q_tgt = q_meas + np.clip(a[:7] - q_meas, -MAX_STEP_RAD, MAX_STEP_RAD)
            command(q_tgt, float(np.clip(a[7], 0.0, 1.0)))

            t_next += dt
            sleep_s = t_next - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            elif sleep_s < -dt:
                t_next = time.monotonic()   # 크게 밀림 — 몰아치기 방지로 틱 시계 재동기

        print(f"[client] done ({n_replans} replans, 예산 초과 {n_late}회).")
        if predict_ms:
            p50, p95 = np.percentile(predict_ms, [50, 95])
            print(f"[client] /predict {p50:.0f} / p95 {p95:.0f} / max {max(predict_ms):.0f} ms "
                  f"(예산 {K*1000//FPS} ms)")
    except KeyboardInterrupt:
        print("\n[client] interrupted.")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        robot.disconnect()
        print("[client] disconnected.")


if __name__ == "__main__":
    main()
