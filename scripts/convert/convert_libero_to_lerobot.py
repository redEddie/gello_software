"""Convert curated LIBERO-format <task>_demo.hdf5 files into one LeRobotDataset
(parquet + video) for efficient, HF-Viewer-browsable upload.

Collection + curation (deleting bad takes) stays exactly as-is in the GUI's
HDF5 workflow -- this only runs afterward, at upload time. Convert *after*
you've deleted what you don't want; this script has no delete/filter step
of its own beyond --only-success, so whatever's still in the .hdf5 files is
what ends up in the LeRobotDataset.

Multiple task files become one multi-task LeRobotDataset (LeRobot's native
way to hold many tasks): each episode carries the source file's language
instruction as its `task`.

SCENE-V1 파일도 같은 명령으로 읽는다 (2026-08-13)
--------------------------------------------------
scene_*.hdf5 (파일=scene, instruction 은 에피소드 attrs) 는 내부 구조로
자동 판별된다 -- 파일명이 아니라 metadata 그룹의 scene_id 로. 차이점:

- task 는 에피소드 attrs 의 instruction (scene 이 달라도 같은 문장이면
  같은 task 로 합쳐진다).
- scene 포맷은 에피소드 삭제가 없으므로(immutable) 큐레이션은 여기서:
  기본 quality_status=success 만 변환, --include-failed 로 failed 포함,
  bad_data/retake/deprecated 는 상시 제외.
- 변환 결과에 meta/episode_uids.json 사이드카를 유지한다: LeRobot
  episode_index -> 출처(episode_uid/scene_id/instruction_id). 학습 로더는
  이 파일을 모르지만, (a) 평가에서 scene 단위 실패 분석, (b) scene 파일의
  --resume 스킵을 개수 산술이 아닌 uid 대조로 정확하게 하는 데 쓴다.
  legacy 파일 에피소드도 출처(source_file/episode)는 기록된다.

SCHEMA IS DETECTED FROM THE FILES, NOT HARDCODED
--------------------------------------------------
Since the GUI's "데이터셋 구조 사용자 지정" dialog (gello/dataset_schema.py) lets an
operator pick a different action space and/or drop obs fields per session,
this script reads each episode's actual obs/ keys, `actions` width, and
`action_space` attr (see gello/libero_format.py's LiberoTaskWriter) instead
of assuming the original fixed LIBERO schema. Files predating that attr are
treated as `ee_delta` (what they always were).

LeRobotDataset fixes ONE `features` dict for the whole dataset at creation
time, so every episode being converted together must share the exact same
obs fields + action space + gripper-in-action-or-not. A quick pre-flight
pass (attrs/keys only, no image data) checks this and fails with a clear
message before any conversion work starts -- convert mismatched files
separately, with different --repo-id/--root.

`observation.state` is built from whichever of `joint_states`/`gripper_states`
are present (skipped entirely if neither is). Other optional obs fields
(ee_pos, ee_ori, ee_states, joint_velocities, timestamp) are NOT currently
propagated into the LeRobotDataset -- only images, joint/gripper state, and
actions are.

Images are centre-cropped square and resized to --image-size (LEROBOT_IMAGE_SIZE
convention, see gello/libero_format.py). The GUI's schema dialog can record
at native camera resolution instead (image_size="원본 해상도 유지") -- this
script does not support converting those files yet; it fails loudly with a
clear message rather than mis-declaring the LeRobotDataset feature shape.

--resume: TRUE INCREMENTAL UPLOAD (verified against lerobot's actual code,
not just its docstring)
--------------------------------------------------------------------------
Without --resume, every run rebuilds the whole LeRobotDataset from scratch
via LeRobotDataset.create() -- converting/re-encoding *all* task files again
just to add one new task gets more wasteful as the dataset grows.
LeRobotDataset.resume(repo_id, root) fixes this, but three things about it
are non-obvious enough that they were checked directly against
lerobot/datasets/{lerobot_dataset,dataset_metadata,dataset_writer}.py
before wiring it in here (not assumed from the docstring):

1. What resume() actually downloads: ONLY meta/ (info.json, stats.json,
   tasks.parquet, episodes/*.parquet) -- never data/ or videos/. This is
   safe (not "silently incomplete") because the first episode saved after
   resume() *always* starts a brand-new chunk/file for both the parquet and
   the video (dataset_writer.py's _save_episode_data/_save_episode_video
   unconditionally call update_chunk_file_indices() the first time), so it
   never needs to open or append into an old chunk file it doesn't have
   locally. Verified locally (no network) by resuming into the SAME root a
   create()'d dataset was built in, adding a new task's episodes, and
   hashing every file before/after: only meta/info.json, meta/stats.json,
   and meta/tasks.parquet changed (all KB-sized bookkeeping); every
   data/*.parquet and videos/*.mp4 file was byte-identical. push_to_hub()
   (huggingface_hub's upload_folder) only uploads new/changed local files
   and does not delete untouched remote ones, so a --resume + --push cycle
   only ever transfers the small metadata + the new task's own chunk files
   -- old tasks' videos are never re-encoded or re-uploaded.

2. Re-curating an ALREADY-PUSHED task (deleting/relabeling episodes in the
   source .hdf5 after that batch is already on the Hub): there is no
   supported way to edit or remove an individual episode from a published
   LeRobotDataset -- resume() only ever appends. The .hdf5 files remain the
   editable source of truth; the Hub LeRobotDataset should be treated as
   append-only once pushed. Practical rule: finish curating a task's .hdf5
   (delete bad takes) *before* the first --resume --push for it, the same
   "convert after you've deleted what you don't want" rule this script
   already followed pre-resume.

3. Concurrent --resume + --push from two people is NOT safe. Both sides
   compute their next chunk/file index from whatever meta/ they each
   downloaded; if two people resume from the same base state, both compute
   the SAME next chunk/file path and each push overwrites the other's file
   at that path with their own content (huggingface_hub's commit API has no
   compare-and-swap against a stale base revision) -- silent data loss for
   whoever pushes second, not a merge conflict either side would notice.
   There is no locking here (out of scope) -- coordinate so only one person
   resume+pushes to a given --repo-id at a time; if you weren't first,
   re-run --resume against the now-current Hub state before pushing.

Usage:
    python scripts/convert/convert_libero_to_lerobot.py \
        /home/franka/libero_datasets/*.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop-lerobot \
        --root /home/franka/lerobot_upload

    # ... review locally, then push:
    python scripts/convert/convert_libero_to_lerobot.py \
        /home/franka/libero_datasets/*.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop-lerobot \
        --root /home/franka/lerobot_upload \
        --push --private=false

    # Later: add a new task's episodes to that same Hub dataset, without
    # re-converting/re-uploading the earlier tasks (see --resume above):
    python scripts/convert/convert_libero_to_lerobot.py \
        /home/franka/libero_datasets/pick_up_the_blue_cup_demo.hdf5 \
        --repo-id knu-physical-ai/fr3-libero-teleop-lerobot \
        --root /home/franka/lerobot_upload \
        --resume --push --private=false
"""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
from lerobot.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDataset
from lerobot.datasets.utils import DatasetInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gello.data.dataset_schema import (  # noqa: E402
    ACTION_SPACE_EE_DELTA,
    OBS_AGENTVIEW_RGB,
    OBS_COMMANDED_GRIPPER_STATES,
    OBS_COMMANDED_JOINT_STATES,
    OBS_EE_ORI,
    OBS_EE_POS,
    OBS_EE_STATES,
    OBS_EYE_IN_HAND_RGB,
    OBS_GRIPPER_STATES,
    OBS_JOINT_STATES,
    SCHEMA_VERSION,
    normalize_schema_version,
)
from gello.data.libero_format import (  # noqa: E402
    EYE_IN_HAND_CROP_X_SHIFT,
    action_column_names,
    resize_rgb,
)
from gello.scene.scene_format import (  # noqa: E402
    EPISODE_GROUP_RE,
    QUALITY_FAILED,
    QUALITY_SUCCESS,
)

