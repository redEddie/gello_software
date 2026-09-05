"""로봇에 붙어 포스·토크 후보 필드와 부하(load) 설정을 확인한다.

    (pylibfranka-venv) python scripts/check/check_ft_fields.py

**로봇 노드를 먼저 끄세요.** FCI 는 클라이언트를 하나만 받습니다.
팔은 움직이지 않습니다 -- read_once() 만 반복합니다.

왜 이 스크립트가 있나: knu-1.1.0 에 넣은 ``tau_J_d`` 가 실제로는 항상 0
이었다 (위치 제어라 libfranka 가 채우지 않는다). 필드가 *존재한다*는 것과
*값이 온다*는 것은 다르고, 그 차이는 로봇에 붙어야만 알 수 있다. 스키마에
필드를 넣기 전에 여기를 먼저 통과시킨다.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

try:
    import pylibfranka as pf
except ImportError:  # noqa: BLE001
    sys.exit("pylibfranka 를 못 찾았습니다 -- pylibfranka-venv 로 실행하세요:\n"
             "  /home/franka/pylibfranka-venv/bin/python "
             "scripts/check/check_ft_fields.py")

#: 확인할 후보. (필드, 기대 길이, 설명)
CANDIDATES = [
    ("tau_J", 7, "측정 관절토크 -- knu-1.1.x 확정"),
    ("tau_ext_hat_filtered", 7, "외력 추정 관절토크 -- 확정"),
    ("O_F_ext_hat_K", 6, "외력 렌치, 베이스 좌표 -- 확정"),
    ("K_F_ext_hat_K", 6, "외력 렌치, EE(강성) 좌표 -- 확정"),
    ("dtau_J", 7, "관절토크 미분 (1kHz) -- 후보. 20Hz 차분으로는 못 만든다"),
    ("tau_J_d", 7, "명령 관절토크 -- 위치 제어에서는 0일 것 (검증용)"),
    ("joint_contact", 7, "접촉 플래그 -- lower==upper 라 0일 것 (검증용)"),
    ("cartesian_contact", 6, "접촉 플래그 -- 위와 같음"),
]

#: 부하 모델. 미신고 질량은 통째로 '외력'으로 계산되므로, 손목에 뭘 달았으면
#: 여기 들어가 있어야 한다.
LOAD_FIELDS = [
    ("m_ee", "엔드이펙터 질량 (kg) -- Desk 의 End-Effector 설정"),
    ("F_x_Cee", "엔드이펙터 무게중심 (m, 플랜지 기준)"),
    ("m_load", "추가 부하 질량 (kg) -- set_load() 로 넣는 값"),
    ("F_x_Cload", "추가 부하 무게중심 (m)"),
    ("m_total", "합계 질량 (kg)"),
    ("F_x_Ctotal", "합계 무게중심 (m)"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="172.16.0.2")
    ap.add_argument("--seconds", type=float, default=5.0,
                    help="샘플링 시간. 그 동안 팔을 손으로 움직여 주세요")
    ap.add_argument("--gripper", action="store_true",
                    help="그리퍼도 확인 (is_grasped). 손을 움직이지는 않습니다")
    args = ap.parse_args()

    print(f"[FCI] {args.ip} 에 연결합니다 (노드가 떠 있으면 실패합니다)...")
    robot = pf.Robot(args.ip)
    st = robot.read_once()
    print("[FCI] 연결됨.\n")

    # ---------------------------------------------------------------- 부하
    print("=" * 68)
    print("부하(load) 설정 -- 미신고 질량은 그대로 '외력'으로 계산됩니다")
    print("=" * 68)
    for name, why in LOAD_FIELDS:
        if not hasattr(st, name):
            print(f"  {name:12s} (이 빌드에 없음)")
            continue
        v = np.asarray(getattr(st, name), dtype=float)
        shown = f"{float(v):.4f}" if v.ndim == 0 or v.size == 1 else \
            "[" + ", ".join(f"{x:+.4f}" for x in v.ravel()) + "]"
        print(f"  {name:12s} = {shown}")
        print(f"  {'':12s}   {why}")
    m_total = float(np.asarray(getattr(st, "m_total", 0.0)).ravel()[0])
    print(f"\n  -> 합계 {m_total * 1000:.0f} g. 손목에 카메라를 달았는데 이 값이 "
          "그 무게를 포함하지\n     않으면, 그 무게만큼이 외력 추정에 상수로 "
          "섞입니다.")

    # -------------------------------------------------------------- 필드
    print("\n" + "=" * 68)
    print(f"필드 확인 -- {args.seconds:.0f}초 동안 샘플링합니다.")
    print("이 동안 **팔을 손으로 움직이고, 그리퍼 근처를 눌러 보세요.**")
    print("가만히 두면 '값이 안 온다'와 '변화가 없다'를 구분할 수 없습니다.")
    print("=" * 68)
    input("준비되면 Enter... ")

    have = [c for c in CANDIDATES if hasattr(st, c[0])]
    missing = [c[0] for c in CANDIDATES if not hasattr(st, c[0])]
    samples: dict[str, list] = {name: [] for name, _, _ in have}
    t_end = time.monotonic() + args.seconds
    n = 0
    while time.monotonic() < t_end:
        s = robot.read_once()
        for name, _, _ in have:
            samples[name].append(np.asarray(getattr(s, name), dtype=float))
        n += 1
        time.sleep(0.005)
    print(f"\n{n} 샘플 수집.\n")

    print(f"{'필드':24s}{'길이':>5s}{'0아닌 비율':>12s}{'|최소|':>10s}{'|최대|':>10s}")
    print("-" * 68)
    for name, want, why in have:
        a = np.stack(samples[name])
        if a.shape[1] != want:
            print(f"  {name:22s} 길이가 {a.shape[1]} 입니다 (예상 {want}) -- "
                  "스키마 정의를 고쳐야 합니다")
        nz = float((np.abs(a) > 1e-9).any(axis=1).mean())
        flag = "  <-- 항상 0" if nz == 0.0 else ""
        print(f"{name:24s}{a.shape[1]:5d}{100 * nz:11.1f}%"
              f"{np.abs(a).min():10.4f}{np.abs(a).max():10.4f}{flag}")
        print(f"    {why}")
    for name in missing:
        print(f"{name:24s} 이 빌드에 없습니다")

    # ------------------------------------------------------------ 그리퍼
    if args.gripper:
        print("\n" + "=" * 68)
        print("그리퍼 -- 힘 센서는 없습니다. is_grasped 가 유일한 파지 신호입니다")
        print("=" * 68)
        g = pf.Gripper(args.ip)
        for a in ("width", "max_width", "is_grasped", "temperature"):
            print(f"  {a:12s} = {getattr(g.read_once(), a, '(없음)')}")
        print()
        print("  수집 코드는 닫을 때 grasp(), 열 때 move() 를 씁니다 -- 그래서")
        print("  is_grasped 가 의미를 가질 조건은 맞습니다. 다만 epsilon 이 전")
        print("  스트로크라(franka_fr3.py 주석 참조) **빈 손으로 닫아도 참**일 수")
        print("  있습니다. 그러면 '뭔가 들고 있다' 신호로 못 씁니다.")
        print()
        print("  아래 두 번을 비교해 주세요 -- 다르면 쓸 수 있고, 같으면 못 씁니다.")
        for label in ("물체를 **쥐게** 한 뒤", "**빈 손으로** 닫은 뒤"):
            input(f"    {label} Enter... ")
            gs = g.read_once()
            print(f"      width={gs.width:.4f}  is_grasped={gs.is_grasped}")

    print("\n판단 기준: '0아닌 비율'이 0.0% 인 필드는 스키마에 넣지 마세요. "
          "tau_J_d 가\n그렇게 들어갔다가 knu-1.1.0 을 폐기하게 만들었습니다.")


if __name__ == "__main__":
    main()
