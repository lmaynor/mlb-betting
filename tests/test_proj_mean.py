"""bets.proj_mean (2026-10-16): the model's own expected value for the player stat, persisted for the MLB projections board.

Covers the column migration on a legacy table, NaN safety (the public API serves this column as JSON), the K / OUTS
back-fill for rows logged before the column existed, and the unit the K runner writes (OUTS projections are stored as IP).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from mlb.runners import public_api
from mlb_core.tracking import bet_tracker
from mlb_core.tracking.bet_tracker import BetTracker, ensure_proj_mean_column

TODAY = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()


@pytest.fixture(autouse=True)
def _sync_today():
    global TODAY
    TODAY = public_api._ct_today()


def _bet(t, **kw):
    base = dict(game_date=TODAY, game_pk=1, player="Gerrit Cole", away_team="NYY", home_team="BOS",
                bet_type="K_OVER_5.5", model_prob=0.6, market_prob=0.5, edge=0.05, kelly_pct=0.01, odds=110,
                stake=25.0, book="fanduel", kelly_triggered=True)
    base.update(kw)
    return t.log_bet(**base)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "bets.db"


def _stored(tracker):
    with tracker.engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT proj_mean FROM bets ORDER BY id")).fetchall()]


def test_proj_mean_is_persisted_and_defaults_to_null(db):
    t = BetTracker(db, "BATTER_TB")
    assert _bet(t, bet_type="BATTER_TB_OVER_1.5", game_pk=1, proj_mean=1.73) > 0
    assert _bet(t, bet_type="BATTER_TB_OVER_1.5", game_pk=2) > 0
    assert _stored(t) == [pytest.approx(1.73), None]


def test_nan_and_junk_projections_are_stored_as_null(db):
    t = BetTracker(db, "K")
    assert _bet(t, game_pk=1, proj_mean=float("nan")) > 0
    assert _bet(t, game_pk=2, proj_mean="not a number") > 0
    assert _stored(t) == [None, None]


def test_migration_adds_the_column_to_a_legacy_table(db):
    import sqlalchemy as sa
    eng = sa.create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text("""CREATE TABLE bets (id INTEGER PRIMARY KEY AUTOINCREMENT, system TEXT NOT NULL, sport TEXT NOT NULL DEFAULT 'mlb',
            game_date TEXT, game_pk INTEGER, player TEXT, away_team TEXT, home_team TEXT, bet_type TEXT, model_prob REAL, market_prob REAL,
            edge REAL, kelly_pct REAL, odds INTEGER, stake REAL, kelly_triggered BOOLEAN DEFAULT TRUE, paper INTEGER DEFAULT 1, result TEXT,
            profit REAL, settled_at TEXT, notes TEXT, created_at TEXT, lambda_k REAL, proj_k REAL, book TEXT, closing_odds REAL,
            closing_prob REAL, clv_pct REAL, morning_odds INTEGER, line_move_pct REAL)"""))
    bet_tracker._PROJ_COL_READY.discard(str(eng.url))
    ensure_proj_mean_column(eng)
    with eng.connect() as c:
        assert "proj_mean" in {r[1] for r in c.execute(text("PRAGMA table_info(bets)")).fetchall()}
    ensure_proj_mean_column(eng)            # idempotent


def test_public_api_returns_proj_mean_and_backfills_old_k_and_outs_rows(db):
    t = BetTracker(db, "K")
    outs = BetTracker(db, "OUTS")
    _bet(t, game_pk=1, proj_mean=6.2)                                           # new row
    _bet(t, game_pk=2, player="Old K")                                          # old K row: only proj_k existed
    _bet(outs, game_pk=3, player="Old Outs", bet_type="OUTS_OVER_16.5")         # old OUTS row: proj_k is IP
    with t.engine.begin() as c:
        c.execute(text("UPDATE bets SET proj_k = 5.4 WHERE player = 'Old K'"))
        c.execute(text("UPDATE bets SET proj_k = 5.5 WHERE player = 'Old Outs'"))

    rows = {r["player"]: r for r in public_api.get_today_picks(t.engine, include_all=True)}
    assert rows["Gerrit Cole"]["proj_mean"] == pytest.approx(6.2)
    assert rows["Old K"]["proj_mean"] == pytest.approx(5.4)
    assert rows["Old Outs"]["proj_mean"] == pytest.approx(16.5)
    assert "proj_k" not in rows["Old K"]
    assert rows["Gerrit Cole"]["book"] == "fanduel"        # the today query now carries the book too

    picked = {r["player"]: r for r in public_api.get_picks(t.engine, date=TODAY)}
    assert picked["Old Outs"]["proj_mean"] == pytest.approx(16.5)


def test_other_systems_without_a_stored_projection_stay_null(db):
    t = BetTracker(db, "HR")
    _bet(t, bet_type="HR", player="Aaron Judge")
    with t.engine.begin() as c:
        c.execute(text("UPDATE bets SET proj_k = 9.9"))   # a stray value must NOT be read as a projection for HR
    (row,) = public_api.get_today_picks(t.engine, include_all=True)
    assert row["proj_mean"] is None


def test_k_runner_writes_ks_for_k_and_outs_for_outs():
    from mlb.runners.run_k import _proj_mean
    assert _proj_mean("K", 6.2) == pytest.approx(6.2)
    assert _proj_mean("OUTS", 5.5) == pytest.approx(16.5)      # proj_k is projected innings on OUTS rows
    assert _proj_mean("K", None) is None
    assert _proj_mean("OUTS", float("nan")) is None