# 학습 파이프라인이 DINOv3 를 쓰고 그 입력이 224x224 다. .hdf5 쪽 기본값
# (원본 640x480)과 일부러 다르다 -- 원본은 보관, 이쪽은 실사용 크기.
LEROBOT_IMAGE_SIZE = 224

# Hub 데이터셋 카드에 박을 태그. push_to_hub 가 매 푸시마다 카드를 다시
# 만들기 때문에, 웹에서 손으로 넣은 태그는 다음 푸시에 지워진다 -- 여기가
# 유일하게 살아남는 자리다. (lerobot 이 'LeRobot' 태그는 알아서 붙인다.)
DATASET_TAGS = [
    "libero", "franka", "fr3", "manipulation", "teleoperation",
    "imitation-learning", "multi-camera", "rgb",
]


def _merge_card_tags(repo_id: str) -> None:
    """기존 데이터셋 카드의 태그에 DATASET_TAGS 를 합친다 (없으면 조용히 넘어감).

    교체 업로드(--replace) 경로는 push_to_hub 를 안 거쳐 카드가 재생성되지
    않으므로, 태그 유지는 여기서 한다. 태그 실패로 업로드 자체를 실패시키지는
    않는다 -- 데이터가 올라간 뒤의 치장이다."""
    try:
        from huggingface_hub import DatasetCard

        card = DatasetCard.load(repo_id, repo_type="dataset")
        merged = sorted(set(card.data.tags or []) | set(DATASET_TAGS))
        if merged != sorted(card.data.tags or []):
            card.data.tags = merged
            card.push_to_hub(repo_id, repo_type="dataset")
            print(f"카드 태그 갱신: {merged}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"카드 태그 갱신 건너뜀 ({type(e).__name__}: {e})", flush=True)


_STAT_KEYS = ("min", "max", "mean", "std", "count", "q01", "q10", "q50", "q90", "q99")


def repair_metadata(root: Path) -> str:
    """Make meta/info.json and meta/stats.json agree with meta/episodes/.

    resume() seeds its running totals from the metadata it downloaded, so a Hub
    copy whose info.json is stale hands that error to every future run: the
    episode records accumulate correctly while total_episodes/total_frames stay
    short, and the newest episodes become invisible to readers. It happened
    twice -- once from an interrupted push, then again when the next resume
    inherited the bad numbers (117 -> 269 instead of 121 -> 273).

    meta/episodes/*.parquet is the authority: one row per episode, each with
    its own length and per-feature stats. Everything here is recomputed from
    it, so a wrong seed cannot survive a conversion.

    Returns a human-readable description of what changed ("" if nothing did).
    """
    import glob as _glob

    import pandas as pd

    files = sorted(_glob.glob(str(root / "meta/episodes/**/*.parquet"), recursive=True))
    if not files:
        return ""
    eps = pd.concat([pd.read_parquet(f) for f in files])
    n_ep, n_frames = len(eps), int(eps["length"].sum())

    info_path = root / "meta/info.json"
    info = json.loads(info_path.read_text())
    before = (info.get("total_episodes"), info.get("total_frames"))
    if before == (n_ep, n_frames):
        return ""
    info["total_episodes"] = n_ep
    info["total_frames"] = n_frames
    info["splits"] = {"train": f"0:{n_ep}"}
    info_path.write_text(json.dumps(info, indent=4))

    # stats는 에피소드별 통계를 집계해 다시 만든다. 형태(이미지의 (3,1,1) 등)는
    # 기존 stats.json이 권위이므로 그걸 본떠 되돌린다.
    stats_path = root / "meta/stats.json"
    detail = ""
    try:
        from lerobot.datasets.compute_stats import aggregate_stats

        old = json.loads(stats_path.read_text())
        shapes = {f: {k: np.asarray(v).shape for k, v in d.items()} for f, d in old.items()}
        feats = sorted({c.split("/")[1] for c in eps.columns if c.startswith("stats/")})
        per_ep = []
        for _, row in eps.iterrows():
            entry = {}
            for feat in feats:
                d = {}
                for key in _STAT_KEYS:
                    col = f"stats/{feat}/{key}"
                    if col not in row or row[col] is None:
                        continue
                    v = np.asarray(row[col], dtype=np.float64).ravel()
                    want = shapes.get(feat, {}).get(key)
                    d[key] = v.reshape(want) if want and v.size == int(np.prod(want)) else v
                if d:
                    entry[feat] = d
            per_ep.append(entry)
        agg = aggregate_stats(per_ep)
        stats_path.write_text(json.dumps(
            {k: {kk: np.asarray(vv).tolist() for kk, vv in v.items()} for k, v in agg.items()},
            indent=4))
        detail = ", stats.json 재계산"
    except Exception as e:  # noqa: BLE001
        detail = f", stats.json 재계산 실패({type(e).__name__}) -- 정규화 통계가 낡을 수 있음"
    return (f"메타데이터 보정: {before[0]}개/{before[1]}프레임 -> "
            f"{n_ep}개/{n_frames}프레임{detail}")


def check_integrity(root: Path) -> list[str]:
    """Structural problems that no amount of metadata patching can fix.

    A wrong seed does more than miscount. resume() numbers the episodes it
    appends starting from the total it inherited, so a total that is short by
    four makes the next four episodes reuse indices 117-120 that already exist
    -- two different episodes answering to the same index. Recomputing
    total_episodes afterwards hides that (the count becomes right) while the
    collision stays, which is worse than the honest miscount.

    So this is checked separately from repair_metadata, and it is fatal: the
    only fix is a rebuild.
    """
    import glob as _glob

    import pandas as pd

    problems = []
    files = sorted(_glob.glob(str(root / "meta/episodes/**/*.parquet"), recursive=True))
    if not files:
        return ["meta/episodes 가 없습니다"]
    eps = pd.concat([pd.read_parquet(f) for f in files])
    idx = eps["episode_index"].tolist()
    dup = sorted({i for i in idx if idx.count(i) > 1})
    if dup:
        problems.append(
            f"episode_index 중복 {len(dup)}개 {dup[:8]}{'...' if len(dup) > 8 else ''} "
            f"-- 서로 다른 에피소드가 같은 번호를 씁니다")
    if sorted(idx) != list(range(len(idx))):
        problems.append(
            f"episode_index 가 0..{len(idx) - 1} 연속이 아닙니다 "
            f"(범위 {min(idx)}~{max(idx)}, {len(idx)}개)")
    return problems


def _fail_integrity(root: Path, problems: list) -> None:
    raise SystemExit(
        "데이터셋이 구조적으로 깨져 있어 중단합니다:\n"
        + "\n".join(f"  - {p}" for p in problems)
        + f"\n\n{root} 를 지우고 --resume 없이 전체를 다시 만드세요.\n"
          "  (원인은 대개 Hub 메타데이터가 낡아 이어붙이기 시작 번호가 어긋난 것입니다.)"
    )


def _hub_commit_message(root: Path, repo_id: str, info: dict, replace: bool) -> str:
    """Hub 커밋 메시지 -- 데이터셋이 자라는 것이 이력에서 읽히게.

    예) ``scene-v1: +12 ep (S000 +10, S001 +2) → 총 47 ep / 9,812 fr · scenes S000-S001``
    Hub 의 이전 총계는 dataset_sync.hub_meta 로, scene 별 증감은 사이드카
    (meta/episode_uids.json) 의 uid 를 Hub 사본과 대조해 계산한다. 조회 실패
    (오프라인·첫 푸시)면 로컬 총계만 적는다. 메시지가 틀려도 업로드를 막지는
    않는다 -- 이력 가독성 목적이지 정합성 장치가 아니다.
    """
    total_ep = int(info.get("total_episodes", 0))
    total_fr = int(info.get("total_frames", 0))
    sidecar = root / "meta" / "episode_uids.json"
    per_scene_local: dict = {}
    local_uids: set = set()
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        for e in data.values():
            if isinstance(e, dict) and e.get("episode_uid"):
                local_uids.add(e["episode_uid"])
                sid = e.get("scene_id") or e["episode_uid"].split("-")[1]
                per_scene_local[sid] = per_scene_local.get(sid, 0) + 1
    except Exception:  # noqa: BLE001
        data = {}
    prev_total = None
    hub_uids: set = set()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from gello.data.dataset_sync import hub_episode_uids, hub_meta

        counts, _lens, err = hub_meta(repo_id)
        if not err:
            prev_total = sum(counts.values())
        hu, err2 = hub_episode_uids(repo_id)
        if not err2 and hu:
            hub_uids = hu
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if prev_total is not None:
        delta = total_ep - prev_total
        parts.append(f"{delta:+d} ep" if delta else "±0 ep")
    else:
        parts.append(f"{total_ep} ep")
    if per_scene_local:
        per_scene_new: dict = {}
        for e in data.values():
            if isinstance(e, dict) and e.get("episode_uid") and e["episode_uid"] not in hub_uids:
                sid = e.get("scene_id") or e["episode_uid"].split("-")[1]
                per_scene_new[sid] = per_scene_new.get(sid, 0) + 1
        if per_scene_new and hub_uids:
            parts.append("(" + ", ".join(f"{k} +{v}" for k, v in sorted(per_scene_new.items())) + ")")
        elif per_scene_local:
            parts.append("(" + ", ".join(f"{k} {v}" for k, v in sorted(per_scene_local.items())) + ")")
    tail = f"→ 총 {total_ep} ep / {total_fr:,} fr"
    if per_scene_local:
        ks = sorted(per_scene_local)
        tail += f" · scenes {ks[0]}" + (f"-{ks[-1]}" if len(ks) > 1 else "")
    kind = "rebuild(큐레이션 반영, 교체)" if replace else "scene-v1"
    return f"{kind}: {' '.join(parts)} {tail}"


def _verify_tag(repo_id: str) -> bool:
    """Check that the version tag lerobot reads actually points at what we pushed.

    lerobot resolves everything at ``revision=CODEBASE_VERSION`` (a git *tag*),
    and push_to_hub moves that tag only as its final step. A push that dies
    anywhere earlier -- as one did, in card generation -- leaves main updated
    and the tag frozen on an older commit, so the next resume() seeds its
    totals from stale metadata and silently produces wrong counts. Nothing
    about the Hub page shows this; the tag has to be asked about directly.
    """
    try:
        from huggingface_hub import HfApi

        refs = HfApi().list_repo_refs(repo_id, repo_type="dataset")
        main = next((b.target_commit for b in refs.branches if b.name == "main"), None)
        tag = next((t.target_commit for t in refs.tags if t.name == CODEBASE_VERSION), None)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 태그 확인 실패: {type(e).__name__}: {e}", flush=True)
        return True
    if tag is None:
        print(f"[경고] {CODEBASE_VERSION} 태그가 없습니다. lerobot이 이 데이터셋을 "
              f"읽지 못할 수 있습니다.", flush=True)
        return False
    elif tag != main:
        print(f"[경고] {CODEBASE_VERSION} 태그가 main과 다른 커밋을 가리킵니다 "
              f"(tag {tag[:8]} vs main {main[:8]}).\n"
              f"        lerobot은 태그 쪽을 읽으므로 방금 올린 내용이 보이지 "
              f"않습니다. 업로드를 다시 실행하세요.", flush=True)
        return False
    else:
        print(f"[검증] {CODEBASE_VERSION} 태그가 방금 커밋을 가리킵니다.", flush=True)
    return True


def _language_instruction(f: h5py.File) -> str:
    info = json.loads(f["data"].attrs["problem_info"])
    lang = info.get("language_instruction", "")
    if len(lang) >= 2 and lang.startswith('"') and lang.endswith('"'):
        lang = lang[1:-1]
    return lang


# --------------------------------------------------------------- scene-v1
# scene 파일(scene_*.hdf5)은 "파일=scene, instruction 은 에피소드 attrs" 라서
# legacy 의 파일 레벨 problem_info 전제가 성립하지 않는다. 판별은 파일명이
# 아니라 내부 구조로 한다 (경로에서 아무것도 역산하지 않는다는 원칙).

def _is_scene_file(f: h5py.File) -> bool:
    return "metadata" in f and "scene_id" in f["metadata"].attrs


def _scene_convertible(f: h5py.File, include_failed: bool):
    """변환 대상 scene 에피소드를 번호순으로 (name, grp, instruction) yield.

    scene 포맷은 에피소드 삭제가 없으므로(immutable) 이 quality 필터가
    큐레이션 관문이다: 기본 success 만, --include-failed 면 failed 까지.
    bad_data/retake/deprecated 는 데이터 자체가 못 쓰는 것이라 항상 제외.
    """
    names = sorted((k for k in f.keys() if EPISODE_GROUP_RE.match(k)),
                   key=lambda n: int(n.split("_")[1]))
    allowed = {QUALITY_SUCCESS} | ({QUALITY_FAILED} if include_failed else set())
    for name in names:
        grp = f[name]
        if str(grp.attrs.get("quality_status", "")) not in allowed:
            continue
        yield name, grp, str(grp.attrs["instruction"])


def _load_uid_sidecar(root: Path, repo_id: str, resume: bool) -> dict:
    """meta/episode_uids.json -- LeRobot episode_index -> 출처(episode_uid 등).

    scene 단위 실패 분석과 scene 파일의 정확한 resume 스킵(uid 대조)을 위해
    변환기가 유지하는 사이드카다. 학습 로더는 이 파일을 모른다(영향 0).
    resume 인데 로컬에 없으면 Hub 에서 받아본다 -- 사이드카 도입 전 데이터셋
    (legacy 전용)은 없는 것이 정상이므로 실패는 조용히 빈 dict.
    """
    local = root / "meta" / "episode_uids.json"
    if local.exists():
        try:
            return json.loads(local.read_text())
        except ValueError:
            return {}
    if resume:
        try:
            from huggingface_hub import hf_hub_download

            # lerobot 은 모든 것을 CODEBASE_VERSION("v3.0") 태그로 읽는다.
            # main 과 태그가 갈라지면 사이드카를 낡은 버전에서 읽어 uid 대조가
            # 어긋날 수 있으므로 revision 을 명시한다.
            p = hf_hub_download(repo_id, "meta/episode_uids.json",
                                repo_type="dataset",
                                revision=CODEBASE_VERSION)
            return json.loads(Path(p).read_text())
        except Exception:  # noqa: BLE001 - 없는 것이 정상 케이스
            return {}
    return {}


def _write_uid_sidecar(root: Path, records: dict) -> None:
    out = root / "meta" / "episode_uids.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=1,
                              sort_keys=True))


