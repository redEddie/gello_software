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
from PyQt6.QtWidgets import (
    QDialog,
    QInputDialog,
    QMessageBox,
    QTreeWidgetItem,
)

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
from gello.scene.collection_progress import scan
from gello.data.dataset_schema import schema_version_key
from gello.scene.schema_doctor import (
    RESET_TOLERANCE_DEG,
    diagnose,
    fill_payload,
    known_payload,
    reset_drift,
    restamp,
)
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
        self._shortfall = None     # 진행 닥터에서 고른 줄
        self._diag = None          # 스키마 닥터에서 고른 줄
        self._drift = {}           # scene -> (초기 자세 대조, 파일 기준인가)

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
        # 진행 쪽 수도 한 번에 낸다 -- 목록의 두 칸이 같은 새로고침에서
        # 나와야 서로 다른 시점을 말하지 않는다.
        by_scene: dict = {}
        plan = dataset_plan_path(root)
        if plan.is_file():
            try:
                for sf in scan(root, plan).shortfalls:
                    by_scene[sf.scene_id] = by_scene.get(sf.scene_id, 0) + 1
            except Exception as e:  # noqa: BLE001
                win.log(f"[닥터] 진행을 세지 못했습니다: {e}")
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
            rows.append((md.scene_id, eps, n_bad, by_scene.get(md.scene_id, 0)))
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
        self.refresh_progress()
        show_center_tab(win, "doc_record")

    def _show_photo(self, path: Path) -> None:
        self._photo(self.win.doctor_photo, path)

    def _photo(self, label, path: Path) -> None:
        """기준 사진을 라벨에 넣는다. 기록 닥터와 진행 닥터가 함께 쓴다."""
        win = self.win
        try:
            img = read_reference_image(path)
        except Exception:  # noqa: BLE001
            img = None
        if img is None:
            label.clear()
            label.setText(tr("기준 사진 없음"))
            return
        pm = np_to_pixmap(img)
        label.setText("")
        label.setPixmap(pm.scaledToWidth(
            PHOTO_W, Qt.TransformationMode.SmoothTransformation))

    def _fill_tasks(self, path: Path, props, md_scene_id: str) -> None:
        win = self.win
        tree = win.doctor_task_tree
        tree.clear()
        # 한 지시문이 두 가지로 틀릴 수 있다 (어순이 뒤집혔고 관계도 틀린
        # 문장) -- dict 로 덮어쓰면 마지막 하나만 남는다.
        bad: dict = {}
        for v in audit_scene(path, props):
            bad.setdefault(v.instruction_id, []).append(v.message)
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
            msgs = bad.get(iid, [])
            state = (f"⚠{len(msgs)}" if len(msgs) > 1 else
                     "⚠" if msgs else ("빈 칸" if iid in empty else "—"))
            it = QTreeWidgetItem([iid, str(n), text, state])
            it.setData(0, Qt.ItemDataRole.UserRole, (iid, text))
            if msgs:
                it.setToolTip(3, "\n".join(f"· {m}" for m in msgs))
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
        msgs = [v.message for v in audit_scene(path, props_by_id())
                if v.instruction_id == iid]
        card.set_fields([
            (tr("지시문"), iid),
            (tr("에피소드"), str(n) if n else
             tr("0 (미수집)")),
            (tr("문장"), text),
        ])
        diag.setText("<br>".join(f"· {m}" for m in msgs) if msgs
                     else tr("없음"))
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

    # ======================================================== 진행 닥터
    def refresh_progress(self) -> None:
        """계획과 파일을 대조해 미달 목록을 채운다.

        수집 중에도 **막지 않는다** -- 기록 닥터와 다른 점이다. 저쪽은 고치는
        화면이라 세션 중에 파일을 건드리면 안 되지만, 이쪽은 읽기만 하고
        "다음에 무엇을 찍지" 는 오히려 수집 중에 묻는 질문이다. 지금 쓰고 있는
        파일 하나는 잠겨서 못 읽는데, 그건 잠겼다고 적는다 (scan 이 처리한다).
        """
        win = self.win
        tree = getattr(win, "progress_tree", None)
        if tree is None:
            return
        root = self._root()
        plan = dataset_plan_path(root)
        tree.clear()
        self._shortfall = None
        if not plan.is_file():
            win.progress_title.setText(tr("지시문 파일 없음"))
            win.progress_hint.setText(tr(
                "{p} 가 없습니다. Configure 에서 지시문을 먼저 적으세요.")
                .format(p=plan.name))
            self._show_shortfall_detail()
            return
        try:
            prog = scan(root, plan)
        except Exception as e:  # noqa: BLE001
            win.progress_title.setText(tr("계획을 읽지 못했습니다"))
            win.progress_hint.setText(str(e))
            return

        # 왼쪽에서 scene 을 골랐으면 **그 안만** 본다. 데이터셋 전체 미달이
        # 수십 개일 때 한 scene 을 끝내는 것이 현실적인 다음 수인데, 전체
        # 목록에서는 그 scene 의 줄들이 흩어져 보인다 (2026-09-07 사용자).
        # Space 로 선택을 풀면 다시 전체가 된다.
        shown = [sf for sf in prog.shortfalls
                 if not self._scene_id or sf.scene_id == self._scene_id]
        win.progress_title.setText(tr(
            "{sid} · 목표 미달 {n}개").format(
                sid=self._scene_id, n=len(shown))
            if self._scene_id else tr(
            "전체 · 지시문 {t}개 · {u}/{g} 에피소드 ({p}%) · 목표 미달 {n}개")
            .format(t=prog.tasks, u=prog.usable, g=prog.target,
                    p=prog.percent, n=len(shown)))
        for sf in shown:
            state = (tr("못 읽음") if sf.unreadable else
                     tr("파일 없음") if sf.missing_file else str(sf.remaining))
            it = QTreeWidgetItem([sf.scene_id, sf.instruction_id,
                                  f"{sf.usable}/{sf.target}", state,
                                  sf.instruction])
            it.setData(0, Qt.ItemDataRole.UserRole, sf)
            tree.addTopLevelItem(it)
        win.progress_hint.setText(
            (tr("{sid} 는 다 채웠습니다. Space 로 전체를 봅니다.")
             .format(sid=self._scene_id) if self._scene_id
             else tr("모두 채웠습니다."))
            if not shown else tr(
                "줄을 눌러 배치를 확인한 뒤 [수집으로] 를 누르세요."
                + ("  (Space: 전체 보기)" if self._scene_id else "")))
        self._show_shortfall_detail()

    def on_shortfall_picked(self, item) -> None:
        sf = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._shortfall = sf
        win = self.win
        if sf is None:
            return
        path = self._path(sf.scene_id)
        if path.is_file():
            self._photo(win.progress_photo, path)
            try:
                win.progress_zones.set_layout_spec(
                    read_scene_metadata(path).layout)
            except Exception:  # noqa: BLE001 -- 잠긴 파일일 수 있다
                win.progress_zones.set_layout_spec(None)
        else:
            win.progress_photo.clear()
            win.progress_photo.setText(tr("아직 만들지 않은 scene 입니다"))
            win.progress_zones.set_layout_spec(None)
        self._show_shortfall_detail()

    def _show_shortfall_detail(self) -> None:
        win = self.win
        card = getattr(win, "progress_card", None)
        if card is None:
            return
        sf = self._shortfall
        note, buttons = win.progress_note, win.progress_buttons
        if sf is None:
            card.set_fields([(tr("지시문"), tr("미선택"))])
            note.setText("—")
            for b in buttons.values():
                b.setEnabled(False)
            return
        card.set_fields([
            (tr("Scene"), sf.scene_id),
            (tr("지시문"), sf.instruction_id),
            (tr("수집"), f"{sf.usable}/{sf.target}"),
            (tr("남음"), str(sf.remaining)),
            (tr("문장"), sf.instruction),
        ])
        # 이어 찍으면 그 파일의 버전이 유지된다. 지금은 연결한 뒤 로그
        # 한 줄로 알게 되는데, 누르기 전에 말하는 편이 낫다.
        note.setText(
            tr("아직 만들지 않은 scene 입니다 — 새로 만들면서 찍습니다.")
            if sf.missing_file else
            tr("읽지 못했습니다 — {why}. 수집한 수를 모릅니다 "
               "(0개가 아닙니다).").format(why=sf.reason)
            if sf.unreadable else
            tr("{sid} 는 {v} 입니다 — 여기서 찍은 것도 그 버전을 유지합니다.")
            .format(sid=sf.scene_id, v=sf.version))
        for b in buttons.values():
            b.setEnabled(not sf.unreadable)

    def go_collect(self) -> None:
        """이 scene·지시문을 시작 설정으로 걸고 Collect 로 데려간다."""
        win = self.win
        sf = self._shortfall
        if sf is None:
            return
        if win.worker is not None:
            QMessageBox.information(win, tr("수집 중"), tr(
                "세션을 끝낸 뒤 다른 지시문으로 옮기세요."))
            return
        try:
            if not win.scene_planning.pick_start(
                    sf.scene_id, sf.instruction_id, sf.instruction):
                return
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("시작 설정 실패"), str(e))
            return
        win.log(f"[닥터] {sf.scene_id} {sf.instruction_id} 로 시작 설정 "
                f"({sf.usable}/{sf.target}, {sf.remaining}개 남음)")
        win._set_activity("collect")

    def clear_selection(self) -> bool:
        """Space -- 고른 것을 한 겹 푼다. 푼 것이 있으면 True.

        깊은 쪽부터다: 지시문/미달 줄이 골라져 있으면 그것만, 없으면 scene.
        한 번에 전부 풀지 않는 이유는 되돌리기 때문이다 -- scene 은 그대로
        두고 줄만 다시 고르는 일이 잦다.

        scene 이 풀리면 진행 닥터가 데이터셋 전체로 돌아온다. 그것이 이 키의
        쓸모다 (2026-09-07 사용자: "너무 많이 놓쳤을 때 도움이 될 거예요").
        """
        win = self.win
        if self._task or self._shortfall:
            self._task = None
            self._shortfall = None
            win.doctor_task_tree.clearSelection()
            if getattr(win, "progress_tree", None) is not None:
                win.progress_tree.clearSelection()
            self._show_task_detail()
            self._show_shortfall_detail()
            return True
        if self._scene_id:
            self._scene_id = ""
            self._suggestion = None
            win.doctor_tree.clearSelection()
            win.doctor_task_tree.clear()
            win.doctor_title.setText(tr("왼쪽에서 scene 을 고르세요"))
            win.doctor_photo.clear()
            win.doctor_photo.setText(tr("기준 사진 없음"))
            win.doctor_info.setText("")
            self._show_scene_detail()
            self._show_task_detail()
            self.refresh_progress()
            return True
        return False

    # ====================================================== 스키마 닥터
    def refresh_schema(self) -> None:
        """모든 scene 의 데이터세트 버전과 내용을 대조한다."""
        win = self.win
        tree = getattr(win, "schema_tree", None)
        if tree is None:
            return
        tree.clear()
        self._diag = None
        root = self._root()
        try:
            files = iter_scene_files(root)
        except Exception as e:  # noqa: BLE001
            win.schema_title.setText(tr("경로 오류"))
            win.schema_hint.setText(str(e))
            return
        spread: dict = {}
        bad = 0
        self._drift = {}
        station = self._station_reset_pose()
        for path in files:
            d = diagnose(path)
            # 적힌 리셋 자세와 **실제로 찍힌 첫 프레임**을 맞댄다. 파일에
            # 적힌 것이 정본이고, 없으면 지금 station 값으로 재되 그 사실을
            # 화면이 밝힌다 (수집 당시와 다를 수 있다).
            try:
                md_ref = read_scene_metadata(path).reset_qpos
            except Exception:  # noqa: BLE001
                md_ref = None
            ref = md_ref or (station[1] if station else None)
            try:
                self._drift[d.scene_id] = (
                    reset_drift(path, ref) if ref else None,
                    bool(md_ref))
            except Exception:  # noqa: BLE001 -- 잠긴 파일 등
                self._drift[d.scene_id] = (None, bool(md_ref))
            if d.error:
                state = tr("못 읽음")
            elif d.ok:
                state = "—"
            else:
                state = tr("어긋남")
                bad += 1
            dr, _from_file = self._drift.get(d.scene_id, (None, False))
            pose = ("—" if dr is None else
                    "—" if not dr["over"] else f"{len(dr['over'])} ⚠")
            it = QTreeWidgetItem([d.scene_id, str(d.episodes), d.stamped,
                                  d.satisfied or "?", pose, state])
            it.setData(0, Qt.ItemDataRole.UserRole, d)
            tree.addTopLevelItem(it)
            if not d.error:
                spread.setdefault(d.stamped, []).append(d.scene_id)
        win.schema_title.setText(tr(
            "scene {n}개 · 어긋남 {b}개 · {s}").format(
                n=len(files), b=bad,
                s=" · ".join(f"{v} {len(ids)}개"
                             for v, ids in sorted(spread.items()))))
        # 버전이 섞여 있는 것 자체는 문제가 아니다 -- 그렇게 말해 둔다.
        win.schema_hint.setText(tr(
            "버전이 섞여 있는 것은 문제가 아닙니다 (변환기가 여분 필드를 "
            "허용합니다). 고칠 것은 '찍힘' 과 '내용' 이 다른 줄입니다.")
            if not bad else tr(
            "'찍힘' 과 '내용' 이 다른 줄은 검증이 실패합니다. 줄을 눌러 "
            "무엇이 빠졌는지 보세요."))
        self._show_schema_detail()

    def on_schema_picked(self, item) -> None:
        self._diag = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._show_schema_detail()

    def _show_schema_detail(self) -> None:
        win = self.win
        card = getattr(win, "schema_card", None)
        if card is None:
            return
        d = self._diag
        miss, plan, buttons = (win.schema_missing, win.schema_plan,
                               win.schema_buttons)
        if d is None:
            card.set_fields([(tr("Scene"), tr("미선택"))])
            miss.setText("—")
            plan.setText("—")
            for b in buttons.values():
                b.setEnabled(False)
            return
        card.set_fields([
            (tr("Scene"), d.scene_id),
            (tr("에피소드"), str(d.episodes)),
            (tr("찍힘"), d.stamped or "?"),
            (tr("내용"), d.satisfied or "?"),
        ])
        self._show_drift(d)
        if d.error:
            miss.setText(d.error)
            plan.setText(tr("읽지 못해 판단할 수 없습니다."))
            for b in buttons.values():
                b.setEnabled(False)
            return
        if not d.missing:
            miss.setText(tr("없음"))
        else:
            lines = []
            for k, n in sorted(d.missing.items()):
                lines.append(k if n < 0 else
                             tr("{k} — 에피소드 {n}개").format(k=k, n=n))
            miss.setText("<br>".join(lines))
        # **양방향이다.** 올리는 것과 내리는 것은 뜻이 정반대라 안내도 갈라야
        # 한다 -- 내리기는 "사실에 맞춘다", 올리기는 "이미 갖춘 것을 제대로
        # 알린다" 이고, 뒤섞으면 조작자가 무엇을 누르는지 모른다.
        if d.can_restamp:
            up = schema_version_key(d.satisfied) > schema_version_key(
                d.stamped or "knu-0.0.0")
            win.schema_buttons["align_version"].setText(
                tr("데이터세트 버전 올리기") if up
                else tr("데이터세트 버전 내리기"))
            plan.setText(tr(
                "내용이 이미 {b} 를 만족합니다 — {a} 에서 {b} 로 올리면 그 "
                "필드들이 제대로 알려집니다. 잃는 것은 없습니다.")
                .format(a=d.stamped or "?", b=d.satisfied) if up else tr(
                "내용은 {b} 까지만 만족합니다 — {a} 에서 {b} 로 내려 사실에 "
                "맞춥니다. 에피소드도, 파일에 있는 필드도 그대로입니다.")
                .format(a=d.stamped or "?", b=d.satisfied))
        elif d.missing:
            plan.setText(tr(
                "내용이 만족하는 버전이 없습니다. 버전을 바꿔서는 못 "
                "고칩니다 — 빠진 것을 채우거나 그 에피소드를 지워야 합니다."))
        else:
            plan.setText(tr("데이터세트 버전이 내용과 맞습니다."))
            win.schema_buttons["align_version"].setText(
                tr("데이터세트 버전 맞추기"))
        buttons["align_version"].setEnabled(d.can_restamp)
        # 채우기는 **빠진 것이 부하 모델뿐일 때만**. 에피소드에 관측이 빠진
        # 것은 값을 넣어 메울 수 없다 -- 그건 지우는 수밖에 없다.
        only_payload = bool(d.missing) and all(
            k.startswith("metadata/payload") for k in d.missing)
        buttons["fill_payload"].setEnabled(only_payload)

    def _show_drift(self, d) -> None:
        """적힌 리셋 자세와 실제 첫 프레임의 어긋남.

        station 설정과 파일 metadata 를 맞대는 것은 장부끼리 맞추는 것이라,
        설정이 나중에 바뀌면 옛 파일이 원래 달라도 오탐이 난다 (2026-09-07
        사용자). 파일이 "여기서 출발했다" 고 적고 데이터는 다른 데서 시작하는
        것 -- 그것만이 어긋남이다.
        """
        win = self.win
        lab = getattr(win, "schema_drift", None)
        if lab is None:
            return
        dr, from_file = self._drift.get(d.scene_id, (None, False))
        if dr is None:
            lab.setText(tr("기준이 없습니다 (파일에 리셋 자세가 없고 "
                           "station 값도 못 읽었습니다)"))
            return
        src = (tr("파일에 적힌 값 기준") if from_file
               else tr("지금 station 설정 기준 — 수집 당시와 다를 수 있습니다"))
        if not dr["over"]:
            lab.setText(tr("{n}개 모두 ±{deg}도 안 (최대 {w:.1f}도) · {src}")
                        .format(n=dr["checked"], deg=int(RESET_TOLERANCE_DEG),
                                w=dr["worst"] * 57.2958, src=src))
            return
        lines = [tr("<span style='color:#8a4b00;'>{n}개가 ±{deg}도를 "
                    "벗어납니다</span> · {src}").format(
                        n=len(dr["over"]), deg=int(RESET_TOLERANCE_DEG),
                        src=src)]
        for name, rad in dr["over"][:5]:
            lines.append(f"&nbsp;&nbsp;{name} — {rad * 57.2958:.1f}도")
        if len(dr["over"]) > 5:
            lines.append(tr("&nbsp;&nbsp;… 외 {n}개").format(
                n=len(dr["over"]) - 5))
        lab.setText("<br>".join(lines))

    def align_version(self) -> None:
        win = self.win
        d = self._diag
        if d is None or not d.can_restamp:
            return
        if win.worker is not None:
            QMessageBox.information(win, tr("수집 중"),
                                    tr("세션을 끝낸 뒤 고치세요."))
            return
        up = schema_version_key(d.satisfied) > schema_version_key(
            d.stamped or "knu-0.0.0")
        ok = QMessageBox.question(
            win, tr("데이터세트 버전 올리기") if up
            else tr("데이터세트 버전 내리기"),
            tr("{sid} 의 데이터세트 버전을 {a} → {b} 로 바꿉니다.\n\n{why}")
            .format(sid=d.scene_id, a=d.stamped or "?", b=d.satisfied,
                    why=tr("내용이 이미 그 버전을 만족합니다 — 갖춘 것을 "
                           "제대로 알리게 됩니다. 잃는 것은 없습니다.") if up
                    else tr("에피소드는 건드리지 않고, 파일에 있는 필드도 "
                            "그대로입니다. 버전만 내용에 맞춥니다.")))
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            restamp(self._path(d.scene_id), d.satisfied)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("버전 변경 실패"), str(e))
            return
        win.log(f"[닥터] {d.scene_id} 데이터세트 버전 {d.stamped} → {d.satisfied}")
        self.refresh_schema()

    def fill_payload(self) -> None:
        """빠진 부하 모델을 채우고 그 자리에서 다시 찍는다.

        기본값은 **같은 데이터셋의 다른 scene 에 적힌 값**이다. 추측이 아니라
        같은 리그에서 같은 시기에 찍은 파일에 남아 있는 사실이고, 그 출처를
        화면에 적어 사람이 확인하게 한다 (#47: "조작자가 값과 그 출처를
        대야 한다"). 여러 값이 섞여 있으면 기본값을 주지 않는다.
        """
        win = self.win
        d = self._diag
        if d is None:
            return
        if win.worker is not None:
            QMessageBox.information(win, tr("수집 중"),
                                    tr("세션을 끝낸 뒤 고치세요."))
            return
        known = known_payload(self._root())
        if known is None:
            QMessageBox.information(win, tr("기본값 없음"), tr(
                "이 데이터셋에 적힌 부하 모델이 없거나 여러 값이 섞여 "
                "있습니다. 수집 당시 값을 직접 확인해 넣어야 합니다."))
            return
        mass, com = known
        text, ok = QInputDialog.getText(
            win, tr("부하 모델 채우기"),
            tr("{sid} 에 부하 모델을 적습니다.\n\n"
               "아래는 이 데이터셋의 다른 scene 에 적힌 값입니다 — 수집 당시와 "
               "같은지 확인하세요.\n형식: 질량, x, y, z").format(sid=d.scene_id),
            text=f"{mass}, {com[0]}, {com[1]}, {com[2]}")
        if not ok:
            return
        try:
            parts = [float(x) for x in text.replace(" ", "").split(",")]
            if len(parts) != 4:
                raise ValueError("질량과 무게중심 셋, 모두 넷이 필요합니다")
            got = fill_payload(self._path(d.scene_id), parts[0], parts[1:])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(win, tr("채우지 못했습니다"), str(e))
            return
        win.log(f"[닥터] {d.scene_id} 부하 모델 채움 → 버전 {got}")
        self.refresh_schema()

    @staticmethod
    def _station_reset_pose() -> "tuple[str, list] | None":
        """지금 station 설정의 리셋 자세.

        gello/scene 에 두지 않는다 -- 그 표(FR3_RESET_POSES)는 gello/robots 에
        있고 scene 층은 구체 하드웨어를 부를 수 없다 (계층 규칙). 그리고 이
        값은 **지금 설정**이라 파일에 적힌 것과 출처가 다르다: 수집 당시와
        다를 수 있으므로 화면이 그 차이를 밝혀야 한다.
        """
        try:
            from gello.config.station import load_station
            from gello.robots.franka_fr3 import FR3_RESET_POSES

            name = str(load_station().robot.reset_pose or "")
            q = FR3_RESET_POSES.get(name)
            if not name or q is None:
                return None
            return name, [float(x) for x in q]
        except Exception:  # noqa: BLE001
            return None
