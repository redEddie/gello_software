"""dataset_sync Hub 조회 revision 핀 검증."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

WT = str(Path(__file__).resolve().parents[2])  # 리포 루트
sys.path.insert(0, WT)
sys.path.insert(0, WT + "/scripts")
sys.argv = ["t"]

from gello.dataset_sync import (  # noqa: E402
    LEROBOT_TAG,
    hub_episode_uids,
    hub_meta,
)


def test_revision_pin():
    """hub_meta/hub_episode_uids 가 lerobot 이 읽는 CODEBASE_VERSION 태그로
    snapshot_download 을 부르고, 태그가 없는 신생 repo 는 빈 결과로
    처리해야 한다."""

    def _fake_download(repo_id, repo_type="dataset", allow_patterns=None,
                       revision=None, force_download=True):
        calls.append((repo_id, revision, allow_patterns))
        # 메타 쪽은 parquet 를, 사이드카 쪽은 json 을 남긴다.
        if allow_patterns and "meta/episode_uids.json" in allow_patterns:
            (tmp / "meta").mkdir(parents=True, exist_ok=True)
            (tmp / "meta" / "episode_uids.json").write_text(
                json.dumps({"0": {"episode_uid": "EP-S000-I000-E000"}}),
                encoding="utf-8",
            )
        else:
            (tmp / "meta" / "episodes").mkdir(parents=True, exist_ok=True)
        return str(tmp)

    tmp = Path(tempfile.mkdtemp(prefix="hubsync_"))
    calls = []

    with patch("huggingface_hub.snapshot_download", side_effect=_fake_download):
        counts, lengths, err = hub_meta("dummy/repo")
        assert err == "", err
        assert counts == {} and lengths == {}  # parquet 가 없어서 빈 결과
        assert any(c[1] == LEROBOT_TAG for c in calls), calls

        uids, err2 = hub_episode_uids("dummy/repo")
        assert err2 == "", err2
        assert uids == {"EP-S000-I000-E000"}, uids
        assert any(c[1] == LEROBOT_TAG and c[2] == ["meta/episode_uids.json"]
                   for c in calls), calls

    # RevisionNotFoundError 는 "아직 태그가 없는 신생 repo" 로 취급.
    from huggingface_hub.errors import RevisionNotFoundError  # noqa: E402

    class FakeRevNotFound(RevisionNotFoundError):
        def __init__(self, msg):
            self.args = (msg,)

    with patch("huggingface_hub.snapshot_download",
               side_effect=FakeRevNotFound("no tag")):
        counts, lengths, err = hub_meta("dummy/repo")
        assert err == "" and counts == {} and lengths == {}
        uids, err2 = hub_episode_uids("dummy/repo")
        assert err2 == "" and uids == set()

    print("test_revision_pin 통과: snapshot_download revision=v3.0, RevisionNotFoundError -> 빈 결과")


if __name__ == "__main__":
    test_revision_pin()
    print("\ndataset_sync 검증 통과")
