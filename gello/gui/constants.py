"""Shared GUI constants.

Split out of gello/gui/gui_widgets.py (emptied in the 5-3 refactor) so that
widgets and workspace code can import small, single-purpose modules without
dragging in unrelated Qt classes.
"""

from __future__ import annotations

import os

# Must run before numpy/cv2/h5py/torch(via lerobot) are imported below --
# these each read the env var once at their own C-level init and spin up a
# BLAS/parallel-executor thread pool sized to the CPU core count (measured:
# 39 extra OS threads on this 20-core machine, for a GUI that does no heavy
# matrix math or bulk image processing at all). Setting these first keeps
# that pool at 1 with zero measured functional difference for this script's
# actual workload (light resize/color-convert calls). Don't copy this into
# scripts/convert/convert_libero_to_lerobot.py -- that one genuinely benefits from
# parallel video encoding.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import cv2  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gello.config.station import load_station  # noqa: E402

# The OMP/OPENBLAS/MKL env vars above only cap numpy's BLAS backend --
# OpenCV's own parallel_for_ executor is a separate thread pool controlled
# only by this runtime call, not an env var.
cv2.setNumThreads(1)

# launch_nodes.py needs pylibfranka, which only exists in this separate venv
# (this GUI itself runs in lerobot-venv -- see module docstring). Spawned as
# a subprocess rather than imported, same as running it by hand in a second
# terminal. 경로는 스테이션 설정의 node.python.
PYLIBFRANKA_PYTHON = load_station().node.python_path
LAUNCH_NODES_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "launch" / "launch_nodes.py")
RUNME_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "runme.sh")
REPACK_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "convert" / "repack_hdf5.py")

# Repo IDs and output paths get retyped every session otherwise, and a typo in
# a repo ID silently creates a *new* Hub dataset rather than failing.
RECENTS_PATH = Path.home() / "libero_gui_logs" / "recent_inputs.json"
# Episodes are recorded at 20 Hz, so playing them back at 20 fps shows the
# motion at the speed it actually happened -- which is the point of reviewing.
PLAYBACK_FPS = 20

# 미개발 표시. collect_workspace 가 이 값을 import 하므로 여기에 두고
# 위에서 가져다 쓴다 -- 반대로 두면 순환 import 가 된다.
TODO_MARK = "미개발"

JOINT_LABELS = [f"J{i}" for i in range(1, 8)] + ["grip"]

STATE_LABELS_KO = {
    "idle": "대기",
    "connecting": "연결 중...",
    "homing": "홈 복귀 중",
    "reset_wait": "환경 리셋 대기",
    "gate": "자세 맞추는 중",
    "approach": "접근 중",
    "recording": "기록 중",
}