def _is_success(grp) -> bool:
    """True only for an episode explicitly marked successful.

    NOT ``attrs.get("success") is not True``: h5py hands back ``numpy.bool_``,
    and ``np.True_ is True`` is False, so identity comparison rejected every
    episode -- ``--only-success`` silently filtered out the entire dataset and
    reported "변환할 에피소드가 없습니다". Compare by value, and treat a
    missing attr (older files, never labeled) as not-success rather than
    crashing.
    """
    v = grp.attrs.get("success")
    return bool(v) if v is not None else False


# 변환기가 실제로 소비하는 obs 키 (_build_features 참조). 스키마 일치 검사를
# 이 집합으로 좁혀야 소비하지 않는 부가 필드(depth #17, timestamp 등)가
# 있는 에피소드와 없는 에피소드를 한 데이터셋으로 변환할 수 있다 --
# frozenset 전체 비교는 "부가 필드 추가 = 기존 데이터와 변환 불가"를 만든다.
_CONSUMED_OBS_KEYS = frozenset({
    OBS_AGENTVIEW_RGB, OBS_EYE_IN_HAND_RGB, OBS_JOINT_STATES, OBS_GRIPPER_STATES,
    OBS_COMMANDED_JOINT_STATES, OBS_COMMANDED_GRIPPER_STATES,
})


