"""
Regression tests for the 2026-08-16 audit's public_api.py fixes:

- C6.6: get_today_picks (the endpoint backing The Edge cockpit) was a third
  query function missing the 4 CLV columns (closing_odds/clv_pct/
  morning_odds/line_move_pct) -- the historical fix covered get_picks and
  get_clv_data but missed this one.

- C6.14: get_picks (the one endpoint hit on every public page load) had no
  upper bound on caller-supplied limit -- get_clv_data already hardcodes
  LIMIT 2000, so the pattern was known but never applied here.

Uses a real BetTracker-provisioned SQLite engine (not mocked SQL), same
convention as tests/test_admin_auth.py's dashboard tests.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pytest
from sqlalchemy import text

from mlb_core.tracking.bet_tracker import BetTracker
from mlb.runners.public_api import get_today_picks, get_picks, _ct_today


@pytest.fixture
def engine_with_one_settled_bet(tmp_path):
    # Bug found 2026-08-25 (a real CI failure on `main`, not induced by this
    # fix): this used bare date.today() -- the SYSTEM's local timezone --
    # while get_today_picks() itself always filters on _ct_today() (US/
    # Central, the timezone this whole product organizes the baseball day
    # by). Those agree most of the day, but GitHub Actions runners default
    # to UTC, which is AHEAD of Central -- for the ~5 hour window each
    # evening (roughly 7pm-midnight Central) where UTC has already rolled
    # to the next calendar day but Central hasn't, the fixture logged a bet
    # dated "tomorrow" while the function queried for "today", so this
    # returned 0 rows instead of 1. Deterministic, not flaky: it fails
    # every time CI happens to run in that window, passes the rest of the
    # day. Fixed by reusing the exact same _ct_today() the code under test
    # uses, so this can never disagree with it regardless of the runner's
    # own clock/timezone.
    today = _ct_today()
    tracker = BetTracker(str(tmp_path / "test_public_api.db"), "HR")
    bet_id = tracker.log_bet(
        game_date=today, game_pk=12345, player="Test Player",
        away_team="NYY", home_team="BOS", bet_type="HR_YES",
        model_prob=0.15, market_prob=0.10, edge=0.05, kelly_pct=0.02,
        odds=650, stake=10.0, kelly_triggered=True, paper=True,
    )
    with tracker.engine.begin() as conn:
        conn.execute(text(
            "UPDATE bets SET closing_odds=:co, clv_pct=:clv, "
            "morning_odds=:mo, line_move_pct=:lmp WHERE id=:id"
        ), {"co": 600.0, "clv": 7.5, "mo": 700, "lmp": -14.3, "id": bet_id})
    return tracker.engine


def test_get_today_picks_includes_clv_columns(engine_with_one_settled_bet):
    picks = get_today_picks(engine_with_one_settled_bet, include_all=True)
    assert len(picks) == 1
    pick = picks[0]
    for col in ("closing_odds", "clv_pct", "morning_odds", "line_move_pct"):
        assert col in pick, f"get_today_picks is missing {col} (C6.6 regression)"
    assert pick["closing_odds"] == 600.0
    assert pick["clv_pct"] == 7.5
    assert pick["morning_odds"] == 700
    assert pick["line_move_pct"] == -14.3


def test_get_picks_still_includes_clv_columns(engine_with_one_settled_bet):
    """Sanity check get_picks (the reference the C6.6 fix copied) wasn't
    broken by the get_today_picks change."""
    picks = get_picks(engine_with_one_settled_bet)
    assert len(picks) == 1
    for col in ("closing_odds", "clv_pct", "morning_odds", "line_move_pct"):
        assert col in picks[0]


def test_get_picks_limit_is_capped_at_200(engine_with_one_settled_bet, monkeypatch):
    """A caller-supplied limit far above the cap must not reach the DB as-is.

    Spies on conn.execute to capture the actual bound :limit param, rather
    than re-deriving it -- this exercises get_picks's real parameter-
    building code, not a reimplementation of the cap."""
    captured = {}
    orig_connect = engine_with_one_settled_bet.connect

    def _spy_connect():
        conn = orig_connect()
        orig_execute = conn.execute

        def _spy_execute(stmt, params=None, *a, **kw):
            if params and "limit" in params:
                captured["limit"] = params["limit"]
            return orig_execute(stmt, params, *a, **kw)

        conn.execute = _spy_execute
        return conn

    monkeypatch.setattr(engine_with_one_settled_bet, "connect", _spy_connect)

    get_picks(engine_with_one_settled_bet, limit=999999)
    assert captured.get("limit") == 200, (
        f"get_picks did not cap an oversized limit at 200 (C6.14 regression): "
        f"got {captured.get('limit')}"
    )


def test_get_picks_limit_under_cap_is_unchanged(engine_with_one_settled_bet):
    """A reasonable limit must not be clobbered by the new cap."""
    picks = get_picks(engine_with_one_settled_bet, limit=5)
    assert len(picks) <= 5
