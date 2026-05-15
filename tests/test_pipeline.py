"""
tests/test_pipeline.py — Pipeline plumbing smoke test.

Tests the bet logging → exposure cap → dedup → settlement pipeline
without requiring GCS, real model files, or a live database.

What this covers:
  - BetTracker.log_bet() + dedup
  - Exposure cap (2u per game_pk)
  - _calc_profit for all result types
  - _settle_nrfi, _settle_f5, _settle_k, _settle_hr settlement logic

What this does NOT cover (backlog):
  - Real XGBoost model prediction (requires GCS model files)
  - Real feature CSV loading (requires GCS)
  - Live SGO snapshot parsing end-to-end (covered by test_sgo_extractors.py)
  - Full settle_bets.run() with mocked GCS (requires DB connection)

Known production issue (tracked in backlog):
  - HR settlement: Statcast player_name is "Last, First" format.
    settle_hr normalizes both bet["player"] (SGO "First Last") and
    Statcast player_name, but "aaron judge" != "judge, aaron" so
    the appeared-set check fails for most players, causing DNP skips
    instead of correct win/loss settlement.
    Fix: normalize Statcast names by stripping the comma and reversing
    word order before building the appeared/hr_set.
"""
import sys
import os
import tempfile
from pathlib import Path
from datetime import date

import pandas as pd
import pytest

# Force local mode (no GCS, no Postgres)
os.environ.pop("MLB_GCS_BUCKET", None)
os.environ.pop("MLB_DB_URL", None)
os.environ["MLB_BASE_DATA"] = str(Path(tempfile.mkdtemp()))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Fixtures ──────────────────────────────────────────────────────────────────

GAME_PK   = 999001
GAME_DATE = "2026-05-15"
AWAY      = "CLE"
HOME      = "LAA"


def make_tracker(system: str, tmp_path: Path):
    from mlb_core.tracking.bet_tracker import BetTracker
    return BetTracker(str(tmp_path / f"{system}_bets.db"), system=system)


def log_nrfi_bet(tracker, game_pk=GAME_PK, bet_type="NRFI", stake=10.0,
                 edge=0.08, odds=-115, kelly_triggered=True):
    return tracker.log_bet(
        game_date=GAME_DATE, game_pk=game_pk,
        player=f"{AWAY} @ {HOME}", away_team=AWAY, home_team=HOME,
        bet_type=bet_type, model_prob=0.60, market_prob=0.52,
        edge=edge, kelly_pct=0.02, odds=odds, stake=stake,
        kelly_triggered=kelly_triggered,
    )


# ── BetTracker tests ──────────────────────────────────────────────────────────

def test_log_bet_returns_id(tmp_path):
    tracker = make_tracker("NRFI", tmp_path)
    bet_id = log_nrfi_bet(tracker)
    assert bet_id > 0


def test_log_bet_dedup(tmp_path):
    tracker = make_tracker("NRFI", tmp_path)
    first  = log_nrfi_bet(tracker)
    second = log_nrfi_bet(tracker)
    assert first > 0
    assert second == -1, "duplicate bet should return -1"


def test_log_bet_dedup_different_systems(tmp_path):
    """Same game_pk + bet_type but different systems should both log."""
    t1 = make_tracker("NRFI", tmp_path)
    t2 = make_tracker("F5",   tmp_path)
    id1 = log_nrfi_bet(t1, bet_type="NRFI")
    id2 = log_nrfi_bet(t2, bet_type="HOME")
    assert id1 > 0
    assert id2 > 0


def test_summary_filters_by_system(tmp_path):
    """summary() should only return rows for its own system."""
    t_nrfi = make_tracker("NRFI", tmp_path)
    t_f5   = make_tracker("F5",   tmp_path)
    log_nrfi_bet(t_nrfi, bet_type="NRFI", stake=10.0)
    log_nrfi_bet(t_f5,   bet_type="HOME", game_pk=999002, stake=20.0)

    with t_nrfi.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "UPDATE bets SET result='win', profit=9.09 WHERE system='NRFI'"
        ))

    stats = t_nrfi.summary()
    assert stats.get("bets") == 1, f"NRFI summary should show 1 bet, got {stats}"


# ── Exposure cap tests ────────────────────────────────────────────────────────

def test_exposure_current_bankroll_no_bets(tmp_path):
    from mlb_core.risk.exposure import current_bankroll
    make_tracker("NRFI", tmp_path)  # creates the table
    from mlb_core.tracking.bet_tracker import _make_engine
    engine = _make_engine(str(tmp_path / "NRFI_bets.db"))
    br = current_bankroll(engine, starting=1000.0)
    assert br == 1000.0


