"""Core math for mlb.execution.kalshi: fees, price grid, order book, Kelly sizing."""
import math

import pytest

from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.config import ExecConfig, load_config
from mlb.execution.kalshi.fees import taker_fee
from mlb.execution.kalshi.price_grid import (
    PriceRange, parse_price_ranges, snap_down, snap_up,
)
from mlb.execution.kalshi.sizing import (
    Plan, Reject, kelly_fraction, net_ev, plan_order, sweep,
)

TAPERED = [PriceRange(0.0, 0.1, 0.001), PriceRange(0.1, 0.9, 0.01), PriceRange(0.9, 1.0, 0.001)]


# ---------------- fees ----------------
def test_fee_matches_schedule_worked_example():
    # fee schedule: 0.07 * 100 * 0.55 * 0.45 = 1.7325 (centicent precise)
    assert taker_fee(0.55, 100) == pytest.approx(1.7325)


def test_fee_rounds_up_to_centicent():
    # 0.07 * 1 * 0.33 * 0.67 = 0.015477 -> 0.0155
    assert taker_fee(0.33, 1) == pytest.approx(0.0155)


def test_fee_fraction_of_cost_is_007_times_one_minus_price():
    for a in (0.1, 0.3, 0.5, 0.7, 0.9):
        n = 10_000   # large n so centicent rounding is negligible
        assert taker_fee(a, n) / (a * n) == pytest.approx(0.07 * (1 - a), rel=1e-4)


def test_fee_degenerate_inputs_are_zero():
    assert taker_fee(0.0, 10) == 0.0
    assert taker_fee(1.0, 10) == 0.0
    assert taker_fee(0.5, 0) == 0.0
    assert taker_fee(0.5, 10, multiplier=0) == 0.0


def test_fee_multiplier_scales():
    assert taker_fee(0.5, 100, 2.0) == pytest.approx(2 * taker_fee(0.5, 100))


# ---------------- price grid ----------------
def test_snap_down_and_up_on_tapered_grid():
    assert snap_down(0.0265, TAPERED) == pytest.approx(0.026)
    assert snap_up(0.0265, TAPERED) == pytest.approx(0.027)
    assert snap_down(0.3649, TAPERED) == pytest.approx(0.36)
    assert snap_up(0.3601, TAPERED) == pytest.approx(0.37)
    assert snap_down(0.9005, TAPERED) == pytest.approx(0.9)


def test_snap_on_grid_price_is_identity():
    for p in (0.01, 0.36, 0.5, 0.97):
        assert snap_down(p, TAPERED) == pytest.approx(p)
        assert snap_up(p, TAPERED) == pytest.approx(p)


def test_snap_float_noise_does_not_drop_a_tick():
    # 0.29 + 0.01 style float noise must not floor to the tick below
    assert snap_down(0.1 + 0.2, [PriceRange(0.0, 1.0, 0.01)]) == pytest.approx(0.30)


def test_snap_clamps_to_grid():
    assert snap_down(1.5, TAPERED) == pytest.approx(1.0)
    assert snap_up(-0.2, TAPERED) == pytest.approx(0.0)


def test_snap_without_ranges_rounds_4dp():
    assert snap_down(0.123456, []) == pytest.approx(0.1234)
    assert snap_up(0.123456, []) == pytest.approx(0.1235)


def test_parse_price_ranges_handles_strings_and_junk():
    r = parse_price_ranges([{"start": "0.1000", "end": "0.9000", "step": "0.0100"}, {"bad": 1}])
    assert r == [PriceRange(0.1, 0.9, 0.01)]
    assert parse_price_ranges(None) == []


# ---------------- order book ----------------
# Real demo response for KXMLBGAME-26OCT032030SDMIL-SD (2026-10-03), market object said
# yes_bid .35 / yes_ask .36 / no_bid .64 / no_ask .65.
REAL = {"orderbook_fp": {
    "no_dollars": [["0.6200", "127629.69"], ["0.6300", "137387.25"], ["0.6400", "141202.73"]],
    "yes_dollars": [["0.0100", "25660.00"], ["0.3200", "7291.62"], ["0.3300", "154040.25"],
                    ["0.3400", "149508.95"], ["0.3500", "113809.41"]],
}}


def test_book_asks_derive_from_opposite_bids_and_match_market_object():
    b = Book.from_response("T", REAL, 0.0)
    assert b.best_ask("yes") == pytest.approx(0.36)     # market yes_ask_dollars
    assert b.best_bid("yes") == pytest.approx(0.35)     # market yes_bid_dollars
    assert b.best_ask("no") == pytest.approx(0.65)      # market no_ask_dollars
    assert b.best_bid("no") == pytest.approx(0.64)      # market no_bid_dollars
    assert b.top_size("yes") == pytest.approx(141202.73)
    assert [lv.price for lv in b.asks("yes")] == pytest.approx([0.36, 0.37, 0.38])
    assert b.yes_spread() == pytest.approx(0.01)
    assert b.yes_mid() == pytest.approx(0.355)
    assert b.is_two_sided() and not b.is_crossed()


