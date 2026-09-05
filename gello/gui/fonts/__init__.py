"""앱 글꼴을 건다 -- 어떤 글꼴을, 어떻게 걸고, 실패하면 어떻게 하는가.

**왜 글꼴을 명시하는가.** 지금까지 아무것도 지정하지 않아 Qt 가 주는
``Sans Serif``(-> DejaVu Sans)로 돌았는데, DejaVu 에는 한글이 없어서 한글만
fontconfig 폴백으로 빠졌다 -- 이 기계에서는 Noto Sans CJK **JP** 였다. 한
라벨 안에서 영문과 한글의 글꼴이 갈리고, 폴백 결과가 기계마다 달라 화면이
재현되지 않는다.

**왜 D2Coding 인가.** 한글·한자·라틴·괘선문자를 한 파일에서 모두 그리는
고정폭 글꼴이다. 괘선문자(│─┌)를 1칸, 한글을 정확히 2칸으로 그려서
``apps/workspace/shared/widgets.py`` 의 배치 격자가 한글이 섞여도 안 어긋난다
(그전까지 쓰던 DejaVu Sans Mono 는 한글이 1.53칸이라 이미 어긋나 있었다).

**왜 시스템에 설치하지 않는가.** ``QFontDatabase.addApplicationFont`` 는 이
프로세스에만 등록한다. root 도, fontconfig 갱신도, 다른 앱에 미치는 영향도
없다. 설치 스크립트가 필요 없고, 기계를 갈아도 캐시만 다시 채우면 된다.

글꼴이 **어디서 오는지**는 여기가 모른다 -- :mod:`gello.gui.fonts.d2coding`
이 릴리스 하나를 핀으로 잡고 있고, 갈아탈 때는 그 파일만 바뀐다.
"""
from __future__ import annotations

import os

from PyQt6.QtGui import QFontDatabase

from gello.gui.fonts import d2coding

#: 스타일시트의 font-family 가 쓰는 이름. 등록에 실패해도 이름 자체는 유효한
#: 값이라, 스타일시트는 뒤에 폴백을 달아 두면 그대로 동작한다.
FAMILY = d2coding.FAMILY

#: 1 이면 받지 않는다. 캐시가 이미 있으면 그건 그대로 쓴다 -- 막는 것은
#: 네트워크지 글꼴이 아니다. 인수 테스트는 네트워크 없이 돌아야 하므로
#: run_all.sh 가 켠다. 카메라·로봇 노드의 GELLO_NO_* 와 같은 계약.
NO_DOWNLOAD_ENV = "GELLO_NO_FONT_DOWNLOAD"

#: 고정폭이 필요한 자리의 스타일시트 ``font-family`` 값.
#:
#: D2Coding 자체가 고정폭이라 :func:`ensure_font` 가 성공하면 앱 전체가 이미
#: 고정폭이고 이 스택은 같은 값을 한 번 더 말하는 셈이다. 그래도 남겨 둔다 --
#: 글꼴 준비가 실패하면 앱 글꼴은 비례폭으로 돌아가는데, 격자·JSON·경로처럼
#: 칸이 맞아야 읽히는 자리는 그때도 고정폭이어야 한다. 뒤의 둘이 그 폴백이고,
#: 그전까지 이 자리들이 쓰던 값 그대로다.
MONO_STACK = f"'{FAMILY}', 'DejaVu Sans Mono', 'Liberation Mono', monospace"

__all__ = ["FAMILY", "MONO_STACK", "NO_DOWNLOAD_ENV", "ensure_font", "set_bold"]


def set_bold(widget, point_size: int) -> None:
    """위젯이 물려받은 글꼴에서 **크기와 굵기만** 바꾼다.

    ``widget.setFont(QFont("", 20, QFont.Weight.Bold))`` 이 하던 일처럼
    보이지만 다르다. 빈 패밀리는 "앱 글꼴을 물려받는다"가 아니라 "이름 없는
    글꼴"이라, Qt 가 폰트 DB 에서 임의로 하나를 골라 준다 -- 이 기계에서는
    세리프인 Bitstream Charter 가 걸려서, 큰 글씨 자리만 본문과 다른 서체로
    나왔다. 심지어 한 라벨 안에서 영문은 세리프, 한글은 폴백 산세리프로
    갈렸다. :func:`ensure_font` 로 앱 글꼴을 바꿔도 그 자리들은 안 따라온다.

    위젯의 현재 글꼴에서 출발하므로 앱 글꼴을 무엇으로 바꾸든 함께 간다.
    """
    font = widget.font()
    font.setPointSize(point_size)
    font.setBold(True)
    widget.setFont(font)


def ensure_font(app) -> bool:
    """글꼴을 등록하고 ``app`` 의 기본 글꼴로 건다. 성공하면 True.

    **수집을 막지 않는다.** 오프라인이거나 릴리스가 사라져도 경고만 남기고
    지금까지의 동작(시스템 글꼴)으로 돌아간다 -- run_scene_collector.sh 가
    git pull 실패를 다루는 것과 같은 원칙이다. 글꼴 때문에 수집이 멈추는 쪽이
    글꼴이 다른 쪽보다 나쁘다.

    크기는 건드리지 않는다. 앱이 이미 가진 point size 를 그대로 두고 이름만
    바꾸므로, 위젯들이 상대 크기로 잡아 둔 배치가 흔들리지 않는다.
    """
    ttfs = d2coding.cached_ttfs()
    if not ttfs:
        if os.environ.get(NO_DOWNLOAD_ENV) == "1":
            return False
        try:
            d2coding.fetch()
        except Exception as exc:      # noqa: BLE001 - 어떤 실패든 폴백이 답이다
            d2coding.say(f"내려받기 실패 -- 시스템 글꼴로 진행합니다: {exc}")
            return False
        ttfs = d2coding.cached_ttfs()
        if not ttfs:
            d2coding.say("캐시가 비어 있습니다 -- 시스템 글꼴로 진행합니다")
            return False

    families: set[str] = set()
    for path in ttfs:
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid < 0:
            d2coding.say(f"등록 실패: {path.name}")
            continue
        families.update(QFontDatabase.applicationFontFamilies(fid))
    if FAMILY not in families:
        d2coding.say(f"'{FAMILY}' 가 등록되지 않았습니다 {sorted(families)} "
                     "-- 시스템 글꼴로 진행합니다")
        return False

    font = app.font()
    font.setFamily(FAMILY)
    app.setFont(font)
    return True
