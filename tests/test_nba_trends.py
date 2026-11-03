"""nba.trends: opponent / rest / split / histogram / teammate-out / driver arithmetic on synthetic box-score frames, the fail-soft
statuses and the route. The loader (`_read_sources`) is monkeypatched with canonical frames; nothing touches GCS."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from nba import trends as T

# three teams so every game has a distinct opponent mix; ISO dates 2 days apart except one back-to-back
OPPS = ["BOS", "MIA", "DAL"]


def _row(gid, season, date, team, name, minutes, pts, fga, home, starter=True, fg3a=4, fg3m=1, reb=4, ast=3, tov=2, fta=3, stype="RS"):
    return {"game_id": gid, "season": season, "stype": stype, "date": date, "team": team, "name": name, "pid": f"p-{name}", "min": minutes, "pts": pts,
            "reb": reb, "ast": ast, "stl": 1, "blk": 0, "tov": tov, "pf": 2, "fgm": fga // 2, "fga": fga, "fg3m": fg3m, "fg3a": fg3a,
            "fta": fta, "starter": starter, "home": home}


def _frame(n=40, season=2025, with_prev=False):
    """NYK's 'Star One' + 'Mate Two' (misses every 4th game, and has a 0-minute row in game 5) vs rotating opponents, each opponent
    with one player. Star: minutes 30 + g%5, fga 10 + g%7, pts = 2*fga + g%3 (so pts is driven by fga and minutes)."""
    rows = []
    base = pd.Timestamp("2025-11-01") if season == 2025 else pd.Timestamp("2024-11-01")
    day = 0
    for g in range(n):
        day += 1 if g == 7 else 2                                   # game 7 is a back-to-back (1 day after game 6)
        date = (base + pd.Timedelta(days=day)).strftime("%Y-%m-%d")
        gid = f"{season}-{g}"
        opp = OPPS[g % 3]
        home = g % 2 == 0
        fga = 10 + g % 7
        rows.append(_row(gid, season, date, "NYK", "Star One", 30 + g % 5, 2 * fga + g % 3, fga, home, fg3a=3 + g % 4, fg3m=g % 3, ast=2 + g % 6, tov=1 + g % 4, fta=3 + g % 5))
        if g % 4 != 0:
            rows.append(_row(gid, season, date, "NYK", "Mate Two", 28, 15, 12, home, fg3a=5, fg3m=2))
        elif g == 4 or g == 8:
            rows.append(_row(gid, season, date, "NYK", "Mate Two", 0, 0, 0, home))
        rows.append(_row(gid, season, date, opp, f"{opp} Guy", 33, 20, 17 + g % 5, not home, fg3a=6, fg3m=2, reb=9))
    return pd.DataFrame(rows)


@pytest.fixture()
def src(monkeypatch):
    holder = {"frame": _frame()}
    monkeypatch.setattr(T, "_read_sources", lambda: holder["frame"])
    T.clear_cache()
    yield holder
    T.clear_cache()


def test_opponent_derivation_home_flag_and_team_aggregates(src):
    out = T.trends("Star One", "points", 24.5)
    g0 = out["games"][0]
    assert out["status"] == "ok" and g0["opp"] == "BOS" and g0["home"] is True
    assert out["games"][1]["opp"] == "MIA" and out["games"][1]["home"] is False
    # teammates' makes exclude the star: game 0 the only teammate row is missing (g % 4 == 0), game 1 has Mate Two (fga 12 -> fgm 6)
    assert g0["tm_fga"] == 0 and out["games"][1]["tm_fga"] == 12 and out["games"][1]["tm_fgm"] == 6
    assert g0["opp_fga"] == 17 and g0["opp_reb"] == 9                              # the opponent's own line


def test_rest_is_days_since_the_team_game_and_null_for_the_first(src):
    games = T.trends("Star One", "points")["games"]
    assert games[0]["rest"] is None
    assert games[1]["rest"] == 2 and games[7]["rest"] == 1 and games[8]["rest"] == 2


def test_splits_count_every_game_once_and_vs_opp_filters(src):
    out = T.trends("Star One", "points", 24.5, opp="mia")
    sp = {s["key"]: s for s in out["splits"]}
    assert sp["home"]["n"] + sp["away"]["n"] == 40 and sp["home"]["n"] == 20
    assert sp["rest0"]["n"] + sp["rest1"]["n"] + sp["rest2p"]["n"] == 39                # the first game has no rest value
    assert sp["rest0"]["n"] == 1 and sp["rest1"]["n"] == 38 and sp["rest2p"]["n"] == 0
    assert sp["vs_opp"]["label"] == "vs MIA" and sp["vs_opp"]["n"] == len([i for i in range(40) if i % 3 == 1])
    assert sp["starter"]["n"] == 40 and sp["bench"]["n"] == 0
    assert "vs_opp" not in {s["key"] for s in T.trends("Star One", "points")["splits"]}


def test_summary_hist_and_over_under_push_on_an_integer_line(src):
    out = T.trends("Star One", "points", 24.0)
    vals = [2 * (10 + g % 7) + g % 3 for g in range(40)]
    s = out["summary"]["season"]
    assert s["n"] == 40 and s["over"] == sum(v > 24 for v in vals) and s["under"] == sum(v < 24 for v in vals)
    assert s["push"] == sum(v == 24 for v in vals) and s["push"] > 0 and s["over"] + s["under"] + s["push"] == 40
    assert s["over_rate"] == round(s["over"] / 40, 4) and s["mean"] == round(float(np.mean(vals)), 2)
    h = out["hist"]
    assert sum(h["counts"]) == 40 and h["hi"] == h["lo"] + len(h["counts"]) * h["width"] and h["lo"] <= min(vals) and h["hi"] > max(vals)
    assert out["summary"]["l5"]["n"] == 5 and out["summary"]["l10"]["n"] == 10 and out["summary"]["prev"]["n"] == 0
    no_line = T.trends("Star One", "points")["summary"]["season"]
    assert no_line["over"] is None and no_line["over_rate"] is None and no_line["mean"] == s["mean"]


def test_rolling_means_are_aligned_and_null_until_the_window_fills(src):
    out = T.trends("Star One", "points")
    r5, r10 = out["rolling"]["l5"], out["rolling"]["l10"]
    vals = [g["val"] for g in out["games"]]
    assert len(r5) == len(r10) == len(vals) and r5[:4] == [None] * 4 and r10[:9] == [None] * 9
    assert r5[4] == round(float(np.mean(vals[:5])), 2) and r10[-1] == round(float(np.mean(vals[-10:])), 2)


def test_teammate_out_with_and_without_arithmetic(src):
    out = T.trends("Star One", "points", 24.5)
    t = {r["player"]: r for r in out["teammate_out"]}
    assert set(t) == {"Mate Two"}                                                   # opponents are not teammates
    m = t["Mate Two"]
    # Mate Two has no row in games 0,4,8,...,36 and a 0-minute row in 4 and 8; his first game is 1, so game 0 is before he joined
    absent = [g for g in range(1, 40) if g % 4 == 0]
    assert m["n_without"] == len(absent) and m["n_with"] == 39 - len(absent) and m["n_with"] + m["n_without"] == 39
    pts = lambda g: 2 * (10 + g % 7) + g % 3
    assert m["mean_without"] == round(float(np.mean([pts(g) for g in absent])), 2)
    assert m["mean_with"] == round(float(np.mean([pts(g) for g in range(1, 40) if g % 4 != 0])), 2)
    assert m["fga_without"] == round(float(np.mean([10 + g % 7 for g in absent])), 1)
    assert m["min_avg"] == 28.0


def test_drivers_sign_floor_and_z(src):
    out = T.trends("Star One", "points")
    d = {x["key"]: x for x in out["drivers"]}
    assert set(d) == {"min", "fga", "fg3a", "fta", "tm_fga"} and d["fga"]["corr"] > 0.9 and d["fga"]["n"] == 40
    assert [x["corr"] for x in out["drivers"]] == sorted((x["corr"] for x in out["drivers"]), key=abs, reverse=True)
    assert d["fga"]["recent_mean"] is not None and d["fga"]["z"] is not None
    assert out["variance"]["share_from_minutes"] == round(out["minutes"]["corr_val_min"] ** 2, 3)
    assert out["minutes"]["per36"] == round(out["summary"]["season"]["mean"] / out["minutes"]["mean"] * 36, 2)


def test_driver_floor_needs_15_games(src):
    src["frame"] = _frame(n=12)
    T.clear_cache()
    out = T.trends("Star One", "points")
    assert out["status"] == "ok" and out["drivers"] == [] and out["scope"] == "two_seasons"


def test_combo_stat_sums_the_parts_and_unions_the_drivers(src):
    out = T.trends("Star One", "pra", 30.5)
    g = out["games"][3]
    assert g["val"] == g["pts"] + g["reb"] + g["ast"]
    keys = {x["key"] for x in out["drivers"]}
    assert {"fga", "min", "opp_miss", "tov", "tm_fgm"} <= keys and len(keys) == len(out["drivers"])
    assert T.trends("Star One", "threes")["games"][4]["val"] == out["games"][4]["fg3m"]


def test_previous_season_and_scope_switch(src):
    src["frame"] = pd.concat([_frame(n=40, season=2024), _frame(n=25, season=2025)], ignore_index=True)
    T.clear_cache()
    out = T.trends("Star One", "points")
    assert out["season"] == 2025 and out["seasons"] == [2024, 2025] and out["scope"] == "season"      # 25 >= 20 games
    assert out["summary"]["season"]["n"] == 25 and out["summary"]["prev"]["n"] == 40 and out["n_loaded"] == 65
    src["frame"] = pd.concat([_frame(n=40, season=2024), _frame(n=10, season=2025)], ignore_index=True)
    T.clear_cache()
    assert T.trends("Star One", "points")["scope"] == "two_seasons"


def test_playoff_games_are_excluded_unless_he_is_in_them(src):
    f = _frame(n=30)
    po = f[f["game_id"].isin(["2025-28", "2025-29"])].copy()
    f = f[~f["game_id"].isin(["2025-28", "2025-29"])]
    po["stype"] = "PO"
    src["frame"] = pd.concat([f, po], ignore_index=True)
    T.clear_cache()
    assert T.trends("Star One", "points")["n_loaded"] == 30                                   # last game is a playoff game: kept
    reg = _frame(n=32)
    post = reg[reg["game_id"].isin(["2025-5", "2025-6"])].copy()
    post["stype"] = "PO"
    src["frame"] = pd.concat([reg[~reg["game_id"].isin(["2025-5", "2025-6"])], post], ignore_index=True)
    T.clear_cache()
    out = T.trends("Star One", "points")                                                       # last game is regular season
    assert out["postseason_games_excluded"] == 2 and out["n_loaded"] == 30


def test_namesakes_pick_the_heavier_player_but_one_player_under_two_ids_is_merged(src):
    f = _frame(n=30)
    twin = f[f["name"] == "Star One"].copy()
    twin["team"], twin["min"], twin["pts"], twin["pid"] = "BOS", 10, 2, "p-twin"                  # same dates, other id -> a second person
    src["frame"] = pd.concat([f, twin], ignore_index=True)
    T.clear_cache()
    out = T.trends("Star One", "points")
    assert out["team"] == "NYK" and out["n_loaded"] == 30
    later = _frame(n=30)
    star = later["name"] == "Star One"
    later.loc[star & (later["game_id"].str.split("-").str[1].astype(int) < 15), "pid"] = "other-source-id"   # one person, two sources, no shared date
    src["frame"] = later
    T.clear_cache()
    merged = T.trends("star one", "points")
    assert merged["status"] == "ok" and merged["n_loaded"] == 30


def test_fail_soft_statuses_and_bad_stat(src, monkeypatch):
    assert T.trends("Nobody Atall", "points")["status"] == "no_player"
    assert T.trends(None, "points")["status"] == "no_player"
    with pytest.raises(ValueError):
        T.trends("Star One", "dunks")
    def boom():
        raise FileNotFoundError("no master")
    monkeypatch.setattr(T, "_read_sources", boom)
    T.clear_cache()
    assert T.trends("Star One", "points")["status"] == "no_data"


def test_master_utc_dates_become_et_game_days_and_unknown_tips_keep_the_date():
    base = pd.DataFrame({"game_id": ["a", "b", "c"], "date": ["2025-11-02", "2025-11-02", "2025-11-05"], "team": "NYK"})
    tips = pd.Series(pd.to_datetime(["2025-11-02T00:30:00Z", "2025-11-02T18:00:00Z"], utc=True), index=["a", "b"])   # 7:30pm ET Nov 1 / 1pm ET Nov 2
    T._ET_DAY.clear()
    out = T.to_et_days(base, tips)
    assert list(out["date"]) == ["2025-11-01", "2025-11-02", "2025-11-05"]
    assert list(base["date"]) == ["2025-11-02", "2025-11-02", "2025-11-05"]            # the input frame is not touched
    T._ET_DAY.clear()


def test_hoopr_rows_carry_home_opponent_free_minutes_and_dnp_as_zero():
    e = pd.DataFrame({"game_id": [1, 1, 1], "athlete_id": [10, 11, 12], "athlete_display_name": ["A One", "B Two", "C Three"],
                      "team_abbreviation": ["NY", "NY", "BOS"], "game_date": ["2026-11-02"] * 3, "season": [2027] * 3, "season_type": [2] * 3,
                      "did_not_play": [False, True, False], "minutes": [30.0, None, 25.0], "points": [20, None, 10], "rebounds": [5, None, 4],
                      "assists": [3, None, 2], "steals": [1, None, 0], "blocks": [0, None, 0], "turnovers": [2, None, 1], "fouls": [1, None, 2],
                      "field_goals_made": [8, None, 4], "field_goals_attempted": [15, None, 9], "three_point_field_goals_made": [2, None, 1],
                      "three_point_field_goals_attempted": [6, None, 3], "free_throws_attempted": [4, None, 2], "starter": [True, False, True],
                      "home_away": ["home", "home", "away"]})
    out = T._from_hoopr(e)
    assert list(out["team"]) == ["NYK", "NYK", "BOS"] and list(out["season"]) == [2026] * 3 and list(out["date"]) == ["2026-11-02"] * 3
    assert list(out["home"]) == [True, True, False] and list(out["min"]) == [30.0, 0.0, 25.0]
    assert out["game_id"].iloc[0] == "e1" and out["fg3a"].iloc[0] == 6 and out["pid"].iloc[0] == "e10"


def test_prepare_rejects_empty_input():
    assert T.prepare(pd.DataFrame()) is None and T.prepare(None) is None


@pytest.fixture(scope="module")
def client():
    with patch("main._get_engine", return_value=MagicMock()):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = "k"
        with m.app.test_client() as c:
            yield c


def test_route_auth_validation_and_args(client):
    h = {"X-API-Key": "k"}
    assert client.get("/api/public/edge/trends?player=x").status_code == 401
    assert client.get("/api/public/edge/trends?sport=nhl&player=x", headers=h).status_code == 400
    assert client.get("/api/public/edge/trends?player=x&line=abc", headers=h).status_code == 400
    assert client.get("/api/public/edge/trends?player=x&stat=dunks", headers=h).status_code == 400
    with patch("nba.trends.trends", return_value={"status": "ok"}) as f:
        r = client.get("/api/public/edge/trends?player=Jalen%20Brunson&stat=assists&line=6.5&opp=bos", headers=h)
    assert r.status_code == 200 and r.get_json() == {"status": "ok"} and r.headers["Cache-Control"] == "public, max-age=300"
    assert f.call_args.args == ("Jalen Brunson", "assists") and f.call_args.kwargs == {"line": 6.5, "opp": "BOS"}


def test_route_serves_the_real_payload_shape(client, src):
    r = client.get("/api/public/edge/trends?player=Star%20One&stat=points&line=24.5&opp=BOS", headers={"X-API-Key": "k"})
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok"
    for k in ("games", "summary", "hist", "rolling", "splits", "minutes", "teammate_out", "drivers", "variance", "proxies", "as_of", "seasons"):
        assert k in body
    assert "potential assists" in body["proxies"]["missing"] and len(body["games"]) == 40
