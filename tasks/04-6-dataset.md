refactor(workspace): 데이터셋 목록·삭제·라벨을 DatasetOps로 분리 (Phase 4-6)

`tasks/_공통.md` 를 먼저 읽으세요.

## 만들 것

`apps/workspace/domains/dataset.py` 의 `DatasetOps`.
창에서는 `self.dataset_ops = DatasetOps(self)`.

## 옮길 것 -- 목록·선택·삭제·라벨만

통계와 분석 그래프는 **다음 조각(4-7)** 입니다. 이번에는 이것만:

    _refresh_dataset_tree  _on_dataset*  _dataset_*
    _on_select_failed  _on_select_jerky  _on_delete_selected
    _on_delete_file  _describe_delete_targets  _on_relabel*
    _browse_root  _on_episode_delete*

`grep -n '    def .*\(dataset\|delete\|relabel\|select_\)' \
 apps/collect_workspace.py` 로 확정하세요. 이름에 `stats`/`analysis`/
`summary`/`hist` 가 들어간 것은 남겨 두세요.

## 주의

- 삭제는 되돌릴 수 없습니다. 확인 대화상자, 문구, 조건을 **한 글자도**
  바꾸지 마세요. `_describe_delete_targets` 가 사람에게 무엇이 지워지는지
  보여주는 유일한 곳입니다.
- `self.win.session.active_file_path`, `active_episode_cache` 를 씁니다.
- `test_relabel`, `test_dataset_sync`, `test_h5view` 가 이 영역입니다.
