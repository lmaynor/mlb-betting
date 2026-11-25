"""PRD-04 slice 1 (adopted Nov 15, 2026; RUL-20261115-prd04-officials-scope R2+R3):

  * blend unification: ev.daily_projections.BLEND_C_BY_SPORT_STAT is the ONE live table (assists 0.00,
    every unfitted prior 0.00, fitted values unchanged); mlb_core.risk.calibration's study BLEND_C is
    retired and market_blend_prob resolves its default c from the unified table.
  * the tiered shadow gate in ev.prd04_officials: Tier 1 floor 40, Tier 2 floor 48, Tier 3 blocked,
    MAX_BLEND_EV = 0.100 cap, +500 odds ceiling -- computed and logged ALONGSIDE the live gate, which
    is untouched (the live verdicts asserted here are the pre-PRD-04 ones).
"""
from datetime import datetime, timedelta, timezone

import pytest

from ev import daily_projections as DP
from ev import model_picks as MP
from ev import prd04_officials as P4

NOW = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)


def row(**kw):
    r = {
        "sport": "nba", "stat": "rebounds", "player": "Jalen Duren", "player_norm": "id4594268", "team": "DET", "opp": "CHI",
        "game_key": "nba:2026-11-15:CHI@DET", "commence": (NOW + timedelta(hours=4)).isoformat(), "mins_to_start": 240.0,
        "has_line": True, "line": 10.5, "side": "over", "proj_mean": 11.8, "p_model_side": 0.58, "p_mkt_side": 0.53,
        "best_over_odds": 105, "best_over_book": "fanduel", "best_under_odds": -125, "best_under_book": "draftkings",
        "n_books": 6, "model_edge": 0.05, "market_ev": 0.0865, "confluence": True, "edge_capped": False,
        "beezy_score": 72, "engine": "empirical_ewm_v2", "validated": False,
        "p_blend_side": 0.545, "blend_ev": 0.07,
        # slice 2: the reference stamps a post-slice-2 board row carries (a passing sharp reference:
        # gap 0.545 - 0.53 = 0.015 <= 0.025 and 0.53 * 2.05 >= 1.005 at the +105 best price)
        "p_sharp_ref_over": 0.53, "p_sharp_ref_book": "pinnacle", "n_sharp_ref": 1,
        "p_cons_w_over": 0.53, "cons_disp": 0.01,
    }
    r.update(kw)
    return r


def nhl_row(**kw):
    base = {"sport": "nhl", "game_key": "nhl:2026-11-15:NYR@BOS", "player": "Igor Shesterkin",
            "player_norm": "id8475170", "team": "NYR", "opp": "BOS", "line": 27.5}
    base.update(kw)
    return row(**base)


def saves_row(**kw):
    base = {"stat": "saves", "saves_starter_dual_confirmed": True, "saves_regime_blocked": False,
            "saves_median_edge": 0.05, "saves_median_n_books": 6}
    base.update(kw)
    return nhl_row(**base)


# ------------------------------------------------------------------------------------- blend unification

def test_unified_table_values():
    nba, nhl = DP.BLEND_C_BY_SPORT_STAT["nba"], DP.BLEND_C_BY_SPORT_STAT["nhl"]
    assert nba["assists"] == 0.00
    for stat in ("ra", "pr", "pa", "steals"):
        assert nba[stat] == 0.00, stat                       # unfitted priors zeroed
    # MOD-02 (2026-11-24): nba blocks moved 0.00 -> 0.03 and nhl sog moved 0.30 -> 0.29 (board-engine refit)
    assert nba["blocks"] == 0.03
    for stat in ("blocked_shots", "pp_points"):
        assert nhl[stat] == 0.00, stat
    # fitted values unchanged -- including every market official-eligible today
    assert (nba["threes"], nba["rebounds"], nba["points"], nba["pra"]) == (0.07, 0.14, 0.06, 0.06)
    assert (nhl["sog"], nhl["saves"], nhl["goals"], nhl["assists"], nhl["points"]) == (0.29, 0.15, 0.03, 0.06, 0.03)
    assert DP.BLEND_PRIOR_PENDING_BOARD_FIT == frozenset({("nhl", "sog"), ("nhl", "saves")})


