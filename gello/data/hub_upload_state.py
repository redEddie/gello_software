"""Hub 업로드 장부 -- "지난 업로드 이후 바뀐 HDF5"를 stat 만으로 고른다.

왜 필요한가 (2026-08-25): 예전에는 "이번에 재압축한 파일만" 체크박스가
업로드 대상을 골랐는데, 그 목록은 재압축 마커 기준이라 **attr 만 고친
파일**(라벨 교정, 삭제·재번호)이 빠졌다 -- 실제로 문법 교정분 5개가 Hub에
안 올라간 사고가 있었다. 이 장부는 업로드가 성공할 때마다 파일별
(크기, mtime)을 repo 별로 기록해 두고, 다음에는 기록과 다른 파일(신규
포함)을 업로드 대상으로 고른다. 어떤 종류의 편집이든 mtime 이 바뀌므로
빠뜨리지 않고, stat 만 보므로 판정은 즉시 끝난다.

한계와 방어:
- mtime 만 바뀌고 내용이 같은 파일(touch 등)은 과잉 선택된다 -- Hub이
  해시 대조로 전송을 건너뛰므로 그 파일 하나를 다시 읽는 시간만 든다.
  과소 선택(빠뜨림)보다 과잉 선택이 항상 싸다는 방향으로 설계했다.
- 장부가 없거나(첫 실행) 깨졌으면 전부 "신규" 판정 = 전체 업로드와 같다.
- 장부는 이 컴퓨터 로컬 기록이다. Hub 쪽에서 파일을 지우는 등 밖에서
  상태가 바뀌면 장부가 모른다 -- GUI 에 "전체 강제 업로드" 탈출구를 남긴
  이유.

키는 (repo_id, 파일의 resolve()된 절대 경로). 심볼릭 링크를 따라가므로
old_data 이전 같은 경로 변경에도 같은 파일로 인식된다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

STATE_PATH = Path.home() / "libero_gui_logs" / "hub_upload_state.json"


def _load(state_path: Path | None = None) -> dict:
    p = state_path or STATE_PATH
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001 -- 없음/깨짐 = 빈 장부 (전부 신규 판정)
        return {}


def record_uploaded(repo_id: str, local: Path,
                    state_path: Path | None = None) -> None:
    """업로드 성공 직후 호출 -- 이 파일의 현재 (size, mtime)을 기록한다.

    업로드 *시작* 시점이 아니라 성공 후에 부르는 이유: 도중 실패하면 기록이
    남지 않아 다음에 다시 선택된다 (빠뜨림 없음 우선)."""
    p = state_path or STATE_PATH
    st = local.stat()
    data = _load(p)
    data.setdefault(repo_id, {})[str(local.resolve())] = {
        "size": st.st_size,
        "mtime": st.st_mtime,
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


def upload_reason(repo_id: str, local: Path,
                  state_path: Path | None = None) -> str | None:
    """이 파일을 업로드해야 하는 이유. None = 지난 업로드 이후 변경 없음."""
    rec = _load(state_path).get(repo_id, {}).get(str(local.resolve()))
    if rec is None:
        return "신규 — 업로드 기록 없음"
    st = local.stat()
    if st.st_size != rec.get("size") or st.st_mtime != rec.get("mtime"):
        edited = time.strftime("%m/%d %H:%M", time.localtime(st.st_mtime))
        return (f"변경 — {edited} 수정 "
                f"(지난 업로드 {rec.get('uploaded_at', '?')})")
    return None


def changed_files(repo_id: str, paths: list,
                  state_path: Path | None = None) -> list:
    """업로드 대상 목록: [(Path, 이유 str)]. 변경 없는 파일은 빠진다."""
    out = []
    for p in paths:
        reason = upload_reason(repo_id, Path(p), state_path=state_path)
        if reason is not None:
            out.append((Path(p), reason))
    return out
