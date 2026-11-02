"""NHL hierarchical prop engine (nhl.props.hierarchical + ev.daily_projections NHL path) and the pp_points data path.

Everything is offline: synthetic game logs, the trimmed real 2026-10-13 NJD@DET fixtures, and stubbed HTTP.
"""
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ev import daily_projections as dp
from ev import grade_props as gp
from mlb_core.data import cockpit_form as cf
from nhl.data import powerplay as pp
from nhl.data.flatten import flatten_game
from nhl.props import hierarchical as H

FX = Path(__file__).parent / "fixtures"
START = datetime(2026, 2, 5, 1, 0, tzinfo=timezone.utc)          # 20:00 ET on 2026-02-04
AS_OF = "2026-02-04"
NOW = datetime(2026, 2, 4, 16, 0, tzinfo=timezone.utc)
STATS = tuple(dp.PROJECT_STATS["nhl"])


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(dp._rosters, "load", lambda sport, now=None, **k: None)
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])


# -------------------------------------------------------------------------------------------------- synthetic logs
FILLER = ("NYR", "NJD", "PHI", "PIT")
SOG_FOR = {"TOR": 29.5, "BOS": 29.5, "MTL": 34.5, "OTT": 25.0, **{t: 29.5 for t in FILLER}}


def _dates(n=30, start="2026-01-01"):
    d0 = datetime.fromisoformat(start)
    return [(d0 + pd.Timedelta(days=i)).date().isoformat() for i in range(n)]


def skater_rows(name, pos, toi, rng, dates, team="TOR", game_ids=None, **over):
    rows = []
    for i, d in enumerate(dates):
        sog = float(rng.poisson(2.8 if pos != "D" else 1.6))
        goals = float(rng.binomial(int(sog), 0.11 if pos != "D" else 0.05))
        ast = float(rng.poisson(0.40 if pos != "D" else 0.28))
        r = {"name": name, "position": pos, "team_abbr": team, "_date": d, "_sort": d, "_opp": "BOS",
             "game_id": (game_ids[i] if game_ids else 5000 + i), "season_year": 2025, "toi_min": toi + float(rng.normal(0, .5)),
             "goals": goals, "assists": ast, "points": goals + ast, "sog": sog,
             "blocked_shots": float(rng.poisson(1.0)), "power_play_points": float(rng.poisson(0.18))}
        r.update({k: (v[i] if isinstance(v, (list, tuple)) else v) for k, v in over.items()})
        rows.append(r)
    return rows


def goalie_rows(name, rng, dates, team="TOR"):
    rows = []
    for i, d in enumerate(dates):
        sa = float(rng.poisson(29))
        rows.append({"name": name, "team_abbr": team, "_date": d, "_sort": d, "_opp": "BOS", "game_id": 5000 + i,
                     "season_year": 2025, "starter": True, "toi_sec": 3600, "shots_against": sa,
                     "saves": float(rng.binomial(int(sa), 0.905))})
    return rows


def team_rows(rng, dates, sog_for=SOG_FOR):
    out = {}
    for ab, sf in sog_for.items():
        rows = []
        for i, d in enumerate(dates):
            rows.append({"team_abbr": ab, "_date": d, "_sort": d, "game_id": 5000 + i, "season_year": 2025,
                         "sog": float(rng.poisson(sf)), "shots_against": float(rng.poisson(29.5)),
                         "goals": 3.0, "goals_against": 3.0, "pim": float(rng.poisson(7))})
        out[ab] = rows
    return out


