"""PRD-04 slice 3 (adopted Nov 15, 2026; RUL-20261115-prd04-officials-scope R3): the NHL trained skater path.

  * engine invariant: only nhl_model == 'trained_v1' goals / assists / points rows enter; the board
    engine's skater rows stay Tier 3;
  * Tier 2 at floor 48, dropping to 42 when the trained-skater shadow cohort's mean CLV reaches +1.00%;
  * the blend uses the measured weights (goals .37 / assists .34 / points .175), matching the
    board's BLEND_C_TRAINED;
  * blend_ev and market_ev inside [3%, 10%], skater odds ceiling +250, lineup hard-block.

Step 0 (read-only, Nov 15, 2026): NHL_TRAINED_PROPS=1 IS set on the Cloud Run job `daily-projections`
(the board builder), so trained rows serve on the production board. It is NOT set on `props-model-picks`
(the picks job, where this gate runs) -- see the slice-3 PR body for why that matters at cutover.
"""
from datetime import datetime, timedelta, timezone

import pytest

from ev import prd04_officials as P4

NOW = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)


def skater_row(**kw):
    """A passing trained assists row: p_blend at c=.34 is 0.5205, blend_ev +9.3% at the +110 best price."""
    r = {
        "sport": "nhl", "stat": "assists", "player": "Leon Draisaitl", "player_norm": "id8477934",
        "team": "EDM", "opp": "CGY", "game_key": "nhl:2026-11-15:CGY@EDM",
        "commence": (NOW + timedelta(hours=4)).isoformat(), "mins_to_start": 240.0,
        "has_line": True, "line": 0.5, "side": "over", "proj_mean": 0.9,
        "p_model_side": 0.56, "p_mkt_side": 0.50,
        "best_over_odds": 110, "best_over_book": "fanduel", "best_under_odds": -130,
        "best_under_book": "draftkings", "n_books": 6, "model_edge": 0.05, "market_ev": 0.05,
        "confluence": True, "edge_capped": False, "beezy_score": 55, "engine": "empirical_ewm_v2",
        "nhl_model": "trained_v1", "validated": False,
        "p_blend_side": 0.5205, "blend_ev": 0.093,        # the board's stamps (BLEND_C_TRAINED .34)
        "p_sharp_ref_over": 0.51, "p_sharp_ref_book": "pinnacle", "n_sharp_ref": 1,
        "p_cons_w_over": 0.51, "cons_disp": 0.01, "lineup_status": None,
    }
    r.update(kw)
    return r


# ------------------------------------------------------------------------------------- the engine invariant

def test_only_trained_skater_rows_enter_the_path():
    assert P4.is_trained_skater(skater_row())
    assert P4.is_trained_skater(skater_row(stat="goals"))
    assert P4.is_trained_skater(skater_row(stat="points"))
    assert not P4.is_trained_skater(skater_row(nhl_model="empirical_ewm_v2"))
    assert not P4.is_trained_skater(skater_row(nhl_model=None))
    assert not P4.is_trained_skater(skater_row(stat="saves"))        # saves is not a skater market
    assert not P4.is_trained_skater(skater_row(stat="sog"))
    assert P4.market_tier(skater_row()) == 2
    assert P4.market_tier(skater_row(nhl_model="empirical_ewm_v2")) == 3


def test_board_engine_skater_row_is_blocked_however_good():
    v = P4.shadow_verdict(skater_row(nhl_model="empirical_ewm_v2", beezy_score=95))
    assert v["tier"] == 3 and not v["would_be_official"] and v["reasons"] == ["tier_3_blocked"]
    assert v["trained_path"] is False


# ------------------------------------------------------------------------------------- the repriced blend

def test_trained_blend_uses_measured_board_weights():
    p_blend, blend_ev, c = P4.prd04_blend(skater_row())
    assert c == 0.34
    assert p_blend == pytest.approx(0.5205, abs=1e-3)                # sigmoid(0.34 * logit(0.56))
    assert blend_ev == pytest.approx(0.0930, abs=1e-3)               # at the +110 best price
    v = P4.shadow_verdict(skater_row())
    assert v["blend_c"] == 0.34                                      # the board's BLEND_C_TRAINED, still recorded
    assert v["blend_c_prd04"] == 0.34 and v["blend_ev_prd04"] == pytest.approx(0.0930, abs=1e-3)
    assert v["trained_path"] is True


def test_trained_goals_weight_is_037():
    _, _, c = P4.prd04_blend(skater_row(stat="goals", p_model_side=0.60, p_mkt_side=0.50))
    assert c == 0.37


# ------------------------------------------------------------------------------------- the gate

def test_trained_skater_row_would_be_official():
    v = P4.shadow_verdict(skater_row())
    assert v["would_be_official"] and v["reasons"] == [] and v["tier"] == 2 and v["floor"] == 48


def test_trained_skater_floor_drops_to_42_on_the_cohort_clv_rule():
    assert P4.trained_skater_floor(None) == 48
    assert P4.trained_skater_floor(0.009) == 48
    assert P4.trained_skater_floor(0.010) == 42
    assert P4.trained_skater_floor(0.025) == 42
    low = skater_row(beezy_score=45)
    v = P4.shadow_verdict(low)
    assert not v["would_be_official"] and "score_below_floor_48" in v["reasons"]
    v = P4.shadow_verdict(low, skater_cohort_clv=0.012)
    assert v["would_be_official"] and v["floor"] == 42


def test_trained_skater_blend_band_floor_is_3pct_not_2p5():
    # goals at c=.37: p_blend 0.5140, blend_ev +2.8% at +100 -- inside the general [2.5%, 10%] band,
    # below the trained path's 3% floor
    r = skater_row(stat="goals", p_model_side=0.5378, p_mkt_side=0.50, best_over_odds=100,
                   p_sharp_ref_over=0.51)
    v = P4.shadow_verdict(r)
    assert v["blend_ev_prd04"] == pytest.approx(0.028, abs=1e-3)
    assert not v["would_be_official"] and "blend_ev_out_of_band" in v["reasons"]


def test_trained_skater_odds_ceiling_is_plus_250():
    v = P4.shadow_verdict(skater_row(best_over_odds=300))
    assert not v["would_be_official"] and "skater_odds_above_ceiling" in v["reasons"]
    assert "odds_above_ceiling" not in v["reasons"]                  # +300 is inside the global +500
    v = P4.shadow_verdict(skater_row(best_over_odds=250, p_model_side=0.66))
    assert "skater_odds_above_ceiling" not in v["reasons"]


def test_trained_skater_lineup_hard_block():
    for status, reason in (("OUT", "lineup_block_out"), ("IR", "lineup_block_ir"), ("DTD", "lineup_block_dtd")):
        v = P4.shadow_verdict(skater_row(lineup_status=status))
        assert not v["would_be_official"] and reason in v["reasons"], status
    v = P4.shadow_verdict(skater_row(status_uncertain=True))
    assert not v["would_be_official"] and "status_uncertain" in v["reasons"]
    assert P4.shadow_verdict(skater_row(lineup_status=None))["would_be_official"]


def test_trained_skater_still_needs_the_sharp_reference():
    v = P4.shadow_verdict(skater_row(p_sharp_ref_over=None, n_sharp_ref=0))
    assert not v["would_be_official"] and "sharp_check_missing" in v["reasons"]
