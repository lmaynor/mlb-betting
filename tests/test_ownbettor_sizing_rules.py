"""ev.ownbettor sizing + rule gate: Kelly math, the $100 cap, gate rejects carry stakes."""
import pytest

from ev.ownbettor import rules, sizing
from ev.ownbettor.config import BettorConfig
from ev.ownbettor.signals import Signal

CFG = BettorConfig()


def sig(**kw) -> Signal:
    base = dict(signal_id="s1", system="NHL_SOG", league="nhl", stat="sog",
                player="Some Player", event_id="ev1", matchup="A @ B", line=2.5,
                side="yes", p_hat=0.60, signal_price=0.50, limit_price_ceiling=0.51,
                net_ev_pct=20.0, game_day="2026-11-16",
                commence="2026-11-17T00:00:00+00:00", raw={})
    base.update(kw)
    return Signal(**base)


# ------------------------------------------------------------------ sizing
def test_kelly_full_matches_the_binary_form():
    assert sizing.kelly_full(0.60, 0.50) == pytest.approx(0.20)   # (q-p)/(1-p)
    assert sizing.kelly_full(0.50, 0.50) == 0.0           # no edge, no bet
    assert sizing.kelly_full(0.40, 0.50) == 0.0           # negative edge clamps to 0


def test_planned_stake_is_fractional_kelly_on_the_configured_bankroll():
    # 1000 * 0.15 * 0.20 = $30
    assert sizing.planned_stake(CFG, 0.60, 0.50) == 30.0


def test_planned_stake_hits_the_per_bet_hard_cap():
    # full Kelly (0.90-0.30)/0.70 = 0.857 -> 1000*0.15*0.857 = $128.57 -> capped at $100
    assert sizing.planned_stake(CFG, 0.90, 0.30) == 100.0


def test_contracts_fit_stake_and_cap_after_fees():
    n, cost = sizing.contracts_for(30.0, 0.50, CFG)
    assert n == 57                                          # 58 would cost $30.02 all-in
    assert cost <= 30.0
    n2, cost2 = sizing.contracts_for(500.0, 0.50, CFG)      # stake above the cap
    assert cost2 <= 100.0                                   # the $100 hard cap governs


def test_contracts_zero_when_stake_buys_nothing():
    assert sizing.contracts_for(0.0, 0.50, CFG) == (0, 0.0)
    assert sizing.contracts_for(0.40, 0.50, CFG) == (0, 0.0)


# ------------------------------------------------------------------ rule gate
def test_clean_signal_passes():
    d = rules.evaluate(sig(p_hat=0.545), CFG)     # +9.0%: inside [1.5%, 10%]
    assert d.allowed and d.reason == ""
    assert abs(d.ev_net_pct - 9.0) < 1e-9


def test_ev_floor_rejects():
    d = rules.evaluate(sig(p_hat=0.50, signal_price=0.495), CFG)   # +1.01% < 1.5%
    assert not d.allowed and d.reason == "REJ_EV_LOW"


def test_odds_ceiling_rejects_long_prices():
    d = rules.evaluate(sig(p_hat=0.30, signal_price=0.20), CFG)    # longer than +300
    assert not d.allowed and d.reason == "REJ_PRICE_BAND"


def test_ev_cap_rejects_implausible_edges():
    d = rules.evaluate(sig(p_hat=0.90, signal_price=0.50), CFG)    # +80% > 10% cap
    assert not d.allowed and d.reason == "REJ_EV_CAP"


def test_missing_limit_ceiling_rejects():
    d = rules.evaluate(sig(p_hat=0.545, limit_price_ceiling=0.0), CFG)
    assert not d.allowed and d.reason == "REJ_NO_LIMIT"
