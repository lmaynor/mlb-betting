"""nba.props.identity: SportsBlaze UUID <-> ESPN id bridge. Ambiguity must be DROPPED, never guessed."""
import pandas as pd
import pytest

from nba.props.identity import build_bridge


def _rows(players):
    """players: [(sb_id, name, team, last_date)] -> prepared-history-shaped frame (one row per player is enough)."""
    return pd.DataFrame([{"player_id": p, "name": n, "team": t, "date": pd.Timestamp(d), "tip_sort": pd.Timestamp(d),
                          "game_id": f"g-{p}", "season_year": 2025} for p, n, t, d in players])


def _xw(players):
    return pd.DataFrame([{"espn_id": e, "full_name": n, "team": t} for e, n, t in players])


END = "2026-06-10"


def test_unique_name_same_team_bridges():
    b = build_bridge(_rows([("sb1", "Jayson Tatum", "BOS", END)]), _xw([(4065648, "Jayson Tatum", "BOS")]))
    assert b.sb_id(4065648) == ("sb1", "ok")


def test_unknown_player_is_dropped_with_reason():
    b = build_bridge(_rows([("sb1", "Jayson Tatum", "BOS", END)]), _xw([(1, "Rookie Nobody", "BOS")]))
    sb, why = b.sb_id(1)
    assert sb is None and why == "unknown"
    assert b.sb_id(999999) == (None, "espn_id_not_in_crosswalk")
    assert b.sb_id("not-an-id") == (None, "bad_espn_id")


def test_namesakes_resolve_only_with_team_and_never_by_guess():
    """Two SportsBlaze players share a name (real data: two 'Jaylin Williams'). A roster team that matches exactly one
    bridges; a team that matches neither is AMBIGUOUS and dropped."""
    rows = _rows([("sbA", "Jaylin Williams", "OKC", END), ("sbB", "Jaylin Williams", "ATL", END)])
    b = build_bridge(rows, _xw([(10, "Jaylin Williams", "OKC")]))
    assert b.sb_id(10) == ("sbA", "ok")
    b2 = build_bridge(rows, _xw([(11, "Jaylin Williams", "DAL")]))        # traded to a third team: cannot tell
    assert b2.sb_id(11)[0] is None and b2.sb_id(11)[1] == "ambiguous"


def test_one_sb_id_claimed_by_two_espn_players_poisons_both():
    rows = _rows([("sb1", "Nic Claxton", "BKN", END)])
    b = build_bridge(rows, _xw([(1, "Nicolas Claxton", "BKN"), (2, "Nic Claxton", "BKN")]))
    assert b.sb_id(1)[0] is None and b.sb_id(2)[0] is None
    assert b.sb_id(1)[1] == "sb_id_claimed_twice"


def test_fuzzy_first_name_with_a_different_team_is_too_weak():
    rows = _rows([("sb1", "Alexander Sarr", "WAS", END)])
    b = build_bridge(rows, _xw([(7, "Alex Sarr", "DAL")]))
    assert b.sb_id(7)[0] is None and b.sb_id(7)[1] == "compat_name_and_team_differs"
    ok = build_bridge(rows, _xw([(7, "Alex Sarr", "WAS")]))
    assert ok.sb_id(7) == ("sb1", "ok") and ok.table.set_index("espn_id").loc[7, "how"] == "compat"


def test_unique_exact_name_survives_an_offseason_trade_and_is_flagged():
    rows = _rows([("sb1", "Kevin Durant", "PHX", END)])
    b = build_bridge(rows, _xw([(5, "Kevin Durant", "HOU")]))
    assert b.sb_id(5) == ("sb1", "ok")
    t = b.table.set_index("espn_id").loc[5]
    assert bool(t["team_changed"]) is True and t["sb_last_team"] == "PHX"


def test_retired_namesake_cannot_capture_a_current_player():
    """An SB player last seen 3 years ago is not a candidate, so the active namesake resolves cleanly (and a rookie with
    the retired player's name is NOT bridged to the old history)."""
    rows = _rows([("old", "Marcus Smart", "BOS", "2022-01-10"), ("new", "Marcus Smart", "MEM", END),
                  ("anchor", "Some Body", "NYK", END)])
    b = build_bridge(rows, _xw([(1, "Marcus Smart", "MEM")]))
    assert b.sb_id(1) == ("new", "ok")
    only_old = _rows([("old", "Marcus Smart", "BOS", "2022-01-10"), ("anchor", "Some Body", "NYK", END)])
    b2 = build_bridge(only_old, _xw([(1, "Marcus Smart", "BOS")]))
    assert b2.sb_id(1)[0] is None


def test_two_espn_players_sharing_a_name_need_team_confirmation():
    rows = _rows([("sbA", "Jaylin Williams", "OKC", END), ("sbB", "Jaylin Williams", "ATL", END)])
    xw = _xw([(10, "Jaylin Williams", "OKC"), (11, "Jaylin Williams", "ATL")])
    b = build_bridge(rows, xw)
    assert b.sb_id(10) == ("sbA", "ok") and b.sb_id(11) == ("sbB", "ok")      # both team-confirmed
    moved = build_bridge(_rows([("sbA", "Jaylin Williams", "OKC", END), ("sbB", "Jaylin Williams", "NYK", END)]), xw)
    assert moved.sb_id(11)[0] is None                                          # sbB's last team no longer matches


def test_real_crosswalk_has_no_one_to_many_mapping():
    """Property over the bridge table itself: a SportsBlaze id appears at most once among bridged rows."""
    rows = _rows([(f"sb{i}", f"Player {chr(65 + i)}son", "BOS", END) for i in range(6)])
    xw = _xw([(100 + i, f"Player {chr(65 + i)}son", "BOS") for i in range(6)])
    t = build_bridge(rows, xw).table
    ok = t[t["status"] == "ok"]
    assert ok["sb_player_id"].is_unique and ok["espn_id"].is_unique and len(ok) == 6
