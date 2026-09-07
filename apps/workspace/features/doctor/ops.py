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
from apps.workspace.features.doctor.object_dialog import ObjectDialog
from apps.workspace.features.doctor.record_tab import PHOTO_W
from apps.workspace.features.doctor.sentence_builder import SentenceDialog
from apps.workspace.features.doctor.swap_dialog import SwapDialog
from apps.workspace.shared.tabs import show_center_tab
from gello.gui.i18n import tr
from gello.gui.widgets.video_view import np_to_pixmap
from gello.scene.dataset_meta import plan_path as dataset_plan_path
from gello.scene.instruction_grammar import (
    enumerate_instructions,
    resolve_reference,
    skill_of,
)
from gello.scene.props import props_by_id
from gello.scene.scene_format import (
    iter_scene_files,
    read_reference_image,
    read_scene_metadata,
    scene_filename,
)
from gello.scene.scene_repair import (
    apply_object_fix,
    audit_scene,
    explain_scene,
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
            win.doctor_dataset_label.setText(tr("경로 오류"))
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
            # 지시문 수로 센다 -- 한 지시문이 두 가지로 틀릴 수 있는데
            # (어순 + 관계) 그것은 두 건이 아니라 한 줄의 문제다.
            n_bad = len({v.instruction_id for v in vs})
            rows.append((md.scene_id, eps, n_bad))
            bad_total += n_bad
        fill_scene_rows(win, rows)
        win.doctor_hint.setText(
            tr("문제 없음 · scene {n}개 검사").format(n=len(rows))
            if not bad_total else
            tr("맞지 않는 지시문 {n}건 · 줄을 눌러 기준 사진과 대조")
            .format(n=bad_total))

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
            QMessageBox.warning(win, tr("Scene 열기 실패"), str(e))
            return
        self._scene_id = scene_id
        win.doctor_title.setText(tr("{sid} — {f}").format(
            sid=scene_id, f=path.name))
        self._show_photo(path)
        win.doctor_info.set_scene(md)

        props = props_by_id()
        self._suggestion = suggest_object_fix(path, props)

        self._task = None
        self._fill_tasks(path, props, md.scene_id)
        self._show_task_detail()
        self._show_scene_detail()
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
                win.log(f"[닥터] 지시문 파일을 읽지 못했습니다: {e}")
        for iid, (n, text) in sorted(seen.items()):
            msg = bad.get(iid, "")
            state = "⚠" if msg else ("빈 칸" if iid in empty else "—")
            it = QTreeWidgetItem([iid, str(n), text, state])
            it.setData(0, Qt.ItemDataRole.UserRole, (iid, text))
            if msg:
                it.setToolTip(3, msg)
            elif iid in empty:
                it.setToolTip(3, tr("미수집 · 지시문 파일에만 있음"))
            tree.addTopLevelItem(it)
        parts = [tr("지시문 {n}개").format(n=len(seen))]
        if empty:
            parts.append(tr("그중 {n}개는 안 찍은 빈 칸").format(n=len(empty)))
        if bad:
            parts = [tr("⚠ = 기록과 불일치 · 상태 칸에 마우스를 올리면 사유")] + parts
        win.doctor_task_hint.setText(" · ".join(parts))

    # ------------------------------------------------------------- 정정
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
            QMessageBox.warning(win, tr("문법 읽기 실패"), str(e))
            return
        # 이미 쓰이는 문장은 **빼지 않고** 누가 쓰는지 넘긴다 -- 대화상자가
        # 뱃지를 남기고 취소선을 긋는다 (2026-09-07 사용자: 뱃지가 사라지면
        # 왜 없는지 알 수 없다). 한 scene 안에서 두 지시문이 같은 말을 하는
        # 것은 여전히 막는다.
        used_by = self._used_by(path, exclude=iid)
        from apps.workspace.features.doctor.sentence_builder import sense_key

        taken = {sense_key(x) for x in used_by}
        if all(sense_key(x) in taken for _sk, x in options):
            QMessageBox.information(win, tr("후보 없음"), tr(
                "이 scene 에서 문법이 만들 수 있는 문장이 이미 전부 "
                "쓰이고 있습니다."))
            return
        note = (tr("에피소드 {n}개의 문장이 바뀝니다 — 이미 Hub 에 올린 "
                   "데이터셋이라면 이어붙이기가 막히고 전체 재빌드·재푸시가 "
                   "필요합니다.").format(n=n) if n else
                tr("안 찍은 빈 칸이라 지시문 파일만 바뀝니다."))
        dlg = SentenceDialog(
            win, tr("{sid} {iid}").format(sid=self._scene_id, iid=iid),
            cur, options, note, used_by,
            resolve=lambda phrase: resolve_reference(phrase, md, props))
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
            QMessageBox.warning(win, tr("문장 수정 실패"), str(e))
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("문장 수정 실패"), str(e))
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
        """우측 [이 지시문] -- 값 / 맞지 않는 것. scene 상자와 같은 읽는 법."""
        win = self.win
        card = getattr(win, "doctor_task_card", None)
        if card is None:
            return
        diag = win.doctor_task_diag
        buttons = win.doctor_task_buttons
        if not self._task or not self._scene_id:
            card.set_fields([(tr("지시문"), tr("미선택"))])
            diag.setText("—")
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
        card.set_fields([
            (tr("지시문"), iid),
            (tr("에피소드"), str(n) if n else
             tr("0 (미수집)")),
            (tr("문장"), text),
        ])
        diag.setText(msg or tr("없음"))
        for name, b in buttons.items():
            # 에피소드가 있으면 지시문 파일에서만 뺄 수 없다 -- 미리 꺼 둔다.
            b.setEnabled(n == 0 if name == "remove_task" else True)

    def _show_scene_detail(self) -> None:
        """우측 [이 scene] -- 값 / 맞지 않는 것 / 고치면.

        **칸은 늘 같은 자리에 늘 있다.** 값이 없으면 "없음" 이라고 적지
        숨기지 않는다 (2026-09-07 사용자: 상자가 늘었다 줄었다 하면 무엇이
        어디 오는지 익힐 수가 없다). 그리고 칸마다 종류가 하나다 -- 편집
        횟수는 값이고, 그 대가는 "고치면" 이다.

        진단은 **원인과 이유**를 적는다. "N건이 맞지 않습니다" 만으로는
        배치를 왜 고쳐야 하는지 알 수 없다.
        """
        win = self.win
        card = getattr(win, "doctor_scene_card", None)
        if card is None:
            return
        diag, cost = win.doctor_scene_diag, win.doctor_scene_cost
        buttons = win.doctor_scene_buttons
        if not self._scene_id:
            card.set_fields([(tr("Scene"), tr("미선택"))])
            card.set_zones(None)
            diag.setText("—")
            cost.setText("—")
            for b in buttons.values():
                b.setEnabled(False)
            return
        path = self._path(self._scene_id)
        try:
            md = read_scene_metadata(path)
            with h5py.File(path, "r") as f:
                eps = sum(1 for k in f if k.startswith("episode"))
                edits = int(f["metadata"].attrs.get("edit_count", 0))
                when = str(f["metadata"].attrs.get("edited", ""))
            reasons = explain_scene(path, props_by_id())
        except Exception as e:  # noqa: BLE001
            card.set_fields([(tr("Scene"), self._scene_id),
                             (tr("오류"), str(e))])
            card.set_zones(None)
            diag.setText("—")
            cost.setText("—")
            return

        card.set_fields([
            (tr("Scene"), md.scene_id),
            (tr("파일"), path.name),
            (tr("에피소드"), str(eps)),
            (tr("스키마"), md.dataset_version),
            (tr("편집"), tr("{n}회 · {when}").format(n=edits, when=when)
             if edits else tr("없음")),
        ])
        card.set_zones(md.layout)

        if not reasons:
            diag.setText(tr("없음"))
        else:
            lines = []
            for r in reasons:
                lines.append(tr("· {text}<br>&nbsp;&nbsp;지시문 {t}건 · "
                                "에피소드 {e}개").format(
                                    text=r.text, t=r.tasks, e=r.episodes))
                if r.detail:
                    lines.append("&nbsp;&nbsp;<span style='color:#8a4b00;'>"
                                 f"{r.detail}</span>")
            if self._suggestion is not None:
                s = self._suggestion
                lines.append(tr(
                    "<br>기록을 {old} → {new} 로 고치면 첫 줄이 사라집니다"
                ).format(old=s.old_id, new=s.new_id))
            diag.setText("<br>".join(lines))

        # 이 칸이 답하는 질문은 "지금 무엇을 누르면 얼마가 드나" 다. 조건문이
        # 아니라 **두 버튼의 값**을 적는다 (2026-09-07: "기록만 고치면
        # 이어붙이기 유지" 가 무슨 뜻이냐는 물음 -- 물어봐야 하면 실패다).
        # "이어붙이기" 는 변환기 resume 을 가리키는 이 저장소의 말이지만,
        # 여기서는 조작자가 실제로 겪는 일(재변환·재업로드)로 적는다.
        cost.setText(tr(
            "소품 수정 — 재변환 불필요<br>"
            "문장 수정 · 교환 — 전체 재변환·재업로드"
        ) if not edits else tr(
            "이미 편집됨 — 무엇을 고치든 다음 변환은 전체 재변환·재업로드"))

        for b in buttons.values():
            b.setEnabled(True)

    # ------------------------------------------------------- 소품 고치기
    def edit_objects(self) -> None:
        win = self.win
        if win.worker is not None:
            QMessageBox.information(win, tr("수집 중"),
                                    tr("세션을 끝낸 뒤 고치세요."))
            return
        if not self._scene_id:
            return
        path = self._path(self._scene_id)
        try:
            md = read_scene_metadata(path)
            props = props_by_id()
            with h5py.File(path, "r") as f:
                sents = sorted({str(f[k].attrs.get("instruction", ""))
                                for k in f if k.startswith("episode")})
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("Scene 열기 실패"), str(e))
            return
        dlg = ObjectDialog(win, md, props, sents, self._suggestion)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.changes:
            return
        try:
            for old_id, new_id in dlg.changes.items():
                apply_object_fix(path, old_id, new_id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("수정 실패"), str(e))
            return
        for old_id, new_id in dlg.changes.items():
            win.log(f"[닥터] {self._scene_id} 기록 정정: {old_id} → {new_id}")
        self.rescan()
        self.select_scene(self._scene_id)

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
                win.log(f"[닥터] 지시문 파일을 읽지 못했습니다: {e}")
        if not others:
            QMessageBox.information(win, tr("교환 상대 없음"), tr(
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
            QMessageBox.warning(win, tr("지시문 파일 없음"), tr(
                "교환은 지시문 파일의 문장을 함께 바꿉니다. "
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
            QMessageBox.warning(win, tr("지시문 파일 없음"),
                                tr("instructions.json 이 없습니다."))
            return
        ok = QMessageBox.question(
            win, tr("지시문 빼기"),
            tr("{sid} {iid} 를 지시문 파일에서 뺍니다.\n\n{text}\n\n"
               "더는 이 문장으로 찍지 않는다는 뜻입니다. 에피소드는 지우지 "
               "않습니다.").format(sid=self._scene_id, iid=iid, text=text))
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_task_from_plan(plan, self._scene_id, iid,
                                  scene_path=self._path(self._scene_id))
        except ValueError as e:
            QMessageBox.warning(win, tr("제거 실패"), str(e))
            return
        win.log(f"[닥터] {self._scene_id} {iid} 를 지시문 파일에서 뺐습니다")
        self.rescan()
        self.select_scene(self._scene_id)

    def _used_by(self, path: Path, exclude: str = "") -> dict:
        """{문장: 그것을 쓰는 지시문 id} -- 에피소드와 지시문 파일 둘 다."""
        used = {}
        try:
            with h5py.File(path, "r") as f:
                for k in f:
                    if not k.startswith("episode"):
                        continue
                    a = f[k].attrs
                    other = str(a.get("instruction_id", ""))
                    if other != exclude:
                        used.setdefault(str(a.get("instruction", "")), other)
        except Exception:  # noqa: BLE001
            pass
        plan = dataset_plan_path(self._root())
        if plan.is_file():
            try:
                for iid, text in plan_task_texts(plan, self._scene_id).items():
                    if iid != exclude:
                        used.setdefault(text, iid)
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
            QMessageBox.information(win, tr("지시문 미선택"), tr(
                "표에서 고칠 지시문 줄을 먼저 누르세요."))
            return False
        return True
