"""Adapters of the game-line harness, offline: NFL event building, NBA quote reduction, NHL refusal, play_clock regression."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nba.game import market_eval as nba_me
from nfl.game import market_eval as nfl_me
from nfl.game.pbp_features import aggregate_team_game_pbp
from nhl.game import market_eval as nhl_me


def test_nfl_events_drop_pushes_and_ties_and_use_nflverse_sign():
    rec = pd.DataFrame([
        # home favoured by 3 (nflverse spread_line = +3), home wins by 7: covers
        dict(date="2024-09-05", margin=7.0, total=47.0, mkt_margin=3.0, mkt_total=46.0, p_home_ml=0.6, p_home_cover=0.5,
             p_away_cover=0.5, p_over=0.55, p_under=0.45, home_ml=-150, away_ml=130, home_sp_odds=-110, away_sp_odds=-110,
             over_odds=-110, under_odds=-110),
        # push on the spread (home wins by exactly the 3) and on the total
        dict(date="2024-09-08", margin=3.0, total=46.0, mkt_margin=3.0, mkt_total=46.0, p_home_ml=0.6, p_home_cover=0.5,
             p_away_cover=0.5, p_over=0.5, p_under=0.5, home_ml=-150, away_ml=130, home_sp_odds=-110, away_sp_odds=-110,
             over_odds=-110, under_odds=-110),
        # tie
        dict(date="2024-09-09", margin=0.0, total=40.0, mkt_margin=-1.0, mkt_total=41.0, p_home_ml=0.5, p_home_cover=0.5,
             p_away_cover=0.5, p_over=0.5, p_under=0.5, home_ml=100, away_ml=-120, home_sp_odds=-110, away_sp_odds=-110,
             over_odds=-110, under_odds=-110)])
    ev = nfl_me.to_events(rec)
    assert len(ev["moneyline (home wins)"]) == 2 and len(ev["spread (home covers)"]) == 2 and len(ev["total (over)"]) == 2
    sp = ev["spread (home covers)"]
    assert sp["y"].tolist() == [1, 1]                    # 7 > 3 covers; a 0-0 margin covers the home +1 line
    assert abs(sp["p_mkt"].iloc[0] - 0.5) < 1e-9


def test_nba_market_reduction_uses_fresh_books_on_the_modal_line_and_drops_consensus():
    def rows(mk, book, sides, line_a, odds, upd="2023-02-04 22:50:00", kind="book"):
        return [dict(date="2023-02-04", market=mk, event_id=1, scheduled="2023-02-04 23:00:00", away="WAS", home="BKN",
                     side=s, line=line_a if s in ("home", "over") else -line_a if mk == "spread" else line_a, book=book,
                     book_kind=kind, odds=o, is_open=False, updated=upd) for s, o in zip(sides, odds)]
    q = []
    for b in ("A", "B", "C"):
        q += rows("spread", b, ("home", "away"), 1.5, (-110, -110))
    q += rows("spread", "STALE", ("home", "away"), 4.5, (-110, -110), upd="2023-02-04 06:00:00")
    q += rows("spread", "Consensus", ("home", "away"), 9.5, (-110, -110), kind="consensus")
    for b in ("A", "B"):
        q += rows("total", b, ("over", "under"), 222.5, (-110, -110))                  # only 2 books: dropped
    m = nba_me.market_by_game(pd.DataFrame(q))
    g = m[("BKN", "WAS", "2023-02-04")]
    assert g["spread"]["line"] == 1.5 and g["spread"]["n"] == 3 and abs(g["spread"]["p"] - 0.5) < 1e-9
    assert "total" not in g
    assert nba_me._tri("PHO") == "PHX" and nba_me._tri("NOR") == "NOP" and nba_me._tri("UTH") == "UTA"


def test_nhl_refuses_without_odds(capsys):
    assert nhl_me.main([]) == 2
    assert "No NHL historical game-line odds" in capsys.readouterr().err


def test_play_clock_is_a_string_column_in_real_nflverse_pbp():
    """Regression: nfl.game.pbp_features took .mean() of the string play_clock column and crashed the whole engine."""
    n = 40
    pbp = pd.DataFrame({
        "game_id": ["2024_01_BAL_KC"] * n, "season": 2024, "week": 1, "game_date": "2024-09-05",
        "home_team": "KC", "away_team": "BAL", "posteam": ["KC", "BAL"] * (n // 2), "defteam": ["BAL", "KC"] * (n // 2),
        "home_score": 27, "away_score": 20, "play_type": ["pass", "run"] * (n // 2), "pass": [1, 0] * (n // 2),
        "rush": [0, 1] * (n // 2), "down": 1, "epa": 0.1, "wp": 0.5, "score_differential": 0,
        "play_clock": ["15", "x"] * (n // 2), "qb_dropback": [1, 0] * (n // 2), "sack": 0, "qb_hit": 0, "yardline_100": 50,
        "touchdown": 0, "success": 1, "fumble_lost": 0, "interception": 0, "qtr": 2, "game_seconds_remaining": 2000})
    pbp["play_clock"] = pbp["play_clock"].astype("string")
    rows = aggregate_team_game_pbp(pbp)
    assert rows and all(np.isfinite(r.seconds_per_play) for r in rows)