def make_logs(seed=11, n_days=30, extra_future=0, wild=False):
    """Skaters + a goalie on TOR (plus filler team rows). `extra_future` appends that many rows dated on / after AS_OF with
    wild values to every table: the leakage tests project with and without them."""
    rng = np.random.default_rng(seed)
    dates = _dates(n_days)
    sk = {}
    for nm, pos, toi in (("Ann Forward", "C", 19.0), ("Bob Winger", "R", 17.5), ("Cal Center", "C", 15.0),
                         ("Dan Defense", "D", 23.0), ("Eli Blue", "D", 20.0), ("Fay Third", "L", 13.0)):
        sk[cf.norm_name(nm)] = skater_rows(nm, pos, toi, rng, dates)
    gl = {cf.norm_name("Gus Goalie"): goalie_rows("Gus Goalie", rng, dates)}
    tm = team_rows(rng, dates)
    if extra_future:
        fut = _dates(extra_future, AS_OF)
        mul = 7.0 if wild else 1.0
        for rows in sk.values():
            for i, d in enumerate(fut):
                r = dict(rows[-1])
                r.update(_date=d, _sort=d, game_id=9000 + i, goals=4.0 * mul, assists=3.0 * mul, points=7.0 * mul,
                         sog=12.0 * mul, blocked_shots=9.0 * mul, power_play_points=3.0 * mul, toi_min=30.0)
                rows.append(r)
        for rows in gl.values():
            for i, d in enumerate(fut):
                r = dict(rows[-1])
                r.update(_date=d, _sort=d, game_id=9000 + i, shots_against=60.0 * mul, saves=3.0)
                rows.append(r)
        for rows in tm.values():
            for i, d in enumerate(fut):
                r = dict(rows[-1])
                r.update(_date=d, _sort=d, game_id=9000 + i, sog=70.0 * mul, shots_against=70.0 * mul, pim=40.0)
                rows.append(r)
    return dp.GameLogs("nhl", {"skater": sk, "goalie": gl, "team": tm})


def row_for(logs, stat, pn, opp="BOS", roster=None, line=0.5):
    table, sdef = logs.table_and_def(stat)
    return dp.make_row("nhl", stat, pn.title(), cf.norm_name(pn), table[cf.norm_name(pn)], sdef, logs, line, None, [],
                       "TOR", opp, f"nhl:{AS_OF}:TOR@{opp}", START, 60.0, roster=roster)


# -------------------------------------------------------------------------------------- pp_points: data derivation
@pytest.mark.parametrize("code,is_home,period,expected", [
    (1551, True, "REG", False),        # 5v5
    (1541, False, "REG", True),        # away 5 v home 4: the away side is on the power play
    (1541, True, "REG", False),        # ... and the home side is short-handed
    (1451, True, "REG", True),
    (1560, True, "REG", False),        # home has pulled its goalie, 6v5 extra attacker, no penalty
    (1460, True, "REG", True),         # 6v4 with the goalie pulled during a delayed penalty IS a power-play goal
    (651, False, "REG", False),        # away pulled (6v5): empty-net situation, not a power play
    (641, False, "REG", True),
    (1541, False, "SO", False),        # shootout goals never count
    (1331, True, "OT", False),         # 3v3 overtime: equal strength
    (1351, True, "OT", True),          # home 5 skaters v away 3
    (None, True, "REG", False),
    (float("nan"), True, "REG", False),
])
def test_goal_is_power_play(code, is_home, period, expected):
    assert pp.goal_is_power_play(code, is_home, period) is expected


def test_fixture_pbp_power_play_matches_the_boxscores_own_power_play_goals():
    box = json.loads((FX / "nhl_boxscore_sample.json").read_text())
    pbp = json.loads((FX / "nhl_pbp_sample.json").read_text())
    derived = pp.pp_points_from_pbp(pbp)
    box_ppg = {p["playerId"]: p["powerPlayGoals"] for side in ("awayTeam", "homeTeam")
               for grp in ("forwards", "defense") for p in box["playerByGameStats"][side][grp] if p.get("powerPlayGoals")}
    assert {pid: r["pp_goals"] for pid, r in derived.items() if r["pp_goals"]} == box_ppg
    assert sum(r["pp_assists"] for r in derived.values()) == 2          # the one PP goal carries two assists
    assert sum(r["pp_points"] for r in derived.values()) == 3


