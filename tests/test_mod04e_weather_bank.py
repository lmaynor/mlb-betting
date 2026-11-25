"""Tests for MOD-04 Cluster (e) E0(v): pre-game weather banking."""
from datetime import datetime, timezone
import pandas as pd
import pytest

from nfl.data import weather as nfl_weather
from nfl.mod04e import weather_bank as W


def test_outdoor_and_closed_roof_banking_with_fake_http_get(tmp_path):
    nfl_weather._CACHE.clear()
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    ko_outdoor = "2026-11-29T18:00:00Z"
    ko_dome = "2026-11-29T18:00:00Z"

    calls = []

    def fake_http_get(url, params, timeout):
        calls.append((url, params))
        return {
            "hourly": {
                "time": ["2026-11-29T18:00"],
                "temperature_2m": [38.5],
                "wind_speed_10m": [18.2],
                "wind_gusts_10m": [27.0],
                "precipitation": [0.4],
                "precipitation_probability": [40.0],
            }
        }

    sched = pd.DataFrame([
        {
            "game_id": "2026_13_MIA_BUF", "season": 2026, "week": 13,
            "home_team": "BUF", "away_team": "MIA", "kickoff_utc": ko_outdoor,
            "roof": "outdoors", "temp": None, "wind": None,
        },
        {
            "game_id": "2026_13_CHI_DET", "season": 2026, "week": 13,
            "home_team": "DET", "away_team": "CHI", "kickoff_utc": ko_dome,
            "roof": "dome", "temp": None, "wind": None,
        },
    ])

    records = W.bank_game_forecasts(sched, http_get=fake_http_get, root=tmp_path, now=now)
    assert len(records) == 2
    assert len(calls) == 1  # dome game never calls Open-Meteo

    by_gid = {r.game_id: r for r in records}
    buf = by_gid["2026_13_MIA_BUF"]
    det = by_gid["2026_13_CHI_DET"]

    assert buf.indoor is False
    assert buf.wind_mph == pytest.approx(18.2)
    assert buf.temp_f == pytest.approx(38.5)
    assert buf.precip == pytest.approx(0.4)
    assert buf.source == "open-meteo"
    assert W.wind_flag(buf) is True

    assert det.indoor is True
    assert det.wind_mph == pytest.approx(0.0)
    assert det.source == "dome"
    assert W.wind_flag(det) is False

    # Partitioned parquet round-trip
    loaded = W.load_game_forecasts(2026, 13, root=tmp_path)
    assert len(loaded) == 2
    assert {r.game_id for r in loaded} == {"2026_13_MIA_BUF", "2026_13_CHI_DET"}


def test_wind_flag_boundary_at_15_mph():
    base = {
        "game_id": "g1", "kickoff_utc": "2026-11-29T18:00:00Z",
        "retrieved_at_utc": "2026-11-25T12:00:00Z", "temp_f": 45.0,
        "precip": 0.0, "indoor": False,
    }
    assert W.wind_flag({**base, "wind_mph": 14.9}) is False
    assert W.wind_flag({**base, "wind_mph": 15.0}) is True
    assert W.wind_flag({**base, "wind_mph": 15.1}) is True
    assert W.wind_flag({**base, "wind_mph": 20.0, "indoor": True}) is False
    assert W.wind_flag({**base, "wind_mph": None}) is False
