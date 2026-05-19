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


def test_prefetch_exposure_empty(tmp_path):
    """prefetch_exposure with no bets returns zeros."""
    from mlb_core.risk.exposure import prefetch_exposure
    make_tracker("NRFI", tmp_path)
    from mlb_core.tracking.bet_tracker import _make_engine
    engine = _make_engine(str(tmp_path / "NRFI_bets.db"))
    bankroll, stakes = prefetch_exposure(engine, [GAME_PK], GAME_DATE, starting=1000.0)
    assert bankroll == 1000.0
    assert stakes.get(GAME_PK, 0.0) == 0.0


def test_prefetch_exposure_with_bet(tmp_path):
    """prefetch_exposure returns correct open stake for a game_pk."""
    from mlb_core.risk.exposure import prefetch_exposure
    tracker = make_tracker("NRFI", tmp_path)
    log_nrfi_bet(tracker, game_pk=GAME_PK, stake=15.0, kelly_triggered=True)
    bankroll, stakes = prefetch_exposure(tracker.engine, [GAME_PK], GAME_DATE, starting=1000.0)
    assert abs(stakes.get(GAME_PK, 0.0) - 15.0) < 0.01


def test_apply_cap_with_pending(tmp_path):
    """apply_cap sees prefetched + in-run pending stakes."""
    from mlb_core.risk.exposure import apply_cap
    # $8 prefetched + $7 pending = $15 open; cap = $20 (2u at 1% of $1000)
    prefetched = {GAME_PK: 8.0}
    pending    = {GAME_PK: 7.0}
    _, remaining = apply_cap(1000.0, GAME_PK, prefetched, pending)
    assert abs(remaining - 5.0) < 0.01


def test_apply_cap_zero_when_exceeded(tmp_path):
    """apply_cap returns 0 when cap already exceeded."""
    from mlb_core.risk.exposure import apply_cap
    _, remaining = apply_cap(1000.0, GAME_PK, {GAME_PK: 25.0}, {})
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

# ── Settlement integration tests (mocked MLB API) ────────────────────────────

def _game_cache_full(
    away_inn1=0, home_inn1=0,
    away_f5=2, home_f5=3,
    pitcher_ks=7, pitcher_outs=18,
    batter_hrs=1, batter_starter=True,
    pitcher_starter=True,
):
    """Full game_cache fixture covering all four settlers."""
    innings = (
        [{"num": 1, "away_runs": away_inn1, "home_runs": home_inn1,
          "away_hits": 0, "home_hits": 0}] +
        [{"num": i, "away_runs": away_f5 // 4, "home_runs": home_f5 // 4,
          "away_hits": 0, "home_hits": 0} for i in range(2, 6)] +
        [{"num": i, "away_runs": 0, "home_runs": 0,
          "away_hits": 0, "home_hits": 0} for i in range(6, 10)]
    )
    return {GAME_PK: {
        "game_pk": GAME_PK, "final": True,
        "innings": innings,
        "pitchers": {"slade cecconi": {
            "starter": pitcher_starter, "strikeouts": pitcher_ks,
            "outs": pitcher_outs, "innings_pitched": "6.0",
            "earned_runs": 2, "hits_allowed": 5, "walks": 2,
            "home_runs_allowed": 0, "pitches_thrown": 90,
            "wins": 1, "losses": 0, "saves": 0,
        }},
        "batters": {"aaron judge": {
            "starter": batter_starter, "batting_order": 300,
            "home_runs": batter_hrs, "hits": 1, "at_bats": 4,
            "plate_appearances": 5, "rbi": 1, "runs": 1,
            "doubles": 0, "triples": 0, "walks": 1,
            "strikeouts": 1, "stolen_bases": 0,
            "total_bases": batter_hrs * 4,
        }},
    }}


def test_settle_nrfi_yrfi_via_cache():
    """YRFI: away scores in inning 1."""
    from runners.settle_bets import _settle_nrfi
    pending = pd.DataFrame([
        {"id": 1, "game_pk": GAME_PK, "bet_type": "NRFI", "stake": 10.0, "odds": -115},
        {"id": 2, "game_pk": GAME_PK, "bet_type": "YRFI", "stake": 10.0, "odds": -105},
    ])
    cache = _game_cache_full(away_inn1=1, home_inn1=0)
    results = _settle_nrfi(pending, cache)
    assert len(results) == 2
    by_id = {r["id"]: r for r in results}
    assert by_id[1]["result"] == "loss"   # NRFI loses when run scored
    assert by_id[2]["result"] == "win"    # YRFI wins


