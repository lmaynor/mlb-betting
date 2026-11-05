"""
Regression tests for the NBA / NHL game-line engines that exercise REAL data shapes (the earlier tests passed only because they
mocked history as None and invented column names / odds payloads):

  * the NBA history loader on a frame with the REAL team_boxscores_master columns (team_abbr / opp_abbr / date, NaN
    rebounds+turnovers for 2019-24, PHO / NOR / UTH tricodes, UTC `date`)
  * the odds loader on the pointer + raw pair the REAL banker writes (nba.odds.accumulator.accumulate_game_lines)
  * Four-Factor defensive signs, Kalman predict purity + season decay, NHL matrix mean-preservation / DC tau / pushes,
    Kalshi leg plumbing for the NHL tricode mismatch, payload labelling and the stale-keep rule.
"""
from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from mlb_core import storage
from nba.config import STAT_FIELDS
from nba.game import odds_feed
from nba.game import run_daily as nba_run
from nba.game.features import (
    LEAGUE_BASELINES,
    FeatureStore,
    TeamGameStats,
    build_team_game_stats_from_raw,
    sb_team_code,
)
from nba.game.kalman_ratings import KalmanTeamTracker as NbaKalman
from nhl.game import run_daily as nhl_run
from nhl.game.kalman_ratings import KalmanTeamTracker as NhlKalman
from nhl.game.matrix_pricer import GRID_SIZE, DistributionalNHLMatrixEngine


# ----------------------------------------------------------------------------------------------- in-memory storage
@pytest.fixture
def mem(monkeypatch):
    """Replace mlb_core.storage with a dict so pointer + raw round-trip through the real banker code, nothing touches GCS."""
    blobs: dict = {}
    monkeypatch.setattr(storage, "write_bytes", lambda data, key, *a, **k: blobs.__setitem__(key, bytes(data)))
    monkeypatch.setattr(storage, "read_bytes", lambda key, *a, **k: blobs[key])
    monkeypatch.setattr(storage, "exists", lambda key, *a, **k: key in blobs)
    monkeypatch.setattr(storage, "write_csv", lambda df, key, *a, **k: blobs.__setitem__(key, b"csv"))
    return blobs


# ------------------------------------------------------------------------------------------------ NBA master shape
def _box_row(game_id, date, team, opp, is_home, pts, **over):
    """One row with exactly the columns nba/data/flatten.py writes to team_boxscores_master.csv."""
    row = {"game_id": game_id, "season_year": 2025, "season_type": "Regular Season", "date": date,
           "team_id": 1, "team_abbr": team, "is_home": is_home, "opp_id": 2, "opp_abbr": opp}
    row.update({f: 0.0 for f in STAT_FIELDS})
    row.update({"points": pts, "minutes": 240.0, "field_goals_made": 40.0, "field_goals_attempted": 88.0,
                "three_points_made": 12.0, "three_points_attempted": 34.0, "free_throws_attempted": 22.0,
                "rebounds_offensive": 10.0, "rebounds_defensive": 33.0, "turnovers": 13.0})
    row.update(over)
    return row


def _box(game_id, date, home, away, hp, ap, **over):
    return [_box_row(game_id, date, home, away, True, hp, **over), _box_row(game_id, date, away, home, False, ap, **over)]


def test_sb_team_code_resolves_sportsblaze_tricodes():
    assert sb_team_code("PHO") == "PHX" and sb_team_code("NOR") == "NOP" and sb_team_code("UTH") == "UTA"
    assert sb_team_code("GS") == "GSW" and sb_team_code("BOS") == "BOS"
    assert sb_team_code(float("nan")) is None and sb_team_code("") is None and sb_team_code("ZZZ") is None


def test_loader_real_master_columns_pho_tips_nan_and_preseason(caplog):
    rows = []
    rows += _box("g_pho", "2026-01-11", "PHO", "NOR", 112, 104)                                  # PHO/NOR tricodes, UTC date
    rows += _box("g_nan", "2026-01-12", "BOS", "MIA", 100, 99, turnovers=np.nan, rebounds_offensive=np.nan)  # 2019-24 shape
    rows += _box("g_pre", "2026-01-13", "BOS", "MIA", 90, 91, season_type="Preseason")
    rows += _box("g_xxx", "2026-01-14", "BOS", "ZZZ", 100, 100)                                 # unresolvable tricode
    rows += _box("g_ok", "2026-01-15", "UTH", "GS", 118, 110)
    df = pd.DataFrame(rows)
    tips = pd.Series({"g_pho": pd.Timestamp("2026-01-11T00:30:00Z")})                          # 7:30 pm ET on Jan 10

    with caplog.at_level(logging.WARNING, logger="nba.game.features"):
        games = build_team_game_stats_from_raw(df, tips=tips)

    by = {(g.game_id, g.team): g for g in games}
    assert set(by) == {("g_pho", "PHX"), ("g_pho", "NOP"), ("g_ok", "UTA"), ("g_ok", "GSW")}
    assert by[("g_pho", "PHX")].opp == "NOP" and by[("g_pho", "PHX")].is_home is True
    assert by[("g_pho", "PHX")].game_date == "2026-01-10"          # UTC date 01-11 -> ET game day 01-10 via the tip
    assert by[("g_ok", "UTA")].game_date == "2026-01-15"           # no tip: master date kept, and said so
    assert all(np.isfinite([g.possessions, g.off_rtg, g.def_rtg, g.tov_pct, g.oreb_pct, g.pace]).all() for g in games)
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "'nan_stats': 2" in msg and "'unknown_team': 2" in msg and "'preseason': 2" in msg   # counted, never silent
    assert "ZZZ" in msg


