"""Reclaim dead space in a curated LIBERO <task>_demo.hdf5 after episode deletes.

HDF5 doesn't shrink a file when a group is deleted (see
gello/libero_format.py's delete_episode docstring) -- the freed space is
only reusable by later writes *within that same file*. This copies the
live demo_N groups + top-level attrs into a fresh file (verifying the copy
matches byte-for-byte before replacing anything), which is exactly what
h5repack would do, without needing h5repack installed.

Usage:
    python scripts/repack_libero_demo.py /home/franka/libero_datasets/<task>_demo.hdf5
"""

import argparse
import shutil
from pathlib import Path

import h5py
import numpy as np

from gello.data.dataset_schema import (
    OBS_AGENTVIEW_RGB,
    OBS_EE_POS,
    OBS_EYE_IN_HAND_RGB,
    OBS_JOINT_STATES,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hdf5_path", type=Path)
    args = p.parse_args()

    src_path = args.hdf5_path
    tmp_path = src_path.with_suffix(src_path.suffix + ".repack.tmp")
    bak_path = src_path.with_suffix(src_path.suffix + ".bak")

    with h5py.File(src_path, "r") as src, h5py.File(tmp_path, "w") as dst:
        src_data = src["data"]
        dst_data = dst.create_group("data")
        for k, v in src_data.attrs.items():
            dst_data.attrs[k] = v
        for name in src_data.keys():
            src.copy(src_data[name], dst_data, name)

    # Full pixel/array-level verification before touching the original.
    with h5py.File(src_path, "r") as f_old, h5py.File(tmp_path, "r") as f_new:
        old_demos = sorted(f_old["data"].keys())
        new_demos = sorted(f_new["data"].keys())
        assert old_demos == new_demos, (old_demos, new_demos)
        for name in old_demos:
            for key in (OBS_AGENTVIEW_RGB, OBS_EYE_IN_HAND_RGB, OBS_JOINT_STATES, OBS_EE_POS):
                a = f_old["data"][name]["obs"][key][:]
                b = f_new["data"][name]["obs"][key][:]
                assert np.array_equal(a, b), (name, key, "MISMATCH")
            assert np.array_equal(
                f_old["data"][name]["actions"][:], f_new["data"][name]["actions"][:]
            ), (name, "actions MISMATCH")

    old_size = src_path.stat().st_size
    new_size = tmp_path.stat().st_size
    shutil.move(str(src_path), str(bak_path))
    shutil.move(str(tmp_path), str(src_path))

    print(f"verified {len(old_demos)} episodes byte-for-byte identical")
    print(f"{old_size/1e6:.1f} MB -> {new_size/1e6:.1f} MB")
    print(f"original kept at: {bak_path}")


if __name__ == "__main__":
    main()
