"""도메인이 창에서 읽는 이름이 실제로 있는지 (2026-09-01, Phase 4 안전망).

메서드를 도메인 모듈로 옮기는 일은 `self.x` 를 `self.win.x` 로 바꾸는
치환이다. 하나를 빠뜨리거나 오타를 내도 임포트는 되고 테스트도 통과한다 --
그 버튼을 누르는 순간에만 죽는다. 분해 세 단계 내내 이 방식으로 세 번
샜다(Tools 메뉴 두 항목, Layout 격자).

그래서 진짜 창을 하나 만들어 놓고, 도메인 소스에 나오는 `self.win.<이름>`
전부가 그 창에 실제로 있는지 본다. 정적 grep 과 살아 있는 인스턴스를 함께
쓰는 것이 요점이다.

도메인이 아직 없으면 아무것도 검사하지 않고 통과한다 -- Phase 4 가
진행되면서 저절로 촘촘해진다.
"""
import ast
import os
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("GELLO_NO_PRIVILEGED", "1")

DOMAINS = WT / "apps" / "workspace" / "domains"
FEATURES = WT / "apps" / "workspace" / "features"
mods = sorted({
    p
    for base in (DOMAINS, FEATURES)
    for p in base.rglob("*.py")
    if p.name != "__init__.py"
})
# features/ 의 모든 모듈을 본다. ops.py 만 보던 때는 planning.py 나 depth.py
# 처럼 이름이 다른 도메인 파일이 검사 밖이었다 (2026-09-04).

if not mods:
    print("도메인 모듈이 아직 없습니다 -- 검사할 것 없음 (Phase 4 시작 전)")
    raise SystemExit(0)

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

sys.path.insert(0, str(WT / "apps"))
import collect_workspace as cw  # noqa: E402

# 창을 만들면 카메라를 열고 미리보기를 띄운다 -- 기계가 없어도 되게 막는다
# (다른 인수 테스트가 쓰는 것과 같은 스텁).
cw.CameraOps.refresh_cameras = lambda self: None
cw.CameraOps.restart_previews = lambda self: None
cw.SystemOps.startup_tuning = lambda self: None
win = cw.WorkspaceWindow(None)

# 창이 만든 뒤에야 생기는 것들 -- 여기에 적는 것은 "없어도 정상"이라는 선언이다.
LATE = {
    "trim_plots", "series_plots",     # 분석 탭을 처음 열 때 만든다
    "_camera_check_process",          # Tools > 카메라 점검 실행 시 만들어진다
}

missing = {}
for p in mods:
    tree = ast.parse(p.read_text(encoding="utf-8"))
    names = set()
    for n in ast.walk(tree):
        # self.win.<이름>  또는  win.<이름>
        if not isinstance(n, ast.Attribute):
            continue
        v = n.value
        if isinstance(v, ast.Attribute) and v.attr == "win" \
                and isinstance(v.value, ast.Name) and v.value.id == "self":
            names.add(n.attr)
        elif isinstance(v, ast.Name) and v.id == "win":
            names.add(n.attr)
    bad = sorted(a for a in names - LATE if not hasattr(win, a))
    if bad:
        missing[p.name] = bad

assert not missing, (
    "도메인이 창에 없는 이름을 읽습니다 -- 옮기면서 빠뜨렸거나 오타입니다.\n"
    "이 이름들은 임포트와 테스트를 통과하고, 그 버튼을 누를 때만 죽습니다:\n"
    + "\n".join(f"  {k}: {v}" for k, v in missing.items()))

n = sum(1 for _ in mods)
print(f"1. 도메인 {n}개, 창에서 읽는 이름 전부 실재 OK")

# ------------------------------------------------------------------ 2
# 반대 방향: connect() 가 가리키는 슬롯이 실제로 있는가.
#
# 1번은 "도메인이 창에서 읽는 이름" 을 본다. 그런데 실제로 터진 사고는 그
# 반대였다 -- 창이 w.episode_discarded.connect(self._on_discarded) 로 자기
# 자신의 없는 메서드를 가리키고 있었다. Phase 4-8 이 그 메서드를 지우면서
# 도메인으로 옮기지 않았고, 조작자가 테이크를 버릴 때마다 죽었다.
# 시그널 연결은 부를 때까지 조용하므로 여기서 미리 풀어 본다.
import re  # noqa: E402

# self 가 창인 곳(collect_workspace.py)과, win 이 창인 곳(features/shell)만
# 본다. 대화상자 안의 self 는 그 대화상자라 여기서 풀면 안 된다.
PATTERNS = (
    (WT / "apps" / "collect_workspace.py", r"\.connect\(\s*self((?:\.\w+)+)\s*\)"),
)
bad = []
for path, pat in PATTERNS:
    src = path.read_text(encoding="utf-8")
    for m in re.finditer(pat, src):
        parts = m.group(1).lstrip(".").split(".")
        line = src[:m.start()].count("\n") + 1
        obj = win
        for part in parts:
            obj = getattr(obj, part, None)
            if obj is None:
                bad.append(f"{path.relative_to(WT)}:{line}  self.{'.'.join(parts)}")
                break
# features / shell 에서는 win.<...> 가 창이다.
for path in sorted((WT / "apps" / "workspace").rglob("*.py")):
    src = path.read_text(encoding="utf-8")
    for m in re.finditer(r"\.connect\(\s*(?:self\.win|win)((?:\.\w+)+)\s*\)", src):
        parts = m.group(1).lstrip(".").split(".")
        line = src[:m.start()].count("\n") + 1
        obj = win
        for part in parts:
            obj = getattr(obj, part, None)
            if obj is None:
                bad.append(f"{path.relative_to(WT)}:{line}  win.{'.'.join(parts)}")
                break
assert not bad, (
    "connect() 가 없는 슬롯을 가리킨다 -- 그 시그널이 처음 울리는 순간 죽는다:\n  "
    + "\n  ".join(bad))
print("2. connect() 대상이 전부 실재 OK")
win.close()
win.deleteLater()
app.processEvents()

# 진짜 창을 하나 만들어 두었으므로 인터프리터를 정상 종료시키면 Qt 객체가
# 잘못된 순서로 풀리며 "double free" 로 죽는다 (검사는 이미 다 끝난 뒤라
# 결과는 맞는데 종료 코드만 0이 아니게 된다 -- run_all.sh 는 그것을 FAIL 로
# 읽는다). 창을 만드는 다른 인수 테스트들과 같은 방식으로 끝낸다.
import os  # noqa: E402

sys.stdout.flush()   # os._exit 는 버퍼를 비우지 않는다
os._exit(0)
