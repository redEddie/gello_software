#!/bin/bash
# Launches the LIBERO data-collection GUI (lerobot-venv). The GUI itself has
# buttons to start/restart the launch_nodes.py robot node (pylibfranka-venv),
# so this is the only script needed for a desktop shortcut.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
source /home/franka/lerobot-venv/bin/activate
exec python apps/collect_workspace.py