def test_loader_backfills_nan_rebounds_turnovers_from_player_rows(caplog):
    """Argon fix (#183), ported from PR #186's team_table: the 2019-24 team box has NaN rebounds/turnovers;
    played-player sums per (game_id, team_abbr) backfill them instead of dropping the team-games."""
    rows = _box("g_nan", "2026-01-12", "BOS", "MIA", 100, 99, turnovers=np.nan, rebounds_offensive=np.nan)
    players = []
    for team, oreb_parts, tov_parts in [("BOS", (4.0, 6.0), (8.0, 5.0)), ("MIA", (5.0, 5.0), (6.0, 5.0))]:
        for oreb, tov in zip(oreb_parts, tov_parts):
            prow = {"game_id": "g_nan", "team_abbr": team, "played": True}
            prow.update({f: 0.0 for f in STAT_FIELDS})
            prow.update({"rebounds_offensive": oreb, "rebounds_defensive": 20.0, "turnovers": tov})
            players.append(prow)
    with caplog.at_level(logging.INFO, logger="nba.game.features"):
        games = build_team_game_stats_from_raw(pd.DataFrame(rows), df_player_boxscores=pd.DataFrame(players))
    by = {(g.game_id, g.team): g for g in games}
    assert set(by) == {("g_nan", "BOS"), ("g_nan", "MIA")}
    assert all(np.isfinite([g.tov_pct, g.oreb_pct, g.dreb_pct, g.possessions]).all() for g in games)
    bos = by[("g_nan", "BOS")]
    assert abs(bos.tov_pct - 13.0 / bos.possessions) < 1e-9          # 8 + 5 player turnovers backfilled
    assert "backfilled rebounds/turnovers for 2 team-rows" in " ".join(r.getMessage() for r in caplog.records)
    # without the player frame, the same game is still dropped (fail-soft, previous behavior)
    assert build_team_game_stats_from_raw(pd.DataFrame(rows)) == []


def test_loader_rejects_the_old_invented_schema():
    bad = pd.DataFrame([{"game_id": "g", "team_abbrev": "BOS", "game_date": "2026-01-10", "points": 100}] * 2)
    with pytest.raises(ValueError, match="missing columns"):
        build_team_game_stats_from_raw(bad)


def test_et_conversion_makes_the_late_game_count_for_rest_and_b2b():
    """A 7:30 pm ET tip is stored on the NEXT UTC date. With the master date it is not 'prior' to the next ET day's game."""
    rows = _box("g1", "2026-01-11", "PHO", "NOR", 112, 104)
    df = pd.DataFrame(rows)
    with_tip = build_team_game_stats_from_raw(df, tips=pd.Series({"g1": pd.Timestamp("2026-01-11T00:30:00Z")}))
    no_tip = build_team_game_stats_from_raw(df)
    fat_tip = FeatureStore(with_tip).compute_schedule_fatigue("PHX", "BOS", "2026-01-11")
    fat_old = FeatureStore(no_tip).compute_schedule_fatigue("PHX", "BOS", "2026-01-11")
    assert fat_tip.b2b_home is True and fat_tip.rest_home == 1
    assert fat_old.b2b_home is False                                # the bug: game invisible on its own ET day


# ---------------------------------------------------------------------------------------------- Four Factors signs
def _g(team, opp, d, gid, **kw):
    base = dict(game_id=gid, game_date=d, season_year=2025, team=team, opp=opp, is_home=True, points=110.0, opp_points=108.0,
                minutes=240.0, possessions=99.0, off_rtg=LEAGUE_BASELINES["off_rtg"], def_rtg=LEAGUE_BASELINES["def_rtg"],
                net_rtg=0.0, pace=99.0, efg_pct=0.545, opp_efg_pct=0.545, tov_pct=0.135, opp_tov_pct=0.135, oreb_pct=0.27,
                dreb_pct=0.73, ftr=0.245, opp_ftr=0.245, ts_pct=0.58, threepar=0.395)
    base.update(kw)
    return TeamGameStats(**base)


