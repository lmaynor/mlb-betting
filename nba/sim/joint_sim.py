"""nba.sim.joint_sim -- ONE coherent joint outcome per NBA game, drawn n times (default 10,000), vectorised numpy, seeded.

UNVALIDATED. Offline only: nothing here is wired into the serving board, an alert or a Discord path. See
docs/analysis/nba_joint_simulator_2026-11-04.md for what the walk-forward evidence did and did not show.

    from nba.sim.joint_sim import GameInput, PlayerInput, Marginal, simulate_game
    draws = simulate_game(game, n=10000, seed=7)
    draws.p_over("jayson tatum", "pra", 40.5)
    draws.joint(("jayson tatum", "points", 27.5), ("jaylen brown", "points", 24.5))       # SGP leg pair

What one draw is (every number in the draw comes from the SAME latent game, so every prop is coherent with every other):

  1. Game script. Possession factor z_pace (shared by both teams), game total T (loaded on z_pace), margin M, so each team's points
     P_h = (T + M) / 2, P_a = (T - M) / 2 are integers that add up to T.
  2. Team pies given the script: team threes, assists and rebounds are linear in the team's own points (more points => more threes and
     assists), the pace, the opponent's points and threes (their misses), with fitted residual noise (nba.sim.fit).
  3. Minutes. Each modeled player's minutes = usual minutes x [mean fraction at this |margin| (garbage time: starters lose minutes,
     the bench gains them) + a draw from the empirical residual distribution (foul trouble, injury exits, overtime)]. Residuals are
     correlated within a team and across the game through a Gaussian copula (fitted).
  4. Shares. Each team pie is split among the modeled players AND a "rest of roster" bucket by multinomial sampling with weights
     (player's usual per-minute production) x (tonight's minutes) x (a log-normal form factor shared across his stats). Threes are
     a pie of their own; a player's points = 3 x his threes + his share of the team's remaining points, so his points can never
     fall below 3 x his threes AND every team's player points + the rest bucket add up EXACTLY to the team total.
  5. Props. points / rebounds / assists / threes are the draws; pra / pr / pa / ra are sums of the SAME draws (no independent moment
     matching), so a combo is exactly as dependent on its legs as the game says.

Marginal consistency. The caller supplies each player's analytic marginal (mean, sd, family) -- the hierarchical engine's. For every
(player, pie) the simulator solves a multiplicative weight (mean) and a noise scale (variance) on a pilot run of the SAME random numbers
so that the simulated mean and sd equal the analytic ones; the SHAPE is whatever the mechanism produces. `marginals="anchored"` goes one
step further and replaces each stat's draws by the analytic quantile of its simulated rank (a Gaussian-copula style transform): single-leg
P(over) then equals the analytic one exactly, the dependence structure is the simulator's, and the exact sum constraints no longer hold
(documented, tested). The default is chosen by the walk-forward evidence in the analysis document.

Honest limits: structural constants are fitted on 2019-2023 box scores only (nba.sim.fit); the game script's mean margin and sd come from
the caller (the production caller would pass the game-line engine's `PricedGame`); NO prices are used anywhere.
"""
from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.stats import nbinom, norm

STATS = ("points", "rebounds", "assists", "threes")
COMBOS = {"pra": ("points", "rebounds", "assists"), "pr": ("points", "rebounds"), "pa": ("points", "assists"),
          "ra": ("rebounds", "assists")}
