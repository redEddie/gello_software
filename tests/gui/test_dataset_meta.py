"""dataset-identity.json 메타 + discover_datasets + plan_progress 검증.

데이터셋 폴더 컨벤션 (2026-09-04): dataset-identity.json (신원) +
instructions.json (수집 계획, 고정 파일명) + scene_NNN.hdf5 (데이터).
Qt 없이 돈다 -- 마법사의 Continue 목록이 이 모듈 위에 서 있다.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)

from gello.scene.dataset_meta import (  # noqa: E402
    IDENTITY_FILENAME,
    PLAN_FILENAME,
    DatasetIdentity,
    discover_datasets,
    load_identity,
    plan_path,
    plan_progress,
    save_identity,
    validate_dataset_name,
)
from gello.scene.scene_format import SceneMetadata, SceneWriter  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="dsmeta_"))


def _cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


# ---- 1. 이름 검증 ----
assert validate_dataset_name("fr3-tabletop") is None
assert validate_dataset_name("knu.libero_v2") is None
assert validate_dataset_name("") is not None
assert validate_dataset_name("한글") is not None
assert validate_dataset_name(" space name") is not None
print("1 통과: 이름 검증 (HF repo 이름 규칙)")

# ---- 2. identity 저장/읽기 왕복 + hf_repo 기본값 ----
ds = TMP / "fr3-tabletop"
ident = DatasetIdentity(name="fr3-tabletop", concept="탁상 정리", created="2026-09-04")
assert ident.hf_repo == "knu-physical-ai/fr3-tabletop"   # 기본값 자동
save_identity(ds, ident)
assert (ds / IDENTITY_FILENAME).is_file()
back = load_identity(ds)
assert back is not None and back.name == "fr3-tabletop" and back.concept == "탁상 정리"
assert back.hf_repo == "knu-physical-ai/fr3-tabletop"
assert load_identity(TMP / "없음") is None                # 없으면 None (예외 아님)
broken = TMP / "broken"; broken.mkdir()
(broken / IDENTITY_FILENAME).write_text("{ not json")
assert load_identity(broken) is None                      # 깨져도 None
assert plan_path(ds) == ds / "instructions.json" and PLAN_FILENAME == "instructions.json"
print("2 통과: identity 왕복 + hf_repo 기본값 + 없음/깨짐 -> None")

# ---- 3. discover: 부모 폴더 스캔 + dedupe + legacy 폴더 ----
legacy = TMP / "old"; legacy.mkdir()
(legacy / "scene_000.hdf5").write_bytes(b"not-hdf5")      # 이름만으로 발견
(TMP / "not-a-dataset").mkdir()
entries = discover_datasets([TMP, ds])                    # ds 중복 후보 -> dedupe
paths = [e.path.name for e in entries]
# "broken" 도 identity 파일이 있으니 데이터셋으로 발견된다 (내용이 깨졌을 뿐)
assert sorted(paths) == ["broken", "fr3-tabletop", "old"], paths
by_name = {e.path.name: e for e in entries}
assert by_name["fr3-tabletop"].identity is not None
assert by_name["old"].identity is None and by_name["old"].scene_files == 1
assert by_name["old"].name == "old"                       # identity 없으면 폴더명
print("3 통과: discover -- 부모 스캔 + dedupe + legacy 폴더 인식")

# ---- 4. plan_progress: 계획 × scene 파일 실측 ----
r = np.random.default_rng(0)
md = SceneMetadata(scene_id="S000", objects=["OBJ-CUP-BLU-01"],
                   layout={"grid": [3, 3],
                           "placements": {"OBJ-CUP-BLU-01": {"zone": [0, 0]}}},
                   description="plan_progress 테스트")
w = SceneWriter(ds, metadata=md, collector="t")
for i in range(3):
    w.start_episode()
    for _ in range(3):
        w.add_frame(agentview_rgb=r.integers(0, 255, (8, 8, 3), dtype=np.uint8),
                    eye_in_hand_rgb=r.integers(0, 255, (8, 8, 3), dtype=np.uint8),
                    joint_positions=r.standard_normal(7).astype(np.float32),
                    gripper_position=0.5, ee_pos_quat=np.zeros(7),
                    gripper_closed=False,
                    commanded_joint_positions=r.standard_normal(7).astype(np.float32),
                    commanded_gripper=0.0)
    w.save_buffer(w.detach_buffer(), instruction="pick up the blue cup",
                  instruction_id="I000", success=(i < 2), collector="t")
w.close()
assert plan_progress(ds) is None                          # 계획 아직 없음
(ds / PLAN_FILENAME).write_text(json.dumps({"plan_version": 1, "scenes": [
    {"scene_id": "S000", "slots": [
        {"instruction_id": "I000", "instruction": "pick up the blue cup", "target": 5}]},
    {"scene_id": "S999", "slots": [                       # 파일 없는 scene
        {"instruction_id": "I000", "instruction": "open the top drawer", "target": 3}]}]},
    ensure_ascii=False), encoding="utf-8")
done, total = plan_progress(ds)
assert (done, total) == (2, 8), (done, total)             # usable 2 / target 5+3
(ds / PLAN_FILENAME).write_text("{ broken")              # 깨진 계획 -> None
assert plan_progress(ds) is None
print("4 통과: plan_progress -- 실측 2/(5+3), 계획 없음/깨짐 -> None")

# ---- 5. 요약이 에피소드를 센다 ----
entry = next(e for e in discover_datasets([TMP]) if e.path.name == "fr3-tabletop")
assert entry.scene_files == 1 and entry.episodes == 3, entry
print("5 통과: discover 요약이 scene 수·에피소드 수를 실측")

print("\ndataset_meta 검증 통과")
_cleanup()
import os  # noqa: E402

os._exit(0)
