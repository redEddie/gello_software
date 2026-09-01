refactor(workspace): 통계·분석을 StatsOps로 분리 (Phase 4-7)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

`apps/workspace/domains/stats.py` 의 `StatsOps`.
창에서는 `self.stats_ops = StatsOps(self)`.

## 옮길 것

    _refresh_stats  _filtered_stats  _on_stats*  _stats_*
    _summary  _on_analysis*  _analysis_*  _on_group*  _on_metric_help
    _update_stats_panel  _bump

`grep -n '    def .*\(stats\|analysis\|summary\|group\|hist\|metric\)' \
 apps/collect_workspace.py` 로 확정하세요. 4-6 에서 나간 것은 건드리지 마세요.

## 주의

- `self.win.session.counters` 는 이번 세션 카운터, `self.win.session.cumulative`
  는 누적, `self.win.session.stats` 는 `scan_dataset()` 이 돌려준 **리스트**
  입니다. 셋을 헷갈리지 마세요 -- 이름이 비슷하지만 다른 것입니다.
- `_bump` 는 카운터를 올리는 곳이라 수집 중에 불립니다. 수집 제어(4-8)가
  이것을 부르므로, 옮긴 뒤 `self.win.stats_ops.bump(...)` 로 부르게 됩니다.
- `trim_plots`, `series_plots` 는 분석 탭을 처음 열 때 만들어지는 위젯입니다
  (`test_domain_attrs` 의 LATE 목록에 있습니다). 창에 그대로 두세요.
- `test_stats_group`, `test_phase4a` 가 이 영역입니다.
