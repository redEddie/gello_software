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
- **Joint torques / external forces / contact** — issue #16. Added in
  `knu-1.1.0` below.

## `knu-1.1.0` — RETIRED 2026-09-06, do not use

Defined seven observation datasets. **Three of them are zero in every frame of
every file**, because they were added by checking that the `RobotState` field
existed without ever checking that a value arrives:

```
desired_joint_torques   always 0   tau_J_d is only filled by an external torque
                                   controller; we drive the arm with
                                   start_joint_position_control
joint_contact           always 0   set_collision_behavior is called with lower ==
cartesian_contact       always 0   upper, so contact is reported only when the
                                   reflex fires — and a reflex aborts the episode,
                                   so no saved frame can have the flag set
```

A zero in those three columns means **"not measured"**, not "no contact". Do not
threshold them, do not train on them.

The definition stays in `SCHEMA_FIELDS` so files already stamped `knu-1.1.0`
still validate, but the version is removed from the launcher's picker
(`SCHEMA_PICKABLE`) so nothing new can be recorded with it. `scene_015`, the
only file that ever carried it, is being re-collected as `knu-1.1.1`.

## `knu-1.1.1` — frozen 2026-09-06

`knu-1.0.0` plus four force/torque datasets. Everything else is identical.

```
episode_NNN/obs/joint_torques      (T, 7) float32   tau_J
episode_NNN/obs/ext_joint_torques  (T, 7) float32   tau_ext_hat_filtered
episode_NNN/obs/ee_wrench          (T, 6) float32   O_F_ext_hat_K  (base frame O)
episode_NNN/obs/ee_wrench_ee       (T, 6) float32   K_F_ext_hat_K  (stiffness/EE frame K)
```

The single source for this list is `FT_OBS_FIELDS` in
`gello/data/dataset_schema.py`; `FT_STATE_ATTRS` in `gello/robots/franka_fr3.py`
maps each name to its `RobotState` field.

Removing fields is outside the "MINOR adds only" rule. It is a PATCH here
because no `knu-1.1.0` file survives into the final dataset, so nothing loses a
column it used to have.

### Candidates that were rejected, and why

All measured on the robot rather than reasoned about — `FT_OBS_REJECTED` keeps
the same list next to the code:

| field | measurement | verdict |
|---|---|---|
| `tau_J_d` | 0 in all 2090 frames of `scene_015` | never filled under position control |
| `dtau_J` | \|max\| 242 N·m/s **stationary**, 255 N·m/s while shaken by hand | noise-dominated; cannot even separate moving from still |
| `joint_contact`, `cartesian_contact` | 0 in all frames | unreachable (see above); and thresholding `ext_joint_torques` offline gives the same thing without baking a threshold into the data |
| gripper `is_grasped` | — | `gripper_states` width already separates it cleanly (0.44–0.46 grasped vs 0.75–0.77 empty) |

### How accurate these values are

Measured 2026-09-06 by grasping a 0.5 kg calibration weight, with the payload
declared in Desk (`m_ee = 0.85 kg` = Franka Hand 0.73 + camera/bracket 0.118):

```
                                         carry-phase Fz, joints below 0.15 rad/s
empty hand                                        -1.671 +- 0.345 N
holding 0.5 kg                                    +3.257 +- 0.410 N
                                       difference  4.928 N  ->  502 g
```

Two things follow, and both matter to anyone using these columns:

1. **The estimate is only trustworthy at low speed.** Restricting to
   near-stationary frames is what makes 500 g read as 502 g; using every frame
   gives 437 g. With the payload *undeclared* the same weight reads 613 g — the
   unmodelled 118 g lands directly in the external-force estimate.
2. **Per-frame values are noisy.** Even among near-stationary frames the
   standard deviation is 2.6–3.4 N, comparable to the 4.9 N signal being
   measured. A single frame cannot tell you whether a 500 g object is held;
   averaging or filtering can. Treat these columns as a signal to integrate,
   not as a per-frame contact sensor. Uncertainty on a mass estimate from a few
   hundred frames is roughly ±50 g, and that is optimistic because consecutive
   20 Hz frames are correlated.

The payload declared in Desk at recording time is **not** stored in the file
yet. Until it is, a file's absolute force values are only interpretable if the
Desk configuration is known to have been correct.

## `knu-1.2.0` — frozen 2026-09-06

Same observation datasets as `knu-1.1.1`. Adds two **metadata** attributes
recording the robot's payload model at the time of recording:

```
metadata/payload_mass   float    kg     m_total
metadata/payload_com    string   m      F_x_Ctotal as JSON [x, y, z], flange frame
```

Frame data does not grow by a single byte — these are written once per file.

**Why this is not optional.** An undeclared mass lands directly in the external
force estimate. Measured 2026-09-06 on the same 0.5 kg weight: with the payload
declared (`m_ee = 0.85 kg`) it read 502 g; with 120 g removed from the
declaration it read 613 g. Without this attribute there is no way, later, to
tell which configuration a file was recorded under — you can only hope the Desk
setting was right at the time. That is exactly the position we were in when
`scene_015` had to be investigated by hand.

The worker reads it from the robot once at session start
(`CollectionWorker._stamp_payload`) via a `payload` request to the robot node.
A robot that cannot report it (simulator, `PrintRobot`) leaves the attributes
absent rather than writing zero — a zero would read as "no payload", which is
worse than missing. Such a file is then checked against `knu-1.1.1`'s rules.

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
