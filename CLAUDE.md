# gello_software (KNU scene collection)

## Python environment — read this first

There is NO repo-local venv and bare `python` is not on PATH.

- **Everything** (GUI, library, tests, scripts): `/home/franka/lerobot-venv/bin/python`
  — also reachable as `.venv/bin/python` (a machine-local symlink, gitignored).
- **Robot node only** (`scripts/launch/launch_nodes.py`): `/home/franka/pylibfranka-venv/bin/python`.
  Never use it for anything else.

## Layout = dependency direction (user's rule, 2026-08-31)

Dependencies must be readable from the tree without opening files.

Arrows point down only. `tests/gui/test_layer_rules.py` enforces this.

- `gello/config/` — **Shared leaf: imports nothing.** Constants both the robot
  and the GUI must agree on, station config, episode-quality vocabulary.
- `gello/core/` — abstractions others depend on (robot/agent/camera/env).
  Stubs (PrintRobot, DummyCamera) live here too; no vendor SDK may.
- `gello/robots|agents|cameras|hw|sim/` — concrete implementations. Nothing in
  `data/`, `scene/` or `gui/` may import these: a data logger must not know
  whether the arm is an FR3 or a simulator.
- `gello/data/` — dataset schema/formats. `gello/scene/` — scene format, rules,
  props, grammar, plans, Hub sync. `scene` may use `data`, never the reverse.
- `gello/collect/` — the collection engine (drives leader+robot+cameras).
  Emits Qt signals but is not a widget; it is allowed to know concrete hardware.
- `gello/gui/` — Qt only (widgets, dialogs, workers). `gello/comm/` — zmq nodes;
  the process boundary is itself the abstraction, so `gui` may use it.
- `apps/workspace/features/<기능>/`   한 기능의 도메인·화면·대화상자
- `apps/workspace/shell/`             창 골격과 앱 수준 설정 페이지
- `apps/workspace/shared/`            여러 기능이 함께 쓰는 위젯·헬퍼
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
