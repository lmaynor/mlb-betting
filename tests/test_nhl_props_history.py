"""Historical SOG DB loader: pairing, ET game date, name resolution inside the event, outcome join / voids. Tiny synthetic sqlite."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from nhl.props import gamelog, history

SCHEMA = [
    """create table nhl_player_props (event_id text, game_date text, home_abbrev text, away_abbrev text, commence_time text,
       bookmaker text, player_name text, market text, over_under text, price int, line real, snapshot_time text)""",
    "create table games (game_id int, date text, home_team text, away_team text, home_score int, away_score int, status text)",
    """create table player_game_stats (game_id int, player_id int, player_name text, team text, position text, shots int,
       goals int, assists int, toi real, is_home int, pp_goals int, shifts int, takeaways int, rest_days int)""",
    """create table mp_player_game (game_id int, player_id int, game_date text, player_name text, team text, opponent text,
       is_home int, position text, situation text, icetime real, shifts int, ice_time_rank int, shots_on_goal int,
       shot_attempts int)""",
]
HOME, AWAY = "WSH", "MTL"
# 2026-01-22 00:10Z == 19:10 ET on 2026-01-21 (the snapshot-date column says 2026-01-22 for a late snapshot)
START = "2026-01-22T00:10:00Z"


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "h.db"
    c = sqlite3.connect(p)
    for s in SCHEMA:
        c.execute(s)
    c.execute("insert into games values (2025020800, '2026-01-21', 'WSH', 'MTL', 3, 2, 'FINAL')")
    c.execute("insert into games values (2025010009, '2025-09-25', 'WSH', 'MTL', 3, 2, 'FINAL')")        # preseason
    players = [(1, "Alex Ovechkin", "WSH", "L", 4, 1), (2, "Cole Caufield", "MTL", "R", 2, 0), (3, "Scratched Guy", "WSH", "C", None, 0)]
    for pid, nm, team, pos, shots, home in players:
        c.execute("insert into mp_player_game values (2025020800,?,?,?,?,?,?,?, 'all', 1000, 20, 1, ?, ?)",
                  (pid, "2026-01-21", nm, team, "MTL" if team == "WSH" else "WSH", home, pos, shots or 0, (shots or 0) + 2))
        c.execute("insert into mp_player_game values (2025020800,?,?,?,?,?,?,?, '5on4', 100, 2, 1, 0, 1)",
                  (pid, "2026-01-21", nm, team, "MTL" if team == "WSH" else "WSH", home, pos))
        if shots is not None:                                  # player 3 did not dress: no player_game_stats row
            c.execute("insert into player_game_stats values (2025020800,?,?,?,?,?,0,0,?,?,0,20,0,2)",
                      (pid, nm.split()[0][0] + ". " + nm.split()[1], team, pos, shots, 17.5, home))
    def q(book, name, over, under, line, snap="2026-01-21T21:05:37.123456Z", home="WSH", away="MTL"):
        for side, price in (("Over", over), ("Under", under)):
            if price is not None:
                c.execute("insert into nhl_player_props values ('e1','2026-01-22',?,?,?,?,?, 'player_shots_on_goal',?,?,?,?)",
                          (home, away, START, book, name, side, price, line, snap))
    q("betmgm", "Alex Ovechkin", -120, 100, 3.5)
    q("fanduel", "Alex Ovechkin", 110, -140, 3.5)
    q("betmgm", "Cole Caufield", -150, 120, 1.5)
    q("betmgm", "Scratched Guy", -110, -110, 0.5)
    q("bovada", "Cole Caufield", -150, None, 1.5)                          # one-sided -> dropped
    q("betmgm", "Alex Ovechkin", -120, 100, 2.5, snap="2026-01-22T00:12:00Z")  # AFTER start -> dropped
    q("betmgm", "Nobody Known", -110, -110, 1.5)
    c.commit()
    c.close()
    return str(p)


def test_quotes_get_et_game_date_and_post_start_snapshots_are_dropped(db):
    q = history.load_quotes(db)
    assert set(q["game_date"]) == {"2026-01-21"}                           # NOT the snapshot-date column
    p = history.pair_quotes(q)
    assert not (p["line"] == 2.5).any()                                    # captured after puck drop
    assert (p["mins_to_start"] >= 0).all()


def test_pairing_drops_one_sided_and_devigs(db):
    p = history.pair_quotes(history.load_quotes(db))
    assert not ((p["bookmaker"] == "bovada")).any()
    r = p[(p.bookmaker == "betmgm") & (p.player_name == "Alex Ovechkin")].iloc[0]
    assert (r["over"], r["under"]) == (-120, 100)
    assert r["overround"] == pytest.approx(0.5455 + 0.5, abs=1e-3)
    assert r["fair_over"] == pytest.approx(0.5455 / (0.5455 + 0.5), abs=1e-3)


def test_utah_alias_normalised():
    q = pd.DataFrame({"home_abbrev": ["Utah Mammoth"], "away_abbrev": ["ARI"], "commence_time": ["2026-01-22T00:10:00Z"],
                      "snapshot_time": ["2026-01-21T21:00:00Z"]})
    for c in ("home_abbrev", "away_abbrev"):
        q[c] = q[c].replace(history.TEAM_FIX)
    assert list(q.home_abbrev) == ["UTA"] and list(q.away_abbrev) == ["UTA"]


def test_resolution_grading_voids_and_unresolved(db):
    p = history.pair_quotes(history.load_quotes(db))
    names = history.name_table(db)
    p = history.resolve_players(p, names)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    games = pd.read_sql("select game_id, date, home_team, away_team from games", con)
    con.close()
    gl = gamelog.from_history_db(db)
    out = history.attach_outcomes(p, games, gl)
    st = out.set_index(["bookmaker", "player_name"])["status"]
    assert st[("betmgm", "Alex Ovechkin")] == "ok"
    assert st[("betmgm", "Scratched Guy")] == "void"                      # resolved but did not dress -> refund, not a loss
    assert st[("betmgm", "Nobody Known")] == "unresolved"
    ov = out[(out.bookmaker == "betmgm") & (out.player_name == "Alex Ovechkin")].iloc[0]
    assert ov["sog_actual"] == 4 and ov["won_over"] == 1.0                # 4 SOG > 3.5
    cf = out[(out.player_name == "Cole Caufield")].iloc[0]
    assert cf["sog_actual"] == 2 and cf["won_over"] == 1.0                # 2 SOG > 1.5
    assert out.loc[out.status != "ok", "won_over"].isna().all()           # void/unresolved are never graded


def test_event_with_no_final_game_is_no_game(db):
    p = history.pair_quotes(history.load_quotes(db))
    p = history.resolve_players(p, history.name_table(db))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    games = pd.read_sql("select game_id, date, home_team, away_team from games where game_id = 2025010009", con)
    con.close()
    out = history.attach_outcomes(p, games, gamelog.from_history_db(db))
    assert (out["status"] == "no_game").all()


def test_gamelog_from_db_excludes_preseason_and_dnp_and_carries_attempts(db):
    gl = gamelog.from_history_db(db)
    assert set(gl["game_id"]) == {2025020800}                             # the preseason game id (2025 01 xxxx) is out
    assert set(gl["player_id"]) == {1, 2}                                 # the non-dressed player has no row
    ov = gl[gl.player_id == 1].iloc[0]
    assert ov["attempts"] == 6 and ov["pp_attempts"] == 1 and ov["opp"] == "MTL" and ov["toi_sec"] == 17.5 * 60


def test_loader_opens_the_db_read_only(db):
    with pytest.raises(sqlite3.OperationalError):
        c = history._ro(db)
        c.execute("insert into games values (1,'x','a','b',0,0,'x')")


def test_gamelog_from_masters_schema_attempts_and_preseason():
    games = pd.DataFrame([
        {"game_id": 2025020001, "game_type": 2, "date": "2025-10-07", "away_abbr": "CHI", "home_abbr": "FLA"},
        {"game_id": 2025010001, "game_type": 1, "date": "2025-09-25", "away_abbr": "CHI", "home_abbr": "FLA"}])
    sk = pd.DataFrame([
        {"game_id": 2025020001, "date": "2025-10-07", "team_abbr": "FLA", "is_home": True, "player_id": 11, "name": "A. B",
         "position": "L", "toi_sec": 900, "played": True, "sog": 3, "goals": 0, "assists": 1},
        {"game_id": 2025020001, "date": "2025-10-07", "team_abbr": "CHI", "is_home": False, "player_id": 22, "name": "C. D",
         "position": "D", "toi_sec": 800, "played": True, "sog": 0, "goals": 0, "assists": 0},
        {"game_id": 2025020001, "date": "2025-10-07", "team_abbr": "CHI", "is_home": False, "player_id": 33, "name": "E. F",
         "position": "C", "toi_sec": 0, "played": False, "sog": 0, "goals": 0, "assists": 0},
        {"game_id": 2025010001, "date": "2025-09-25", "team_abbr": "FLA", "is_home": True, "player_id": 11, "name": "A. B",
         "position": "L", "toi_sec": 900, "played": True, "sog": 9, "goals": 0, "assists": 0}])
    shots = pd.DataFrame([
        {"game_id": 2025020001, "shooter_id": 11, "event": "sog", "skaters_for": 5, "skaters_against": 4},
        {"game_id": 2025020001, "shooter_id": 11, "event": "miss", "skaters_for": 5, "skaters_against": 5},
        {"game_id": 2025020001, "shooter_id": 11, "event": "block", "skaters_for": 5, "skaters_against": 5}])
    gl = gamelog.from_masters(games, sk, shots)
    assert list(gl.columns) == gamelog.GAMELOG_COLS
    assert set(gl.game_id) == {2025020001} and set(gl.player_id) == {11, 22}      # preseason + DNP rows excluded
    a = gl[gl.player_id == 11].iloc[0]
    assert a["opp"] == "CHI" and a["is_home"] == 1 and a["attempts"] == 3 and a["pp_attempts"] == 1
    d = gl[gl.player_id == 22].iloc[0]
    assert d["opp"] == "FLA" and d["attempts"] == 0 and d["pp_attempts"] == 0     # played, never shot, game HAS pbp -> 0 not NaN