def test_exposure_current_bankroll_with_profit(tmp_path):
    from mlb_core.risk.exposure import current_bankroll
    tracker = make_tracker("NRFI", tmp_path)
    bid = log_nrfi_bet(tracker, stake=10.0)
    with tracker.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            f"UPDATE bets SET result='win', profit=50.0 WHERE id={bid}"
        ))
    br = current_bankroll(tracker.engine, starting=1000.0)
    assert br == 1050.0


def test_exposure_cap_reduces_stake(tmp_path):
    """If game already has 1.8u open, new bet should be capped at 0.2u."""
    from mlb_core.risk.exposure import get_bankroll_and_cap, UNIT_PCT, CAP_UNITS
    tracker = make_tracker("NRFI", tmp_path)
    bankroll = 1000.0
    # Log a bet that uses 1.8u = $18 on this game
    log_nrfi_bet(tracker, game_pk=GAME_PK, stake=18.0, kelly_triggered=True)
    _, remaining = get_bankroll_and_cap(
        tracker.engine, GAME_PK, GAME_DATE, starting=bankroll
    )
    assert abs(remaining - 2.0) < 0.01, f"expected $2 remaining, got ${remaining:.2f}"


def test_exposure_cap_zero_when_full(tmp_path):
    """If game already has 2u+ open, remaining cap should be 0."""
    from mlb_core.risk.exposure import get_bankroll_and_cap
    tracker = make_tracker("NRFI", tmp_path)
    log_nrfi_bet(tracker, game_pk=GAME_PK, stake=25.0, kelly_triggered=True)
    _, remaining = get_bankroll_and_cap(
        tracker.engine, GAME_PK, GAME_DATE, starting=1000.0
    )
    assert remaining == 0.0


# ── Settlement logic tests ────────────────────────────────────────────────────

def test_calc_profit_win_plus_odds():
    from runners.settle_bets import _calc_profit
    assert _calc_profit(10.0, 150, "win") == 15.0


def test_calc_profit_win_minus_odds():
    from runners.settle_bets import _calc_profit
    assert abs(_calc_profit(10.0, -110, "win") - 9.09) < 0.01


def test_calc_profit_loss():
    from runners.settle_bets import _calc_profit
    assert _calc_profit(10.0, -115, "loss") == -10.0


def test_calc_profit_push():
    from runners.settle_bets import _calc_profit
    assert _calc_profit(10.0, -115, "push") == 0.0


def test_calc_profit_void():
    from runners.settle_bets import _calc_profit
    assert _calc_profit(10.0, -115, "void") == 0.0


def _make_game_cache(away_inn1=0, home_inn1=0, innings_5_away=None, innings_5_home=None):
    """Build a minimal game_cache dict for settlement tests."""
    if innings_5_away is None:
        innings_5_away = [away_inn1] + [0] * 4
    if innings_5_home is None:
        innings_5_home = [home_inn1] + [0] * 4
    innings = [
        {"num": i+1, "away_runs": innings_5_away[i], "home_runs": innings_5_home[i],
         "away_hits": 0, "home_hits": 0}
        for i in range(5)
    ] + [{"num": i+6, "away_runs": 0, "home_runs": 0, "away_hits": 0, "home_hits": 0}
         for i in range(4)]
    return {GAME_PK: {"game_pk": GAME_PK, "final": True, "innings": innings,
                      "pitchers": {}, "batters": {}}}


def test_settle_nrfi_win():
    from runners.settle_bets import _settle_nrfi
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "bet_type": "NRFI",
                              "stake": 10.0, "odds": -115}])
    results = _settle_nrfi(pending, _make_game_cache(away_inn1=0, home_inn1=0))
    assert len(results) == 1
    assert results[0]["result"] == "win"
    assert abs(results[0]["profit"] - 8.70) < 0.01


def test_settle_nrfi_loss():
    from runners.settle_bets import _settle_nrfi
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "bet_type": "NRFI",
                              "stake": 10.0, "odds": -115}])
    results = _settle_nrfi(pending, _make_game_cache(away_inn1=1, home_inn1=0))
    assert results[0]["result"] == "loss"
    assert results[0]["profit"] == -10.0


def test_settle_3way_away():
    from runners.settle_bets import _settle_nrfi
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "bet_type": "1I_AWAY",
                              "stake": 10.0, "odds": 200}])
    results = _settle_nrfi(pending, _make_game_cache(away_inn1=1, home_inn1=0))
    assert results[0]["result"] == "win"
    assert results[0]["profit"] == 20.0


