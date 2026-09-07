"""이어찍기에서 버전 도장이 이번 세션 것으로 올라가는지 검증.

2026-09-06 사고: scene_015 의 에피소드를 모두 지우고 새 필드 구성으로 40개를
다시 찍었는데, 도장은 파일이 처음 만들어질 때의 knu-1.1.0 그대로였다. 그
버전이 요구하는 (이제는 기록하지 않는) 필드가 없다며 검증에 걸렸다.

이어 찍으면 **이번 세션이 쓰는 필드**가 그 파일에 들어간다. 도장이 그것을
따라가지 않으면 파일이 자기 내용과 다른 약속을 하게 된다 -- 버저닝이 막으려던
바로 그 상황이다.

규칙은 올리기만 한다 (사용자 결정). 내려 찍으면 이웃 에피소드가 가진 열을 잃은
파일이 되므로, 낮은 버전으로 찍고 싶으면 새 데이터셋으로 시작한다.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)

from gello.data.dataset_schema import (  # noqa: E402
    FT_OBS_FIELDS,
    schema_required_fields,
    schema_version_key,
)
from gello.scene.scene_format import SceneMetadata, SceneWriter  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="resume_ver_"))
LAYOUT = {"grid": [3, 3], "placements": {"OBJ-CUP-BLU-01": {"zone": [0, 0]}}}


def _meta(version, payload: bool = True):
    """payload 는 기본으로 넣는다 -- knu-1.2.0 이 그것을 요구하고, 없이 그
    버전을 찍는 것은 2026-09-07 부터 막혀 있다 (파일이 갖지 않은 필드를
    가졌다고 주장하는 상태였고, 실물에서 57 에피소드가 그렇게 됐다).
    없는 경우를 보고 싶으면 payload=False 로 부른다."""
    return SceneMetadata(
        scene_id="S000", objects=["OBJ-CUP-BLU-01"],
        layout=LAYOUT, station="t", dataset_version=version,
        payload_mass=0.85 if payload else None,
        payload_com=[-0.01, 0.0, 0.03] if payload else None)


def _episode(w, rng):
    for _ in range(3):
        w.add_frame(
            agentview_rgb=rng.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            eye_in_hand_rgb=rng.integers(0, 255, (48, 64, 3), dtype=np.uint8),
            joint_positions=rng.standard_normal(7).astype(np.float32),
            gripper_position=0.5, ee_pos_quat=np.array([.4, 0, .3, 0, 0, 0, 1.]),
            gripper_closed=False,
            commanded_joint_positions=rng.standard_normal(7).astype(np.float32),
            commanded_gripper=0.0,
            ft={k: rng.standard_normal(n).astype(np.float32)
                for k, n in FT_OBS_FIELDS})
    w.save_buffer(w.detach_buffer(), instruction="pick up the blue cup and "
                  "place it on the yellow bowl", instruction_id="I000",
                  success=True, collector="t")


# --- 1) 버전 비교가 문자열이 아니라 숫자다 --------------------------------
# knu-1.10.0 이 knu-1.9.0 보다 크다 -- 문자로 비교하면 뒤집힌다.
assert schema_version_key("knu-1.9.0") < schema_version_key("knu-1.10.0")
assert schema_version_key("scene-v1") == schema_version_key("knu-1.0.0")  # 별칭
print("1 통과: 버전 비교가 SemVer 순서를 따른다")

# --- 2) 이어찍기가 도장을 올린다 ------------------------------------------
# 에피소드를 다 지우고 다시 찍는 경우 = scene_015 가 겪은 그 상황.
root = TMP / "up"
w = SceneWriter(root=root, metadata=_meta("knu-1.1.0"))
w.close()
w2 = SceneWriter(root=root, scene_id="S000", resume=True,
                 session_version="knu-1.2.0",
                 session_payload={"mass": 0.85, "com": [-0.01, 0.0, 0.03]})
assert w2.metadata.dataset_version == "knu-1.2.0", w2.version_note
assert "올렸습니다" in w2.version_note, w2.version_note
_episode(w2, np.random.default_rng(0))
w2.close()
w3 = SceneWriter(root=root, scene_id="S000", resume=True)
assert w3.metadata.dataset_version == "knu-1.2.0"      # 파일에 남았다
w3.close()
print("2 통과: 이어찍기가 도장을 올리고 파일에 남긴다")

# --- 3) 내려 찍지 않는다 ---------------------------------------------------
root2 = TMP / "down"
w = SceneWriter(root=root2, metadata=_meta("knu-1.2.0"))
w.close()
w2 = SceneWriter(root=root2, scene_id="S000", resume=True,
                 session_version="knu-1.0.0")
assert w2.metadata.dataset_version == "knu-1.2.0", "내려 찍었다"
assert "내리지 않습니다" in w2.version_note, w2.version_note
w2.close()
print("3 통과: 낮은 버전 세션은 도장을 내리지 않고 이유를 남긴다")

# --- 4) 기존 에피소드가 못 갖추면 올리지 않는다 ---------------------------
# 도장이 파일 내용을 넘어서 약속하면 안 된다. knu-1.0.0 으로 찍힌 (포스·토크가
# 없는) 에피소드가 있는 파일을 1.2.0 으로 올리면 그 에피소드가 거짓이 된다.
root3 = TMP / "partial"
w = SceneWriter(root=root3, metadata=_meta("knu-1.0.0"))
rng = np.random.default_rng(1)
for _ in range(3):
    w.add_frame(
        agentview_rgb=rng.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        eye_in_hand_rgb=rng.integers(0, 255, (48, 64, 3), dtype=np.uint8),
        joint_positions=rng.standard_normal(7).astype(np.float32),
        gripper_position=0.5, ee_pos_quat=np.array([.4, 0, .3, 0, 0, 0, 1.]),
        gripper_closed=False,
        commanded_joint_positions=rng.standard_normal(7).astype(np.float32),
        commanded_gripper=0.0)                 # ft 없음 = 1.0.0 모양
w.save_buffer(w.detach_buffer(), instruction="pick up the blue cup and place "
              "it on the yellow bowl", instruction_id="I000",
              success=True, collector="t")
w.close()
w2 = SceneWriter(root=root3, scene_id="S000", resume=True,
                 session_version="knu-1.2.0")
assert w2.metadata.dataset_version == "knu-1.0.0", "내용이 못 미치는데 올렸다"
assert "올리지 못했습니다" in w2.version_note, w2.version_note
w2.close()
print("4 통과: 기존 에피소드가 새 버전을 못 갖추면 도장을 그대로 둔다")

# --- 4b) 새 버전이 요구하는 metadata 를 모르면 올리지 않는다 --------------
# 관측만 보고 올리면, 도장은 knu-1.2.0 인데 그 버전이 요구하는 부하 모델이
# 없는 파일이 된다 -- 고치려던 것과 똑같은 모양의 결함이다.
root4 = TMP / "nometa"
# 부하가 **없는** 1.1.1 파일이어야 한다 -- 있으면 1.2.0 이 이미 만족되어
# 올리는 것이 옳고, 이 검사가 보려는 상황이 아니다.
w = SceneWriter(root=root4, metadata=_meta("knu-1.1.1", payload=False))
w.close()
w2 = SceneWriter(root=root4, scene_id="S000", resume=True,
                 session_version="knu-1.2.0")            # 부하를 안 준다
assert w2.metadata.dataset_version == "knu-1.1.1", "부하를 모르는데 올렸다"
assert "알지 못합니다" in w2.version_note, w2.version_note
w2.close()
# 부하를 주면 올라가고, 그 값이 파일에 남는다
w3 = SceneWriter(root=root4, scene_id="S000", resume=True,
                 session_version="knu-1.2.0",
                 session_payload={"mass": 0.85, "com": [-0.01, 0.0, 0.03]})
assert w3.metadata.dataset_version == "knu-1.2.0", w3.version_note
w3.close()
import h5py, json as _json  # noqa: E402
with h5py.File(root4 / "scene_000.hdf5") as _f:
    _m = _f["metadata"].attrs
    _need = schema_required_fields("knu-1.2.0")["metadata_attrs"]
    assert not [k for k in _need if k not in _m], [k for k in _need if k not in _m]
    assert float(_m["payload_mass"]) == 0.85
    assert _json.loads(_m["payload_com"]) == [-0.01, 0.0, 0.03]
print("4b 통과: metadata 를 모르면 안 올리고, 주면 올리면서 파일에 남긴다")

# --- 5) 같은 버전이면 아무 말도 하지 않는다 -------------------------------
w = SceneWriter(root=TMP / "same", metadata=_meta("knu-1.2.0"))
w.close()
w2 = SceneWriter(root=TMP / "same", scene_id="S000", resume=True,
                 session_version="knu-1.2.0")
assert w2.version_note == "", w2.version_note
w2.close()
print("5 통과: 버전이 같으면 조용하다")

shutil.rmtree(TMP, ignore_errors=True)
print("\n이어찍기 버전 도장 검증 통과")
