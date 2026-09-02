"""
Tests for the BettingPros odds toolkit + odds_history store.

Covers roadmap section 6 items for this track:
- golden-file melt per market shape (one-sided prop, moneyline, total, yesno)
- de-vig correctness (two-way fair probs sum to 1)
- team-norm / market-map coverage (no market silently unmapped)
- id_resolver: doubleheader -> game 1, name-collision disambiguation
- odds_history Parquet round-trip + coverage report

All offline: id_resolver caches are pre-populated (no MLB Stats API calls);
storage is forced into local mode via tmp_path.
"""

import pandas as pd
import pytest

from mlb_core.data import id_resolver as R
from mlb_core.odds import bettingpros as bp
from mlb.analysis import bettingpros_to_parquet as b2p
from mlb.analysis import odds_history as oh


# --------------------------------------------------------------------------- #
# market-map / shape coverage -- no market silently dropped
# --------------------------------------------------------------------------- #

def test_every_bp_market_is_mapped_to_history():
    for _mid, (name, _kind) in bp.MARKETS.items():
        assert name in b2p.BP_TO_HISTORY, f"market {name} has no odds_history mapping"


def test_every_kind_has_sides():
    kinds = {k for _n, k in bp.MARKETS.values()}
    for kind in kinds:
        assert kind in b2p.KIND_SIDES, f"kind {kind} missing from KIND_SIDES"


# --------------------------------------------------------------------------- #
# id_resolver -- pure index building + resolution via cache
# --------------------------------------------------------------------------- #

def test_game_index_doubleheader_orders_games():
    sched = {"dates": [{"games": [
        {"gamePk": 1, "teams": {"away": {"team": {"id": 147}}, "home": {"team": {"id": 110}}}},
        {"gamePk": 2, "teams": {"away": {"team": {"id": 147}}, "home": {"team": {"id": 110}}}},
    ]}]}
    idx = R._build_game_index(sched)
    assert idx[("NYY", "BAL")] == [1, 2]


def test_player_index_collision_needs_team():
    players = {"people": [
        {"id": 1, "fullName": "Will Smith", "currentTeam": {"id": 119}},   # LAD
        {"id": 2, "fullName": "Will Smith", "currentTeam": {"id": 108}},   # LAA
        {"id": 3, "fullName": "Aaron Judge", "currentTeam": {"id": 147}},  # NYY
    ]}
    n2i, nt2i = R._build_player_index(players)
    assert n2i["aaron judge"] == {3}
    assert n2i["will smith"] == {1, 2}
    assert nt2i[("will smith", "LAD")] == 1


def test_resolve_via_cache(monkeypatch):
    monkeypatch.setitem(R._schedule_cache, "2024-05-01", {("NYY", "BAL"): [10, 11]})
    monkeypatch.setitem(R._player_cache, "2024",
                        ({"aaron judge": {99}, "will smith": {1, 2}},
                         {("aaron judge", "NYY"): 99}))
    assert R.resolve_game_pk("2024-05-01", "NYY", "BAL") == 10        # game 1
    assert R.resolve_game_pk("2024-05-01", "SEA", "HOU") is None      # missing
    assert R.resolve_player_id("Aaron Judge", "NYY", "2024-05-01") == 99
    assert R.resolve_player_id("Will Smith", "BOS", "2024-05-01") is None  # ambiguous


# --------------------------------------------------------------------------- #
# melt + de-vig golden files
# --------------------------------------------------------------------------- #

def _prime_resolver(monkeypatch):
    monkeypatch.setitem(R._schedule_cache, "2024-05-01",
                        {("NYY", "BAL"): [745101], ("TB", "MIL"): [745200]})
    monkeypatch.setitem(R._player_cache, "2024",
                        ({"aaron judge": {592450}}, {("aaron judge", "NYY"): 592450}))


