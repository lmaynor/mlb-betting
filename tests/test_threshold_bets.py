"""
Tests for mlb_core.risk.threshold_bets -- the shared "N+" one-sided
threshold scoring math used by K/OUTS/BATTER_TB/BATTER_HITS's 2+/3+
sub-markets (2026-08-19).
"""
import pytest

from mlb_core.risk import threshold_bets as TB


def _cfg(**overrides):
    base = {"kelly_fraction": 0.25, "min_kelly_pct": 0.0, "max_kelly_pct": 0.05,
            "min_edge": 0.03, "cap_units": 2.0}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _fixed_vig(monkeypatch):
    """Avoid the real book_vig GCS lookup in every test -- deterministic 10%."""
    monkeypatch.setattr(TB, "_get_vig", lambda market_key, book: 0.10)


def test_no_odds_returns_none():
    row, bankroll = TB.score_threshold_bet(
        model_prob_raw=0.15, alt_odds_info={"bookmaker": "draftkings"},
        vig_market_key="k_2plus", game_pk=1, bankroll=500.0,
        prefetched_stakes={}, pending_stakes={}, cfg=_cfg(), gate_suppressed=False,
    )
    assert row is None
    assert bankroll == 500.0


def test_positive_edge_triggers_a_stake():
    """odds=+800 -> vig-inclusive implied prob ~11.1%, devigged at 10% vig ->
    ~10.1% fair. Model says 20% -> a real, large edge -> must trigger."""
    row, bankroll = TB.score_threshold_bet(
        model_prob_raw=0.20,
        alt_odds_info={"odds": 800, "bookmaker": "draftkings", "line": 2,
                       "away_team": "NYY", "home_team": "BOS"},
        vig_market_key="k_2plus", game_pk=1, bankroll=500.0,
        prefetched_stakes={}, pending_stakes={}, cfg=_cfg(), gate_suppressed=False,
    )
    assert row is not None
    assert row["kelly_triggered"] is True
    assert row["stake"] > 0
    assert row["model_prob"] == 0.2
    assert row["edge"] > 0.03
    assert row["line"] == 2
    assert row["away_team"] == "NYY" and row["home_team"] == "BOS"


def test_negative_edge_does_not_trigger():
    """Model agrees almost exactly with the vig-inclusive market price --
    after devigging, the model is actually worse than fair -- must not
    trigger, but still returns a logged (not staked) row."""
    row, _ = TB.score_threshold_bet(
        model_prob_raw=0.05,
        alt_odds_info={"odds": 1500, "bookmaker": "draftkings", "line": 3},
        vig_market_key="k_3plus", game_pk=1, bankroll=500.0,
        prefetched_stakes={}, pending_stakes={}, cfg=_cfg(), gate_suppressed=False,
    )
    assert row is not None
    assert row["kelly_triggered"] is False
    assert row["stake"] == 0.0


def test_gate_suppression_logs_but_never_triggers():
    row, _ = TB.score_threshold_bet(
        model_prob_raw=0.30,
        alt_odds_info={"odds": 1000, "bookmaker": "draftkings", "line": 2},
        vig_market_key="k_2plus", game_pk=1, bankroll=500.0,
        prefetched_stakes={}, pending_stakes={}, cfg=_cfg(), gate_suppressed=True,
    )
    assert row is not None
    assert row["kelly_triggered"] is False


def test_second_threshold_bet_shares_the_same_per_game_cap():
    """By design (documented assumption, not a bug): a 2+ bet and a 3+ bet
    on the same game both draw from the SAME pending_stakes accumulator --
    this is what makes them share the main line's per-game system cap,
    exactly like two main-line bets on the same game_pk already do. A large
    first stake can fully consume the shared cap, correctly squeezing the
    second bet down to zero rather than each threshold getting its own
    independent allowance."""
    pending: dict = {}
    prefetched = {1: 0.0}
    row1, bankroll = TB.score_threshold_bet(
        model_prob_raw=0.30, alt_odds_info={"odds": 1000, "bookmaker": "draftkings", "line": 2},
        vig_market_key="k_2plus", game_pk=1, bankroll=500.0,
        prefetched_stakes=prefetched, pending_stakes=pending, cfg=_cfg(), gate_suppressed=False,
    )
    assert row1["kelly_triggered"] is True
    assert row1["stake"] > 0
    assert pending[1] == row1["stake"]

    row2, bankroll = TB.score_threshold_bet(
        model_prob_raw=0.30, alt_odds_info={"odds": 1000, "bookmaker": "draftkings", "line": 3},
        vig_market_key="k_3plus", game_pk=1, bankroll=bankroll,
        prefetched_stakes=prefetched, pending_stakes=pending, cfg=_cfg(), gate_suppressed=False,
    )
    # Whatever room was left after bet 1 (possibly none) went to bet 2 --
    # the key assertion is that it drew from the SAME reduced budget, not
    # a fresh independent one.
    assert row2["stake"] <= max(_cfg()["cap_units"] * bankroll * 0.01 - row1["stake"], 0.0) + 1e-6
    assert pending[1] == pytest.approx(row1["stake"] + row2["stake"])

    # A single threshold bet in isolation (no main-line stake already eating
    # the cap) proves the mechanism does let a threshold bet through at all
    # when the shared budget hasn't been spent yet.
    fresh_pending: dict = {}
    row_alone, _ = TB.score_threshold_bet(
        model_prob_raw=0.30, alt_odds_info={"odds": 1000, "bookmaker": "draftkings", "line": 3},
        vig_market_key="k_3plus", game_pk=1, bankroll=500.0,
        prefetched_stakes=prefetched, pending_stakes=fresh_pending, cfg=_cfg(), gate_suppressed=False,
    )
    assert row_alone["kelly_triggered"] is True


def test_live_event_never_triggers():
    from datetime import datetime, timezone
    past = (datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    row, _ = TB.score_threshold_bet(
        model_prob_raw=0.30,
        alt_odds_info={"odds": 1000, "bookmaker": "draftkings", "line": 2,
                       "commence_time": past},
        vig_market_key="k_2plus", game_pk=1, bankroll=500.0,
        prefetched_stakes={}, pending_stakes={}, cfg=_cfg(), gate_suppressed=False,
    )
    assert row["kelly_triggered"] is False
