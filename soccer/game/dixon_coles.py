"""soccer.game.dixon_coles -- time-decayed Dixon-Coles team ratings on a composite xG + goals observation.

Model: each club i has an attack rating alpha_i and a defense rating beta_i (sum alpha = 0 identifiability),
plus a league intercept mu0, home advantage gamma, and the Dixon-Coles low-score correlation rho:

    ln lambda_home = mu0 + gamma + alpha_home - beta_away
    ln lambda_away = mu0 + alpha_away - beta_home

Observation (what a played match contributes): a BLEND of chance quality and outcome, so a team that creates
2.0 xG and scores 0 is not rated like a team that created nothing:
  * with Understat xG:  eff = 0.70 * xG + 0.30 * goals          (xg_variant "understat_shot_v1")
  * without xG (football-data rows, UCL gaps): eff = 0.60 * SOT_xG_proxy + 0.40 * goals,
    SOT_xG_proxy = shots_on_target * 0.30 (league-typical goals per shot on target)
  * with neither: eff = goals.

Matches are weighted by exponential time decay (half-life ~110 days) and the fit is anchored toward a ClubElo
prior (attack/defense priors from the club's Elo strength) so promoted or thin-sample clubs start near their
Elo expectation instead of wherever a handful of matches puts them. The fit is a Poisson quasi-likelihood
(effective goals are non-integer) maximized with deterministic Adam; rho is then fit by profile likelihood
over a grid on the ACTUAL low scores. Only matches strictly BEFORE `as_of` enter the fit: a match on or after
the pricing date can never move the rating that prices it (zero time-travel leakage, pinned by tests).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

log = logging.getLogger("soccer.dixon_coles")

XG_BLEND = 0.70                 # weight on Understat xG in the composite observation (rest: actual goals)
SOT_PROXY_BLEND = 0.60          # weight on the shots-on-target xG proxy when no xG exists
SOT_GOAL_RATE = 0.30            # goals per shot on target, league-typical conversion
HALF_LIFE_DAYS = 110.0
ELO_LOG_GOALS = 0.9             # log-goal supremacy per 400 Elo points, split evenly attack / defense
DEFAULT_ELO_SHRINKAGE = 3.0     # L2 pull toward the ClubElo prior, in pseudo-observation units
RHO_GRID = tuple(round(-0.25 + 0.01 * i, 2) for i in range(51))
SANITY_THRESHOLD = 0.18         # p_home divergence vs BOTH references that trips the Elo sanity guard


def effective_goals(match: dict) -> tuple[float, float]:
    """The composite (home, away) scoring observation for one played match (see the module docstring)."""
    hg, ag = float(match["home_goals"]), float(match["away_goals"])
    xh, xa = match.get("xg_home"), match.get("xg_away")
    if xh is not None and xa is not None:
        return XG_BLEND * float(xh) + (1.0 - XG_BLEND) * hg, XG_BLEND * float(xa) + (1.0 - XG_BLEND) * ag
    hst, ast = match.get("hst"), match.get("ast")
    if hst is not None and ast is not None:
        return (SOT_PROXY_BLEND * float(hst) * SOT_GOAL_RATE + (1.0 - SOT_PROXY_BLEND) * hg,
                SOT_PROXY_BLEND * float(ast) * SOT_GOAL_RATE + (1.0 - SOT_PROXY_BLEND) * ag)
    return hg, ag


@dataclass
class Ratings:
    mu0: float
    gamma: float
    rho: float
    alpha: dict[str, float]
    beta: dict[str, float]
    as_of: date
    n_matches: int
    teams: tuple[str, ...] = field(default_factory=tuple)

    def expected_goals(self, home: str, away: str) -> tuple[float, float] | None:
        """(lambda_home, lambda_away) for a fixture, or None when either club is unrated (fail closed: the
        caller skips the fixture rather than pricing a league-average stand-in)."""
        if home not in self.alpha or away not in self.alpha:
            return None
        lh = math.exp(self.mu0 + self.gamma + self.alpha[home] - self.beta[away])
        la = math.exp(self.mu0 + self.alpha[away] - self.beta[home])
        return lh, la


def _elo_priors(teams: list[str], elo: dict[str, float] | None) -> tuple[np.ndarray, np.ndarray]:
    if not elo:
        return np.zeros(len(teams)), np.zeros(len(teams))
    vals = [elo[t] for t in teams if t in elo]
    mean_elo = float(np.mean(vals)) if vals else 1500.0
    strength = np.array([ELO_LOG_GOALS * (elo.get(t, mean_elo) - mean_elo) / 400.0 for t in teams])
    return strength / 2.0, strength / 2.0


def fit_ratings(matches: list[dict], as_of: date, elo: dict[str, float] | None = None,
                half_life_days: float = HALF_LIFE_DAYS, elo_shrinkage: float = DEFAULT_ELO_SHRINKAGE,
                iterations: int = 2500) -> Ratings:
    """Fit the ratings on matches played strictly before `as_of`. Deterministic: fixed init (the Elo prior),
    fixed iteration count, no randomness."""
    hist = [m for m in matches if m["date"] < as_of and m.get("home_goals") is not None]
    if not hist:
        raise ValueError("fit_ratings: no played matches before as_of")
    teams = sorted({m["home"] for m in hist} | {m["away"] for m in hist})
    tidx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = np.array([tidx[m["home"]] for m in hist])
    ai = np.array([tidx[m["away"]] for m in hist])
    eff = np.array([effective_goals(m) for m in hist], dtype=float)
    eff_h, eff_a = eff[:, 0], eff[:, 1]
    days = np.array([(as_of - m["date"]).days for m in hist], dtype=float)
    w = np.exp(-math.log(2.0) * days / float(half_life_days))
    a_prior, b_prior = _elo_priors(teams, elo)

    # Parameters: [mu0, gamma, alpha(n), beta(n)]. Adam on the weighted Poisson quasi-log-likelihood.
    theta = np.concatenate([[math.log(1.35), 0.25], a_prior.copy(), b_prior.copy()])
    mom = np.zeros_like(theta)
    vel = np.zeros_like(theta)
    lr = 0.08
    for step in range(1, iterations + 1):
        mu0, gamma = theta[0], theta[1]
        alpha, beta = theta[2:2 + n], theta[2 + n:]
        lh = np.exp(mu0 + gamma + alpha[hi] - beta[ai])
        la = np.exp(mu0 + alpha[ai] - beta[hi])
        # d(-ll)/d(param): for log-lambda params, residual = (lambda - eff) * w summed over the matches touched.
        rh, ra = w * (lh - eff_h), w * (la - eff_a)
        g = np.zeros_like(theta)
        g[0] = rh.sum() + ra.sum()
        g[1] = rh.sum()
        np.add.at(g, 2 + hi, rh)
        np.add.at(g, 2 + ai, ra)
        np.add.at(g, 2 + n + ai, -rh)
        np.add.at(g, 2 + n + hi, -ra)
        # identifiability: pull sum(alpha) to 0; anchor: pull ratings toward the Elo prior.
        g[2:2 + n] += 1.0 * alpha.sum() / max(n, 1) + elo_shrinkage * (alpha - a_prior) * 0.01
        g[2 + n:] += elo_shrinkage * (beta - b_prior) * 0.01
        mom = 0.9 * mom + 0.1 * g
        vel = 0.999 * vel + 0.001 * g * g
        m_hat = mom / (1.0 - 0.9 ** step)
        v_hat = vel / (1.0 - 0.999 ** step)
        theta -= lr * m_hat / (np.sqrt(v_hat) + 1e-8)

    mu0, gamma = float(theta[0]), float(theta[1])
    alpha = {t: float(theta[2 + i]) for i, t in enumerate(teams)}
    beta = {t: float(theta[2 + n + i]) for i, t in enumerate(teams)}
    # recenter alpha to sum exactly 0 (mu0 absorbs the shift)
    shift = float(np.mean(list(alpha.values())))
    alpha = {t: v - shift for t, v in alpha.items()}
    mu0 += shift

    ratings = Ratings(mu0=mu0, gamma=gamma, rho=0.0, alpha=alpha, beta=beta,
                      as_of=as_of, n_matches=len(hist), teams=tuple(teams))
    ratings.rho = _fit_rho(hist, ratings)
    return ratings


def _fit_rho(hist: list[dict], ratings: Ratings) -> float:
    """Profile-likelihood rho on actual goals: maximize sum log[ tau(x, y) * Pois(x; lh) * Pois(y; la) ] over the
    grid. Only the four low-score cells move with rho; the rest is a constant offset across the grid."""
    from soccer.game.matrix_pricer import tau
    cells = []
    for m in hist:
        lam = ratings.expected_goals(m["home"], m["away"])
        if lam is None:
            continue
        x, y = int(m["home_goals"]), int(m["away_goals"])
        if x <= 1 and y <= 1:
            cells.append((x, y, lam[0], lam[1]))
    if not cells:
        return 0.0
    best_rho, best_ll = 0.0, -math.inf
    for rho in RHO_GRID:
        ll = 0.0
        for x, y, lh, la in cells:
            t = tau(x, y, lh, la, rho)
            if t <= 0:
                ll = -math.inf
                break
            ll += math.log(t)
        if ll > best_ll:
            best_ll, best_rho = ll, rho
    return float(best_rho)


def elo_sanity(p_model: tuple[float, float, float], p_elo: tuple[float, float, float] | None,
               p_market: tuple[float, float, float] | None, threshold: float = SANITY_THRESHOLD
               ) -> tuple[tuple[float, float, float], str | None]:
    """The Elo sanity guard. When the model's p_home diverges from BOTH the ClubElo prior and the market
    consensus by more than `threshold` at once, the model is presumed wrong (a ratings blow-up, a missing
    star, a bad join): shrink 50% toward ClubElo and flag the row ELO_DIVERGENCE. With only one reference
    available the guard cannot trip -- a model is allowed to disagree with a single source."""
    if p_elo is None or p_market is None:
        return p_model, None
    if abs(p_model[0] - p_elo[0]) > threshold and abs(p_model[0] - p_market[0]) > threshold:
        blended = tuple(0.5 * m + 0.5 * e for m, e in zip(p_model, p_elo))
        s = sum(blended)
        return tuple(v / s for v in blended), "ELO_DIVERGENCE"
    return p_model, None