def _episode_schema(grp: h5py.Group) -> dict:
    obs = grp["obs"]
    obs_keys = frozenset(obs.keys()) & _CONSUMED_OBS_KEYS
    action_space = grp.attrs.get("action_space", ACTION_SPACE_EE_DELTA)
    base_cols = action_column_names(action_space)
    action_dim = grp["actions"].shape[1]
    if action_dim not in (len(base_cols), len(base_cols) + 1):
        raise ValueError(
            f"actions has {action_dim} columns, expected {len(base_cols)} or "
            f"{len(base_cols) + 1} for action_space={action_space!r}"
        )
    # Images are reduced to --image-size here (see _to_target); the .hdf5 (LIBERO/OpenVLA
    # convention) -- the GUI's "사용자 지정" dialog can record at native
    # camera resolution instead (DatasetSchemaConfig.image_size=None), but
    # this script does not support converting those; check_image_shapes()
    # fails loudly rather than silently mis-declaring the LeRobotDataset
    # feature shape.
    has_gripper = action_dim == len(base_cols) + 1
    # Episodes written after the schema dialog's per-column name overrides
    # were added carry the ACTUAL names used (see gello/libero_format.py's
    # save_episode); older episodes predate the attr and never had
    # overrides, so the built-in names are exactly right for them too.
    raw_names = grp.attrs.get("action_column_names")
    if raw_names:
        action_names = tuple(json.loads(raw_names))
    else:
        action_names = tuple(base_cols) + (("gripper.pos",) if has_gripper else ())
    return {
        "obs_keys": obs_keys,
        "action_space": action_space,
        "has_gripper": has_gripper,
        "action_names": action_names,
        # derive_commanded_ee_actions.py가 추가하는 파생 액션 (있는 파일만)
        "has_actions_ee": "actions_ee" in grp,
    }


def _check_image_shape(path: Path, name: str, obs: h5py.Group, key: str,
                       target: int) -> None:
    """The .hdf5 may be larger than what goes to the Hub -- only reject what
    cannot be reduced to `target`."""
    shape = obs[key].shape[1:]
    if len(shape) != 3 or shape[2] != 3:
        raise SystemExit(f"{path.name}/{name}의 {key} shape={shape} -- (H, W, 3) 이 아닙니다.")
    h, w = shape[0], shape[1]
    if min(h, w) < target:
        raise SystemExit(
            f"{path.name}/{name}의 {key} shape={shape} 인데 --image-size={target} 로 "
            f"줄이려 합니다. 원본보다 크게 만들 수 없습니다.")


def _to_target(img: np.ndarray, target: int, zoom: float = 1.0,
               x_shift: int = 0, y_shift: int = 0) -> np.ndarray:
    """Square-crops then resizes to `target` -- the same operation the
    collector applies (libero_format.square_crop 규약), so a 480x480 .hdf5 and
    a 256x256 one converted from it differ only in how much detail survived,
    not in framing.

    Crop parameters come from each episode's ``crop_params`` attrs. An
    already-target-square source passes through untouched."""
    if img.shape[0] == target and img.shape[1] == target and zoom <= 1.0:
        return img
    return resize_rgb(img, size=target, zoom=zoom, x_shift=x_shift,
                      y_shift=y_shift)


def _scan_schema(hdf5_paths: list, only_success: bool,
                 include_failed: bool = False) -> dict:
    """Pre-flight pass over every episode that will be converted -- cheap
    (attrs/group keys only, no array data) -- so a schema mismatch is caught
    before LeRobotDataset.create() has written anything to --root.

    변환 본문과 같은 필터를 적용해 "변환될 에피소드"만 본다: legacy 는
    --only-success, scene 은 quality_status (기본 success 만)."""
    reference = None
    reference_loc = None
    for path in hdf5_paths:
        with h5py.File(path, "r") as f:
            if _is_scene_file(f):
                candidates = [(n, g) for n, g, _ in
                              _scene_convertible(f, include_failed)]
            else:
                data = f["data"]
                candidates = [
                    (n, data[n])
                    for n in sorted(data.keys(), key=lambda x: int(x.split("_")[1]))
                    if not (only_success and not _is_success(data[n]))
                ]
            for name, grp in candidates:
                schema = _episode_schema(grp)
                if reference is None:
                    reference = schema
                    reference_loc = f"{path.name}/{name}"
                elif schema != reference:
                    raise SystemExit(
                        f"스키마 불일치: {reference_loc}는 {reference}였는데 "
                        f"{path.name}/{name}는 {schema}입니다.\n"
                        "LeRobotDataset은 변환 전체에 걸쳐 하나의 고정된 obs/action "
                        "구조가 필요합니다 -- 스키마가 다른 파일은 --repo-id/--root를 "
                        "바꿔 따로 변환하세요."
                    )
    if reference is None:
        raise SystemExit("변환할 에피소드가 없습니다 (--only-success로 전부 걸러졌을 수 있음)")
    return reference