def _store_with(team, **kw):
    games = [_g(team, "ZZZ", f"2025-12-{i + 1:02d}", f"{team}{i}", **kw) for i in range(20)]
    return FeatureStore(games)


def test_elite_home_defense_raises_the_home_margin_terms():
    # efg/ftr conceded LOW and turnovers forced HIGH == elite defense
    elite = _store_with("BOS", opp_efg_pct=0.47, opp_ftr=0.18, opp_tov_pct=0.19, def_rtg=104.0)
    m = elite.extract_matchup_features("BOS", "MIA", "2026-01-10")
    assert m.z_efg_diff > 0.5 and m.z_tov_diff > 0.5 and m.z_ftr_diff > 0.5 and m.z_off_def_matchup > 0.5
    away = elite.extract_matchup_features("MIA", "BOS", "2026-01-10")          # the same defense AWAY lowers the home margin
    assert away.z_efg_diff < -0.5 and away.z_tov_diff < -0.5 and away.z_ftr_diff < -0.5 and away.z_off_def_matchup < -0.5


def test_elite_home_offense_and_turnover_discipline_also_raise_home_terms():
    good = _store_with("BOS", efg_pct=0.60, ftr=0.32, tov_pct=0.10, oreb_pct=0.33, off_rtg=124.0)
    m = good.extract_matchup_features("BOS", "MIA", "2026-01-10")
    assert m.z_efg_diff > 0.5 and m.z_ftr_diff > 0.5 and m.z_tov_diff > 0.5 and m.z_oreb_diff > 0.5 and m.z_off_def_matchup > 0.5


def test_defense_that_concedes_more_hurts_the_home_side():
    leaky = _store_with("BOS", opp_efg_pct=0.60, opp_ftr=0.33, opp_tov_pct=0.09, def_rtg=124.0)
    m = leaky.extract_matchup_features("BOS", "MIA", "2026-01-10")
    assert m.z_efg_diff < 0 and m.z_tov_diff < 0 and m.z_ftr_diff < 0 and m.z_off_def_matchup < 0


def test_add_games_out_of_order_stays_sorted_and_season_shrink_uses_games_this_season():
    s = FeatureStore()
    s.add_games([_g("BOS", "MIA", "2026-01-05", "b")])
    s.add_games([_g("BOS", "MIA", "2026-01-03", "a"), _g("BOS", "MIA", "2026-01-09", "c")])
    assert [g.game_id for g in s._by_team["BOS"]] == ["a", "b", "c"]
    # 40 elite games LAST season, none this season: the opener profile is pulled toward the league, not left at 40-game strength
    last = FeatureStore([_g("BOS", "ZZZ", f"2025-0{1 + i // 20}-{1 + i % 20:02d}", f"x{i}", efg_pct=0.62) for i in range(40)])
    opener = last.compute_team_profile("BOS", "2025-10-25")["efg_pct"]
    full = last.compute_team_profile("BOS", "2025-07-01")["efg_pct"]
    assert LEAGUE_BASELINES["efg_pct"] < opener < full


# ---------------------------------------------------------------------------------------------------------- Kalman
@pytest.mark.parametrize("Tracker,away,args", [(NbaKalman, "MIA", (115, 105, 98.0, 240.0)), (NhlKalman, "MTL", (4, 2, 32, 28))])
def test_predict_is_pure_and_walk_forward_matches_production(Tracker, away, args):
    def fresh():
        t = Tracker()
        t.update_game("BOS", away, *args, game_date="2026-01-01")
        return t

    a = fresh()
    cov0 = a.get_state("BOS").cov.copy()
    p1 = a.predict_game("BOS", away, "2026-01-08")
    p2 = a.predict_game("BOS", away, "2026-01-08")
    assert p1.std_margin == pytest.approx(p2.std_margin)                          # was 17.108 -> 17.188 -> 17.268
    np.testing.assert_allclose(a.get_state("BOS").cov, cov0)
    b = fresh()                                                                    # walk-forward: predict THEN update
    b.predict_game("BOS", away, "2026-01-08")
    b.update_game("BOS", away, *args, game_date="2026-01-08")
    c = fresh()                                                                    # production replay: update only
    c.update_game("BOS", away, *args, game_date="2026-01-08")
    np.testing.assert_allclose(b.get_state("BOS").cov, c.get_state("BOS").cov)
    np.testing.assert_allclose(b.get_state("BOS").theta, c.get_state("BOS").theta)


@pytest.mark.parametrize("Tracker,away,args", [(NbaKalman, "MIA", (125, 100, 98.0, 240.0)), (NhlKalman, "MTL", (6, 1, 36, 24))])
def test_roll_season_applies_the_offseason_decay_once(Tracker, away, args):
    t = Tracker()
    assert t.roll_season("2025-11-01") is False
    t.update_game("BOS", away, *args, game_date="2026-04-01")
    theta = t.get_state("BOS").theta.copy()
    assert t.roll_season("2026-04-10") is False                                    # same season: nothing
    assert t.roll_season("2026-10-25") is True                                     # new season: decay
    np.testing.assert_allclose(t.get_state("BOS").theta, theta * 0.65)
    assert t.roll_season("2026-11-01") is False                                    # once only
    restored = Tracker.from_dict(t.to_dict())
    assert restored.current_season == t.current_season