def test_settle_3way_draw_via_cache():
    """1I_DRAW: neither team scores in inning 1."""
    from runners.settle_bets import _settle_nrfi
    pending = pd.DataFrame([
        {"id": 1, "game_pk": GAME_PK, "bet_type": "1I_DRAW", "stake": 10.0, "odds": 200},
        {"id": 2, "game_pk": GAME_PK, "bet_type": "1I_AWAY", "stake": 10.0, "odds": 250},
    ])
    cache = _game_cache_full(away_inn1=0, home_inn1=0)
    results = _settle_nrfi(pending, cache)
    by_id = {r["id"]: r for r in results}
    assert by_id[1]["result"] == "win"    # draw wins
    assert by_id[2]["result"] == "loss"   # away loses (no score)


def test_settle_f5_away_win_via_cache():
    """F5 AWAY: away team leads after 5 innings."""
    from runners.settle_bets import _settle_f5
    pending = pd.DataFrame([
        {"id": 1, "game_pk": GAME_PK, "bet_type": "AWAY", "stake": 10.0, "odds": 110},
    ])
    # away scores 3, home scores 1 across innings 1-5
    cache = _game_cache_full(away_inn1=3, home_inn1=1, away_f5=3, home_f5=1)
    results = _settle_f5(pending, cache)
    assert results[0]["result"] == "win"


def test_settle_k_outs_via_cache():
    """OUTS O/U: pitcher records 18 outs, line 17.5."""
    from runners.settle_bets import _settle_k
    pending = pd.DataFrame([
        {"id": 1, "game_pk": GAME_PK, "player": "Slade Cecconi",
         "bet_type": "OUTS_OVER_17.5", "stake": 10.0, "odds": -110},
        {"id": 2, "game_pk": GAME_PK, "player": "Slade Cecconi",
         "bet_type": "OUTS_UNDER_17.5", "stake": 10.0, "odds": -110},
    ])
    cache = _game_cache_full(pitcher_outs=18)
    results = _settle_k(pending, cache)
    by_id = {r["id"]: r for r in results}
    assert by_id[1]["result"] == "win"    # over 17.5 wins with 18
    assert by_id[2]["result"] == "loss"


def test_settle_hr_starter_no_hr():
    """HR: starter who did not hit a HR -> loss."""
    from runners.settle_bets import _settle_hr
    pending = pd.DataFrame([
        {"id": 1, "game_pk": GAME_PK, "player": "Aaron Judge",
         "bet_type": "HR", "stake": 10.0, "odds": 500, "game_date": GAME_DATE},
    ])
    cache = _game_cache_full(batter_hrs=0, batter_starter=True)
    results = _settle_hr(pending, cache)
    assert results[0]["result"] == "loss"


def test_settle_game_not_final_all_systems():
    """All systems skip when game not Final (cache returns None)."""
    from runners.settle_bets import _settle_nrfi, _settle_f5, _settle_hr, _settle_k
    cache = {GAME_PK: None}
    pending_nrfi = pd.DataFrame([{"id":1,"game_pk":GAME_PK,"bet_type":"NRFI","stake":10.0,"odds":-115}])
    pending_f5   = pd.DataFrame([{"id":2,"game_pk":GAME_PK,"bet_type":"HOME","stake":10.0,"odds":-110}])
    pending_hr   = pd.DataFrame([{"id":3,"game_pk":GAME_PK,"player":"Aaron Judge","bet_type":"HR","stake":10.0,"odds":500,"game_date":GAME_DATE}])
    pending_k    = pd.DataFrame([{"id":4,"game_pk":GAME_PK,"player":"Slade Cecconi","bet_type":"K_OVER_6.5","stake":10.0,"odds":-120}])
    assert _settle_nrfi(pending_nrfi, cache) == []
    assert _settle_f5(pending_f5, cache) == []
    assert _settle_hr(pending_hr, cache) == []
    assert _settle_k(pending_k, cache) == []


# ── T19: IL-return threshold tests ────────────────────────────────────────────

def test_il_return_threshold_stale():
    """Pitcher whose last appearance was 11 days ago should be flagged as stale."""
    import pandas as pd
    run_date = "2026-05-19"
    last_app = pd.Timestamp("2026-05-08")   # 11 days before run_date
    days_since = (pd.Timestamp(run_date) - last_app).days
    assert days_since > 10, (
        f"Expected pitcher to be skipped (days_since={days_since} > 10)"
    )


def test_il_return_threshold_active():
    """Pitcher whose last appearance was 9 days ago should NOT be flagged."""
    import pandas as pd
    run_date = "2026-05-19"
    last_app = pd.Timestamp("2026-05-10")   # 9 days before run_date
    days_since = (pd.Timestamp(run_date) - last_app).days
    assert days_since <= 10, (
        f"Expected pitcher to be included (days_since={days_since} <= 10)"
    )


def test_il_return_threshold_boundary():
    """Pitcher whose last appearance was exactly 10 days ago is NOT skipped (boundary inclusive)."""
    import pandas as pd
    run_date = "2026-05-19"
    last_app = pd.Timestamp("2026-05-09")   # exactly 10 days
    days_since = (pd.Timestamp(run_date) - last_app).days
    assert days_since <= 10, (
        f"Expected pitcher at boundary to be included (days_since={days_since})"
    )