def test_calibration_study_table_is_retired_and_defaults_come_from_the_unified_table():
    from mlb_core.risk import calibration
    assert not hasattr(calibration, "BLEND_C")
    mbp = calibration.market_blend_prob
    # assists is 0.00 in the unified table: the blend IS the market now (the study table said .18)
    assert mbp(0.50, 0.60, "nba", "assists") == pytest.approx(0.50)
    # threes resolves to the live .07, not the study .28: a strictly smaller move toward the model
    p = mbp(0.50, 0.60, "nba", "threes")
    assert 0.50 < p < 0.52
    # unlisted still means c=0
    assert mbp(0.55, 0.70, "nfl", "pass_yds") == pytest.approx(0.55)


# ------------------------------------------------------------------------------------- tiers and floors

def test_market_tiers():
    assert P4.market_tier(row(stat="rebounds")) == 1
    assert P4.market_tier(row(stat="threes")) == 1
    assert P4.market_tier(row(stat="points")) == 1
    assert P4.market_tier(nhl_row(stat="sog")) == 2
    assert P4.market_tier(saves_row()) == 2
    for stat in ("assists", "pra", "pr", "pa", "ra", "steals", "blocks"):
        assert P4.market_tier(row(stat=stat)) == 3, stat
    for stat in ("goals", "assists", "points", "blocked_shots", "pp_points"):
        assert P4.market_tier(nhl_row(stat=stat)) == 3, stat
    # slice 3 opened the trained path: trained skater rows are Tier 2 (the board engine's stay Tier 3
    # in the loop above); trained saves is Tier 2 via TIER2_MARKETS
    assert P4.market_tier(nhl_row(stat="goals", nhl_model="trained_v1")) == 2
    assert P4.market_tier(nhl_row(stat="assists", nhl_model="trained_v1")) == 2
    assert P4.market_tier(nhl_row(stat="points", nhl_model="trained_v1")) == 2
    assert P4.market_tier(saves_row(nhl_model="trained_v1")) == 2
    # every production NFL market, anytime_td included
    assert P4.market_tier(row(sport="nfl", stat="pass_yds")) == 3
    assert P4.market_tier(row(sport="nfl", stat="anytime_td")) == 3


def test_tier2_floor_drops_only_when_trained_props_serve(monkeypatch):
    monkeypatch.delenv("NHL_TRAINED_PROPS", raising=False)
    assert P4.tier2_floor() == 48
    monkeypatch.setenv("NHL_TRAINED_PROPS", "1")
    assert P4.tier2_floor() == 42


# ------------------------------------------------------------------------------------- the shadow gate

def test_tier1_row_would_be_official_and_the_live_gate_is_untouched():
    v = P4.shadow_verdict(row(), live_tier=MP.qualifies(row()))
    assert v["would_be_official"] and v["reasons"] == [] and v["tier"] == 1 and v["floor"] == 40
    # points is the headline change: live says tracked (not allowlisted), PRD-04 Tier 1 says official
    pts = row(stat="points")
    assert MP.qualifies(pts) == "tracked"
    v = P4.shadow_verdict(pts, live_tier="tracked")
    assert v["would_be_official"] and v["tier"] == 1


def test_tier1_score_floor_is_40():
    assert P4.shadow_verdict(row(beezy_score=40))["would_be_official"]
    v = P4.shadow_verdict(row(beezy_score=39))
    assert not v["would_be_official"] and "score_below_floor_40" in v["reasons"]


def test_tier3_rows_are_blocked_however_good_the_numbers():
    v = P4.shadow_verdict(row(stat="assists", beezy_score=95, blend_ev=0.09))
    assert v["tier"] == 3 and not v["would_be_official"] and v["reasons"] == ["tier_3_blocked"]
    assert v["blend_c"] == 0.0 and v["blend_c_zero"]


