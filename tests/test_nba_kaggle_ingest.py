"""Tests for nba.data.kaggle_ingest -- exercises the download->upload->manifest
flow with an injected fake downloader (no network, no kagglehub required)."""
import json
from pathlib import Path

import pytest

from nba.data import kaggle_ingest


@pytest.fixture
def fake_dataset(tmp_path):
    """Build a fake downloaded dataset dir with a nested file."""
    root = tmp_path / "dl"
    (root / "csv").mkdir(parents=True)
    (root / "Games.csv").write_text("game_id,date\n1,2024-01-01\n")
    (root / "csv" / "PlayerStatistics.csv").write_text("player,pts\nx,10\n")
    return root


def test_run_uploads_all_files_and_writes_sentinel(monkeypatch, fake_dataset, tmp_path):
    uploaded = {}

    def fake_upload(local_path, key):
        uploaded[key] = Path(local_path).read_text()

    written = {}

    def fake_write_bytes(data, key):
        written[key] = data

    monkeypatch.setattr(kaggle_ingest.storage, "upload_file", fake_upload)
    monkeypatch.setattr(kaggle_ingest.storage, "write_bytes", fake_write_bytes)

    result = kaggle_ingest.run(handle="fake/ds", download_fn=lambda h: str(fake_dataset))

    # both files uploaded under the stats_nba raw prefix, nesting preserved
    assert "NBA/stats_nba/raw/Games.csv" in uploaded
    assert "NBA/stats_nba/raw/csv/PlayerStatistics.csv" in uploaded
    assert result["files"] == 2
    assert result["status"] == "ok"
    assert result["total_bytes"] > 0

    # sentinel written with a manifest
    assert "NBA/stats_nba/last_ingest.json" in written
    sentinel = json.loads(written["NBA/stats_nba/last_ingest.json"])
    assert sentinel["files"] == 2
    assert {m["file"] for m in sentinel["manifest"]} == {"Games.csv", "csv/PlayerStatistics.csv"}


def test_run_raises_on_empty_dir(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(kaggle_ingest.storage, "upload_file", lambda *a: None)
    monkeypatch.setattr(kaggle_ingest.storage, "write_bytes", lambda *a: None)
    with pytest.raises(RuntimeError):
        kaggle_ingest.run(handle="fake/ds", download_fn=lambda h: str(empty))