def test_boxscore_has_no_power_play_points_field():
    """The premise that fixed the design: the official skater row carries powerPlayGoals only."""
    box = json.loads((FX / "nhl_boxscore_sample.json").read_text())
    for side in ("awayTeam", "homeTeam"):
        for grp in ("forwards", "defense"):
            for p in box["playerByGameStats"][side][grp]:
                assert "powerPlayPoints" not in p and "powerPlayAssists" not in p


def test_flatten_writes_pp_columns_and_leaves_them_null_without_pbp():
    box = json.loads((FX / "nhl_boxscore_sample.json").read_text())
    pbp = json.loads((FX / "nhl_pbp_sample.json").read_text())
    sk = {s["player_id"]: s for s in flatten_game(box, pbp)["skaters"]}
    assert sk[8480002]["power_play_points"] == 1 and sk[8480002]["power_play_assists"] == 0
    assert sk[8479407]["power_play_assists"] == 1 and sk[8479407]["power_play_points"] == 1
    assert sum(s["power_play_points"] for s in sk.values()) == 3
    for s in flatten_game(box, None)["skaters"] + flatten_game(box, {"plays": [], "rosterSpots": []})["skaters"]:
        assert s["power_play_points"] is None and s["power_play_assists"] is None     # unknown, not zero


def test_pp_points_from_pbp_is_none_without_plays():
    assert pp.pp_points_from_pbp({}) is None and pp.pp_points_from_pbp({"plays": []}) is None


def test_fill_skater_frame_fills_only_games_with_play_by_play():
    shots = pd.DataFrame([
        {"game_id": 1, "event": "shot-on-goal", "is_home": True, "period_type": "REG", "situation_code": 1551,
         "shooter_id": 11, "assist1_id": None, "assist2_id": None},
        {"game_id": 1, "event": "goal", "is_home": False, "period_type": "REG", "situation_code": 1541,
         "shooter_id": 10, "assist1_id": 20, "assist2_id": float("nan")},
    ])
    sk = pd.DataFrame([{"game_id": 1, "player_id": 10, "power_play_points": float("nan")},
                       {"game_id": 1, "player_id": 20, "power_play_points": float("nan")},
                       {"game_id": 1, "player_id": 30, "power_play_points": float("nan")},
                       {"game_id": 2, "player_id": 10, "power_play_points": float("nan")},     # no shots data for game 2
                       {"game_id": 1, "player_id": 40, "power_play_points": 5.0}])             # already filled: untouched
    out = pp.fill_skater_frame(sk, shots).set_index(["game_id", "player_id"])
    assert out.loc[(1, 10), "power_play_points"] == 1 and out.loc[(1, 10), "power_play_assists"] == 0
    assert out.loc[(1, 20), "power_play_points"] == 1 and out.loc[(1, 20), "power_play_assists"] == 1
    assert out.loc[(1, 30), "power_play_points"] == 0
    assert math.isnan(out.loc[(2, 10), "power_play_points"])
    assert out.loc[(1, 40), "power_play_points"] == 5.0


def test_grade_pp_points_from_play_by_play_not_a_boxscore_field():
    box = json.load(open(FX / "nhl_boxscore_2026020087.json"))
    pbp = json.loads((FX / "nhl_pbp_sample.json").read_text())

    def provider(with_pbp):
        def get(url):
            if "schedule" in url:
                return {"gameWeek": [{"date": "2026-10-12", "games": [
                    {"id": 2026020087, "gameState": "OFF", "awayTeam": {"abbrev": "FLA"}, "homeTeam": {"abbrev": "BUF"}}]}]}
            if "play-by-play" in url:
                if not with_pbp:
                    raise RuntimeError("503")
                return pbp
            return box
        return gp.nhl_actuals("2026-10-12", "FLA", "BUF", get=get)

    ok = provider(True)
    some = next(iter(ok["players"].values()))
    assert "pp_points" in some and some["pp_points"] == 0           # a dressed skater with no PP point, graded as 0
    row = {"player_id": next(iter(ok["players"])), "stat": "pp_points", "point": 0.5, "side": "OVER", "american": 150}
    assert gp.grade_row(row, ok) == ("loss", -100.0)
    down = provider(False)                                           # no play-by-play: ungradable, never a fake 0
    assert gp.grade_row(row, down) == ("ungradable", 0.0)