ALL_STATS = STATS + tuple(COMBOS) + ("minutes",)
PARAMS_PATH = Path(__file__).with_name("params.json")
DEFAULT_N = 10_000
CALIB_ITERS = 7
SIGMA_MAX = 1.3                     # cap on a log-normal weight noise scale (a bigger one means the structure cannot reach the sd)
TEAM_COUNT_SHARE_CAP = 0.58         # threes can be at most this share of a team's points / 3 (keeps nonthree points >= 0)
# Player-level structure, tuned on the 2023 tune season (params["player"] overrides; see the analysis doc):
#   amp   : fixed log-sd of the shared player "form" factor nu_i in each pie's weight (NOT solved per player: the solved idiosyncratic
#           noise floors at zero for a high-variance scorer, which would otherwise erase the cross-stat dependence)
#   gamma : how much a player's share rises with the size of his team's pie (weight x (pie / expected pie)^gamma); 0 = shares are
#           independent of the pie, > 0 lets the rotation absorb a big night while the rest-of-roster bucket does not
DEFAULT_AMP = {"points": 0.18, "threes": 0.18, "assists": 0.12, "rebounds": 0.22}
DEFAULT_GAMMA = {"points": 1.0, "threes": 0.7, "assists": 1.0, "rebounds": 0.7}
DEFAULT_REST_NOISE = 0.4            # log-sd of the rest-of-roster weight (params["player"]["rest_noise"])
DEFAULT_TAU = {"threes": 0.07, "assists": 0.07, "rebounds": 0.07}   # extra shared log-sd of a non-points pie (params["player"]["tau"])
MIN_REST_FRAC = 0.02                # the rest bucket never holds less than this share of a pie


# ----------------------------------------------------------------------------------------------------------------- inputs
@dataclass
class Marginal:
    """The analytic single-leg distribution of one stat: 'normal' (mu, sd) or 'negbin' (mu, alpha: var = mu + alpha mu^2)."""
    family: str
    mu: float
    sd: float
    alpha: float | None = None

    def p_over(self, line: float) -> float:
        if self.mu <= 0:
            return 0.0 if line >= 0 else 1.0
        if self.family == "negbin":
            a = max(float(self.alpha if self.alpha is not None else max((self.sd ** 2 - self.mu) / self.mu ** 2, 1e-6)), 1e-6)
            n = 1.0 / a
            return float(1.0 - nbinom.cdf(math.floor(line), n, n / (n + self.mu)))
        return float(1.0 - norm.cdf((line - self.mu) / max(self.sd, 1e-6)))

    def ppf(self, u: np.ndarray) -> np.ndarray:
        if self.family == "negbin":
            a = max(float(self.alpha if self.alpha is not None else max((self.sd ** 2 - self.mu) / self.mu ** 2, 1e-6)), 1e-6)
            n = 1.0 / a
            p = n / (n + max(self.mu, 1e-9))
            top = int(nbinom.ppf(1.0 - 1e-9, n, p)) + 2
            cdf = np.cumsum(nbinom.pmf(np.arange(top + 1), n, p))      # table lookup: scipy's discrete ppf is a slow bisection
            return np.minimum(np.searchsorted(cdf, np.asarray(u), side="left"), top).astype(float)
        return np.maximum(np.rint(norm.ppf(u, self.mu, max(self.sd, 1e-6))), 0.0)


@dataclass
class PlayerInput:
    """One modeled player. `minutes` = expected minutes tonight (the analytic mean); `base_minutes` = the usual (pre-trim) minutes the
    minutes table is indexed by (default `minutes`). `marginals` needs points / rebounds / assists; threes is optional (absent =
    the player is not in the threes pie and all his points come from the points pie)."""
    name: str
    team: str                                  # "home" | "away"
    minutes: float
    marginals: dict
    base_minutes: float | None = None


@dataclass
class GameInput:
    game_key: str
    players: list
    mu_margin: float | None = None             # home minus away; None = implied by the players' means
    sd_margin: float | None = None             # None = the fitted ex-ante residual sd
    sd_total: float | None = None
    home: str = "HOME"
    away: str = "AWAY"


def load_params(path: str | Path | None = None) -> dict:
    with open(path or PARAMS_PATH) as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------------------------------------------- helpers
def _unit_t(rng: np.random.Generator, df: float, n: int) -> np.ndarray:
    """Student-t draw rescaled to unit variance (the game engine's margin / total are t(10) / t(12) with fat tails)."""
    return rng.standard_t(df, n) * math.sqrt((df - 2.0) / df)


def _seeds(seed: int, game_key: str, k: int) -> list:
    ss = np.random.SeedSequence([int(seed) & 0xFFFFFFFF, zlib.crc32(str(game_key).encode("utf-8"))])
    return ss.spawn(k)


