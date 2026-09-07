"""기록 닥터의 동작 -- 검사하고, 고른 scene 을 펴고, 고친다.

**수집 중에는 검사하지 않는다.** 검사는 데이터셋의 scene 파일을 전부 열고,
그중 하나는 지금 수집자가 쓰고 있는 파일이다. 조작자의 손이 리더암에 있을
때 파일을 여는 것도, 그 결과를 읽으라고 화면을 채우는 것도 둘 다 나쁘다.

고치는 두 가지의 값이 다르다는 것을 화면이 **누르기 전에** 말해야 한다.
기록 정정은 공짜고(metadata attr 둘), 문장 정정은 Hub 재빌드를 부른다.
"""
from pathlib import Path

import h5py
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QMessageBox, QTreeWidgetItem

from apps.workspace.features.doctor.page import fill_scene_rows
from apps.workspace.features.doctor.record_tab import PHOTO_W
from apps.workspace.features.doctor.sentence_builder import SentenceDialog
from apps.workspace.features.doctor.swap_dialog import SwapDialog
from apps.workspace.shared.tabs import show_center_tab
from gello.gui.i18n import tr
from gello.gui.widgets.video_view import np_to_pixmap
from gello.scene.dataset_meta import plan_path as dataset_plan_path
from gello.scene.instruction_grammar import (
    enumerate_instructions,
    skill_of,
)
from gello.scene.props import props_by_id
from gello.scene.scene_format import (
    describe_scene,
    iter_scene_files,
    read_reference_image,
    read_scene_metadata,
    scene_filename,
)
from gello.scene.scene_repair import (
    apply_object_fix,
    audit_scene,
    episode_counts,
    plan_task_texts,
    remove_task_from_plan,
    rewrite_task_text,
    suggest_object_fix,
    swap_task_texts,
)


