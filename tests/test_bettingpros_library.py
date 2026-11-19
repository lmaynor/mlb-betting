"""
tests.test_bettingpros_library -- unit and regression tests for BettingPros odds library.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

import pandas as pd
import pytest

from mlb_core.odds import bettingpros_games as bpg
from mlb_core.odds import bettingpros_library
from mlb_core.odds import bettingpros_props as bpp

CATALOG_NBA = [
    {"id": 156, "slug": "points", "category": "player-props", "period": "game"},
    {"id": 127, "slug": "moneyline", "category": "game-odds", "period": "game"},
    {"id": 154, "slug": "q1-over-under", "category": "game-props", "period": "game"},
    {"id": 134, "slug": "first-overall-pick", "category": "specials", "period": "game"},
    {"id": 169, "slug": "championship", "category": "team-futures", "period": "season"},
]

EVENTS = [
    {
        "id": 1001,
        "visitor": "BOS",
        "home": "MIA",
        "scheduled": "2026-11-18T19:00:00Z",
        "season_type": "REG",
        "status": "closed",
        "participants": [],
    }
]

BOOKS_PAYLOAD = {
    "books": [
        {"id": 10, "name": "FanDuel"},
        {"id": 12, "name": "DraftKings"},
    ]
}

POINTS_OFFERS = [
    {
        "id": 5001,
        "event_id": 1001,
        "player_id": 2001,
        "participants": [
            {
                "id": 2001,
                "name": "Jayson Tatum",
                "player": {"team": "BOS", "position": "SF"},
            }
        ],
        "selections": [
            {
                "id": 10,
                "selection": "over",
                "label": "Over",
                "opening_line": {
                    "book_id": 10,
                    "line": 26.5,
                    "cost": -110,
                    "created": "2026-11-18T10:00:00Z",
                },
                "books": [
                    {
                        "id": 10,
                        "lines": [
                            {"cost": -115, "line": None, "main": True, "updated": "2026-11-18T18:00:00Z"},
                            {"cost": None, "line": 27.5, "main": False, "updated": "2026-11-18T18:05:00Z"},
                            {"cost": -110, "line": 26.5, "main": False, "updated": "2026-11-18T18:10:00Z"},
                        ],
                    }
                ],
            },
            {
                "id": 11,
                "selection": "under",
                "label": "Under",
                "opening_line": {
                    "book_id": 10,
                    "line": 26.5,
                    "cost": -110,
                    "created": "2026-11-18T10:00:00Z",
                },
                "books": [
                    {
                        "id": 10,
                        "lines": [
                            {"cost": -110, "line": 26.5, "main": True, "updated": "2026-11-18T18:00:00Z"},
                        ],
                    }
                ],
            },
        ],
    },
    {
        "id": 5002,
        "event_id": 1001,
        "player_id": 2002,
        "participants": [
            {
                "id": 2002,
                "name": "Bam Adebayo",
            }
        ],
        "selections": [
            {
                "id": 12,
                "selection": "over",
                "label": "Over",
                "books": [
                    {
                        "id": 12,
                        "lines": [
                            {"cost": -110, "line": 18.5, "main": True, "updated": "2026-11-18T18:00:00Z"},
                        ],
                    }
                ],
            },
            {
                "id": 13,
                "selection": "under",
                "label": "Under",
                "books": [
                    {
                        "id": 12,
                        "lines": [
                            {"cost": -110, "line": 18.5, "main": True, "updated": "2026-11-18T18:00:00Z"},
                        ],
                    }
                ],
            },
        ],
    },
]

MONEYLINE_OFFERS = [
    {
        "id": 6001,
        "event_id": 1001,
        "selections": [
            {
                "id": 21,
                "selection": "MIA",
                "label": "Miami Heat",
                "participant": "MIA",
                "books": [
                    {
                        "id": 10,
                        "lines": [
                            {"cost": 150, "line": 1, "main": True, "updated": "2026-11-18T18:00:00Z"},
                        ],
                    }
                ],
            },
            {
                "id": 22,
                "selection": "BOS",
                "label": "Boston Celtics",
                "participant": "BOS",
                "books": [
                    {
                        "id": 10,
                        "lines": [
                            {"cost": -175, "line": 1, "main": True, "updated": "2026-11-18T18:00:00Z"},
                        ],
                    }
                ],
            },
        ],
    }
]

OFFERS_BY_MARKET = {
    156: POINTS_OFFERS,
    127: MONEYLINE_OFFERS,
    154: [],
    134: [],
}

FUTURES_CHAMPIONSHIP = [
    {
        "id": 7001,
        "event_id": None,
        "participants": [
            {"id": "BOS", "name": "Boston Celtics", "team": {"abbreviation": "BOS"}},
            {"id": "LAL", "name": "Los Angeles Lakers", "team": {"abbreviation": "LAL"}},
        ],
        "selections": [
            {
                "id": 31,
                "label": "Boston Celtics",
                "participant": "BOS",
                "books": [
                    {
                        "id": 12,
                        "lines": [{"line": 1, "cost": 350, "main": True}],
                    }
                ],
            },
            {
                "id": 32,
                "label": "Los Angeles Lakers",
                "participant": "LAL",
                "books": [
                    {
                        "id": 12,
                        "lines": [{"line": 1, "cost": 800, "main": True}],
                    }
                ],
            },
        ],
    }
]

FUTURES_OU = [
    {
        "id": 8001,
        "event_id": None,
        "participants": [
            {
                "id": 3001,
                "name": "Patrick Mahomes",
                "player": {"team": "KC", "position": "QB"},
            }
        ],
        "selections": [
            {
                "id": 41,
                "label": "Over",
                "participant": None,
                "books": [
                    {
                        "id": 12,
                        "lines": [{"line": 4250.5, "cost": -110}],
                    }
                ],
            },
            {
                "id": 42,
                "label": "Under",
                "participant": None,
                "books": [
                    {
                        "id": 12,
                        "lines": [{"line": 4250.5, "cost": -110}],
                    }
                ],
            },
        ],
    },
    {
        "id": 8002,
        "event_id": None,
        "participants": [
            {
                "id": 3002,
                "name": "Josh Allen",
                "player": {"team": "BUF", "position": "QB"},
            }
        ],
        "selections": [
            {
                "id": 43,
                "label": "Over",
                "participant": None,
                "books": [
                    {
                        "id": 12,
                        "lines": [{"line": 3999.5, "cost": -115}],
                    }
                ],
            },
            {
                "id": 44,
                "label": "Under",
                "participant": None,
                "books": [
                    {
                        "id": 12,
                        "lines": [{"line": 3999.5, "cost": -105}],
                    }
                ],
            },
        ],
    },
]


class FakeClock:
    """Controllable monotonically increasing clock for tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.time += seconds