def _bin_of(x: float, edges) -> int:
    return int(min(max(np.searchsorted(edges, x, side="right") - 1, 0), len(edges) - 2))


def _ratio_mean(tab_row, abs_edges, absM: np.ndarray) -> np.ndarray:
    mids = np.array([(abs_edges[i] + min(abs_edges[i + 1], abs_edges[i] + 12.0)) / 2.0 for i in range(len(abs_edges) - 1)])
    return np.interp(absM, mids, np.asarray(tab_row, dtype=float))


# ----------------------------------------------------------------------------------------------------------------- draws
class JointDraws:
    """n coherent joint outcomes of one game. Lookups are by (player name, stat); game-level series use player 'GAME'
    (`total`, `margin`, `home_points`, `away_points`). All series are float64 arrays of length n."""

    def __init__(self, n: int, series: dict, marginals: dict, meta: dict):
        self.n = n
        self._s = series
        self.marginals = marginals            # {(player, stat): Marginal}
        self.meta = meta

    def anchored(self, seed: int = 0) -> "JointDraws":
        """A copy whose single-leg marginals ARE the analytic ones: every stat's draws are replaced by the analytic quantile of the draw's
        rank (ties broken at random, deterministically), so the copula -- who is high when whom -- is the simulator's and P(over) of a
        single leg equals the analytic one up to the n-point quantile grid. Points no longer add up to the team total exactly and
        points >= 3 x threes can break by a point or two (the analytic marginals do not know about either); combos are sums of the
        anchored legs."""
        rj = np.random.default_rng(np.random.SeedSequence([int(seed) & 0xFFFFFFFF, zlib.crc32(str(self.meta["game_key"]).encode())]))
        series = {k: v for k, v in self._s.items() if k not in self.marginals and k[1] not in COMBOS}   # combos are re-summed
        for key, m in self.marginals.items():
            x = self._s[key]
            order = np.lexsort((rj.random(self.n), x))
            u = np.empty(self.n)
            u[order] = (np.arange(self.n) + 0.5) / self.n
            series[key] = np.asarray(m.ppf(u), dtype=np.float64)
        return JointDraws(self.n, series, self.marginals, dict(self.meta, marginals="anchored"))

    def players(self) -> list:
        return sorted({k[0] for k in self._s if k[0] != "GAME"})

    def series(self, player: str, stat: str) -> np.ndarray:
        key = (player, stat)
        if key in self._s:
            return self._s[key]
        if player != "GAME" and stat in COMBOS:
            out = sum(self._s[(player, k)] for k in COMBOS[stat])
            self._s[key] = out
            return out
        raise KeyError(f"no simulated series for {player!r} / {stat!r}")

    def _leg(self, leg) -> np.ndarray:
        """leg = (player, stat, line) or (player, stat, line, 'over'|'under') -> boolean array 'the leg wins' (strict, half-point lines)."""
        player, stat, line = leg[0], leg[1], float(leg[2])
        side = leg[3] if len(leg) > 3 else "over"
        x = self.series(player, stat)
        return (x > line) if side == "over" else (x < line)

    def p_over(self, player: str, stat: str, line: float) -> float:
        return float(np.mean(self.series(player, stat) > float(line)))

    def p_under(self, player: str, stat: str, line: float) -> float:
        return float(np.mean(self.series(player, stat) < float(line)))

    def joint(self, a, b) -> dict:
        """P(leg a AND leg b) from the SAME draws, beside the independent product and the lift. `se` is the Monte-Carlo standard
        error of p_joint (binomial). A leg is (player, stat, line[, 'over'|'under'])."""
        wa, wb = self._leg(a), self._leg(b)
        pa, pb, pj = float(wa.mean()), float(wb.mean()), float((wa & wb).mean())
        ind = pa * pb
        return {"p_a": pa, "p_b": pb, "p_joint": pj, "p_indep": ind, "lift": (pj / ind) if ind > 0 else float("nan"),
                "se": math.sqrt(max(pj * (1.0 - pj), 0.0) / self.n), "n": self.n}

    def joint_all(self, legs) -> float:
        """P(every leg wins) for any number of legs."""
        w = np.ones(self.n, dtype=bool)
        for leg in legs:
            w &= self._leg(leg)
        return float(w.mean())

    def corr(self, a: tuple, b: tuple) -> float:
        """Pearson correlation of two (player, stat) series."""
        x, y = self.series(*a), self.series(*b)
        if x.std() < 1e-12 or y.std() < 1e-12:
            return float("nan")
        return float(np.corrcoef(x, y)[0, 1])


