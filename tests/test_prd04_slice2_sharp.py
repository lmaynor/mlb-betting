"""PRD-04 slice 2 (adopted Nov 15, 2026; RUL-20261115-prd04-officials-scope R2+R3):

  * the sharp-weighted consensus stamped at board assembly (ev.daily_projections.prd04_consensus_fields):
    sharp 2.0 at hold <= 6.5%, primary US retail 1.0, secondary / soft 0.5, DFS / offshore 0.0;
  * p_sharp_ref: Pinnacle's fair, else the lowest-hold Novig / ProphetX fair, bet book excluded;
  * the shadow gate's sharp check is FAIL-CLOSED (ev.prd04_officials.sharp_check): p_blend within +2.5pp
    of the reference AND the reference clearing +0.5% EV at the best price; no reference -> fail, with
    the round-3 Kalshi fallback (veto only) and the OPENER unconfirmed-sharp bypass as the only escapes;
  * the Kalshi fallback can never activate on today's ParlayAPI Kalshi quotes (no depth / trade stamps).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ev import daily_projections as DP
from ev import prd04_officials as P4
from ev.props import Quote

NOW = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)


def cons(book_quotes):
    """A PropConsensus-shaped stub: book_quotes = ((book, over_am, under_am, fair_over), ...)."""
    return SimpleNamespace(book_quotes=tuple(book_quotes),
                           book_fair=tuple((b, f) for b, _, _, f in book_quotes),
                           p_over=0.52, n_books=len(book_quotes), disp=0.01)


BEST = {"over": ("fanduel", 2.05, 105), "under": ("draftkings", 1.80, -125)}


def row(**kw):
    r = {
        "sport": "nba", "stat": "rebounds", "player": "Jalen Duren", "side": "over", "line": 10.5,
        "best_over_odds": 105, "best_over_book": "fanduel", "best_under_odds": -125,
        "best_under_book": "draftkings", "n_books": 6, "p_blend_side": 0.545, "cons_disp": 0.01,
    }
    r.update(kw)
    return r


# ------------------------------------------------------------------------------------- book weights

def test_book_weight_taxonomy():
    tight = 0.0476                                   # -110 / -110
    assert DP.prd04_book_weight("pinnacle", tight) == 2.0
    assert DP.prd04_book_weight("novig", tight) == 2.0
    assert DP.prd04_book_weight("prophetx", 0.02) == 2.0
    assert DP.prd04_book_weight("pinnacle", 0.09) == 0.5      # fat hold: not sharp-grade on this quote
    assert DP.prd04_book_weight("novig", None) == 0.5
    for b in ("draftkings", "fanduel", "betmgm", "caesars", "espnbet", "betrivers"):
        assert DP.prd04_book_weight(b, tight) == 1.0, b
    assert DP.prd04_book_weight("fanatics", tight) == 0.5     # secondary / soft
    assert DP.prd04_book_weight("bovada", tight) == 0.0       # offshore
    assert DP.prd04_book_weight("sleeper", tight) == 0.0      # DFS


def test_weighted_median():
    assert DP._weighted_median([(0.50, 0.5), (0.52, 1.0), (0.54, 2.0)]) == 0.54
    assert DP._weighted_median([(0.50, 1.0), (0.60, 1.0)]) == 0.50
    assert DP._weighted_median([(0.50, 0.0)]) is None
    assert DP._weighted_median([]) is None


# ------------------------------------------------------------------------------------- consensus fields

def test_weighted_consensus_pulls_toward_the_sharp_book():
    c = cons([("pinnacle", -110, -110, 0.54), ("draftkings", -110, -110, 0.52),
              ("fanatics", -110, -110, 0.50), ("bovada", -110, -110, 0.60)])
    f = DP.prd04_consensus_fields(c, "over", BEST)
    assert f["p_cons_w_over"] == 0.54                 # the 2.0-weighted sharp fair, not the 0.52 pack median
    assert f["n_cons_w"] == 3                         # bovada (offshore, 0.0) contributes nothing
    assert f["p_sharp_ref_over"] == 0.54 and f["p_sharp_ref_book"] == "pinnacle"
    assert f["sharp_ref_hold"] == pytest.approx(0.0476, abs=1e-3)
    assert f["n_sharp_ref"] == 1


def test_sharp_ref_falls_to_the_lowest_hold_exchange():
    c = cons([("novig", -105, -115, 0.51), ("prophetx", -108, -112, 0.53),
              ("draftkings", -110, -110, 0.52)])
    f = DP.prd04_consensus_fields(c, "over", BEST)
    # holds: novig ~0.0471, prophetx ~0.0475 -> novig is the reference
    assert f["p_sharp_ref_book"] == "novig" and f["p_sharp_ref_over"] == 0.51
    assert f["n_sharp_ref"] == 2


def test_sharp_ref_excludes_the_bet_book():
    c = cons([("novig", -105, -115, 0.51), ("prophetx", -108, -112, 0.53)])
    f = DP.prd04_consensus_fields(c, "over", {"over": ("novig", 1.95, -105), "under": None})
    assert f["p_sharp_ref_book"] == "prophetx" and f["n_sharp_ref"] == 1
    # the only sharp venue IS the bet book: no reference at all (the fallback's n_sharp == 0 case)
    f = DP.prd04_consensus_fields(cons([("novig", -105, -115, 0.51)]), "over",
                                  {"over": ("novig", 1.95, -105), "under": None})
    assert f["p_sharp_ref_over"] is None and f["n_sharp_ref"] == 0


def test_consensus_fields_empty_without_quotes():
    f = DP.prd04_consensus_fields(None, "over", BEST)
    assert f["p_cons_w_over"] is None and f["p_sharp_ref_over"] is None and f["n_sharp_ref"] == 0


# ------------------------------------------------------------------------------------- kalshi fields

def _quote(book, over, under, ts=None):
    return Quote(book=book, stat="rebounds", alt=False, player="Jalen Duren", player_norm="id1",
                 team_hint="DET", point=10.5, over=over, under=under, ts=ts)


def test_kalshi_fields_from_the_quote():
    ts = (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    f = DP.prd04_kalshi_fields([_quote("draftkings", -110, -110), _quote("kalshi", 100, -102, ts)], now=NOW)
    assert f["kalshi_mid_over"] == pytest.approx(0.4975, abs=1e-3)
    assert f["kalshi_spread"] == pytest.approx(0.0050, abs=1e-3)
    assert f["kalshi_age_sec"] == pytest.approx(300.0, abs=2.0)
    # the feed carries no depth and no trade tape: both stay None, so the fallback cannot activate
    assert f["kalshi_depth"] is None and f["kalshi_traded_30m"] is None


def test_kalshi_fields_absent_without_a_two_sided_kalshi_quote():
    f = DP.prd04_kalshi_fields([_quote("kalshi", -110, None)], now=NOW)
    assert f["kalshi_mid_over"] is None and f["kalshi_age_sec"] is None


# ------------------------------------------------------------------------------------- the fail-closed check

def test_sharp_check_pass_and_fail_against_the_reference():
    ok = P4.sharp_check(row(p_sharp_ref_over=0.53))
    assert ok["ok"] and ok["status"] == "pass" and ok["cohort_class"] == "agrees"
    assert ok["p_sharp_ref_side"] == 0.53
    gap = P4.sharp_check(row(p_sharp_ref_over=0.51))          # blend sits +3.5pp above the reference
    assert not gap["ok"] and gap["status"] == "fail" and gap["cohort_class"] == "disagrees"
    cheap = P4.sharp_check(row(p_sharp_ref_over=0.53, best_over_odds=-115, p_blend_side=0.53))
    assert not cheap["ok"] and cheap["status"] == "fail"      # 0.53 * 1.870 < 1.005: the ref clears no EV


def test_sharp_check_is_fail_closed_without_a_reference():
    v = P4.sharp_check(row())
    assert not v["ok"] and v["status"] == "missing" and v["cohort_class"] == "missing"


def test_sharp_check_under_side_uses_one_minus_the_reference():
    v = P4.sharp_check(row(side="under", p_sharp_ref_over=0.47, p_blend_side=0.545,
                           best_under_odds=105, best_under_book="draftkings"))
    assert v["ok"] and v["p_sharp_ref_side"] == pytest.approx(0.53)


# ------------------------------------------------------------------------------------- the kalshi fallback

def kalshi_row(**kw):
    base = {"kalshi_mid_over": 0.53, "kalshi_spread": 0.02, "kalshi_depth": 800,
            "kalshi_age_sec": 120.0, "kalshi_traded_30m": True}
    base.update(kw)
    return row(**base)


def test_kalshi_fallback_activates_only_when_every_condition_holds():
    assert P4.kalshi_fallback(kalshi_row(), "over")["active"]
    for bad in ({"kalshi_spread": 0.05}, {"kalshi_depth": 499}, {"kalshi_depth": None},
                {"kalshi_age_sec": 901.0}, {"kalshi_age_sec": None}, {"kalshi_traded_30m": None},
                {"kalshi_traded_30m": False}, {"kalshi_mid_over": 0.90}, {"kalshi_mid_over": None}):
        assert not P4.kalshi_fallback(kalshi_row(**bad), "over")["active"], bad


def test_kalshi_fallback_passes_a_row_the_reference_would_pass():
    v = P4.sharp_check(kalshi_row())
    assert v["ok"] and v["status"] == "kalshi_pass" and v["cohort_class"] == "missing"


def test_kalshi_fallback_vetoes_on_disagreement_and_on_price():
    v = P4.sharp_check(kalshi_row(p_blend_side=0.60))        # 7pp above the Kalshi mid
    assert not v["ok"] and v["status"] == "kalshi_veto"
    v = P4.sharp_check(kalshi_row(best_over_odds=-115, p_blend_side=0.53))
    assert not v["ok"] and v["status"] == "kalshi_veto"       # 0.53 * 1.870 < 1.005 against the mid


def test_kalshi_never_activates_on_feed_stamps():
    # a row stamped from today's ParlayAPI Kalshi quote: mid + spread + age present, depth/traded None
    v = P4.sharp_check(row(kalshi_mid_over=0.53, kalshi_spread=0.02, kalshi_age_sec=60.0))
    assert not v["ok"] and v["status"] == "missing" and v["kalshi"]["active"] is False
    assert v["kalshi"]["checks"]["depth_ok"] is False and v["kalshi"]["checks"]["traded_ok"] is False


# ------------------------------------------------------------------------------------- the opener bypass

def test_opener_bypass_only_for_the_opener_cohort():
    r = row(n_books=3, cons_disp=0.04)
    v = P4.sharp_check(r, cohort="opener")
    assert v["ok"] and v["status"] == "opener_bypass" and v["tag"] == "OPENER_UNCONFIRMED_SHARP"
    assert not P4.sharp_check(r)["ok"]                                   # intraday: still fail-closed
    assert not P4.sharp_check(row(n_books=3, cons_disp=0.05), cohort="opener")["ok"]
    assert not P4.sharp_check(row(n_books=2, cons_disp=0.04), cohort="opener")["ok"]


# ------------------------------------------------------------------------------------- verdict integration

def full_row(**kw):
    """A complete board row (the live-stack fields shadow_verdict reads), with a passing sharp reference."""
    r = {
        "sport": "nba", "stat": "rebounds", "player": "Jalen Duren", "player_norm": "id4594268",
        "team": "DET", "opp": "CHI", "game_key": "nba:2026-11-15:CHI@DET",
        "commence": (NOW + timedelta(hours=4)).isoformat(), "mins_to_start": 240.0,
        "has_line": True, "line": 10.5, "side": "over", "proj_mean": 11.8,
        "p_model_side": 0.58, "p_mkt_side": 0.53,
        "best_over_odds": 105, "best_over_book": "fanduel", "best_under_odds": -125,
        "best_under_book": "draftkings", "n_books": 6, "model_edge": 0.05, "market_ev": 0.0865,
        "confluence": True, "edge_capped": False, "beezy_score": 72, "engine": "empirical_ewm_v2",
        "validated": False, "p_blend_side": 0.545, "blend_ev": 0.07,
        "p_sharp_ref_over": 0.53, "p_sharp_ref_book": "pinnacle", "n_sharp_ref": 1,
        "p_cons_w_over": 0.53, "cons_disp": 0.01,
    }
    r.update(kw)
    return r


def test_shadow_record_carries_the_sharp_cohort_fields():
    v = P4.shadow_verdict(full_row())
    assert v["would_be_official"] and v["sharp_status"] == "pass" and v["sharp_cohort"] == "agrees"
    assert v["p_sharp_ref_book"] == "pinnacle" and v["p_cons_w_over"] == 0.53
    v = P4.shadow_verdict(full_row(p_sharp_ref_over=None, n_sharp_ref=0))
    assert not v["would_be_official"] and "sharp_check_missing" in v["reasons"]
    assert v["sharp_cohort"] == "missing" and v["kalshi_fallback_active"] is False