class FakeResponse:
    """Mock HTTP response matching requests.Response."""

    def __init__(self, data: Any, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data)

    def json(self) -> Any:
        return self._data


class FakeSession:
    """Fake requests.Session recording every request and routing payloads."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.headers: dict[str, str] = {}
        self.clock = clock
        self.requests: list[tuple[str, dict[str, Any], float]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float = 30) -> FakeResponse:
        params = params or {}
        path = url.split("/v3/")[-1] if "/v3/" in url else url.rsplit("/", 1)[-1]
        self.requests.append((path, dict(params), self.clock()))

        if path == "markets":
            return FakeResponse({"markets": CATALOG_NBA})
        if path == "events":
            return FakeResponse({"events": EVENTS})
        if path == "books":
            return FakeResponse(BOOKS_PAYLOAD)
        if path == "offers":
            mid = int(params.get("market_id", -1))
            offers = OFFERS_BY_MARKET.get(mid, [])
            return FakeResponse({
                "offers": offers,
                "_pagination": {"total_pages": 1, "page": 1, "limit": 10},
            })
        return FakeResponse({}, 404)


@pytest.fixture(autouse=True)
def _setup_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    bettingpros_library.clear_catalog_cache()


def test_catalog_walk_completeness(tmp_path: Any) -> None:
    clock = FakeClock()
    sess = FakeSession(clock)
    limiter = bettingpros_library.SharedLimiter(min_interval=0.0, clock=clock, sleep=clock.sleep)

    stats = bettingpros_library.bank_day(sess, limiter, "NBA", "2026-11-18", today=date(2026, 11, 19))
    assert not stats.get("blocked")
    assert stats.get("errors", 0) == 0

    visited_market_ids = {
        int(req[1]["market_id"]) for req in sess.requests if req[0] == "offers"
    }
    assert visited_market_ids == {156, 127, 154, 134}
    assert 169 not in visited_market_ids

    p_pts = tmp_path / "Odds/bettingpros_nba/points/2026-11-18.parquet"
    p_spec = tmp_path / "Odds/bettingpros_nba/first-overall-pick/2026-11-18.parquet"
    p_ml = tmp_path / "Odds/bettingpros_nba_games/moneyline/2026-11-18.parquet"
    p_q1 = tmp_path / "Odds/bettingpros_nba_games/q1-over-under/2026-11-18.parquet"

    assert p_pts.exists()
    assert p_spec.exists()
    assert p_ml.exists()
    assert p_q1.exists()


def test_zero_row_marker(tmp_path: Any) -> None:
    clock = FakeClock()
    sess = FakeSession(clock)
    limiter = bettingpros_library.SharedLimiter(min_interval=0.0, clock=clock, sleep=clock.sleep)

    bettingpros_library.bank_day(sess, limiter, "NBA", "2026-11-18", today=date(2026, 11, 19))

    p_q1 = tmp_path / "Odds/bettingpros_nba_games/q1-over-under/2026-11-18.parquet"
    assert p_q1.exists()
    df = pd.read_parquet(p_q1)
    assert len(df) == 0
    assert list(df.columns) == bpg.COLUMNS


def test_incomplete_row_retention(tmp_path: Any) -> None:
    clock = FakeClock()
    sess = FakeSession(clock)
    limiter = bettingpros_library.SharedLimiter(min_interval=0.0, clock=clock, sleep=clock.sleep)

    bettingpros_library.bank_day(sess, limiter, "NBA", "2026-11-18", today=date(2026, 11, 19))

    p_pts = tmp_path / "Odds/bettingpros_nba/points/2026-11-18.parquet"
    assert p_pts.exists()
    df = pd.read_parquet(p_pts)

    odds_set_line_nan = df[df["odds"].notna() & df["line"].isna()]
    assert len(odds_set_line_nan) > 0

    line_set_odds_nan = df[df["line"].notna() & df["odds"].isna()]
    assert len(line_set_odds_nan) > 0

    missing_team = df[df["team"] == ""]
    assert len(missing_team) > 0
    assert "Bam Adebayo" in set(missing_team["player"])


def test_limiter_discipline() -> None:
    clock = FakeClock(1000.0)
    sess = FakeSession(clock)
    limiter = bettingpros_library.SharedLimiter(min_interval=0.8, clock=clock, sleep=clock.sleep)

    bettingpros_library.bank_day(sess, limiter, "NBA", "2026-11-18", today=date(2026, 11, 19))

    assert len(sess.requests) == 6

    timestamps = [req[2] for req in sess.requests]
    for i in range(len(timestamps) - 1):
        diff = timestamps[i + 1] - timestamps[i]
        assert diff >= 0.8 - 1e-9, f"Interval between requests {i} and {i+1} was {diff:.6f} < 0.8"


def test_idempotence(tmp_path: Any) -> None:
    clock = FakeClock()
    sess1 = FakeSession(clock)
    limiter1 = bettingpros_library.SharedLimiter(min_interval=0.0, clock=clock, sleep=clock.sleep)

    bettingpros_library.bank_day(sess1, limiter1, "NBA", "2026-11-18", today=date(2026, 11, 19))

    partition_paths = [
        tmp_path / "Odds/bettingpros_nba/points/2026-11-18.parquet",
        tmp_path / "Odds/bettingpros_nba/first-overall-pick/2026-11-18.parquet",
        tmp_path / "Odds/bettingpros_nba_games/moneyline/2026-11-18.parquet",
        tmp_path / "Odds/bettingpros_nba_games/q1-over-under/2026-11-18.parquet",
    ]
    counts1 = {p: len(pd.read_parquet(p)) for p in partition_paths}

    sess2 = FakeSession(clock)
    limiter2 = bettingpros_library.SharedLimiter(min_interval=0.0, clock=clock, sleep=clock.sleep)
    bettingpros_library.bank_day(sess2, limiter2, "NBA", "2026-11-18", today=date(2026, 11, 19))

    assert len(sess2.requests) == 0

    counts2 = {p: len(pd.read_parquet(p)) for p in partition_paths}
    assert counts1 == counts2


def test_futures_outright_rows() -> None:
    rows = bettingpros_library.build_futures_rows("NBA", "championship", 169, "2026-11-19", FUTURES_CHAMPIONSHIP)
    df = bpp.to_frame(rows)
    assert len(df) == 2
    assert pd.isna(df["line"]).all()
    assert set(df["side"]) == {"boston celtics", "los angeles lakers"}

    celtics = df[df["side"] == "boston celtics"].iloc[0]
    assert celtics["player"] == "Boston Celtics"
    assert celtics["team"] == "BOS"

    lakers = df[df["side"] == "los angeles lakers"].iloc[0]
    assert lakers["player"] == "Los Angeles Lakers"
    assert lakers["team"] == "LAL"


def test_futures_ou_rows() -> None:
    rows = bettingpros_library.build_futures_rows("NFL", "passing-yards", 201, "2026-11-19", FUTURES_OU)
    df = bpp.to_frame(rows)
    assert len(df) == 4
    assert set(df["player"]) == {"Patrick Mahomes", "Josh Allen"}
    assert set(df["side"]) == {"over", "under"}
    assert set(df["line"]) == {4250.5, 3999.5}


def test_season_for() -> None:
    assert bettingpros_library.season_for("NBA", date(2026, 11, 19)) == "2026"
    assert bettingpros_library.season_for("NBA", date(2027, 3, 1)) == "2026"
    assert bettingpros_library.season_for("NBA", date(2027, 10, 1)) == "2027"
    assert bettingpros_library.season_for("MLB", date(2026, 6, 1)) == "2026"
    assert bettingpros_library.season_for("NFL", date(2027, 1, 15)) == "2026"
