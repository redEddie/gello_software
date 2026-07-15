# Patches required by the FR3 backend

`gello/robots/franka_fr3.py` does **not** work against a stock `pylibfranka`
build. Apply `pylibfranka-gil-release.diff` and rebuild before using
`--robot=fr3`, or the robot aborts within a second of connecting.

## pylibfranka-gil-release.diff

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
`ActiveControlBase::writeOnce` overloads.

`ActiveControlBase::readOnce` is a **lambda** and gets an inner
`py::gil_scoped_release` scoped around only the blocking read instead.
`call_guard` there would hold the guard across the whole lambda body, including
the `py::make_tuple` that builds the return value — touching Python objects
without the GIL, which segfaults.

Constructors are deliberately left unguarded: they run once, before any thread
exists, so there is nothing for them to starve.

**Applying**

```bash
cd ~/libfranka-0.17.0                 # the tree pylibfranka was built from
git apply /path/to/gello_software/patches/pylibfranka-gil-release.diff

source /opt/ros/humble/setup.bash     # CMake needs pinocchio from ROS
source ~/pylibfranka-venv/bin/activate
pip install .
```

**Verifying**

`pylibfranka.__version__` is hardcoded in `__init__.py` and disagrees with
`pyproject.toml`, so it does not change when you rebuild — check the `.so` hash
instead, then confirm the GIL is actually released: call `Gripper.read_once()`
on a worker thread while another thread counts in a `while` loop. Pre-patch the
counter freezes; post-patch it runs at full speed.
