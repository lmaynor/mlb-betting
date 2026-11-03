"""
Game-model ladder + game metadata (2026-11-02): every priced game carries `ladder` = the model's probability for every alternate
ML / spread / total rung, and every `games[]` entry carries the model drivers the pipeline already computes. Offline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from nba.game import run_daily as nba_run
from nba.game.model import DistributionalGameEngine, LADDER_HALFWIDTH, ladder_rungs
from nhl.game import run_daily as nhl_run
from nhl.game.matrix_pricer import (
    NHL_SPREAD_RUNGS,
    NHL_TOTAL_RUNGS,
    DistributionalNHLMatrixEngine,
)
from tests.test_game_models_real_shapes import _bank, _event, mem  # noqa: F401  (mem is a fixture)

TOL = 1.5e-4          # probabilities are rounded to 4 decimals and p_home / p_away are rounded independently


def _event_with(lines_home_spread, total, **kw):
    ev = _event(**kw)
    home, away = ev["home_team"], ev["away_team"]
    for bk in ev["bookmakers"]:
        for mk in bk["markets"]:
            if mk["key"] == "spreads":
                mk["outcomes"] = [{"name": home, "price": -110, "point": lines_home_spread},
                                  {"name": away, "price": -110, "point": -lines_home_spread}]
            elif mk["key"] == "totals":
                for o in mk["outcomes"]:
                    o["point"] = total
    return ev


def _assert_ladder_shape(lad, spread_dir=1):
    assert set(lad) == {"ml", "spread", "total"}
    assert lad["ml"]["p_home"] + lad["ml"]["p_away"] == pytest.approx(1.0, abs=TOL)
    sp, tot = lad["spread"], lad["total"]
    assert [r["line"] for r in sp] == sorted(r["line"] for r in sp) and len({r["line"] for r in sp}) == len(sp)
    assert [r["line"] for r in tot] == sorted(r["line"] for r in tot) and len({r["line"] for r in tot}) == len(tot)
    for r in sp:
        assert set(r) == {"line", "p_home", "p_away", "push"}
        # push-adjusted two-way cover probabilities (the main-line market convention): the covers sum to 1, push is the raw mass
        assert r["p_home"] + r["p_away"] == pytest.approx(1.0, abs=TOL)
        assert (r["p_home"] * (1 - r["push"]) + r["p_away"] * (1 - r["push"]) + r["push"]) == pytest.approx(1.0, abs=TOL)
        assert 0.0 <= r["push"] < 1.0
    for r in tot:
        assert set(r) == {"line", "p_over", "p_under", "push"}
        assert r["p_over"] + r["p_under"] == pytest.approx(1.0, abs=TOL)
        assert 0.0 <= r["push"] < 1.0
    # monotone: the home cover chance grows with the home line, the over chance shrinks with the total
    hs = [r["p_home"] for r in sp]
    assert all(b >= a - 1e-9 for a, b in zip(hs, hs[1:]))
    os_ = [r["p_over"] for r in tot]
    assert all(b <= a + 1e-9 for a, b in zip(os_, os_[1:]))


# ------------------------------------------------------------------------------------------------------------ NHL
@pytest.mark.parametrize("lh,la", [(3.0, 3.0), (3.4, 2.6), (2.2, 2.0), (4.2, 1.9)])
def test_nhl_matrix_ladder_sums_monotone_and_whole_number_totals_push(lh, la):
    r = DistributionalNHLMatrixEngine().compute_score_matrix(lh, la)
    lad = r.ladder()
    assert [x["line"] for x in lad["spread"]] == list(NHL_SPREAD_RUNGS)
    assert [x["line"] for x in lad["total"]] == list(NHL_TOTAL_RUNGS)
    _assert_ladder_shape(lad)
    by_line = {x["line"]: x for x in lad["total"]}
    assert by_line[6.0]["push"] > 0.05 and by_line[6.5]["push"] == 0.0 and by_line[5.0]["push"] > 0.0
    assert all(x["push"] == 0.0 for x in lad["spread"])                # the spread rung set is half-point lines only
    # raw (un-adjusted) mass of a total rung re-adds to 1 with the over / under raw masses
    po, pu, pp, fair = r.total(6.0)
    assert po + pu + pp == pytest.approx(1.0, abs=1e-9) and by_line[6.0]["p_over"] == pytest.approx(fair, abs=1e-4)


def test_nhl_ladder_accepts_whole_number_spread_lines_and_pushes_them():
    r = DistributionalNHLMatrixEngine().compute_score_matrix(3.1, 2.8)
    lad = r.ladder(spread_lines=[-2.0, -1.5, -1.0, -0.5], total_lines=[6.0])
    by = {x["line"]: x for x in lad["spread"]}
    assert by[-1.0]["push"] > 0.05 and by[-2.0]["push"] > 0.0 and by[-1.5]["push"] == 0.0
    ph, pa, pp = r.puck_line_two_way(-1.0)
    assert (by[-1.0]["p_home"], by[-1.0]["p_away"], by[-1.0]["push"]) == (round(ph, 4), round(pa, 4), round(pp, 4))


@pytest.mark.parametrize("home_line,total", [(-1.5, 6.0), (1.5, 5.5), (-2.5, 6.5), (-1.0, 6.0)])
def test_nhl_end_to_end_main_line_ladder_value_equals_the_market_value(mem, monkeypatch, home_line, total):
    ev = _event_with(home_line, total, day="2026-01-10T00:30:00Z", home="Tampa Bay Lightning", away="Los Angeles Kings")
    _bank("icehockey_nhl", [ev], mem)
    monkeypatch.setattr(nhl_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nhl_run, "load_historical_goalies", lambda: None)
    res = nhl_run.run_nhl_game_predictions(target_date="2026-01-09", now=datetime.now(timezone.utc))
    pg = res["priced_games"][0]
    lad = pg["ladder"]
    _assert_ladder_shape(lad)
    mk = {(m["system"], m["side"]): m for m in pg["markets"]}
    # moneyline
    assert lad["ml"]["p_home"] == mk[("NHL_ML", "home")]["p_model"] and lad["ml"]["p_away"] == mk[("NHL_ML", "away")]["p_model"]
    # the market's main spread / total line is on the ladder with exactly the existing market value
    sp = {x["line"]: x for x in lad["spread"]}[home_line]
    hm, am = mk[("NHL_SPREAD", "home")], mk[("NHL_SPREAD", "away")]
    assert hm["line"] == home_line
    assert (sp["p_home"], sp["p_away"], sp["push"]) == (hm["p_model"], am["p_model"], hm["push_prob"])
    tt = {x["line"]: x for x in lad["total"]}[total]
    om, um = mk[("NHL_TOTAL", "over")], mk[("NHL_TOTAL", "under")]
    assert (tt["p_over"], tt["p_under"], tt["push"]) == (om["p_model"], um["p_model"], om["push_prob"])
    assert json.loads(json.dumps(res))["priced_games"][0]["ladder"] == lad          # JSON round trip (GCS) is lossless


def test_nhl_off_range_main_line_is_merged_into_the_ladder(mem, monkeypatch):
    ev = _event_with(-1.5, 9.5, day="2026-01-10T00:30:00Z", home="Tampa Bay Lightning", away="Los Angeles Kings")
    _bank("icehockey_nhl", [ev], mem)
    monkeypatch.setattr(nhl_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nhl_run, "load_historical_goalies", lambda: None)
    res = nhl_run.run_nhl_game_predictions(target_date="2026-01-09", now=datetime.now(timezone.utc))
    lines = [x["line"] for x in res["priced_games"][0]["ladder"]["total"]]
    assert 9.5 in lines and lines == sorted(lines) and set(NHL_TOTAL_RUNGS) <= set(lines)


# ------------------------------------------------------------------------------------------------------------ NBA
@pytest.mark.parametrize("mu_m,mu_t,sig_m,sig_t", [(4.0, 224.0, 11.5, 17.0), (-7.3, 231.4, 12.5, 19.0), (0.0, 218.0, 10.0, 15.0)])
def test_nba_ladder_sums_monotone_and_rung_window(mu_m, mu_t, sig_m, sig_t):
    eng = DistributionalGameEngine()
    lad = eng.build_ladder(mu_m, sig_m, mu_t, sig_t)
    _assert_ladder_shape(lad)
    for r in lad["spread"]:
        assert abs(r["line"] - (-mu_m)) <= LADDER_HALFWIDTH and (r["line"] * 2) % 2 == 1      # x.5 lines inside +/- 8 of the mean
        assert r["push"] == 0.0
    for r in lad["total"]:
        assert abs(r["line"] - mu_t) <= LADDER_HALFWIDTH and (r["line"] * 2) % 2 == 1
    sl = [r["line"] for r in lad["spread"]]
    assert all(b - a == pytest.approx(1.0) for a, b in zip(sl, sl[1:])) and len(sl) in (16, 17)
    # a rung near the model mean is close to a coin flip
    mid = min(lad["total"], key=lambda r: abs(r["line"] - mu_t))
    assert mid["p_over"] == pytest.approx(0.5, abs=0.04)


def test_nba_integer_main_line_is_merged_and_pushes_consistently_with_the_market():
    eng = DistributionalGameEngine()
    lad = eng.build_ladder(5.0, 11.0, 225.0, 17.0, main_spread=-5.0, main_total=225.0)
    _assert_ladder_shape(lad)
    sp = {r["line"]: r for r in lad["spread"]}[-5.0]
    tt = {r["line"]: r for r in lad["total"]}[225.0]
    assert sp["push"] > 0.0 and tt["push"] > 0.0 and sp["push"] < 0.1
    ph, pa, pp = eng.price_spread(5.0, 11.0, -5.0)
    assert (sp["p_home"], sp["p_away"], sp["push"]) == (round(ph, 4), round(pa, 4), round(pp, 4))
    po, pu, pt = eng.price_total(225.0, 17.0, 225.0)
    assert (tt["p_over"], tt["p_under"], tt["push"]) == (round(po, 4), round(pu, 4), round(pt, 4))


def test_ladder_rungs_helper_is_half_points_within_the_window():
    assert ladder_rungs(0.0, 2.0) == [-1.5, -0.5, 0.5, 1.5]
    assert ladder_rungs(0.25, 1.0) == [-0.5, 0.5] and ladder_rungs(10.0, 1.0, 10.0) == [9.5, 10.0, 10.5]


def test_nba_end_to_end_main_line_ladder_value_equals_the_market_value_and_meta_has_drivers(mem, monkeypatch):
    _bank("basketball_nba", [_event("2026-01-10T23:30:00Z")], mem)
    monkeypatch.setattr(nba_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nba_run, "load_live_injuries", lambda: {})
    res = nba_run.run_nba_game_predictions(target_date="2026-01-10", now=datetime.now(timezone.utc))
    pg = res["priced_games"][0]
    lad = pg["ladder"]
    _assert_ladder_shape(lad)
    mk = {(m["system"], m["side"]): m for m in pg["markets"]}
    assert lad["ml"]["p_home"] == mk[("NBA_ML", "home")]["p_model"] and lad["ml"]["p_away"] == mk[("NBA_ML", "away")]["p_model"]
    sp = {x["line"]: x for x in lad["spread"]}[-4.5]
    assert (sp["p_home"], sp["p_away"], sp["push"]) == (mk[("NBA_SPREAD", "home")]["p_model"], mk[("NBA_SPREAD", "away")]["p_model"],
                                                       mk[("NBA_SPREAD", "home")]["push_prob"])
    tt = {x["line"]: x for x in lad["total"]}[222.5]
    assert (tt["p_over"], tt["p_under"]) == (mk[("NBA_TOTAL", "over")]["p_model"], mk[("NBA_TOTAL", "under")]["p_model"])
    assert json.loads(json.dumps(res))["priced_games"][0]["ladder"] == lad

    g = res["games"][0]
    # old consumers' fields unchanged
    assert {"game_key", "away", "home", "start"} <= set(g) and g["game_key"] == "nba:2026-01-10:MIA@BOS"
    assert g["away"] == "MIA" and g["home"] == "BOS" and g["start"] == "2026-01-10T23:30:00Z"
    # new driver fields: the real, already-computed values (same numbers as priced_games)
    assert g["mu_margin"] == pg["mu_margin"] and g["mu_total"] == pg["mu_total"] and g["exp_pace"] == pg["exp_pace"]
    assert set(g) >= {"rest_home", "rest_away", "b2b_home", "b2b_away", "b2b_road_disadvantage", "home_points_lost",
                      "away_points_lost", "injury_margin_delta", "injury_total_delta"}
    assert isinstance(g["b2b_home"], bool) and isinstance(g["b2b_road_disadvantage"], bool)
    assert not any(k in g for k in ("home_goalie", "away_goalie", "gsax"))                  # no invented goalie fields on NBA


# ------------------------------------------------------------------------------------------ payload + public API
def test_nhl_payload_has_old_and_new_fields_and_the_public_api_passes_them_through(mem, monkeypatch):
    ev = _event_with(-1.5, 6.0, day="2026-01-10T00:30:00Z", home="Tampa Bay Lightning", away="Los Angeles Kings")
    _bank("icehockey_nhl", [ev], mem)
    monkeypatch.setattr(nhl_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nhl_run, "load_historical_goalies", lambda: None)
    res = nhl_run.run_nhl_game_predictions(target_date="2026-01-09", now=datetime.now(timezone.utc))
    g = res["games"][0]
    # old fields unchanged
    assert g["game_key"] == "nhl:2026-01-09:LAK@TBL" and g["home"] == "TBL" and g["away"] == "LAK"
    assert g["commence_time"] == "2026-01-10T00:30:00Z" and g["home_name"] == "Tampa Bay Lightning"
    # driver fields (already in the pipeline) + `start`, the key the frontend GameMetadata type reads
    assert g["start"] == g["commence_time"]
    for k in ("lambda_h", "lambda_a", "exp_total", "home_goalie", "away_goalie", "is_b2b_road"):
        assert k in g
    pg = res["priced_games"][0]
    assert g["lambda_h"] == pg["lambda_h"] or g["lambda_h"] == pytest.approx(pg["lambda_h"], abs=1e-3)
    assert {"game_key", "home_team", "away_team", "game_date", "lambda_h", "lambda_a", "exp_total", "sigma_margin", "sigma_total",
            "sd_margin", "sd_total", "markets", "ladder"} <= set(pg)
    assert len(pg["markets"]) == 6 and len(res["rows"]) == 6

    # served by GET /api/public/games/projections without stripping anything
    import mlb.runners.public_api as pa
    storage_key = "EVSweep/nhl_games/predictions_latest.json"
    from mlb_core import storage
    storage.write_bytes(json.dumps(res).encode(), storage_key)
    pa._GAME_PROJ_CACHE.clear()
    try:
        out = pa.get_game_projections("nhl")
    finally:
        pa._GAME_PROJ_CACHE.clear()
    assert out["games"][0] == g and out["priced_games"][0]["ladder"] == pg["ladder"]