def test_cockpit_form_pp_points_stat_and_loader_fill(monkeypatch):
    assert "pp_points" in cf.NHL_SKATER_STATS and "pp_points" in cf.supported_stats("nhl")
    sk = pd.DataFrame([{"game_id": 1, "player_id": 10, "power_play_points": float("nan"), "season_year": 2025}])
    shots = pd.DataFrame([{"game_id": 1, "event": "goal", "is_home": True, "period_type": "REG", "situation_code": 1451,
                           "shooter_id": 10, "assist1_id": None, "assist2_id": None}])
    monkeypatch.setattr(cf, "_read", lambda keys, kind, usecols=None: shots)
    out = cf._fill_nhl_power_play(sk)
    assert out.loc[0, "power_play_points"] == 1
    monkeypatch.setattr(cf, "_read", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("none")))
    assert math.isnan(cf._fill_nhl_power_play(sk).loc[0, "power_play_points"])      # unreadable shots: left unknown


# ------------------------------------------------------------------------------- the seven markets are live
def test_pp_points_is_a_live_market_with_labels_and_blend_weights():
    assert dp.PROJECT_STATS["nhl"]["pp_points"] == "negbin"
    assert dp.STAT_LABEL["pp_points"] == "PP Points" and "pp_points" in dp.ROTATION_MIN_USAGE["nhl"]
    assert dp.BLEND_C_BY_SPORT_STAT["nhl"] == {"sog": 0.30, "saves": 0.15, "blocked_shots": 0.15, "points": 0.12,
                                               "goals": 0.10, "assists": 0.12, "pp_points": 0.10}
    assert dp.blend_c("nhl", "sog") == 0.30 and dp.blend_c("nfl", "rec_yds") == dp.BLEND_C


def test_every_nhl_market_produces_a_valid_row_and_uses_its_blend_weight():
    logs = make_logs()
    names = {"sog": "Ann Forward", "goals": "Ann Forward", "assists": "Ann Forward", "points": "Ann Forward",
             "pp_points": "Ann Forward", "blocked_shots": "Dan Defense", "saves": "Gus Goalie"}
    for stat in STATS:
        r = row_for(logs, stat, names[stat], line=24.5 if stat == "saves" else 0.5)
        assert r is not None and r["proj_mean"] > 0 and r["nhl_model"] == "hierarchical_v1", stat
        assert 0.0 < r["p_over_model"] < 1.0 and r["proj_p10"] <= r["proj_p90"]
        assert r["validated"] is False


def test_run_sport_emits_pp_points_rows():
    logs = make_logs()
    ev = {"id": "e1", "commence_time": START.isoformat().replace("+00:00", "Z"), "away_team": "Boston Bruins",
          "home_team": "Toronto Maple Leafs", "bookmakers": [
              {"key": b, "markets": [{"key": "player_power_play_points", "outcomes": [
                  {"name": "Over", "description": "Ann Forward", "price": 220, "point": 0.5},
                  {"name": "Under", "description": "Ann Forward", "price": -290, "point": 0.5}]}]}
              for b in ("draftkings", "fanduel", "betmgm", "caesars")]}
    out = dp.run_sport("nhl", NOW, logs=logs, events=[ev], meta={}, games=[], write=False, roster=None)
    rows = [r for r in out["rows"] if r["stat"] == "pp_points"]
    assert len(rows) == 1 and rows[0]["has_line"] and rows[0]["stat_label"] == "PP Points"
    assert rows[0]["proj_mean"] > 0 and 0.0 < rows[0]["p_over_model"] < 1.0
    assert "pp_points" in out["stats"]


