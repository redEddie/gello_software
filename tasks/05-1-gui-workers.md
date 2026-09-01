refactor(gui): 백그라운드 워커 4개와 순수 유틸을 gello/gui/ 아래로 분리 (5-1)

`tasks/_공통.md` 의 규칙 중 "동작을 바꾸지 말 것", "옮긴 뒤 grep 으로 셀 것",
"run_all.sh 하나로만 검증할 것" 이 그대로 적용됩니다. 도메인 클래스 방식은
여기 해당하지 않습니다 -- 이번엔 **파일을 옮기는 것**입니다.

## 왜 이 파일부터인가

`gello/gui/gui_widgets.py` 는 1,484줄인데 한 책임이 아니라 **독립 위젯 12개와
함수 8개의 모음**입니다. 파일 docstring 자체가 "마법사 GUI 에서 떼어내
워크스페이스가 재사용하려고 모아둔 것"이라고 말합니다. 서로 거의 참조하지
않으므로 가장 안전하고, `run_all.sh` 20개가 이미 지키고 있습니다.

## 이번에 옮길 것 -- 워커와 순수 유틸만

    gello/gui/workers.py
        EpisodeLoadWorker  GalleryLoadWorker  CameraPreviewWorker
        DepthCloudWorker
    gello/gui/text_utils.py
        is_progress_line  clean_stream_lines  repo_id_error

다이얼로그 5개와 `VideoView`/`Recents`/`DeltaBar` 는 **다음 조각**입니다.
건드리지 마세요.

## 방법

1. 새 모듈을 만들고 클래스·함수를 통째로 옮깁니다. 본문은 한 글자도
   바꾸지 마세요.
2. 그 클래스가 쓰는 임포트도 같이 가져갑니다. **옮긴 뒤 원본에서 고아가 된
   임포트를 지우세요** -- `test_app_structure` 는 `apps/` 만 보므로 여기서는
   사람이 세야 합니다:

       grep -cw <이름> gello/gui/gui_widgets.py     # 0 이어야 함

3. 쓰는 쪽의 임포트를 새 경로로 고칩니다. `gui_widgets` 를 임포트하는 파일이
   27개 있으니 반드시 grep 으로 전부 찾으세요:

       grep -rn 'EpisodeLoadWorker\|GalleryLoadWorker\|CameraPreviewWorker\|DepthCloudWorker\|is_progress_line\|clean_stream_lines\|repo_id_error' --include='*.py' . | grep -v third_party

4. **재수출 껍데기를 만들지 마세요.** `gui_widgets.py` 에서
   `from .workers import *` 같은 것으로 옛 경로를 살려두면 편하지만, 그러면
   무엇이 어디 있는지 디렉터리로 알 수 없게 됩니다. 이 저장소는 방금 그런
   껍데기(테스트가 `cw.PlanJsonDialog` 로 가져다 쓰던 것)를 없앴습니다.

## 주의

- `CameraPreviewWorker` 와 `DepthCloudWorker` 는 QThread 입니다. 카메라
  프레임 경로라 성능에 민감합니다 -- 코드를 만지지 말고 자리만 옮기세요.
- `PLAYBACK_FPS`, `REPACK_SCRIPT`, `TODO_MARK` 같은 상수는 이번에 옮기지
  마세요. 여러 그룹이 함께 쓰므로 마지막 조각에서 정리합니다.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python

20개 전부 통과해야 합니다.

## 보고

옮긴 것, 새 모듈 줄 수, `gui_widgets.py` 줄 수 변화, 고친 임포트 파일 수,
위 grep 결과를 한 문단으로.
