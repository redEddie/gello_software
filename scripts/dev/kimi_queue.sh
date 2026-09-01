#!/usr/bin/env bash
# 작업 지시서를 순서대로 시킨다. 하나라도 실패하면 거기서 멈춘다.
#
#     bash scripts/dev/kimi_queue.sh tasks/03-*.md
#     tmux new -d -s kimi 'bash scripts/dev/kimi_queue.sh tasks/03-*.md'
#     tmux attach -t kimi          # 진행 상황 보기
#
# 실패 지점에서 멈추는 것이 핵심이다. 깨진 상태 위에 다섯 개를 더 쌓으면
# 아침에 어디서부터 봐야 할지 알 수 없게 된다 (2026-08-31 에 겪었다).
# 푸시하지 않는다 -- 아침에 사람이 보고 올린다.
set -uo pipefail

WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WT" || exit 2
[ $# -gt 0 ] || { echo "사용: $0 <작업지시서.md> [...]"; exit 2; }

LOGDIR="$WT/.kimi-runs"; mkdir -p "$LOGDIR"
SUMMARY="$LOGDIR/$(date +%Y%m%d-%H%M%S)-queue.md"
START="$(git rev-parse HEAD)"

{
  echo "# 밤샘 실행 $(date '+%Y-%m-%d %H:%M')"
  echo
  echo "- 브랜치: \`$(git rev-parse --abbrev-ref HEAD)\`  시작: \`$START\`"
  echo "- 대기열 $#개: $*"
  echo
  echo "| # | 작업 | 결과 | 커밋 | 소요 |"
  echo "|---|------|------|------|------|"
} > "$SUMMARY"

i=0; failed=""
for task in "$@"; do
  i=$((i+1)); t0=$SECONDS
  echo "=== [$i/$#] $task ==="
  if bash scripts/dev/kimi_task.sh "$task"; then
    printf '| %d | `%s` | 통과 | `%s` | %dm |\n' \
      "$i" "$(basename "$task")" "$(git rev-parse --short HEAD)" \
      "$(( (SECONDS-t0)/60 ))" >> "$SUMMARY"
  else
    printf '| %d | `%s` | **실패 — 여기서 멈춤** | - | %dm |\n' \
      "$i" "$(basename "$task")" "$(( (SECONDS-t0)/60 ))" >> "$SUMMARY"
    failed="$task"; break
  fi
done

{
  echo
  if [ -n "$failed" ]; then
    echo "## 멈춘 곳: \`$failed\`"
    echo
    echo "변경은 되돌렸으므로 트리는 깨끗합니다. 마지막 로그의 FAIL 부분을 보세요:"
    echo
    echo '```'
    ls -t "$LOGDIR"/*.log | head -1
    grep -n 'FAIL\|중단\|실패' "$(ls -t "$LOGDIR"/*.log | head -1)" | tail -15
    echo '```'
  else
    echo "## 대기열 전부 통과"
  fi
  echo
  echo "### 아침에 할 일"
  echo
  echo '```bash'
  echo "cd $WT"
  echo "git log --oneline $START..HEAD"
  echo "git diff --stat $START..HEAD"
  echo "# GUI 로 눈으로 확인한 뒤에 올린다"
  echo "git push"
  echo '```'
} >> "$SUMMARY"

echo; echo "요약: $SUMMARY"; cat "$SUMMARY"
[ -z "$failed" ]
