"""데이터 정보를 보여주는 **한 가지 형식** -- 화면마다 다르게 그리지 않는다.

2026-09-07 조작자 요청: "데이터 정보 보여주는 것은 단순히 텍스트와 찌그러진
박스로 보여주는데, 아예 보여주는 형식을 고정해서 모듈로 만들어 필요할 때
지속적으로 공유하자. 일관된 UX 를 얻도록."

전에는 ``describe_scene(md)`` 가 뱉은 **글 덩어리**를 ``SceneInfoView`` 가
줄 단위로 쪼개서 보여줬다. 배치도는 ``│┌┬┐`` 선문자로 그린 ASCII 그림을
고정폭 QLabel 에 넣고, 패널이 좁으면 오른쪽이 잘리게 뒀다 -- 그것이 "찌그러진
박스"다. 게다가 글꼴에 기댔다: 'monospace' 별칭이 한국어 로케일에서 CJK 모노로
풀리면 선문자를 2칸으로 그려 격자가 어긋나서, D2Coding 을 스택 맨 앞에 두는
것으로 겨우 맞춰 놨었다.

여기서는 배치도를 **진짜 위젯**(QGridLayout 셀)으로 그린다. 그래서
 - 패널이 좁으면 셀이 같이 줄어든다. 잘리지 않는다.
 - 글꼴에 안 휘둘린다. D2Coding 이 없어도 같다.
 - Configure 좌측·수집 중 우측·추천 카드·Doctor·Dataset 미리보기가 전부
   같은 모양이 된다.

``describe_scene`` 은 그대로 둔다 -- 터미널 QA 검사기
(scripts/check/check_scene_file.py)의 정본 렌더러이고, 글로 찍는 자리는
여전히 글이 맞다. 이 모듈은 **화면용**이다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr

#: 값 없음. 회색 점 하나 -- "-" 는 "있어야 할 값이 빈 것" 처럼 읽힌다
#: (StatusLight 가 같은 이유로 "-" 를 안 쓴다).
EMPTY = "·"

_CAPTION = "color:#777; font-size:11px;"
_VALUE = "color:#222;"
# 채운 칸과 빈 칸이 **같은 테두리**를 갖는다. 빈 칸이 안 보이면 3×3 구조가
# 사라져서 남은 것이 허공에 뜬 상자 몇 개가 된다 -- ASCII 격자보다 나쁘다
# (2026-09-07 실측). 다른 것은 바탕과 글자색뿐이다.
_CELL = ("border:1px solid #b8b8b8; background:#f2f6f2; "
         "color:#1a3a1a; font-size:10px; font-weight:bold; padding:2px;")
_CELL_EMPTY = ("border:1px solid #d0d0d0; background:#fafafa; "
               "color:#c0c0c0; font-size:11px;")


class WrapLabel(QLabel):
    """접힌 만큼 **높이를 실제로 확보하는** QLabel.

    QLabel 은 wordWrap 을 켜도 sizeHint 가 한 줄치라, QVBoxLayout 안에서
    두 줄짜리 문장이 한 줄 높이(18px)만 받아 첫 줄이 잘린다 (2026-09-07
    실측: 우측 패널의 "아직 저장되지 않았습니다 — ..." 가 그랬다).

    sizePolicy 의 heightForWidth 를 켜는 것만으로는 모자라고,
    ``QLabel.heightForWidth`` 는 이 자리에서 -1 을 돌려준다. 그래서 폭이
    정해질 때마다(resizeEvent) 글꼴에 직접 물어 최소 높이로 박는다 --
    QFontMetrics 는 폭만 있으면 언제나 답한다.

    이 문제는 화면마다 따로 겪었다 (layout.build_right 의 WIDE_FIELDS,
    SceneInfoView, 우측 패널). 한 군데서 푼다.
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)
        sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self.setSizePolicy(sp)

    def setText(self, text: str) -> None:  # noqa: N802 -- Qt 이름
        super().setText(text)
        self._fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt 이름
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        w = self.width()
        if w <= 0:
            return
        text = self.text()
        need = self.fontMetrics().boundingRect(
            0, 0, w, 10_000, int(Qt.TextFlag.TextWordWrap), text
        ).height() if text else 0
        # 값이 같을 때 다시 박으면 resize -> setMinimumHeight -> resize 로
        # 맴돈다. 달라졌을 때만 손댄다.
        if need != self.minimumHeight():
            self.setMinimumHeight(max(0, need))


