"""apps/ 구조 계약 -- 이름 하나에 정의 하나, 화살표는 한쪽 (2026-09-01).

collect_workspace.py 를 쪼개는 동안 세 번 다 같은 종류의 사고가 났고, 셋 다
테스트도 화면도 멀쩡한 채로 지나갔다:

  1단계  클래스를 새 모듈로 '복사'하고 원본을 안 지웠다 (5곳). 그중 4개는
         임포트를 데려오지 않아 부르는 순간 NameError 가 날 상태였다.
  3단계  함수를 옮기면서 호출부를 안 고쳤다 (_grid_overlay). 그리고 옮긴
         함수 바로 아래 붙어 있던 상수 두 개(CHECK_CAMERAS, RESET_PROTECTION)가
         같이 지워졌다 -- Tools 메뉴 두 항목이 눌리는 순간 죽는 상태였다.
  매 단계  옮겨간 이름의 임포트가 원본 파일에 고아로 남았다.

셋 다 "모듈 최상위에서 해석되지 않는 이름"이거나 "한 이름에 정의 둘"이다.
둘 다 소스만 읽으면 기계로 잡힌다. 남은 분해(페이지 7개 + 상태 객체 +
도메인)가 훨씬 얽힌 구간이므로 여기서 못박는다.

로봇도 화면도 필요 없다: AST 만 읽는다.
"""
import ast
import builtins
import sys
from collections import defaultdict
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

APPS = WT / "apps"
mods = sorted(p for p in APPS.rglob("*.py"))
assert len(mods) > 10, f"{APPS} 에서 모듈을 거의 못 찾았다 -- 경로가 바뀌었나?"
trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in mods}


def _rel(p: Path) -> str:
    return str(p.relative_to(WT))


# ------------------------------------------------------------------ 1
where = defaultdict(list)
for p, t in trees.items():
    for n in t.body:
        if isinstance(n, ast.ClassDef):
            where[n.name].append(_rel(p))
dupes = {k: v for k, v in where.items() if len(v) > 1}
assert not dupes, f"같은 클래스가 여러 모듈에 정의돼 있다: {dupes}"
print(f"1. apps/ 클래스 {len(where)}개, 중복 정의 없음 OK")