class DoctorOps:
    def __init__(self, win) -> None:
        self.win = win
        self._scene_id = ""
        self._suggestion = None
        self._task = None          # (instruction_id, 문장)

    # ------------------------------------------------------------- 검사
    def _root(self) -> Path:
        return Path(self.win.root_edit.text().strip() or ".")

    def _path(self, scene_id: str) -> Path:
        return self._root() / scene_filename(scene_id)

    def rescan(self) -> None:
        win = self.win
        if win.worker is not None:
            win.doctor_dataset_label.setText(tr("수집 중"))
            win.doctor_hint.setText(tr(
                "수집 중에는 검사하지 않습니다 — 세션을 끝낸 뒤 "
                "[다시 검사] 를 누르세요."))
            win.doctor_tree.clear()
            return

        root = self._root()
        try:
            files = iter_scene_files(root)
        except Exception as e:  # noqa: BLE001
            win.doctor_dataset_label.setText(tr("경로를 읽을 수 없습니다"))
            win.doctor_hint.setText(str(e))
            win.doctor_tree.clear()
            return

        win.doctor_dataset_label.setText(
            tr("데이터셋: {name} — scene {n}개").format(
                name=root.name or str(root), n=len(files)))
        props = props_by_id()
        rows = []
        bad_total = 0
        for path in files:
            try:
                md = read_scene_metadata(path)
                vs = audit_scene(path, props)
                with h5py.File(path, "r") as f:
                    eps = sum(1 for k in f if k.startswith("episode"))
            except Exception as e:  # noqa: BLE001
                win.log(f"[닥터] {path.name} 을 읽지 못했습니다: {e}")
                continue
            rows.append((md.scene_id, eps, len(vs)))
            bad_total += len(vs)
        fill_scene_rows(win, rows)
        win.doctor_hint.setText(
            tr("문제 없음 — scene {n}개를 검사했습니다.").format(n=len(rows))
            if not bad_total else
            tr("지시문 {n}건이 기록과 맞지 않습니다. 줄을 눌러 "
               "기준 사진과 대조하세요.").format(n=bad_total))

    # ------------------------------------------------------------- 선택
    def on_row_picked(self, item: QTreeWidgetItem) -> None:
        sid = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if sid:
            self.select_scene(str(sid))

    def select_scene(self, scene_id: str) -> None:
        win = self.win
        path = self._path(scene_id)
        try:
            md = read_scene_metadata(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("scene 을 열 수 없습니다"), str(e))
            return
        self._scene_id = scene_id
        win.doctor_title.setText(tr("{sid} — {f}").format(
            sid=scene_id, f=path.name))
        self._show_photo(path)
        win.doctor_info.setText(describe_scene(md))

        props = props_by_id()
        self._suggestion = suggest_object_fix(path, props)
        if self._suggestion is None:
            win.doctor_fix_box.setVisible(False)
        else:
            s = self._suggestion
            win.doctor_fix_label.setText(tr(
                "{reason}\n{old} → {new}").format(
                    reason=s.reason, old=s.old_id, new=s.new_id))
            win.doctor_fix_box.setVisible(True)

        self._task = None
        self._fill_tasks(path, props, md.scene_id)
        self._show_task_detail()
        self._show_file_detail()
        show_center_tab(win, "doc_record")

    def _show_photo(self, path: Path) -> None:
        win = self.win
        try:
            img = read_reference_image(path)
        except Exception:  # noqa: BLE001
            img = None
        if img is None:
            win.doctor_photo.clear()
            win.doctor_photo.setText(tr("기준 사진 없음"))
            return
        pm = np_to_pixmap(img)
        win.doctor_photo.setText("")
        win.doctor_photo.setPixmap(pm.scaledToWidth(
            PHOTO_W, Qt.TransformationMode.SmoothTransformation))

    def _fill_tasks(self, path: Path, props, md_scene_id: str) -> None:
        win = self.win
        tree = win.doctor_task_tree
        tree.clear()
        bad = {v.instruction_id: v.message for v in audit_scene(path, props)}
        seen: dict = {}
        with h5py.File(path, "r") as f:
            for k in f:
                if not k.startswith("episode"):
                    continue
                a = f[k].attrs
                iid = str(a.get("instruction_id", "?"))
                text = str(a.get("instruction", ""))
                n, _t = seen.get(iid, (0, text))
                seen[iid] = (n + 1, text)
        # 계획에만 있고 안 찍은 지시문도 줄로 보인다 (2026-09-07 사용자:
        # "S016 인데 왜 8개가 아니라 6개만 보이죠?"). 에피소드만 읽으면 빈
        # 칸이 안 보이는데, 정작 S016 의 해법이 **그 빈 칸과 교환하는 것**
        # 이다 -- 고칠 재료가 화면에 없으면 고칠 수가 없다.
        empty = set()
        plan = dataset_plan_path(self._root())
        if plan.is_file():
            try:
                for iid, text in plan_task_texts(plan, md_scene_id).items():
                    if iid not in seen:
                        seen[iid] = (0, text)
                        empty.add(iid)
            except Exception as e:  # noqa: BLE001
                win.log(f"[닥터] 계획을 읽지 못했습니다: {e}")
        for iid, (n, text) in sorted(seen.items()):
            msg = bad.get(iid, "")
            state = "⚠" if msg else ("빈 칸" if iid in empty else "—")
            it = QTreeWidgetItem([iid, str(n), text, state])
            it.setData(0, Qt.ItemDataRole.UserRole, (iid, text))
            if msg:
                it.setToolTip(3, msg)
            elif iid in empty:
                it.setToolTip(3, tr("계획에만 있고 아직 안 찍었습니다."))
            tree.addTopLevelItem(it)
        parts = [tr("지시문 {n}개").format(n=len(seen))]
        if empty:
            parts.append(tr("그중 {n}개는 안 찍은 빈 칸").format(n=len(empty)))
        if bad:
            parts = [tr("⚠ 표시된 줄이 기록과 맞지 않습니다 (상태 칸에 "
                        "마우스를 올리면 사유가 보입니다).")] + parts
        win.doctor_task_hint.setText(" · ".join(parts))

    # ------------------------------------------------------------- 정정
    def apply_suggestion(self) -> None:
        win = self.win
        s = self._suggestion
        if s is None:
            return
        if win.worker is not None:
            QMessageBox.information(win, tr("수집 중"),
                                    tr("세션을 끝낸 뒤 고치세요."))
            return
        ok = QMessageBox.question(
            win, tr("기록 정정"),
            tr("{sid} 의 기록을 고칩니다.\n\n{old}\n  → {new}\n\n{reason}\n\n"
               "기준 사진과 대조하셨습니까? 에피소드와 지시문은 바뀌지 "
               "않습니다.").format(sid=s.scene_id, old=s.old_id, new=s.new_id,
                                   reason=s.reason))
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            apply_object_fix(self._path(s.scene_id), s.old_id, s.new_id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("고치지 못했습니다"), str(e))
            return
        win.log(f"[닥터] {s.scene_id} 기록 정정: {s.old_id} → {s.new_id}")
        self.rescan()
        self.select_scene(s.scene_id)

    def edit_task_text(self) -> None:
        win = self.win
        if not self._guard():
            return
        iid, cur = self._task
        path = self._path(self._scene_id)
        n = episode_counts(path).get(iid, 0)

        # 후보는 문법이 만든다 -- 조립한 것이 합법인지 검사할 필요가 없다.
        try:
            md = read_scene_metadata(path)
            props = props_by_id()
            options = [(skill_of(x), x)
                       for x in enumerate_instructions(md, props)]
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("문법을 읽지 못했습니다"), str(e))
            return
        # 이미 쓰이는 문장은 뺀다 -- 한 scene 안에서 두 지시문이 같은 말을
        # 하면 그 자체가 결함이다.
        used = self._used_texts(path, exclude=iid)
        options = [(sk, x) for sk, x in options if x not in used]
        if not options:
            QMessageBox.information(win, tr("고를 문장이 없습니다"), tr(
                "이 scene 에서 문법이 만들 수 있는 문장이 이미 전부 "
                "쓰이고 있습니다."))
            return
        note = (tr("에피소드 {n}개의 문장이 바뀝니다 — 이미 Hub 에 올린 "
                   "데이터셋이라면 이어붙이기가 막히고 전체 재빌드·재푸시가 "
                   "필요합니다.").format(n=n) if n else
                tr("안 찍은 빈 칸이라 계획 파일만 바뀝니다."))
        dlg = SentenceDialog(
            win, tr("{sid} {iid}").format(sid=self._scene_id, iid=iid),
            cur, options, note)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
            return
        text = dlg.chosen
        if text == cur:
            return

        plan = dataset_plan_path(self._root())
        try:
            changed = rewrite_task_text(
                path, iid, text.strip(),
                plan_path=plan if plan.is_file() else None)
        except ValueError as e:
            QMessageBox.warning(win, tr("문장을 바꾸지 못했습니다"), str(e))
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("문장을 바꾸지 못했습니다"), str(e))
            return
        win.log(f"[닥터] {self._scene_id} {iid} 문장 정정 "
                f"({changed}개) — Hub 재푸시가 필요합니다")
        self.rescan()
        self.select_scene(self._scene_id)

    # ------------------------------------------------- 고른 지시문 한 줄
    def on_task_picked(self, item) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._task = tuple(data) if data else None
        self._show_task_detail()

    def _show_task_detail(self) -> None:
        """우측 패널 -- 고른 지시문의 값과 그 줄에 하는 일.

        고른 것이 없으면 안내만 두고 버튼을 전부 끈다. 상자를 숨기지는
        않는다 -- 우측은 닥터의 자기 페이지라, 비어 있어도 "여기가 고치는
        자리" 라는 사실 자체가 정보다.
        """
        win = self.win
        buttons = getattr(win, "doctor_task_buttons", None)
        if not buttons:
            return
        if not self._task or not self._scene_id:
            win.doctor_task_detail.setText(
                tr("가운데 표에서 지시문을 고르세요"))
            for b in buttons.values():
                b.setEnabled(False)
            return
        iid, text = self._task
        path = self._path(self._scene_id)
        n = episode_counts(path).get(iid, 0) if path.is_file() else 0
        msg = ""
        for v in audit_scene(path, props_by_id()):
            if v.instruction_id == iid:
                msg = v.message
                break
        lines = [f"<b>{iid}</b> — {tr('에피소드')} {n}", text]
        if msg:
            lines.append(f"<span style='color:#8a4b00;'>⚠ {msg}</span>")
        if not n:
            lines.append(tr("(안 찍은 빈 칸 — 계획에만 있습니다)"))
        win.doctor_task_detail.setText("<br>".join(lines))
        for name, b in buttons.items():
            # 에피소드가 있으면 계획에서만 뺄 수 없다 -- 미리 꺼 둔다.
            b.setEnabled(n == 0 if name == "remove_task" else True)

    def _show_file_detail(self) -> None:
        """우측 아래 -- 이 scene 파일의 상태.

        edit_count 가 0 이 아니면 변환기가 이어붙이기를 거부한다(전체
        재빌드만 허용). 고치기 전에 이미 고쳐진 파일인지 보이는 편이 낫다.
        """
        win = self.win
        lab = getattr(win, "doctor_file_detail", None)
        if lab is None:
            return
        if not self._scene_id:
            lab.setText(tr("scene 을 고르세요"))
            return
        path = self._path(self._scene_id)
        try:
            md = read_scene_metadata(path)
            with h5py.File(path, "r") as f:
                eps = sum(1 for k in f if k.startswith("episode"))
                edits = int(f["metadata"].attrs.get("edit_count", 0))
                when = str(f["metadata"].attrs.get("edited", ""))
        except Exception as e:  # noqa: BLE001
            lab.setText(str(e))
            return
        lines = [f"<b>{md.scene_id}</b> — {path.name}",
                 tr("에피소드 {n} · 스키마 {v}").format(
                     n=eps, v=md.dataset_version)]
        if edits:
            lines.append(tr(
                "<span style='color:#8a4b00;'>편집 {n}회{when} — 변환은 "
                "이어붙이기 없이 전체 재빌드입니다</span>").format(
                    n=edits, when=f" ({when})" if when else ""))
        lab.setText("<br>".join(lines))

    # ----------------------------------------------------------- 교환
    def swap_task_text(self) -> None:
        win = self.win
        if not self._guard():
            return
        iid, _text = self._task
        others = []
        for i in range(win.doctor_task_tree.topLevelItemCount()):
            it = win.doctor_task_tree.topLevelItem(i)
            other = it.data(0, Qt.ItemDataRole.UserRole)
            if other and other[0] != iid:
                others.append((other[0], it.text(1), other[1]))
        plan = dataset_plan_path(self._root())
        if plan.is_file():
            # 계획에만 있고 안 찍은 지시문도 후보다 -- S016 의 해법이 그것이다
            # ('on' 으로 찍힌 것과, 같은 동작을 옳게 적어 둔 빈 칸을 맞바꾼다).
            try:
                shown = {o[0] for o in others} | {iid}
                for other_id, text in plan_task_texts(
                        plan, self._scene_id).items():
                    if other_id not in shown:
                        others.append((other_id, "0", text))
            except Exception as e:  # noqa: BLE001
                win.log(f"[닥터] 계획을 읽지 못했습니다: {e}")
        if not others:
            QMessageBox.information(win, tr("바꿀 상대가 없습니다"), tr(
                "이 scene 에는 지시문이 하나뿐입니다."))
            return
        others.sort()
        mine = (iid, self._task[1],
                episode_counts(self._path(self._scene_id)).get(iid, 0))
        dlg = SwapDialog(win, mine, [(o, int(n), t) for o, n, t in others])
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
            return
        other_id = dlg.chosen
        if not plan.is_file():
            QMessageBox.warning(win, tr("계획이 없습니다"), tr(
                "교환은 계획 파일의 문장을 함께 바꿉니다. "
                "instructions.json 이 있어야 합니다."))
            return
        try:
            a, b = swap_task_texts(self._path(self._scene_id), iid, other_id,
                                   plan_path=plan)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("바꾸지 못했습니다"), str(e))
            return
        win.log(f"[닥터] {self._scene_id} {iid} ↔ {other_id} 문장 교환 "
                f"(에피소드 {a} / {b})")
        if a or b:
            win.log("[닥터] 에피소드 문장이 바뀌었습니다 — Hub 재푸시가 "
                    "필요합니다")
        self.rescan()
        self.select_scene(self._scene_id)

    # ------------------------------------------------------- 계획에서 빼기
    def remove_task(self) -> None:
        win = self.win
        if not self._guard():
            return
        iid, text = self._task
        plan = dataset_plan_path(self._root())
        if not plan.is_file():
            QMessageBox.warning(win, tr("계획이 없습니다"),
                                tr("instructions.json 이 없습니다."))
            return
        ok = QMessageBox.question(
            win, tr("계획에서 빼기"),
            tr("{sid} {iid} 를 계획에서 뺍니다.\n\n{text}\n\n"
               "더는 이 문장으로 찍지 않는다는 뜻입니다. 에피소드는 지우지 "
               "않습니다.").format(sid=self._scene_id, iid=iid, text=text))
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_task_from_plan(plan, self._scene_id, iid,
                                  scene_path=self._path(self._scene_id))
        except ValueError as e:
            QMessageBox.warning(win, tr("빼지 못했습니다"), str(e))
            return
        win.log(f"[닥터] {self._scene_id} {iid} 를 계획에서 뺐습니다")
        self.rescan()
        self.select_scene(self._scene_id)

    def _used_texts(self, path: Path, exclude: str = "") -> set:
        """이 scene 에서 이미 쓰이는 문장 (에피소드 + 계획)."""
        used = set()
        try:
            with h5py.File(path, "r") as f:
                for k in f:
                    if not k.startswith("episode"):
                        continue
                    a = f[k].attrs
                    if str(a.get("instruction_id", "")) != exclude:
                        used.add(str(a.get("instruction", "")))
        except Exception:  # noqa: BLE001
            pass
        plan = dataset_plan_path(self._root())
        if plan.is_file():
            try:
                for iid, text in plan_task_texts(plan, self._scene_id).items():
                    if iid != exclude:
                        used.add(text)
            except Exception:  # noqa: BLE001
                pass
        return used

    def _guard(self) -> bool:
        """고른 줄이 있고 수집 중이 아닌가."""
        win = self.win
        if win.worker is not None:
            QMessageBox.information(win, tr("수집 중"),
                                    tr("세션을 끝낸 뒤 고치세요."))
            return False
        if not self._task or not self._scene_id:
            QMessageBox.information(win, tr("지시문을 고르세요"), tr(
                "표에서 고칠 지시문 줄을 먼저 누르세요."))
            return False
        return True
