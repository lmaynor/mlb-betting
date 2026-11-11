"""Argon 2026-11-10 directions, PR-A gates:

  * Props D3 (NBA): assists out of the official signal allowlist; points / pra stay display-only;
    BLEND_C_BY_SPORT_STAT carries the empirical walk-forward constants.
  * Props D4 (NHL trained skaters): goals / assists / points out of standalone official eligibility
    (saves untouched); goals + assists ONLY enter the shadow cohort on exchange / soft-book prices
    with hold < 4.5%; points has no cohort.
  * Model-improvement D3 (NFL): hierarchical NFL rows are absent from served output even with
    NFL_ENGINE=hierarchical set, and can never be official.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from ev import daily_projections as DP
from ev import model_picks as MP

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def row(**kw):
    r = {
        "sport": "nba", "stat": "rebounds", "player": "Jalen Duren", "player_norm": "id4594268", "team": "DET", "opp": "CHI",
        "game_key": "nba:2026-11-11:CHI@DET", "commence": (NOW + timedelta(hours=4)).isoformat(), "mins_to_start": 240.0,
        "has_line": True, "line": 10.5, "side": "over", "proj_mean": 11.8, "p_model_side": 0.58, "p_mkt_side": 0.53,
        "best_over_odds": 105, "best_over_book": "fanduel", "best_under_odds": -125, "best_under_book": "draftkings",
        "n_books": 6, "model_edge": 0.05, "market_ev": 0.0865, "confluence": True, "edge_capped": False,
        "beezy_score": 72, "engine": "empirical_ewm_v2", "validated": False,
        "p_blend_side": 0.545, "blend_ev": 0.07,
    }
    r.update(kw)
    return r


# ----------------------------------------------------------------------------------------------- D3 NBA

def test_nba_assists_cannot_become_official():
    # every other official gate passes (card gate, blend EV, sharp check): the allowlist alone demotes it
    assert MP.qualifies(row(stat="assists")) == "tracked"
    assert not MP.has_measured_signal(row(stat="assists"))
    # points / pra stay display-only (already out; verified kept out)
    for stat in ("points", "pra"):
        assert MP.qualifies(row(stat=stat)) == "tracked", stat
        assert not MP.has_measured_signal(row(stat=stat)), stat
    # the surviving NBA officials are untouched
    assert MP.qualifies(row(stat="rebounds")) == "official"
    assert MP.qualifies(row(stat="threes")) == "official"


def test_nba_blend_constants_are_the_empirical_walk_forward_values():
    nba = DP.BLEND_C_BY_SPORT_STAT["nba"]
    assert nba["rebounds"] == 0.14
    assert nba["threes"] == 0.07
    assert nba["assists"] == 0.03
    assert nba["points"] == 0.06
    assert nba["pra"] == 0.06
    # and the accessor the board actually prices with returns them
    for stat, c in (("rebounds", 0.14), ("threes", 0.07), ("assists", 0.03), ("points", 0.06), ("pra", 0.06)):
        assert DP.blend_c("nba", stat) == c, stat
    # stats the direction does not name keep their constants
    assert nba["ra"] == 0.11 and nba["pr"] == 0.06 and nba["pa"] == 0.05
    assert nba["steals"] == 0.05 and nba["blocks"] == 0.05


# ----------------------------------------------------------------------------------------------- D4 NHL

def trained(stat, **kw):
    return row(sport="nhl", stat=stat, nhl_model="trained_v1", line=0.5, **kw)


def test_trained_skater_stats_cannot_become_official_but_saves_can():
    for stat in ("goals", "assists", "points"):
        assert MP.qualifies(trained(stat)) == "tracked", stat          # paper tracking keeps running
        assert not MP.has_measured_signal(trained(stat)), stat
    assert MP.qualifies(trained("saves")) == "official"                # saves officials untouched
    assert MP.has_measured_signal(trained("saves"))


def test_shadow_cohort_price_and_hold_conditions():
    # -110/-110 would be hold ~4.76% (over the 4.5% ceiling); -105/-105 is ~2.44%
    low_hold = [{"book": "novig", "over": -105, "under": -105}]
    for stat in ("goals", "assists"):
        c = MP.shadow_cohort(trained(stat, shadow_quotes=low_hold))
        assert c is not None and c["book"] == "novig" and c["source"] == "exchange" and c["hold"] < 0.045, stat
        assert MP.is_shadow_cohort(trained(stat, shadow_quotes=low_hold)), stat
    # soft books qualify too
    c = MP.shadow_cohort(trained("goals", shadow_quotes=[{"book": "fliff", "over": -105, "under": -105}]))
    assert c is not None and c["source"] == "soft_book"
    c = MP.shadow_cohort(trained("assists", shadow_quotes=[{"book": "kalshi", "over": -102, "under": -108}]))
    assert c is not None and c["source"] == "exchange"


def test_shadow_cohort_rejections():
    low_hold = [{"book": "novig", "over": -105, "under": -105}]
    # points: NO cohort, even on a qualifying price
    assert MP.shadow_cohort(trained("points", shadow_quotes=low_hold)) is None
    # saves: not a skater cohort stat
    assert MP.shadow_cohort(trained("saves", shadow_quotes=low_hold)) is None
    # board-engine (untrained) skater rows: no cohort
    assert MP.shadow_cohort(row(sport="nhl", stat="goals", line=0.5, shadow_quotes=low_hold)) is None
    # hold >= 4.5% fails: -120/-120 hold ~9.1%
    assert MP.shadow_cohort(trained("goals", shadow_quotes=[{"book": "novig", "over": -120, "under": -120}])) is None
    # a mainstream sharp-priced book is not a cohort venue, however low the hold
    assert MP.shadow_cohort(trained("goals", shadow_quotes=[{"book": "draftkings", "over": -105, "under": -105}])) is None
    # one-sided quotes cannot show a hold
    assert MP.shadow_cohort(trained("goals", shadow_quotes=[{"book": "novig", "over": -105, "under": None}])) is None


def test_shadow_cohort_board_stamp_is_recognised():
    r = trained("goals", shadow_cohort=True, shadow_book="betr", shadow_hold=0.031)
    c = MP.shadow_cohort(r)
    assert c is not None and c["book"] == "betr" and c["source"] == "soft_book"
    # a stamp at/above the hold ceiling does not count
    assert MP.shadow_cohort(trained("goals", shadow_cohort=True, shadow_book="betr", shadow_hold=0.045)) is None


def test_daily_projections_shadow_stamp_matches_model_picks():
    assert DP.NHL_SHADOW_STATS == MP.NHL_SHADOW_STATS
    assert DP.NHL_SHADOW_BOOKS == MP.NHL_SHADOW_BOOKS
    assert DP.NHL_SHADOW_MAX_HOLD == MP.NHL_SHADOW_MAX_HOLD
    class Q:                                                            # ev.props.Quote-shaped stand-in
        def __init__(self, book, over, under):
            self.book, self.over, self.under = book, over, under
    f = DP.nhl_shadow_cohort_fields("goals", True, [Q("kalshi", -105, -105)])
    assert f["shadow_cohort"] and f["shadow_book"] == "kalshi" and f["shadow_source"] == "exchange" and f["shadow_hold"] < 0.045
    assert not DP.nhl_shadow_cohort_fields("points", True, [Q("kalshi", -105, -105)])["shadow_cohort"]
    assert not DP.nhl_shadow_cohort_fields("goals", False, [Q("kalshi", -105, -105)])["shadow_cohort"]
    assert not DP.nhl_shadow_cohort_fields("goals", True, [Q("kalshi", -120, -120)])["shadow_cohort"]


def test_shadow_cohort_pick_is_logged_as_tracked_with_the_stamp():
    r = trained("goals", shadow_quotes=[{"book": "novig", "over": -105, "under": -105}])
    picks = MP.select({"rows": [r]})
    assert len(picks) == 1 and picks[0]["tier"] == "tracked"
    assert picks[0]["shadow_cohort"]["book"] == "novig"


# ----------------------------------------------------------------------------------------------- D3 NFL

def nfl_row(stat, engine):
    return row(sport="nfl", stat=stat, engine=engine, line=62.5)


def test_hierarchical_nfl_rows_are_not_live_served_even_with_engine_on(monkeypatch):
    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    rows = [nfl_row("rec_yds", "hierarchical_v1"), nfl_row("pass_tds", "empirical_ewm_v2+hierarchical_v1"),
            nfl_row("rush_yds", "hierarchical_v1"), nfl_row("pass_yds", "empirical_ewm_v2"),
            row(sport="nba", stat="rebounds", engine="hierarchical_v1")]
    served = DP.live_served_rows(rows)
    assert [r["stat"] for r in served] == ["pass_yds", "rebounds"]     # legacy NFL + non-NFL rows untouched
    for stat in DP.NFL_HIER_AUDIT_STATS:
        assert DP.is_hierarchical_nfl_row(nfl_row(stat, "hierarchical_v1")), stat
        assert not DP.is_hierarchical_nfl_row(nfl_row(stat, "empirical_ewm_v2")), stat


def test_run_sport_publishes_no_hierarchical_nfl_rows(monkeypatch):
    monkeypatch.setenv("NFL_ENGINE", "hierarchical")
    hier = {**nfl_row("rec_yds", "hierarchical_v1"), "game_key": "nfl:2026-11-15:KC@BUF"}
    legacy = {**nfl_row("pass_yds", "empirical_ewm_v2"), "game_key": "nfl:2026-11-15:KC@BUF"}

    class FakeLogs:
        def latest_date(self):
            return None

    monkeypatch.setattr(DP, "build_line_rows", lambda *a, **k: ([hier, legacy], {}))
    monkeypatch.setattr(DP, "rotation_rows", lambda *a, **k: ([], {}))
    monkeypatch.setattr(DP, "build_games", lambda *a, **k: [])
    payload = DP.run_sport("nfl", NOW, logs=FakeLogs(), events=[{"x": 1}], meta={}, games=[], write=False, roster=None)
    assert [r["stat"] for r in payload["rows"]] == ["pass_yds"]
    assert payload["summary"]["hier_rows_gated"] == 1


def test_hierarchical_nfl_rows_can_never_be_official():
    for stat in ("rush_yds", "rec_yds", "pass_tds", "receptions", "pass_yds", "rush_attempts"):
        assert MP.qualifies(nfl_row(stat, "hierarchical_v1")) == "tracked", stat
        assert not MP.has_measured_signal(nfl_row(stat, "hierarchical_v1")), stat
