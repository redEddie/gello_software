"""Collection-plan and slot-planning operations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox, QTreeWidgetItem

from apps.workspace.features.scene.dialogs.plan_edit_dialog import PlanEditDialog
from gello.gui.i18n import tr
from gello.scene.collection_plan import (
    PLANS_DIR,
    check_scene_against_plan,
    list_plans,
    load_plan,
)
from gello.scene.scene_format import (
    INSTRUCTION_ID_RE,
    count_by_slot,
    scene_filename,
)


class ScenePlanningOps:
    """Plan files, slot dropdowns, counts, and plan progress."""

    def __init__(self, win) -> None:
        self.win = win

    def refresh_plan_progress(self) -> None:
        """Statistics 의 계획 진행률 표 -- 계획 × 실제 scene 파일 대조."""
        tree = getattr(self.win, "plan_progress_tree", None)
        if tree is None:
            return
        tree.clear()
        plan = self.current_plan()
        if plan is None:
            self.win.plan_progress_label.setText(
                tr("Configure 에서 수집 계획을 선택하세요."))
            return
        root = Path(self.win.root_edit.text().strip() or ".")
        done = total = 0
        skipped: list = []
        for sp in plan.scenes:
            path = root / scene_filename(sp.scene_id)
            counts: dict = {}
            note = ""
            if self.win.session.scene_session and sp.scene_id == self.win.scene_ops.session_scene_id():
                counts = self.session_slot_counts()
                note = tr(" (세션 중 — 캐시)")
            elif path.exists():
                try:
                    counts = count_by_slot(path)
                except Exception:  # noqa: BLE001 -- 잠금 등
                    note = tr(" (파일 사용 중)")
            else:
                # 파일이 없는(아직 안 찍었거나 지운) scene 은 표에 넣지
                # 않는다 -- 지운 파일의 slot 목록이 계속 보이는 것이
                # 혼란스럽다는 실사용 피드백. 개수는 아래 요약에 남긴다.
                skipped.append(sp.scene_id)
                continue
            s_done = s_total = 0
            top = QTreeWidgetItem([f"{sp.scene_id}{note}", "", "", ""])
            for s in sp.slots:
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                s_done += min(c, s.target)
                s_total += s.target
                it = QTreeWidgetItem(
                    [f"  {s.instruction_id}", str(c), str(s.target),
                     s.instruction])
                if c >= s.target:
                    for col_i in range(4):
                        it.setForeground(col_i, Qt.GlobalColor.darkGreen)
                top.addChild(it)
            top.setText(1, str(s_done))
            top.setText(2, str(s_total))
            done += s_done
            total += s_total
            tree.addTopLevelItem(top)
        tree.expandAll()
        pct = (100 * done // total) if total else 0
        text = tr("전체 {d}/{t} ({p}%) — {n}").format(
            d=done, t=total, p=pct, n=plan.path.name)
        if skipped:
            text += tr("  ·  파일 없는 scene {n}개 표시 안 함 ({s})").format(
                n=len(skipped), s=", ".join(skipped[:4]))
        self.win.plan_progress_label.setText(text)

    def refresh_start_plan_combo(self) -> None:
        """Configure 의 계획 문장 드롭다운 = 계획 × 선택 scene.

        카운트는 scene 파일에서 온다 (계획 파일에는 카운트가 없다 -- 두 개의
        진실 금지). 세션이 파일을 쥐고 있으면 카운트만 생략된다.
        """
        if not hasattr(self.win, "start_plan_combo"):
            return
        combo = self.win.start_plan_combo
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        plan = self.current_plan()
        # 계획이 있으면 문장은 계획에서만 고른다 -- 자유 입력이 계획 밖
        # slot(문장-ID 갈라짐)을 실데이터에 만들었다. 새 문장은 ✎ 편집으로
        # 계획에 먼저 추가한다. 계획이 없을 때만 직접 입력을 연다.
        combo.addItem(tr("(계획에서 선택)") if plan is not None
                      else tr("(직접 입력)"), None)
        self.win.lang_edit.setReadOnly(plan is not None)
        self.win.scene_iid_edit.setReadOnly(plan is not None)
        for w in (self.win.lang_edit, self.win.scene_iid_edit):
            w.setStyleSheet("color:#888;" if plan is not None else "")
        sid = self.win.scene_ops.configure_scene_id()
        if plan is not None and sid is not None:
            counts: dict = {}
            if self.win.session.scene_session and sid == self.win.scene_ops.session_scene_id():
                # 세션이 파일을 쥐고 있다 -- saver 가 보내준 캐시로 센다
                counts = self.session_slot_counts()
            else:
                p = self.win.scene_ops.selected_scene_path()
                if p is not None and p.exists():
                    try:
                        counts = count_by_slot(p)
                    except Exception:  # noqa: BLE001 -- HDF5 잠금 등
                        counts = {}
            for s in plan.slots_for(sid):
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                combo.addItem(
                    f"{s.instruction_id} · {c}/{s.target} · {s.instruction}",
                    (s.instruction_id, s.instruction))
            if keep:
                for i in range(combo.count()):
                    if combo.itemData(i) == keep:
                        combo.setCurrentIndex(i)
                        break
        combo.blockSignals(False)

    def on_start_plan_pick(self, *_args) -> None:
        d = self.win.start_plan_combo.currentData()
        if d:
            self.win.scene_iid_edit.setText(d[0])
            self.win.lang_edit.setText(d[1])

    def on_slot_sentence_edited(self) -> None:
        # 세션 중에는 파일이 잠겨 있으므로 캐시로 (파일 인자 없이)
        self.auto_assign_iid(self.win.slot_instr_edit.text(), self.win.slot_iid_edit,
                              scene_id=self.win.scene_ops.session_scene_id(),
                              episodes=self.win.session.active_episode_cache)

    def current_plan(self):
        """선택된 계획 파일. 작아서 캐시 없이 매번 읽는다 -- 파일을 고치고
        새로고침할 때 낡은 캐시가 남는 쪽이 더 나쁘다."""
        data = getattr(self.win, "plan_combo", None) and self.win.plan_combo.currentData()
        if not data:
            return None
        try:
            return load_plan(Path(data))
        except Exception as e:  # noqa: BLE001
            self.win.log(f"[계획] {Path(data).name} 로드 실패: {type(e).__name__}: {e}")
            return None

    def refresh_plan_combo(self, select: "str | None" = None) -> None:
        """계획 파일 목록을 다시 읽는다. select 로 파일명을 주면 그걸 고른다."""
        keep = select or self.win.plan_combo.currentText()
        self.win.plan_combo.blockSignals(True)
        self.win.plan_combo.clear()
        self.win.plan_combo.addItem(tr("(계획 없음 — 자유 입력)"), None)
        for p in list_plans():
            self.win.plan_combo.addItem(p.name, str(p))
        idx = self.win.plan_combo.findText(keep)
        self.win.plan_combo.setCurrentIndex(max(0, idx))
        self.win.plan_combo.blockSignals(False)
        self.on_plan_selected()

    def on_new_plan(self) -> None:
        name, ok = QInputDialog.getText(
            self, tr("새 수집 계획"),
            tr("계획 이름 (영문/숫자/-/_, 확장자 없이):"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            QMessageBox.warning(self.win, tr("이름 오류"),
                                tr("영문·숫자·-·_ 만 쓸 수 있습니다."))
            return
        path = PLANS_DIR / f"{name}.json"
        if path.exists():
            QMessageBox.warning(self.win, tr("이미 있음"),
                                tr("{n} 이 이미 있습니다. 드롭다운에서 "
                                   "선택하세요.").format(n=path.name))
            return
        PLANS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"plan_version": 1, "scenes": []},
                                   ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        self.win.log(f"[계획] 새 계획 생성: {path.name}")
        self.refresh_plan_combo(select=path.name)
        self.on_edit_plan()    # 빈 계획은 쓸모없으니 바로 편집으로

    def on_delete_plan(self) -> None:
        data = self.win.plan_combo.currentData()
        if not data:
            QMessageBox.information(self.win, tr("계획 없음"),
                                    tr("삭제할 계획 파일을 먼저 선택하세요."))
            return
        p = Path(data)
        ans = QMessageBox.question(
            self, tr("계획 삭제"),
            tr("{n} 을(를) 삭제할까요?\n수집 파일에는 영향이 없고, git 이력"
               "에서 되살릴 수 있습니다.").format(n=p.name))
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            p.unlink()
        except OSError as e:
            QMessageBox.warning(self.win, tr("삭제 실패"), str(e))
            return
        self.win.log(f"[계획] 삭제: {p.name}")
        self.refresh_plan_combo(select="")

    def on_edit_plan(self) -> None:
        data = self.win.plan_combo.currentData()
        if not data:
            QMessageBox.information(self.win, tr("계획 없음"),
                                    tr("편집할 계획 파일을 먼저 선택하세요."))
            return
        dlg = PlanEditDialog(self.win, Path(data))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for w in getattr(dlg, "warnings", []):
                self.win.log(f"[계획 경고] {w}")
            self.win.log(f"[계획] {Path(data).name} 저장됨")
            # 갱신된 목표/slot 이 화면에 반영되게
            self.on_plan_selected()

    def on_plan_selected(self, *_args) -> None:
        plan = self.current_plan()
        if plan is not None:
            self.win._recents.add("plan_file", self.win.plan_combo.currentText())
            for w in plan.warnings:
                self.win.log(f"[계획 경고] {w}")
        self.refresh_slot_panel()
        self.win.scene_ops.on_scene_selected()

    def refresh_slot_panel(self) -> None:
        """계획 slot 드롭다운 + 수집 카운트 + 계획-파일 불일치 경고 갱신.

        카운트는 계획 파일이 아니라 scene 파일에서 계산한다(두 개의 진실
        금지). 세션 중 에피소드가 저장될 때마다 다시 계산된다.
        """
        if not hasattr(self.win, "slot_plan_combo"):
            return
        combo = self.win.slot_plan_combo
        combo.blockSignals(True)
        combo.clear()
        plan = self.current_plan()
        # Configure 쪽과 같은 규칙: 계획이 있으면 드롭다운에서만 고른다.
        combo.addItem(tr("(계획에서 선택)") if plan is not None
                      else tr("(직접 입력)"), None)
        if hasattr(self.win, "slot_iid_edit"):
            self.win.slot_iid_edit.setReadOnly(plan is not None)
            self.win.slot_instr_edit.setReadOnly(plan is not None)
            for w in (self.win.slot_iid_edit, self.win.slot_instr_edit):
                w.setStyleSheet("color:#888;" if plan is not None else "")
        # 세션 중이므로 파일을 다시 열지 않는다(HDF5 잠금) -- scene ID 는
        # 워커 설정에서, 에피소드·카운트는 saver 가 보내준 캐시에서.
        sid = self.win.scene_ops.session_scene_id() if self.win.session.scene_session else None
        counts = self.session_slot_counts()
        episodes = list(self.win.session.active_episode_cache or [])
        warn: list = []
        if plan is not None and sid is not None:
            slots = plan.slots_for(sid)
            for s in slots:
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                combo.addItem(
                    f"{s.instruction_id} · {c}/{s.target} · {s.instruction}",
                    (s.instruction_id, s.instruction))
            if not slots:
                warn.append(tr("계획에 scene {s} 가 없습니다").format(s=sid))
            warn.extend(check_scene_against_plan(plan, sid, episodes))
        combo.blockSignals(False)
        self.win.slot_plan_warn.setText("\n".join(warn[:4]))

    def on_slot_plan_pick(self, *_args) -> None:
        d = self.win.slot_plan_combo.currentData()
        if d:
            self.win.slot_iid_edit.setText(d[0])
            self.win.slot_instr_edit.setText(d[1])
            self.win.collection.refresh_slot_counter()

    def on_next_slot(self) -> None:
        """계획에서 목표(target)를 못 채운 첫 slot 을 골라 채워준다 (§6:
        채우지 못한 채 책상을 치우는 것이 재수집의 시작이다)."""
        plan = self.current_plan()
        sid = self.win.scene_ops.session_scene_id() if self.win.session.scene_session else None
        if plan is None or sid is None:
            self.win.log("[SLOT] 계획이 없거나 scene 세션이 아닙니다")
            return
        counts = self.session_slot_counts()
        for s in plan.slots_for(sid):
            c = counts.get(s.instruction_id, {}).get("usable", 0)
            if c < s.target:
                for i in range(self.win.slot_plan_combo.count()):
                    if self.win.slot_plan_combo.itemData(i) == (s.instruction_id, s.instruction):
                        self.win.slot_plan_combo.setCurrentIndex(i)
                        break
                self.win.slot_iid_edit.setText(s.instruction_id)
                self.win.slot_instr_edit.setText(s.instruction)
                self.win.log(f"[SLOT] 다음 미수집: {s.instruction_id} ({c}/{s.target}) {s.instruction}")
                return
        self.win.log("[SLOT] 이 scene 의 모든 slot 이 목표를 채웠습니다")

    def on_apply_slot(self) -> None:
        """scene 세션 중 slot 전환 -- worker 의 cmd_set_slot 호출만 한다."""
        if self.win.worker is None or not self.win.session.scene_session:
            return
        iid = self.win.slot_iid_edit.text().strip()
        instr = self.win.slot_instr_edit.text().strip()
        if not INSTRUCTION_ID_RE.match(iid):
            QMessageBox.warning(self.win, tr("slot 오류"),
                                tr("instruction ID 형식이 틀렸습니다 (예: I000)."))
            return
        if not instr or (instr.startswith('"') and instr.endswith('"')):
            QMessageBox.warning(self.win, tr("slot 오류"),
                                tr("따옴표 없는 순수 문장을 입력하세요."))
            return
        # 계획이 있으면 계획의 (ID, 문장) 쌍만 적용 가능 -- 자유 입력이
        # 계획 밖 slot 을 만들던 구멍을 세션 중에도 막는다.
        plan = self.current_plan()
        sid = self.win.scene_ops.session_scene_id()
        if plan is not None and sid is not None:
            slots = plan.slots_for(sid)
            if slots and not any(s.instruction_id == iid
                                 and s.instruction == instr for s in slots):
                QMessageBox.warning(self.win, tr("slot 오류"), tr(
                    "계획에 없는 slot 입니다 ({i}). '계획 slot' 드롭다운에서 "
                    "고르세요 — 새 문장은 계획을 먼저 수정하세요.").format(i=iid))
                return
        self.win.worker.cmd_set_slot(instr, iid)
        self.win.slot_current_label.setText(f"{iid}: {instr}")
        self.win.collection.refresh_slot_counter()
        # cmd_set_slot 은 워커 큐로 가서 다음 드레인에 반영된다 -- 오른쪽
        # 패널은 사용자가 누른 값으로 즉시 갱신한다 (워커 속성은 곧 같아진다).
        self.win.right_fields["ds_task"].setText(f"{iid}: {instr}")
        self.win.right_fields["ds_task"].setToolTip(f"{iid}: {instr}")
        self.win._recents.add("instruction_id", iid)
        self.win._recents.add("language", instr)
        # 계획과 어긋난 수동 입력은 막지 않되 즉시 보이게 한다 (ID-문장
        # 갈라짐이 실데이터에서 실제로 발생했다).
        plan = self.current_plan()
        sid = self.win.scene_ops.session_scene_id()
        if plan is not None and sid is not None:
            sentences = {s.instruction_id: s.instruction
                         for s in plan.slots_for(sid)}
            if iid in sentences and sentences[iid] != instr:
                self.win.log(f"[SLOT 경고] {iid} 문장이 계획({sid})과 다릅니다 -- "
                         f"계획: {sentences[iid]!r}")
            elif sentences and iid not in sentences:
                self.win.log(f"[SLOT 경고] 계획({sid})에 없는 slot {iid} 로 수집합니다")

    def on_rank_selected(self) -> None:
        """Selecting a row draws its curves -- the point of the panel is that a
        number never decides on its own whether a take is bad."""
        items = self.win.rank_tree.selectedItems()
        if not items:
            return
        path, demo = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.win.stats_ops.show_analysis_for(path, demo)
        self.win.playback_ops.show_trim_for(path, demo)

    def session_slot_counts(self) -> dict:
        """세션 중 slot 카운트 -- 파일은 saver 가 h5py 로 잠그고 있으므로
        다시 열지 않고, saver 가 보내준 에피소드 목록으로 계산한다
        (count_by_slot 과 같은 정의: usable = quality_status success)."""
        counts: dict = {}
        for e in (self.win.session.active_episode_cache or []):
            iid = e.get("instruction_id")
            if not iid:
                continue
            c = counts.setdefault(iid, {"total": 0, "usable": 0})
            c["total"] += 1
            if e.get("quality_status") == "success":
                c["usable"] += 1
        return counts

    def known_slots(self, scene_id=None, scene_path=None, episodes=None) -> dict:
        """**이 scene 의** instruction_id -> 문장 매핑.

        ID 는 scene 마다 독립이다(각 scene 의 첫 instruction 이 I000, 새
        문장마다 +1 -- 2026-08-13 결정). 그래서 참조 범위도 scene 하나:
        계획에서 그 scene 의 slot + 그 scene 파일에 기록된 에피소드.
        계획이 먼저다 -- 파일 쪽에 갈라짐 사고가 있어도 계획이 정본.

        세션 중에는 episodes(GUI 가 saver 에게서 받은 캐시)를 넘겨야 한다 --
        HDF5 파일 잠금 때문에 열려 있는 파일을 다시 읽을 수 없다.
        """
        m: dict = {}
        plan = self.current_plan()
        if plan is not None and scene_id is not None:
            for s in plan.slots_for(scene_id):
                m.setdefault(s.instruction_id, s.instruction)
        for ep in (episodes or []):
            if ep.get("instruction_id"):
                m.setdefault(ep["instruction_id"], ep.get("instruction", ""))
        if episodes is None and scene_path is not None and Path(scene_path).exists():
            try:
                from gello.scene.scene_format import list_scene_episodes

                for ep in list_scene_episodes(scene_path):
                    m.setdefault(ep["instruction_id"], ep["instruction"])
            except Exception:  # noqa: BLE001 - 다른 프로세스가 잠갔을 수 있다
                pass
        return m

    def next_iid(known: dict) -> str:
        used = [int(i[1:]) for i in known if INSTRUCTION_ID_RE.match(i)]
        return f"I{(max(used) + 1) if used else 0:03d}"

    def auto_assign_iid(self, instr: str, iid_edit, scene_id=None,
                         scene_path=None, episodes=None) -> None:
        """문장이 바뀌면 slot ID 를 자동으로 맞춘다 (**scene 안에서**).

        모든 scene 은 첫 instruction 이 I000 이고 새 문장마다 하나씩
        올라간다. 이 scene 에서 아는 문장 -> 그 ID 재사용, 처음 보는 문장
        -> 이 scene 의 다음 빈 ID. 다른 scene 의 ID 는 참조하지 않는다.
        자동 배정 후에도 손으로 고칠 수 있다.
        """
        instr = instr.strip()
        if not instr:
            return
        known = self.known_slots(scene_id, scene_path, episodes=episodes)
        for iid, s in known.items():
            if s == instr:
                if iid_edit.text().strip() != iid:
                    iid_edit.setText(iid)
                    self.win.log(f"[SLOT] 아는 문장 -- {iid} 재사용")
                return
        cur = iid_edit.text().strip()
        nxt = self.next_iid(known)
        if cur in known and known[cur] != instr:
            iid_edit.setText(nxt)
            self.win.log(f"[SLOT] 새 문장 -- {nxt} 자동 배정 ({cur} 는 이 scene 에서 사용 중)")
        elif not INSTRUCTION_ID_RE.match(cur) or cur not in known and cur != nxt:
            # 빈/이상한 값이거나, 이 scene 기준으로 뜬금없는 번호(예: 다른
            # scene 에서 넘어온 I003)면 이 scene 의 다음 번호로 정렬한다.
            iid_edit.setText(nxt)
            if cur and cur != nxt:
                self.win.log(f"[SLOT] 새 문장 -- {nxt} 자동 배정")
