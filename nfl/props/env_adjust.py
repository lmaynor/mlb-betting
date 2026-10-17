"""Calibration of the NFL projection board's mean: a per-stat SCALE and, where it earns its place, a game-environment
term from the closing spread and total.

Why. nfl.props.engine_eval replayed the serving engine (ev.daily_projections) walk-forward over 2021-2025 and found
that every trailing-average projection -- the engine's and the plain L5 mean alike -- OVER-projects the player pool the
board covers by 3-7% in EVERY season (sum of outcomes / sum of projections = 0.93-0.99): the players a book quotes are
selected on high trailing usage, so they regress. That is a bias, correctable by a fitted scale. Separately, the
engine knows nothing about the game itself (an underdog in a 52-total shootout and a favourite in a 38-total slog get
the same mean), so an optional term learns what the lines add:

    mode "scale":  mu_cal = k * mu                                               k = sum(y) / sum(mu) over training seasons
    mode "env":    mu_cal = mu * exp(b0 + b1 * ln(implied_team_total / 22.5) + b2 * team_spread / 7)

    implied_team_total = total/2 + team_spread/2   (team_spread > 0 = team favoured; nflverse spread_line is the HOME
                                                    margin, so the away team's is its negation)

The env coefficients come from a quasi-Poisson fit (the engine's own as-of projection is the offset), regularised toward
0. Rows without both lines fall back to exp(b0): the calibration, without the game term. Only stats whose walk-forward
verdict is ADOPT are written to the shipped JSON (CALIB_PATH); the NORMAL family keeps its spread tied to the UNCALIBRATED
mean (cv was measured against players' raw trailing means; scaling the spread with the mean worsens log score).

This is accuracy tooling: it improves a projection of the STAT, not a claim of edge against a price.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

LEAGUE_TEAM_TOTAL = 22.5
SPREAD_SCALE = 7.0
RIDGE = 5.0                      # L2 on b1, b2 in the quasi-Poisson loss (absolute; a few thousand rows dwarf it)
MIN_FIT_ROWS = 400
CALIB_PATH = os.path.join(os.path.dirname(__file__), "projection_calibration.json")
_ALIAS = {"JAX": "JAC", "LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC", "WSH": "WAS"}


def canon(team) -> Optional[str]:
    if team is None or (isinstance(team, float) and math.isnan(team)):
        return None
    t = str(team).strip().upper()
    return _ALIAS.get(t, t) if t else None


def env_features(implied_total, team_spread):
    """(x1, x2) arrays; NaN where either line is missing."""
    it = np.asarray(implied_total, dtype=float)
    sp = np.asarray(team_spread, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        x1 = np.log(np.where(it > 0, it, np.nan) / LEAGUE_TEAM_TOTAL)
    return x1, sp / SPREAD_SCALE


def game_env_table(schedules: pd.DataFrame) -> pd.DataFrame:
    """schedules -> one row per (game_id, team): canonical team / opp, kickoff date, implied_total, team_spread.
    Rows without both lines keep NaN (the adjustment is then skipped, never guessed)."""
    s = schedules
    rows = []
    for side, other, sign in (("home", "away", 1.0), ("away", "home", -1.0)):
        spread = sign * pd.to_numeric(s["spread_line"], errors="coerce")
        total = pd.to_numeric(s["total_line"], errors="coerce")
        rows.append(pd.DataFrame({
            "game_id": s["game_id"], "season": s["season"], "week": s["week"], "gameday": s["gameday"].astype(str),
            "team": s[f"{side}_team"].map(canon), "opp": s[f"{other}_team"].map(canon),
            "team_spread": spread, "implied_total": total / 2.0 + spread / 2.0}))
    return pd.concat(rows, ignore_index=True)


def fit_coefs(y, mu, x1, x2) -> Optional[dict]:
    """Quasi-Poisson fit of y ~ mu * exp(b0 + b1 x1 + b2 x2). None when there are too few usable rows."""
    y, mu, x1, x2 = (np.asarray(a, dtype=float) for a in (y, mu, x1, x2))
    ok = np.isfinite(y) & np.isfinite(mu) & (mu > 0) & np.isfinite(x1) & np.isfinite(x2)
    if ok.sum() < MIN_FIT_ROWS:
        return None
    y, mu, x1, x2 = y[ok], mu[ok], x1[ok], x2[ok]
    X = np.column_stack([np.ones_like(x1), x1, x2])

    def loss(b):
        eta = X @ b
        return float(np.sum(mu * np.exp(eta) - y * (np.log(mu) + eta)) + RIDGE * (b[1] ** 2 + b[2] ** 2))

    def grad(b):
        r = mu * np.exp(X @ b) - y
        g = X.T @ r
        g[1:] += 2 * RIDGE * b[1:]
        return g

    res = minimize(loss, np.zeros(3), jac=grad, method="BFGS")
    b = res.x
    return {"b0": float(b[0]), "b1": float(b[1]), "b2": float(b[2]), "n": int(ok.sum())}


def fit_scale(y, mu) -> Optional[dict]:
    """k = sum(y) / sum(mu) over rows with a positive projection (None when there are too few rows)."""
    y, mu = np.asarray(y, dtype=float), np.asarray(mu, dtype=float)
    ok = np.isfinite(y) & np.isfinite(mu) & (mu > 0)
    if ok.sum() < MIN_FIT_ROWS or mu[ok].sum() <= 0:
        return None
    return {"k": float(y[ok].sum() / mu[ok].sum()), "n": int(ok.sum())}


def adjust(mu, x1, x2, coef: Optional[dict]):
    """Calibrated mean. coef {"k"}: mu * k. coef {"b0","b1","b2"}: mu * exp(eta), where rows with a missing line fall
    back to eta = b0 (the calibration without the game term). No coefficients: mu unchanged."""
    mu = np.asarray(mu, dtype=float)
    if not coef:
        return mu
    if "k" in coef:
        return mu * float(coef["k"])
    x1 = np.asarray(x1, dtype=float)
    x2 = np.asarray(x2, dtype=float)
    ok = np.isfinite(x1) & np.isfinite(x2)
    eta = coef["b0"] + np.where(ok, coef["b1"] * np.where(ok, x1, 0.0) + coef["b2"] * np.where(ok, x2, 0.0), 0.0)
    return mu * np.exp(eta)


def multiplier(coef: Optional[dict], implied_total=None, team_spread=None) -> float:
    """Scalar calibration factor for one player-game (the serving path); 1.0 with no coefficients."""
    if not coef:
        return 1.0
    if "k" in coef:
        return float(coef["k"])
    x1, x2 = env_features([np.nan if implied_total is None else implied_total],
                          [np.nan if team_spread is None else team_spread])
    return float(adjust([1.0], x1, x2, coef)[0])


def load_calibration(path: str = CALIB_PATH) -> dict:
    """{stat: coef} from the shipped JSON ({} when absent or unreadable: the board is then uncalibrated)."""
    try:
        with open(path) as fh:
            return {k: v for k, v in (json.load(fh).get("coefs") or {}).items() if v}
    except (OSError, ValueError):
        return {}
