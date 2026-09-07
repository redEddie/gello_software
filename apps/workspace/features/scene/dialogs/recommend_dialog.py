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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.shared.widgets import SceneInfoView
from gello.gui.i18n import tr
from gello.scene.collection_plan import load_plan
from gello.scene.scene_diversity import AXES, recommend_detailed, recommend_placement
from gello.scene.scene_format import INSTRUCTION_ID_RE, SceneMetadata, describe_scene
from gello.scene.scene_rules import violations_by_section
from gello.scene.skill_stats import (
    collected_skill_counts,
    format_skill_counts,
    rank_instructions,
)

AXIS_KO = {
    "category": "물체 종류",
    "color": "색",
    "position": "위치",
    "relation": "관계",
}

# 버킷 색의 뜻은 그대로: 멀수록 초록(새롭다), 가까울수록 붉음(비슷하다).
# 값은 밝은 테마용 -- 옅게 물들인 바탕 + 진한 글자. 앞 팔레트(어두운 바탕 +
# 밝은 글자)는 밝은 창(#efefef)에서 뱃지만 혼자 튀었다.
_BUCKET_STYLE = {
    "원거리": ("#dff0e4", "#1e6b38", "#a8d5b8"),
    "중간":   ("#f6efd0", "#7a6413", "#ddcd8e"),
    "근거리": ("#f9e0df", "#8c2f2b", "#e8b3b0"),
}
_WEAK_STYLE = ("#e3e6f7", "#37407e", "#b9c0e4")
_SKILL_STYLE = ("#dfeaf5", "#1f5c85", "#aecbe0")

# 창 팔레트가 밝다(Window #efefef, 글자 검정). 카드만 어두우면 대화상자에서
# 혼자 튀고 글자가 안 읽힌다 (2026-09-06 조작자). 기본은 흰 바탕에 옅은
# 테두리, 고른 것만 초록 테두리 + 아주 옅은 초록 바탕으로 구분한다.
_CARD_DEF = ("_CardFrame{border:1px solid #c9c9c9; border-radius:6px;"
             " background:#ffffff;}")
_CARD_SEL = ("_CardFrame{border:2px solid #2e7d46; border-radius:6px;"
             " background:#eef7f0;}")


def _badge(text: str, bg: str, fg: str, border: str) -> QLabel:
    lab = QLabel(text)
    # 테두리 색을 인자로 받는다 -- 전에는 "{fg}44" 로 알파를 뒤에 붙였는데,
    # Qt 는 8자리 hex 를 #AARRGGBB(알파가 앞)로 읽어서 전혀 다른 색이 됐다
    # (#82c99a44 -> rgba(201,154,68,130)). 2026-09-07.
    lab.setStyleSheet(
        f"background:{bg}; color:{fg}; border:1px solid {border};"
        " border-radius:8px; padding:1px 8px; font-size:11px;")
    lab.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return lab


def _bucket_badge(bucket: str) -> QLabel:
    bg, fg, border = _BUCKET_STYLE.get(bucket, ("#eeeeee", "#555555", "#cccccc"))
    return _badge(bucket, bg, fg, border)


class _CardFrame(QFrame):
    """클릭하면 선택되는 카드 -- 세로로 쌓으면 비교가 아니라 스크롤하며
    기억하기가 되므로, 가로로 나란히 놓고 카드게임처럼 눌러서 고른다
    (2026-09-06 조작자)."""

    clicked = pyqtSignal()

    def mousePressEvent(self, e) -> None:
        self.clicked.emit()
        super().mousePressEvent(e)


class RecommendWorker(QThread):
    """scene 추천 계산을 GUI 스레드 밖에서 수행한다."""

    # QThread 자체의 finished 시그널을 가리면 안 되므로(수명 관리가 그걸 쓴다)
    # 결과 시그널은 recs_ready 로 명명한다 -- 리포의 다른 QThread 들(loaded,
    # frame_ready, cloud_ready)과 같은 관례.
    recs_ready = pyqtSignal(list, object)   # (detailed 추천, 스킬 Counter)
    error = pyqtSignal(str)

    def __init__(self, existing: list, props: dict, k: int,
                 seed: int, scene_id: str,
                 data_root: "Path | None" = None,
                 objects: "list | None" = None) -> None:
        super().__init__()
        self._existing = existing
        self._props = props
        self._k = k
        self._seed = seed
        self._scene_id = scene_id
        self._data_root = data_root
        self._objects = objects

    def run(self) -> None:
        try:
            # 스킬별 누적 수집량 -- 지시문 랭킹용. HDF5 IO 라 워커에서 센다.
            counts = collected_skill_counts(self._data_root)
            if self._objects:
                # 물체는 사람이 골랐다 -- 배치만 추천한다. 후보는 무작위
                # 표본이 아니라 규칙을 만족하는 배치 전부다.
                recs = recommend_placement(
                    self._objects, self._existing, self._props, k=self._k,
                    seed=self._seed, scene_id=self._scene_id)
            else:
                recs = recommend_detailed(self._existing, self._props,
                                          k=self._k, seed=self._seed,
                                          scene_id=self._scene_id)
            self.recs_ready.emit(recs, counts)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