def test_blend_ev_cap_and_floor():
    assert P4.MAX_BLEND_EV == 0.100
    v = P4.shadow_verdict(row(blend_ev=0.101))
    assert not v["would_be_official"] and "blend_ev_out_of_band" in v["reasons"]
    assert P4.shadow_verdict(row(blend_ev=0.100))["would_be_official"]
    v = P4.shadow_verdict(row(blend_ev=0.024))
    assert not v["would_be_official"] and "blend_ev_out_of_band" in v["reasons"]
    assert P4.shadow_verdict(row(blend_ev=0.025))["would_be_official"]


def test_odds_ceiling_plus_500():
    assert P4.shadow_verdict(row(best_over_odds=500))["would_be_official"]
    v = P4.shadow_verdict(row(best_over_odds=501))
    assert not v["would_be_official"] and "odds_above_ceiling" in v["reasons"]


def test_tier2_sog_needs_score_48():
    assert not P4.shadow_verdict(nhl_row(stat="sog", beezy_score=47))["would_be_official"]
    assert P4.shadow_verdict(nhl_row(stat="sog", beezy_score=48))["would_be_official"]


def test_tier2_saves_needs_goalie_confirmation():
    v = P4.shadow_verdict(saves_row(beezy_score=60))
    assert v["would_be_official"] and v["goalie_confirmed"]
    v = P4.shadow_verdict(saves_row(beezy_score=60, saves_starter_dual_confirmed=False))
    assert not v["would_be_official"] and "goalie_not_confirmed" in v["reasons"]


def test_shadow_gate_still_requires_the_live_stack():
    v = P4.shadow_verdict(row(n_books=3))
    assert not v["would_be_official"] and "n_books<4" in v["reasons"]
    v = P4.shadow_verdict(row(market_ev=0.12))
    assert not v["would_be_official"] and "market_ev_out_of_band" in v["reasons"]
    # slice 2: the fail-closed sharp check replaced the live gate's sharp_agrees inside the shadow gate.
    # A row with no sharp reference fails closed; a reference the blend disagrees with fails it too.
    v = P4.shadow_verdict(row(p_sharp_ref_over=None, n_sharp_ref=0))
    assert not v["would_be_official"] and "sharp_check_missing" in v["reasons"]
    v = P4.shadow_verdict(row(p_sharp_ref_over=0.40))
    assert not v["would_be_official"] and "sharp_check_fail" in v["reasons"]


# ------------------------------------------------------------------------------------- payload + logging

def test_evaluate_payload_only_judges_rows_the_live_path_tracks():
    payload = {"rows": [row(), row(stat="assists"), row(confluence=False), row(has_line=False)]}
    recs = P4.evaluate_payload(payload, NOW)
    assert len(recs) == 2
    assert {r["stat"] for r in recs} == {"rebounds", "assists"}
    assert recs[0]["live_tier"] == "official"          # rebounds is a live official today
    assert recs[1]["live_tier"] == "tracked" and recs[1]["tier"] == 3


def test_shadow_flag_parsing(monkeypatch):
    monkeypatch.delenv("PRD04_SHADOW", raising=False)
    assert not P4.shadow_enabled()
    monkeypatch.setenv("PRD04_SHADOW", "1")
    assert P4.shadow_enabled()
    monkeypatch.setenv("PRD04_SHADOW", "0")
    assert not P4.shadow_enabled()


def test_log_shadow_first_sighting_wins(monkeypatch):
    store: dict[str, bytes] = {}
    from mlb_core import storage
    monkeypatch.setattr(storage, "exists", lambda k: k in store)
    monkeypatch.setattr(storage, "read_bytes", lambda k: store[k])
    monkeypatch.setattr(storage, "write_bytes", lambda data, k: store.__setitem__(k, data))
    recs = P4.evaluate_payload({"rows": [row(), row(stat="assists")]}, NOW)
    assert P4.log_shadow("nba", recs, NOW) == 2
    assert P4.log_shadow("nba", recs, NOW) == 0                       # same record_keys: nothing new
    import json
    saved = json.loads(store[P4.shadow_key("nba", "2026-11-15")].decode())
    assert len(saved["records"]) == 2 and saved["sport"] == "nba"
