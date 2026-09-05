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
from gello.scene.dataset_meta import DatasetIdentity, load_identity  # noqa: E402
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

# ---- 6. 카메라 노드 인계 계약 (하드웨어 없이) ----
# 마법사가 미리보기용으로 띄운 노드를 창이 이어받는다. 넘기지 않고 창이
# 새로 띄우면 같은 카메라를 두 번 열려다 포트 6021 충돌로 죽는다
# (2026-09-05). 프로세스를 실제로 띄우지 않고 계약만 본다: take_node 가
# 소유권을 넘기는지, 넘긴 뒤 페이지가 손을 떼는지.
from PyQt6.QtCore import QProcess  # noqa: E402

from apps.workspace.launcher.pages import PAGE_HW  # noqa: E402
from apps.workspace.shared.camera_node_proc import (  # noqa: E402
    node_specs,
    spec_key,
)

# 노드에 넘어가는 것은 시리얼뿐이다 -- 역할이 붙으면 역할만 바꿔도 노드가
# 재시작한다 (2026-09-05 3층 분리).
assert node_specs(["A1", "B2"]) == ["A1", "B2"]
assert node_specs(["A1", ""]) == ["A1"]            # 빈 시리얼은 빠진다
assert node_specs(["A1", "A1"]) == ["A1"]          # 같은 장치를 두 번 열지 않는다
assert spec_key(node_specs(["A1", "B2"])) == "A1,B2"
# 키는 순서를 지운다: cam1 과 cam2 의 카메라를 맞바꾸면 여는 장치는 똑같으므로
# 노드를 재시작할 이유가 없다. 순서가 남아 있으면 "역할만 바꿨는데 재시작"이
# 그대로 살아난다 (Q2 가 없애려던 것).
assert spec_key(node_specs(["A1", "B2"])) == spec_key(node_specs(["B2", "A1"])), \
    "cam 을 맞바꾼 것만으로 노드가 재시작하면 안 된다"
assert spec_key(node_specs(["A1", "B2"])) != spec_key(node_specs(["A1", "C3"]))
assert spec_key(node_specs(["A1", "B2"])) != spec_key(node_specs(["A1"]))
assert all(":" not in x for x in node_specs(["A1", "B2"])), \
    "spec 에 역할이 섞이면 안 된다"

hw = wiz.page(PAGE_HW)
fake = QProcess()                                  # start() 하지 않는다
hw._node, hw._node_key = fake, "A1,B2"
proc, key = hw.take_node()
assert proc is fake and key == "A1,B2"
assert hw._node is None and hw._node_key == "", "넘긴 뒤에는 페이지가 손을 뗀다"
proc2, key2 = hw.take_node()
assert proc2 is None and key2 == "", "두 번 넘기지 않는다"
hw.cleanup()                                       # 넘긴 뒤 정리는 무해해야
print("6 통과: 카메라 노드 인계 -- spec 규칙 + 소유권 이전 + 중복 인계 없음")

# ---- 7. 스테이션 편집기: 기존은 읽기 전용, 새 것만 만들고 지운다 ----
# 기존 스테이션을 GUI 로 못 고치게 한 것은 의도된 제약이다 -- 셋업 값 변경을
# 코드로만 하게 해서 git 커밋 기록을 강제한다 (2026-09-05). 이 검사가 그
# 제약을 지킨다: 읽기 전용이 풀리면 실패한다.
from apps.workspace.launcher.station_editor import NEW_STATION  # noqa: E402
from gello.config.station import (  # noqa: E402
    CameraSpec,
    list_stations,
    load_station,
    station_path,
    validate_station_name,
)

TEST_ST = "zz-test-station"
station_path(TEST_ST).unlink(missing_ok=True)      # 앞선 실패의 잔재 정리
ed = wiz.page(PAGE_HW).station_editor
ed.reload(select=None)
assert ed.combo.count() >= 2 and ed.combo.itemData(ed.combo.count() - 1) == NEW_STATION

assert ed.name_edit.isReadOnly(), "등록된 스테이션은 읽기 전용이어야 한다"
assert not (ed.copy_btn.isEnabled() or ed.del_btn.isEnabled()
            or ed.save_btn.isEnabled()), "기존 선택에서는 세 버튼 모두 비활성"

