feat(ui): 프로세스 항목을 Process 메뉴 하나로 모은다 (9-1, 이슈 #37C)

`tasks/_공통.md` 를 먼저 읽으세요.

## 왜

수집기는 여러 프로세스가 함께 도는데, 그것을 다루는 메뉴 항목이 세 곳에
흩어져 있어 "무엇을 어디서 켜고 끄는지" 를 외워야 한다. 한곳에 모은다.

**이번 작업은 이관입니다.** 새 기능을 만들지 마세요 -- 항목을 옮기고
메뉴를 하나 만드는 것이 전부입니다.

## 만들 것

`apps/workspace/shell/toolbar.py` 의 `build_menu` 에 Process 메뉴를 추가하고,
아래 항목을 **지금 있는 자리에서 옮깁니다** (복제 아님):

    Process
      로봇 노드 시작            <- Robot 의 "노드 시작"
      로봇 노드 종료            <- Robot 의 "노드 종료"
      ─────────
      카메라 노드 재시작         <- Camera 의 "카메라 노드 재시작"
      카메라 노드 종료 (카메라 해제)  <- Camera 의 "카메라 노드 종료 (카메라 해제)"
      ─────────
      시스템 튜닝 실행 (runme.sh)     <- Tools
      리더암 서보 보호 해제 (재부팅)   <- Tools
      카메라 점검 (USB 속도·프레임)    <- Tools

옮기고 나면 이렇게 남아야 합니다:

    Robot   연결 / 세션 종료 / 홈으로            (프로세스가 아닌 것)
    Camera  새로고침 / 미리보기 중지              (프로세스가 아닌 것)
    Tools   Hugging Face 계정... / 데이터셋 구조 사용자 설정... / 언어 전환 (미개발)

메뉴 순서는 Robot 과 Camera 사이에 Process 를 넣으세요.

## 반드시 지킬 것

- **항목의 문구를 한 글자도 바꾸지 마세요.** 옮기기만 합니다. 문구가 바뀌면
  `test_ui_surface` 가 잡습니다 -- 그때 기준선을 고치지 말고 문구를 되돌리세요.
- **슬롯 연결도 그대로.** 같은 메서드에 붙습니다.
- 새 메서드를 만들지 마세요. 상태 표시, 중단 버튼 같은 것은 이번 범위 밖입니다.

## 기준선 갱신 (이번엔 필요합니다)

메뉴 구조가 **의도적으로** 바뀌므로 `tests/gui/ui_surface_baseline.json` 을
갱신해야 합니다:

    QT_QPA_PLATFORM=offscreen /home/franka/lerobot-venv/bin/python \
      tests/gui/test_ui_surface.py --update

갱신 전후로 **항목의 총 개수가 같아야 합니다** (이관이지 추가가 아니므로).
개수가 달라졌으면 옮기다 흘린 것이니 찾아서 고치세요. 갱신한 JSON 의 diff 를
보고에 적으세요 -- 사람이 그 diff 를 봅니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

23개 전부 통과.

## 보고

옮긴 항목, 각 메뉴에 남은 항목, 기준선 JSON diff, 총 항목 수(전/후).
