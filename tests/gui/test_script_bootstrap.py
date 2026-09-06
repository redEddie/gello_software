"""scripts/ 의 gello 임포트가 sys.path 부트스트랩 뒤에 오는지 (2026-09-06).

증상: GUI 의 [재압축] 버튼이 즉시 죽었다.

    [재압축] 시작: 5개 파일
    ModuleNotFoundError: No module named 'gello.data'

'gello' 가 아니라 'gello.data' 가 없다는 게 단서다 -- gello 는 찾았다는 뜻이다.
GUI 가 도는 venv(lerobot-venv)에는 gello 의 editable 설치가 남아 있고, 그
finder 는 gello 를 gello_software-deploy/gello 로 보낸다. deploy 워크트리는
아직 옛 평면 배치(gello/dataset_schema.py)라 gello/data 가 없다.

    lerobot-venv/.../__editable___gello_0_0_1_finder.py
    MAPPING = {'gello': '/home/franka/teleop-franka/gello_software-deploy/gello'}

그래서 스크립트는 저마다 맨 위에서 자기 워크트리 루트를 sys.path 앞에 꽂는다.
editable finder 는 sys.meta_path 에 append 되므로 PathFinder 보다 뒤이고,
sys.path[0] 이 이긴다. 부트스트랩이 없거나 임포트보다 아래 있으면 조용히 남의
워크트리를 임포트한다 -- 죽으면 다행이고, 이름이 우연히 맞으면 옛 코드가 돈다.

repack_hdf5.py 는 부트스트랩이 209줄 아래, main() 안에 있었다 (--skip-repacked
갈래에서만 실행). 같은 실수가 5개 더 있었다.

로봇도 화면도 필요 없다: 소스만 읽는다.
"""
import ast
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

SCRIPTS = WT / "scripts"
files = sorted(SCRIPTS.rglob("*.py"))
assert len(files) > 15, f"{SCRIPTS} 에서 스크립트를 거의 못 찾았다 -- 경로가 바뀌었나?"


def _is_bootstrap(node: ast.AST) -> bool:
    """sys.path.insert(0, <루트>) / sys.path.append(...) 한 줄인가."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    f = node.value.func
    return (isinstance(f, ast.Attribute)
            and f.attr in ("insert", "append")
            and isinstance(f.value, ast.Attribute) and f.value.attr == "path"
            and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys")


bad = {}
for p in files:
    tree = ast.parse(p.read_text(encoding="utf-8"))

    # 부트스트랩은 모듈 최상위여야 한다. 함수 안에 있으면 그 함수를 부르는
    # 갈래에서만 도는데, 임포트는 이미 모듈 로드 때 끝난 뒤다.
    boot = min((n.lineno for n in tree.body if _is_bootstrap(n)), default=None)

    imp = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "gello":
            imp = n.lineno if imp is None else min(imp, n.lineno)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] == "gello":
                    imp = n.lineno if imp is None else min(imp, n.lineno)
    if imp is None:
        continue
    if boot is None:
        bad[str(p.relative_to(WT))] = f"{imp}줄에서 gello 를 임포트하는데 부트스트랩이 없다"
    elif boot > imp:
        bad[str(p.relative_to(WT))] = f"부트스트랩이 {boot}줄, gello 임포트가 {imp}줄 -- 순서가 거꾸로다"

assert not bad, (
    "scripts/ 의 gello 임포트가 sys.path 부트스트랩보다 앞선다. GUI 가 이 "
    "스크립트를 QProcess 로 띄우면 남의 워크트리를 임포트한다:\n  "
    + "\n  ".join(f"{k}: {v}" for k, v in sorted(bad.items())))
print(f"1. scripts/ {len(files)}개 부트스트랩 순서 OK")

# GUI 가 실제로 띄우는 스크립트는 계약이 더 강하다: 중립 cwd 에서 --help 가
# 떠야 한다. 위 정적 검사가 놓치는 것(부트스트랩이 잘못된 깊이를 가리키는
# 경우 등)을 여기서 잡는다.
import subprocess

from apps.workspace.constants import CONVERT_SCRIPT, REPACK_SCRIPT, UPLOAD_SCRIPT

for s in (REPACK_SCRIPT, CONVERT_SCRIPT, UPLOAD_SCRIPT):
    r = subprocess.run([sys.executable, s, "--help"], cwd="/", capture_output=True, text=True)
    err = r.stderr.strip()
    assert "ModuleNotFoundError" not in err and "ImportError" not in err, (
        f"{Path(s).name} 을 중립 cwd 에서 띄우면 임포트가 깨진다:\n{err[-800:]}")
print("2. GUI 가 띄우는 스크립트 3개 중립 cwd 에서 임포트 OK")

print("\nscripts/ 부트스트랩 계약 통과")
