# Dataset schema versioning (`knu-X.Y.Z`)

Our recorded data has its own format version, independent of LeRobot's
`codebase_version`. GitHub issue #41.

The canonical definition in code is `gello/data/dataset_schema.py`
(`SCHEMA_VERSION`, `SCHEMA_FIELDS`, `SCHEMA_VERSION_ALIASES`). If this document
and that module ever disagree, the module is right and this document is a bug.

## Version rule (SemVer)

`knu-MAJOR.MINOR.PATCH`

| Bump | Means | Example |
|---|---|---|
| MINOR | **Fields added only.** Nothing existing changes. | joint torques → `knu-1.1.0` |
| MAJOR | A field's meaning, unit, or name changes, or a field is removed. | angles in degrees instead of radians |
| PATCH | This document changed; the data did not. | wording fix |

Because MINOR only ever *adds*, compatibility holds in both directions inside
one MAJOR:

- **Backward** — a `knu-1.1.0` reader opens `knu-1.0.0` data; the fields added
  in 1.1.0 are simply absent.
- **Forward** — a `knu-1.0.0` reader opens `knu-1.1.0` data; every field it
  knows is still there, and it ignores the new ones.

So a reader must accept every MINOR within its own MAJOR
(`schema_is_readable()`), and refuse a different MAJOR.

## Where the version is written

| Format | Location |
|---|---|
| HDF5 (source of truth) | `metadata.attrs["dataset_version"]`, mirrored to `metadata.attrs["schema_version"]` |
| LeRobot conversion | `meta/info.json` → `schema_version`, plus `source_schema_versions` listing the versions of the HDF5 files that fed it |

`dataset_version` is the original attribute name and stays for
back-compatibility; `schema_version` is the name shared with the LeRobot side so
both formats can be queried the same way. When both exist they must agree — the
validator fails the file otherwise.

### Files recorded before versioning

Everything collected up to 2026-08-31 (`scene_000` … `scene_014`, 1100
episodes) was written with the older label `dataset_version = "scene-v1"`.
Those files were surveyed field by field and are **identical** to what is
frozen below, so they were stamped in place with
`scripts/convert/stamp_schema_version.py`: both version attributes now read
`knu-1.0.0`. Only those two attributes changed — no episode was touched, no
data was rewritten, and `edit_count` was deliberately left alone (bumping it
would force a full LeRobot rebuild, and no episode changed).

The alias table stays anyway, permanently:

```
"scene-v1" -> "knu-1.0.0"
""         -> "knu-1.0.0"     # very early files with no version attribute
```

Copies that left this machine before the stamping — the Hub upload, backups,
`old_data/` — still carry the old label, and readers must keep resolving it.

## `knu-1.0.0` — frozen 2026-08-31

One HDF5 file per scene: `scene_NNN.hdf5`.

```
scene_NNN.hdf5
├── metadata                       (group, attrs only + reference image)
└── episode_NNN                    (one group per episode)
    ├── actions, dones, rewards
    └── obs/…
```

**`metadata` attrs** — `scene_id`, `objects` (JSON list of prop instance IDs
from `configs/scenes/props.yaml`), `layout` (JSON), `description`, `station`,
`dataset_version`, `created`, `next_episode_idx`.

**`episode_NNN` datasets** — `actions` (T, 8), `dones` (T,), `rewards` (T,).

**`episode_NNN/obs` datasets**

| Field | Shape | Note |
|---|---|---|
| `agentview_rgb` | (T, H, W, 3) uint8 | scene camera |
| `eye_in_hand_rgb` | (T, H, W, 3) uint8 | wrist camera |
| `joint_states` | (T, 7) float32 | measured |
| `commanded_joint_states` | (T, 7) float32 | what the leader commanded |
| `gripper_states` | (T, 1) float32 | measured |
| `commanded_gripper_states` | (T, 1) float32 | commanded |
| `ee_states` | (T, 6) float32 | |
| `ee_pos` | (T, 3) float32 | |
| `ee_ori` | (T, 3) float32 | |

**`episode_NNN` attrs** — `instruction`, `instruction_id`, `episode_id`,
`episode_uid`, `num_samples`, `success`, `quality_status`, `scene_id`,
`slot_episode_idx`, `collector`, `station`, `timestamp`, `action_space`,
`action_column_names`, `gripper_action_convention`, `crop_params`.

Images are written with `lzf` during collection (fast, so the background save
never stalls the operator) and re-compressed to `gzip` by
`scripts/convert/repack_hdf5.py` afterwards. **Compression is not part of the
schema** — the same version can be stored either way.

### Not in 1.0.0

- **Depth.** `DatasetSchemaConfig` has `save_agentview_depth` /
  `save_eye_in_hand_depth`, but both are force-disabled (`_FIXED`): the camera
  driver in use (lerobot 0.5.0 `RealSenseCamera`) has no `read_latest_depth`.
  No recorded file contains depth. When the driver supports it, depth is a
  field **addition** → `knu-1.1.0`.
- **Joint torques / external forces** (`tau_J`, `tau_ext_hat_filtered`,
  `O_F_ext_hat_K`) — issue #16. Added in `knu-1.1.0` below.

## `knu-1.1.0` — frozen 2026-09-05

Adds three observation datasets. Everything else is identical to `knu-1.0.0`.

```
episode_NNN/obs/joint_torques       (T, 7) float32   tau_J
episode_NNN/obs/ext_joint_torques   (T, 7) float32   tau_ext_hat_filtered
episode_NNN/obs/ee_wrench           (T, 6) float32   O_F_ext_hat_K
```

They come straight from the FR3 robot state. A firmware without those fields
makes the node log a warning and skip them — a file written on such a rig has
`knu-1.0.0`'s field set and must be stamped `knu-1.0.0`, not `1.1.0`.

**Why the bump was needed, in hindsight.** The torque fields started being
written with `scene_015` (2026-09-04) while the version string stayed
`knu-1.0.0`. That produced two files both claiming `knu-1.0.0` with different
observation sets — exactly what versioning exists to prevent. `scene_015` was
stamped `knu-1.1.0` in place on 2026-09-05 (two attributes; no episode
touched). `scene_000` … `scene_014` genuinely lack the fields and stay
`knu-1.0.0`.

Nothing downstream of the HDF5 changes: the LeRobot converter never consumed
these keys (`_CONSUMED_OBS_KEYS`), so the published `-lerobot` dataset is
unaffected.

## How to bump a MINOR

1. Add the fields to the writer.
2. Add a new key to `SCHEMA_FIELDS` in `gello/data/dataset_schema.py` — copy the
   previous version's lists and add the new names. **Do not edit the older
   entry**: old files must keep being checked by the rules of their own version.
3. Set `SCHEMA_VERSION` to the new version.
4. Add a section to this document describing what was added and why.
5. Run `python scripts/check/check_scene_file.py <files>` over both an old and a
   new file — both must pass, each against its own version's field list.

Old files are never rewritten. A dataset directory legitimately holds several
versions at once; the LeRobot conversion records all of them in
`source_schema_versions`.