ed.combo.setCurrentIndex(ed.combo.findData(NEW_STATION))
assert not ed.name_edit.isReadOnly() and ed.copy_btn.isEnabled()
assert not ed.save_btn.isEnabled(), "이름이 비면 저장 불가"
ed.name_edit.setText(list_stations()[0])
assert not ed.save_btn.isEnabled(), "이름이 겹치면 저장 불가"
assert validate_station_name(list_stations()[0]) is not None
ed.name_edit.setText("bad name/x")
assert not ed.save_btn.isEnabled(), "파일명에 못 쓰는 이름은 저장 불가"

ed.name_edit.setText(TEST_ST)
ed.ip_edit.setText("10.0.0.9")
ed.port_spin.setValue(6099)
assert ed.save_btn.isEnabled()
try:
    assert ed.save_new() == TEST_ST
    assert station_path(TEST_ST).is_file()
    cfg = load_station(TEST_ST)
    assert cfg.robot.ip == "10.0.0.9" and cfg.node.port == 6099
    # 스테이션은 cam id -> 역할만 안다. 어느 실물이 꽂혔는지(시리얼)는
    # 데이터셋이 정본이라 여기 저장하지 않는다 -- 두 곳에 적으면 갈라진다.
    assert cfg.cam_ids() == ["cam1", "cam2"], cfg.cam_ids()
    assert [cfg.cameras[c].role for c in cfg.cam_ids()] == ["agent", "wrist"]
    assert all(cfg.cameras[c].serial == "" for c in cfg.cam_ids()), \
        "스테이션 YAML 에 시리얼을 적으면 데이터셋과 갈라진다"
    # 저장 직후: 드롭다운이 그 이름으로 옮겨가도 이번 세션 것이라 삭제 가능
    assert ed.combo.currentData() == TEST_ST
    assert ed.del_btn.isEnabled(), "방금 만든 것은 지울 수 있어야 한다"
    assert ed.name_edit.isReadOnly(), "저장 뒤에는 다시 읽기 전용"
    ed._on_delete()
    assert not station_path(TEST_ST).exists()
finally:
    station_path(TEST_ST).unlink(missing_ok=True)

# identity 에 station 이 붙는다 (이어서 하기의 기본 선택 근거)
ident_st = DatasetIdentity(name="x", station="knu-eng7",
                           cameras={"cam1": "AAA", "cam2": "BBB"})
_rt = DatasetIdentity.from_dict(ident_st.to_dict())
assert _rt.station == "knu-eng7" and _rt.cameras == {"cam1": "AAA", "cam2": "BBB"}
_old = DatasetIdentity.from_dict({"name": "old"})
assert _old.station == "" and _old.cameras == {}, "옛 데이터셋은 빈 값"
print("7 통과: 스테이션 편집기 -- 기존 읽기전용 / 새 것 생성·삭제 / identity.station")

# ---- 8. 미리보기가 부모 밖으로 잘리지 않는다 ----
# 2026-09-05: 콤보와 미리보기를 한 줄에 짝지어 두었더니, 긴 항목 문자열
# ("Intel RealSense D405 (2304...)")이 콤보의 최소 폭을 262px 로 밀어올려
# 고정폭 미리보기가 x=605 에 놓였고 부모(614)를 넘어 **9px 세로줄**로만
# 보였다. 고르는 곳과 보는 곳을 나눠서 고쳤다. 위젯 크기만 보면 160x120 로
# 멀쩡해 보이므로, 부모 안에 들어오는지를 봐야 잡힌다.
hwp = wiz.page(PAGE_HW)
# 줄 목록은 스테이션이 정한다 (cam id -> 역할). 콤보·미리보기가 같은
# 목록을 따라야 한 쪽만 빠지는 일이 없다.
_cams = set(hwp.station_editor.cam_roles())
assert _cams, "스테이션이 카메라를 하나는 정해야 한다"
assert set(hwp.previews) == _cams, "cam 마다 미리보기가 있어야"
assert set(hwp.combos) == _cams, "cam 마다 시리얼 콤보가 있어야"
wiz.resize(760, 780)
app.processEvents()
app.processEvents()
for _role, _v in hwp.previews.items():
    par = _v.parentWidget()
    assert _v.x() + _v.width() <= par.width() + 1, (
        f"{_role} 미리보기가 부모 밖으로 나감: "
        f"x={_v.x()} w={_v.width()} 부모={par.width()}")
    assert _v.width() >= 120, f"{_role} 미리보기가 너무 좁다: {_v.width()}px"