# ----------------------------------------------------------------------------------------------------------------- simulate
class _Static:
    """Everything in one game's simulation that does NOT depend on the calibration state (common random numbers across the
    pilot iterations): script, team pies, minutes, the standard-normal noise blocks."""


def _build_static(game: GameInput, n: int, seed: int, P: dict) -> _Static:
    S = _Static()
    pl = game.players
    k = len(pl)
    S.n, S.pl = n, pl
    pp = P.get("player", {})
    S.amp = {**DEFAULT_AMP, **pp.get("amp", {})}
    S.gamma = {**DEFAULT_GAMMA, **pp.get("gamma", {})}
    S.rest_noise = float(pp.get("rest_noise", DEFAULT_REST_NOISE))
    S.tau = {**DEFAULT_TAU, **pp.get("tau", {})}
    g, tm = P["game"], P["team"]
    sg = _seeds(seed, game.game_key, 8)
    r_script, r_team, r_min, r_form, r_idio, r_rest = (np.random.default_rng(sg[i]) for i in range(6))
    S.alloc_seeds = sg[6:8]
    side = np.array([0 if p.team == "home" else 1 for p in pl])
    S.side = side
    # --- target means per player-stat
    tgt = {s: np.array([p.marginals[s].mu if s in p.marginals else 0.0 for p in pl], dtype=float) for s in STATS}
    has3 = np.array(["threes" in p.marginals for p in pl])
    S.has3 = has3
    tgt["nonthree"] = np.maximum(tgt["points"] - 3.0 * tgt["threes"], 0.05 * tgt["points"])
    tgt["threes"] = np.where(has3, tgt["threes"], 0.0)
    S.tgt = tgt
    rest = P["rest"]
    S.mu_pie = {}
    for pie, key in (("points", "nonthree"), ("threes", "threes"), ("assists", "assists"), ("rebounds", "rebounds")):
        row = []
        for s in (0, 1):
            tot = tgt[key][side == s].sum()
            row.append(tot / (1.0 - rest[pie]))
        S.mu_pie[pie] = np.array(row)
    mu_P = np.array([S.mu_pie["points"][s] + 3.0 * S.mu_pie["threes"][s] for s in (0, 1)])       # team points mean
    mu_T = float(mu_P.sum())
    mu_M = float(game.mu_margin) if game.mu_margin is not None else float(mu_P[0] - mu_P[1])
    sd_T = float(game.sd_total or g["sd_total_resid"])
    sd_M = float(game.sd_margin or g["sd_margin_resid"])
    S.mu_P, S.mu_T, S.mu_M, S.sd_T, S.sd_M = mu_P, mu_T, mu_M, sd_T, sd_M
    # --- game script
    z_pace = r_script.standard_normal(n)
    a = float(g["corr_total_pace"])
    z_e = _unit_t(r_script, 12.0, n)
    T = mu_T + sd_T * (a * z_pace + math.sqrt(max(1.0 - a * a, 0.0)) * z_e)
    M = mu_M + sd_M * _unit_t(r_script, 10.0, n)
    P_team = [np.maximum(np.rint((T + M) / 2.0), 60.0), np.maximum(np.rint((T - M) / 2.0), 60.0)]
    S.z_pace, S.P = z_pace, P_team
    S.T = P_team[0] + P_team[1]
    S.M = P_team[0] - P_team[1]
    # --- team pies
    e3 = [r_team.standard_normal(n) for _ in (0, 1)]
    eA = [r_team.standard_normal(n) for _ in (0, 1)]
    eR = [r_team.standard_normal(n) for _ in (0, 1)]
    X3, A, R = [], [], []
    for s in (0, 1):
        dP = P_team[s] - mu_P[s]
        x3 = S.mu_pie["threes"][s] + tm["threes"]["b_points"] * dP + tm["threes"]["resid_sd"] * e3[s]
        x3 = np.clip(np.rint(x3), 0, np.floor(TEAM_COUNT_SHARE_CAP * P_team[s] / 3.0))
        X3.append(x3)
    for s in (0, 1):
        dP = P_team[s] - mu_P[s]
        a_ = (S.mu_pie["assists"][s] + tm["assists"]["b_points"] * dP + tm["assists"]["b_threes"] * (X3[s] - S.mu_pie["threes"][s])
              + tm["assists"]["resid_sd"] * eA[s])
        A.append(np.clip(np.rint(a_), 0, None))
        o = 1 - s
        c = tm["rebounds"]
        r_ = (S.mu_pie["rebounds"][s] + c["b_pace"] * z_pace + c["b_points"] * dP + c["b_opp_points"] * (P_team[o] - mu_P[o])
              + c["b_threes"] * (X3[s] - S.mu_pie["threes"][s]) + c["b_opp_threes"] * (X3[o] - S.mu_pie["threes"][o])
              + c["resid_sd"] * eR[s])
        R.append(np.clip(np.rint(r_), 5, None))
    # Team threes / assists / rebounds are LESS dispersed than a Poisson count, and a multinomial split of an under-dispersed total makes
    # teammates look more negatively correlated than they are (Cov = p_i p_j (Var N - E N)); tau adds a shared log-normal wobble to the
    # pie so the split sees a total with Poisson-like spread. Points are not touched: they keep the exact team-total constraint.
    for pie, arr in (("threes", X3), ("assists", A), ("rebounds", R)):
        tau = float(S.tau.get(pie, 0.0))
        for s_ in (0, 1):
            eta = r_team.standard_normal(n)
            if tau > 0:
                arr[s_] = np.rint(arr[s_] * np.exp(tau * eta - 0.5 * tau * tau))
                if pie == "threes":
                    arr[s_] = np.minimum(arr[s_], np.floor(TEAM_COUNT_SHARE_CAP * P_team[s_] / 3.0))
    S.pie_count = {"threes": X3, "points": [P_team[s] - 3.0 * X3[s] for s in (0, 1)], "assists": A, "rebounds": R}
    # feasibility: a team's modeled players cannot hold more of a pie than the pie has (a caller whose margin disagrees with the players'
    # own means can shrink a team's pie below the sum of its players' targets); scale such a pie up so the rest bucket keeps >= MIN_REST_FRAC
    S.pie_mean = {}
    for pie, key in (("points", "nonthree"), ("threes", "threes"), ("assists", "assists"), ("rebounds", "rebounds")):
        mean = []
        for s in (0, 1):
            need = S.tgt[key][side == s].sum() / (1.0 - MIN_REST_FRAC)
            have = float(S.pie_count[pie][s].mean())
            if have < need and have > 0:
                S.pie_count[pie][s] = np.rint(S.pie_count[pie][s] * (need / have))
                have = float(S.pie_count[pie][s].mean())
            mean.append(have)
        S.pie_mean[pie] = np.array(mean)
    # --- minutes: table ratio at this |margin| + copula-correlated empirical residual
    mn = P["minutes"]
    ab = np.abs(S.M)
    edges, mpg_edges = mn["abs_margin_edges"], mn["mpg_bins"]
    q = np.asarray(mn["qgrid"])
    rho_g, rho_t = float(mn.get("rho_game", 0.0)), float(mn.get("rho_team", 0.0))
    zg = r_min.standard_normal(n)
    zt = [r_min.standard_normal(n), r_min.standard_normal(n)]
    mins = np.zeros((n, k))
    for i, p in enumerate(pl):
        base = float(p.base_minutes if p.base_minutes is not None else p.minutes)
        b = _bin_of(base, mpg_edges)
        mean_ratio = _ratio_mean(mn["mean_ratio"][b], edges, ab)
        z = math.sqrt(rho_g) * zg + math.sqrt(rho_t) * zt[side[i]] + math.sqrt(max(1.0 - rho_g - rho_t, 0.05)) * r_min.standard_normal(n)
        u = norm.cdf(z)
        resid = np.interp(u, q, np.asarray(mn["resid_quantiles"][b]))
        m = np.clip(base * (mean_ratio + resid), 0.0, 53.0)
        mins[:, i] = m * (float(p.minutes) / max(m.mean(), 1e-6))                   # anchor the mean to the analytic minutes
    S.mins = mins
    S.min_rel = mins / np.maximum(np.array([p.minutes for p in pl]), 1e-6)
    # bench (rest-of-roster) multiplier: the lowest mpg bin's ratio profile, mean-one
    prof = _ratio_mean(mn["mean_ratio"][0], edges, ab)
    S.bench = prof / prof.mean()
    # --- standard-normal noise blocks
    S.nu = r_form.standard_normal((n, k))
    S.eps = {s: r_idio.standard_normal((n, k)) for s in STATS}
    S.eps_nt = r_idio.standard_normal((n, k))
    S.eps_rest = {s: r_rest.standard_normal((n, 2)) for s in STATS}
    return S


