#!/usr/bin/env bash
# 작업 지시서 하나를 Kimi 에게 시키고, 통과했을 때만 커밋한다.
#
#     bash scripts/dev/kimi_task.sh tasks/03-1-session-state.md
#
# 사람이 없는 자리에서 도는 것을 전제로 만들었다:
#   - 관리자 비밀번호 창이 뜨지 않는다 (GELLO_NO_PRIVILEGED).
#   - 검증은 run_all.sh 하나로만 한다. 실패하면 커밋하지 않고 되돌린다.
#   - 푸시하지 않는다. 아침에 사람이 보고 올린다.
# 종료 코드: 0 통과·커밋, 1 실패(되돌림), 2 사용법/환경 문제.
set -uo pipefail

TASK="${1:-}"
[ -n "$TASK" ] || { echo "사용: $0 <작업지시서.md>"; exit 2; }
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WT" || exit 2
[ -f "$TASK" ] || { echo "지시서를 찾을 수 없습니다: $TASK"; exit 2; }

PY=/home/franka/lerobot-venv/bin/python
export KIMI_CODE_HOME="${KIMI_CODE_HOME:-$HOME/.kimi-code-chanwook}"
export GELLO_NO_PRIVILEGED=1          # pkexec 창이 뜨면 답할 사람이 없다
export PYTHONPATH=""                  # ROS 의 PYTHONPATH 가 lark 를 가린다

NAME="$(basename "$TASK" .md)"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOGDIR="$WT/.kimi-runs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/$STAMP-$NAME.log"
BEFORE="$(git rev-parse HEAD)"

say() { printf '%s | %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

# 시작 전에 깨끗해야 한다. 남은 변경 위에 얹으면 무엇이 이번 작업인지
# 알 수 없고, 실패했을 때 되돌릴 지점도 사라진다.
if [ -n "$(git status --porcelain)" ]; then
  say "중단: 작업 트리가 깨끗하지 않습니다"; git status --short | tee -a "$LOG"; exit 2
fi
say "시작 $NAME (기준 $BEFORE, 브랜치 $(git rev-parse --abbrev-ref HEAD))"

# 이번 작업 전에 이미 통과 상태인지 확인한다. 앞 작업이 남긴 실패를
# 이번 작업 탓으로 돌리지 않기 위해서다.
if ! bash tests/gui/run_all.sh "$PY" >>"$LOG" 2>&1; then
  say "중단: 작업 시작 전부터 테스트가 실패합니다"; exit 2
fi
say "사전 확인 통과"

timeout "${KIMI_TIMEOUT:-5400}" kimi -p "$(cat "$TASK")" >>"$LOG" 2>&1
RC=$?
say "kimi 종료 코드 $RC"

if [ "$RC" -ne 0 ]; then
  say "실패: kimi 가 정상 종료하지 않았습니다 -- 변경을 되돌립니다"
  git reset --hard "$BEFORE" >>"$LOG" 2>&1; git clean -fd >>"$LOG" 2>&1
  exit 1
fi

AFTER="$(git rev-parse HEAD)"
if [ "$AFTER" != "$BEFORE" ] && [ -z "$(git status --porcelain)" ]; then
  # kimi 가 스스로 커밋한 경우. 지시서에 "한 커밋에" 같은 말이 있으면 그렇게
  # 읽는다. 검증만 하고 그 커밋을 인정한다 -- 되돌렸다가 다시 만들 이유가 없다.
  say "kimi 가 직접 커밋함 ($(git rev-parse --short HEAD)) -- 검증만 한다"
  if bash tests/gui/run_all.sh "$PY" >>"$LOG" 2>&1; then
    say "검증 통과 -- 그 커밋을 인정"; exit 0
  fi
  say "검증 실패 -- kimi 의 커밋을 되돌립니다"
  grep -n 'FAIL' "$LOG" | tail -20 | tee -a "$LOG"
  git reset --hard "$BEFORE" >>"$LOG" 2>&1; git clean -fd >>"$LOG" 2>&1
  exit 1
fi

if [ -z "$(git status --porcelain)" ]; then
  say "변경 없음 -- 커밋할 것이 없습니다"; exit 1
fi

if bash tests/gui/run_all.sh "$PY" >>"$LOG" 2>&1; then
  say "검증 통과 -- 커밋"
  git add -A >>"$LOG" 2>&1
  git commit -q -m "$(head -1 "$TASK" | sed 's/^#* *//')" \
    -m "지시서: $TASK" -m "러너: scripts/dev/kimi_task.sh, 로그: ${LOG#$WT/}" \
    -m "검증: tests/gui/run_all.sh 전체 통과 (사람 검증 전)" >>"$LOG" 2>&1
  say "커밋 $(git rev-parse --short HEAD)"
  exit 0
fi

say "검증 실패 -- 변경을 되돌립니다. 진단은 로그의 FAIL 부분을 보세요"
grep -n 'FAIL' "$LOG" | tail -20 | tee -a "$LOG"
git reset --hard "$BEFORE" >>"$LOG" 2>&1; git clean -fd >>"$LOG" 2>&1
exit 1