# ----------------------------------------------------------------------------------------------------- NHL matrix
@pytest.mark.parametrize("lh,la", [(3.0, 3.0), (3.4, 2.6), (2.2, 2.0), (4.6, 1.8)])
def test_matrix_preserves_mean_goals_and_sums_to_one(lh, la):
    r = DistributionalNHLMatrixEngine().compute_score_matrix(lh, la)
    x = np.arange(GRID_SIZE)
    mh, ma = float(np.sum(r.m_final.sum(1) * x)), float(np.sum(r.m_final.sum(0) * x))
    assert mh == pytest.approx(lh, abs=2e-3) and ma == pytest.approx(la, abs=2e-3)
    assert mh + ma == pytest.approx(lh + la, abs=4e-3)                              # was lh + la + ~0.26
    for m in (r.m_stage1, r.m_stage2, r.m_final):
        assert float(m.sum()) == pytest.approx(1.0, abs=1e-9)
    assert r.p_home_ml + r.p_away_ml == pytest.approx(1.0, abs=1e-9)
    legacy = DistributionalNHLMatrixEngine().compute_score_matrix(lh, la, calibrate=False)
    assert float(np.sum(legacy.m_final * (np.arange(GRID_SIZE)[:, None] + np.arange(GRID_SIZE)[None, :]))) > lh + la + 0.1


def test_dixon_coles_tau_orientation_matches_the_standard_form():
    from scipy.stats import poisson
    lh, la, rho = 3.4, 2.1, -0.06
    r = DistributionalNHLMatrixEngine(rho_dc=rho).compute_score_matrix(lh, la, calibrate=False)
    exp = np.array([[poisson.pmf(x, lh) * poisson.pmf(y, la) for y in range(GRID_SIZE)] for x in range(GRID_SIZE)])
    exp[0, 0] *= 1 - lh * la * rho
    exp[0, 1] *= 1 + lh * rho            # home rate on the (home 0, away 1) cell
    exp[1, 0] *= 1 + la * rho            # away rate on the (home 1, away 0) cell
    exp[1, 1] *= 1 - rho
    np.testing.assert_allclose(r.m_stage1, exp / exp.sum(), atol=1e-12)


def test_totals_default_to_ex_shootout_and_pushes_are_adjusted_everywhere():
    r = DistributionalNHLMatrixEngine().compute_score_matrix(3.0, 3.0)
    assert r.total(6.0) == r.total(6.0, exclude_shootout=True)
    assert r.total(6.0)[3] < r.total(6.0, exclude_shootout=False)[3]                # shootout 'goals' no longer inflate overs
    for ex in (True, False):
        po, pu, pp, fair = r.total(6.0, exclude_shootout=ex)
        assert pp > 0.05 and po + pu + pp == pytest.approx(1.0, abs=1e-9) and fair == pytest.approx(po / (po + pu))
    ph, pa, pp = r.puck_line(-1.0)
    assert pp > 0.05                                                                # integer line: real pushes
    ah, aa, ap = r.puck_line_two_way(-1.0)
    assert ah + aa == pytest.approx(1.0) and ap == pytest.approx(pp) and ah == pytest.approx(ph / (ph + pa))
    h15, a15, p15 = r.puck_line_two_way(-1.5)
    assert p15 == 0.0 and h15 == pytest.approx(r.puck_line(-1.5)[0])
    sd_m, sd_t = r.outcome_sd()
    assert 1.8 < sd_m < 3.0 and 1.8 < sd_t < 3.0                                    # outcome dispersion, not parameter std


# ---------------------------------------------------------------------------------- odds loader on the REAL banker output
def _event(day="2026-01-10T23:30:00Z", home="Boston Celtics", away="Miami Heat", price=-110):
    def bk(key, hp, ap):
        return {"key": key, "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": hp}, {"name": away, "price": ap}]},
            {"key": "spreads", "outcomes": [{"name": home, "price": price, "point": -4.5},
                                            {"name": away, "price": price, "point": 4.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": price, "point": 222.5},
                                           {"name": "Under", "price": price, "point": 222.5}]}]}
    return {"id": "e1", "commence_time": day, "home_team": home, "away_team": away,
            "bookmakers": [bk("draftkings", -180, 150), bk("fanduel", -175, 145)]}


class _Client:
    credits_remaining = 100
    credits_last = 3

    def __init__(self, events):
        self.events = events

    def get_slate(self, sport, markets=None):
        return self.events


