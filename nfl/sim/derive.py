"""Read every market off ONE simulated joint game (the point of the simulator).

    R = nfl.sim.engine.simulate(params, (home_spec, away_spec), n=...)
    team_markets(R, spread_line=-3.5, total_line=47.5)       # side 0 = home; nflverse convention: spread_line > 0 = home favoured
    prop_prob(R, side=0, j=3, stat="rec_yds", line=64.5)     # P(over) from the SAME draws
    joint_over(R, [(0, 0, "pass_yds", 249.5), (0, 3, "rec_yds", 64.5)])   # P(all legs over) -- a same-game parlay from the draws

Because every stat of every player is accumulated from the same simulated plays, the joint probability of several legs of
one game (QB passing yards with his receiver's yards, a team total with a player prop) is read directly from the draws; no
correlation is assumed. Output is UNVALIDATED research output: see docs/analysis/nfl_drive_simulator_*.md.
"""
from __future__ import annotations

import numpy as np

from nfl.sim import engine as E

_STAT_COLS = {"pass_attempts": (E.S_ATT,), "pass_completions": (E.S_CMP,), "pass_yds": (E.S_PYD,), "pass_tds": (E.S_PTD,),
              "interceptions": (E.S_INT,), "rush_attempts": (E.S_CAR,), "rush_yds": (E.S_RYD,), "targets": (E.S_TGT,),
              "receptions": (E.S_REC,), "rec_yds": (E.S_RECYD,), "anytime_td": (E.S_RTD, E.S_RECTD)}


def draws(R: "E.SimResult", side: int, j: int, stat: str) -> np.ndarray:
    cols = _STAT_COLS[stat]
    return R.stats[:, side, j, list(cols)].astype(np.int32).sum(axis=1)


def prop_prob(R: "E.SimResult", side: int, j: int, stat: str, line: float) -> float:
    """P(over) for a line (a push on an integer line counts half); anytime_td is P(at least one rushing / receiving TD)."""
    d = draws(R, side, j, stat)
    if stat == "anytime_td":
        return float((d >= 1).mean())
    return float((d > line).mean() + 0.5 * (d == line).mean())


def joint_over(R: "E.SimResult", legs: list) -> float:
    """P(every leg goes over) for legs = [(side, j, stat, line), ...] read from the same simulated games."""
    ok = np.ones(R.stats.shape[0], dtype=bool)
    for side, j, stat, line in legs:
        d = draws(R, side, j, stat)
        ok &= (d >= 1) if stat == "anytime_td" else (d > line)
    return float(ok.mean())


def team_markets(R: "E.SimResult", spread_line: float, total_line: float) -> dict:
    """Moneyline / spread cover / total / team totals of the home side (side 0) from the regulation score draws."""
    home, away = R.pts[:, 0].astype(float), R.pts[:, 1].astype(float)
    margin, total = home - away, home + away
    return {"mean_home": float(home.mean()), "mean_away": float(away.mean()), "mean_total": float(total.mean()),
            "mean_margin": float(margin.mean()), "p_home_win": float((margin > 0).mean() + 0.5 * (margin == 0).mean()),
            "p_home_cover": float((margin > spread_line).mean() + 0.5 * (margin == spread_line).mean()),
            "p_over": float((total > total_line).mean() + 0.5 * (total == total_line).mean()),
            "p_tie_in_regulation": float((margin == 0).mean())}
