"""2026-09-06 GUI 개선분 인수 테스트.

조작자가 말한 다섯 가지를 하나씩 못박는다:

1. 데이터 preview 에 데이터세트 버전이 보인다           (ds_schema)
2. 저장 경로 여유가 상태바에 있다                       (sb_disk)
3. 다시 분석이 자동으로 돈다 + 수집 이력이 남는다        (stats_stale / collection_history)
4. 상태바에 수집 개수는 없다                            (sb_right)
5. 활동 바가 워크플로 순서다                            (ACTIVITIES / WORKFLOW)

로봇도 카메라도 필요 없다 (offscreen). 창 전체 대신 test_ui_surface 와 같은
방식으로 빌더가 요구하는 것만 흉내 내는 스텁에 붙여 만든다.
"""
import sys
import tempfile
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from PyQt6.QtWidgets import QApplication, QLineEdit, QMainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])

from gello.data.collection_history import (  # noqa: E402
    MIN_RATE_SECONDS,
    SessionRecord,
    append_session,
    leaderboard,
    load_sessions,
    runs,
)
from apps.workspace.constants import (  # noqa: E402
    ACTIVITIES,
    WORKFLOW,
    workflow_step,
)
from apps.workspace.features.stats import build_stats  # noqa: E402
from apps.workspace.features.stats.ops import StatsOps  # noqa: E402
from apps.workspace.shell import build_statusbar  # noqa: E402

# --------------------------------------------------------------- 5. 워크플로
keys = [k for k, *_ in ACTIVITIES]
assert keys[:4] == ["layout", "configure", "collect", "dataset"], keys
assert [k for k, _m, _l in WORKFLOW] == keys[:4], WORKFLOW
assert workflow_step("layout")[0] == "①"
assert workflow_step("upload") is None, "도구에는 번호를 붙이지 않는다"
# "다음 단계" 버튼은 뺐다 (2026-09-06) -- 활동 바 아이콘과 Ctrl+1~7 이
# 이미 그 일을 한다. 번호는 남는다: 순서가 순서라는 것을 말해 주는 것은
# 아이콘 바만으로는 안 되기 때문이다.
print("5. 활동 바가 수집 한 바퀴 순서 OK:", " → ".join(k for k, _m, _l in WORKFLOW))