class RecommendDialog(QDialog):
    """2단계 마법사: ① 배치 카드 3장 중 하나 고르기 -> ② 문장 체크 + 계획 등록."""

    def __init__(self, parent, existing: list, props: dict,
                 scene_id: str, plan_path: "Path | None" = None,
                 data_root: "Path | None" = None,
                 objects: "list | None" = None) -> None:
        super().__init__(parent)
        # objects 가 주어지면 조합은 사람이 고른 것이고 배치만 추천한다.
        self._objects = list(objects) if objects else None
        if self._objects:
            self.setWindowTitle(
                tr("배치 추천 — 물체 {m}개 고정, 기존 {n}개 기준")
                .format(m=len(self._objects), n=len(existing)))
        else:
            self.setWindowTitle(
                tr("scene 추천 — 기존 {n}개 기준 (거리 버킷 + 커버리지)")
                .format(n=len(existing)))
        # 가로 3열 카드 배치로 바뀌었다 -- 넓고 낮아야 기본 크기에서 1단계가
        # 스크롤 없이 다 보인다.
        self.setMinimumSize(900, 560)
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
        self._card_widgets: list = []
        self._sentence_checks: list[list[QCheckBox]] = []
        self._worker: RecommendWorker | None = None
        # 아직 도는 옛 워커들의 파이썬 참조. 참조를 버리면 GC 가 실행 중
        # QThread 를 파괴해 "Destroyed while thread is still running" 으로
        # 프로세스가 abort 한다 -- 결과는 워커 정체성 비교로 무시하고,
        # 참조는 스레드가 끝날 때(finished) 거둔다.
        self._stale_workers: list[RecommendWorker] = []

        col = QVBoxLayout(self)
        self._stack = QStackedWidget()
        col.addWidget(self._stack, 1)

        # ---- 1단계: 배치(레이아웃) 고르기 ----
        page0 = QWidget()
        p0 = QVBoxLayout(page0)
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
        p0.addLayout(top)

        # 배치로는 고칠 수 없는 위반은 여기서 말해 준다 -- 배치안만 보여주고
        # 침묵하면 조작자는 규칙에 맞는 조합인 줄 안다.
        warn = self._compose_warning()
        if warn:
            wl = QLabel(tr("물체 구성 경고 (배치로는 고칠 수 없습니다): {v}")
                        .format(v="; ".join(warn)))
            wl.setWordWrap(True)
            wl.setStyleSheet("color:#e67e22;")
            p0.addWidget(wl)

        # 거리 숫자의 눈금 설명은 카드마다가 아니라 카드 줄 위에 한 번만 둔다.
        # 눈금 설명은 부연 -- 본문보다 한 단계 옅게. 다만 #888 은 밝은 바탕에서
        # 대비 미달이라 #777 까지만 내린다.
        hint = QLabel(tr("0=기존과 같음, 1=완전히 다름"))
        hint.setStyleSheet("color:#666; font-size:11px;")
        p0.addWidget(hint)

        scroll0 = QScrollArea()
        scroll0.setWidgetResizable(True)
        scroll0.setFrameShape(QFrame.Shape.NoFrame)
        self._cards = QWidget()
        self._cards_col = QVBoxLayout(self._cards)
        scroll0.setWidget(self._cards)
        p0.addWidget(scroll0, 1)
        self._stack.addWidget(page0)

        # ---- 2단계: 문장 고르기 ----
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        head_w = QWidget()
        self._head_row = QHBoxLayout(head_w)
        self._head_row.setContentsMargins(0, 0, 0, 0)
        p1.addWidget(head_w)
        p1.addWidget(
            QLabel(tr("이 배치에서 시킬 수 있는 문장 — 수집이 적은 스킬부터")))
        # 길어질 수 있는 것은 문장 목록뿐이다 -- 스크롤은 여기만.
        scroll1 = QScrollArea()
        scroll1.setWidgetResizable(True)
        scroll1.setFrameShape(QFrame.Shape.NoFrame)
        self._sent_stack = QStackedWidget()
        scroll1.setWidget(self._sent_stack)
        p1.addWidget(scroll1, 1)

        if self._plan_path is not None:
            self._register_check = QCheckBox(
                tr("채택 시 선택한 문장을 지시문 파일 {n} 에 등록 (target=10)")
                .format(n=self._plan_path.name))
            self._register_check.setChecked(True)
        else:
            # 계획이 없으면 등록할 곳이 없다. 그래도 체크박스를 **보여준다** --
            # 숨기면 조작자는 추천을 채택하고도 문장이 어디에도 안 남은 것을
            # 한참 뒤에야 안다 (2026-09-04 에 실제로 그렇게 됐다). 왜 못 하는지
            # 와 어떻게 해야 하는지를 그 자리에서 말한다.
            self._register_check = QCheckBox(
                tr("지시문에 등록 — Configure 에서 지시문 파일을 먼저 고르세요"))
            self._register_check.setChecked(False)
            self._register_check.setEnabled(False)
            self._register_check.setStyleSheet("color:#e67e22;")
            self._register_check.setToolTip(tr(
                "지금은 지시문 파일이 선택돼 있지 않아 문장을 등록할 곳이 없습니다. "
                "채택해도 배치만 반영되고 문장은 남지 않습니다."))
        # 등록 체크박스는 문장과 함께 있어야 뜻이 통한다 -- 2단계에만 둔다.
        p1.addWidget(self._register_check)
        self._stack.addWidget(page1)

        # 버튼은 페이지에 따라 바뀐다 -- QDialogButtonBox 대신 직접 놓는 편이
        # 페이지별 배선이 읽기 쉽다.
        self._btn_group = QButtonGroup(self)
        btns = QHBoxLayout()
        self.back_btn = QPushButton(tr("← 뒤로"))
        self.back_btn.clicked.connect(lambda: self._show_page(0))
        btns.addWidget(self.back_btn)
        btns.addStretch(1)
        cancel_btn = QPushButton(tr("취소"))
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        self.next_btn = QPushButton(tr("다음 →"))
        self.next_btn.setEnabled(False)     # 추천이 도착하기 전엔 갈 곳이 없다
        self.next_btn.clicked.connect(self._next)
        btns.addWidget(self.next_btn)
        self.accept_btn = QPushButton(tr("채택"))
        self.accept_btn.clicked.connect(self._accept)
        btns.addWidget(self.accept_btn)
        col.addLayout(btns)
        self._fill()

    def _compose_warning(self) -> list:
        """사람이 고른 조합의 구성 규칙 위반 (배치와 무관한 것들)."""
        if not self._objects:
            return []
        probe = SceneMetadata(
            scene_id=self._scene_id, objects=list(self._objects),
            layout={"grid": [3, 3],
                    "placements": {o: {"zone": [i // 3, i % 3]}
                                   for i, o in enumerate(self._objects)}})
        try:
            return violations_by_section(probe, self._props)["compose"]
        except Exception:  # noqa: BLE001 -- 경고를 못 만들어도 추천은 보여준다
            return []

    def _clear_cards(self) -> None:
        while self._cards_col.count():
            it = self._cards_col.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        while self._sent_stack.count():
            w = self._sent_stack.widget(0)
            self._sent_stack.removeWidget(w)
            w.deleteLater()
        self._radios = []
        self._card_widgets = []
        self._sentence_checks = []

    def _fill(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            # 강제 중단하지 않는다 (recommend() 는 인터럽트를 보지 않는다) --
            # 참조만 보관해 GC 파괴를 막고, 낡은 결과는 정체성 비교로 버린다.
            self._stale_workers.append(self._worker)
        # 다시 추천은 항상 1단계(배치)부터 -- 카드가 바뀌는데 문장 화면에
        # 남아 있으면 고른 배치와 문장이 어긋난다.
        self._show_page(0)
        self.next_btn.setEnabled(False)
        self._clear_cards()
        self.again_btn.setEnabled(False)
        self.status_label.setText(tr("추천 계산 중..."))
        w = RecommendWorker(
            self._existing, self._props, k=3,
            seed=self.seed_spin.value(), scene_id=self._scene_id,
            data_root=self._data_root, objects=self._objects)
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
        if self._recs:
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            for i, rec in enumerate(self._recs, 1):
                row.addWidget(self._build_card(i, rec), 1)
            self._cards_col.addWidget(row_w)
            self._restyle_cards()
        else:
            note = QLabel(
                tr("규칙을 만족하는 배치를 찾지 못했습니다 — 소품 조합을 바꿔 "
                   "보세요 (키 큰 소품은 열을 통째로 비웁니다).")
                if self._objects else
                tr("추천 후보를 만들지 못했습니다 — 인벤토리와 규칙을 "
                   "확인하세요."))
            note.setWordWrap(True)
            note.setStyleSheet("color:#e67e22;")
            self._cards_col.addWidget(note)
        # 문장 체크박스는 1단계가 끝나기 전에 모두 만들어 둔다 -- 2단계는
        # 보여주기만 하고, 인수 테스트가 결과 직후에 이 목록을 읽는다.
        if self._objects is None:
            for rec in self._recs:
                page = QWidget()
                pc = QVBoxLayout(page)
                self._sentence_checks.append(
                    self._build_sentence_checks(rec["md"], counts, pc))
                pc.addStretch(1)
                self._sent_stack.addWidget(page)
        elif self._recs:
            # 배치만 추천할 때 문장은 세 안이 모두 같다 (지시문은 존을 보지
            # 않는다) -- 페이지 하나만 만들고 같은 리스트를 세 번 담는다.
            page = QWidget()
            pc = QVBoxLayout(page)
            checks = self._build_sentence_checks(
                self._recs[0]["md"], counts, pc)
            pc.addStretch(1)
            self._sent_stack.addWidget(page)
            self._sentence_checks = [checks for _ in self._recs]
        if counts:
            summary = QLabel(tr("스킬별 누적 수집 (적은 순): {s}")
                             .format(s=format_skill_counts(counts)))
            summary.setStyleSheet("color:#666;")
            summary.setWordWrap(True)
            self._cards_col.addWidget(summary)
        self._cards_col.addStretch(1)
        # 추천이 0개면 2단계로 갈 수 없다.
        self.next_btn.setEnabled(bool(self._recs))

    def _build_card(self, i: int, rec: dict) -> _CardFrame:
        """추천 한 장을 카드로 -- 축별 숫자 나열 대신 뱃지 + 한 문장만 띄운다
        (숫자 넷은 읽히지 않고 그냥 넘어간다, 2026-09-06 조작자)."""
        card = _CardFrame()
        cc = QVBoxLayout(card)
        head = QHBoxLayout()
        title = QLabel(tr("추천 {i}").format(i=i))
        title.setStyleSheet("font-weight:bold;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(_bucket_badge(rec["bucket"]))
        cc.addLayout(head)
        # 카드에서 가장 먼저 읽는 숫자라 진하게.
        diff = QLabel(tr("기존과 {d:.2f} 다름").format(d=rec["min_dist"]))
        diff.setStyleSheet("color:#222; font-weight:bold;")
        cc.addWidget(diff)
        view = SceneInfoView()
        view.setText(describe_scene(rec["md"]))
        cc.addWidget(view, 1)
        ax = rec.get("axes", {})
        best = None
        for a in AXES:
            if ax.get(a) is not None and (best is None or ax[a] > ax[best]):
                best = a
        if best is not None:
            # 부연 정보라 본문보다 한 단계 낮춘다.
            best_lab = QLabel(tr("가장 다른 점: {a}").format(a=AXIS_KO[best]))
            best_lab.setStyleSheet("color:#555;")
            cc.addWidget(best_lab)
        wk = rec.get("weak_axis")
        if wk in AXIS_KO:
            # 모르는 축 이름은 그대로 화면에 내지 않는다.
            wr = QHBoxLayout()
            wr.addWidget(_badge(tr("{a} 보강").format(a=AXIS_KO[wk]),
                                *_WEAK_STYLE))
            wr.addStretch(1)
            cc.addLayout(wr)
        # 축별 숫자는 근거를 찾는 사람을 위해 지우지 않고 툴팁에만 남긴다.
        card.setToolTip("  ".join(
            f"{a}={ax[a]:.2f}" if ax.get(a) is not None else f"{a}=--"
            for a in AXES))
        # 계약: 선택 상태는 숨은 QRadioButton 이 들고 있고, 인수 테스트가
        # 이 리스트를 직접 만진다. 카드 클릭이 곧 라디오 체크다.
        rb = QRadioButton(card)
        rb.setVisible(False)
        self._btn_group.addButton(rb)
        rb.setChecked(i == 1)
        self._radios.append(rb)
        self._card_widgets.append(card)
        card.clicked.connect(lambda rb=rb: rb.setChecked(True))
        rb.toggled.connect(lambda _c=False: self._restyle_cards())
        return card

    def _restyle_cards(self) -> None:
        for rb, card in zip(self._radios, self._card_widgets):
            card.setStyleSheet(_CARD_SEL if rb.isChecked() else _CARD_DEF)

    def _show_page(self, i: int) -> None:
        """2단계 마법사의 페이지 전환 -- 버튼도 페이지를 따라간다."""
        self._stack.setCurrentIndex(i)
        on_sent = i == 1
        self.back_btn.setVisible(on_sent)
        self.accept_btn.setVisible(on_sent)
        self.next_btn.setVisible(not on_sent)

    def _selected_index(self) -> int:
        for i, rb in enumerate(self._radios):
            if rb.isChecked():
                return i
        return -1

    def _next(self) -> None:
        idx = self._selected_index()
        if idx < 0:
            return
        # 배치만 추천할 때는 문장 페이지가 하나뿐이다 (세 안이 공유).
        self._sent_stack.setCurrentIndex(idx if self._objects is None else 0)
        self._fill_step2_head(idx)
        self._show_page(1)

    def _fill_step2_head(self, idx: int) -> None:
        """2단계 머리말 = 고른 카드의 요약 (번호·scene id·버킷·보강 축)."""
        while self._head_row.count():
            it = self._head_row.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        rec = self._recs[idx]
        lab = QLabel(tr("추천 {i} · {sid}")
                     .format(i=idx + 1, sid=rec["md"].scene_id))
        lab.setStyleSheet("font-weight:bold;")
        self._head_row.addWidget(lab)
        self._head_row.addWidget(_bucket_badge(rec["bucket"]))
        wk = rec.get("weak_axis")
        if wk in AXIS_KO:
            self._head_row.addWidget(
                _badge(tr("{a} 보강").format(a=AXIS_KO[wk]), *_WEAK_STYLE))
        self._head_row.addStretch(1)

    def _build_sentence_checks(self, md, counts, into) -> list:
        """문장 체크리스트를 into 레이아웃에 만들고 체크박스 목록을 준다."""
        ranked = rank_instructions(md, self._props, counts or {})
        checks: list[QCheckBox] = []
        if not ranked:
            note = QLabel(tr("(문법상 생성 가능한 문장이 없음)"))
            note.setStyleSheet("color:#666;")
            into.addWidget(note)
            return checks
        for s, sk, n in ranked:
            cb = QCheckBox(s)
            cb.setChecked(True)
            cb.setEnabled(self._plan_path is not None)
            cb.setToolTip(tr("스킬 {sk} · 지금까지 {n} 에피소드 수집")
                          .format(sk=sk, n=n))
            checks.append(cb)
            into.addWidget(cb)
            # 스킬과 누적 수집량은 뱃지 + 한 줄 텍스트로 -- 체크박스 문장에
            # 붙이면 문장이 길어져 읽기 어렵다.
            sub = QHBoxLayout()
            sub.setContentsMargins(28, 0, 0, 6)
            sub.addWidget(_badge(sk, *_SKILL_STYLE))
            # #888 은 밝은 바탕에서 대비 미달(2.9:1)이라 #666 으로.
            cnt = QLabel(tr("{n}개 수집").format(n=n))
            cnt.setStyleSheet("color:#666;")
            sub.addWidget(cnt)
            sub.addStretch(1)
            into.addLayout(sub)
        return checks

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
            QMessageBox.warning(self, tr("지시문 읽기 실패"), str(e))
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
                self, tr("지시문에 등록"),
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
                self, tr("지시문 등록 실패"),
                tr("load_plan 검증을 통과하지 못했습니다:\n{e}").format(e=e))
            return False
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        if plan.warnings:
            # 통일 문법 경고(§4)는 등록을 막지 않지만 버리지도 않는다 --
            # PlanEditDialog 저장 경로와 같은 규칙.
            QMessageBox.warning(self, tr("지시문 경고"),
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
                    self, tr("지시문에 등록"),
                    tr("{n}개 문장 × target 10 = 총 {t} 에피소드를 {sid} 에 "
                       "등록합니다. 진행할까요?")
                    .format(n=len(sents), t=len(sents) * 10, sid=md.scene_id),
            ) != QMessageBox.StandardButton.Yes:
                sents = []
            if sents and self._register_plan(md, sents):
                dup = getattr(self, "_n_dup_skipped", 0)
                QMessageBox.information(
                    self, tr("지시문 등록 완료"),
                    tr("{n}개 문장을 {sid} 에 등록했습니다.{d}")
                    .format(n=len(sents) - dup, sid=md.scene_id,
                            d=tr(" (중복 {k}건 건너뜀)").format(k=dup) if dup else ""))
        super().accept()


