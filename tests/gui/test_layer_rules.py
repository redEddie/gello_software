"""gello/ 계층 규칙 -- 화살표는 아래로만, 상위는 구체 하드웨어를 모른다.

apps/ 는 test_app_structure.py 가 지키고 있었지만 gello/ 는 무방비였고,
그 사이에 순환 두 개가 조용히 생겼다 (core<->data, data<->scene). 둘 다
"공용 어휘가 기능 폴더 안에 살아서" 생긴 것이다.

여기서 못박는 두 가지:

  1. gello/ 안에 패키지 수준 순환이 없다.
  2. 상위 계층(data / scene / gui)은 구체 하드웨어 구현을 임포트하지 않는다.
     데이터 로거는 연결된 팔이 Franka 인지 시뮬레이터인지 몰라야 하고,
     알아야 하는 것은 core 의 추상뿐이다. 이 규칙이 깨지면 팔을 바꿀 때
     데이터·GUI 코드까지 따라 고쳐야 한다.

소스만 AST 로 읽는다. 로봇도 화면도 필요 없다.
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
G = WT / "gello"

# 계층 정의 -- 위에서 아래로만 부른다.
# 구체 구현 -- 상위가 이것을 알면 팔이나 카메라를 바꿀 때 상위까지 따라 고쳐야 한다.
HARDWARE = {"robots", "cameras", "hw", "sim", "agents"}
# gello/comm 은 여기 넣지 않는다. 프로세스 경계(ZMQ) 자체가 추상이라,
# GUI 가 카메라 노드에 붙는다는 것은 그 카메라가 RealSense 인지 모른다는 뜻이다.
UPPER = {"data", "scene", "gui"}

# 규칙을 어기지만 지금은 인정하는 것. 비어 있는 것이 목표다.
# (파일, 임포트하는 패키지): 이유
ALLOWED = {}


def pkg_of(p: Path) -> str:
    rel = p.relative_to(G).parts
    return rel[0] if len(rel) > 1 else ""


files = [p for p in sorted(G.rglob("*.py")) if "__pycache__" not in str(p)]
assert len(files) > 30, f"{G} 에서 파일을 거의 못 찾았다 -- 경로가 바뀌었나?"

imports = {}          # 파일 -> 그 파일이 임포트하는 gello 하위 패키지 집합
for p in files:
    mods = set()
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        mod = None
        if isinstance(n, ast.ImportFrom) and n.module and not n.level:
            mod = n.module
        elif isinstance(n, ast.Import):
            mod = n.names[0].name
        if mod and mod.startswith("gello."):
            parts = mod.split(".")
            if len(parts) > 2:
                mods.add(parts[1])
    imports[p] = mods

# ------------------------------------------------------------------ 1
edges = defaultdict(set)
for p, mods in imports.items():
    src = pkg_of(p)
    for m in mods:
        if m and m != src:
            edges[src].add(m)

cycles = []
seen, stack = set(), []


def visit(node: str) -> None:
    if node in stack:
        cycles.append(" -> ".join(stack[stack.index(node):] + [node]))
        return
    if node in seen:
        return
    stack.append(node)
    for m in sorted(edges.get(node, ())):
        visit(m)
    stack.pop()
    seen.add(node)


for a in sorted(edges):
    seen.clear()
    visit(a)
assert not cycles, ("gello/ 안에 순환 임포트가 있다:\n  "
                    + "\n  ".join(sorted(set(cycles)))
                    + "\n공용으로 쓰이는 것이 기능 폴더 안에 있으면 이렇게 된다 -- "
                      "그 이름을 gello/config/ 같은 잎사귀로 내리는 것이 답이다.")
print(f"1. gello/ 패키지 {len(edges)}개, 순환 없음 OK")

# ------------------------------------------------------------------ 2
bad = {}
for p, mods in imports.items():
    if pkg_of(p) not in UPPER:
        continue
    hit = sorted(m for m in mods & HARDWARE
                 if (str(p.relative_to(WT)), m) not in ALLOWED)
    if hit:
        bad[str(p.relative_to(WT))] = hit
assert not bad, (
    "상위 계층이 구체 하드웨어를 직접 임포트한다:\n  "
    + "\n  ".join(f"{k}: {v}" for k, v in sorted(bad.items()))
    + "\ndata/scene/gui 는 core 의 추상만 알아야 한다. 하드웨어를 직접 부르는 "
      "파일이라면 그 파일이 상위 계층에 있을 자리가 아니다.")
print(f"2. data/scene/gui 가 {sorted(HARDWARE)} 를 임포트하지 않음 OK"
      + (f" (인정 {len(ALLOWED)}건)" if ALLOWED else ""))

# ------------------------------------------------------------------ 3
cfg = [p for p in files if pkg_of(p) == "config"]
leaky = {str(p.relative_to(WT)): sorted(imports[p]) for p in cfg if imports[p]}
assert not leaky, (
    f"gello/config 는 아무것도 임포트하지 않는 잎사귀여야 한다: {leaky}")
print(f"3. gello/config 무의존 (파일 {len(cfg)}개) OK")

print("\ngello/ 계층 규칙 통과")