def _build_features(schema: dict, image_size: int) -> tuple[dict, list[str]]:
    obs_keys = schema["obs_keys"]
    features = {}

    state_parts = []
    state_names = []
    if OBS_JOINT_STATES in obs_keys:
        state_parts.append(OBS_JOINT_STATES)
        state_names += [f"joint{i}.pos" for i in range(1, 8)]
    if OBS_GRIPPER_STATES in obs_keys:
        state_parts.append(OBS_GRIPPER_STATES)
        state_names += ["gripper.pos"]
    if state_parts:
        features["observation.state"] = {
            "dtype": "float32", "shape": (len(state_names),), "names": state_names,
        }

    if OBS_AGENTVIEW_RGB in obs_keys:
        features["observation.images.agent"] = {
            "dtype": "video", "shape": (image_size, image_size, 3), "names": ["height", "width", "channel"],
        }
    if OBS_EYE_IN_HAND_RGB in obs_keys:
        features["observation.images.wrist"] = {
            "dtype": "video", "shape": (image_size, image_size, 3), "names": ["height", "width", "channel"],
        }

    # GELLO leader 명령 스트림 (commanded-ee-actions 브랜치 수집분).
    # 차원 이름을 observation.state와 동일하게 맞춰 시각화에서 실측 vs 명령이
    # 같은 이름으로 나란히 비교되게 한다.
    cmd_parts = []
    cmd_names = []
    if OBS_COMMANDED_JOINT_STATES in obs_keys:
        cmd_parts.append(OBS_COMMANDED_JOINT_STATES)
        cmd_names += [f"joint{i}.pos" for i in range(1, 8)]
    if OBS_COMMANDED_GRIPPER_STATES in obs_keys:
        cmd_parts.append(OBS_COMMANDED_GRIPPER_STATES)
        cmd_names += ["gripper.pos"]
    if cmd_parts:
        features["observation.commanded_state"] = {
            "dtype": "float32", "shape": (len(cmd_names),), "names": cmd_names,
        }

    action_names = list(schema["action_names"])
    features["action"] = {
        "dtype": "float32", "shape": (len(action_names),), "names": action_names,
    }
    if schema["has_actions_ee"]:
        features["action_ee"] = {
            "dtype": "float32", "shape": (7,),
            "names": ["dx", "dy", "dz", "d_axis_x", "d_axis_y", "d_axis_z", "gripper.pos"],
        }
    return features, state_parts, cmd_parts


# Only the features this script itself declares -- not the bookkeeping keys
# (timestamp, frame_index, episode_index, index, task_index) LeRobotDataset
# adds on its own regardless of what's passed to create().
_CHECKED_FEATURE_KEYS = ("observation.state", "observation.commanded_state",
                         "observation.images.agent", "observation.images.wrist",
                         "action", "action_ee")


def _task_episode_count(ds, task: str) -> int:
    """How many episodes of ``task`` the resumed dataset already holds.

    The episode metadata stores the task as a list of strings (one dataset
    supports multi-task episodes), so a plain equality test against the string
    silently matches nothing and reports 0 -- which would re-add everything,
    the exact failure this guards. Membership in the list is the right test.

    Returns 0 for a task the dataset has never seen, which is also the correct
    answer for "add a brand-new task file to an existing dataset".
    """
    meta = getattr(ds, "meta", None)
    episodes = getattr(meta, "episodes", None)
    if episodes is None:
        return 0
    n = 0
    for i in range(len(episodes)):
        tasks = episodes[i].get("tasks")
        if tasks is None:
            continue
        if isinstance(tasks, str):
            tasks = [tasks]
        if task in list(tasks):
            n += 1
    return n


def _check_resume_compatible(remote_features: dict, local_features: dict) -> None:
    """--resume appends into an existing Hub dataset, which already has ONE
    fixed features dict -- LeRobotDataset.resume() doesn't take a `features`
    argument to re-declare it, so a mismatch would only surface later, mid-
    conversion, as a validate_frame() crash inside add_frame(). Catch it here
    instead, before any conversion work starts."""
    for key in _CHECKED_FEATURE_KEYS:
        in_remote, in_local = key in remote_features, key in local_features
        if in_remote != in_local:
            raise SystemExit(
                f"--resume 대상 Hub 데이터셋과 스키마가 다릅니다: '{key}'가 "
                f"{'기존 데이터셋에는 있는데 지금 변환할 파일들에는 없음' if in_remote else '지금 변환할 파일들에는 있는데 기존 데이터셋에는 없음'}.\n"
                "다른 스키마로 이어붙이면 Hub 데이터셋이 망가집니다 -- --repo-id를 바꿔 별도 데이터셋으로 변환하세요."
            )
        if not in_remote:
            continue
        r, local = remote_features[key], local_features[key]
        if (
            r["dtype"] != local["dtype"]
            or tuple(r["shape"]) != tuple(local["shape"])
            or list(r.get("names") or []) != list(local.get("names") or [])
        ):
            raise SystemExit(
                f"--resume 대상 Hub 데이터셋과 '{key}' 스키마가 다릅니다:\n"
                f"  기존: dtype={r['dtype']} shape={tuple(r['shape'])} names={r.get('names')}\n"
                f"  지금: dtype={local['dtype']} shape={tuple(local['shape'])} names={local.get('names')}\n"
                "다른 스키마로 이어붙이면 Hub 데이터셋이 망가집니다 -- --repo-id를 바꿔 별도 데이터셋으로 변환하세요."
            )


