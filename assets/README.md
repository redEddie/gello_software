# assets/

## libero_init_layouts.zip — LIBERO initial-scene reference images

These are the reference photos the **Layout** tab shows next to the live camera,
so you can match the real tabletop to a LIBERO initial scene before recording.

### You normally do not have to do anything

The collector unpacks the zip by itself the first time it needs it
(`_ensure_layout_refs()` in `apps/collect_workspace.py`). Open the **Layout**
tab and the images are there. If the tab says
`assets/libero_init_layouts.zip 이 없습니다`, the zip itself is missing — see
*Getting the zip* below.

### Unpacking by hand

From the repository root:

```bash
unzip assets/libero_init_layouts.zip -d assets/libero_init_layouts/
```

The `-d` target matters. The suite folders must land **directly** under
`libero_init_layouts/`, not one level deeper:

```
assets/
  libero_init_layouts.zip        <- tracked in git (3.9 MB)
  libero_init_layouts/           <- NOT tracked, created by unpacking
    .zip_stamp                   <- written by the GUI, see below
    libero_10/
      agent/KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it.png
      wrist/KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it.png
    libero_goal/
    libero_object/
    libero_spatial/
```

An entry only appears in the Layout tab when the **same filename exists in both
`agent/` and `wrist/`** — the tab always shows the pair. A png that exists in
only one of the two is silently skipped, so a half-populated suite looks like a
short list rather than an error.

### Why the extracted folder is not in git

`.gitignore` excludes both `*.png` and `assets/libero_init_layouts/`. Only the
zip is committed: the 80 pngs (40 agent/wrist pairs) would otherwise be 80
separate blobs to review and re-download on every clone. **Never `git add -f`
the extracted images.**

### Updating the references

Replace `libero_init_layouts.zip` and commit only the zip. The GUI stores the
zip's `size:mtime` in `.zip_stamp` and re-extracts whenever that stamp stops
matching — no manual step, and no stale images left behind (the old folder is
removed before extracting).

To force a re-extract without touching the zip, delete the stamp:

```bash
rm assets/libero_init_layouts/.zip_stamp
```

### Getting the zip

It is committed to this repository, so a normal clone already has it. If it is
missing, you are most likely in a worktree or a shallow/partial clone — run
`git checkout assets/libero_init_layouts.zip` from the repository root.
