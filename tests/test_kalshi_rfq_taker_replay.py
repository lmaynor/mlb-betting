"""tests/test_kalshi_rfq_taker_replay.py -- Unit tests for Kalshi RFQ taker replay harness.

Tests:
1. Two-leg cross-game combo clearing Blade 3 fires and grades against outcome "no".
2. Blade 4 liability guard binds across a sequence at the $75 player cap.
3. Blade 1 flags a rotten leg when consensus-vs-masked gap >= 0.050.
4. Taker fee vs maker fee: combo clears only with halved fee; asserts PricerConfig multipliers.
5. Post-fix pricer behavior: same-player collinear containment pair rejects with REJ_SGP_COLLINEAR.
6. Tape record with no followup trade classified NO_EXECUTABLE_PRICE and never evaluated.
7. Combo with unresolvable leg recorded UNPRICED_LEG and never evaluated.
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest

from ev.combo_taker_scanner import ComboLiabilityGuard
from ev.kalshi_rfq_pricer import (
    InMemoryFairBook,
    KalshiRFQPricer,
    LegFairValue,
    PricerConfig,
)
from ev.kalshi_rfq_taker_replay import (
    candidate_from_legs,
    evaluate_taker_combo,
    load_tape_records,
    run_candidates,
)

T0 = 1_800_000_000.0


def _leg(ticker: str, game_key: str, stat: str = "total", strike: float = 45.5,
         p: float = 0.50, player: str | None = None, sport: str = "nfl") -> LegFairValue:
    return LegFairValue(
        ticker=ticker,
        sport=sport,
        game_key=game_key,
        market_class="player_prop" if player else "game_total",
        player_norm=player,
        stat=stat,
        strike=strike,
        p_cons_yes=p,
        n_books=4,
        disp=0.01,
        p_model_yes=p,
        p_true_yes=p,
        pooled=False,
        updated_ts=T0,
    )


def test_cross_game_clears_blade3_fires_and_grades_lay():
    """1. Two-leg cross-game combo clearing Blade 3 fires and grades to hand-computed PnL."""
    book = InMemoryFairBook(clock=lambda: T0)
    l1 = _leg("T1", "nfl:2026-10-25:BUF@MIA")
    l2 = _leg("T2", "nfl:2026-10-25:KC@DEN")
    book.replace_all([l1, l2])

    cand, status = candidate_from_legs(
        combo_id="c1",
        venue="kalshi",
        legs_with_sides=[("T1", "yes"), ("T2", "yes")],
        book=book,
        t=T0,
        bid_yes=0.45,
        game_start_lookup=lambda gk: T0 + 3600.0,
    )
    assert status == "OK"
    assert cand is not None

    rec = evaluate_taker_combo(cand, yes_won=False)
    assert rec["fires"] is True
    assert "QUALIFIED_NO_TAKER" in rec["reasons"]
    assert rec["yes_won"] is False

    # Hand-computed PnL per contract:
    # ask_no = 1.0 - 0.45 = 0.55
    # fee = ceil_centicent(1.0 * 0.07 * 100 * 0.55 * 0.45) / 100 = 1.7325 / 100 = 0.017325
    # NO won (yes_won=False): 1.0 - 0.55 - 0.017325 = 0.432675
    assert rec["pnl_per_contract"] == pytest.approx(0.432675)


def test_blade4_binds_at_player_cap():
    """2. Blade 4 binds across sequence: repeated fires sharing a player admitted until $75 cap."""
    book = InMemoryFairBook(clock=lambda: T0)
    legs = []
    for i in range(6):
        legs.append(_leg(f"P{i}", f"g{i}a", stat="rush_yds", strike=50.5, p=0.50, player="star_player"))
        legs.append(_leg(f"Q{i}", f"g{i}b", stat="total", strike=45.5, p=0.50, player=None))
    book.replace_all(legs)

    candidates_with_meta = []
    for i in range(6):
        cand, status = candidate_from_legs(
            combo_id=f"combo_{i}",
            venue="kalshi",
            legs_with_sides=[(f"P{i}", "yes"), (f"Q{i}", "yes")],
            book=book,
            t=T0 + i * 10.0,
            bid_yes=0.45,
            game_start_lookup=lambda gk: T0 + 3600.0,
        )
        assert status == "OK"
        candidates_with_meta.append((cand, {"combo_id": f"combo_{i}", "ts": T0 + i * 10.0}))

    guard = ComboLiabilityGuard()
    records, summary = run_candidates(candidates_with_meta, guard=guard)

    admitted_flags = [r["admitted"] for r in records]
    # Each take is min(15.0, 0.55 * 100) = $15.00 liability.
    # 5 takes * $15 = $75.00 (the player cap). The 6th take must be refused.
    assert admitted_flags == [True, True, True, True, True, False]
    assert summary["n_fires"] == 6
    assert summary["n_admitted"] == 5


def test_blade1_flags_rotten_leg():
    """3. Blade 1 flags a rotten leg when consensus-vs-masked gap >= 0.050."""
    book = InMemoryFairBook(clock=lambda: T0)
    # Leg 1: consensus 0.55 vs true 0.50 -> gap = 0.050 >= 0.050
    l1 = LegFairValue(
        ticker="L1",
        sport="nfl",
        game_key="g1",
        market_class="game_total",
        player_norm=None,
        stat="total",
        strike=45.5,
        p_cons_yes=0.55,
        n_books=4,
        disp=0.01,
        p_model_yes=0.50,
        p_true_yes=0.50,
        pooled=False,
        updated_ts=T0,
    )
    l2 = _leg("L2", "g2", p=0.50)
    book.replace_all([l1, l2])

    cand, status = candidate_from_legs(
        combo_id="c_rotten",
        venue="kalshi",
        legs_with_sides=[("L1", "yes"), ("L2", "yes")],
        book=book,
        t=T0,
        bid_yes=0.45,
        game_start_lookup=lambda gk: T0 + 3600.0,
    )
    assert status == "OK"
    rec = evaluate_taker_combo(cand)

    assert rec["has_rotten_leg"] is True
    assert "BLADE1_ROTTEN_LEG" in rec["reasons"]


def test_taker_fee_not_maker_fee():
    """4. Taker fee blocks combo whose ROI clears 0.030 only if fee were halved; assert multipliers."""
    # Assert PricerConfig fee multipliers
    cfg = PricerConfig()
    assert cfg.maker_fee_mult == 0.5
    assert cfg.taker_fee_mult == 1.0

    # 3 independent legs at p_lambda = 0.77 with bid_yes = 0.50:
    # P_true = 0.77^3 = 0.456533
    # P_cons = (0.77 + 0.008)^3 = 0.778^3 = 0.470916
    # ask_no = 0.50
    # fee_taker = 0.07 * 0.50 * 0.50 = 0.0175
    # net_true_edge = 1.0 - 0.456533 - 0.50 - 0.0175 = 0.025967 >= 0.025 (edge clears)
    # net_cons_edge_taker = 1.0 - 0.470916 - 0.50 - 0.0175 = 0.011584
    # roi_no_taker = 0.011584 / 0.50 = 0.023168 < 0.030 (fails ROI gate!)
    # If fee were halved (fee_maker = 0.00875):
    # net_cons_edge_maker = 1.0 - 0.470916 - 0.50 - 0.00875 = 0.020334
    # roi_maker = 0.020334 / 0.50 = 0.040668 >= 0.030 (would clear!)
    book = InMemoryFairBook(clock=lambda: T0)
    legs = [_leg(f"T{i}", f"g{i}", p=0.77) for i in range(3)]
    book.replace_all(legs)

    cand, status = candidate_from_legs(
        combo_id="c_fee",
        venue="kalshi",
        legs_with_sides=[(f"T{i}", "yes") for i in range(3)],
        book=book,
        t=T0,
        bid_yes=0.50,
        game_start_lookup=lambda gk: T0 + 3600.0,
    )
    assert status == "OK"
    rec = evaluate_taker_combo(cand)

    assert rec["fires"] is False
    assert "ROI_BELOW_0.030" in rec["reasons"]
    assert rec["roi_no_taker"] < 0.030

    fee_maker = 0.5 * (0.07 * rec["ask_no"] * (1.0 - rec["ask_no"]))
    roi_maker = (1.0 - rec["p_cons_yes"] - rec["ask_no"] - fee_maker) / rec["ask_no"]
    assert roi_maker >= 0.030


def test_postfix_pricer_sgp_collinear_rejection():
    """5. Same-player collinear containment pair (pra x points) rejects with REJ_SGP_COLLINEAR.

    Note: The companion assertion that an unmeasured same-player pair fails closed
    with REJ_SGP_SAME_PLAYER is already tested in tests/test_kalshi_rfq.py
    (test_same_player_in_one_game_rejected and
    test_same_player_cells_have_no_numeric_default_and_rfqs_reject_same_player).
    """
    book = InMemoryFairBook(clock=lambda: T0)
    l1 = LegFairValue(
        ticker="PRA", sport="nba", game_key="nba:2026-10-25:DEN@LAL",
        market_class="player_prop", player_norm="jokic", stat="pra", strike=45.5,
        p_cons_yes=0.50, n_books=4, disp=0.01, p_model_yes=0.50, pooled=False, updated_ts=T0,
    )
    l2 = LegFairValue(
        ticker="PTS", sport="nba", game_key="nba:2026-10-25:DEN@LAL",
        market_class="player_prop", player_norm="jokic", stat="points", strike=25.5,
        p_cons_yes=0.50, n_books=4, disp=0.01, p_model_yes=0.50, pooled=False, updated_ts=T0,
    )
    book.replace_all([l1, l2])

    pricer = KalshiRFQPricer(book, PricerConfig(allow_same_game_combos=True))
    rfq = {
        "id": "rfq_collinear",
        "contracts_fp": "1.0",
        "mve_selected_legs": [
            {"market_ticker": "PRA", "side": "yes"},
            {"market_ticker": "PTS", "side": "yes"},
        ],
    }
    decision = pricer.price(rfq, T0)
    assert not decision.quoted
    assert decision.reason == "REJ_SGP_COLLINEAR"


def test_tape_record_with_no_followup_trade_not_evaluated():
    """6. Tape record with no followup trade is classified NO_EXECUTABLE_PRICE and never evaluated."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tape_sample.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "kind": "rfq",
                "ts": T0,
                "rfq": {
                    "id": "rfq_no_trade",
                    "created_ts": "2026-10-26T00:00:00Z",
                    "mve_selected_legs": [
                        {"market_ticker": "T1", "side": "yes"},
                        {"market_ticker": "T2", "side": "yes"},
                    ],
                },
            }) + "\n")
            f.write(json.dumps({
                "kind": "followup",
                "rfq_id": "rfq_no_trade",
                "trades": [],
                "ts": T0 + 15.0,
            }) + "\n")

        recs = load_tape_records(td)
        assert len(recs) == 1
        assert recs[0]["bid_yes"] is None
        assert recs[0]["bid_src"] is None
        assert recs[0]["status"] == "NO_EXECUTABLE_PRICE"

        # Fed through run_candidates
        items = [(None, recs[0])]
        results, summary = run_candidates(items)
        assert len(results) == 1
        assert results[0]["status"] == "NO_EXECUTABLE_PRICE"
        assert summary["n_total"] == 1
        assert summary["n_evaluated"] == 0
        assert summary["n_fires"] == 0


def test_unpriced_leg_recorded_and_not_evaluated():
    """7. Combo with unresolvable leg is recorded UNPRICED_LEG and not evaluated."""
    book = InMemoryFairBook(clock=lambda: T0)
    l1 = _leg("T1", "g1")
    # T2 is omitted from the book
    book.replace_all([l1])

    cand, status = candidate_from_legs(
        combo_id="c_unpriced",
        venue="kalshi",
        legs_with_sides=[("T1", "yes"), ("T2", "yes")],
        book=book,
        t=T0,
        bid_yes=0.45,
    )
    assert cand is None
    assert status == "UNPRICED_LEG"

    items = [(cand, {
        "combo_id": "c_unpriced",
        "status": status,
        "legs": [("T1", "yes"), ("T2", "yes")],
        "ts": T0,
        "bid_yes": 0.45,
    })]
    results, summary = run_candidates(items)
    assert len(results) == 1
    assert results[0]["status"] == "UNPRICED_LEG"
    assert results[0]["reasons"] == ["UNPRICED_LEG"]
    assert summary["n_total"] == 1
    assert summary["n_evaluated"] == 0