def test_pp_points_projection_is_shrunk_toward_the_position_prior():
    rng = np.random.default_rng(3)
    dates = _dates(30)
    zero = skater_rows("Zed Zero", "C", 18.0, rng, dates, power_play_points=0.0)
    hot = skater_rows("Hal Hot", "C", 18.0, rng, dates, power_play_points=1.0)
    logs = make_logs()
    logs.idx["skater"][cf.norm_name("Zed Zero")] = zero
    logs.idx["skater"][cf.norm_name("Hal Hot")] = hot
    mz = row_for(logs, "pp_points", "Zed Zero")["proj_mean"]
    mh = row_for(logs, "pp_points", "Hal Hot")["proj_mean"]
    assert 0.0 < mz < H.PP_PRIOR["F"]                   # never a flat zero for a player with 30 empty games
    assert H.PP_PRIOR["F"] < mh < 1.0                   # and a 1.0/game heater is pulled down toward the prior


# ------------------------------------------------------------------- conservation: points = goals + assists
def test_points_projection_is_exactly_goals_plus_assists_for_every_skater():
    logs = make_logs()
    for nm in ("Ann Forward", "Bob Winger", "Cal Center", "Dan Defense", "Eli Blue", "Fay Third"):
        g = row_for(logs, "goals", nm)["proj_mean"]
        a = row_for(logs, "assists", nm)["proj_mean"]
        p = row_for(logs, "points", nm)["proj_mean"]
        assert round(p, 3) == pytest.approx(round(g + a, 3), abs=1.1e-3), nm       # _f() rounds each to 3 dp
        r = row_for(logs, "points", nm)
        assert r["mu_goals"] + r["mu_assists"] == pytest.approx(p, abs=2e-3)
    for opp in ("MTL", "OTT"):                                                       # holds against any opponent
        g, a = (row_for(logs, s, "Ann Forward", opp)["proj_mean"] for s in ("goals", "assists"))
        assert row_for(logs, "points", "Ann Forward", opp)["proj_mean"] == pytest.approx(g + a, abs=1.1e-3)


def test_points_mu_is_additive_by_construction():
    assert H.points_mu(0.31, 0.47) == pytest.approx(0.78)


def test_points_alone_falls_back_when_goals_or_assists_history_is_missing():
    logs = make_logs()
    rows = [{k: v for k, v in r.items() if k not in ("goals", "assists")} for r in logs.idx["skater"][cf.norm_name("Ann Forward")]]
    sdef = cf.NHL_SKATER_STATS["points"]
    r = dp.make_row("nhl", "points", "Ann Forward", "ann forward", rows, sdef, logs, 0.5, None, [], "TOR", "BOS", "g",
                    START, 60.0)
    assert r is not None and r["proj_mean"] > 0 and "mu_goals" not in r


# ------------------------------------------------------------------ goals: empirical-Bayes shooting regression
def test_eb_shooting_pct_regresses_a_heater_to_the_position_prior():
    heater = H.eb_shooting_pct(5, 10, "C")
    assert heater == pytest.approx((5 + 85 * 0.112) / 95)
    assert 0.10 < heater < 0.20 and heater < 0.5 / 2          # nowhere near the raw 50%
    assert H.eb_shooting_pct(0, 0, "D") == pytest.approx(0.055) and H.eb_shooting_pct(0, 0, "L") == pytest.approx(0.112)
    assert H.pos_group("D") == "D" and H.pos_group("RW") == "F" and H.pos_group(None) == "F"