def test_book_crossed_detected():
    b = Book("T", [(0.60, 10.0)], [(0.50, 10.0)])      # 0.60 + 0.50 > 1
    assert b.is_crossed()


def test_book_empty_and_malformed():
    b = Book.from_response("T", {}, 0.0)
    assert b.asks("yes") == [] and b.best_ask("no") is None and b.yes_mid() is None
    b2 = Book.from_response("T", {"orderbook_fp": {"yes_dollars": [["x", "1"], ["0.5", "0"]]}}, 0.0)
    assert b2.yes_bids == []           # non-numeric and zero-size levels dropped
    with pytest.raises(ValueError):
        b.asks("maybe")


# ---------------- kelly / ev ----------------
def test_kelly_fraction_known_values():
    assert kelly_fraction(0.6, 0.5) == pytest.approx(0.2)           # classic even-money, 60% win
    assert kelly_fraction(0.5, 0.5) == 0.0
    assert kelly_fraction(0.4, 0.5) == 0.0                          # negative edge never sizes
    assert kelly_fraction(0.9, 1.0) == 0.0                          # degenerate cost


def test_kelly_matches_utils_formula_on_same_basis():
    from mlb_core.odds.utils import kelly_pct
    p, dec = 0.55, 2.1                                              # +110
    c = 1.0 / dec
    assert kelly_fraction(p, c) == pytest.approx(kelly_pct(p, 110, fraction=1.0), rel=1e-6)


def test_kelly_maximizes_log_growth():
    p, c = 0.58, 0.5
    def g(f):
        return p * math.log(1 + f * (1 / c - 1)) + (1 - p) * math.log(1 - f)
    fs = kelly_fraction(p, c)
    assert g(fs) > g(fs * 0.8) and g(fs) > g(fs * 1.2)


def test_net_ev():
    assert net_ev(0.55, 0.5) == pytest.approx(0.10)


# ---------------- sweep ----------------
def _levels(*pairs):
    from mlb.execution.kalshi.book import Level
    return [Level(p, s) for p, s in pairs]


def test_sweep_across_levels():
    sw = sweep(_levels((0.50, 10), (0.52, 10)), 15)
    assert sw.price_cost == pytest.approx(0.5 * 10 + 0.52 * 5)
    assert sw.last_price == 0.52
    assert sw.fee == pytest.approx(taker_fee(0.50, 10) + taker_fee(0.52, 5))
    assert sw.vwap == pytest.approx(sw.price_cost / 15)


def test_sweep_insufficient_depth_is_none():
    assert sweep(_levels((0.5, 3)), 4) is None
    assert sweep(_levels((0.5, 3)), 0) is None


# ---------------- plan_order ----------------
def _cfg(**kw):
    return ExecConfig(**kw)


def _book(yes_ask_levels):
    """Book whose YES ask ladder is [(price, size), ...] (built from NO bids)."""
    no_bids = sorted((round(1 - p, 4), s) for p, s in yes_ask_levels)
    return Book("T", [(0.01, 1.0)], no_bids)


def test_plan_sizes_to_fractional_kelly():
    cfg = _cfg()
    plan = plan_order(_book([(0.50, 1000)]), "yes", 0.55, cfg, cfg.kelly_lambda, 1000.0, 1e9)
    assert isinstance(plan, Plan)
    c = 0.5 + taker_fee(0.5, 1000) / 1000       # all-in cost per contract at this level
    fs = (0.55 - c) / (1 - c)
    expect = math.floor(0.25 * fs * 1000 / c)
    assert plan.contracts == expect == 32
    assert plan.stake <= 0.25 * fs * 1000 + 1e-6
    assert (plan.contracts + 1) * c > 0.25 * fs * 1000   # one more contract would breach Kelly
    assert plan.ev == pytest.approx(0.55 / c - 1, abs=1e-4)
    assert plan.limit_price == pytest.approx(0.50)


