"""업로드 장부(gello/hub_upload_state.py) 검증 -- 2026-08-25 도입.

배경: '재압축한 파일만' 체크박스는 attr 만 고친 파일(라벨 교정)을 빠뜨려
문법 교정분 5개가 Hub에 안 올라간 사고가 있었다. 장부는 업로드 성공 시점의
(size, mtime)을 repo 별로 기록하고, 다음에는 기록과 다른 파일을 고른다."""
import os
import sys
import tempfile
import time
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])  # 리포 루트
sys.path.insert(0, WT)

from gello.hub_upload_state import (  # noqa: E402
    changed_files,
    record_uploaded,
    upload_reason,
)

tmp = Path(tempfile.mkdtemp())
state = tmp / "state.json"
repo = "org/test-repo"
a = tmp / "scene_000.hdf5"
b = tmp / "scene_001.hdf5"
a.write_bytes(b"aaaa")
b.write_bytes(b"bbbb")

# ---- 1. 장부 없음 = 전부 신규 ----
sel = changed_files(repo, [a, b], state_path=state)
assert [x.name for x, _ in sel] == ["scene_000.hdf5", "scene_001.hdf5"]
assert all("신규" in r for _, r in sel), sel
print("1. 장부 없음 -> 전부 신규 OK")

# ---- 2. 기록 후 = 변경 없음 ----
record_uploaded(repo, a, state_path=state)
record_uploaded(repo, b, state_path=state)
assert upload_reason(repo, a, state_path=state) is None
assert changed_files(repo, [a, b], state_path=state) == []
print("2. 기록 후 -> 변경 없음 OK")

# ---- 3. attr 편집처럼 내용이 바뀌면 (mtime 변화) 다시 선택된다 ----
time.sleep(0.01)
a.write_bytes(b"aaaa")          # 크기 같아도 mtime 이 바뀐다 -- attr 편집 모사
sel = changed_files(repo, [a, b], state_path=state)
assert [x.name for x, _ in sel] == ["scene_000.hdf5"]
assert "변경" in sel[0][1], sel
print("3. attr 편집(mtime) -> 재선택 OK")

# ---- 4. 크기 변화도 잡는다 (mtime 을 되돌려도) ----
st = b.stat()
b.write_bytes(b"bbbbbb")
os.utime(b, (st.st_atime, st.st_mtime))  # mtime 조작 -- 크기로 잡혀야 한다
assert upload_reason(repo, b, state_path=state) is not None
print("4. 크기 변화 -> 재선택 OK")

# ---- 5. repo 별로 따로 -- 다른 repo 에는 기록이 없다 ----
assert upload_reason("org/other", a, state_path=state) is not None
assert "신규" in upload_reason("org/other", a, state_path=state)
print("5. repo 별 분리 OK")

# ---- 6. 깨진 장부 = 전부 신규 (업로드가 막히면 안 된다) ----
state.write_text("{broken json")
assert len(changed_files(repo, [a, b], state_path=state)) == 2
print("6. 깨진 장부 -> 전부 신규 (fail-open) OK")

# ---- 7. 심볼릭 링크는 resolve 되어 같은 파일로 본다 ----
state.unlink()
record_uploaded(repo, a, state_path=state)
link = tmp / "link.hdf5"
link.symlink_to(a)
assert upload_reason(repo, link, state_path=state) is None
print("7. symlink resolve OK")

# ---- 8~11. PipelineDialog 가 장부로 업로드 대상을 고른다 ----
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.argv = ["t"]
import gello.hub_upload_state as hus

tmp2 = Path(tempfile.mkdtemp())
hus.STATE_PATH = tmp2 / "state.json"   # 실제 장부 보호
a2 = tmp2 / "scene_000.hdf5"
b2 = tmp2 / "scene_001.hdf5"
a2.write_bytes(b"a" * 100)
b2.write_bytes(b"b" * 100)
repo2 = "org/x"
hus.record_uploaded(repo2, a2)
hus.record_uploaded(repo2, b2)

from PyQt6.QtWidgets import QApplication
import experiments.collect_workspace as cw

app = QApplication.instance() or QApplication([])
plan = {"action": "up_to_date", "rows": [], "ambiguous": [],
        "local_total": 0, "hub_total": 0, "paths": [str(a2), str(b2)]}
dlg = cw.PipelineDialog(None, str(tmp2), plan, "org/lr", repo2,
                        str(tmp2 / "lr"))
dlg.repack_check.setChecked(False)   # 가짜 파일은 전부 '미압축' 판정이라 끔
dlg.hdf5_check.setChecked(True)

up = [s_ for s_ in dlg.steps() if "HDF5" in s_["name"]]
assert len(up) == 1 and "program" not in up[0] and "생략" in up[0]["name"], up
print("8. 변경 없음 -> 생략(note) 단계 OK")

time.sleep(0.01)
a2.touch()                            # attr 편집 모사
up = [s_ for s_ in dlg.steps() if "HDF5" in s_["name"]]
assert "변경분 1개" in up[0]["name"], up
assert str(a2) in up[0]["args"] and str(b2) not in up[0]["args"]
assert "scene_000.hdf5" in up[0]["detail"], up[0]
print("9. 변경 1개 -> 그 파일만 + 사유(detail) OK")

dlg.hdf5_only_new_check.setChecked(False)
up = [s_ for s_ in dlg.steps() if "HDF5" in s_["name"]]
assert "전체 강제" in up[0]["name"] and str(b2) in up[0]["args"]
print("10. 체크 해제 -> 전체 강제 업로드 OK")

dlg.hdf5_only_new_check.setChecked(True)
dlg.repack_check.setChecked(True)     # repack_todo == 전체 (마커 없음)
hus.record_uploaded(repo2, a2)        # 장부상 변경 없어도
up = [s_ for s_ in dlg.steps() if "HDF5" in s_["name"]]
assert str(a2) in up[0]["args"] and "재압축" in up[0]["detail"]
print("11. 재압축 대상 합집합 OK")

print("\n업로드 장부 검증 통과")