def test_goals_projection_regresses_a_shooting_heater_toward_shots_times_shrunk_pct():
    rng = np.random.default_rng(5)
    dates = _dates(30)
    sog = [3.0] * 26 + [3.0, 3.0, 2.0, 2.0]
    goals = ([1.0, 0.0, 0.0] * 9)[:26] + [2.0, 1.0, 1.0, 1.0]            # ~10% for 26 games, then 5 goals on 10 shots
    rows = skater_rows("Hot Hand", "C", 18.0, rng, dates, sog=sog, goals=goals)
    logs = make_logs()
    logs.idx["skater"][cf.norm_name("Hot Hand")] = rows
    r = row_for(logs, "goals", "Hot Hand")
    naive = dp.project_mu(dp._hist(rows, cf.NHL_SKATER_STATS["goals"]), 8.0, 1.0, dp.EWM_WEIGHT)["mu"]
    sh = H.shooting_pct_from_rows(rows, "C")
    mu_sog = dp.project_mu(dp._hist(rows, cf.NHL_SKATER_STATS["sog"]), 8.0, 1.0, dp.EWM_WEIGHT)["mu"]
    assert r["proj_raw"] < naive                                          # regressed below the raw trailing estimate
    assert r["proj_raw"] > mu_sog * 0.08                                  # but a real scorer, not zeroed
    g30, s30 = sum(x["goals"] for x in rows[-30:]), sum(x["sog"] for x in rows[-30:])
    assert r["sh_pct_eb"] == pytest.approx((g30 + 85 * 0.112) / (s30 + 85), abs=1e-4)      # K = 85 shots of prior
    assert r["sh_pct_eb"] < g30 / s30 - 0.01                                                # strictly below the raw rate
    assert r["proj_raw"] == pytest.approx(H.goals_mu(naive, mu_sog, sh) * (r["opp_factor"] or 1.0), rel=1e-2)


def test_goals_mu_blend_weights():
    assert H.goals_mu(0.40, 3.0, 0.11) == pytest.approx(0.55 * 0.40 + 0.45 * 3.0 * 0.11)


# ------------------------------------------------------- opponent shot volume: saves and blocked shots
def test_goalie_saves_rise_with_opponent_shot_volume():
    logs = make_logs()
    hi = row_for(logs, "saves", "Gus Goalie", opp="MTL", line=25.5)          # 34.5 SOG/g
    lo = row_for(logs, "saves", "Gus Goalie", opp="OTT", line=25.5)          # 25.0 SOG/g
    assert hi["proj_mean"] > lo["proj_mean"] + 1.0
    assert hi["exp_shots_against"] > lo["exp_shots_against"]
    assert hi["p_over_model"] > lo["p_over_model"]


def test_defenseman_blocked_shots_rise_with_opponent_shot_volume():
    logs = make_logs()
    hi = row_for(logs, "blocked_shots", "Dan Defense", opp="MTL")
    lo = row_for(logs, "blocked_shots", "Dan Defense", opp="OTT")
    assert hi["proj_mean"] > lo["proj_mean"]
    assert dp.OPP_CLIP[0] <= H.pace_factor(34.5, 29.5) <= dp.OPP_CLIP[1] and H.PACE_CLIP == (0.85, 1.15)
    assert H.pace_factor(100.0, 29.5) == 1.15 and H.pace_factor(5.0, 29.5) == 0.85 and H.pace_factor(None, 29.5) == 1.0


def test_team_pace_uses_only_games_before_as_of_and_has_league_means():
    logs = make_logs()
    p = logs.team_pace(AS_OF)
    assert p["MTL"]["sog_for"] > p["OTT"]["sog_for"] and p["MTL"]["n"] == H.PACE_WINDOW
    assert 25.0 < p["_league"]["sog_for"] < 34.5


# ----------------------------------------------------------------------------------------------- saves mixture
def test_saves_mixture_keeps_the_mean_and_total_spread_and_fattens_the_left_tail():
    mu, cv = 26.0, 0.29
    d = H.SavesMixture.fit(mu, cv)
    assert d.mean == pytest.approx(mu, abs=1e-9)
    xs = np.linspace(-5, 70, 4001)
    pdf = np.gradient([d.cdf(x) for x in xs], xs)
    var = float(np.trapezoid(pdf * (xs - mu) ** 2, xs))
    assert math.sqrt(var) == pytest.approx(cv * mu, rel=0.02)
    from scipy.stats import norm
    sd = cv * mu
    assert d.cdf(14.5) > norm.cdf(14.5, mu, sd)                          # more mass in the early-hook region
    assert d.ppf(0.1) < norm.ppf(0.1, mu, sd) + 1.0
    qs = [d.ppf(q) for q in (0.1, 0.5, 0.9)]
    assert qs == sorted(qs) and d.p_over(24.5) > d.p_over(28.5)
    assert H.SavesMixture.fit(mu, 0.05).cdf(mu) > 0                      # the cv floor keeps a degenerate input sane


