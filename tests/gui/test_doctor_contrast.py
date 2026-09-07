"""Doctor 화면의 글자 대비 검사 (#49).

규칙만 적어 두면 다음 사람이 또 어긴다 -- 실제로 어겼다. 그래서 기계가
잡는다: 스타일시트 한 덩이 안에 background 와 color 가 함께 있으면 그 둘의
대비를 재고, WCAG AA(본문 4.5:1)에 못 미치면 실패시킨다.

같은 덩이 안에 background 가 없는 규칙은 부모 바탕을 알 수 없으므로, 이
파일이 아는 바탕 목록(_ON) 과 짝지어 검사한다. Doctor 가 놓이는 바탕은
흰 패널(#ffffff)과 창 바탕(#efefef) 둘이다.

여기서 다루지 않는 것: 테두리 같은 비텍스트 대비(3:1). 그것은 "무엇이
컴포넌트인가" 를 사람이 판단해야 해서 기계가 셀 수 없다 -- 뱃지 테두리는
손으로 3:1 로 맞춰 두었고 주석에 이유를 남겼다.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOCTOR = ROOT / "apps" / "workspace" / "features" / "doctor"

#: 색이 얹히는 바탕. 창 바탕과 흰 패널 -- 둘 다에서 읽혀야 한다.
_ON = ("#ffffff", "#efefef")

#: 대비를 잴 수 없는(=글자가 아닌) 속성.
_SKIP_PROPS = ("border", "background")

AA = 4.5


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(h: str) -> float:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


_RULE = re.compile(r'"([^"]*(?:color|background)\s*:[^"]*)"')
_COLOR = re.compile(r'(?<!background-)\bcolor\s*:\s*(#[0-9a-fA-F]{3,8})')
_BG = re.compile(r'background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})')


def main() -> None:
    bad = []
    checked = 0
    for path in sorted(DOCTOR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            for chunk in _RULE.findall(line):
                fgs = _COLOR.findall(chunk)
                if not fgs:
                    continue
                bgs = _BG.findall(chunk) or list(_ON)
                for fg in fgs:
                    if len(fg.lstrip("#")) == 8:
                        bad.append(f"{path.name}:{line_no} 8자리 hex {fg} "
                                   "-- Qt 는 #AARRGGBB 로 읽는다")
                        continue
                    for bg in bgs:
                        checked += 1
                        r = ratio(fg, bg)
                        if r < AA:
                            bad.append(
                                f"{path.name}:{line_no} {fg} on {bg} "
                                f"= {r:.2f}:1 (AA {AA}:1 미달)")
    assert not bad, "\n  " + "\n  ".join(bad)
    print(f"test_doctor_contrast OK ({checked}쌍)")


if __name__ == "__main__":
    main()
