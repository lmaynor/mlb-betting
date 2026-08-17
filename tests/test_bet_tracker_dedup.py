"""
Regression tests for the 2026-08-16 audit's bet_tracker.py fixes:

- B3.2: bet_tracker._init_db() used to re-run a full-table one-shot data
  migration (reclassify OUTS bets logged under system="K" before the
  2026-05-14 split) unconditionally on every single BetTracker(...)
  construction, forever. Deleted now that the historical reclassification
  is long done.

- B3.3: bet dedup relied on a non-unique index and a check-then-insert
  race window, not a DB constraint -- is_duplicate() and log_bet() used
  two separate connections with a real TOCTOU gap, so a double-log could
  double-stake a real bet with no error. Fixed with a genuinely UNIQUE
  index on (system, game_date, game_pk, bet_type, kelly_triggered) and
  INSERT ... ON CONFLICT DO NOTHING.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pytest

from mlb_core.tracking.bet_tracker import BetTracker


@pytest.fixture
def tracker(tmp_path):
    return BetTracker(str(tmp_path / "test_dedup.db"), "HR")


def _log(tracker, kelly_triggered=True, game_pk=555):
    return tracker.log_bet(
        game_date="2026-08-17", game_pk=game_pk, player="Test Player",
        away_team="NYY", home_team="BOS", bet_type="HR_YES",
        model_prob=0.15, market_prob=0.10, edge=0.05, kelly_pct=0.02,
        odds=650, stake=10.0, kelly_triggered=kelly_triggered, paper=True,
    )


def test_no_outs_migration_runs_at_init(tracker):
    """B3.2: _init_db() must not reference the deleted one-shot migration
    at all -- confirmed indirectly by construction succeeding cleanly and
    the schema having no leftover artifact of it."""
    from mlb_core.tracking import bet_tracker as bt_module
    assert not hasattr(bt_module, "_MIGRATE_OUTS_SQL")


def test_sequential_duplicate_triggered_bet_is_blocked(tracker):
    """The common (non-racy) case, via is_duplicate()'s own pre-check."""
    id1 = _log(tracker, kelly_triggered=True)
    assert id1 != -1
    id2 = _log(tracker, kelly_triggered=True)
    assert id2 == -1, "a second kelly_triggered=True bet for the same key must be blocked"


def test_sequential_duplicate_non_triggered_log_is_blocked(tracker):
    id1 = _log(tracker, kelly_triggered=False)
    assert id1 != -1
    id2 = _log(tracker, kelly_triggered=False)
    assert id2 == -1


def test_triggered_bet_allowed_after_a_non_triggered_log_same_key(tracker):
    """The legitimate case the fix must NOT break: a prediction gets
    logged (kelly_triggered=False, e.g. below min_edge in the morning run),
    then a real bet on the SAME market side gets triggered later the same
    day (edge crossed the gate in the PM run) -- must be allowed as a
    second row, not treated as a duplicate."""
    id1 = _log(tracker, kelly_triggered=False)
    assert id1 != -1
    id2 = _log(tracker, kelly_triggered=True)
    assert id2 != -1, "a real triggered bet must not be blocked by an earlier logged-only row"
    assert id1 != id2


def test_db_constraint_catches_a_race_that_bypasses_is_duplicate(tracker, monkeypatch):
    """The actual point of B3.3: even if the Python-level pre-check is
    defeated (simulating two concurrent calls that both read 'not a
    duplicate' before either has committed its insert), the DB-level
    UNIQUE index + ON CONFLICT DO NOTHING must still prevent a second row
    -- and log_bet() must return -1, not silently double-insert or raise."""
    monkeypatch.setattr(tracker, "is_duplicate", lambda *a, **kw: False)

    id1 = _log(tracker, kelly_triggered=True, game_pk=777)
    assert id1 != -1
    id2 = _log(tracker, kelly_triggered=True, game_pk=777)
    assert id2 == -1, (
        "is_duplicate() was bypassed, but the DB constraint must still "
        "catch the duplicate insert (C.f. finding B3.3)"
    )

    from sqlalchemy import text
    with tracker.engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM bets WHERE game_pk=777 AND kelly_triggered=1"
        )).scalar()
    assert n == 1, f"expected exactly 1 row despite the bypassed race, found {n}"