def test_goalie_saves_row_carries_the_eb_save_pct_and_a_mixture_range():
    r = row_for(make_logs(), "saves", "Gus Goalie", opp="BOS", line=25.5)
    assert 0.88 < r["sv_pct_eb"] < 0.93 and r["usage_label"] == "Shots faced"
    assert r["proj_p10"] < r["proj_mean"] < r["proj_p90"]
    assert H.eb_save_pct([{"saves": 90, "shots_against": 100}], k=400, prior=0.9) == pytest.approx((90 + 360) / 500)


# ------------------------------------------------------------------------------------------ vacated opportunity
class Roster:
    """Stand-in for ev.rosters.Roster (a COMPLETE pull): `on` = names the roster carries for TOR."""
    def __init__(self, on):
        self.on = set(on)
        self.players = {n: "TOR" for n in on}

    def lookup(self, pn, log_team=None):
        return ("on", "TOR") if pn in self.on else ("off", None)


def _vacated_logs():
    rng = np.random.default_rng(21)
    dates = _dates(30)
    logs = make_logs()
    sk = logs.idx["skater"]
    gone = skater_rows("Star Gone", "C", 20.5, rng, dates[:-3], sog=4.0)           # last played 3 games ago, top line
    sk[cf.norm_name("Star Gone")] = gone
    return logs


def test_absent_top_line_forward_boosts_active_forwards_but_not_defensemen():
    logs = _vacated_logs()
    on = [n for n in logs.idx["skater"] if n != cf.norm_name("Star Gone")]
    roster = Roster(on)
    base = row_for(logs, "sog", "Ann Forward", roster=None)
    boosted = row_for(logs, "sog", "Ann Forward", roster=roster)
    assert boosted["proj_mean"] > base["proj_mean"] and "vacated" in boosted
    assert boosted["vacated"]["sog"] > 0
    d_base, d_boost = row_for(logs, "sog", "Dan Defense", roster=None), row_for(logs, "sog", "Dan Defense", roster=roster)
    assert d_boost["proj_mean"] == d_base["proj_mean"] and "vacated" not in d_boost
    a_base, a_boost = row_for(logs, "assists", "Ann Forward", roster=None), row_for(logs, "assists", "Ann Forward", roster=roster)
    assert a_boost["proj_mean"] > a_base["proj_mean"]
    p_boost = row_for(logs, "points", "Ann Forward", roster=roster)["proj_mean"]
    g_boost = row_for(logs, "goals", "Ann Forward", roster=roster)["proj_mean"]
    assert p_boost == pytest.approx(g_boost + a_boost["proj_mean"], abs=1.1e-3)       # conservation survives the boost


def test_vacated_share_is_damped_and_split_by_toi():
    logs = _vacated_logs()
    on = [n for n in logs.idx["skater"] if n != cf.norm_name("Star Gone")]
    v = logs.nhl_vacated("TOR", AS_OF, Roster(on))
    assert set(v) <= set(on) and v
    total = sum(x["sog"] for x in v.values())
    assert 0 < total <= 0.5 * 4.0 + 1e-9                                            # at most half of his ~4 shots a game
    ann, fay = cf.norm_name("Ann Forward"), cf.norm_name("Fay Third")
    assert fay not in v or v[ann]["sog"] > v[fay]["sog"]                            # split by TOI: the 19-minute forward gets more