def _run(S: _Static, theta: dict) -> dict:
    """One pass of the player layer for the current calibration state theta = {pie: (logc (k,), sigma (k,))}. -> {(i, stat): (n,)}"""
    n, k, side = S.n, len(S.pl), S.side
    out = {}
    for pie, key in (("threes", "threes"), ("points", "nonthree"), ("assists", "assists"), ("rebounds", "rebounds")):
        logc, sig = theta[pie]
        eps = S.eps_nt if pie == "points" else S.eps[pie]
        amp = S.amp[pie]
        w = (np.exp(logc)[None, :] * S.tgt[key][None, :] * S.min_rel
             * np.exp(amp * S.nu + sig[None, :] * eps - 0.5 * (amp ** 2 + sig[None, :] ** 2)))
        if pie == "threes":
            w = w * S.has3[None, :]
        counts = np.zeros((n, k))
        rg = np.random.default_rng(S.alloc_seeds[0 if pie in ("threes", "points") else 1])
        for s in (0, 1):
            idx = np.where(side == s)[0]
            if len(idx) == 0:
                continue
            if S.gamma[pie]:           # a bigger pie goes mostly to the rotation, not to the rest-of-roster bucket
                ratio = np.maximum(S.pie_count[pie][s] / max(float(S.pie_mean[pie][s]), 1e-9), 0.2)
                w[:, idx] *= (ratio ** S.gamma[pie])[:, None]
            rest_mean = max(S.pie_mean[pie][s] - S.tgt[key][idx].sum(), MIN_REST_FRAC * S.pie_mean[pie][s])
            rest = rest_mean * S.bench * np.exp(S.rest_noise * S.eps_rest[pie][:, s] - 0.5 * S.rest_noise ** 2)
            W = np.column_stack([w[:, idx], rest])
            pv = W / W.sum(axis=1, keepdims=True)
            c = rg.multinomial(S.pie_count[pie][s].astype(np.int64), pv)
            counts[:, idx] = c[:, :len(idx)]
        out[pie] = counts
    return out


