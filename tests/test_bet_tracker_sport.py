"""sport column on `bets` (2026-10-14): migration safety, scoping, dedup.

The migration test builds a LEGACY table (the pre-sport schema + the v3 dedup index, with real-looking rows)
and constructs a tracker over it -- the exact thing that happens to production on first deploy.
"""
import sqlalchemy as sa
import pytest
from sqlalchemy import text

from mlb_core.tracking import bet_tracker
from mlb_core.tracking.bet_tracker import BetTracker, norm_sport

LEGACY_SCHEMA = """
CREATE TABLE bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, system TEXT NOT NULL, game_date TEXT, game_pk INTEGER, player TEXT,
    away_team TEXT, home_team TEXT, bet_type TEXT, model_prob REAL, market_prob REAL, edge REAL, kelly_pct REAL,
    odds INTEGER, stake REAL, kelly_triggered BOOLEAN DEFAULT TRUE, paper INTEGER DEFAULT 1, result TEXT,
    profit REAL, settled_at TEXT, notes TEXT, created_at TEXT, lambda_k REAL, proj_k REAL, book TEXT,
    closing_odds REAL, closing_prob REAL, clv_pct REAL, morning_odds INTEGER, line_move_pct REAL)
"""
LEGACY_V3 = ("CREATE UNIQUE INDEX idx_bets_dedup_v3 ON bets(system, game_date, game_pk, player, bet_type, "
             "kelly_triggered)")


def _bet(t, **kw):
    base = dict(game_date="2026-10-15", game_pk=1, player="Alex Ovechkin", away_team="MTL", home_team="WSH",
                bet_type="GOAL_OVER_0.5_fanduel", odds=250, stake=100.0, edge=0.05, book="fanduel")
    base.update(kw)
    return t.log_bet(**base)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "bets.db"


def test_norm_sport():
    assert norm_sport(None) == "mlb" and norm_sport(" NHL ") == "nhl"
    with pytest.raises(ValueError):
        norm_sport("nfl")                      # not a bets-table sport yet; never silently default a typo


def test_default_tracker_is_mlb_and_unchanged(db):
    t = BetTracker(db, "HR")
    assert t.sport == "mlb"
    assert _bet(t, bet_type="HR", game_pk=7) > 0
    with t.engine.connect() as c:
        assert c.execute(text("SELECT sport FROM bets")).scalar() == "mlb"


def test_same_key_two_sports_transitional_v3_is_handled_not_crashed(db, caplog):
    """While legacy idx_bets_dedup_v3 exists, an identical key in another sport conflicts on v3. It must be
    reported as a duplicate (-1) with an ERROR log -- never an unhandled IntegrityError in a runner."""
    mlb, nhl = BetTracker(db, "EV"), BetTracker(db, "EV", sport="nhl")
    assert _bet(mlb) > 0
    with caplog.at_level("ERROR"):
        assert _bet(nhl) == -1
    assert "legacy v3 dedup index" in caplog.text


def test_same_key_in_two_sports_coexists_once_v3_is_dropped(db):
    mlb, nhl = BetTracker(db, "EV"), BetTracker(db, "EV", sport="nhl")
    with mlb.engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS idx_bets_dedup_v3"))      # the post-rollout cleanup step
    assert _bet(mlb) > 0
    assert _bet(nhl) > 0                       # identical (system, date, pk, player, type): different sport
    assert _bet(nhl) == -1                     # a true duplicate inside one sport is still caught
    assert _bet(mlb) == -1


def test_reads_are_sport_scoped(db):
    mlb, nhl = BetTracker(db, "EV"), BetTracker(db, "EV", sport="nhl")   # distinct game_pk per row, as in prod
    _bet(mlb, game_pk=1)
    _bet(nhl, game_pk=2)
    _bet(nhl, game_pk=3)
    assert len(mlb.all_bets()) == 1 and len(nhl.all_bets()) == 2
    assert len(nhl.pending()) == 2 and len(mlb.pending()) == 1


