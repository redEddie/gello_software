"""Scene recommendation dialog."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.shared.widgets import SceneInfoView
from gello.gui.i18n import tr
from gello.scene.collection_plan import load_plan
from gello.scene.scene_diversity import AXES, recommend_detailed
from gello.scene.scene_format import INSTRUCTION_ID_RE, SceneMetadata, describe_scene
from gello.scene.skill_stats import (
    collected_skill_counts,
    format_skill_counts,
    rank_instructions,
)


class RecommendWorker(QThread):
    """scene 추천 계산을 GUI 스레드 밖에서 수행한다."""

    # QThread 자체의 finished 시그널을 가리면 안 되므로(수명 관리가 그걸 쓴다)
    # 결과 시그널은 recs_ready 로 명명한다 -- 리포의 다른 QThread 들(loaded,
    # frame_ready, cloud_ready)과 같은 관례.
    recs_ready = pyqtSignal(list, object)   # (detailed 추천, 스킬 Counter)
    error = pyqtSignal(str)

    def __init__(self, existing: list, props: dict, k: int,
                 seed: int, scene_id: str,
                 data_root: "Path | None" = None) -> None:
        super().__init__()
        self._existing = existing
        self._props = props
        self._k = k
        self._seed = seed
        self._scene_id = scene_id
        self._data_root = data_root

    def run(self) -> None:
        try:
            # 스킬별 누적 수집량 -- 지시문 랭킹용. HDF5 IO 라 워커에서 센다.
            counts = collected_skill_counts(self._data_root)
            recs = recommend_detailed(self._existing, self._props, k=self._k,
                                      seed=self._seed,
                                      scene_id=self._scene_id)
            self.recs_ready.emit(recs, counts)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


class RecommendDialog(QDialog):
    """scene 다양성 추천안 3개 중 하나 고르기 + 문장 체크리스트 + 계획 등록."""

    def __init__(self, parent, existing: list, props: dict,
                 scene_id: str, plan_path: "Path | None" = None,
                 data_root: "Path | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("scene 추천 — 기존 {n}개 기준 (거리 버킷 + 커버리지)")
                            .format(n=len(existing)))
        self.setMinimumSize(620, 720)
        self._existing = existing
        self._props = props
        self._scene_id = scene_id
        self._plan_path = plan_path
        self._data_root = data_root
        self._skill_counts = None          # 워커가 채움 (Counter)
        self.picked = None                 # accept 시 SceneMetadata
        self.registered_plan_path: "Path | None" = None  # 등록 성공 시 경로
        self._recs: list = []
        self._radios: list = []
        self._sentence_checks: list[list[QCheckBox]] = []
        self._worker: RecommendWorker | None = None
        # 아직 도는 옛 워커들의 파이썬 참조. 참조를 버리면 GC 가 실행 중
        # QThread 를 파괴해 "Destroyed while thread is still running" 으로
        # 프로세스가 abort 한다 -- 결과는 워커 정체성 비교로 무시하고,
        # 참조는 스레드가 끝날 때(finished) 거둔다.
        self._stale_workers: list[RecommendWorker] = []

        col = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("seed")))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 9999)
        top.addWidget(self.seed_spin)
        self.again_btn = QPushButton(tr("다시 추천"))
        self.again_btn.clicked.connect(self._fill)
        top.addWidget(self.again_btn)
        self.status_label = QLabel("")
        top.addWidget(self.status_label, 1)
        top.addStretch(1)
        col.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards = QWidget()
        self._cards_col = QVBoxLayout(self._cards)
        scroll.setWidget(self._cards)
        col.addWidget(scroll, 1)

        if self._plan_path is not None:
            self._register_check = QCheckBox(
                tr("채택 시 선택한 문장을 계획 {n} 에 등록 (target=10)")
                .format(n=self._plan_path.name))
            self._register_check.setChecked(True)
            col.addWidget(self._register_check)
        else:
            # 계획이 없으면 등록할 곳이 없다. 그래도 체크박스를 **보여준다** --
            # 숨기면 조작자는 추천을 채택하고도 문장이 어디에도 안 남은 것을
            # 한참 뒤에야 안다 (2026-09-04 에 실제로 그렇게 됐다). 왜 못 하는지
            # 와 어떻게 해야 하는지를 그 자리에서 말한다.
            self._register_check = QCheckBox(
                tr("계획에 등록 — Configure 에서 계획 파일을 먼저 고르세요"))
            self._register_check.setChecked(False)
            self._register_check.setEnabled(False)
            self._register_check.setStyleSheet("color:#e67e22;")
            self._register_check.setToolTip(tr(
                "지금은 계획이 선택돼 있지 않아 문장을 등록할 곳이 없습니다. "
                "채택해도 배치만 반영되고 문장은 남지 않습니다."))
            col.addWidget(self._register_check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._fill()

    def _clear_cards(self) -> None:
        while self._cards_col.count():
            it = self._cards_col.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self._radios = []
        self._sentence_checks = []

    def _fill(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            # 강제 중단하지 않는다 (recommend() 는 인터럽트를 보지 않는다) --
            # 참조만 보관해 GC 파괴를 막고, 낡은 결과는 정체성 비교로 버린다.
            self._stale_workers.append(self._worker)
        self._clear_cards()
        self.again_btn.setEnabled(False)
        self.status_label.setText(tr("추천 계산 중..."))
        w = RecommendWorker(
            self._existing, self._props, k=3,
            seed=self.seed_spin.value(), scene_id=self._scene_id,
            data_root=self._data_root)
        self._worker = w
        w.recs_ready.connect(
            lambda recs, counts, w=w: self._on_recs_ready(w, recs, counts))
        w.error.connect(lambda msg, w=w: self._on_recs_error(w, msg))
        w.finished.connect(lambda w=w: self._reap(w))
        w.start()

    def _reap(self, w: RecommendWorker) -> None:
        """끝난 옛 워커의 참조 회수 (QThread 기본 finished 시그널 경유)."""
        if w in self._stale_workers and not w.isRunning():
            self._stale_workers.remove(w)

    def _wait_workers(self) -> None:
        """다이얼로그가 닫히기 전에 도는 워커를 기다린다 -- 다이얼로그 소멸과
        함께 워커가 GC 되면 실행 중 파괴로 abort 한다."""
        for w in [self._worker, *self._stale_workers]:
            if w is not None and w.isRunning():
                w.wait(10000)

    def done(self, r: int) -> None:  # accept/reject/close 공통 경유지
        self._wait_workers()
        super().done(r)

    def _on_recs_error(self, w: RecommendWorker, msg: str) -> None:
        if w is not self._worker:
            return                       # 낡은 워커의 결과 -- 무시
        self.status_label.setText(tr("오류: {m}").format(m=msg))
        self.again_btn.setEnabled(True)
        self._worker = None

    def _on_recs_ready(self, w: RecommendWorker, recs: list,
                       counts) -> None:
        if w is not self._worker:
            return                       # 낡은 워커의 결과 -- 무시
        self._worker = None
        self.again_btn.setEnabled(True)
        self.status_label.setText("")
        self._recs = recs
        self._skill_counts = counts
        group = QButtonGroup(self)
        for i, rec in enumerate(self._recs, 1):
            md = rec["md"]
            box = QGroupBox()
            bc = QVBoxLayout(box)
            rb = QRadioButton(tr("추천 {i} — {b} 변형 · 기존과의 최소 거리 {d}")
                              .format(i=i, b=rec["bucket"], d=rec["min_dist"]))
            group.addButton(rb)
            rb.setChecked(i == 1)
            self._radios.append(rb)
            bc.addWidget(rb)
            ax = rec.get("axes", {})
            ax_s = "  ".join(
                f"{a}={ax[a]:.2f}" if ax.get(a) is not None else f"{a}=--"
                for a in AXES)
            why = QLabel(tr("축별 최소 거리: {ax} · 커버리지 보강 축: {wk}")
                         .format(ax=ax_s, wk=rec.get("weak_axis", "?")))
            why.setStyleSheet("color:#888;")
            why.setWordWrap(True)
            bc.addWidget(why)
            view = SceneInfoView()
            view.setText(describe_scene(md))
            bc.addWidget(view)

            ranked = rank_instructions(md, self._props, counts or {})
            checks: list[QCheckBox] = []
            if ranked:
                bc.addWidget(QLabel(
                    tr("추천 문장 — 수집이 적은 스킬 우선 (채택 시 등록됨):")))
                for s, sk, n in ranked:
                    cb = QCheckBox(s)
                    cb.setChecked(True)
                    cb.setEnabled(self._plan_path is not None)
                    cb.setToolTip(tr("스킬 {sk} · 지금까지 {n} 에피소드 수집")
                                  .format(sk=sk, n=n))
                    checks.append(cb)
                    bc.addWidget(cb)
            else:
                note = QLabel(tr("(문법상 생성 가능한 문장이 없음)"))
                note.setStyleSheet("color:#888;")
                bc.addWidget(note)
            self._sentence_checks.append(checks)
            self._cards_col.addWidget(box)
        if counts:
            summary = QLabel(tr("스킬별 누적 수집 (적은 순): {s}")
                             .format(s=format_skill_counts(counts)))
            summary.setStyleSheet("color:#888;")
            summary.setWordWrap(True)
            self._cards_col.addWidget(summary)
        self._cards_col.addStretch(1)

    def _selected_sentences(self, idx: int) -> list[str]:
        return [cb.text() for cb in self._sentence_checks[idx] if cb.isChecked()]

    def _register_plan(self, md: SceneMetadata, sentences: list[str]) -> bool:
        """선택한 문장을 plan_path 의 scene+slots 로 등록. load_plan 검증 통과."""
        if self._plan_path is None or not sentences:
            return False
        path = self._plan_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("계획 읽기 실패"), str(e))
            return False
        raw.setdefault("plan_version", 1)
        if not isinstance(raw.get("scenes"), list):
            raw["scenes"] = []
        by_sid = {s.get("scene_id"): s for s in raw["scenes"]}
        scene = by_sid.get(md.scene_id)
        if scene is None:
            scene = {"scene_id": md.scene_id, "slots": []}
            raw["scenes"].append(scene)
        used = {
            int(m.group(1))
            for sl in scene.get("slots", [])
            if (m := INSTRUCTION_ID_RE.match(str(sl.get("instruction_id", ""))))
        }
        # 같은 문장이 이미 있으면 새 ID 로 또 쌓지 않는다 -- load_plan 은
        # "같은 ID·다른 문장"만 막으므로 여기서 문장 기준으로 걸러야 한다.
        existing_sents = {str(sl.get("instruction", "")).strip()
                          for sl in scene.get("slots", [])}
        new_slots = []
        n_dup = 0
        for sent in sentences:
            if sent.strip() in existing_sents:
                n_dup += 1
                continue
            existing_sents.add(sent.strip())
            n = max(used, default=-1) + 1
            used.add(n)
            new_slots.append({
                "instruction_id": f"I{n:03d}",
                "instruction": sent,
                "target": 10,
            })
        if not new_slots:
            QMessageBox.information(
                self, tr("계획 등록"),
                tr("선택한 문장이 모두 이미 등록되어 있습니다 (중복 {n}건 건너뜀).")
                .format(n=n_dup))
            return False
        scene.setdefault("slots", []).extend(new_slots)

        # 검증 게이트 -- 실패해도 temp 파일은 남기지 않는다.
        tmp: "Path | None" = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as tf:
                tf.write(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
                tmp = Path(tf.name)
            plan = load_plan(tmp)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(
                self, tr("계획 등록 실패"),
                tr("load_plan 검증을 통과하지 못했습니다:\n{e}").format(e=e))
            return False
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        if plan.warnings:
            # 통일 문법 경고(§4)는 등록을 막지 않지만 버리지도 않는다 --
            # PlanEditDialog 저장 경로와 같은 규칙.
            QMessageBox.warning(self, tr("계획 경고"),
                                "\n".join(str(x) for x in plan.warnings))

        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        self._n_dup_skipped = n_dup
        self.registered_plan_path = path
        return True

    def _accept(self) -> None:
        idx = -1
        for i, rb in enumerate(self._radios):
            if rb.isChecked():
                idx = i
                break
        if idx < 0:
            return
        md = self._recs[idx]["md"]
        self.picked = md
        if self._register_check is not None and self._register_check.isChecked():
            sents = self._selected_sentences(idx)
            # 문장 수 × target 이 곧 수집량이다 -- 물체 5개 scene 은 문장이
            # 20개를 넘을 수 있어, 무심코 OK 한 번에 200 에피소드가 계획에
            # 얹히는 것을 총량 확인으로 막는다.
            if sents and QMessageBox.question(
                    self, tr("계획 등록"),
                    tr("{n}개 문장 × target 10 = 총 {t} 에피소드를 {sid} 에 "
                       "등록합니다. 진행할까요?")
                    .format(n=len(sents), t=len(sents) * 10, sid=md.scene_id),
            ) != QMessageBox.StandardButton.Yes:
                sents = []
            if sents and self._register_plan(md, sents):
                dup = getattr(self, "_n_dup_skipped", 0)
                QMessageBox.information(
                    self, tr("계획 등록 완료"),
                    tr("{n}개 문장을 {sid} 에 등록했습니다.{d}")
                    .format(n=len(sents) - dup, sid=md.scene_id,
                            d=tr(" (중복 {k}건 건너뜀)").format(k=dup) if dup else ""))
        super().accept()