for _role, _c in hwp.combos.items():
    assert _c.minimumSizeHint().width() <= 160, (
        f"{_role} 콤보가 항목 문자열만큼 폭을 요구한다 "
        f"({_c.minimumSizeHint().width()}px) -- shrinkable_combo 가 빠졌다")
# 역할 이름은 그림 **위 오버레이**다. 아래 별도 라벨로 두면 폭이 어긋나는
# 순간 어느 캡션이 어느 그림 것인지 애매해진다 (실제로 그림이 셀 안에서
# 왼쪽으로 치우쳐 그렇게 됐다). 겹쳐 있으면 어긋날 수가 없다.
for _role, _cell in hwp.preview_column.cells.items():
    cap, view = _cell.caption, _cell.view
    assert cap.parent() is _cell and view.parent() is _cell
    cr = cap.geometry()
    vr = view.geometry()
    assert vr.contains(cr.topLeft()), (
        f"{_role} 캡션이 그림 밖에 있다: 캡션={cr} 그림={vr}")
print("8 통과: 설정/미리보기 2단 분리 -- 잘림 없음, 콤보가 폭을 강요하지 않음, "
      "캡션은 그림 위 오버레이")

# ---- 9. 3층 분리: cam id / 역할 / 시리얼 ----
# 같은 카메라가 두 cam 에 붙으면 노드가 같은 장치를 두 번 열려다 실패하고,
# 어느 쪽이 이겼는지 화면에도 안 보인다. 방금 고른 쪽을 남기고 다른 쪽을
# (선택 안함) 으로 내린다.
ed.combo.setCurrentIndex(ed.combo.findData(NEW_STATION))
hwp._entries = [("S-AAA", "Model X (S-AAA)"), ("S-BBB", "Model X (S-BBB)")]
hwp._fill_camera_combos({})
c1, c2 = hwp.combos["cam1"], hwp.combos["cam2"]
c1.setCurrentIndex(c1.findData("S-AAA"))
c2.setCurrentIndex(c2.findData("S-BBB"))
assert hwp.serials() == {"cam1": "S-AAA", "cam2": "S-BBB"}
c2.setCurrentIndex(c2.findData("S-AAA"))       # cam2 가 cam1 것을 가져간다
hwp._dedup("cam2")
assert hwp.serials() == {"cam1": "", "cam2": "S-AAA"}, hwp.serials()
print("9 통과: 같은 카메라를 두 cam 에 못 붙인다 (앞선 쪽이 선택 안함으로)")

# ---- 10. 스테이션에서 +/- 로 카메라 개수 조절 ----
before = list(ed.cam_roles())
ed._on_add_cam()
assert list(ed.cam_roles()) == before + ["cam3"], ed.cam_roles()
assert set(hwp.combos) == set(ed.cam_roles()), "시리얼 줄이 따라와야"
assert set(hwp.previews) == set(ed.cam_roles()), "미리보기도 따라와야"
ed._on_del_cam()
assert list(ed.cam_roles()) == before
ed._set_cams([("cam1", "agent")])
ed._on_del_cam()                                # 마지막 한 대는 못 지운다
assert list(ed.cam_roles()) == ["cam1"], ed.cam_roles()
print("10 통과: +/- 로 카메라 개수 조절, 최소 한 대 보장, 줄이 함께 따라옴")

# ---- 11. 데이터세트 스키마 버전: 기본은 최신, 이력은 파일에서 파생 ----
# 새 필드를 쓰기 시작하면 버전이 따라 올라가는 것이 기본이어야 한다
# (2026-09-05). 한 데이터셋에 여러 버전이 섞이는 것은 허용하고, "언제부터
# 바뀌었나" 는 scene 파일에서 읽는다 -- 별도 이력을 적으면 두 번째 진실이 된다.
from gello.data.dataset_schema import (  # noqa: E402
    FT_OBS_KEYS,
    SCHEMA_FIELDS,
    SCHEMA_VERSION,
)
from gello.scene.dataset_meta import schema_version_spans  # noqa: E402

hwp.initializePage()
assert hwp.schema_version() == SCHEMA_VERSION, \
    f"기본값은 최신이어야 한다: {hwp.schema_version()} != {SCHEMA_VERSION}"
