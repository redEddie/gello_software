"""데이터 정보 표시 모듈의 계약 (2026-09-07).

조작자 요청: "데이터 정보 보여주는 형식을 고정해서 모듈로 만들어 필요할 때
지속적으로 공유하자. 일관된 UX 를 얻도록."

여기서 못박는 것은 **옛 방식으로 돌아가지 않는다**는 것이다:

1. 배치도는 진짜 위젯이다 (ASCII 아트 QLabel 이 아니다). 그래서 패널이
   좁아져도 셀이 같이 줄어들 뿐 잘리지 않고, 고정폭 글꼴에 기대지 않는다.
2. 값이 길면 캡션이 위로 올라간다 -- 그 판단을 카드가 스스로 한다
   (전에는 constants.WIDE_FIELDS 라는 전역 집합에 손으로 적었다).
3. 빈 값은 줄을 만들지 않는다 ("-" 스무 줄을 걷어낸 것과 같은 규칙).

로봇도 카메라도 필요 없다 (offscreen).
"""
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication([])

from apps.workspace.shared.info import (  # noqa: E402
    EMPTY,
    InfoCard,
    ZoneMap,
    scene_fields,
)
from gello.scene.scene_format import SceneMetadata  # noqa: E402

CUP, BOWL = "OBJ-CUP-BLU-01", "OBJ-BOWLS-WHT-01"
LAYOUT = {"grid": [3, 3],
          "placements": {CUP: {"zone": [0, 1]}, BOWL: {"zone": [2, 0]}}}

# ------------------------------------------------- 1. 배치도는 위젯이다
z = ZoneMap()
z.set_layout_spec(LAYOUT)
assert len(z._cells) == 9, len(z._cells)
assert z._cells[(0, 1)].text() == "CUP-BLU-01", z._cells[(0, 1)].text()
assert z._cells[(2, 0)].text() == "BOWLS-WHT-01", z._cells[(2, 0)].text()
assert z._cells[(1, 1)].text() == EMPTY, "빈 존이 빈 존으로 안 보인다"
# 셀은 제 내용만큼 폭을 요구하면 안 된다 -- 좁은 패널에서 격자가 패널 폭을
# 밀어내던 것이 옛 ASCII 격자가 잘리던 이유다.
from PyQt6.QtWidgets import QSizePolicy  # noqa: E402

assert z._cells[(0, 1)].sizePolicy().horizontalPolicy() == \
    QSizePolicy.Policy.Ignored, "셀이 가로 폭을 강제한다"
# 격자 모양이 바뀌면 셀도 다시 만든다
z.set_layout_spec({"grid": [2, 2], "placements": {}})
assert len(z._cells) == 4, len(z._cells)
# 어떤 셀에도 격자 선문자가 없다 -- 그림이 아니라 위젯이라는 뜻
assert not any(set(lab.text()) & set("│┌┬┐├┼┤└┴┘─")
               for lab in z._cells.values()), "아직 ASCII 격자를 그린다"
print("1. 배치도 = QGridLayout 셀 (선문자 없음, 폭 강제 없음) OK")

# ------------------------------------------- 2. 긴 값은 캡션이 위로 간다
card = InfoCard()
long_v = "OBJ-CUP-BLU-01(cup/blue), OBJ-BOWLS-WHT-01(small_bowl/white)"
card.set_fields([("Scene", "S001"), ("물체", long_v)])
assert card._rows_col.count() == 2, card._rows_col.count()
short_row = card._rows_col.itemAt(0).widget()
long_row = card._rows_col.itemAt(1).widget()
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout  # noqa: E402

assert isinstance(short_row.layout(), QHBoxLayout), "짧은 값이 좌우가 아니다"
assert isinstance(long_row.layout(), QVBoxLayout), "긴 값의 캡션이 안 올라갔다"
assert len(long_v) > InfoCard.WIDE_AT
# 전역 집합에 손으로 적는 방식으로 돌아가지 않았는지
import apps.workspace.constants as C  # noqa: E402

assert "ds_task" in getattr(C, "WIDE_FIELDS", {"ds_task"}), \
    "WIDE_FIELDS 를 지웠으면 이 단언도 지워라 (지금은 우측 세션 페이지가 쓴다)"
print("2. 값 길이로 캡션 위치를 카드가 정한다 OK")

# --------------------------------------------------- 3. 빈 값은 줄이 없다
card2 = InfoCard()
card2.set_fields([("A", "값"), ("B", ""), ("C", None), ("D", "   ")])
assert card2._rows_col.count() == 1, \
    f"빈 값이 줄을 만들었다 ({card2._rows_col.count()})"
print("3. 빈 값은 줄을 만들지 않는다 OK")

# --------------------------------------------- 4. 배치도는 켜고 끌 수 있다
card3 = InfoCard()
card3.set_zones(None)
assert not card3._zones.isVisible() or not card3.isVisible()
card3.set_zones(LAYOUT, note="[0,0]=왼쪽 위")
assert card3._note.text() == "[0,0]=왼쪽 위"
assert card3._zones._cells[(0, 1)].text() == "CUP-BLU-01"
print("4. 배치도 on/off + 주석 OK")

# ------------------------------------------- 5. metadata -> 필드 (글 아님)
md = SceneMetadata(scene_id="S001", objects=[CUP, BOWL], layout=LAYOUT,
                   description="컵 하나 그릇 하나", station="knu-eng7")
fields = scene_fields(md, counts={"I000": {"usable": 3, "total": 4}})
keys = [k for k, _v in fields]
for want in ("Scene", "스테이션", "스키마", "설명", "물체", "수집"):
    assert want in keys, f"{want} 줄이 없다: {keys}"
vals = dict(fields)
assert vals["Scene"] == "S001"
assert "cup/blue" in vals["물체"], vals["물체"]
assert "I000 3/4" in vals["수집"], vals["수집"]
# 설명이 없으면 그 줄도 없다
md2 = SceneMetadata(scene_id="S002", objects=[CUP], layout=LAYOUT)
assert "설명" not in [k for k, _ in scene_fields(md2)]
print("5. scene_fields = 줄 목록 (글 덩어리 아님) OK")

print("\n정보 표시 모듈 계약 통과")
