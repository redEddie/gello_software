"""D2Coding 릴리스 하나를 핀으로 고정해 받아 둔다 -- 이 파일이 그 핀이다.

레포에 글꼴 파일을 넣지 않는다. 4MB 짜리 바이너리 둘은 git 델타 압축이 안
먹어서, 한 번 커밋하면 그 뒤 모든 clone 이 값을 치른다. 대신 **핀 고정한 외부
의존성**으로 다룬다 -- 태그가 URL 에 박혀 있고 sha256 을 검사하므로 재현성은
lockfile 과 같다. pip 가 글꼴을 실어주지 못해서 그 역할을 여기가 대신한다.

글꼴을 갈아야 하면 이 파일만 바꾼다. 위(``__init__``)는 "어떤 이름의 글꼴을
앱에 건다"만 알고, 그것이 어디서 어떻게 왔는지는 모른다.

합자(ligature) 판은 받지 않는다 -- ``!=`` 를 한 글자로 붙여 그리는 것이 코드
편집기에서는 이점이지만, 이 GUI 는 사람이 읽는 문장과 수치를 띄우는 자리라
얻을 것이 없다.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

#: naver/d2-coding-font 릴리스. 태그가 URL 에 박혀 있어 이것이 곧 버전 고정이다.
#: 올릴 때는 네 상수를 함께 바꾼다 -- URL 만 바꾸면 sha256 이 걸러낸다.
VERSION = "1.3.3"
URL = ("https://github.com/naver/d2-coding-font/releases/download/"
       "VER1.3.3/D2Coding-Ver1.3.3-20260725.zip")
SHA256 = "c2a6e364d4102eb2c4de52ffe3d76317c1f4c045e3737e022e69ee0be47f31e2"

#: zip 안에서 꺼낼 것. OFL 은 재배포 조건이라 글꼴과 같은 자리에 둔다.
MEMBERS = (
    "D2Coding/D2Coding-Ver1.3.3-20260725.ttf",
    "D2Coding/D2CodingBold-Ver1.3.3-20260725.ttf",
    "OFL.txt",
)

#: 등록 뒤 Qt 가 부르는 이름. 스타일시트의 font-family 도 이 이름을 쓴다.
#: nerd-fonts 패치본은 OFL 예약 이름 규정 때문에 D2Koding 으로 개명돼 있어
#: 이 이름이 안 나온다 -- 그쪽으로 갈아탈 때는 이 값도 같이 바꿔야 한다.
FAMILY = "D2Coding"

CACHE_DIR = Path.home() / ".cache" / "gello" / "fonts" / f"d2coding-{VERSION}"

_TIMEOUT_S = 30.0


def say(msg: str) -> None:
    """창이 아직 없을 때 불리므로 win.log 가 아니라 stderr 로 남긴다."""
    print(f"[폰트] {msg}", file=sys.stderr)


def cached_ttfs() -> list[Path]:
    """캐시에 있는 ttf 목록. 하나라도 없으면 빈 목록 -- 반쪽 캐시는 없는 것과 같다."""
    want = [CACHE_DIR / Path(m).name for m in MEMBERS if m.endswith(".ttf")]
    return want if all(p.exists() for p in want) else []


def fetch() -> None:
    """받아서 sha256 을 검사하고 캐시에 푼다.

    임시 폴더에 다 채운 뒤 통째로 옮긴다 -- 받다가 죽어도 반쪽 캐시가 남지
    않는다. 실패는 예외로 올린다; 경고로 낮출지는 부르는 쪽이 정한다.
    """
    CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE_DIR.parent) as tmp:
        tmpdir = Path(tmp)
        blob = tmpdir / "d2coding.zip"
        say(f"D2Coding {VERSION} 내려받는 중 (21MB, 처음 한 번만)...")
        with urlopen(URL, timeout=_TIMEOUT_S) as resp:
            blob.write_bytes(resp.read())

        got = hashlib.sha256(blob.read_bytes()).hexdigest()
        if got != SHA256:
            raise ValueError(f"sha256 불일치: {got} != {SHA256}")

        staged = tmpdir / "staged"
        staged.mkdir()
        with zipfile.ZipFile(blob) as zf:
            for member in MEMBERS:
                # 이름만 남겨 평평하게 푼다. zip 안의 폴더 구조는 버전마다
                # 달라질 수 있고, 우리가 쓰는 것은 파일 세 개뿐이다.
                (staged / Path(member).name).write_bytes(zf.read(member))
        # 같은 버전을 두 프로세스가 동시에 받으면 뒤엣것이 진다 -- 내용이
        # 같으므로 어느 쪽이 이겨도 결과는 같다.
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        staged.replace(CACHE_DIR)
    say(f"캐시에 저장했습니다: {CACHE_DIR}")