assert {hwp.schema_combo.itemData(i) for i in range(hwp.schema_combo.count())} \
    == set(SCHEMA_FIELDS), "정의된 버전이 모두 고를 수 있어야 한다"
# 옛 버전으로 내려 찍는 것도 가능해야 한다 (섞임 허용)
_i = hwp.schema_combo.findData("knu-1.0.0")
hwp.schema_combo.setCurrentIndex(_i)
assert hwp.schema_version() == "knu-1.0.0"
hwp.schema_combo.setCurrentIndex(hwp.schema_combo.findData(SCHEMA_VERSION))

# 이력은 파일에서 파생한다 -- 섞인 폴더를 만들어 구간이 갈리는지 본다
_MIX = TMP / "mixed"
_MIX.mkdir()
import h5py  # noqa: E402
for _sid, _v in (("S000", "knu-1.0.0"), ("S001", "knu-1.0.0"),
                 ("S002", "knu-1.1.0")):
    with h5py.File(_MIX / f"scene_{_sid[1:]}.hdf5", "w") as _f:
        _m = _f.create_group("metadata")
        _m.attrs["scene_id"] = _sid
        _m.attrs["schema_version"] = _v
assert schema_version_spans(_MIX) == [
    ("knu-1.0.0", "S000", "S001"), ("knu-1.1.0", "S002", "S002")], \
    schema_version_spans(_MIX)
print("11 통과: 스키마 버전 기본=최신 / 내려 찍기 가능 / 이력은 파일에서 파생")

# --- 12) [확인] 이 검사하는 필드 == 그 버전이 실제로 요구하는 필드 -------------
# 두 목록이 갈라지면 확인은 통과했는데 검증기는 떨어뜨리는 파일이 나온다.
from apps.workspace.launcher.pages import _ROBOT_OBS_FIELDS  # noqa: E402

for _v, _fields in _ROBOT_OBS_FIELDS.items():
    _req = SCHEMA_FIELDS[_v]["obs_datasets"]
    _extra = [f for f in _fields if f not in _req]
    assert not _extra, f"{_v}: 확인만 하고 스키마엔 없는 필드 {_extra}"
_base = set(SCHEMA_FIELDS["knu-1.0.0"]["obs_datasets"])
_added = [f for f in SCHEMA_FIELDS["knu-1.1.0"]["obs_datasets"] if f not in _base]
assert set(_ROBOT_OBS_FIELDS["knu-1.1.0"]) == set(_added), \
    f"1.1.0 이 더한 필드와 확인 대상이 다르다: {_added}"
assert set(_added) == set(FT_OBS_KEYS)
print("12 통과: 버전 [확인] 대상 == 그 버전이 더한 관측 필드")

# --- 13) 로봇 노드 인계 ------------------------------------------------------
# [확인] 이 노드를 띄웠는데 창이 이어받지 못하면, FCI 를 쥔 프로세스가 둘이 되어
# 창의 '노드 시작' 이 조용히 실패한다.
assert hwp.take_robot_node() is None      # 띄운 적 없으면 None
from apps.workspace.launcher.wizard import LaunchResult  # noqa: E402

_lr = LaunchResult(mode="new", dataset_root=TMP, station="", cameras={},
                   cam_roles={}, identity=DatasetIdentity(name="x", created="d"))
assert _lr.robot_node is None
# 창 쪽은 임포트가 무거워(numpy/cv2/h5py) 소스만 읽어 확인한다 -- 인자 이름이
# 갈리면 인계가 조용히 끊기므로 이름까지 본다.
import ast as _ast  # noqa: E402

_src = _ast.parse((Path(WT) / "apps" / "collect_workspace.py").read_text())
_fns = {n.name: n for n in _ast.walk(_src)
        if isinstance(n, (_ast.FunctionDef,))}
for _name in ("main", "__init__"):
    _args = {a.arg for a in _fns[_name].args.args
             + _fns[_name].args.kwonlyargs}
    assert "robot_node" in _args, f"{_name}() 에 robot_node 자리가 없다"
print("13 통과: 로봇 노드 인계 경로 (마법사 -> LaunchResult -> 워크스페이스)")

print("\n런처 마법사 검증 통과")
_cleanup()
os._exit(0)
