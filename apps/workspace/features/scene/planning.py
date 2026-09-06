"""Collection-plan and slot-planning operations."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QMessageBox, QTreeWidgetItem

from apps.workspace.features.scene.dialogs.plan_edit_dialog import PlanEditDialog
from gello.config.quality import QUALITY_SUCCESS
from gello.scene.dataset_meta import PLAN_FILENAME, plan_path
from gello.gui.i18n import tr
from gello.scene.collection_plan import (
    check_scene_against_plan,
    load_plan,
)
from gello.scene.scene_format import (
    INSTRUCTION_ID_RE,
    SCENE_FILE_RE,
    count_by_slot,
    iter_scene_files,
    list_scene_episodes,
    read_scene_metadata,
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
                tr("이 데이터셋에는 계획이 없습니다 (instructions.json 없음)."))
            return
        root = Path(self.win.root_edit.text().strip() or ".")
        done = total = 0
        skipped: list = []
        for sp in plan.scenes:
            path = root / scene_filename(sp.scene_id)
            counts: dict = {}
            note = ""
            if self.win.session.scene_session and sp.scene_id == self.win.scene_ops.session_scene_id():
                counts = self.session_instruction_counts()
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
                # 줄을 누르면 이것이 시작 설정이 된다 (on_plan_row_picked).
                it.setData(0, Qt.ItemDataRole.UserRole,
                           (sp.scene_id, s.instruction_id, s.instruction))
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

    def on_plan_row_picked(self, item) -> None:
        """Instruction 탭의 지시문 줄 = 시작 설정 (2026-09-06 사용자 요청).

        scene 과 지시문이 **함께** 정해진다. 전에는 왼쪽에서 scene 을 고르고
        다시 문장 드롭다운에서 고르는 두 단계였는데, 정작 무엇을 고를지는
        이 표를 보고 정하고 있었다 -- 보던 줄을 누르는 것이 곧 답이다.

        세션 중에는 바꾸지 않는다. 그때 지시문을 바꾸는 자리는 ③ Collect 의
        목록이고(같은 scene 안에서만), scene 자체는 파일이라 세션 중에 못 바꾼다.
        """
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not data:
            return                      # scene 머리줄 -- 고를 것이 없다
        sid, iid, instr = data
        if self.win.worker is not None:
            self.win.log("[지시문] 수집 중에는 시작 설정을 바꿀 수 없습니다 "
                         "(세션을 끝낸 뒤 고르세요)")
            return
        combo = self.win.scene_combo
        for i in range(combo.count()):
            if combo.itemData(i) == sid:
                combo.setCurrentIndex(i)
                break
        else:
            self.win.log(f"[지시문] {sid} 파일이 아직 없습니다 -- scene 을 먼저 만드세요")
            return
        self.win.scene_iid_edit.setText(iid)
        self.win.lang_edit.setText(instr)
        self.refresh_start_instruction()
        self.win.collection.refresh_instruction()
        self.win.log(f"[지시문] 시작 설정: {sid} · {iid} — {instr}")

    def refresh_start_instruction(self) -> None:
        """Configure 의 "시작 지시문" 한 줄과 계획 없음 경고를 갱신한다.

        고르는 장치는 여기 없다 (2026-09-06) -- Instruction 탭이 그 일을 하고,
        이 줄은 그 결과를 보여 줄 뿐이다. 드롭다운 + 문장 칸 + ID 칸 세 줄이
        같은 하나를 말하던 것을 한 줄로 줄였다.

        **계획이 없으면 수집할 수 없다**고 여기서 미리 말한다. Connect 를
        눌러 봐야 알게 되는 것보다, 준비 화면에서 붉게 보이는 편이 낫다.
        """
        warn = getattr(self.win, "start_warn", None)
        if warn is None:
            return
        plan = self.current_plan()
        if plan is None:
            warn.setText(tr(
                "이 데이터셋에는 수집 계획이 없습니다 — Instruction 탭의 "
                "[새 계획] 으로 먼저 만드세요. 계획 없이는 수집할 수 없습니다."))
            return
        sid = self.win.scene_ops.configure_scene_id()
        if sid is not None and not plan.slots_for(sid):
            warn.setText(tr(
                "계획에 {s} 가 없습니다 — Instruction 탭의 [계획 편집] 에서 "
                "이 scene 의 지시문을 추가하세요.").format(s=sid))
            return
        warn.setText("")

    def dataset_plan_path(self) -> Path:
        """현재 데이터셋(저장 경로)의 계획 파일 — 고정 파일명 컨벤션
        (instructions.json). 폴더에 있으면 계획 수집, 없으면 자유 입력."""
        return plan_path(Path(self.win.root_edit.text().strip() or "."))

    def current_plan(self):
        """데이터셋 폴더의 instructions.json. 작아서 캐시 없이 매번 읽는다 --
        파일을 고치고 새로고침할 때 낡은 캐시가 남는 쪽이 더 나쁘다."""
        path = self.dataset_plan_path()
        if not path.is_file():
            return None
        try:
            return load_plan(path)
        except Exception as e:  # noqa: BLE001
            self.win.log(f"[계획] {path.name} 로드 실패: {type(e).__name__}: {e}")
            return None

    def refresh_plan_label(self) -> None:
        """Configure 의 읽기 전용 계획 표시. 계획은 데이터셋에 귀속되므로
        선택 드롭다운은 없다."""
        label = getattr(self.win, "plan_label", None)
        if label is None:
            return
        path = self.dataset_plan_path()
        plan = self.current_plan()
        if plan is not None:
            n = sum(len(sp.slots) for sp in plan.scenes)
            label.setText(tr("{f} — scene {s}개 · 지시문 {n}개").format(
                f=PLAN_FILENAME, s=len(plan.scenes), n=n))
            label.setStyleSheet("")
        elif path.is_file():
            label.setText(tr("{f} — 로드 실패 (로그 참조)").format(f=PLAN_FILENAME))
            label.setStyleSheet("color:#e74c3c;")
        else:
            label.setText(tr("(계획 없음 — 자유 입력)"))
            label.setStyleSheet("color:#888;")

    def on_new_plan(self) -> None:
        """이 데이터셋에 빈 계획(instructions.json)을 만들고 바로 편집."""
        path = self.dataset_plan_path()
        if path.exists():
            QMessageBox.information(self.win, tr("이미 있음"), tr(
                "이 데이터셋에는 이미 계획이 있습니다. ✎ 로 편집하세요."))
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"plan_version": 1, "scenes": []},
                                       ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self.win, tr("생성 실패"), str(e))
            return
        self.win.log(f"[계획] 새 계획 생성: {path}")
        self.on_plan_changed()
        self.on_edit_plan()    # 빈 계획은 쓸모없으니 바로 편집으로

    def on_delete_plan(self) -> None:
        path = self.dataset_plan_path()
        if not path.exists():
            QMessageBox.information(self.win, tr("계획 없음"),
                                    tr("이 데이터셋에는 계획 파일이 없습니다."))
            return
        ans = QMessageBox.question(
            self.win, tr("계획 삭제"),
            tr("{n} 을(를) 삭제할까요?\n수집 파일에는 영향이 없습니다.")
            .format(n=path.name))
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as e:
            QMessageBox.warning(self.win, tr("삭제 실패"), str(e))
            return
        self.win.log(f"[계획] 삭제: {path}")
        self.on_plan_changed()

    def on_edit_plan(self) -> None:
        path = self.dataset_plan_path()
        if not path.exists():
            QMessageBox.information(self.win, tr("계획 없음"), tr(
                "이 데이터셋에는 계획 파일이 없습니다. + 로 먼저 만드세요."))
            return
        dlg = PlanEditDialog(self.win, path)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for w in getattr(dlg, "warnings", []):
                self.win.log(f"[계획 경고] {w}")
            self.win.log(f"[계획] {path.name} 저장됨")
            # 갱신된 목표/slot 이 화면에 반영되게
            self.on_plan_changed()

    def on_plan_changed(self) -> None:
        """계획 파일이 생기/바뀌/사라지거나 데이터셋(저장 경로)이 바뀐 뒤
        화면 전반을 갱신한다."""
        plan = self.current_plan()
        if plan is not None:
            for w in plan.warnings:
                self.win.log(f"[계획 경고] {w}")
        self.refresh_plan_label()
        self.refresh_instruction_list()
        self.win.scene_ops.on_scene_selected()

    def refresh_instruction_list(self) -> None:
        """Collect 화면의 지시문 목록 + 계획-파일 불일치 경고를 갱신한다.

        드롭다운을 대신한다 (2026-09-06). 고르는 것과 보는 것이 같은 위젯이라
        "지금 무엇을 찍고 있고, 남은 것은 무엇인가"가 한 눈에 들어온다 --
        드롭다운은 닫혀 있는 동안 그 답을 감추고 있었다.

        카운트는 계획 파일이 아니라 scene 파일에서 계산한다(두 개의 진실
        금지). 세션 중에는 파일이 잠겨 있으므로 saver 가 본내준 캐시로 센다.
        """
        tree = getattr(self.win, "instr_tree", None)
        if tree is None:
            return
        tree.clear()
        plan = self.current_plan()
        sid = (self.win.scene_ops.session_scene_id()
               if self.win.session.scene_session else None)
        counts = self.session_instruction_counts()
        episodes = list(self.win.session.active_episode_cache or [])
        cur_iid = ""
        if self.win.worker is not None:
            cur_iid = (getattr(self.win.worker, "_slot_instruction_id", "")
                       or getattr(self.win.worker.cfg, "instruction_id", ""))
        warn: list = []

        rows: list = []
        if plan is not None and sid is not None:
            for sl in plan.slots_for(sid):
                c = counts.get(sl.instruction_id, {}).get("usable", 0)
                rows.append((sl.instruction_id, c, sl.target, sl.instruction))
            if not rows:
                warn.append(tr("계획에 scene {s} 가 없습니다").format(s=sid))
            warn.extend(check_scene_against_plan(plan, sid, episodes))
        elif sid is not None:
            # 계획이 없는 데이터셋: 파일에 이미 있는 지시문만 보여준다.
            # 새 문장은 여기서 못 만든다 -- 계획을 먼저 쓰는 것이 규칙이고,
            # 자유 입력이 계획 밖 지시문을 실데이터에 만든 적이 있다.
            for iid, instr in sorted(self.known_instructions(
                    sid, episodes=episodes).items()):
                rows.append((iid, counts.get(iid, {}).get("usable", 0),
                             None, instr))

        for iid, done, target, instr in rows:
            item = QTreeWidgetItem([
                ("▸ " if iid == cur_iid else "  ") + iid,
                f"{done}/{target}" if target is not None else str(done),
                instr])
            item.setData(0, Qt.ItemDataRole.UserRole, (iid, instr))
            item.setToolTip(2, instr)
            if target is not None and done >= target:
                # 목표를 채운 줄은 초록 -- 남은 것이 무엇인지가 목록의 요점이다.
                for c in range(3):
                    item.setForeground(c, Qt.GlobalColor.darkGreen)
            if iid == cur_iid:
                font = item.font(0)
                font.setBold(True)
                for c in range(3):
                    item.setFont(c, font)
            tree.addTopLevelItem(item)
        self.win.instr_warn.setText("\n".join(warn[:4]))

    def on_instruction_picked(self, item) -> None:
        """목록에서 한 줄을 누르면 **바로** 그 지시문으로 바꾼다.

        고르기와 확정을 나누지 않는다 (2026-09-06 사용자 결정). 진행 중인
        에피소드는 워커가 시작 시점에 지시문을 캡처하므로 영향이 없고,
        바뀐 것은 다음 에피소드부터다 -- 되돌리는 것도 다른 줄을 누르는 것
        하나라, 확정 버튼이 막아 줄 실수가 없다.
        """
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not data:
            return
        self.apply_instruction(*data)

    # ------------------------------------------------------------ 빠른 재개
    def pick_resume_slot(self) -> tuple:
        """"지금 이어 찍을 자리" 를 데이터에서 골라 준다.

        규칙은 조작자가 준 그대로다 (2026-09-06): **scene 은 번호가 가장 높은
        것, task 는 번호가 낮은 순.** 노드가 반사로 죽어 다시 붙을 때 매번
        같은 자리를 손으로 다시 고르고 있었고, 그 자리를 고르는 규칙 자체는
        늘 같았다.

        "번호가 낮은 순"의 후보는 **아직 목표를 못 채운 slot** 이다 -- 다 채운
        I000 으로 매번 돌아가면 계획이 영영 안 끝난다. 전부 채웠으면 그때는
        가장 낮은 ID 로 그냥 이어 찍는다 (판단이 필요한 자리라 막지 않는다).

        반환: ``(scene_id, instruction_id, instruction, note)`` 또는
        고를 수 없으면 ``(None, None, None, 사유)``.
        """
        root = Path(self.win.root_edit.text().strip() or ".")
        try:
            files = list(iter_scene_files(root))
        except OSError as e:
            return None, None, None, tr("저장 경로를 읽을 수 없습니다: {e}").format(e=e)
        if not files:
            return None, None, None, tr(
                "{r} 에 scene 파일이 없습니다 — 첫 scene 은 '새 Scene 구성...' 으로 "
                "사람이 정해야 합니다.").format(r=root)
        # 파일명 번호가 곧 scene 번호다 (scene_NNN.hdf5). metadata 를 열어
        # 확인하지 않는 이유: 여기서 알고 싶은 것은 "가장 최근 자리" 뿐이고,
        # 파일 열기는 잠겨 있을 수 있다.
        newest = max(files, key=lambda p: int(SCENE_FILE_RE.match(p.name).group(1)))
        try:
            sid = read_scene_metadata(newest).scene_id
        except Exception:  # noqa: BLE001 -- 잠겼거나 깨졌다: 파일명으로 되돌린다
            sid = f"S{int(SCENE_FILE_RE.match(newest.name).group(1)):03d}"

        counts: dict = {}
        try:
            counts = count_by_slot(newest)
        except Exception:  # noqa: BLE001 -- 잠금: 카운트 없이 고른다
            pass

        plan = self.current_plan()
        if plan is not None and plan.slots_for(sid):
            slots = sorted(plan.slots_for(sid),
                           key=lambda s: self._iid_order(s.instruction_id))
            for s in slots:
                c = counts.get(s.instruction_id, {}).get("usable", 0)
                if c < s.target:
                    return (sid, s.instruction_id, s.instruction,
                            tr("{i} ({c}/{t})").format(
                                i=s.instruction_id, c=c, t=s.target))
            s = slots[0]
            return (sid, s.instruction_id, s.instruction,
                    tr("{i} — 이 scene 의 지시문이 모두 목표를 채웠습니다")
                    .format(i=s.instruction_id))

        # 계획이 없는 데이터셋: 그 파일에 이미 있는 slot 중 가장 낮은 ID.
        try:
            known = {ep["instruction_id"]: ep["instruction"]
                     for ep in list_scene_episodes(newest)}
        except Exception:  # noqa: BLE001
            known = {}
        if not known:
            return None, None, None, tr(
                "{s} 에 기록된 slot 이 없고 계획도 없습니다 — 문장을 직접 "
                "입력해야 합니다.").format(s=sid)
        iid = min(known, key=self._iid_order)
        return sid, iid, known[iid], tr("{i} (계획 없음)").format(i=iid)

    @staticmethod
    def _iid_order(iid: str) -> tuple:
        """I007 < I010 이 되게 숫자로 센다. 형식이 아니면 맨 뒤."""
        m = INSTRUCTION_ID_RE.match(str(iid))
        return (0, int(m.group(1))) if m else (1, 0)

    def on_next_instruction(self) -> None:
        """아직 목표를 못 채운 지시문 중 **번호가 가장 낮은 것**으로 바꾼다
        (§6: 채우지 못한 채 책상을 치우는 것이 재수집의 시작이다).

        빠른 재개(pick_resume_slot)와 같은 규칙이고, 같은 헬퍼로 센다 --
        두 곳이 다른 순서로 고르면 조작자가 어느 쪽을 믿을지 알 수 없다.
        """
        plan = self.current_plan()
        sid = (self.win.scene_ops.session_scene_id()
               if self.win.session.scene_session else None)
        if plan is None or sid is None:
            self.win.log("[지시문] 계획이 없거나 scene 세션이 아닙니다")
            return
        counts = self.session_instruction_counts()
        for sl in sorted(plan.slots_for(sid),
                         key=lambda x: self._iid_order(x.instruction_id)):
            c = counts.get(sl.instruction_id, {}).get("usable", 0)
            if c < sl.target:
                self.win.log(f"[지시문] 다음 미수집: {sl.instruction_id} "
                             f"({c}/{sl.target}) {sl.instruction}")
                self.apply_instruction(sl.instruction_id, sl.instruction)
                return
        self.win.log("[지시문] 이 scene 의 지시문이 모두 목표를 채웠습니다")

    def apply_instruction(self, iid: str, instr: str) -> None:
        """세션의 지시문을 바꾼다 -- 다음 에피소드부터 적용된다.

        부르는 곳은 둘뿐이다: 목록의 줄을 누를 때(on_instruction_picked)와
        [Next unfilled]. 손으로 문장을 치는 길은 없앴다 -- 그 길이 계획 밖
        지시문을 실데이터에 만들었다 (ID-문장 갈라짐).
        """
        if self.win.worker is None or not self.win.session.scene_session:
            return
        iid, instr = iid.strip(), instr.strip()
        if not INSTRUCTION_ID_RE.match(iid) or not instr:
            QMessageBox.warning(self.win, tr("지시문 오류"),
                                tr("지시문 ID 형식이 틀렸습니다 (예: I000)."))
            return
        # 계획이 있으면 계획의 (ID, 문장) 쌍만 적용 가능. 목록이 계획에서
        # 나오므로 정상 경로에서는 늘 통과하지만, 계획이 그 사이 바뀌었을
        # 수 있어 적용 시점에 다시 본다.
        plan = self.current_plan()
        sid = self.win.scene_ops.session_scene_id()
        if plan is not None and sid is not None:
            slots = plan.slots_for(sid)
            if slots and not any(x.instruction_id == iid
                                 and x.instruction == instr for x in slots):
                QMessageBox.warning(self.win, tr("지시문 오류"), tr(
                    "계획에 없는 지시문입니다 ({i}). 계획을 먼저 고치세요 "
                    "(② Configure > 수집 계획 ✎).").format(i=iid))
                return
        self.win.worker.cmd_set_slot(instr, iid)
        # cmd_set_slot 은 워커 큐로 가서 다음 드레인에 반영된다 -- 화면은
        # 사용자가 누른 값으로 즉시 갱신한다 (워커 값은 곧 같아진다).
        self.win.right_fields["ds_task"].setText(f"{iid}: {instr}")
        self.win.right_fields["ds_task"].setToolTip(f"{iid}: {instr}")
        self.win._recents.add("instruction_id", iid)
        self.win._recents.add("language", instr)
        # refresh_instruction 이 목록까지 함께 갱신한다.
        self.win.collection.refresh_instruction()

    def on_rank_selected(self) -> None:
        """Selecting a row draws its curves -- the point of the panel is that a
        number never decides on its own whether a take is bad."""
        items = self.win.rank_tree.selectedItems()
        if not items:
            return
        path, demo = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.win.stats_ops.show_analysis_for(path, demo)
        self.win.playback_ops.show_trim_for(path, demo)

    def session_instruction_counts(self) -> dict:
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
            if e.get("quality_status") == QUALITY_SUCCESS:
                c["usable"] += 1
        return counts

    def known_instructions(self, scene_id=None, scene_path=None, episodes=None) -> dict:
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

    @staticmethod
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
        known = self.known_instructions(scene_id, scene_path, episodes=episodes)
        for iid, s in known.items():
            if s == instr:
                if iid_edit.text().strip() != iid:
                    iid_edit.setText(iid)
                    self.win.log(f"[지시문] 아는 문장 -- {iid} 재사용")
                return
        cur = iid_edit.text().strip()
        nxt = self.next_iid(known)
        if cur in known and known[cur] != instr:
            iid_edit.setText(nxt)
            self.win.log(f"[지시문] 새 문장 -- {nxt} 자동 배정 ({cur} 는 이 scene 에서 사용 중)")
        elif not INSTRUCTION_ID_RE.match(cur) or cur not in known and cur != nxt:
            # 빈/이상한 값이거나, 이 scene 기준으로 뜬금없는 번호(예: 다른
            # scene 에서 넘어온 I003)면 이 scene 의 다음 번호로 정렬한다.
            iid_edit.setText(nxt)
            if cur and cur != nxt:
                self.win.log(f"[지시문] 새 문장 -- {nxt} 자동 배정")