def test_legacy_table_migrates_in_place_and_keeps_old_index(db):
    eng = sa.create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text(LEGACY_SCHEMA))
        c.execute(text(LEGACY_V3))
        c.execute(text("INSERT INTO bets (system, game_date, game_pk, player, bet_type, kelly_triggered, stake) "
                       "VALUES ('HR','2026-09-01',555,'Aaron Judge','HR',1,10.0)"))
    t = BetTracker(db, "HR")                    # first init over the legacy table
    with t.engine.connect() as c:
        assert c.execute(text("SELECT sport FROM bets WHERE game_pk=555")).scalar() == "mlb"
        idx = {r[1] for r in c.execute(text("PRAGMA index_list(bets)")).fetchall()}
    assert {"idx_bets_dedup_v3", "idx_bets_dedup_v4"} <= idx      # v3 kept for rolling-deploy compatibility
    assert len(t.all_bets()) == 1
    assert _bet(t, bet_type="HR", game_pk=556) > 0               # new rows still insert
    assert _bet(t, bet_type="HR", game_pk=555, player="Aaron Judge", game_date="2026-09-01") == -1


def test_migration_is_idempotent(db):
    BetTracker(db, "HR")
    BetTracker(db, "HR")                        # second construction must not raise or duplicate anything
    BetTracker(db, "EV", sport="nba")


def test_old_revision_insert_still_works_after_migration(db):
    """A still-running OLD revision inserts without `sport` and conflicts on v3's columns: both must keep working."""
    BetTracker(db, "HR")
    eng = sa.create_engine(f"sqlite:///{db}")
    old_sql = text("INSERT INTO bets (system, game_date, game_pk, player, bet_type, kelly_triggered) "
                   "VALUES ('HR','2026-10-01',9,'X','HR',1) "
                   "ON CONFLICT (system, game_date, game_pk, player, bet_type, kelly_triggered) DO NOTHING RETURNING id")
    with eng.begin() as c:
        assert c.execute(old_sql).fetchone() is not None
        assert c.execute(old_sql).fetchone() is None            # v3 still enforced
        assert c.execute(text("SELECT sport FROM bets WHERE game_pk=9")).scalar() == "mlb"


def test_registry_systems_are_mlb_and_filter_is_sport_aware():
    from mlb_core.registry import CANONICAL_ORDER, SYSTEMS, active_systems
    assert all(SYSTEMS[n].sport == "mlb" for n in CANONICAL_ORDER)
    assert active_systems() == active_systems("mlb") and active_systems("nhl") == []


def test_bare_engine_readers_work_on_a_legacy_table_before_any_tracker_exists(db, monkeypatch):
    """settle_bets / exposure / monitors build a bare _make_engine() and query `sport = 'mlb'` -- on the FIRST run after a
    deploy no BetTracker has migrated the table yet. _make_engine must add the column itself."""
    eng = sa.create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text(LEGACY_SCHEMA))
        c.execute(text("INSERT INTO bets (system, game_date, game_pk, player, bet_type, kelly_triggered, stake) "
                       "VALUES ('HR','2026-09-01',555,'Aaron Judge','HR',1,10.0)"))
    monkeypatch.setattr(bet_tracker, "DB_URL", "")
    bet_tracker._SPORT_COL_READY.clear()
    e2 = bet_tracker._make_engine(str(db))
    with e2.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM bets WHERE sport = 'mlb' AND result IS NULL")).scalar()
    assert n == 1


def test_ensure_sport_column_is_idempotent_and_tolerates_a_missing_table(db, monkeypatch):
    monkeypatch.setattr(bet_tracker, "DB_URL", "")
    bet_tracker._SPORT_COL_READY.clear()
    eng = bet_tracker._make_engine(str(db))             # no `bets` table yet: must not raise
    BetTracker(db, "HR")                                 # table + column created by the tracker
    bet_tracker._SPORT_COL_READY.clear()
    bet_tracker.ensure_sport_column(eng)
    bet_tracker.ensure_sport_column(eng)                 # second call: no-op