def _bank(sport, events, mem):
    from nba.odds.accumulator import accumulate_game_lines
    return accumulate_game_lines(sport, client=_Client(events), guard=False)


def test_pointer_is_a_count_not_events_and_the_loader_follows_it_to_the_raw_snapshot(mem):
    result = _bank("basketball_nba", [_event(), _event(home="Los Angeles Lakers", away="Phoenix Suns")], mem)
    ptr = json.loads(mem["OddsAccum/basketball_nba/latest_game_lines.json"])
    assert ptr["events"] == 2 and isinstance(ptr["events"], int) and ptr["kind"] == "game_lines"   # the shape that broke the old reader
    meta: dict = {}
    events = nba_run.load_live_game_odds(meta_out=meta)
    assert meta["ok"] is True and len(events) == 2 and all(isinstance(e, dict) for e in events)
    assert meta["raw_key"].endswith(f"raw/{result['date']}/game_lines_{result['hhmm']}.json")
    # the OLD reader on the same pointer: len() of an int raised inside a swallowed try -> no odds ever
    with pytest.raises(TypeError):
        len(ptr.get("events") or ptr.get("rows") or 0)


def test_nhl_loader_uses_its_own_sport_key_and_never_the_props_feed(mem):
    _bank("icehockey_nhl", [_event(home="Tampa Bay Lightning", away="Los Angeles Kings")], mem)
    assert len(nhl_run.load_live_game_odds()) == 1
    assert nba_run.load_live_game_odds() == []                                      # NBA pointer absent -> empty + reason
    mem["Odds/bettingpros_live/nba/latest.json"] = json.dumps({"events": [_event()], "rows": [{"x": 1}]}).encode()
    meta: dict = {}
    assert nba_run.load_live_game_odds(meta_out=meta) == [] and meta["ok"] is False   # a prop feed is NOT game odds
    assert "pointer" in meta["reason"]


def test_loader_failure_modes_carry_a_reason(mem):
    now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    ptr_key = "OddsAccum/basketball_nba/latest_game_lines.json"
    mem[ptr_key] = json.dumps({"kind": "game_lines", "date": "2026-01-10", "hhmm": "0700", "events": 3,
                               "timestamp": "2026-01-10T07:00:00+00:00"}).encode()
    ev, meta = odds_feed.read_banked_game_lines("basketball_nba", now)
    assert ev is None and "missing" in meta["reason"]
    mem["OddsAccum/basketball_nba/raw/2026-01-10/game_lines_0700.json"] = json.dumps({"events": 3}).encode()
    ev, meta = odds_feed.read_banked_game_lines("basketball_nba", now)
    assert ev is None and "not a non-empty list" in meta["reason"]
    mem["OddsAccum/basketball_nba/raw/2026-01-10/game_lines_0700.json"] = json.dumps([_event()]).encode()
    assert odds_feed.read_banked_game_lines("basketball_nba", now)[0] is not None
    ev, meta = odds_feed.read_banked_game_lines("basketball_nba", datetime(2026, 1, 11, 12, 0, tzinfo=timezone.utc))
    assert ev is None and "stale" in meta["reason"]


def test_run_nba_game_predictions_prices_todays_games_only_with_real_banker_output(mem, monkeypatch):
    today, tomorrow = _event("2026-01-10T23:30:00Z"), _event("2026-01-11T23:30:00Z", home="New York Knicks", away="Chicago Bulls")
    _bank("basketball_nba", [today, tomorrow], mem)
    monkeypatch.setattr(nba_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nba_run, "load_live_injuries", lambda: {})
    now = datetime.now(timezone.utc)
    res = nba_run.run_nba_game_predictions(target_date="2026-01-10", now=now)
    assert [g["game_key"] for g in res["games"]] == ["nba:2026-01-10:MIA@BOS"]     # tomorrow's game not keyed with today's date
    assert res["odds_meta"]["events_loaded"] == 2 and res["odds_meta"]["events_for_day"] == 1
    assert len(res["rows"]) == 6 and res["status"] == "live"
    assert res["validated"] is False and res["label"] == odds_feed.UNVALIDATED_LABEL
    assert res["engine"] == "kalman_joint_t_v1" and res["engine_version"] and res["stale"] is False
    assert all(r["validated"] is False and r["label"] for r in res["rows"])
    pg = res["priced_games"][0]
    assert pg["sd_margin"] > pg["sigma_margin"] > 0 and pg["sd_total"] > pg["sigma_total"] > 0   # t scale -> true sd


