"""소품 고치기 -- 기록된 물체를 인벤토리에서 다시 고른다.

전에는 [이 정정 적용] 버튼 하나뿐이었다 (2026-09-07 사용자: "소품 수정하는
버튼이 없고 단순히 정정버튼만 있는데 이걸 누르면 뭐가 어떻게 될지 예상할
수도 없다"). 추천이 성립하는 경우는 좁아서 -- 문장이 만장일치로 다른 색을
말할 때뿐 -- 그 밖의 오등록은 화면에서 고칠 길이 아예 없었다.

여기서는 물체마다 콤보를 두고 인벤토리에서 고른다. 추천이 있으면 그것이
미리 골라져 있고 "추천" 이라고 적힌다 -- 한 번 누르면 되던 일이 여전히
한 번이고, 안 맞으면 다른 것을 고를 수 있다.

존은 따라간다. 물체를 바꾸는 것은 "이 자리에 있던 것이 무엇이었나" 를
고치는 일이지 물체를 옮기는 일이 아니다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from apps.workspace.features.doctor.confirm import ConfirmDialog, side_by_side
from apps.workspace.shared.sizing import roomy
from gello.gui.i18n import tr
from gello.scene.instruction_grammar import lint
from gello.scene.scene_format import SceneMetadata, describe_scene


def _label(prop) -> str:
    return f"{prop.color} {prop.category}  ({prop.id})"


class ObjectDialog(ConfirmDialog):
    """``changes`` 가 {옛 id: 새 id} (취소면 빈 dict).

    sentences 는 이 scene 의 지시문 목록 -- 고친 뒤에 규칙 위반이 몇 건
    남는지를 즉시 세어 보여주기 위한 것이다. 고쳐 놓고 나서 아는 것보다
    고르는 동안 아는 편이 낫다.
    """

    def __init__(self, parent, md: SceneMetadata, props: dict,
                 sentences: list, suggestion=None) -> None:
        self._md = md
        self._props = props
        self._sentences = list(sentences)
        self.changes: dict = {}

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 6)
        self._combos = {}
        active = [p for p in props.values() if not getattr(p, "retired", False)]
        for oid in md.objects:
            cur = props.get(oid)
            combo = QComboBox()
            # 같은 category 를 앞에 둔다 -- 오등록은 거의 언제나 같은 종류
            # 안에서 색을 잘못 적은 것이다 (S008: 초록 -> 회색 small_bowl).
            same = sorted((p for p in active
                           if cur is not None and p.category == cur.category),
                          key=lambda p: p.id)
            rest = sorted((p for p in active if p not in same),
                          key=lambda p: (p.category, p.id))
            for p in same + rest:
                combo.addItem(_label(p), p.id)
            idx = combo.findData(oid)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(self._redraw)
            roomy(combo)
            self._combos[oid] = combo
            cap = QLabel(tr("자리 {z}").format(
                z=md.layout.get("placements", {}).get(oid, {}).get("zone", "?")))
            form.addRow(cap, combo)
        col.addLayout(form)

        self._diff, self._now, self._after = side_by_side(mono=True)
        col.addWidget(self._diff, 1)

        super().__init__(parent, tr("소품 고치기 — {sid}").format(
            sid=md.scene_id), body, ok_text=tr("이대로 고치기"))

        if suggestion is not None:
            c = self._combos.get(suggestion.old_id)
            if c is not None:
                i = c.findData(suggestion.new_id)
                if i >= 0:
                    c.setItemText(i, c.itemText(i) + tr("  ← 추천"))
                    c.setCurrentIndex(i)
        self._redraw()

    # ------------------------------------------------------------------
    def _picked(self) -> dict:
        out = {}
        for oid, combo in self._combos.items():
            new = combo.currentData()
            if new and new != oid:
                out[oid] = new
        return out

    def _after_md(self, changes: dict) -> SceneMetadata:
        objects = [changes.get(o, o) for o in self._md.objects]
        pl = {}
        for o, v in self._md.layout.get("placements", {}).items():
            pl[changes.get(o, o)] = v
        layout = dict(self._md.layout)
        layout["placements"] = pl
        # 안 바뀌는 필드는 그대로 옮긴다 -- 미리보기에서 created 가 "(미기록)"
        # 으로 보이면 값이 사라지는 것처럼 읽힌다 (실제로는 손대지 않는다).
        return SceneMetadata(
            scene_id=self._md.scene_id, objects=objects, layout=layout,
            description=self._md.description, station=self._md.station,
            dataset_version=self._md.dataset_version,
            created=self._md.created,
            payload_mass=self._md.payload_mass,
            payload_com=self._md.payload_com)

    def _violations(self, md: SceneMetadata) -> int:
        return sum(1 for s in self._sentences
                   if lint(s, md, self._props) is not None)

    def _redraw(self, *_a) -> None:
        changes = self._picked()
        after = self._after_md(changes)
        self._now.setText(describe_scene(self._md))
        self._after.setText(describe_scene(after))
        if not changes:
            self.set_note(tr("고를 것을 바꾸면 오른쪽이 다시 그려집니다."))
            self.set_ok_enabled(False)
            return
        dup = len(set(after.objects)) != len(after.objects)
        if dup:
            self.set_note(tr("같은 물체를 두 자리에 둘 수 없습니다."))
            self.set_ok_enabled(False)
            return
        b, a = self._violations(self._md), self._violations(after)
        self.set_note(tr(
            "물체 {n}개를 고칩니다 — 규칙에 맞지 않는 지시문 {b}건 → {a}건. "
            "에피소드는 건드리지 않고 기록만 바뀌므로, 올려 둔 데이터셋을 "
            "다시 만들 필요는 없습니다.").format(n=len(changes), b=b, a=a))
        self.set_ok_enabled(True)

    def accept(self) -> None:
        self.changes = self._picked()
        super().accept()