class ZoneMap(QWidget):
    """3×3(일반적으로 rows×cols) 배치도를 격자 위젯으로 그린다.

    ASCII 아트가 아니다. 셀은 폭을 균등하게 나눠 가지므로 패널이 좁아지면
    같이 줄어들 뿐 잘리지 않는다.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)
        self._cells: dict[tuple[int, int], QLabel] = {}
        self._shape = (0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)

    def set_layout_spec(self, layout: dict | None) -> None:
        """scene metadata 의 ``layout`` ({"grid": [r, c], "placements": {...}})."""
        layout = layout or {}
        rows, cols = (layout.get("grid") or [3, 3])[:2]
        self._reshape(int(rows), int(cols))
        here: dict[tuple[int, int], list[str]] = {}
        for oid, spec in (layout.get("placements") or {}).items():
            zone = tuple(spec["zone"]) if isinstance(spec, dict) else tuple(spec)
            # ID 앞의 OBJ- 는 모든 소품에 똑같이 붙어 있어 구분에 기여하지
            # 않는다 -- 좁은 칸에서 지면만 먹는다 (describe_scene 과 같은 규칙).
            here.setdefault(zone, []).append(
                oid[4:] if oid.startswith("OBJ-") else oid)
        for (r, c), lab in self._cells.items():
            names = here.get((r, c), [])
            lab.setText("\n".join(names) if names else EMPTY)
            lab.setStyleSheet(_CELL if names else _CELL_EMPTY)

    def cell_texts(self) -> list:
        """읽기 순서(왼쪽 위 -> 오른쪽 아래)로 칸의 글."""
        return [self._cells[(r, c)].text()
                for (r, c) in sorted(self._cells)]

    def _reshape(self, rows: int, cols: int) -> None:
        if self._shape == (rows, cols):
            return
        while self._grid.count():
            self._grid.takeAt(0).widget().deleteLater()
        self._cells.clear()
        for r in range(rows):
            for c in range(cols):
                lab = QLabel(EMPTY)
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lab.setWordWrap(True)
                lab.setMinimumHeight(30)
                # 셀이 제 내용만큼 폭을 요구하면 좁은 패널에서 격자가 패널
                # 폭을 밀어낸다 -- 그것이 옛 ASCII 격자가 잘리던 이유다.
                # Ignored 로 두면 셀이 남는 폭을 나눠 갖고 글자만 접힌다.
                lab.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Preferred)
                lab.setStyleSheet(_CELL_EMPTY)
                self._grid.addWidget(lab, r, c)
                self._cells[(r, c)] = lab
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)
        for r in range(rows):
            self._grid.setRowStretch(r, 1)
        self._shape = (rows, cols)


class InfoCard(QWidget):
    """라벨-값 줄들과 (선택적으로) 배치도 하나를 담는 고정 형식.

    값이 길면(파일명·문장) 캡션을 값 위로 올린다. 전에는 그 판단이
    ``constants.WIDE_FIELDS`` 라는 전역 집합에 손으로 적혀 있어서, 새 필드를
    넣을 때마다 그 집합도 같이 고쳐야 했고 안 고치면 값이 150px 에 갇혀
    서너 줄로 접혔다. 이제 카드가 값 길이를 보고 스스로 정한다.
    """

    #: 이 길이를 넘으면 캡션을 위로 올린다 (좌우로 놓으면 접힌다).
    WIDE_AT = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(3)
        self._rows = QWidget()
        self._rows_col = QVBoxLayout(self._rows)
        self._rows_col.setContentsMargins(0, 0, 0, 0)
        self._rows_col.setSpacing(2)
        # 글 한 줄만 보여야 하는 때가 있다 ("(scene 세션 없음)", "고르세요",
        # 읽기 실패). 그때는 필드도 배치도도 없다 -- setText 가 그 모드다.
        self._msg = WrapLabel("")
        self._msg.setStyleSheet(_VALUE)
        self._msg.setVisible(False)
        self._col.addWidget(self._msg)
        self._col.addWidget(self._rows)
        self._zones = ZoneMap()
        self._zones.setVisible(False)
        self._col.addWidget(self._zones)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(_CAPTION)
        self._note.setVisible(False)
        self._col.addWidget(self._note)
        self._fields: list = []
        self._msg_text = ""
        self._has_zones = False

    def set_fields(self, fields) -> None:
        """``[(라벨, 값), ...]`` -- 값이 빈 줄은 넣지 않는다."""
        self._msg_text = ""
        self._msg.setVisible(False)
        self._fields = [(str(k), "" if v is None else str(v)) for k, v in fields]
        while self._rows_col.count():
            self._rows_col.takeAt(0).widget().deleteLater()
        for label, value in fields:
            text = "" if value is None else str(value)
            if not text.strip():
                continue
            self._rows_col.addWidget(_field_row(label, text, self.WIDE_AT))

    def set_scene(self, md, counts: dict | None = None, extra=None) -> None:
        """SceneMetadata 하나를 통째로 -- 여섯 화면이 부르는 것이 이 하나다.

        어느 화면에서 scene 을 보든 같은 순서로 같은 것이 나온다. 화면마다
        무엇을 보여줄지 고르기 시작하면 다시 여섯 가지가 된다.
        """
        self.set_fields(scene_fields(md, counts) + list(extra or []))
        self.set_zones(md.layout, note=tr("[0,0] = 왼쪽 위 (agentview)"))

    def setText(self, text: str) -> None:  # noqa: N802 -- SceneInfoView 호환
        """글 한 줄 모드 -- 필드도 배치도도 지우고 문장만 보여준다.

        이름이 ``setText`` 인 것은 옛 ``SceneInfoView`` 를 그대로 대신하기
        위해서다. 부르는 자리 여섯 곳이 "scene 이 있으면 배치도, 없으면
        안내 문장" 을 같은 위젯에 넣고 있다.
        """
        self.set_fields([])
        self.set_zones(None)
        self._msg_text = text
        self._msg.setText(text)
        self._msg.setVisible(bool(text))

    def text(self) -> str:
        """지금 보이는 것 전부를 글로 -- 테스트와 로그가 이걸로 확인한다."""
        # isVisible() 을 쓰지 않는다 -- 창이 아직 안 떠 있으면 언제나
        # False 라, 화면 없이 도는 테스트에서 늘 빈 글이 나온다 (실측).
        # 무엇을 보여주기로 했는지는 우리가 안다.
        parts = []
        if self._msg_text:
            parts.append(self._msg_text)
        parts += [f"{k}: {v}" for k, v in self._fields if v.strip()]
        if self._has_zones:
            parts += [t for t in self._zones.cell_texts() if t != EMPTY]
        if self._note.text():
            parts.append(self._note.text())
        return "\n".join(parts)

    def set_zones(self, layout: dict | None, note: str = "") -> None:
        """배치도를 켠다. ``layout`` 이 None 이면 끈다."""
        on = bool(layout and (layout.get("placements") or layout.get("grid")))
        self._has_zones = on
        self._zones.setVisible(on)
        if on:
            self._zones.set_layout_spec(layout)
        self._note.setText(note)
        self._note.setVisible(bool(note))


def _field_row(label: str, value: str, wide_at: int) -> QWidget:
    w = QWidget()
    cap = QLabel(tr(label))
    cap.setStyleSheet(_CAPTION)
    val = WrapLabel(value)
    val.setStyleSheet(_VALUE)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if len(value) > wide_at or "\n" in value:
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(cap)
        col.addWidget(val)
    else:
        from PyQt6.QtWidgets import QHBoxLayout

        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        cap.setMinimumWidth(72)
        row.addWidget(cap)
        row.addWidget(val, 1)
    return w


def scene_fields(md, counts: dict | None = None) -> list:
    """SceneMetadata -> InfoCard 필드 목록.

    ``describe_scene`` 과 같은 사실을 말하지만 줄을 나눠 준다 -- 글 덩어리를
    다시 쪼개는 쪽이 아니라, 처음부터 필드로 준다.
    """
    from gello.scene.props import props_by_id

    try:
        inv = props_by_id()
    except Exception:  # noqa: BLE001 -- 인벤토리가 없어도 요약은 나와야 한다
        inv = {}

    def _kind(oid: str) -> str:
        p = inv.get(oid)
        return f"{oid}({p.category}/{p.color})" if p else oid

    fields = [
        (tr("Scene"), md.scene_id),
        (tr("스테이션"), md.station or tr("(미기록)")),
        (tr("스키마"), md.dataset_version),
        (tr("만든 때"), md.created or tr("(미기록)")),
    ]
    if md.description:
        fields.append((tr("설명"), md.description))
    fields.append((tr("물체"), ", ".join(_kind(o) for o in md.objects)))
    for a, rel, b in (md.layout.get("relations") or []):
        fields.append((tr("관계"), f"{a} {rel} {b}"))
    if counts:
        fields.append((tr("수집"), "  ".join(
            f"{iid} {c.get('usable', 0)}/{c.get('total', 0)}"
            for iid, c in sorted(counts.items()))))
    return fields
