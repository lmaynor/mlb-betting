"""Tests for the storage parquet-twin layer (local-mode, tmp_path)."""

import os

import pandas as pd
import pytest

from mlb_core import storage


KEY = "Statcast/statcast_master.csv"
TWIN = "Statcast/statcast_master.parquet"


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_PARQUET_TWIN", raising=False)
    return tmp_path


def _df(n=3):
    return pd.DataFrame({"game_pk": range(n), "batter": ["a"] * n, "ev": [1.5] * n})


class TestParquetTwin:
    def test_write_csv_creates_twin_for_allowlisted_key(self, local_store):
        storage.write_csv(_df(), KEY)
        assert (local_store / KEY).exists()
        assert (local_store / TWIN).exists()

    def test_write_csv_no_twin_for_other_keys(self, local_store):
        storage.write_csv(_df(), "HR_Pro/data/model_features.csv")
        assert not (local_store / "HR_Pro/data/model_features.parquet").exists()

    def test_read_prefers_twin(self, local_store):
        storage.write_csv(_df(3), KEY)
        # corrupt the CSV; a twin-served read still works
        (local_store / KEY).write_text("garbage,not,csv")
        df = storage.read_csv(KEY)
        assert len(df) == 3 and list(df.columns) == ["game_pk", "batter", "ev"]

    def test_kill_switch_falls_back_to_csv(self, local_store, monkeypatch):
        storage.write_csv(_df(3), KEY)
        monkeypatch.setenv("MLB_PARQUET_TWIN", "0")
        df = storage.read_csv(KEY)
        assert len(df) == 3  # served from the real CSV

    def test_usecols_list_and_callable(self, local_store):
        storage.write_csv(_df(3), KEY)
        df = storage.read_csv(KEY, usecols=["game_pk", "ev"])
        assert list(df.columns) == ["game_pk", "ev"]
        df2 = storage.read_csv(KEY, usecols=lambda c: c in ("batter",))
        assert list(df2.columns) == ["batter"]

    def test_missing_twin_reads_csv(self, local_store):
        # write CSV without the twin (bypass write_csv)
        p = local_store / KEY
        p.parent.mkdir(parents=True, exist_ok=True)
        _df(4).to_csv(p, index=False)
        df = storage.read_csv(KEY, low_memory=False)
        assert len(df) == 4

    def test_corrupt_twin_falls_back_to_csv(self, local_store):
        p = local_store / KEY
        p.parent.mkdir(parents=True, exist_ok=True)
        _df(4).to_csv(p, index=False)
        (local_store / TWIN).write_bytes(b"not parquet")
        df = storage.read_csv(KEY)
        assert len(df) == 4
