"""apps/dialogs 구조 계약 -- 이름 하나에 정의 하나 (2026-09-01).

collect_workspace.py 를 쪼개는 과정에서 클래스를 새 모듈로 '복사'하고 원본을
지우지 않는 일이 실제로 있었다 (SceneInfoView, StatusLight, PlanJsonDialog 가
각각 두 곳에 정의됐다). 사본은 아무도 쓰지 않아 테스트도 화면도 멀쩡했고,
그중 둘은 임포트가 빠져 있어 부르는 순간 NameError 가 날 상태였다.

한 이름이 두 정의를 갖는 순간 "디렉터리만 봐도 의존관계가 보인다" 는 원칙이
깨진다 -- 고친 쪽이 실제로 쓰이는 쪽인지 알 수 없기 때문이다. 분할이 계속되는
동안 같은 실수가 반복되지 않게 여기서 못박는다.

로봇도 화면도 필요 없다: 소스를 AST 로만 읽는다.
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

PKG = WT / "apps" / "dialogs"
mods = sorted(p for p in PKG.glob("*.py") if p.name != "__init__.py")
assert mods, f"{PKG} 에 모듈이 없다 -- 경로가 바뀌었나?"

trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in mods}

# ------------------------------------------------------------------ 1
where = defaultdict(list)
for p, t in trees.items():
    for n in t.body:
        if isinstance(n, ast.ClassDef):
            where[n.name].append(p.name)
dupes = {k: v for k, v in where.items() if len(v) > 1}
assert not dupes, f"같은 클래스가 여러 모듈에 정의돼 있다: {dupes}"
print(f"1. 클래스 {len(where)}개, 중복 정의 없음 OK")

# ------------------------------------------------------------------ 2
# 사본은 대개 임포트를 데려오지 않아 이름이 붕 뜬 채로 남는다. 모듈 최상위에서
# 해석되지 않는 이름이 있으면 사본을 의심할 자리다.
import builtins  # noqa: E402

BUILTIN = set(dir(builtins))
missing = {}
for p, t in trees.items():
    bound = set(BUILTIN)
    for n in ast.walk(t):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            bound |= {a.asname or a.name.split(".")[0] for a in n.names}
        elif isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    used = {n.id for n in ast.walk(t)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    unresolved = used - bound
    if unresolved:
        missing[p.name] = sorted(unresolved)
assert not missing, f"모듈 안에서 해석되지 않는 이름이 있다(사본 흔적?): {missing}"
print(f"2. 모듈 {len(mods)}개 모두 이름 해석 OK")

# ------------------------------------------------------------------ 3
# 패키지 안에서만 순환이 없어야 한다 -- 순환이 생기면 임포트 순서에 따라
# GUI 가 뜨다 말고 죽는다.
edges = {}
for p, t in trees.items():
    deps = set()
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("apps.dialogs."):
            deps.add(n.module.split(".")[-1] + ".py")
    edges[p.name] = deps

seen, stack = set(), set()


def visit(name: str, path: list) -> None:
    if name in stack:
        raise AssertionError(f"apps/dialogs 안에 순환 임포트: {' -> '.join(path + [name])}")
    if name in seen:
        return
    stack.add(name)
    for d in sorted(edges.get(name, ())):
        visit(d, path + [name])
    stack.discard(name)
    seen.add(name)


for m in sorted(edges):
    visit(m, [])
print("3. 패키지 내부 순환 임포트 없음 OK")

# ------------------------------------------------------------------ 4
# __init__.py 가 광고하는 이름은 실제로 임포트돼야 한다.
import apps.dialogs as D  # noqa: E402

for name in D.__all__:
    assert hasattr(D, name), f"__all__ 에 있는 {name} 을 임포트할 수 없다"
print(f"4. __init__.__all__ {len(D.__all__)}개 임포트 OK")

print("\napps/dialogs 구조 계약 통과")
