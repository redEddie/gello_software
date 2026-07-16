#!/usr/bin/env bash
# 실기 텔레옵 전에 실행한다.  재부팅했거나 GELLO USB를 다시 꽂았으면 또 실행해야 한다.
#
#     ./scripts/runme.sh
#
# 여기서 하는 것은 전부 (a) sudo가 필요하고 (b) 재부팅하면 초기화되는 것들이다.
# 서보 쪽 설정(baud 1 Mbps, Return Delay 0)은 서보 EEPROM에 있어 전원을 내려도
# 유지되므로 여기서는 확인만 한다 -- 되돌리려면 scripts/ 의 설정 스크립트를 쓴다.
#
# 로봇(FCI) 쪽 점검은 scripts/fr3_preflight.py 가 한다 (모션 없음, sudo 불필요).
set -uo pipefail

fail=0
ok()   { printf '  [OK]   %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; fail=1; }

echo "=== 1. FTDI latency timer ==="
# 기본값 16 ms.  다이나믹셀은 요청-응답이고 응답이 64 B USB 패킷을 절대 못 채우므로,
# FTDI 칩이 매 왕복마다 16 ms를 꽉 채워 기다린 뒤에야 데이터를 넘긴다.  1 ms로 내리면
# sync read 한 번이 18 ms -> 3 ms 가 된다 (55 Hz -> 340 Hz).
gello_port=$(ls /dev/serial/by-id/*FTDI* 2>/dev/null | head -1)
if [ -z "$gello_port" ]; then
  warn "GELLO를 못 찾음 (/dev/serial/by-id/*FTDI*) -- USB 연결 확인"
else
  tty=$(basename "$(readlink -f "$gello_port")")
  lat_path="/sys/bus/usb-serial/devices/$tty/latency_timer"
  if [ ! -e "$lat_path" ]; then
    warn "$lat_path 없음"
  elif [ "$(cat "$lat_path")" = "1" ]; then
    ok "latency_timer=1 ($tty, 이미 적용됨)"
  else
    echo 1 | sudo tee "$lat_path" > /dev/null
    if [ "$(cat "$lat_path")" = "1" ]; then
      ok "latency_timer=1 ($tty, 방금 설정)"
    else
      warn "latency_timer 설정 실패 (현재 $(cat "$lat_path"))"
    fi
  fi
fi

echo "=== 2. CPU governor ==="
# powersave는 주파수를 오르내리며 지연 스파이크를 만든다.  FR3 제어 루프는 1 kHz
# 마감을 놓치면 communication_constraints_violation 으로 죽는다.
#
# cpupower는 쓰지 않는다: 이 RT 커널에는 대응하는 linux-tools 패키지가 없어서
# 껍데기만 설치돼 있다.  sysfs에 직접 쓰는 쪽이 커널 버전과 무관하게 동작한다.
# 코어마다 별도 노드이므로 전부(cpu0가 아니라) 설정해야 한다.
govs=(/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor)
if [ ! -e "${govs[0]}" ]; then
  warn "cpufreq 노드가 없음 -- governor 설정 불가 (VM이거나 드라이버 미로드)"
elif ! grep -qw performance /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors; then
  warn "performance governor를 이 시스템이 지원하지 않음"
else
  n_total=${#govs[@]}
  n_perf=$(grep -lx performance "${govs[@]}" 2>/dev/null | wc -l)
  if [ "$n_perf" -eq "$n_total" ]; then
    ok "governor=performance ($n_total개 코어, 이미 적용됨)"
  else
    # stdout만 버린다: sudo 비밀번호 프롬프트는 stderr로 나가므로 살려둬야 한다
    printf 'performance\n' | sudo tee "${govs[@]}" > /dev/null
    n_perf=$(grep -lx performance "${govs[@]}" 2>/dev/null | wc -l)
    [ "$n_perf" -eq "$n_total" ] && ok "governor=performance ($n_total개 코어, 방금 설정)" \
      || warn "governor 설정 실패 ($n_perf/$n_total 코어만 적용됨)"
  fi
fi

echo "=== 3. 서보 설정 확인 (EEPROM, 유지됨) ==="
# 코드 기본값이 1 Mbps라, 공장 초기화된 서보(57600)를 꽂으면 통신이 안 된다.
venv_py="$HOME/pylibfranka-venv/bin/python"
if [ -z "$gello_port" ]; then
  warn "GELLO가 없어 건너뜀"
elif [ ! -x "$venv_py" ]; then
  warn "$venv_py 없음 -- 서보 확인 건너뜀"
else
  "$venv_py" - "$gello_port" <<'PY' || fail=1
import sys
from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler

ph = PortHandler(sys.argv[1])
if not ph.openPort() or not ph.setBaudRate(1000000):
    print("  [WARN] 1 Mbps로 포트를 열 수 없음"); raise SystemExit(1)
pk, bad = PacketHandler(2.0), []
for i in range(1, 9):
    baud, r1, _ = pk.read1ByteTxRx(ph, i, 8)   # Baud Rate: 3 = 1 Mbps
    rdt, r2, _ = pk.read1ByteTxRx(ph, i, 9)    # Return Delay Time: 0
    if r1 != COMM_SUCCESS or r2 != COMM_SUCCESS:
        bad.append(f"ID{i} 무응답")
    elif baud != 3 or rdt != 0:
        bad.append(f"ID{i} baud={baud}(3이어야 함) rdt={rdt}(0이어야 함)")
ph.closePort()
if bad:
    print("  [WARN] " + "; ".join(bad))
    print("         텔레옵이 떠 있으면 포트가 잡혀 실패한다.  아니라면 서보가")
    print("         초기화된 것이므로 57600으로 스캔해 다시 설정해야 한다.")
    raise SystemExit(1)
print("  [OK]   서보 8개 전부 1 Mbps / Return Delay 0")
PY
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "준비 완료.  로봇 쪽은: python scripts/fr3_preflight.py"
else
  echo "위 경고를 해결한 뒤 텔레옵을 시작할 것."
  exit 1
fi