# ------------------------------------------------------------------ 2
# 모듈 최상위에서 해석되지 않는 이름 = 옮기다 만 자국. 사본이 임포트를 안
# 데려왔거나, 원본을 지웠는데 호출부가 남았거나, 옮긴 것 옆에 붙어 있던
# 정의가 같이 지워졌거나.
BUILTIN = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
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
        elif isinstance(n, ast.Global):
            bound |= set(n.names)
    used = {n.id for n in ast.walk(t)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    unresolved = used - bound
    if unresolved:
        missing[_rel(p)] = sorted(unresolved)
assert not missing, f"모듈 안에서 해석되지 않는 이름이 있다(옮기다 만 자국?): {missing}"
print(f"2. apps/ 모듈 {len(mods)}개 모두 이름 해석 OK")

# ------------------------------------------------------------------ 3
# 화살표는 한쪽이다. collect_workspace 가 분해된 조각들을 부르는 것이지,
# 조각이 collect_workspace 를 되부르면 안 된다 -- 되부르는 순간 "디렉터리만
# 봐도 의존이 보인다"가 깨지고, 다음 조각을 뗄 때 순환이 된다.
back = {}
for p, t in trees.items():
    if p.name == "collect_workspace.py":
        continue
    hits = set()
    for n in ast.walk(t):
        mod = n.module if isinstance(n, ast.ImportFrom) else None
        names = [a.name for a in n.names] if isinstance(n, ast.Import) else []
        if (mod and "collect_workspace" in mod) or any("collect_workspace" in x for x in names):
            hits.add(n.lineno)
    if hits:
        back[_rel(p)] = sorted(hits)
assert not back, f"조각이 collect_workspace 를 되부른다(화살표 역류): {back}"
print(f"3. apps/workspace·apps/dialogs -> collect_workspace 임포트 없음 OK")

# ------------------------------------------------------------------ 4
# 패키지 안의 순환 임포트. 생기면 임포트 순서에 따라 GUI 가 뜨다 말고 죽는다.
edges = {}
for p, t in trees.items():
    deps = set()
    for n in ast.walk(t):
        if not isinstance(n, ast.ImportFrom):
            continue
        if n.level and n.module:                       # from .x import ...
            base = p.parent / n.module.split(".")[-1]
        elif (n.module or "").startswith("apps."):
            base = WT / n.module.replace(".", "/")
        else:
            continue
        # 패키지를 임포트하면 그 __init__.py 가 실행된다. 이것을 빠뜨리면
        # 패키지를 낀 순환을 통째로 놓친다 -- 실제로 pages -> builders ->
        # pages 순환이 이 구멍으로 지나갔다 (2026-09-02). 앱은 임포트 순서가
        # 우연히 맞아 떴고, pages 를 먼저 임포트하면 ImportError 였다.
        deps.add(base.with_suffix(".py"))
        deps.add(base / "__init__.py")
        # 하위 모듈을 임포트하면 그 부모 패키지의 __init__.py 가 **먼저**
        # 실행된다. `from apps.workspace.sizing import x` 는
        # builders/__init__.py 를 돌리므로, 파일 단위로만 보면 없는 고리가
        # 패키지 단위로는 생긴다. 이 한 줄이 없어서 pages -> builders ->
        # pages 순환이 지나갔다.
        # 자기 패키지 안의 형제 모듈은 제외한다: 그때 부모 __init__ 은 이미
        # sys.modules 에 있어 다시 돌지 않는다.
        for parent in base.parents:
            if parent == WT:
                break
            if parent in p.parents:
                continue
            deps.add(parent / "__init__.py")
    # 패키지 __init__ 이 자기 하위 모듈을 부르는 것은 순환이 아니다.
    edges[p] = {d for d in deps if d in trees and d != p}

seen, stack = set(), set()


def visit(node: Path, path: list) -> None:
    if node in stack:
        raise AssertionError(
            "apps/ 안에 순환 임포트: " + " -> ".join(_rel(x) for x in path + [node]))
    if node in seen:
        return
    stack.add(node)
    for d in sorted(edges.get(node, ()), key=str):
        visit(d, path + [node])
    stack.discard(node)
    seen.add(node)


for m in sorted(edges, key=str):
    visit(m, [])
print("4. apps/ 순환 임포트 없음 OK")

# ------------------------------------------------------------------ 5
# 패키지 __init__ 이 광고하는 이름은 실제로 임포트돼야 한다.
import apps.workspace.shared as D   # noqa: E402
import apps.workspace.shell as B    # noqa: E402

for mod in (D, B):
    names = getattr(mod, "__all__", None)
    assert names is not None, f"{mod.__name__} 에 __all__ 이 없다 -- 공개 API 가 불분명하다"
    for name in names:
        assert hasattr(mod, name), f"{mod.__name__}.__all__ 의 {name} 을 임포트할 수 없다"
    print(f"5. {mod.__name__}.__all__ {len(names)}개 임포트 OK")

# ------------------------------------------------------------------ 6
# 옮긴 이름의 임포트가 원본에 고아로 남는 일이 분해 매 단계에 있었다 (마지막에
# 49개까지 쌓였다). 고아 자체는 죽지 않지만, 임포트 목록이 실제 의존과
# 어긋나면 "파일만 봐도 의존이 보인다"가 성립하지 않는다.
ALLOWED_UNUSED = {
    # (파일, 이름): 이유
    ("apps/fr3_policy_client.py", "fk"): "mamba real_deploy 사본과 줄을 맞춘 재수출",
}
stale = {}
for p, t in trees.items():
    imported = {}
    for n in ast.walk(t):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                imported.setdefault(a.asname or a.name.split(".")[0], n.lineno)
    if p.name == "__init__.py":
        continue                      # 재수출이 일이다
    src = p.read_text(encoding="utf-8")
    used = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            v = n.value
            while isinstance(v, ast.Attribute):
                v = v.value
            if isinstance(v, ast.Name):
                used.add(v.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            used |= set(n.value.replace("|", " ").replace(".", " ").split())
    bad = [k for k in imported
           if k not in used and k != "annotations"
           and (_rel(p), k) not in ALLOWED_UNUSED]
    if bad:
        stale[_rel(p)] = sorted(bad)
assert not stale, f"쓰지 않는 임포트가 남아 있다(옮기고 안 지운 자국?): {stale}"
print(f"6. apps/ 고아 임포트 없음 OK (허용 목록 {len(ALLOWED_UNUSED)}개 제외)")

print("\napps/ 구조 계약 통과")