def test_run_nhl_game_predictions_end_to_end_on_real_banker_output(mem, monkeypatch):
    _bank("icehockey_nhl", [_event("2026-01-10T00:30:00Z", home="Tampa Bay Lightning", away="Los Angeles Kings")], mem)
    monkeypatch.setattr(nhl_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nhl_run, "load_historical_goalies", lambda: None)
    res = nhl_run.run_nhl_game_predictions(target_date="2026-01-09", now=datetime.now(timezone.utc))
    assert [g["game_key"] for g in res["games"]] == ["nhl:2026-01-09:LAK@TBL"]       # 7:30 pm ET Jan 9 == 00:30Z Jan 10
    r0 = res["rows"][0]
    assert r0["player"] == "LAK @ TBL" and r0["stat"] in ("moneyline", "spread", "total") and r0["commence"]
    assert r0["validated"] is False and res["validated"] is False and res["label"]
    pg = res["priced_games"][0]
    assert 1.5 < pg["sd_margin"] < 3.5 and 1.5 < pg["sd_total"] < 3.5 and pg["sigma_margin"] < 1.5


# --------------------------------------------------------------------------------------- None price must not abort
@pytest.mark.parametrize("extract,home,away", [(nba_run.extract_game_market_odds, "Boston Celtics", "Miami Heat"),
                                              (nhl_run.extract_game_market_odds, "Tampa Bay Lightning", "Boston Bruins")])