def _stats_from(S: _Static, run: dict) -> dict:
    pts = 3.0 * run["threes"] + run["points"]
    return {"points": pts, "threes": run["threes"], "assists": run["assists"], "rebounds": run["rebounds"]}


def _calibrate(S: _Static, iters: int) -> tuple:  # -> (theta, stats of the best pass)
    """Solve per-(player, pie) weight multiplier (mean) and noise scale (variance) on the pilot: the SAME random numbers every pass.
    The mean step is divided by (1 - share) because a pie's shares are normalised (raising one weight raises its share by less);
    the last two passes move the means only, so the final noise scales are not disturbed by the last variance step."""
    pl = S.pl
    k = len(pl)
    theta = {pie: (np.zeros(k), np.full(k, 0.15)) for pie in ("threes", "points", "assists", "rebounds")}
    target_mu = {s: np.array([p.marginals[s].mu if s in p.marginals else 0.0 for p in pl]) for s in STATS}
    target_sd = {s: np.array([p.marginals[s].sd if s in p.marginals else 0.0 for p in pl]) for s in STATS}
    best, best_err = None, float("inf")
    for it in range(iters):
        run = _run(S, theta)
        st = _stats_from(S, run)
        z2 = 0.0                                   # how far this pass's simulated means sit from the targets, in Monte-Carlo SEs
        for stat in STATS:
            se = np.maximum(st[stat].std(axis=0), 1e-6) / math.sqrt(S.n)
            z2 += float(np.sum(((st[stat].mean(axis=0) - target_mu[stat]) / se) ** 2))
            short = np.minimum(st[stat].std(axis=0) - target_sd[stat], 0.0)          # only UNDER-dispersion counts: noise cannot remove variance
            z2 += float(np.sum((short / np.maximum(target_sd[stat] / math.sqrt(2.0 * S.n), 1e-6)) ** 2))
        if z2 < best_err:
            best, best_err = {kk: v.copy() for kk, v in st.items()}, z2
        for pie, stat in (("threes", "threes"), ("points", "points"), ("assists", "assists"), ("rebounds", "rebounds")):
            x = st[stat]
            mean_x, sd_hat = x.mean(axis=0), x.std(axis=0)
            logc, sig = theta[pie]
            mt, mu_hat = target_mu[stat], mean_x
            if pie == "points":                                           # the weight steers the NONTHREE mean
                mt = np.maximum(mt - 3.0 * st["threes"].mean(axis=0), 0.05 * target_mu["points"])
                mu_hat = mean_x - 3.0 * st["threes"].mean(axis=0)
            ok = (mt > 1e-9) & (mu_hat > 1e-9)
            share = np.clip(mu_hat / np.maximum(S.pie_mean[pie][S.side], 1e-9), 0.0, 0.6)
            logc = logc + np.where(ok, np.log(np.where(ok, mt, 1.0) / np.where(ok, mu_hat, 1.0)), 0.0) / (1.0 - share)
            if it < iters - 2:
                cv_t = target_sd[stat] / np.maximum(target_mu[stat], 1e-9)
                cv_h = sd_hat / np.maximum(mean_x, 1e-9)
                step = np.log((1.0 + cv_t ** 2) / (1.0 + cv_h ** 2))
                sig = np.clip(np.sqrt(np.maximum(sig ** 2 + step, 0.0)), 0.0, SIGMA_MAX)
            theta[pie] = (logc, sig)
    return theta, best


