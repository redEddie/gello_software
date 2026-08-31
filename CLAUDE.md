# gello_software (KNU scene collection)

## Python environment — read this first

There is NO repo-local venv and bare `python` is not on PATH.

- **Everything** (GUI, library, tests, scripts): `/home/franka/lerobot-venv/bin/python`
  — also reachable as `.venv/bin/python` (a machine-local symlink, gitignored).
- **Robot node only** (`scripts/launch/launch_nodes.py`): `/home/franka/pylibfranka-venv/bin/python`.
  Never use it for anything else.

## Layout = dependency direction (user's rule, 2026-08-31)

Dependencies must be readable from the tree without opening files.

- `gello/core/` — abstractions others depend on (robot/agent/camera/env/station).
- `gello/robots|agents|cameras/` — concrete implementations of core.
- `gello/hw/` — device drivers (dynamixel, factr). `gello/sim/` — simulation only.
- `gello/data/` — dataset schema/formats/sync. `gello/scene/` — scene format,
  rules, props inventory, instruction grammar, plans.
- `gello/gui/` — Qt components of the collector. `gello/comm/` — zmq/camera nodes.
- `apps/` — operational entry points (collect_workspace GUI, fr3_policy_client).
- `scripts/launch|check|calib|convert|analyze/` — purpose-split tools.
- `experiments/` — research only; nothing in gello/ may import from it.
- `configs/stations|robots|scenes|collection/` — config data.

Keep this invariant when adding files; a module's folder must announce its role.

## Verification

- Grammar/rules: `python -m gello.scene.instruction_grammar`, `python -m gello.scene.scene_rules`
- Full GUI acceptance suite (offscreen, no hardware):
  `bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python`
- The collector is launched by desktop icon via `run_scene_collector.sh`
  (auto `git pull --ff-only`, then `apps/collect_workspace.py`) — keep the
  working tree clean/committed or the pull is skipped.

## Conventions

- New user-facing GUI strings: Korean call site + English entry in
  `gello/gui/i18n.py` `_EN` (byte-exact key). New comments/docs/issues in
  English (issue #42, for international students).
- Prop/scene decisions are recorded on GitHub issues (e.g. #36); props.yaml
  color tokens must be lowercase (the grammar parser lowercases phrases).
- This checkout shares its git repo with the `gello_software-deploy` worktree
  (branch `feat/waypoint-client`); prefer cherry-picks over merges across the
  2026-08-31 layout change.