def test_book_with_none_prices_is_skipped_not_fatal(extract, home, away):
    ev = _event(home=home, away=away)
    ev["bookmakers"].append({"key": "badbook", "markets": [
        {"key": "h2h", "outcomes": [{"name": home, "price": None}, {"name": away, "price": 120}]},
        {"key": "spreads", "outcomes": [{"name": home, "price": None, "point": -4.5}, {"name": away, "price": -110, "point": 4.5}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": None, "point": 222.5}, {"name": "Under", "price": -110, "point": 222.5}]},
    ]})
    out = extract(ev)
    assert out["h2h"]["n_books"] == 2 and out["spreads"]["n_books"] == 2 and out["totals"]["n_books"] == 2


# ------------------------------------------------------------------------------- payload persistence: stale, not empty
def test_failed_odds_load_keeps_the_previous_good_payload_and_marks_it_stale(mem):
    good = {"date": "2026-01-10", "ts": "t0", "rows": [{"a": 1}], "status": "live", "stale": False}
    mem["EVSweep/nba_games/predictions_latest.json"] = json.dumps(good).encode()
    empty = {"date": "2026-01-10", "ts": "t1", "rows": [], "status": "awaiting_lines",
             "odds_meta": {"ok": False, "reason": "raw game_lines snapshot missing"}}
    out = odds_feed.persist_payload(empty, "EVSweep/nba_games/predictions_latest.json", "EVSweep/nba_games/archive", odds_ok=False)
    stored = json.loads(mem["EVSweep/nba_games/predictions_latest.json"])
    assert out["rows"] == [{"a": 1}] and stored["rows"] == [{"a": 1}] and stored["stale"] is True
    assert "missing" in stored["stale_reason"] and "EVSweep/nba_games/archive/2026-01-10.json" not in mem
    # a successful load with no games that day is a REAL empty state and is written normally
    odds_feed.persist_payload({**empty, "odds_meta": {"ok": True}}, "EVSweep/nba_games/predictions_latest.json",
                              "EVSweep/nba_games/archive", odds_ok=True)
    assert json.loads(mem["EVSweep/nba_games/predictions_latest.json"])["rows"] == []


def test_empty_payload_never_overwrites_a_day_archive_that_has_rows(mem):
    arch = "EVSweep/nba_games/archive/2026-01-10.json"
    mem[arch] = b'{"rows":[1]}'
    odds_feed.persist_payload({"date": "2026-01-10", "ts": "t", "rows": []}, "EVSweep/nba_games/predictions_latest.json",
                              "EVSweep/nba_games/archive", odds_ok=True)
    assert mem[arch] == b'{"rows":[1]}'


# ------------------------------------------------------------------------------------------------ Kalshi game legs
def test_nhl_game_key_canonicalises_across_nhl_api_and_kalshi_tricodes():
    from ev import kalshi_game_legs as kgl
    assert kgl.canon_game_key("nhl", "nhl:2026-01-10:LAK@TBL") == kgl.canon_game_key("nhl", "nhl:2026-01-10:LA@TB") == "nhl:2026-01-10:LA@TB"
    assert kgl.canon_game_key("nhl", "nhl:2026-01-10:NJD@SJS") == "nhl:2026-01-10:NJ@SJ"
    assert kgl.canon_game_key("nba", "nba:2026-01-10:MIA@BOS") == "nba:2026-01-10:MIA@BOS"


def test_kalshi_sd_comes_from_outcome_dispersion_not_parameter_uncertainty(monkeypatch):
    from ev import kalshi_game_legs as kgl
    from nba.game.model import NU_MARGIN, NU_TOTAL
    kgl._reset_pred_cache()
    payload_nhl = {"priced_games": [{"game_key": "nhl:2026-01-10:LAK@TBL", "sigma_margin": 0.7, "sigma_total": 0.7,
                                     "sd_margin": 2.4, "sd_total": 2.3, "markets": []},
                                    {"game_key": "nhl:2026-01-10:NJD@SJS", "sigma_margin": 0.7, "sigma_total": 0.7, "markets": []}]}
    payload_nba = {"priced_games": [{"game_key": "nba:2026-01-10:MIA@BOS", "sigma_margin": 12.0, "sigma_total": 18.0}]}
    monkeypatch.setattr(nhl_run, "read_cached_game_predictions", lambda: payload_nhl)
    monkeypatch.setattr(nba_run, "read_cached_game_predictions", lambda: payload_nba)
    assert kgl._get_nhl_game_sd("nhl:2026-01-10:LA@TB") == {"margin": 2.4, "total": 2.3}           # Kalshi-side key matches
    assert kgl._get_nhl_game_sd("nhl:2026-01-10:NJ@SJ") == kgl.GAME_SD["nhl"]                      # legacy payload: defaults, not 0.7
    nba_sd = kgl._get_nba_game_sd("nba:2026-01-10:MIA@BOS")
    assert nba_sd["margin"] == pytest.approx(12.0 * np.sqrt(NU_MARGIN / (NU_MARGIN - 2)))
    assert nba_sd["total"] == pytest.approx(18.0 * np.sqrt(NU_TOTAL / (NU_TOTAL - 2)))
    assert kgl._get_nba_game_sd("nba:2026-01-10:XXX@YYY") == kgl.GAME_SD["nba"]
    kgl._reset_pred_cache()


def test_game_model_payload_is_read_once_per_process_even_on_a_miss(monkeypatch):
    from ev import kalshi_game_legs as kgl
    kgl._reset_pred_cache()
    calls = []
    monkeypatch.setattr(nhl_run, "read_cached_game_predictions", lambda: calls.append(1))
    for _ in range(5):
        kgl._get_nhl_game_sd("nhl:2026-01-10:LA@TB")
    assert len(calls) == 1
    kgl._reset_pred_cache()


def test_away_favourite_spread_leg_gets_the_models_p_model_yes(monkeypatch):
    from ev import kalshi_game_legs as kgl
    kgl._reset_pred_cache()
    markets = [{"system": "NHL_SPREAD", "side": "away", "line": -1.5, "p_model": 0.41},
               {"system": "NHL_SPREAD", "side": "home", "line": 1.5, "p_model": 0.59},
               {"system": "NHL_ML", "side": "home", "line": None, "p_model": 0.38},
               {"system": "NHL_ML", "side": "away", "line": None, "p_model": 0.62}]
    monkeypatch.setattr(nhl_run, "read_cached_game_predictions", lambda: {"priced_games": [
        {"game_key": "nhl:2026-01-10:TBL@LAK", "sd_margin": 2.3, "sd_total": 2.4, "markets": markets}]})
    home, away = "Los Angeles Kings", "Tampa Bay Lightning"

    def book(key):
        return {"key": key, "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": 125, "point": None}, {"name": away, "price": -145}]},
            {"key": "spreads", "outcomes": [{"name": away, "price": 160, "point": -1.5}, {"name": home, "price": -190, "point": 1.5}]}]}
    ev = {"id": "x", "commence_time": "2026-01-11T00:00:00Z", "home_team": home, "away_team": away,
          "bookmakers": [book("draftkings"), book("fanduel"), book("betmgm")]}
    rows, stats = kgl.slate_rows([ev], "nhl", now=0.0)
    spread = [r for r in rows if r["market_class"] == "game_spread" and not r.get("pooled")]
    assert len(spread) == 1 and spread[0]["ticker"].endswith("-TB2")                  # away (TB) favourite -1.5
    assert spread[0]["p_model_yes"] == pytest.approx(0.41)
    ml = {r["ticker"].rsplit("-", 1)[1]: r for r in rows if r["market_class"] == "game_ml"}
    assert ml["TB"]["p_model_yes"] == pytest.approx(0.62) and ml["LA"]["p_model_yes"] == pytest.approx(0.38)  # LAK/TBL keys matched
    kgl._reset_pred_cache()


# ------------------------------------------------------------------------------------------ daily card + wiring
def test_nhl_rows_are_card_dedupable_per_system_now_that_they_carry_a_player(mem, monkeypatch):
    from ev import daily_card as dc
    _bank("icehockey_nhl", [_event("2026-01-10T00:30:00Z", home="Tampa Bay Lightning", away="Los Angeles Kings")], mem)
    monkeypatch.setattr(nhl_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nhl_run, "load_historical_goalies", lambda: None)
    rows = nhl_run.run_nhl_game_predictions(target_date="2026-01-09", now=datetime.now(timezone.utc))["rows"]
    picks = []
    for sys in ("NHL_ML", "NHL_SPREAD"):
        r = dict(next(x for x in rows if x["system"] == sys))
        r.update(confluence=True, n_books=5, market_ev=0.05, model_edge=0.03, beezy_score=75)
        picks.append(r)
    card = dc.build_daily_card(picks)
    assert {r["system"] for r in card} == {"NHL_ML", "NHL_SPREAD"}                   # one play per (game, system), not per game


