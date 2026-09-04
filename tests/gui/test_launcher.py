"""런처 마법사 검증 (offscreen) — 모드 분기, 새 데이터셋 생성, 이어서 하기.

Cancel 버튼이 없고 첫 페이지는 모드 버튼 2개만 보이는 것, Finish 가
폴더/dataset-identity.json/instructions.json 복사를 만드는 것, legacy 폴더에
identity 를 자동 생성하는 것, apply_result 가 env+recents 에 반영하는 것까지.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication, QWizard  # noqa: E402

app = QApplication(sys.argv)

from apps.collect_launcher import apply_result  # noqa: E402
from apps.workspace.launcher import LauncherWizard  # noqa: E402
from apps.workspace.launcher.pages import (  # noqa: E402
    PAGE_CONTINUE,
    PAGE_HW,
    PAGE_MODE,
    PAGE_NEW,
)
from gello.scene.dataset_meta import load_identity  # noqa: E402
from gello.gui.widgets import Recents  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="launcher_"))


def _cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


# 원본 데이터셋 (복사 원본): identity + instructions.json + scene 파일 1개
SRC = TMP / "src-dataset"
SRC.mkdir()
(SRC / "dataset-identity.json").write_text(json.dumps({
    "format": "knu-dataset-identity/1", "name": "src-dataset",
    "hf_repo": "knu-physical-ai/src-dataset", "concept": "원본 컨셉",
    "created": "2026-01-01", "schema_version": "knu-1.0.0"}), encoding="utf-8")
(SRC / "instructions.json").write_text(json.dumps(
    {"plan_version": 1, "scenes": []}), encoding="utf-8")
(SRC / "scene_000.hdf5").write_bytes(b"x")
# legacy 데이터셋: scene 파일만, 메타 없음
LEG = TMP / "legacy_ds"
LEG.mkdir()
(LEG / "scene_000.hdf5").write_bytes(b"x")

# ---- 1. 첫 페이지: 모드 버튼 2개 + Cancel 없음 + Next 숨김 ----
wiz = LauncherWizard()
wiz.show()
assert wiz.currentId() == PAGE_MODE
assert not wiz.button(QWizard.WizardButton.NextButton).isVisible()
assert not wiz.button(QWizard.WizardButton.BackButton).isVisible()
assert not wiz.button(QWizard.WizardButton.CancelButton).isVisible()
mode_page = wiz.page(PAGE_MODE)
assert mode_page.continue_btn.isVisible() and mode_page.new_btn.isVisible()
print("1 통과: 첫 페이지는 모드 버튼 2개만 (Back/Next/Cancel 없음)")

# ---- 2. 분기: 새 데이터세트 -> PAGE_NEW, 이어서 하기 -> PAGE_CONTINUE ----
mode_page.new_btn.click()
assert wiz.currentId() == PAGE_NEW
wiz.back()
mode_page.continue_btn.click()
assert wiz.currentId() == PAGE_CONTINUE
print("2 통과: 모드 버튼이 곧 분기 이동")

# ---- 3. Continue: 목록 표시 + legacy 선택 시 identity 자동 생성 ----
cont = wiz.page(PAGE_CONTINUE)
cont.reload()      # 실제 홈 디렉터리를 스캔한다 -- 죽지 않으면 됨
# TMP 의 데이터셋을 목록에 주입한다 (기본 스캔 경로는 홈 디렉터리라 테스트
# 폴더가 안 잡힌다)
from gello.scene.dataset_meta import discover_datasets  # noqa: E402
from PyQt6.QtWidgets import QListWidgetItem  # noqa: E402
cont._entries = discover_datasets([TMP])
assert {e.path.name for e in cont._entries} >= {"src-dataset", "legacy_ds"}
cont.list.clear()
for e in cont._entries:
    cont.list.addItem(QListWidgetItem(e.name))
row = next(i for i, e in enumerate(cont._entries) if e.path == LEG)
cont.list.setCurrentRow(row)
assert cont.isComplete()
wiz.next()
assert wiz.currentId() == PAGE_HW
wiz.accept()                                   # Finish
res = wiz.result()
assert res is not None and res.dataset_root == LEG and res.mode == "continue"
ident = load_identity(LEG)                     # legacy -> identity 자동 생성
assert ident is not None and ident.name == "legacy_ds", ident
print("3 통과: Continue -- legacy 폴더에 identity 자동 생성 후 진입")

# ---- 4. New dataset: 검증 + 설정 복사 + Finish 생성 ----
wiz2 = LauncherWizard()
wiz2.show()
wiz2.page(PAGE_MODE).new_btn.click()
newp = wiz2.page(PAGE_NEW)
assert not newp.isComplete()                   # 이름 비어 있음
newp.name_edit.setText("한글 이름")
assert not newp.isComplete() and newp.error.text()
newp.name_edit.setText("new-ds")
newp.location_edit.setText(str(TMP))
assert newp.isComplete(), newp.error.text()
assert str(TMP / "new-ds") in newp.preview.text()
# 설정 가져오기: 컨셉 prefill
newp._entries = discover_datasets([TMP])
newp.copy_combo.blockSignals(True)
newp.copy_combo.clear()
newp.copy_combo.addItem("(비어 있게 시작)", None)
for e in newp._entries:
    newp.copy_combo.addItem(e.name, str(e.path))
newp.copy_combo.blockSignals(False)
idx = newp.copy_combo.findData(str(SRC))
newp.copy_combo.setCurrentIndex(idx)
assert newp.concept_edit.toPlainText() == "원본 컨셉"
wiz2.next()
assert wiz2.currentId() == PAGE_HW
wiz2.accept()
res2 = wiz2.result()
ds = TMP / "new-ds"
assert res2.mode == "new" and res2.dataset_root == ds
ident2 = load_identity(ds)
assert ident2.name == "new-ds" and ident2.concept == "원본 컨셉"
assert ident2.hf_repo == "knu-physical-ai/new-ds"
assert (ds / "instructions.json").is_file()    # 원본 계획 복사됨
print("4 통과: New dataset -- 검증/미리보기/복사/생성 + hf_repo 기본값")

# ---- 5. apply_result: station env + recents ----
os.environ.pop("GELLO_STATION", None)
rec_path = TMP / "recents.json"
rec = Recents(rec_path)
apply_result(res2, recents=rec)
assert os.environ["GELLO_STATION"] == res2.station
saved = json.loads(rec_path.read_text())
assert saved["data_root"][0] == str(ds)
print("5 통과: apply_result -- GELLO_STATION + data_root recents 반영")

print("\n런처 마법사 검증 통과")
_cleanup()
os._exit(0)
