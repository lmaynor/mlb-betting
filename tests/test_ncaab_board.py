"""ev.ncaab_board: join BettingPros / BestBook / Kalshi banked NCAAB game lines into one board (probed live 2026-11-04)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ev import game_odds_board as gob
from ev import ncaab_board as nb

START = "2026-11-05T01:00:00+00:00"            # 20:00 ET, 2026-11-04
NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)


def _q(market, line, book, a, b):
    return {"market": market, "line": line, "book": book, "a": a, "b": b}


def _file(fetched, games):
    return {"fetched_at": fetched, "games": games}


def _game(away, home, quotes, start=START, **kw):
    return {"away_team": away, "home_team": home, "commence_time": start, "quotes": quotes, **kw}


BP = _file("2026-11-04T17:40:00+00:00", [_game("CSF", "SDSU", [_q("ml", None, "fanduel", 700, -1200), _q("ml", None, "hard rock", 650, -1000),
                                                                 _q("total", 150.5, "fanduel", -110, -110)],
                                                away_name="Cal State Fullerton", home_name="San Diego State")])
BB = _file("2026-11-04T17:50:00+00:00", [_game("CSUF", "SDSU", [_q("ml", None, "draftkings", 750, -1300), _q("ml", None, "hardrock", 700, -1100),
                                                                 _q("total", 150.5, "draftkings", -108, -112)])])
KX = _file("2026-11-04T17:45:00+00:00", [{"away_team": "CSUF", "home_team": "SDSU", "commence_time": None, "date": "2026-11-04",
                                          "quotes": [_q("ml", None, "kalshi", 733, -809), _q("total", 150.5, "kalshi", 105, -120)]},
                                         {"away_team": "ZZZ", "home_team": "YYY", "commence_time": None, "date": "2026-11-04",
                                          "quotes": [_q("ml", None, "kalshi", 100, -120)]}])


def test_sim_handles_prefix_alias_and_distinct_codes():
    assert nb.sim("CSUF", "CSUF") == 1.0 and nb.sim("HP", "HPU") == 0.9 and nb.sim("BTLR", "BUT") == 1.0
    assert nb.sim("SC", "STET") < nb.MIN_SIM


def test_unify_joins_by_start_and_team_likeness_and_dedupes_books():
    games, names, counts = nb.unify({"bettingpros": BP, "bestbook": BB, "kalshi": KX})
    assert len(games) == 1 and games[0]["away_team"] == "CSF"                      # BettingPros codes are canonical
    assert counts["joined"] == 2 and counts["dropped"] == 1 and counts["own"] == 0   # the unmatched Kalshi game is dropped
    books = {(q["market"], q["book"]) for q in games[0]["quotes"]}
    assert ("ml", "hardrock") in books and ("ml", "hard rock") not in books         # spellings folded, first (newest) quote wins
    assert {b for _, b in books} == {"fanduel", "draftkings", "hardrock", "kalshi"}
    assert names == {"CSF": "Cal State Fullerton", "SDSU": "San Diego State"}
    hr = next(q for q in games[0]["quotes"] if q["book"] == "hardrock")
    assert (hr["a"], hr["b"]) == (700, -1100)                                      # BestBook (fresher) snapshot, never mixed with BP's


def test_bestbook_game_with_no_anchor_keeps_its_own_codes_and_far_start_does_not_join():
    far = _file("2026-11-04T17:50:00+00:00", [_game("CSUF", "SDSU", [_q("ml", None, "draftkings", 750, -1300),
                                                                     _q("ml", None, "fanduel", 700, -1200)],
                                                    start=(datetime.fromisoformat(START) + timedelta(hours=8)).isoformat())])
    games, _, counts = nb.unify({"bettingpros": BP, "bestbook": far})
    assert counts["own"] == 1 and len(games) == 2


def test_unified_games_build_a_normal_board():
    games, names, _ = nb.unify({"bettingpros": BP, "bestbook": BB, "kalshi": KX})
    p = gob.build_game_board("ncaab", None, NOW, None, games, {"fetched_at": BB["fetched_at"], "age_min": 10})
    assert p["status"] == "live" and p["sport"] == "ncaab"
    ml = next(r for r in p["rows"] if r["market"] == "ml")
    assert {b["book"] for b in ml["books"]} == {"fanduel", "draftkings", "hardrock", "kalshi"}
    assert [g["game_key"] for g in p["games"]] == ["ncaab:2026-11-04:CSF@SDSU"]


def test_ncaab_is_a_registered_board_sport_and_resolver():
    assert "ncaab" in gob.SPORTS and gob._resolver("ncaab")(" uk ") == "UK"


def test_banker_names_from_participants():
    from ev.ncaab_banker import bp_names
    offers = [{"participants": [{"id": "TENN", "team": {"city": "Tennessee"}}, {"id": "WOF", "team": {"city": "Wofford"}}]}]
    assert bp_names(offers) == {"TENN": "Tennessee", "WOF": "Wofford"}