def test_plan_rejects_ev_too_high_not_clamps():
    cfg = _cfg()
    r = plan_order(_book([(0.50, 1000)]), "yes", 0.62, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(r, Reject) and r.reason == "REJ_EV_TOO_HIGH"


def test_plan_rejects_ev_low_after_fee():
    cfg = _cfg()
    # gross edge 2c at a 50c ask is roughly a 2% edge and the 3.5% fee eats it
    r = plan_order(_book([(0.50, 1000)]), "yes", 0.52, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(r, Reject) and r.reason == "REJ_EV_LOW"


def test_plan_budget_cap_binds():
    cfg = _cfg()
    plan = plan_order(_book([(0.50, 1000)]), "yes", 0.55, cfg, 0.25, 1000.0, budget_cap=6.0)
    assert isinstance(plan, Plan) and plan.stake <= 6.0 + 1e-9 and plan.contracts == 11


def test_plan_per_bet_cap_binds():
    cfg = _cfg(bankroll=1_000_000.0)            # Kelly budget huge; per-bet cap 5% = 50k
    plan = plan_order(_book([(0.50, 10_000_000)]), "yes", 0.55, cfg, 0.25, 1_000_000.0, 1e12)
    assert isinstance(plan, Plan) and plan.stake <= cfg.per_bet_cap + 1e-6


def test_plan_size_too_small():
    cfg = _cfg()
    r = plan_order(_book([(0.50, 1000)]), "yes", 0.55, cfg, 0.25, 1000.0, budget_cap=0.1)
    assert isinstance(r, Reject) and r.reason == "REJ_SIZE_TOO_SMALL"


def test_plan_min_stake_floor():
    cfg = _cfg(min_stake=5.0)
    r = plan_order(_book([(0.50, 1000)]), "yes", 0.55, cfg, 0.25, 1000.0, budget_cap=4.0)
    assert isinstance(r, Reject) and r.reason == "REJ_SIZE_TOO_SMALL"


def test_plan_depth_limited_walks_levels_and_reprices():
    cfg = _cfg()
    plan = plan_order(_book([(0.50, 5), (0.51, 1000)]), "yes", 0.56, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(plan, Plan)
    assert plan.contracts > 5                    # walked into the second level
    assert plan.limit_price == pytest.approx(0.51)
    assert plan.vwap > 0.50                      # blended price above best ask
    assert plan.ev >= cfg.ev_min                 # EV re-checked at the blended all-in cost


def test_plan_stops_walking_when_deeper_levels_kill_ev():
    cfg = _cfg()
    # cheap sliver then a level priced past fair: must not buy the bad level
    plan = plan_order(_book([(0.48, 8), (0.60, 1000)]), "yes", 0.52, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(plan, Plan) and plan.contracts <= 8 and plan.limit_price == pytest.approx(0.48)


def test_plan_no_side_uses_yes_bids():
    cfg = _cfg()
    book = Book("T", [(0.40, 1000)], [(0.30, 10)])        # no ask = 1 - 0.40 = 0.60
    assert book.best_ask("no") == pytest.approx(0.60)
    plan = plan_order(book, "no", 0.66, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(plan, Plan) and plan.side == "no" and plan.limit_price == pytest.approx(0.60)


def test_plan_extreme_price_never_traded():
    cfg = _cfg()
    r = plan_order(_book([(0.99, 1000)]), "yes", 0.999, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(r, Reject) and r.reason == "REJ_BOOK_ILLIQUID"


def test_plan_empty_book():
    r = plan_order(Book("T", [], []), "yes", 0.6, _cfg(), 0.25, 1000.0, 1e9)
    assert isinstance(r, Reject) and r.reason == "REJ_BOOK_ILLIQUID"


def test_plan_limit_price_never_exceeds_ev_breakeven():
    """The IOC limit is the deepest level swept -- every contract at or below it was priced in."""
    cfg = _cfg()
    book = _book([(0.50, 20), (0.52, 20), (0.54, 20), (0.56, 1000)])
    plan = plan_order(book, "yes", 0.55, cfg, 0.25, 1000.0, 1e9)
    assert isinstance(plan, Plan)
    assert plan.p_hat / plan.cost_per_contract - 1 >= cfg.ev_min - 1e-9


# ---------------- config ----------------
def test_config_defaults_encode_owner_bankroll_and_demo_only():
    c = ExecConfig()
    assert c.bankroll == 1000.0 and c.kalshi_env == "demo" and c.enabled is False
    assert c.per_bet_cap == pytest.approx(50.0)
    assert c.per_game_cap == pytest.approx(40.0)
    assert c.daily_deploy_cap == pytest.approx(100.0)
    assert c.daily_loss_cap == pytest.approx(50.0)


def test_config_env_override():
    c = load_config({"KEXEC_BANKROLL": "2500", "KEXEC_ENABLED": "true",
                     "KEXEC_SERIES": "KXMLBGAME,KXMLBRFI", "KEXEC_EV_MIN": "0.04",
                     "KEXEC_MAX_ORDERS_PER_DAY": "7"})
    assert c.bankroll == 2500.0 and c.enabled is True and c.ev_min == 0.04
    assert c.series == ("KXMLBGAME", "KXMLBRFI") and c.max_orders_per_day == 7
