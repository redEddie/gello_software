refactor(workspace): collection 과 stats 를 features/ 로 (8-4)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

    features/collection/__init__.py   재수출 + __all__
    features/collection/ops.py        domains/collection.py
    features/collection/page.py       pages/collect.py

    features/stats/__init__.py        재수출 + __all__
    features/stats/ops.py             domains/stats.py
    features/stats/page.py            pages/stats.py
    features/stats/analysis_tab.py    builders/analysis_tab.py

## 주의: collection 은 수집의 심장입니다

- `worker` 는 창이 소유합니다 (`self.win.worker`). 옮기지 마세요.
- `builders/toolbar.py` 는 **옮기지 마세요.** Connect/Record/Save 버튼이
  수집 동작을 부르지만, 툴바·메뉴·상태표시줄은 창 골격입니다.
- 단축키 처리(`eventFilter`)는 창에 있습니다. 건드리지 마세요 -- 양손이
  리더암 위에 있는 조작자에게 유일한 조작 수단입니다.
- `test_gate_reset`, `test_phase4a` 가 게이트·리셋·판정 뒤집기를 직접
  확인합니다. 둘 중 하나라도 깨지면 되돌려야 합니다.

## 주의: stats 의 세 가지를 헷갈리지 마세요

`session.counters`(이번 세션) / `session.cumulative`(누적) /
`session.stats`(scan_dataset 이 돌려준 리스트). 이름이 비슷하지만 다릅니다.

`trim_plots`, `series_plots` 는 분석 탭을 처음 열 때 만들어지는 위젯이라
창에 있습니다 (`test_domain_attrs` 의 LATE 목록). 그대로 두세요.

## 검증

    bash tests/gui/run_all.sh /home/franka/lerobot-venv/bin/python