def _stamp_schema_version(root: Path, source_versions: "list[str]") -> None:
    """변환본 meta/info.json 에 원본 HDF5 의 스키마 버전을 남긴다 (issue #41).

    LeRobot 자신의 ``codebase_version`` 은 LeRobot 포맷의 버전이지 우리
    필드 구성의 버전이 아니다. 변환본만 받은 사람이 "이 데이터가 어느
    세대 스키마에서 왔는가" 를 알 수 있어야 하므로 별도 키로 적는다.
    여러 세대의 원본이 섞여 들어올 수 있어 목록으로 남긴다.
    """
    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        return
    try:
        info = json.loads(info_path.read_text())
        info["schema_version"] = SCHEMA_VERSION
        if source_versions:
            info["source_schema_versions"] = source_versions
        info_path.write_text(json.dumps(info, indent=4))
    except Exception as e:  # noqa: BLE001 -- 스탬프 실패로 변환을 버리지 않는다
        print(f"[경고] meta/info.json 에 schema_version 을 남기지 못했습니다: {e}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hdf5_paths", type=Path, nargs="*",
                   help="<task>_demo.hdf5 파일들 (여러 개 가능). --push-only 일 때는 불필요")
    p.add_argument("--repo-id", required=True, help="예: knu-physical-ai/fr3-libero-teleop-lerobot")
    p.add_argument("--root", type=Path, required=True, help="로컬에 LeRobotDataset을 만들 경로")
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--only-success", action="store_true", help="success=True인 에피소드만 포함")
    p.add_argument(
        "--include-failed", action="store_true",
        help="scene 파일 전용: quality_status=failed 에피소드도 변환에 포함한다. "
             "scene 포맷은 에피소드 삭제가 없으므로(immutable) 이 필터가 큐레이션 "
             "관문이다 -- 기본은 success 만, bad_data/retake/deprecated 는 항상 제외.")
    p.add_argument("--image-writer-threads", type=int, default=4)
    p.add_argument(
        "--resume",
        action="store_true",
        help=(
            "처음부터 새로 만드는 대신 --repo-id의 기존 Hub 데이터셋에 이 파일들의 에피소드만 "
            "이어붙임 (기존 task는 재변환/재업로드하지 않음). 동시에 두 명이 같은 --repo-id에 "
            "--resume --push 하지 말 것 -- 이 파일 상단 docstring 3번 참고"
        ),
    )
    p.add_argument("--force-all", action="store_true",
                   help="--resume에서 이미 들어간 에피소드 건너뛰기를 끄고 파일 전체를 "
                        "다시 추가함. 중복이 생기고 되돌릴 수 없으니 거의 항상 쓰지 말 것")
    p.add_argument("--image-size", type=int, default=LEROBOT_IMAGE_SIZE,
                   help=f"LeRobot 쪽 이미지 한 변(px, 기본 {LEROBOT_IMAGE_SIZE}). .hdf5 가 더 크면 "
                        "정사각 크롭 후 이 크기로 줄인다 -- 원본은 그대로 둔 채 "
                        "학습용 사본만 가볍게 만들 때 쓴다. 원본보다 크게는 못 만든다.")
    p.add_argument("--push", action="store_true", help="변환 후 바로 Hugging Face Hub에 업로드")
    p.add_argument("--replace", action="store_true",
                   help="--push-only와 함께: 로컬에 없는 원격 파일을 지우며 통째로 교체. "
                        "큐레이션에서 에피소드를 삭제해 재빌드한 결과를 올릴 때 쓴다 "
                        "(그냥 push하면 지운 에피소드의 청크가 원격에 남는다)")
    p.add_argument("--push-only", action="store_true",
                   help="변환하지 않고 --root의 기존 LeRobot 데이터셋만 업로드 "
                        "(hdf5 인자는 무시됨). 이미 변환해둔 결과를 올릴 때 사용")
    p.add_argument("--private", action=argparse.BooleanOptionalAction, default=None, help="--push일 때만 적용")
    args = p.parse_args()

    if not args.push_only and not args.hdf5_paths:
        raise SystemExit("변환할 .hdf5 파일을 지정하세요 (업로드만 하려면 --push-only)")

    if args.push_only:
        # Upload what is already in --root, without re-encoding anything.
        # Without this, "I converted yesterday, now just upload it" has no
        # answer: a plain re-run re-converts every episode, and --resume
        # appends them a second time.
        # NOTE: no local `import LeRobotDataset` here. A function-scoped
        # import binds the name as a local for the WHOLE function, which shadows
        # the module-level import and makes the later resume()/create() calls
        # raise UnboundLocalError. It is already imported at module level.
        root = Path(args.root)
        if not (root / "meta" / "info.json").exists():
            raise SystemExit(f"{root}에 변환된 LeRobot 데이터셋이 없습니다 (meta/info.json 없음)")
        # LeRobotDataset(repo_id=..., root=...) 를 여기서 쓰면 안 된다. 그 생성자는
        # Hub 사본을 root로 먼저 동기화하면서, 방금 변환이 써놓은 meta/info.json과
        # meta/stats.json을 Hub의 더 오래된 것으로 덮어쓴다. 그 뒤 push_to_hub는
        # 새 data/·videos/ 청크는 올리지만 메타데이터는 낡은 채로 두므로, 실제
        # 담긴 것보다 적은 에피소드를 선언하는 데이터셋이 게시된다 -- 새 에피소드는
        # 파일로는 존재하나 어떤 리더에도 보이지 않는다.
        # (실제로 발생: 121개를 올려놓고 total_episodes=117로 게시됨.)
        # push_to_hub 자체는 repo_id / root / revision / meta.info 만 쓴다.
        broken = check_integrity(root)
        if broken:
            _fail_integrity(root, broken)
        fixed = repair_metadata(root)
        if fixed:
            # 낡은 메타데이터를 그대로 게시하면 새 에피소드가 어떤 리더에도
            # 보이지 않는다. 올리기 직전이 마지막으로 막을 수 있는 지점이다.
            print(f"[검증] {fixed}", flush=True)
        info_dict = json.loads((root / "meta" / "info.json").read_text())
        # 카드 생성기가 dataset_info.to_dict()를 부르므로 평범한 dict를 넘기면
        # AttributeError로 죽는다. 그것도 upload_folder가 끝난 *뒤*에 -- 즉 데이터는
        # 올라갔는데 카드도 없고, 더 중요하게는 그 다음의 create_tag가 실행되지
        # 않아 lerobot이 읽는 v3.0 태그가 옛 커밋에 멈춘다. 다음 resume이 그
        # 낡은 메타데이터를 씨앗으로 삼아 틀린 개수를 만들어낸다.
        info = DatasetInfo.from_dict(info_dict)
        ds = LeRobotDataset.__new__(LeRobotDataset)
        ds.repo_id = args.repo_id
        ds.root = root
        ds.revision = CODEBASE_VERSION
        ds.meta = SimpleNamespace(info=info)
        print(f"업로드만 진행: {info_dict['total_episodes']}개 에피소드, "
              f"{info_dict['total_frames']} 프레임 -> {args.repo_id}", flush=True)
        commit_msg = _hub_commit_message(root, args.repo_id, info_dict, bool(args.replace))
        print(f"Hub 커밋 메시지: {commit_msg}", flush=True)
        if args.replace:
            # 재빌드한 결과로 Hub을 통째로 교체한다. push_to_hub는 새/바뀐 파일만
            # 올리고 사라진 파일은 지우지 않으므로, 그것만으로는 지운 에피소드의
            # 청크가 원격에 남는다 -- 선언은 줄었는데 파일은 남은 상태.
            # delete_patterns 로 로컬에 없는 원격 파일을 함께 정리한다.
            from huggingface_hub import HfApi

            api = HfApi()
            api.create_repo(repo_id=args.repo_id, repo_type="dataset",
                            private=bool(args.private), exist_ok=True)
            print("교체 업로드: 로컬에 없는 원격 파일도 함께 정리합니다...", flush=True)
            api.upload_folder(
                repo_id=args.repo_id, folder_path=str(root), repo_type="dataset",
                ignore_patterns=["images/", ".cache/**"],
                delete_patterns=["data/**", "videos/**", "meta/**"],
                commit_message=commit_msg,
            )
            # upload_folder는 태그를 건드리지 않는다. lerobot이 읽는 건 태그이므로
            # 여기서 옮기지 않으면 교체한 내용이 보이지 않는다.
            from huggingface_hub.errors import RevisionNotFoundError

            try:
                api.delete_tag(args.repo_id, tag=CODEBASE_VERSION, repo_type="dataset")
            except RevisionNotFoundError:
                pass
            api.create_tag(args.repo_id, tag=CODEBASE_VERSION, repo_type="dataset")
            # upload_folder 는 카드도 건드리지 않는다 -- 기존 README 의 태그에
            # DATASET_TAGS 를 합쳐 유지한다 (카드가 없으면 push_to_hub 가 다음
            # 비교체 푸시에서 만든다).
            _merge_card_tags(args.repo_id)
        else:
            # lerobot 의 push_to_hub 는 commit_message 를 받지 않아 이력이 전부
            # 'Upload dataset' 으로 남는다. 같은 일(폴더 업로드 -> 카드 -> 태그)을
            # 직접 하되 메시지를 붙인다 -- 데이터셋이 자라는 것이 Hub 이력에서
            # 읽히게 (사용자 요청 2026-08-19).
            from huggingface_hub import HfApi
            from lerobot.datasets.utils import create_lerobot_dataset_card

            api = HfApi()
            api.create_repo(repo_id=args.repo_id, repo_type="dataset",
                            private=bool(args.private), exist_ok=True)
            api.upload_folder(
                repo_id=args.repo_id, folder_path=str(root), repo_type="dataset",
                ignore_patterns=["images/", ".cache/**"],
                commit_message=commit_msg,
            )
            card = create_lerobot_dataset_card(
                tags=DATASET_TAGS, dataset_info=info, repo_id=args.repo_id)
            card.push_to_hub(repo_id=args.repo_id, repo_type="dataset",
                             commit_message=f"card: {commit_msg}")
            from huggingface_hub.errors import RevisionNotFoundError

            try:
                api.delete_tag(args.repo_id, tag=CODEBASE_VERSION, repo_type="dataset")
            except RevisionNotFoundError:
                pass
            api.create_tag(args.repo_id, tag=CODEBASE_VERSION, repo_type="dataset")
        ok_tag = _verify_tag(args.repo_id)
        print(f"완료: https://huggingface.co/datasets/{args.repo_id}", flush=True)
        # 태그가 안 따라왔으면 성공이 아니다. lerobot은 태그를 읽으므로 올린
        # 내용이 보이지 않는다 -- 파이프라인이 이 단계를 실패로 처리해야 한다.
        return 0 if ok_tag else 1

    # .hdf5 는 견고한 원본, LeRobot 사본은 실사용 크기 -- 둘을 갈라두면 원본을
    # 다시 찍지 않고도 학습용 해상도를 바꿀 수 있다.
    image_size = args.image_size
    schema = _scan_schema(args.hdf5_paths, args.only_success,
                          include_failed=args.include_failed)
    features, state_parts, cmd_parts = _build_features(schema, image_size)
    print(
        f"감지된 스키마: action_space={schema['action_space']!r} "
        f"(gripper {'포함' if schema['has_gripper'] else '제외'}), "
        f"obs={sorted(schema['obs_keys'])}"
    )
    print(f"features: {list(features.keys())}")

    if args.resume:
        ds = LeRobotDataset.resume(
            repo_id=args.repo_id,
            root=args.root,
            image_writer_processes=0,
            image_writer_threads=args.image_writer_threads,
        )
        _check_resume_compatible(ds.meta.features, features)
        print(
            f"기존 데이터셋에 이어붙임: 현재 {ds.meta.total_episodes}개 에피소드, "
            f"{ds.meta.total_frames} 프레임, task {ds.meta.total_tasks}개"
        )
    else:
        ds = LeRobotDataset.create(
            repo_id=args.repo_id,
            fps=args.fps,
            root=args.root,
            robot_type="fr3_gello_real",
            features=features,
            use_videos=True,
            image_writer_processes=0,
            image_writer_threads=args.image_writer_threads,
        )

    has_agent = "observation.images.agent" in features
    has_wrist = "observation.images.wrist" in features

    def _convert_episode(path, name, grp, task) -> int:
        """에피소드 하나를 ds 에 추가한다. 두 포맷 공통 -- 에피소드 안쪽
        페이로드(obs/actions/attrs)가 동일하게 만들어져 있어서(scene 전환 시
        write_episode_payload 공유) legacy demo_N 과 scene episode_NNN 이
        같은 코드로 읽힌다. task 는 LeRobot 의 task 문자열: legacy 는 파일
        레벨 instruction, scene 은 에피소드 attrs 의 instruction (scene 이
        달라도 같은 문장이면 같은 task 로 합쳐진다 -- scene 구분은 uid
        사이드카로 복원)."""
        obs = grp["obs"]
        if has_agent:
            _check_image_shape(path, name, obs, OBS_AGENTVIEW_RGB, image_size)
        if has_wrist:
            _check_image_shape(path, name, obs, OBS_EYE_IN_HAND_RGB, image_size)
        # 이 에피소드가 수집될 때의 크롭 정렬. 조작자가 GUI 에서 맞춘
        # 프레이밍을 그대로 재현한다. 없는 옛 파일은 기본값 (wrist 는
        # 측정된 D405 좌측 이미저 오프셋).
        try:
            cp = json.loads(grp.attrs["crop_params"])
        except (KeyError, ValueError, TypeError):
            cp = {}
        ap = cp.get("agent", {}) if isinstance(cp, dict) else {}
        wp = cp.get("wrist", {}) if isinstance(cp, dict) else {}
        agent_crop = dict(zoom=ap.get("zoom", 1.0),
                          x_shift=ap.get("x", 0), y_shift=ap.get("y", 0))
        wrist_crop = dict(zoom=wp.get("zoom", 1.0),
                          x_shift=wp.get("x", EYE_IN_HAND_CROP_X_SHIFT),
                          y_shift=wp.get("y", 0))
        state_arrays = [obs[part][:] for part in state_parts]
        cmd_arrays = [obs[part][:] for part in cmd_parts]
        agent_rgb = obs[OBS_AGENTVIEW_RGB][:] if has_agent else None
        wrist_rgb = obs[OBS_EYE_IN_HAND_RGB][:] if has_wrist else None
        actions = grp["actions"][:]
        actions_ee = grp["actions_ee"][:] if schema["has_actions_ee"] else None
        n = actions.shape[0]
        for t in range(n):
            frame = {"action": actions[t].astype("float32"), "task": task}
            if state_arrays:
                frame["observation.state"] = np.concatenate(
                    [arr[t] for arr in state_arrays]
                ).astype("float32")
            if cmd_arrays:
                frame["observation.commanded_state"] = np.concatenate(
                    [arr[t] for arr in cmd_arrays]
                ).astype("float32")
            if actions_ee is not None:
                frame["action_ee"] = actions_ee[t].astype("float32")
            if has_agent:
                frame["observation.images.agent"] = _to_target(
                    agent_rgb[t], image_size, **agent_crop)
            if has_wrist:
                frame["observation.images.wrist"] = _to_target(
                    wrist_rgb[t], image_size, **wrist_crop)
            ds.add_frame(frame)
        ds.save_episode()
        return n

    n_episodes = 0
    n_skipped = 0
    n_already = 0
    # LeRobot episode_index -> 출처 매핑 (episode_uid 사이드카). resume 이면
    # 기존 매핑에 이어 쓴다 -- scene 파일의 스킵은 개수 산술이 아니라 이
    # uid 집합과의 대조로 정확하게 한다.
    # resume 가 아니면 빈 기록에서 시작한다 -- 이전 실행의 사이드카가 root 에
    # 남아 있으면 재빌드가 0..k-1 만 덮어써 k 이상 인덱스의 유령 레코드가
    # 살아남고, 다음 resume 의 uid 대조를 오염시킨다.
    uid_records = (_load_uid_sidecar(Path(args.root), args.repo_id, resume=True)
                   if args.resume else {})
    existing_uids = {e["episode_uid"] for e in uid_records.values()
                     if isinstance(e, dict) and e.get("episode_uid")}
    next_index = ds.meta.total_episodes if args.resume else 0
    source_versions: set = set()   # 원본들의 스키마 버전 (issue #41)
    for path in args.hdf5_paths:
        with h5py.File(path, "r") as f:
            if _is_scene_file(f):
                scene_id = str(f["metadata"].attrs["scene_id"])
                source_versions.add(normalize_schema_version(
                    f["metadata"].attrs.get("dataset_version", "")))
                # 큐레이션 편집 게이트 (2026-08-23): 삭제 후 renumber 는 uid 를
                # 재배정하므로, 편집이 있었던 scene 파일에 uid 집합 대조로
                # 이어붙이면 다른 에피소드를 "이미 올렸다"고 오판한다(새 궤적
                # 조용한 누락 / 지운 궤적 Hub 잔존). 트림도 uid·개수를 안 바꿔
                # 같은 구멍이다. 그래서 파일의 edit_count(삭제·트림마다 증가,
                # gello/scene/scene_format.mark_scene_edited)를 사이드카에 기록해 두고,
                # resume 때 달라져 있으면 여기서 멈춘다 -- 전체 재빌드만 허용.
                edit_count = int(f["metadata"].attrs.get("edit_count", 0))
                if args.resume and not args.force_all:
                    edit_base = uid_records.get("_scene_edits", {})
                    if edit_count != int(edit_base.get(scene_id, 0)):
                        raise SystemExit(
                            f"{path.name}: 큐레이션 편집(삭제/트림) 이력이 마지막 변환 "
                            f"이후 바뀌었습니다 (edit_count {edit_count}, 사이드카 기준 "
                            f"{edit_base.get(scene_id, 0)}). 편집 후에는 uid 가 재배정되어 "
                            "이어붙이기가 안전하지 않습니다 -- '전체 처리(재빌드)' 로 올리세요."
                        )
                uid_records.setdefault("_scene_edits", {})[scene_id] = edit_count
                conv = list(_scene_convertible(f, args.include_failed))
                total_eps = sum(1 for k in f.keys() if EPISODE_GROUP_RE.match(k))
                n_skipped += total_eps - len(conv)
                print(f"{path.name}: scene={scene_id}, "
                      f"변환 대상 {len(conv)}/{total_eps} episodes")
                for name, grp, instruction in conv:
                    uid = str(grp.attrs["episode_uid"])
                    if args.resume and not args.force_all and uid in existing_uids:
                        n_already += 1
                        continue
                    n = _convert_episode(path, name, grp, instruction)
                    uid_records[str(next_index)] = {
                        "episode_uid": uid,
                        "scene_id": scene_id,
                        "instruction_id": str(grp.attrs["instruction_id"]),
                        "instruction": instruction,
                        "quality_status": str(grp.attrs.get("quality_status", "")),
                        "source_file": path.name,
                        "source_episode": name,
                    }
                    next_index += 1
                    n_episodes += 1
                    print(f"  {name} ({n} frames, {uid}) converted")
                continue

            task = _language_instruction(f)
            data = f["data"]
            demo_names = sorted(data.keys(), key=lambda n: int(n.split("_")[1]))
            print(f"{path.name}: task={task!r}, {len(demo_names)} episodes")
            # --resume appends unconditionally, so handing it a task file that
            # has grown since the last run re-adds every episode already in the
            # dataset -- and LeRobot has no way to delete one afterwards. Both
            # sides append in demo order and never reorder, so the count of this
            # task's episodes already present is exactly how many to skip.
            if args.resume and not args.force_all:
                # 같은 문장(task)이 scene 파일에서도 올라갈 수 있다. legacy
                # 스킵 산술은 'task 개수 = 이 legacy 파일의 선두 N개' 전제라,
                # scene 출신 에피소드(사이드카에 instruction 기록)를 빼고
                # 세야 한다 -- 안 빼면 have 가 부풀어 SystemExit 로 오탐.
                scene_sourced = sum(
                    1 for e in uid_records.values()
                    if isinstance(e, dict) and e.get("episode_uid")
                    and e.get("instruction") == task)
                have = _task_episode_count(ds, task) - scene_sourced
                if have:
                    if have > len(demo_names):
                        raise SystemExit(
                            f"{path.name}: 데이터셋에 이 task 에피소드가 {have}개 있는데 "
                            f"HDF5에는 {len(demo_names)}개뿐입니다. 푸시 뒤에 HDF5에서 "
                            f"에피소드를 지우면 개수 대응이 깨져 이어붙이기가 안전하지 "
                            f"않습니다 -- 처음부터 다시 만드세요(--resume 없이)."
                        )
                    print(f"  이미 데이터셋에 {have}개 있음 -> 뒤쪽 "
                          f"{len(demo_names) - have}개만 추가합니다")
                    n_already += have
                    demo_names = demo_names[have:]
            for name in demo_names:
                grp = data[name]
                success = grp.attrs.get("success")
                if args.only_success and not _is_success(grp):
                    n_skipped += 1
                    continue
                n = _convert_episode(path, name, grp, task)
                uid_records[str(next_index)] = {
                    "source_file": path.name, "source_episode": name,
                }
                next_index += 1
                n_episodes += 1
                print(f"  {name} ({n} frames, success={success}) converted")

    ds.finalize()
    broken = check_integrity(Path(args.root))
    if broken:
        _fail_integrity(Path(args.root), broken)
    fixed = repair_metadata(Path(args.root))
    if fixed:
        print(f"[검증] {fixed}", flush=True)
    # push 전에 써야 push_to_hub(폴더 업로드)에 사이드카가 실린다.
    _write_uid_sidecar(Path(args.root), uid_records)
    _stamp_schema_version(Path(args.root), sorted(source_versions))
    print(f"\n완료: {n_episodes}개 에피소드 변환, {n_skipped}개 건너뜀 (필터)"
          + (f", {n_already}개는 이미 데이터셋에 있어 제외" if n_already else "")
          + f" -> {args.root}")

    if args.push:
        print("Hugging Face Hub에 업로드 중...")
        ds.push_to_hub(private=args.private, tags=DATASET_TAGS)
        print(f"완료: https://huggingface.co/datasets/{args.repo_id}")


if __name__ == "__main__":
    main()
