"""Hdf5TreeDialog 스모크 — selftest 로 만든 scene 파일을 트리로 탐색."""
import subprocess
import sys
import tempfile
from pathlib import Path

WT = str(Path(__file__).resolve().parents[2])   # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/experiments")
sys.argv = ["t"]
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import collect_workspace as cw  # noqa: E402

d = tempfile.mkdtemp(prefix="h5view_")
subprocess.run([sys.executable, WT + "/scripts/check_scene_file.py",
                "--selftest", "--keep", d], check=True, capture_output=True)
p = Path(d) / "scene_000.hdf5"
assert p.exists()

dlg = cw.Hdf5TreeDialog(None, p)
root = dlg.tree.invisibleRootItem()
names = [root.child(i).text(0) for i in range(root.childCount())]
assert "metadata" in names and "episode_000" in names, names


def find(item, name):
    for i in range(item.childCount()):
        c = item.child(i)
        if c.text(0) == name:
            return c
    return None


meta = find(root, "metadata")
dlg.tree.setCurrentItem(meta)
assert "scene_id" in dlg.detail.toPlainText()      # attrs 표시
ep = find(root, "episode_000")
img = find(find(ep, "obs"), "agentview_rgb")
dlg.tree.setCurrentItem(img)
t = dlg.detail.toPlainText()
assert "shape" in t and "미리보기" in t
assert dlg.preview.pixmap() is not None            # 이미지 미리보기
dlg.tree.setCurrentItem(find(ep, "actions"))
assert "shape" in dlg.detail.toPlainText()
print("통과: 트리 구성 + attrs + 이미지 미리보기 + 데이터셋 정보")
import os  # noqa: E402

os._exit(0)
