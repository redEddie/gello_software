"""스키마 닥터 (#47) 인수 테스트.

  1. 데이터세트 버전과 내용이 어긋난 파일을 찾는다
  2. 내리기는 무손실 -- 에피소드도, 파일에 있는 필드도 그대로다
  3. 내용이 못 따라가는 버전은 **적을 수 없다** (knu-1.1.0 사고를 만든 조작)
  4. 화면이 그 판단을 그대로 보여주고, 고칠 수 없으면 버튼이 꺼진다
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from gello.data.dataset_schema import SCHEMA_FIELDS  # noqa: E402
from gello.scene.schema_doctor import diagnose, restamp  # noqa: E402

OBJ = ["OBJ-CUP-WHT-02"]


def _write(path: Path, version: str, *, payload: bool, n: int = 2) -> None:
    """version 으로 찍되, payload attr 은 골라서 넣는다 -- 그것이 1.2.0 과
    1.1.1 을 가르는 것이고, 실물에서 어긋난 지점이다."""
    need = SCHEMA_FIELDS["knu-1.1.1"]
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["scene_id"] = "S000"
        meta.attrs["objects"] = json.dumps(OBJ)
        meta.attrs["layout"] = json.dumps(
            {"grid": [3, 3], "placements": {OBJ[0]: {"zone": [0, 0]}}})
        meta.attrs["dataset_version"] = version
        for a in need["metadata_attrs"]:
            if a not in meta.attrs:
                meta.attrs[a] = "x"
        if payload:
            meta.attrs["payload_mass"] = 0.7
            meta.attrs["payload_com"] = json.dumps([0.0, 0.0, 0.05])
        for i in range(n):
            g = f.create_group(f"episode_{i:03d}")
            for ds in need["episode_datasets"]:
                g.create_dataset(ds, data=np.zeros((2, 7), np.float32))
            obs = g.create_group("obs")
            for ds in need["obs_datasets"]:
                obs.create_dataset(ds, data=np.zeros((2, 3), np.float32))
            for a in need["episode_attrs"]:
                g.attrs[a] = 1 if a in ("episode_id", "num_samples") else "x"


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 실물의 S017 과 같은 모양: 1.2.0 으로 찍혔는데 payload 가 없다.
        _write(root / "scene_000.hdf5", "knu-1.2.0", payload=False)
        _write(root / "scene_001.hdf5", "knu-1.2.0", payload=True)

        bad = diagnose(root / "scene_000.hdf5")
        assert not bad.ok and bad.stamped == "knu-1.2.0", bad
        assert bad.satisfied == "knu-1.1.1", bad.satisfied
        assert set(bad.missing) == {"metadata/payload_mass",
                                    "metadata/payload_com"}, bad.missing
        assert all(v == -1 for v in bad.missing.values()), bad.missing
        good = diagnose(root / "scene_001.hdf5")
        assert good.ok and good.satisfied == "knu-1.2.0", good
        print("1. 어긋남 진단 OK")

        # 2. 내리기는 무손실
        with h5py.File(root / "scene_000.hdf5", "r") as f:
            before = (sorted(f), sorted(f["episode_000"]["obs"]))
        restamp(root / "scene_000.hdf5", "knu-1.1.1")
        after_d = diagnose(root / "scene_000.hdf5")
        assert after_d.ok and after_d.stamped == "knu-1.1.1", after_d
        with h5py.File(root / "scene_000.hdf5", "r") as f:
            after = (sorted(f), sorted(f["episode_000"]["obs"]))
        assert before == after, "에피소드나 필드가 바뀌었다"
        print("2. 내리기 무손실 OK")

        # 값이 섞여 있으면 기본값을 주지 않는다 (어느 것이 맞는지 모른다)
        from gello.scene.schema_doctor import known_payload as _kp

        assert _kp(root) == (0.7, [0.0, 0.0, 0.05]), _kp(root)

        # 3. 내용이 못 따라가는 버전은 못 찍는다
        try:
            restamp(root / "scene_000.hdf5", "knu-1.2.0")
        except ValueError as e:
            assert "payload" in str(e), str(e)
        else:
            raise AssertionError("내용이 못 따라가는 버전이 적혔다")
        assert diagnose(root / "scene_000.hdf5").stamped == "knu-1.1.1"
        print("3. 거짓 버전 거부 OK")

        # 4. 화면
        import apps.collect_workspace as cw

        app = QApplication.instance() or QApplication([])
        win = cw.WorkspaceWindow(None)
        win.root_edit.setText(str(root))
        win._set_activity("doctor")
        # 다시 어긋난 상태로 되돌려 화면을 본다
        restamp_forced = root / "scene_000.hdf5"
        with h5py.File(restamp_forced, "r+") as f:
            f["metadata"].attrs["dataset_version"] = "knu-1.2.0"
        win.doctor.refresh_schema()
        t = win.schema_tree
        assert t.topLevelItemCount() == 2, t.topLevelItemCount()
        row = next(t.topLevelItem(i) for i in range(t.topLevelItemCount())
                   if t.topLevelItem(i).text(5) != "—")
        assert row.text(2) == "knu-1.2.0" and row.text(3) == "knu-1.1.1", row
        win.doctor.on_schema_picked(row)
        assert "payload" in win.schema_missing.text(), win.schema_missing.text()
        assert win.schema_buttons["align_version"].isEnabled()
        # 방향에 따라 라벨과 안내가 갈린다 -- 올리기와 내리기는 뜻이 반대다
        assert "내리기" in win.schema_buttons["align_version"].text(), \
            win.schema_buttons["align_version"].text()
        assert "내려" in win.schema_plan.text(), win.schema_plan.text()
        # 맞는 줄이어도 **더 높이 갈 수 있으면** 버튼이 켜진다 (리셋 자세를
        # 채우면 knu-1.2.1). 어긋난 것을 고치는 것과 더 올리는 것은 다른
        # 일이고, 둘 다 이 버튼이 한다.
        ok_row = next(t.topLevelItem(i) for i in range(t.topLevelItemCount())
                      if t.topLevelItem(i).text(5) == "—")
        win.doctor.on_schema_picked(ok_row)
        assert win.schema_buttons["align_version"].isEnabled()
        assert "올리기" in win.schema_buttons["align_version"].text()
        assert "knu-1.2.1" in win.schema_plan.text(), win.schema_plan.text()
        assert not win.schema_buttons["fill_payload"].isEnabled(), \
            "맞는 줄인데 채우기가 켜져 있다"
        win.doctor.on_schema_picked(row)
        # 빠진 것이 부하 모델뿐이므로 채우기가 켜진다
        assert win.schema_buttons["fill_payload"].isEnabled()
        print("4. 화면 OK")

        # 5. 사후 채우기 -- 채우고 그 자리에서 다시 찍는다
        from gello.scene.schema_doctor import fill_payload, known_payload

        known = known_payload(root)
        assert known == (0.7, [0.0, 0.0, 0.05]), known
        got = fill_payload(root / "scene_000.hdf5", *known)
        assert got == "knu-1.2.0", got
        after = diagnose(root / "scene_000.hdf5")
        assert after.ok and after.stamped == "knu-1.2.0", after
        # 채우기와 재스탬프는 한 연산이다 -- 따로면 "채웠는데 도장은 그대로"
        # 인 파일이 남고, 그건 고치기 전과 똑같이 검증에 걸린다.
        assert not after.missing, after.missing
        print("5. 사후 채우기 OK")

        # 5b. **올리기도 된다.** 내용이 이미 높은 버전을 만족하는데 버전만
        # 낮게 적혀 있으면, 올리는 것이 잃는 것 없는 옳은 방향이다
        # (2026-09-07 사용자: "1.1.1 에서 1.2.0 은 쉽게 되니까요").
        with h5py.File(root / "scene_000.hdf5", "r+") as f:
            f["metadata"].attrs["dataset_version"] = "knu-1.1.1"
        up = diagnose(root / "scene_000.hdf5")
        assert up.stamped == "knu-1.1.1" and up.satisfied == "knu-1.2.0", up
        assert up.can_restamp and not up.missing, up
        win.doctor.refresh_schema()
        t2 = win.schema_tree
        r2 = next(t2.topLevelItem(i) for i in range(t2.topLevelItemCount())
                  if t2.topLevelItem(i).text(0) == "S000")
        win.doctor.on_schema_picked(r2)
        assert "올리기" in win.schema_buttons["align_version"].text(), \
            win.schema_buttons["align_version"].text()
        assert "잃는 것은 없습니다" in win.schema_plan.text(), \
            win.schema_plan.text()
        restamp(root / "scene_000.hdf5", up.satisfied)
        assert diagnose(root / "scene_000.hdf5").stamped == "knu-1.2.0"
        print("5b. 올리기 OK")
        win.close()
    # 6. 새 파일은 **채울 수 있는 버전까지만** 찍는다 (뿌리 쪽 수정)
    #    로봇이 부하를 못 알려준 세션에서 S017~S019 가 payload 없이 1.2.0 으로
    #    찍혀 57 에피소드가 검증 불가가 됐다 -- _resume_version 은 이 검사를
    #    하고 있었는데 새 파일 경로에는 없었다 (2026-09-07).
    from gello.scene.props import active_prop_ids
    from gello.scene.scene_format import SceneMetadata, SceneWriter

    with tempfile.TemporaryDirectory() as d2:
        root2 = Path(d2)
        md = SceneMetadata(
            scene_id="S000", objects=OBJ,
            layout={"grid": [3, 3], "placements": {OBJ[0]: {"zone": [0, 0]}}},
            dataset_version="knu-1.2.0")          # payload 없이 1.2.0 요구
        SceneWriter(root2, metadata=md, known_prop_ids=active_prop_ids(),
                    session_version="knu-1.2.0").close()
        d6 = diagnose(root2 / "scene_000.hdf5")
        assert d6.stamped == "knu-1.1.1", d6.stamped
        assert d6.ok, d6.missing

        # payload 를 주면 1.2.0 으로 찍힌다
        md2 = SceneMetadata(
            scene_id="S001", objects=OBJ,
            layout={"grid": [3, 3], "placements": {OBJ[0]: {"zone": [0, 0]}}},
            dataset_version="knu-1.2.0",
            payload_mass=0.85, payload_com=[-0.01, 0.0, 0.03])
        SceneWriter(root2, metadata=md2, known_prop_ids=active_prop_ids(),
                    session_version="knu-1.2.0").close()
        d7 = diagnose(root2 / "scene_001.hdf5")
        assert d7.stamped == "knu-1.2.0" and d7.ok, d7
    print("6. 새 파일은 채울 수 있는 버전까지만 OK")

    # 7. 초기 자세 -- 적힌 것과 **실제로 찍힌 첫 프레임**을 맞댄다
    #    station 설정과 metadata 를 맞대는 것은 장부끼리라 설정이 바뀌면
    #    오탐이 난다. 파일이 "여기서 출발했다" 고 적고 데이터가 다른 데서
    #    시작하는 것만이 어긋남이다 (2026-09-07 사용자).
    from gello.scene.schema_doctor import (
        RESET_TOLERANCE_DEG,
        reset_drift,
    )

    QPOS = [0.0, -0.161037389, 0.0, -2.44459747, 0.0, 2.2267522, 0.785398]
    with tempfile.TemporaryDirectory() as d3:
        root3 = Path(d3)
        path3 = root3 / "scene_000.hdf5"
        _write(path3, "knu-1.2.0", payload=True, n=3)
        with h5py.File(path3, "r+") as f:
            f["metadata"].attrs["reset_pose"] = "libero"
            f["metadata"].attrs["reset_qpos"] = json.dumps(QPOS)
            for i, off in enumerate((0.0, 0.01, 0.5)):
                js = np.tile(np.asarray(QPOS, dtype=np.float32), (2, 1))
                js[0, 3] += off
                g = f[f"episode_{i:03d}"]["obs"]
                del g["joint_states"]
                g.create_dataset("joint_states", data=js)
        dr = reset_drift(path3)
        assert dr["checked"] == 3, dr
        # ±5도(0.0873 rad) 문턱: 0.01 은 통과, 0.5 만 걸린다 -- 초기 자세를
        # 조금씩 흔들어 찍는 운용을 감안한 값이다.
        assert [n for n, _ in dr["over"]] == ["episode_002"], dr["over"]
        assert abs(dr["worst"] - 0.5) < 1e-6, dr["worst"]
        assert RESET_TOLERANCE_DEG == 5.0

        # 기준이 없으면 None -- 지금 station 값을 갖다 쓰는 것은 부르는 쪽이
        # 정하고, 화면이 출처를 밝힌다
        _write(root3 / "scene_001.hdf5", "knu-1.0.0", payload=False, n=1)
        assert reset_drift(root3 / "scene_001.hdf5") is None
    print("7. 초기 자세 대조 OK")

    # 8. 에피소드를 비운 파일은 **가장 높은 버전으로** 자유롭게 올린다
    #    잘못 기술할 데이터가 없기 때문이다 (2026-09-07 사용자: S006 은
    #    다시 찍으려고 에피소드를 다 비운 scene 이다).
    from gello.scene.schema_doctor import (
        fill_and_raise,
        reachable_version,
    )

    RESET = ("libero", QPOS)
    PAY = (0.85, [-0.01, 0.0, 0.03])
    with tempfile.TemporaryDirectory() as d4:
        root4 = Path(d4)
        empty = root4 / "scene_000.hdf5"
        _write(empty, "knu-1.1.1", payload=False, n=0)
        d8 = diagnose(empty)
        assert d8.episodes == 0 and d8.ok, d8
        # 지금 만족하는 것과 채우면 닿는 것이 다르다 -- 그 차이가 이 버튼이다
        assert d8.satisfied == "knu-1.1.1", d8.satisfied
        assert reachable_version(empty, payload=PAY, reset=RESET) == \
            "knu-1.2.1", reachable_version(empty, payload=PAY, reset=RESET)
        got = fill_and_raise(empty, payload=PAY, reset=RESET)
        assert got == "knu-1.2.1", got
        after8 = diagnose(empty)
        assert after8.ok and after8.stamped == "knu-1.2.1", after8

        # 에피소드가 있고 관측이 옛 버전이면 채워도 못 넘는다 -- 벽은 그대로
        old = root4 / "scene_001.hdf5"
        _write(old, "knu-1.0.0", payload=False, n=1)
        with h5py.File(old, "r+") as f:
            for k in list(f["episode_000"]["obs"]):
                if "torque" in k or "wrench" in k or "contact" in k:
                    del f["episode_000"]["obs"][k]
        assert reachable_version(old, payload=PAY, reset=RESET) == \
            "knu-1.0.0", reachable_version(old, payload=PAY, reset=RESET)
    print("8. 비운 파일은 가장 높은 버전으로 OK")
    print("test_doctor_schema OK")


if __name__ == "__main__":
    main()