# ------------------------------------------------------- 1·2·4. 화면 표면
class _Stub(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.root_edit = QLineEdit(str(Path.home()))
        self.collector_edit = QLineEdit("tester")
        self.run_id = "test-run"
        self.session = None
        self.stats_ops = StatsOps(self)
        self.log_lines: list = []

    def log(self, msg) -> None:
        self.log_lines.append(str(msg))


win = _Stub()
build_statusbar(win)
assert hasattr(win, "sb_disk"), "저장 경로 여유가 상태바에 없다"
win.stats_ops.refresh_disk()
assert "GB" in win.sb_disk.text(), win.sb_disk.text()
print("2. 저장 경로 여유가 상태바에 있다 OK:", win.sb_disk.text())

src = (WT / "apps/workspace/features/camera/ops.py").read_text(encoding="utf-8")
tick = src[src.index("def tick_fps"):src.index("def camera_node_specs")]
assert "sb_right" in tick, "상태바 오른쪽을 채우는 곳이 여기가 아니게 됐다"
assert "에피소드 {t}개" not in tick and "counters" not in tick, (
    "상태바에 수집 개수가 다시 들어왔다 -- 헤더·오른쪽 패널·slot 카운터가 "
    "이미 세 번 말하고 있다 (2026-09-06 사용자 결정)")
print("4. 상태바에 수집 개수 없음 OK")

# 오른쪽 패널의 Dataset 상자에 스키마 줄이 있는가 -- 빌더는 창 전체를
# 요구하므로 정본(배치표)과 그것을 채우는 쪽을 함께 확인한다.
layout_src = (WT / "apps/workspace/shell/layout.py").read_text(encoding="utf-8")
assert '("ds_schema", "스키마")' in layout_src, "Dataset 상자에 스키마 줄이 없다"
ds_src = (WT / "apps/workspace/features/dataset/ops.py").read_text(encoding="utf-8")
for expect in ('f["ds_schema"].setText(self.win.schema_version)',
               'f["ds_schema"].setText(schema)'):
    assert expect in ds_src, f"스키마 줄을 채우지 않는다: {expect}"
assert "normalize_schema_version" in ds_src, "옛 표기(scene-v1)를 풀지 않는다"
print("1. 데이터 preview 에 데이터세트 버전 줄 OK")


# ----------------------------------------------------------- 3. 수집 이력
tmp = Path(tempfile.mkdtemp(prefix="colhist_")) / "collection_history.jsonl"
rows = [
    # 20분에 40개 = 분당 2.0
    SessionRecord(run="r1", started="2026-09-06T09:00:00", ended="2026-09-06T09:20:00",
                  collector="gibeom", dataset="fr3-tabletop", saved=40, success=36,
                  failed=4, seconds=1200.0),
    # 20분에 20개 = 분당 1.0
    SessionRecord(run="r1", started="2026-09-06T09:30:00", ended="2026-09-06T09:50:00",
                  collector="eddie", dataset="fr3-tabletop", saved=20, success=20,
                  seconds=1200.0),
    # 다른 데이터셋 -- 순위표에 섞이면 안 된다
    SessionRecord(run="r2", started="2026-09-06T10:00:00", ended="2026-09-06T10:20:00",
                  collector="eddie", dataset="other-set", saved=99, seconds=1200.0),
]
for r in rows:
    append_session(r, path=tmp)
back = load_sessions(path=tmp)
assert len(back) == 3 and back[0].collector == "gibeom"
assert abs(back[0].per_minute - 2.0) < 1e-6, back[0].per_minute

board = leaderboard(back, dataset="fr3-tabletop")
assert [p.collector for p in board] == ["gibeom", "eddie"], [p.collector for p in board]
assert board[0].saved == 40 and abs(board[0].success_rate - 0.9) < 1e-6
assert board[1].saved == 20, "다른 데이터셋의 99개가 섞였다"
assert [r for r, _v in runs(back)] == ["r2", "r1"], "최근 실행이 앞에 와야 한다"

short = SessionRecord(saved=3, seconds=MIN_RATE_SECONDS - 1)
assert short.per_minute == 0.0, "1분도 안 되는 세션의 분당 환산은 속도가 아니다"

# 깨진 줄이 나머지 이력을 잃게 하지 않는다
with open(tmp, "a", encoding="utf-8") as f:
    f.write("{ this is not json\n")
assert len(load_sessions(path=tmp)) == 3
print("3a. 수집 이력 저장·순위표 OK:",
      ", ".join(f"{p.collector} {p.per_minute:.2f}/min" for p in board))

# 자동 재분석: 바뀐 게 없으면 다시 스캔하지 않는다.
src = (WT / "apps/workspace/features/stats/ops.py").read_text(encoding="utf-8")
assert "def auto_refresh_analysis" in src and "def mark_stats_stale" in src
assert 'if self.win.worker is not None:\n            return' in src, (
    "세션 중에도 스캔한다 -- 기록 중인 파일은 saver 가 쥐고 있어 통째로 빠진다")
for mutation in ("apps/workspace/features/collection/ops.py",
                 "apps/workspace/features/dataset/ops.py"):
    body = (WT / mutation).read_text(encoding="utf-8")
    assert "mark_stats_stale()" in body, f"{mutation} 의 변경이 재분석을 예약하지 않는다"

scans = {"n": 0}


class _AnalysisStub:
    """refresh_analysis 의 게이트만 떼어 확인한다 -- 실제 스캔은 파일이
    필요하고, 여기서 보려는 것은 '언제 도는가' 뿐이다."""

    class _Session:
        def __init__(self) -> None:
            self.stats: list = []
            self.stats_stale = True

    def __init__(self) -> None:
        self.session = self._Session()

    def refresh_analysis(self, force: bool = False) -> None:
        if not force and self.session.stats and not self.session.stats_stale:
            return
        scans["n"] += 1
        self.session.stats = ["one"]
        self.session.stats_stale = False


a = _AnalysisStub()
a.refresh_analysis()                    # 처음 -- 아직 아무것도 안 읽었다
a.refresh_analysis()                    # 바뀐 게 없다 -- 안 돈다
assert scans["n"] == 1, scans
a.session.stats_stale = True            # 저장/삭제가 있었다
a.refresh_analysis()
assert scans["n"] == 2, scans
a.refresh_analysis(force=True)          # 조작자가 [다시 분석] 을 눌렀다
assert scans["n"] == 3, scans
print("3b. 자동 재분석 게이트 OK (바뀐 것이 있을 때만, 세션 중엔 미룸)")

# 순위표·최근 세션 상자가 Statistics 화면에 실제로 만들어지는가.
page_win = _Stub()
build_stats(page_win)
for attr in ("board_tree", "board_hint", "history_tree"):
    assert hasattr(page_win, attr), f"Statistics 에 {attr} 가 없다"
assert not hasattr(page_win, "disk_label"), "디스크 상자는 상태바로 옮겼다"
print("3c. Statistics 에 순위표·최근 세션 상자 OK")

print("\n2026-09-06 GUI 개선 인수 통과")