def test_melt_moneyline_devig_and_snapshots(monkeypatch):
    _prime_resolver(monkeypatch)
    ml = pd.DataFrame([{
        "Date": "2024-05-01", "Matchup": "TB at MIL", "Away": "TB", "Home": "MIL",
        "DraftKings_Away": "-112", "DraftKings_Home": "-108",
        "Consensus_Away": "-115", "Consensus_Home": "-105",
        "Open_Away": "-126", "Open_Home": "+108",
    }])
    out = b2p.market_rows_to_history("moneyline", ml, "2026-01-01T00:00:00Z")
    assert set(out["book"]) == {"draftkings", "consensus"}
    assert set(out["snapshot_ts"]) == {"2024-05-01 00:00:00", "2024-05-01 23:30:00"}
    dk = out[(out.book == "draftkings") & out.is_closing]
    assert round(dk["fair_prob"].sum(), 6) == 1.0          # de-vig sums to 1
    assert set(dk["selection"]) == {"AWAY", "HOME"}
    assert out["game_pk"].dropna().unique().tolist() == [745200]
    assert out["line"].isna().all()                        # ML has no line


def test_melt_player_ou_line_matchup_player_id(monkeypatch):
    _prime_resolver(monkeypatch)
    hr = pd.DataFrame([{
        "Date": "2024-05-01", "Player": "Aaron Judge", "Player_Page": "/x/",
        "Matchup": "NYY at BAL", "Team": "NYY", "Position": "RF", "Line": "0.5",
        "DraftKings_Over": "+330", "DraftKings_Under": "-430",
        "Consensus_Over": "+340", "Consensus_Under": "-440",
        "Open_Over": "+300", "Open_Under": "-400",
    }])
    out = b2p.market_rows_to_history("home_runs", hr, "2026-01-01T00:00:00Z")
    r0 = out.iloc[0]
    assert r0["market"] == "hr_yn" and r0["system"] == "HR"
    assert r0["away_team"] == "NYY" and r0["home_team"] == "BAL"
    assert float(r0["line"]) == 0.5
    assert out["game_pk"].dropna().unique().tolist() == [745101]
    assert out["player_id"].dropna().unique().tolist() == [592450]


def test_melt_skips_empty_cells(monkeypatch):
    _prime_resolver(monkeypatch)
    # only Consensus priced; sportsbook cells blank -> only consensus rows
    df = pd.DataFrame([{
        "Date": "2024-05-01", "Matchup": "TB at MIL", "Away": "TB", "Home": "MIL", "Line": "8.5",
        "Consensus_Over": "-104", "Consensus_Under": "-118",
        "Open_Over": "", "Open_Under": "",
        "DraftKings_Over": "", "DraftKings_Under": "",
    }])
    out = b2p.market_rows_to_history("total_runs", df, "2026-01-01T00:00:00Z")
    assert set(out["book"]) == {"consensus"}
    assert float(out.iloc[0]["line"]) == 8.5


# --------------------------------------------------------------------------- #
# odds_history Parquet round-trip (needs pyarrow)
# --------------------------------------------------------------------------- #

def test_odds_history_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))

    df = pd.DataFrame([
        {"sport": "mlb", "market": "game_ml", "selection": "HOME", "line": None,
         "book": "draftkings", "american": -108, "game_date": "2024-05-01",
         "snapshot_ts": "2024-05-01 23:30:00", "source": "bettingpros", "game_pk": 1},
        {"sport": "mlb", "market": "game_ml", "selection": "AWAY", "line": None,
         "book": "draftkings", "american": -112, "game_date": "2024-05-01",
         "snapshot_ts": "2024-05-01 23:30:00", "source": "bettingpros", "game_pk": 1},
    ])
    n = oh.write_partition(df, "game_ml", "2024-05-01")
    assert n == 2
    back = oh.read_history("game_ml")
    assert len(back) == 2
    assert list(back.columns) == oh.SCHEMA_COLUMNS
    cov = oh.coverage_report("game_ml")
    assert cov["n_dates"] == 1 and cov["per_season"] == {"2024": 1}


