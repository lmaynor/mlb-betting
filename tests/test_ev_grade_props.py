"""ev.grade_props against REAL payload shapes (NHL official boxscore, ESPN NBA summary)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from ev import grade_props as gp

FX = Path(__file__).parent / "fixtures"
NHL_BOX = json.load(open(FX / "nhl_boxscore_2026020087.json"))        # FLA @ BUF, 2026-10-12, final 3-5
ESPN_SUM = json.load(open(FX / "espn_nba_summary_sample.json"))


def nhl_provider():
    def get(url):
        if "schedule" in url:
            return {"gameWeek": [{"date": "2026-10-12", "games": [
                {"id": 2026020087, "gameState": "OFF", "awayTeam": {"abbrev": "FLA"}, "homeTeam": {"abbrev": "BUF"}},
                {"id": 2026020088, "gameState": "FUT", "awayTeam": {"abbrev": "XXX"}, "homeTeam": {"abbrev": "YYY"}}]}]}
        return NHL_BOX
    return lambda day, a, h: gp.nhl_actuals(day, a, h, get=get)


def test_nhl_actuals_real_boxscore():
    g = nhl_provider()("2026-10-12", "FLA", "BUF")
    assert g["final"] and g["game_id"] == 2026020087
    tk = g["players"][8480801]                                     # B. Tkachuk: 1 A, 3 SOG, 7 hits
    assert (tk["assists"], tk["sog"], tk["hits"], tk["points"], tk["_played"]) == (1, 3, 7, 1, True)
    assert g["players"][8474593]["saves"] == 9                     # Markstrom: 9 saves on 11 shots


def test_nhl_not_final_or_not_found():
    p = nhl_provider()
    assert p("2026-10-12", "XXX", "YYY")["final"] is False
    assert p("2026-10-12", "AAA", "BBB")["reason"].startswith("game not found")


def _row(**kw):
    base = {"player_id": 8480801, "stat": "sog", "point": 2.5, "side": "OVER", "american": 120}
    base.update(kw)
    return base


def test_grade_row_over_under_push_and_profit():
    g = nhl_provider()("2026-10-12", "FLA", "BUF")                 # Tkachuk SOG = 3
    assert gp.grade_row(_row(), g) == ("win", pytest.approx(120.0))
    assert gp.grade_row(_row(side="UNDER", american=-110), g) == ("loss", -100.0)
    assert gp.grade_row(_row(point=3.0), g) == ("push", 0.0)
    assert gp.grade_row(_row(stat="hits", point=6.5, side="OVER", american=-150), g)[0] == "win"
    assert gp.grade_row(_row(stat="saves", player_id=8474593, point=9.5, side="UNDER", american=100), g)[0] == "win"


def test_void_pending_ungradable_never_become_losses():
    g = nhl_provider()("2026-10-12", "FLA", "BUF")
    assert gp.grade_row(_row(player_id=999999), g) == ("void", 0.0)               # not dressed
    assert gp.grade_row(_row(player_id=None), g) == ("ungradable", 0.0)
    assert gp.grade_row(_row(player_id=float("nan")), g) == ("ungradable", 0.0)   # pandas NaN
    assert gp.grade_row(_row(stat="pp_points"), g) == ("ungradable", 0.0)
    assert gp.grade_row(_row(), {"final": False, "players": {}}) == ("pending", 0.0)


def test_goalie_who_never_played_is_void():
    g = {"final": True, "players": {7: {"saves": 0, "_played": False}}}
    assert gp.grade_row(_row(player_id=7, stat="saves", point=20.5), g) == ("void", 0.0)


# ----------------------------------------------------------------------------- NBA / ESPN
def nba_provider():
    sb = {"events": [{"id": "401908944", "status": {"type": {"name": "STATUS_FINAL"}},
                      "competitions": [{"competitors": [{"homeAway": "away", "team": {"abbreviation": "NY"}},
                                                        {"homeAway": "home", "team": {"abbreviation": "TOR"}}]}]}]}
    return lambda day, a, h: gp.nba_actuals(day, a, h, get=lambda url: sb if "scoreboard" in url else ESPN_SUM)


def test_nba_actuals_real_summary_and_espn_abbrev_mapping():
    g = nba_provider()("2026-10-13", "NYK", "TOR")                 # ESPN says NY; our vocabulary says NYK
    assert g["final"]
    d = g["players"][5289900]                                      # Mohamed Diawara: 29 min, 13 pts, 3 reb, 1 ast, 3PT 3-7
    assert (d["points"], d["rebounds"], d["assists"], d["threes"], d["minutes"]) == (13.0, 3.0, 1.0, 3.0, 29.0)
    assert d["pra"] == 17.0 and d["pr"] == 16.0 and d["_played"] is True
    assert gp.grade_row({"player_id": 5289900, "stat": "pra", "point": 16.5, "side": "OVER", "american": -110}, g)[0] == "win"


def test_nba_game_not_on_scoreboard():
    assert nba_provider()("2026-10-13", "BOS", "MIA")["final"] is False


# ----------------------------------------------------------------------------- frame + summary
def test_first_sighting_dedup_and_summary_stats():
    base = dict(game_key="nhl:2026-10-12:FLA@BUF", player_norm="id8480801", stat="sog", point=2.5, side="OVER",
                book="fanduel", american=120, ev=0.05, flag=True, player_id=8480801)
    df = pd.DataFrame([{**base, "ts": "2026-10-12T10:00"}, {**base, "ts": "2026-10-12T11:00", "american": 90},
                       {**base, "book": "bovada", "ts": "2026-10-12T10:05", "side": "UNDER", "american": -110,
                        "player_id": 999}])
    fs = gp.first_sightings(df)
    assert len(fs) == 2 and fs[fs.book == "fanduel"].iloc[0]["american"] == 120      # earliest price, not the later one
    graded = gp.grade_frame(fs, "nhl", provider=nhl_provider())
    s = gp.summarize(graded)
    assert (s["quotes"], s["graded"], s["wins"], s["voids"]) == (2, 1, 1, 1)
    assert s["flat_pnl"] == 120.0 and s["flat_roi_pct"] == 120.0 and "UNDETERMINED" in s["verdict"]
    assert {r["book"] for r in gp.summarize(graded, "book")} == {"fanduel", "bovada"}


def test_provider_failure_leaves_rows_pending_not_lost():
    df = pd.DataFrame([dict(game_key="nhl:2026-10-12:FLA@BUF", player_norm="x", stat="sog", point=2.5, side="OVER",
                            book="fanduel", american=120, ev=0.05, flag=True, ts="t", player_id=8480801)])
    out = gp.grade_frame(df, "nhl", provider=lambda *a: (_ for _ in ()).throw(RuntimeError("api down")))
    assert out.iloc[0]["result"] == "pending"
