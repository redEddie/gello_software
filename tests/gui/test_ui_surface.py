"""GUI 표면 계약 -- 리팩토링이 눈에 보이는 것을 바꾸지 않았는지 (2026-09-01).

분해 작업 내내 진짜 위험은 "테스트는 통과하는데 메뉴 하나가 조용히 사라지는
것"이었다. 실제로 Tools 메뉴 두 항목이 눌리는 순간 죽는 채로 커밋될 뻔했다.
사람이 매번 grep 으로 세는 대신, 진짜 빌더를 호출해 만들어진 표면을 기준선과
대조한다 -- 정규식이 아니라 실제 코드가 만든 결과라서, 코드가 바뀌면 여기도
같이 바뀐다.

기준선을 고칠 때는 반드시 의도적으로:  python tests/gui/test_ui_surface.py --update

로봇도 카메라도 필요 없다 (offscreen). WorkspaceWindow 전체는 백그라운드
스레드 때문에 offscreen 에서 죽으므로, 빌더가 요구하는 최소한만 흉내 내는
QMainWindow 스텁에 붙여 만든다.
"""
import ast
import json
import re
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
BASELINE = Path(__file__).with_name("ui_surface_baseline.json")

from PyQt6.QtWidgets import QApplication, QMainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])

from apps.workspace.builders import (  # noqa: E402
    build_menu,
    build_statusbar,
    build_toolbar,
)
from apps.workspace.constants import ACTIVITIES  # noqa: E402
from apps.workspace.pages import PAGE_BUILDERS  # noqa: E402


class _NoOp:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Stub(QMainWindow):
    """빌더가 창에 요구하는 것 중 슬롯은 전부 no-op 로 받아준다."""

    def __init__(self):
        super().__init__()
        self.upload = _NoOp()
        self.playback_ops = _NoOp()
        self.camera_ops = _NoOp()
        self.dataset_ops = _NoOp()

    def __getattr__(self, name):
        if name.startswith("_"):
            return lambda *a, **k: None
        raise AttributeError(name)


def surface() -> dict:
    win = _Stub()
    build_menu(win)
    build_toolbar(win)
    build_statusbar(win)

    menus = {}
    for act in win.menuBar().actions():
        sub = act.menu()
        menus[act.text()] = [x.text() for x in sub.actions() if x.text()] if sub else []

    scripts = {}
    for script_path in (WT / "apps" / "collect_workspace.py",
                        WT / "apps" / "workspace" / "constants.py"):
        src = script_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in tree.body:
            if not isinstance(n, ast.Assign):
                continue
            name = getattr(n.targets[0], "id", "")
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                continue
            # ast.walk 는 소스 순서가 아니라 폭 우선이다 -- 경로 조각을 이어붙이려면
            # 위치로 다시 정렬해야 한다.
            seg = sorted((c for c in ast.walk(n.value)
                          if isinstance(c, ast.Constant) and isinstance(c.value, str)),
                         key=lambda c: (c.lineno, c.col_offset))
            parts = [c.value for c in seg]
            if parts and parts[-1].endswith((".py", ".sh")):
                scripts[name] = "/".join(parts)

    keys = set()
    for p in sorted((WT / "apps").rglob("*.py")):
        keys |= set(re.findall(r"Qt\.Key\.Key_(\w+)", p.read_text(encoding="utf-8")))

    return {
        "menus": menus,
        "toolbar": sorted(win.tb_actions),
        "status_lights": sorted(win.lights),
        "shortcuts": sorted(keys),
        "scripts": scripts,
        "activities": [k for k, *_ in ACTIVITIES],
        "page_builders": sorted(PAGE_BUILDERS),
    }


def _report(want: dict, got: dict) -> list:
    out = []
    for key in sorted(set(want) | set(got)):
        a, b = want.get(key), got.get(key)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    out.append(f"  {key}[{k!r}]:\n    기준선 {a.get(k)!r}\n    현재   {b.get(k)!r}")
        else:
            out.append(f"  {key}:\n    기준선 {a!r}\n    현재   {b!r}")
    return out


now = surface()

if "--update" in sys.argv:
    BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"기준선 갱신: {BASELINE}")
    raise SystemExit(0)

assert BASELINE.exists(), (
    f"{BASELINE} 이 없습니다. 처음이라면 --update 로 만드세요.")
want = json.loads(BASELINE.read_text(encoding="utf-8"))
diff = _report(want, now)
assert not diff, ("GUI 표면이 기준선과 다릅니다 -- 의도한 변경이면 "
                  "`python tests/gui/test_ui_surface.py --update` 로 기준선을 "
                  "갱신하고 그 갱신을 리뷰에 포함하세요.\n" + "\n".join(diff))

n_items = sum(len(v) for v in now["menus"].values())
print(f"1. 메뉴 {len(now['menus'])}개 / 항목 {n_items}개 기준선과 동일 OK")
print(f"2. 툴바 {len(now['toolbar'])}개, 상태표시등 {len(now['status_lights'])}개 동일 OK")
print(f"3. 단축키 {len(now['shortcuts'])}개 동일 OK: {' '.join(now['shortcuts'])}")
gone = {k: v for k, v in now["scripts"].items() if not (WT / v).exists()}
assert not gone, (f"GUI 가 실행할 스크립트가 그 경로에 없습니다: {gone} -- "
                  "파일을 옮겼다면 상수도 같이 고쳐야 합니다 (버튼을 누를 때만 죽습니다)")
print(f"4. 하위 프로세스 스크립트 {len(now['scripts'])}개 경로 동일, 파일 존재 OK")

assert now["activities"] and set(now["activities"]) == set(now["page_builders"]), (
    f"ACTIVITIES({now['activities']}) 와 PAGE_BUILDERS({now['page_builders']}) 의 "
    "키가 다릅니다 -- build_left 가 시작 즉시 KeyError 로 죽습니다")
print(f"5. ACTIVITIES == PAGE_BUILDERS ({len(now['activities'])}개) OK")

print("\nGUI 표면 계약 통과")
