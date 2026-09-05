"""새 스테이션 저장 -- 카메라 대수와 저장된 YAML 모양 검증.

2026-09-05 사고: 마법사에서 새 스테이션을 만들려 하면 GUI 가 조용히 사라졌다.
저장 핸들러가 3층 분리(하드웨어=시리얼 / 데이터세트=역할 / 인터페이스=cam id)
이전의 잔재라 카메라를 정확히 2대로 못 박고 있었다::

    agent, wrist = self.cameras()          # 3대면 ValueError
    cams = {"agent": CameraSpec(serial=agent), ...}

2대여도 저장 결과가 틀렸다: 키가 cam id 가 아니라 역할, role 은 빈 문자열,
게다가 시리얼이 스테이션에 적혔다 (데이터셋이 정본인데).

여기서는 configs/stations 를 임시 폴더로 돌려 실제 파일까지 쓴 뒤 읽는다 --
"안 터진다"만 보면 잘못된 모양으로 저장되는 것을 못 잡는다.
"""
import shutil
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)
sys.argv = ["t"]

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import gello.config.station as st  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="stations_"))
st.STATIONS_DIR = TMP          # 저장 대상만 임시로 옮긴다
shutil.copy2(Path(WT) / "configs" / "stations" / "knu-eng7.yaml", TMP)

from apps.workspace.launcher import LauncherWizard  # noqa: E402
from apps.workspace.launcher.pages import PAGE_HW, PAGE_MODE  # noqa: E402
from apps.workspace.launcher.station_editor import NEW_STATION  # noqa: E402


def _hardware_page():
    w = LauncherWizard()
    w.show()
    for _ in range(3):
        app.processEvents()
    w.page(PAGE_MODE).continue_btn.click()      # 이어서 하기
    for _ in range(5):
        app.processEvents()
    w.next()
    for _ in range(8):
        app.processEvents()
    assert w.currentId() == PAGE_HW, w.currentId()
    return w, w.page(PAGE_HW)


def _new_station(hw, name, n_cams):
    se = hw.station_editor
    se.combo.setCurrentIndex(se.combo.findData(NEW_STATION))
    for _ in range(5):
        app.processEvents()
    se.name_edit.setText(name)
    while len(se.cam_roles()) < n_cams:
        se.cam_add_btn.click()
    while len(se.cam_roles()) > n_cams:
        se.cam_del_btn.click()
    for _ in range(5):
        app.processEvents()
    cams = list(se.cam_roles())
    for i, cam in enumerate(cams):             # 역할을 사람이 적은 것처럼
        se.role_edits[cam].setText(f"role{i}")
    hw._on_save_station()
    for _ in range(5):
        app.processEvents()
    return se, cams


# --- 1) 카메라 2/3/4대 어디서도 저장이 터지지 않는다 ----------------------
# 고치기 전에는 3대에서 ValueError: too many values to unpack (expected 2) 가
# 슬롯을 빠져나가 PyQt 가 프로세스를 죽였다.
import yaml  # noqa: E402

for n in (2, 3, 4):
    wiz, hw = _hardware_page()
    se, cams = _new_station(hw, f"t-{n}cam", n)
    path = TMP / f"t-{n}cam.yaml"
    assert path.is_file(), f"{n}대: 저장이 안 됐다 -- {se.msg.text()}"

    data = yaml.safe_load(path.read_text())
    got = data["cameras"]
    # --- 2) 키는 cam id, 역할은 그 안, 시리얼은 없다 ---------------------
    assert list(got) == cams, f"{n}대: 키가 cam id 가 아니다 -- {list(got)}"
    for i, cam in enumerate(cams):
        assert got[cam]["role"] == f"role{i}", f"{n}대 {cam}: role 이 안 적혔다 -- {got[cam]}"
        assert "serial" not in got[cam], \
            f"{n}대 {cam}: 시리얼이 스테이션에 적혔다 (데이터셋이 정본) -- {got[cam]}"
    wiz.page(PAGE_HW).cleanup()
    print(f"1-{n} 통과: 카메라 {n}대 저장 OK, 키={list(got)} 역할={[got[c]['role'] for c in cams]}")

# --- 3) 저장한 것을 다시 읽으면 같은 표가 나온다 --------------------------
# 키와 역할이 뒤바뀌어 있으면 여기서 cam id 가 역할 이름으로 돌아온다.
cfg = st.load_station("t-3cam")
assert cfg.cam_ids() == ["cam1", "cam2", "cam3"], cfg.cam_ids()
assert [cfg.cameras[c].role for c in cfg.cam_ids()] == ["role0", "role1", "role2"], \
    [cfg.cameras[c].role for c in cfg.cam_ids()]
print("3 통과: 저장 -> 로드 왕복에서 cam id 와 역할이 유지된다")

# --- 4) 마법사 단계의 예외는 로그로 남는다 --------------------------------
# 훅이 없으면 PyQt 가 abort() 해서 창이 아무 말 없이 사라지고, 아이콘 실행에는
# stderr 가 없어 아무 기록도 안 남는다 -- 이번 크래시가 딱 그랬다.
# 오류 창은 일부러 안 띄운다 (2026-09-05 사용자 결정): 조용히 닫히되 로그는
# 남는다.
from apps.collect_launcher import install_excepthook  # noqa: E402

_log = TMP / "sub" / "launcher_test.log"      # 없는 폴더도 만들어야 한다
_prev = sys.excepthook
install_excepthook(_log)
try:
    raise ValueError("too many values to unpack (expected 2)")
except ValueError:
    sys.excepthook(*sys.exc_info())
finally:
    sys.excepthook = _prev
assert _log.is_file(), "예외 로그 파일이 안 생겼다"
_text = _log.read_text()
assert "too many values to unpack" in _text, _text
assert "Traceback" in _text, _text
print("4 통과: 마법사 예외가 로그 파일에 남는다 (오류 창은 안 띄운다)")

shutil.rmtree(TMP, ignore_errors=True)
print("\n스테이션 저장 검증 통과")