def test_no_roster_or_partial_roster_means_nobody_is_absent():
    logs = _vacated_logs()
    assert logs.nhl_vacated("TOR", AS_OF, None) == {}

    class Partial(Roster):
        def lookup(self, pn, log_team=None):
            return ("on", "TOR") if pn in self.on else ("unknown", None)
    on = [n for n in logs.idx["skater"] if n != cf.norm_name("Star Gone")]
    assert logs.nhl_vacated("TOR", AS_OF, Partial(on)) == {}


def test_a_player_who_dressed_in_the_latest_game_is_never_absent_whatever_the_roster_says():
    logs = make_logs()
    roster = Roster([n for n in logs.idx["skater"] if n != cf.norm_name("Ann Forward")])      # a name-mismatch shape
    assert logs.nhl_vacated("TOR", AS_OF, roster) == {}


def test_extra_absent_hook_works_without_a_roster():
    logs = make_logs()
    v = logs.nhl_vacated("TOR", AS_OF, None, extra_absent=(cf.norm_name("Ann Forward"),))
    assert v and cf.norm_name("Ann Forward") not in v


# --------------------------------------------------------------------------------------------- no time travel
def _fingerprint(logs):
    out = {}
    names = {"sog": "Ann Forward", "goals": "Ann Forward", "assists": "Ann Forward", "points": "Ann Forward",
             "pp_points": "Ann Forward", "blocked_shots": "Dan Defense", "saves": "Gus Goalie"}
    for stat in STATS:
        r = row_for(logs, stat, names[stat], opp="MTL", line=25.5 if stat == "saves" else 0.5)
        out[stat] = {k: r[k] for k in ("proj_mean", "proj_raw", "proj_p10", "proj_p90", "p_over_model", "p_over_blend",
                                       "opp_factor", "usage_proj", "n_games")}
    return out


@pytest.mark.parametrize("wild", [False, True])
def test_projections_ignore_every_game_on_or_after_the_game_date(wild):
    clean = make_logs()                                        # nothing dated on / after AS_OF
    dirty = make_logs(extra_future=6, wild=wild)               # same history + 6 future days of absurd values in EVERY table
    assert _fingerprint(dirty) == _fingerprint(clean)          # bit for bit: rows, contexts, pace, league save %


def test_league_contexts_and_pace_are_as_of_safe():
    clean, dirty = make_logs(), make_logs(extra_future=6, wild=True)
    for stat in ("sog", "goals", "assists", "points", "pp_points", "blocked_shots", "saves"):
        a, b = clean.context(stat, AS_OF), dirty.context(stat, AS_OF)
        assert (a.alpha, a.cv, a.opp_factor, a.league_mean) == (b.alpha, b.cv, b.opp_factor, b.league_mean), stat
    assert clean.team_pace(AS_OF) == dirty.team_pace(AS_OF)
    assert clean.league_sv(AS_OF) == dirty.league_sv(AS_OF)
    # and the control: WITHOUT the as-of cut the dirty master does move the contexts (the test has teeth)
    assert dirty.context("sog", None).league_mean != clean.context("sog", None).league_mean


def test_before_keeps_undated_rows_and_cuts_dated_ones():
    rows = [{"_date": "2026-01-01"}, {"_date": "2026-02-04"}, {"_date": "2026-02-09"}, {}]
    assert H.before(rows, "2026-02-04") == [rows[0], rows[3]]
    assert H.before(rows, None) is rows


def test_live_board_path_leaves_whole_master_contexts_alone():
    """With every game before the as-of date nothing is cut, so the cached whole-master contexts are used as before."""
    logs = make_logs()
    assert logs._eff_as_of(AS_OF) is None and logs._eff_as_of(None) is None
    assert logs._eff_as_of("2026-01-10") == "2026-01-10"
    assert logs.context("sog", AS_OF) is logs.context("sog")


def test_nfl_and_nba_paths_are_untouched():
    nba = dp.GameLogs("nba", {"all": {}})
    assert nba._eff_as_of("2026-02-04") is None and nba.team_pace(AS_OF) == {} and nba.nhl_vacated("LAL", AS_OF, None) == {}