def test_write_partition_dedups(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    # identical DEDUP_KEYS -> collapses to 1 row (keep last)
    df = pd.DataFrame([
        {"market": "k_ou", "game_pk": 5, "selection": "OVER", "line": 5.5,
         "book": "fanduel", "snapshot_ts": "2024-06-01 23:30:00", "source": "bettingpros",
         "american": -110, "game_date": "2024-06-01"},
        {"market": "k_ou", "game_pk": 5, "selection": "OVER", "line": 5.5,
         "book": "fanduel", "snapshot_ts": "2024-06-01 23:30:00", "source": "bettingpros",
         "american": -115, "game_date": "2024-06-01"},
    ])
    n = oh.write_partition(df, "k_ou", "2024-06-01")
    assert n == 1
    assert oh.read_history("k_ou").iloc[0]["american"] == -115   # last wins


def _bhits_row(snapshot_ts, ingested_at, american):
    return {"market": "bhits_ou", "game_pk": 9, "selection": "OVER", "line": 1.5,
            "book": "fanduel", "snapshot_ts": snapshot_ts, "source": "bettingpros",
            "american": american, "game_date": "2024-07-01", "ingested_at": ingested_at}


def test_write_partition_append_does_not_read_existing_data(tmp_path, monkeypatch):
    """The perf fix (docs/solutions/logic-errors/
    odds-history-append-write-amplification.md): append=True used to read +
    concat + rewrite the WHOLE existing partition on every call -- fine for
    a few writes/day, but ruinous at fast_alert_loop's */15 cadence (4-8 min/
    run, almost entirely this one step). It must now write only the
    incoming batch as its own new file, with zero reads of prior data."""
    pytest.importorskip("pyarrow")
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    from mlb_core import storage

    oh.write_partition(pd.DataFrame([_bhits_row("2024-07-01 15:00:00", "t1", -110)]),
                       "bhits_ou", "2024-07-01", append=True)

    read_calls = []
    real_read = storage.read_bytes
    monkeypatch.setattr(storage, "read_bytes",
                        lambda key: (read_calls.append(key), real_read(key))[1])

    n = oh.write_partition(pd.DataFrame([_bhits_row("2024-07-01 15:15:00", "t2", -105)]),
                           "bhits_ou", "2024-07-01", append=True)

    assert n == 1, "should report rows written THIS call, not the partition total"
    assert read_calls == [], (
        "append=True read existing data -- the write-amplification regression is back"
    )

    # Both snapshots must still be visible via read_history (accumulated as
    # separate files, merged at read time -- nothing lost).
    back = oh.read_history("bhits_ou")
    assert len(back) == 2
    assert set(back["snapshot_ts"]) == {"2024-07-01 15:00:00", "2024-07-01 15:15:00"}


def test_write_partition_overwrite_clears_stale_append_files(tmp_path, monkeypatch):
    """append=False is the deliberate 'replace corrupt rows' maintenance
    path -- it must actually remove prior append=True files too, or a
    'clean rewrite' would silently leave stale data for read_history() to
    merge back in."""
    pytest.importorskip("pyarrow")
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))

    oh.write_partition(pd.DataFrame([_bhits_row("2024-07-01 15:00:00", "t1", -110)]),
                       "bhits_ou", "2024-07-01", append=True)
    oh.write_partition(pd.DataFrame([_bhits_row("2024-07-01 15:15:00", "t2", -105)]),
                       "bhits_ou", "2024-07-01", append=True)
    assert len(oh.read_history("bhits_ou")) == 2

    fixed = pd.DataFrame([_bhits_row("2024-07-01 16:00:00", "t3", -120)])
    oh.write_partition(fixed, "bhits_ou", "2024-07-01", append=False)

    back = oh.read_history("bhits_ou")
    assert len(back) == 1, "append=False must clear prior append files, not just add its own"
    assert back.iloc[0]["snapshot_ts"] == "2024-07-01 16:00:00"
