"""시그널이 실제로 슬롯에 붙는지 (2026-09-03).

@pyqtSlot 은 QObject 를 상속한 클래스에서만 뜻이 있다. 평범한 파이썬 클래스의
메서드에 붙이면 PyQt 가 그것을 진짜 Qt 슬롯으로 취급하려다 붙을 QObject 가
없어 connect() 가 TypeError 로 죽는다 -- 그런데 **부르기 전까지는 아무 일도
일어나지 않는다.**

실제로 그랬다. Phase 4 에서 메서드를 WorkspaceWindow(QObject)에서 평범한
XOps 클래스로 옮길 때 데코레이터가 따라왔고, 일곱 자리가 그대로 깨진 채
22개 테스트를 전부 통과했다. 조작자가 에피소드를 재생하려 한 순간에야
드러났다:

    TypeError: connect() failed between
    EpisodeLoadWorker.loaded[str, str, object, object] and on_episode_loaded()

구조 검사(test_app_structure)는 모양을 보지 배선을 보지 않는다. 그래서
여기서는 **진짜로 connect 를 걸어 본다.**
"""
import ast
import os
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ.setdefault("GELLO_NO_PRIVILEGED", "1")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

# ------------------------------------------------------------------ 1
# QObject 가 아닌 클래스에 @pyqtSlot 이 붙어 있으면 안 된다.
bad = {}
for p in sorted((WT / "apps").rglob("*.py")):
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if not isinstance(n, ast.ClassDef):
            continue
        bases = {getattr(b, "id", getattr(b, "attr", "")) for b in n.bases}
        if bases & {"QObject", "QThread", "QWidget", "QDialog", "QMainWindow",
                    "QLabel", "QAbstractItemView"}:
            continue                      # 진짜 QObject -- 데코레이터가 뜻이 있다
        hit = [m.name for m in n.body
               if isinstance(m, ast.FunctionDef)
               and any(getattr(getattr(d, "func", d), "id", "") == "pyqtSlot"
                       for d in m.decorator_list)]
        if hit:
            bad[f"{p.relative_to(WT)}:{n.name}"] = hit
assert not bad, (
    "QObject 가 아닌 클래스에 @pyqtSlot 이 붙어 있다 -- 그 메서드에 시그널을\n"
    "연결하는 순간 TypeError 로 죽는다 (데코레이터를 빼면 된다):\n  "
    + "\n  ".join(f"{k}: {v}" for k, v in sorted(bad.items())))
print("1. QObject 아닌 클래스에 @pyqtSlot 없음 OK")

# ------------------------------------------------------------------ 2
# 모양만 보지 말고 실제로 붙여 본다. 도메인의 on_* 메서드 전부에 대해,
# 인자 개수가 같은 시그널을 만들어 connect 를 시도한다.
class _Probe(QObject):
    s0 = pyqtSignal()
    s1 = pyqtSignal(object)
    s2 = pyqtSignal(object, object)
    s3 = pyqtSignal(object, object, object)
    s4 = pyqtSignal(object, object, object, object)


FEATURES = WT / "apps" / "workspace" / "features"
import importlib  # noqa: E402

failures = []
checked = 0
for p in sorted(FEATURES.rglob("*.py")):
    if p.name == "__init__.py":
        continue
    mod_name = ".".join(p.relative_to(WT).with_suffix("").parts)
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:                # noqa: BLE001
        failures.append(f"{mod_name}: 임포트 실패 {type(e).__name__}: {e}")
        continue
    for cls in vars(mod).values():
        if not (isinstance(cls, type) and cls.__module__ == mod_name):
            continue
        if issubclass(cls, QObject):
            continue                      # 진짜 QObject 는 PyQt 가 알아서 한다
        obj = cls.__new__(cls)            # __init__ 은 창을 요구하므로 건너뛴다
        for name in dir(cls):
            if not name.startswith("on_"):
                continue
            fn = getattr(cls, name, None)
            if not callable(fn):
                continue
            try:
                nargs = fn.__code__.co_argcount - 1        # self 제외
            except AttributeError:
                continue
            if nargs > 4:
                continue
            probe = _Probe()
            sig = getattr(probe, f"s{nargs}")
            checked += 1
            try:
                sig.connect(getattr(obj, name))
            except TypeError as e:
                failures.append(f"{mod_name}.{cls.__name__}.{name}: {str(e)[:80]}")

assert not failures, ("시그널을 실제로 연결해 보니 실패한다:\n  "
                      + "\n  ".join(failures))
print(f"2. features 의 on_* 슬롯 {checked}개 실제 connect OK")

# QApplication 을 만들었으므로 정상 종료하면 Qt 객체가 잘못된 순서로 풀린다.
# 다른 인수 테스트와 같은 방식으로 끝내되, 버퍼를 먼저 비운다.
print("\n시그널·슬롯 배선 통과")
sys.stdout.flush()
os._exit(0)
