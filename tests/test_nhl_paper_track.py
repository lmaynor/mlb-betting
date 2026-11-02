"""nhl.props.paper_track: snapshot x settle x report, offline, on synthetic payloads."""
import math
from datetime import datetime, timedelta, timezone

import pytest
from scipy.stats import nbinom

from nhl.props import paper_track as PT

NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=2)).isoformat()
GK = "nhl:2026-11-03:BOS@TOR"


def served(rows=None):
    base = [{"player_id": 11, "name": "Gus Goalie", "team": "TOR", "opp": "BOS", "date": "2026-11-03", "game_key": GK,
             "stat": "saves", "mu": 26.0, "alpha": 0.04},
            {"player_id": 22, "name": "Ann Forward", "team": "TOR", "opp": "BOS", "date": "2026-11-03", "game_key": GK,
             "stat": "goals", "mu": 0.4, "alpha": 0.0}]
    return {"version": "v1", "ts": NOW.isoformat(), "rows": rows or base}


def board(extra=None):
    rows = [{"game_key": GK, "player": "Gus Goalie", "player_norm": "gus goalie", "stat": "saves", "has_line": True, "line": 25.5,
             "p_over_market": 0.52, "n_books": 5, "commence": START, "team": "TOR", "opp": "BOS", "best_over_odds": -105,
             "best_under_odds": -115, "best_over_book": "dk", "best_under_book": "fd"},
            {"game_key": GK, "player": "Ann Forward", "player_norm": "ann forward", "stat": "goals", "has_line": True, "line": 0.5,
             "p_over_market": 0.30, "n_books": 4, "commence": START, "team": "TOR", "opp": "BOS", "best_over_odds": 220,
             "best_under_odds": -300, "best_over_book": "dk", "best_under_book": "fd"}]
    return {"rows": rows + (extra or [])}


def test_snapshot_prices_the_line_with_the_served_negbin():
    recs = PT.snapshot_records(NOW, served(), board())
    assert len(recs) == 2
    s = next(r for r in recs if r["stat"] == "saves")
    n = 1 / 0.04
    assert s["p_model"] == pytest.approx(1 - nbinom.cdf(25, n, n / (n + 26.0)), abs=1e-4)
    assert s["p_mkt"] == 0.52 and s["over_odds"] == -105 and s["version"] == "v1" and s["mu"] == 26.0
    g = next(r for r in recs if r["stat"] == "goals")
    assert g["p_model"] == pytest.approx(1 - math.exp(-0.4), abs=1e-4)                # alpha 0 = Poisson


def test_snapshot_skips_unpriced_started_thin_and_unserved_props():
    bad = [dict(board()["rows"][0], has_line=False), dict(board()["rows"][0], n_books=2, player_norm="gus goalie"),
           dict(board()["rows"][0], commence=(NOW - timedelta(minutes=5)).isoformat()),
           dict(board()["rows"][0], player="Nobody", player_norm="nobody"), dict(board()["rows"][0], p_over_market=None),
           dict(board()["rows"][0], stat="sog")]
    assert PT.snapshot_records(NOW, served(), {"rows": bad}) == []


def snap(ts_off, p_model, p_mkt, over=-110, under=-110, stat="saves", line=25.5, pid=11):
    return {"ts": (NOW + timedelta(minutes=ts_off)).isoformat(), "commence": START, "game_key": GK, "player_id": pid,
            "player": "x", "stat": stat, "line": line, "mu": 26.0, "alpha": 0.04, "p_model": p_model, "p_mkt": p_mkt,
            "over_odds": over, "under_odds": under, "version": "v1"}


def actuals(final=True, played=True, saves=28):
    def f(day, away, home):
        assert (day, away, home) == ("2026-11-03", "BOS", "TOR")
        return {"final": final, "players": {11: {"saves": saves, "_played": played}}}
    return f


def test_settle_keeps_first_and_last_pre_game_sighting_and_the_result():
    snaps = [snap(0, 0.60, 0.50), snap(30, 0.58, 0.53), snap(200, 0.57, 0.55)]            # the last is AFTER commence (+120 min)
    out = PT.settle_records(snaps, actuals(), NOW + timedelta(days=1))
    assert len(out) == 1
    r = out[0]
    assert r["first"]["p_model"] == 0.60 and r["last"]["p_mkt"] == 0.53 and r["n_snaps"] == 2     # in-play sighting excluded
    assert r["actual"] == 28 and r["won_over"] == 1.0 and not r["void"]


def test_settle_skips_unfinished_voids_non_dressers_and_survives_a_failing_provider():
    s = [snap(0, 0.6, 0.5)]
    assert PT.settle_records(s, actuals(final=False), NOW) == []
    v = PT.settle_records(s, actuals(played=False), NOW)[0]
    assert v["void"] and v["won_over"] is None
    def boom(*a):
        raise RuntimeError("503")
    assert PT.settle_records(s, boom, NOW) == []
    push = PT.settle_records([snap(0, 0.6, 0.5, line=26.0)], actuals(saves=26), NOW)[0]
    assert push["won_over"] is None                                                            # a push is neither


def settled(n, over_taken=False, model_p=0.60, mkt_p=0.50, win=True, clv=0.02):
    out = []
    for i in range(n):
        out.append({"game_key": f"g{i}", "player_id": i, "player": "x", "stat": "saves", "line": 25.5, "version": "v1", "n_snaps": 2,
                    "first": {"ts": "a", "p_model": model_p, "p_mkt": mkt_p, "over_odds": 100, "under_odds": -120, "mu": 26.0},
                    "last": {"ts": "b", "p_model": model_p, "p_mkt": mkt_p + (clv if over_taken else -clv), "over_odds": 100,
                             "under_odds": -120, "mu": 26.0},
                    "actual": 30 if win else 20, "void": False, "won_over": 1.0 if win else 0.0})
    return out


def test_report_roi_and_clv_of_disagreement_bets():
    rep = PT.report_from(settled(40, over_taken=True, model_p=0.60, mkt_p=0.50, win=True))["saves"]
    assert rep["n_props"] == 40 and rep["bets"]["n"] == 40
    assert rep["bets"]["roi"] == pytest.approx(1.0)                         # +100 odds, every Over wins: +1u each
    assert rep["bets"]["clv_mean"] == pytest.approx(0.02) and rep["bets"]["clv_pos"] == 1.0 and rep["bets"]["under_share"] == 0.0
    under = PT.report_from(settled(40, model_p=0.40, mkt_p=0.50, win=False))["saves"]["bets"]
    assert under["under_share"] == 1.0 and under["roi"] == pytest.approx(100 / 120)          # Under at -120 wins 0.8333u
    assert PT.report_from(settled(20, model_p=0.52, mkt_p=0.50))["saves"]["bets"]["n"] == 0  # inside the 5pp gap: no bets


def test_report_ignores_voids_and_other_stats_and_handles_empty():
    rows = settled(10) + [dict(settled(1)[0], void=True, won_over=None, actual=None), dict(settled(1)[0], stat="sog")]
    assert PT.report_from(rows)["saves"]["n_props"] == 10 and "sog" not in PT.report_from(rows)
    assert PT.report_from([]) == {}


def test_jsonl_round_trip_and_day_helpers():
    d = {}
    PT.write_jsonl("k", [{"a": 1}, {"b": [2]}], write=lambda k, b: d.__setitem__(k, b))
    assert PT.read_jsonl("k", read=lambda k: d.get(k)) == [{"a": 1}, {"b": [2]}]
    assert PT.read_jsonl("missing", read=lambda k: None) == []
    assert PT.ct_day(datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)) == "2026-11-03"        # 21:00 Central