def simulate_game(game: GameInput, n: int = DEFAULT_N, seed: int = 0, params: dict | None = None,
                  marginals: str = "structural", calib_iters: int = CALIB_ITERS) -> JointDraws:
    """Draw `n` coherent joint outcomes for the game. Deterministic in (game, n, seed, params, marginals): the same call twice gives
    bit-identical arrays. marginals = 'structural' (default) | 'anchored' (see the module docstring)."""
    if marginals not in ("structural", "anchored"):
        raise ValueError("marginals must be 'structural' or 'anchored'")
    if not game.players:
        raise ValueError("a game needs at least one player")
    if {p.team for p in game.players} - {"home", "away"}:
        raise ValueError("PlayerInput.team must be 'home' or 'away'")
    for p in game.players:
        for s in ("points", "rebounds", "assists"):
            if s not in p.marginals:
                raise ValueError(f"{p.name}: missing {s} marginal")
    P = params if params is not None else load_params()
    S = _build_static(game, n, seed, P)
    theta, st = _calibrate(S, calib_iters)             # st = the pass whose means sit closest to the targets (same draws)
    series, margs = {}, {}
    for i, p in enumerate(game.players):
        for s in STATS:
            if s in p.marginals:
                series[(p.name, s)] = st[s][:, i].astype(np.float64)
                margs[(p.name, s)] = p.marginals[s]
        series[(p.name, "minutes")] = S.mins[:, i]
    series[("GAME", "total")] = S.T.astype(np.float64)
    series[("GAME", "margin")] = S.M.astype(np.float64)
    series[("GAME", "home_points")] = S.P[0].astype(np.float64)
    series[("GAME", "away_points")] = S.P[1].astype(np.float64)
    meta = {"game_key": game.game_key, "seed": seed, "n": n, "marginals": marginals, "mu_total": S.mu_T, "mu_margin": S.mu_M,
            "sd_total": S.sd_T, "sd_margin": S.sd_M,
            "sigma_noise": {pie: [float(x) for x in theta[pie][1]] for pie in theta},
            "players": [p.name for p in game.players], "validated": False,
            "label": "UNVALIDATED joint simulation (offline research; not a served price)"}
    d = JointDraws(n, series, margs, meta)
    return d.anchored(seed) if marginals == "anchored" else d


