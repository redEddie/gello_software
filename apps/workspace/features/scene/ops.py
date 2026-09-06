"""Scene configuration and core scene identity operations."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from gello.config.station import load_station
from gello.scene.dataset_meta import plan_path as dataset_plan_path
from apps.workspace.shared.tabs import show_center_tab
from gello.gui.i18n import tr
from gello.scene.scene_format import (
    INSTRUCTION_ID_RE,
    count_by_slot,
    describe_scene,
    iter_scene_files,
    next_scene_id,
    read_scene_metadata,
    scene_filename,
)

STATION = load_station()


class SceneOps:
    """Scene configuration, file identity, and right-panel metadata."""

    def __init__(self, win) -> None:
        self.win = win

    def refresh_scene_combo(self) -> None:
        """저장 경로의 scene_*.hdf5 목록. 파일명이 아니라 내부 metadata 로
        표시한다 (경로 역산 금지)."""
        self.win.scene_combo.blockSignals(True)
        self.win.scene_combo.clear()
        root = Path(self.win.root_edit.text().strip() or ".")
        try:
            sid_next = next_scene_id(root)
        except Exception:  # noqa: BLE001
            sid_next = "S???"
        self.win.scene_combo.addItem(tr("— 새 Scene ({sid}) —").format(sid=sid_next), None)
        try:
            for p in iter_scene_files(root):
                try:
                    md = read_scene_metadata(p)
                except Exception as e:  # noqa: BLE001
                    self.win.scene_combo.addItem(f"{p.name} (읽기 실패: {type(e).__name__})", None)
                    continue
                label = f"{md.scene_id} · 물체 {len(md.objects)}개"
                if md.description:
                    label += f" · {md.description[:28]}"
                self.win.scene_combo.addItem(label, md.scene_id)
        except Exception:  # noqa: BLE001
            pass
        self.win.scene_combo.blockSignals(False)
        # 저장 경로가 바뀌었을 수 있으니 데이터셋 귀속 계획(instructions.json)
        # 표시·slot 패널·scene 정보를 함께 갱신한다.
        self.win.scene_planning.on_plan_changed()

    def on_scene_selected(self, *_args) -> None:
        self.win.scene_planning.refresh_start_instruction()
        self.win.collection.refresh_instruction()
        sid = self.win.scene_combo.currentData()
        if sid is None:
            if self.win._pending_scene_meta is not None:
                self.win.scene_info.setText(
                    describe_scene(self.win._pending_scene_meta)
                    + "\n" + tr("(연결하면 이 구성으로 새 scene 파일이 만들어집니다)"))
            else:
                self.win.scene_info.setText(
                    tr("Scene 탭에서 물체 배치를 정하세요."))
            return
        root = Path(self.win.root_edit.text().strip() or ".")
        try:
            path = root / scene_filename(sid)
            if self.win.session.active_file_path is not None and path == self.win.session.active_file_path:
                # 세션이 파일을 쥐고 있다 -- 캐시 요약으로 대신한다
                counts = self.win.scene_planning.session_instruction_counts()
                lines = [tr("{s} — 수집 세션 진행 중 (배치도는 오른쪽 패널에)")
                         .format(s=sid)]
                if counts:
                    lines.append("slot: " + "  ".join(
                        f"{iid} {c.get('usable', 0)}/{c.get('total', 0)}"
                        for iid, c in sorted(counts.items())))
                self.win.scene_info.setText("\n".join(lines))
                return
            md = read_scene_metadata(path)
            counts = count_by_slot(path)
            lines = [describe_scene(md)]
            if counts:
                lines.append("slot: " + "  ".join(
                    f"{iid} {c['usable']}/{c['total']}" for iid, c in sorted(counts.items())))
            plan = self.win.scene_planning.current_plan()
            if plan is not None and plan.slots_for(sid):
                lines.append(f"계획({plan.path.name}): " + "  ".join(
                    f"{s.instruction_id} {counts.get(s.instruction_id, {}).get('usable', 0)}"
                    f"/{s.target}" for s in plan.slots_for(sid)))
            self.win.scene_info.setText("\n".join(lines))
        except BlockingIOError:
            self.win.scene_info.setText(tr(
                "(다른 프로세스가 파일을 사용 중입니다 — 재압축/변환이 끝난 "
                "뒤 새로고침하세요)"))
        except Exception as e:  # noqa: BLE001
            self.win.scene_info.setText(f"(scene 정보 읽기 실패: {type(e).__name__}: {e})")

    def on_scene_activated(self, *_args) -> None:
        """드롭다운을 **사람이** 건드렸을 때만 온다 (``activated``).

        "— 새 Scene (Sxxx) —" 을 고르는 것이 곧 "새로 짜겠다"는 뜻이라 Scene
        탭을 연다 -- 옛 [새 Scene 구성...] 버튼을 대신한다.

        ``currentIndexChanged`` 가 아니라 ``activated`` 인 이유: 목록을 다시
        채울 때마다(경로 변경·새로고침·세션 종료) 인덱스는 0 으로 돌아가는데,
        그때마다 탭을 열면 보고 있던 화면을 빼앗는다 (실제로 그랬다).
        """
        if self.win.scene_combo.currentData() is None and self.win.worker is None:
            self.on_new_scene()

    def on_new_scene(self) -> None:
        """Scene 탭을 열고 다음 scene 번호로 맞춘다 (2026-09-06: 대화상자 ->
        탭). 파일은 아직 만들지 않는다 -- Connect 가 만든다."""
        root = Path(self.win.root_edit.text().strip() or ".")
        try:
            sid = next_scene_id(root)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self.win, tr("경로 오류"),
                                tr("저장 경로를 확인하세요: {e}").format(e=e))
            return
        # 계획은 데이터셋 폴더 안 instructions.json 하나뿐이다 (고정 파일명).
        pp = dataset_plan_path(root)
        self.win.scene_composer.set_context(
            sid, root, pp if pp.is_file() else None,
            STATION.name, self.win.schema_version)
        self.win.scene_compose_hint.setText(tr(
            "물체를 고르고 격자에 배치한 뒤 [이 구성으로 시작] 을 누르세요."))
        show_center_tab(self.win, "scene")

    def on_compose_done(self) -> None:
        """Scene 탭의 [이 구성으로 시작] -- 규칙을 통과하면 대기 scene 으로."""
        md = self.win.scene_composer.build_valid()
        if md is None:
            return
        self.win._pending_scene_meta = md
        # 드롭다운을 "— 새 Scene —" 로 돌려 놓는다 (그것이 이 구성의 자리다).
        if self.win.scene_combo.currentData() is not None:
            self.win.scene_combo.setCurrentIndex(0)
        self.on_scene_selected()
        self.win.scene_compose_hint.setText(tr(
            "{sid} 구성을 얹었습니다 — Connect 하면 이 배치로 파일이 만들어집니다."
        ).format(sid=md.scene_id))
        self.win.log(f"[Scene] {md.scene_id} 구성 준비 (물체 {len(md.objects)}개)")

    def scene_config_from_ui(self):
        """Connect 시점의 scene 설정 검증. (meta, scene_id, resume, error) --
        error 가 None 이 아니면 연결을 중단하고 그 메시지를 보여준다."""
        lang = self.win.lang_edit.text().strip()
        iid = self.win.scene_iid_edit.text().strip()
        collector = self.win.collector_edit.text().strip()
        if not lang:
            return None, None, False, tr(
                "시작 지시문이 없습니다 — Instruction 탭에서 줄을 눌러 고르세요.")
        if lang.startswith('"') and lang.endswith('"'):
            return None, None, False, tr("instruction 은 따옴표 없는 순수 문장이어야 합니다.")
        if not INSTRUCTION_ID_RE.match(iid):
            return None, None, False, tr("시작 지시문 ID 형식이 틀렸습니다 (예: I000).")
        if not collector:
            return None, None, False, tr("수집자 식별자를 입력하세요 (에피소드 필수 attr).")
        # **계획은 필수다** (2026-09-06 사용자 결정). 지시문은 데이터셋의
        # instructions.json 에서만 온다 -- 자유 입력이 계획 밖 지시문을
        # 실데이터에 만든 적이 있고(ID-문장 갈라짐), 그 길을 아예 없앴다.
        # 계획 없이 찍은 파일은 나중에 무엇을 얼마나 모았는지 셀 수가 없다.
        plan = self.win.scene_planning.current_plan()
        if plan is None:
            return None, None, False, tr(
                "이 데이터셋에는 수집 계획이 없습니다.\n\n"
                "Instruction 탭의 [새 계획] 으로 만든 뒤 [계획 편집] 에서 "
                "scene 과 지시문을 적으세요. 지시문은 계획에서만 옵니다.")
        psid = self.configure_scene_id()
        slots = plan.slots_for(psid) if psid else ()
        if not slots:
            return None, None, False, tr(
                "계획에 scene {s} 가 없습니다.\n\n"
                "Instruction 탭의 [계획 편집] 에서 이 scene 의 지시문을 "
                "추가하세요.").format(s=psid)
        if not any(s.instruction_id == iid and s.instruction == lang
                   for s in slots):
            return None, None, False, tr(
                "시작 지시문이 계획에 없습니다 ({i}: {t!r}).\n\n"
                "Instruction 탭에서 줄을 눌러 고르세요.").format(i=iid, t=lang[:40])
        sid = self.win.scene_combo.currentData()
        if sid is None:
            if self.win._pending_scene_meta is None:
                return None, None, False, tr(
                    "Scene 탭에서 배치를 먼저 정하거나, 드롭다운에서 기존 "
                    "scene 을 고르세요.")
            return self.win._pending_scene_meta, None, False, None
        return None, sid, True, None

    def configure_scene_id(self):
        """Configure 가 가리키는 scene ID -- 기존 선택이면 그것, 새 scene 이면
        구성해 둔 metadata 의 ID, 그것도 없으면 다음 발번 예정 ID."""
        sid = self.win.scene_combo.currentData()
        if sid is not None:
            return sid
        if self.win._pending_scene_meta is not None:
            return self.win._pending_scene_meta.scene_id
        try:
            return next_scene_id(Path(self.win.root_edit.text().strip() or "."))
        except Exception:  # noqa: BLE001
            return None

    def selected_scene_path(self):
        """Configure 의 Scene 콤보가 가리키는 기존 scene 파일 (새 scene 이면 None)."""
        sid = self.win.scene_combo.currentData()
        if sid is None:
            return None
        return Path(self.win.root_edit.text().strip() or ".") / scene_filename(sid)

    def session_scene_id(self):
        if self.win.worker is None:
            return None
        cfg = self.win.worker.cfg
        if getattr(cfg, "scene_metadata", None) is not None:
            return cfg.scene_metadata.scene_id
        return getattr(cfg, "scene_id", None)

    def scene_session_file(self):
        if not self.win.session.scene_session or self.win.session.active_file_path is None:
            return None
        return self.win.session.active_file_path

    def set_right_scene(self, md, sid=None) -> None:
        """오른쪽 패널의 '수집 중 scene 배치도'를 갱신한다."""
        if not hasattr(self.win, "right_scene_view"):
            return
        if md is not None:
            self.win.right_scene_view.setText(describe_scene(md))
        elif sid:
            self.win.right_scene_view.setText(
                tr("{s} — 배치 정보를 읽지 못했습니다").format(s=sid))
        else:
            self.win.right_scene_view.setText(tr("(scene 세션 없음)"))

    def apply_session_config(self, cfg: dict) -> list:
        """Puts a file's recorded session_config back into the widgets that
        produced it (see gello/collect/worker.py's record_session_config).

        Returns the labels of what was actually restored, so the hint can say
        what changed rather than claim more than it did -- older files were
        written before some of these keys existed.
        """
        done = []
        for key, combo, label in (("reset_pose", self.win.reset_pose_combo, tr("Reset pose")),
                                  ("grip", self.win.grip_combo, tr("Grip"))):
            val = cfg.get(key)
            if val is None:
                continue
            i = combo.findText(str(val))
            if i >= 0:
                combo.setCurrentIndex(i)
                done.append(label)
        for key, edit, label in (("max_episode_seconds", self.win.eplen_edit, tr("에피소드 길이")),
                                 ("reset_wait_seconds", self.win.resetwait_edit, tr("리셋 대기"))):
            val = cfg.get(key)
            if val is not None:
                edit.setText(str(int(val)))
                done.append(label)
        if cfg.get("enable_wall") is not None:
            self.win.wall_check.setChecked(bool(cfg["enable_wall"]))
            done.append(tr("관절 한계 벽"))
        return done

    # on_start_sentence_edited 를 지웠다 (2026-09-06). 시작 문장을 손으로
    # 치는 길이 없어졌기 때문이다 -- 계획이 필수가 되면서 지시문은
    # instructions.json 에서만 온다 (scene_config_from_ui 가 막는다).
