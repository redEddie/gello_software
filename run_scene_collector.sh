#!/bin/bash
# Scene 기반 수집 GUI 런처 (main 브랜치, 유일한 아이콘). 바탕화면 아이콘용.
#
# 실행 전에 자동으로 git pull 을 시도한다 -- "아이콘은 눌렀는데 옛 코드가
# 돌고 있었다" 사고를 원천 차단하기 위해서다. 오프라인이거나 pull 이
# 실패하면 경고만 찍고 현재 코드로 그냥 실행한다 (수집을 막는 쪽이 더
# 나쁘다). 이미 떠 있는 GUI 는 pull 의 영향을 받지 않는다 -- 재시작해야
# 새 코드다.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
git pull --ff-only 2>/dev/null || echo "[scene-collector] git pull 실패 (오프라인?) -- 현재 코드로 실행합니다"
source /home/franka/lerobot-venv/bin/activate
exec python experiments/collect_workspace.py
