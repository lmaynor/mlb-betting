"""NHL SOG BettingPros hook: the opt-in pregame grace window, carried price columns, and Arizona/Utah game-side mapping.
The default (slack_min=None) behaviour is pinned by tests/test_bettingpros_history_hooks.py."""
import numpy as np
import pandas as pd

from nhl.props import study
from tests.test_bettingpros_history_hooks import _late_game, _nhl_world, cons_frame, cons_row


def _setup():
    gl, names = _nhl_world()
    d, gid, home, away = _late_game(gl)
    team_rows = gl[(gl["game_id"] == gid) & (gl["team"] == home)].sort_values("player_id")
    pid = int(team_rows["player_id"].iloc[0])
    return gl, names, d, home, away, f"Skater{pid % 100} Team{home.lower()}"


def _row(player, d, home, away, **kw):
    return cons_row(player, home, game_date=d, stat="sog", line=1.5, p=0.5, home=home, away=away, **kw)


def test_slack_keeps_last_quotes_updated_just_after_the_start_but_not_in_play_lines():
    gl, names, d, home, away, nm = _setup()
    near = _row(nm, d, home, away, event_id=1, pregame=False)           # strict flag says post-game
    near["updated_max"] = f"{d} 23:04:00"                               # scheduled 23:00 -> +4 min: the book's last pre-game quote
    inplay = _row(nm, d, home, away, event_id=2, pregame=False)
    inplay["updated_max"] = f"{d} 23:50:00"                             # +50 min: an in-play line
    cons = cons_frame([near, inplay])
    strict = study.bettingpros_model_vs_market(cons, gl, names, min_books=3)
    assert strict["coverage"]["dropped"]["postgame_quote"] == 2 and strict["coverage"]["matched"] == 0
    slack = study.bettingpros_model_vs_market(cons, gl, names, min_books=3, slack_min=10.0)
    c = slack["coverage"]
    assert c["dropped"]["postgame_quote"] == 1 and c["matched"] == 1
    assert c["matched"] + sum(c["dropped"].values()) == c["consensus_rows"] == 2          # the books still balance


def test_frame_carries_the_best_prices_for_price_based_scoring():
    gl, names, d, home, away, nm = _setup()
    r = _row(nm, d, home, away, event_id=1)
    r["best_over_odds"], r["best_under_odds"], r["novig_over_odds"] = 110.0, -125.0, 105.0
    out = study.bettingpros_model_vs_market(cons_frame([r]), gl, names, min_books=3)
    f = out["frame"]
    assert len(f) == 1
    assert (f["best_over"].iloc[0], f["best_under"].iloc[0], f["novig_over"].iloc[0]) == (110.0, -125.0, 105.0)
    assert np.isnan(f["novig_under"].iloc[0])


def test_arizona_game_log_matches_a_quote_written_as_arizona_or_utah():
    gl, names, d, home, away, nm = _setup()
    gl = gl.replace({"team": {home: "ARI"}, "opp": {home: "ARI"}})        # the masters spell Arizona ARI until 2023-24
    names = names.assign(team=names["team"].replace({home: "UTA"}))     # bp_eval.name_table maps ARI -> UTA
    for quote_side in ("ARI", "UTA"):
        out = study.bettingpros_model_vs_market(cons_frame([_row(nm, d, quote_side, away, event_id=1)]), gl, names, min_books=3)
        assert out["coverage"]["dropped"]["no_game"] == 0 and out["coverage"]["matched"] == 1, quote_side