# ----------------------------------------------------------------------------------------------------------------- CLI
def game_from_dict(spec: dict) -> GameInput:
    """JSON spec -> GameInput. {"game_key", "mu_margin"?, "sd_margin"?, "sd_total"?, "players": [{"name", "team": "home"|"away",
    "minutes", "base_minutes"?, "marginals": {stat: {"family": "normal"|"negbin", "mu", "sd"?, "alpha"?}}}]}"""
    players = []
    for p in spec["players"]:
        marg = {}
        for stat, m in p["marginals"].items():
            mu = float(m["mu"])
            alpha = None if m.get("alpha") is None else float(m["alpha"])
            sd = float(m["sd"]) if m.get("sd") is not None else math.sqrt(mu + (alpha or 0.0) * mu * mu)
            marg[stat] = Marginal(m.get("family", "normal"), mu, sd, alpha)
        players.append(PlayerInput(str(p["name"]), p["team"], float(p["minutes"]), marg,
                                   None if p.get("base_minutes") is None else float(p["base_minutes"])))
    return GameInput(str(spec.get("game_key", "game")), players, spec.get("mu_margin"), spec.get("sd_margin"), spec.get("sd_total"),
                     spec.get("home", "HOME"), spec.get("away", "AWAY"))


def _parse_leg(txt: str) -> tuple:
    parts = txt.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(f"leg must be player:stat:line[:over|under], got {txt!r}")
    return (parts[0], parts[1], float(parts[2])) + ((parts[3],) if len(parts) == 4 else ())


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Draw one coherent joint NBA game outcome n times and price legs / pairs from it "
                                              "(UNVALIDATED research tool; prices nothing, serves nothing).")
    ap.add_argument("--game", required=True, help="path to a game spec JSON (see game_from_dict)")
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--marginals", choices=("structural", "anchored"), default="structural")
    ap.add_argument("--params", help="params.json (default: the committed fit)")
    ap.add_argument("--prop", action="append", default=[], help="player:stat:line  -> P(over); repeatable")
    ap.add_argument("--joint", action="append", default=[], help="legA+legB, a leg is player:stat:line[:over|under]; repeatable")
    a = ap.parse_args(argv)
    with open(a.game) as fh:
        game = game_from_dict(json.load(fh))
    draws = simulate_game(game, n=a.n, seed=a.seed, params=load_params(a.params) if a.params else None, marginals=a.marginals)
    out = {"label": draws.meta["label"], "validated": False, "n": a.n, "seed": a.seed, "marginals": a.marginals,
           "props": [], "joint": []}
    for txt in a.prop:
        player, stat, line = _parse_leg(txt)[:3]
        row = {"player": player, "stat": stat, "line": line, "p_over": draws.p_over(player, stat, line)}
        m = draws.marginals.get((player, stat))
        if m is not None:
            row["p_over_analytic"] = m.p_over(line)
        out["props"].append(row)
    for txt in a.joint:
        left, right = txt.split("+")
        out["joint"].append(dict(draws.joint(_parse_leg(left), _parse_leg(right)), legs=[left, right]))
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
