# Patches required by the FR3 backend

`gello/robots/franka_fr3.py` does **not** work against a stock `pylibfranka`
build. Apply the GIL-release patch matching your libfranka tree and rebuild
before using `--robot=fr3`, or the robot aborts within a second of connecting.

| patch | libfranka tree | robot system image |
|---|---|---|
| `pylibfranka-0.21-gil-release.diff` | `~/libfranka-0.21.2` (current) | >= 5.9.0 (ours: 5.10.0) |
| `pylibfranka-gil-release.diff` | `~/libfranka-0.17.0` (historical) | >= 5.7.2, < 5.9.0 |

The official PyPI wheels (checked up to 0.21.2) still contain zero
`gil_scoped_release`, so a source build with this patch remains mandatory.

## pylibfranka GIL release (both diffs)

**Symptom without it**

```
[FR3] CONTROL LOOP ABORTED: libfranka: Move command aborted: motion aborted by
reflex! ["communication_constraints_violation"]
```

**Cause**

The stock bindings contain zero `gil_scoped_release`, so every blocking
libfranka call holds the Python GIL for its whole duration. `Gripper.read_once()`
blocks ~60 ms per call and the gripper thread calls it every 50 ms, which freezes
the interpreter — and therefore the 1 kHz control thread — more than half the
time. `Gripper.move()` is worse: it blocks until the fingers stop moving (~1 s).
The control thread cannot make its 1 ms deadline, so the robot stops receiving
commands and its reflex aborts the motion.

Note this is *not* a real-time-configuration problem. Nothing in libfranka or
pylibfranka calls `mlockall()`, so the `memlock` limit is irrelevant here, and
`rtprio` is already sufficient — libfranka's `kEnforce` sets SCHED_FIFO itself
(`src/control_tools.cpp`). Chasing the RT setup is a dead end; the GIL is the
whole story.

**What the patch does**

Adds `py::call_guard<py::gil_scoped_release>()` to the blocking bindings:
`Gripper::{homing,grasp,read_once,stop,move}`, `Robot::read_once`, and the five
`ActiveControlBase::writeOnce` overloads. (In 0.21.x the gripper bindings live
in `pylibfranka/src/gripper.cpp`; the rest is unchanged in spirit.)

`ActiveControlBase::readOnce` is a **lambda** and gets an inner
`py::gil_scoped_release` scoped around only the blocking read instead.
`call_guard` there would hold the guard across the whole lambda body, including
the `py::make_tuple` that builds the return value — touching Python objects
without the GIL, which segfaults.

Constructors are deliberately left unguarded: they run once, before any thread
exists, so there is nothing for them to starve. The async-control bindings added
in 0.20 (`async_control.cpp`) are also left unguarded — the FR3 backend does not
use them; guard them first if that ever changes.

**Applying**

```bash
cd ~/libfranka-0.21.2                 # tree cloned with --recurse-submodules
git apply /path/to/gello_software/patches/pylibfranka-0.21-gil-release.diff

source /opt/ros/humble/setup.bash     # CMake needs pinocchio from ROS
source ~/pylibfranka-venv/bin/activate
pip install .
```

**Verifying**

Check `pylibfranka.__version__` (0.21.x generates `_version.py` from
CMakeLists at build time), then confirm the GIL is actually released: call
`Gripper.read_once()` on a worker thread while another thread counts in a
`while` loop. Pre-patch the counter freezes; post-patch it runs at full speed.