def test_settle_f5_home_win():
    from runners.settle_bets import _settle_f5
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "bet_type": "HOME",
                              "stake": 10.0, "odds": -110}])
    cache = _make_game_cache(innings_5_away=[0,0,0,0,0], innings_5_home=[1,0,0,0,0])
    results = _settle_f5(pending, cache)
    assert results[0]["result"] == "win"


def test_settle_f5_push():
    from runners.settle_bets import _settle_f5
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "bet_type": "HOME",
                              "stake": 10.0, "odds": -110}])
    cache = _make_game_cache(innings_5_away=[1,0,0,0,0], innings_5_home=[1,0,0,0,0])
    results = _settle_f5(pending, cache)
    assert results[0]["result"] == "push"
    assert results[0]["profit"] == 0.0


def _make_k_cache(strikeouts=8, outs=18, pitcher_name="Slade Cecconi"):
    """Build a minimal game_cache for K/OUTS settlement tests."""
    return {GAME_PK: {"game_pk": GAME_PK, "final": True, "innings": [],
                      "batters": {},
                      "pitchers": {pitcher_name.lower(): {
                          "starter": True, "strikeouts": strikeouts, "outs": outs,
                          "innings_pitched": "6.0", "earned_runs": 2,
                          "hits_allowed": 5, "walks": 2, "home_runs_allowed": 0,
                          "pitches_thrown": 90, "wins": 1, "losses": 0, "saves": 0,
                      }}}}


def test_settle_k_over_win():
    from runners.settle_bets import _settle_k
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Slade Cecconi",
                              "bet_type": "K_OVER_6.5", "stake": 10.0, "odds": -120}])
    results = _settle_k(pending, _make_k_cache(strikeouts=8))
    assert results[0]["result"] == "win"


def test_settle_k_under_win():
    from runners.settle_bets import _settle_k
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Slade Cecconi",
                              "bet_type": "K_UNDER_6.5", "stake": 10.0, "odds": 110}])
    results = _settle_k(pending, _make_k_cache(strikeouts=4))
    assert results[0]["result"] == "win"


def test_settle_k_push():
    from runners.settle_bets import _settle_k
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Slade Cecconi",
                              "bet_type": "K_OVER_7.0", "stake": 10.0, "odds": -110}])
    results = _settle_k(pending, _make_k_cache(strikeouts=7))
    assert results[0]["result"] == "push"


def _make_hr_cache(starter=True, home_runs=0, final=True):
    """Build a minimal game_cache for HR settlement tests."""
    if not final:
        return {GAME_PK: None}
    return {GAME_PK: {"game_pk": GAME_PK, "final": True, "innings": [],
                      "pitchers": {},
                      "batters": {"aaron judge": {
                          "starter": starter, "home_runs": home_runs,
                          "batting_order": 300 if starter else 301,
                          "hits": 1, "at_bats": 4, "plate_appearances": 5,
                          "rbi": 1, "runs": 1, "doubles": 0, "triples": 0,
                          "walks": 1, "strikeouts": 1, "stolen_bases": 0, "total_bases": home_runs * 4,
                      }}}}


def test_settle_hr_win():
    from runners.settle_bets import _settle_hr
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Aaron Judge",
                              "bet_type": "HR", "stake": 10.0, "odds": 500, "game_date": GAME_DATE}])
    results = _settle_hr(pending, _make_hr_cache(starter=True, home_runs=1))
    assert results[0]["result"] == "win"
    assert results[0]["profit"] == 50.0


def test_settle_hr_loss():
    from runners.settle_bets import _settle_hr
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Aaron Judge",
                              "bet_type": "HR", "stake": 10.0, "odds": 500, "game_date": GAME_DATE}])
    results = _settle_hr(pending, _make_hr_cache(starter=True, home_runs=0))
    assert results[0]["result"] == "loss"
    assert results[0]["profit"] == -10.0


def test_settle_hr_void_not_starter():
    from runners.settle_bets import _settle_hr
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Aaron Judge",
                              "bet_type": "HR", "stake": 10.0, "odds": 500, "game_date": GAME_DATE}])
    results = _settle_hr(pending, _make_hr_cache(starter=False, home_runs=0))
    assert results[0]["result"] == "void"
    assert results[0]["profit"] == 0.0


def test_settle_hr_game_not_final():
    from runners.settle_bets import _settle_hr
    pending = pd.DataFrame([{"id": 1, "game_pk": GAME_PK, "player": "Aaron Judge",
                              "bet_type": "HR", "stake": 10.0, "odds": 500, "game_date": GAME_DATE}])
    results = _settle_hr(pending, _make_hr_cache(final=False))
    assert len(results) == 0, "non-final game should be skipped"