def test_daily_projections_hands_its_in_memory_events_to_the_game_runners_and_logs_tracebacks(monkeypatch, caplog):
    from ev import daily_projections as dp
    run_models = inspect.unwrap(dp._run_game_models)                                 # conftest no-ops the hook for other tests
    seen = {}
    monkeypatch.setattr(nba_run, "run_nba_game_predictions", lambda **kw: seen.update(kw) or {})
    ev = [{"id": "p1"}]
    assert run_models("nba", ev, datetime(2026, 1, 10, tzinfo=timezone.utc)) is True
    assert seen["events"] is ev and seen["write_gcs"] is True

    def boom(**kw):
        raise RuntimeError("replay failed")
    monkeypatch.setattr(nhl_run, "run_nhl_game_predictions", boom)
    with caplog.at_level(logging.ERROR, logger="ev.daily_projections"):
        assert run_models("nhl", ev, datetime(2026, 1, 10, tzinfo=timezone.utc)) is False
    assert any(r.exc_info for r in caplog.records)                                    # traceback preserved
    assert run_models("nfl", ev, datetime(2026, 1, 10, tzinfo=timezone.utc)) is False


def test_prop_events_are_not_mistaken_for_game_odds(mem, monkeypatch):
    prop_ev = {"id": "p1", "commence_time": "2026-01-10T23:30:00Z", "home_team": "Boston Celtics", "away_team": "Miami Heat",
               "bookmakers": [{"key": "draftkings", "markets": [{"key": "player_points", "outcomes": []}]}]}
    assert odds_feed.has_game_markets([prop_ev]) is False and odds_feed.has_game_markets([_event()]) is True
    monkeypatch.setattr(nba_run, "load_historical_boxscores", lambda: None)
    monkeypatch.setattr(nba_run, "load_live_injuries", lambda: {})
    res = nba_run.run_nba_game_predictions(target_date="2026-01-10", now=datetime.now(timezone.utc), events=[prop_ev])
    assert res["status"] == "awaiting_lines" and res["odds_meta"]["ok"] is False        # falls back to the (empty) banked pointer


# ----------------------------------------------------------------------------------------------- NHL evaluator
def test_nhl_evaluator_feeds_both_teams_and_says_its_lines_are_not_market():
    from nhl.game.evaluate import WalkForwardNHLEvaluator
    ev = WalkForwardNHLEvaluator()
    for i in range(6):
        ev.evaluate_game(home="BOS", away="MTL", game_date=f"2026-01-{10 + i:02d}", game_id=f"g{i}",
                         actual_home_goals=3.0, actual_away_goals=2.0, actual_home_sog=33.0, actual_away_sog=27.0)
    assert len(ev.features.team_games["BOS"]) == 6 and len(ev.features.team_games["MTL"]) == 6
    assert ev.features.team_games["MTL"][0].is_home is False and ev.features.team_games["MTL"][0].season_year == 2025
    s = ev.summarize().summary()
    assert "NOT closing prices" in s and "calibration" in s
    ev2 = WalkForwardNHLEvaluator()
    ev2.evaluate_game("BOS", "MTL", "2026-01-10", "g", 3.0, 2.0, closing_puck_line=-1.5, closing_total=6.0, lines_are_market=True)
    assert "NOT closing prices" not in ev2.summarize().summary()


def test_nhl_profile_shrinks_on_games_this_season_not_career_games():
    from nhl.game.features import LEAGUE_BASELINES as NHL_BASE
    from nhl.game.features import FeatureStore as NhlStore
    from nhl.game.features import build_team_game_stats

    def stat(i, date):
        return build_team_game_stats(f"g{i}", date, 2025, "BOS", "MTL", True, goals=5.0, goals_against=1.0, sog=40.0,
                                     shots_against=20.0)
    store = NhlStore()
    for i in range(40):                                   # 40 dominant games last season
        store.add_game_stat(stat(i, f"2025-{1 + i // 20:02d}-{1 + i % 20:02d}"))
    base = NHL_BASE["gf_60"]
    opener = store.compute_team_profile("BOS", "2025-10-25")["gf_60"]    # first game of the next season
    late = store.compute_team_profile("BOS", "2025-07-01")["gf_60"]      # same data, still last season
    assert base < opener < late                                           # old code: career n=40 -> no shrink at all
    for i in range(40, 52):                                               # 12 games into the new season: shrink is gone
        store.add_game_stat(stat(i, f"2025-11-{i - 39:02d}"))
    assert store.compute_team_profile("BOS", "2025-12-01")["gf_60"] == pytest.approx(late, rel=0.02)
