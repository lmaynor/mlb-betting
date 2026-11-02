"""Hierarchical generative NFL prop pricers: opportunity x efficiency funnels with coherent compound distributions.

The serving engine (ev.daily_projections, "empirical_ewm_v2") prices every stat as its own trailing average under a Normal /
NegBin. This module prices the same stats as one generative process, so related props can never contradict each other:

    pass_attempts  (3-regime game-script mixture of NegBins)
      -> pass_completions (Beta-Binomial over attempts, marginalised over the attempts mixture: P(C > A) == 0 exactly)
      -> pass_yds         (completions x gamma yards-per-completion, compound mean / variance, hurdle at C == 0)
      -> pass_tds, interceptions (Poisson-family intensities anchored to the implied team total / script x pressure)
    rush_attempts  (trailing carries + vacated share + script boost, NegBin)
      -> rush_yds         (NegBin carries x right-skewed per-carry yards, hurdle-gamma)
    targets -> receptions (NegBin, catch rate falls with aDOT) -> rec_yds (receptions x gamma yards-per-reception whose
                            spread grows with aDOT, hurdle-gamma)
    anytime_td     (3-zone intensity decomposition, shrunk toward the market-implied team touchdowns)

EVERY COEFFICIENT BELOW IS A HAND-SET PRIOR (collected in PRIORS), not a fitted value. Nothing here has been validated
against closing prices; accuracy evidence comes only from nfl.props.engine_eval (`--hierarchical`), per
docs/solutions/conventions/nfl-projection-engine-changes-need-walkforward-evidence.md. Rows priced by this module stay
`validated: false`.

Spread convention (the spec's, opposite of nfl.props.env_adjust): `team_spread` is the team's own point spread, NEGATIVE =
FAVOURED (-7.5 is a 7.5-point favourite). env_adjust's `team_spread` is POSITIVE = favoured; convert with
`spread_from_favoured_positive`.

Mean convention: the *_ewm inputs are trailing averages and therefore already contain the average game script a team
experiences. The script shifts below are applied relative to the league-average shift (`_league_mean_shift`), so a game
with an average spread reproduces the trailing mean and only a spread away from average moves it.

ASCII only. Pure numpy / scipy, no I/O: every function is deterministic and runs offline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.stats import betabinom, binom, gamma as _gamma, nbinom, norm

N_MAX = 100                      # support of every count pmf (0..N_MAX-1); tail mass beyond it is < 1e-12 for NFL counts

# --------------------------------------------------------------------------------------------------------- priors
PRIORS = {
    # game script (spread -> regime weights, attempt / carry shifts)
    "trail_center": 3.5, "lead_center": 6.5, "script_scale": 13.5,
    "att_shift_trail": 4.2, "att_shift_lead": -3.8,
    "car_boost_lead": 3.2, "car_boost_trail": -2.6, "car_share_ref": 0.55, "car_share_cap": 1.2,
    "league_spread_sd": 6.0,
    # completions
    "comp_kappa": 60.0, "comp_lo": 0.45, "comp_hi": 0.80,
    "comp_wind_per_mph": 0.004, "comp_pressure_slope": 0.03, "comp_adot_slope": 0.0035, "comp_cpoe_slope": 0.002,
    # yards per completion / reception / carry
    "ypc_yac_share": 0.45, "ypc_two_high_slope": 0.10, "ypc_wind_per_mph": 0.004, "att_wind_per_mph": 0.004,
    "pass_sd_base": 0.78, "pass_sd_adot_slope": 0.012, "pass_sd_adot_ref": 8.0,
    "rec_sd_base": 0.70, "rec_sd_adot_slope": 0.04, "rec_sd_adot_ref": 6.0, "rec_sd_lo": 0.6, "rec_sd_hi": 1.3,
    "ypr_ewm_w": 0.65, "ypr_adot_w": 0.82, "yac_w": 0.35,
    "rush_sd_mult": 1.35, "rush_ol_slope": 1.0, "rush_stuff_slope": 0.5, "rush_stuff_clip": 0.08,
    # receiving
    "shell_short_boost": 0.06, "shell_deep_boost": 0.07, "shell_scale": 0.15,
    "slot_adot_max": 9.0, "deep_adot_min": 11.0, "catch_adot_slope": -0.055, "catch_cpoe_slope": 0.04,
    # touchdowns / interceptions
    "td_w_gl": 0.45, "td_w_rz": 0.35, "td_w_ex": 0.20, "td_prior_w": 0.65, "td_anchor_w": 0.35,
    "team_td_floor": 0.8, "team_td_offset": 4.5, "team_td_div": 6.9, "pass_td_div": 7.0, "pass_td_share": 0.62,
    "int_trail_slope": 0.35,
    # league reference rates used to centre the coverage-shell effects
    "lg_two_high": 0.38, "lg_man": 0.22, "lg_single_high": 0.55,
}
P = PRIORS

COUNT_STATS = ("pass_attempts", "pass_completions", "rush_attempts", "receptions", "pass_tds", "interceptions")
YARD_STATS = ("pass_yds", "rush_yds", "rec_yds")
STATS = COUNT_STATS + YARD_STATS + ("anytime_td",)


def spread_from_favoured_positive(team_spread_favoured_positive: float) -> float:
    """env_adjust's team_spread (positive = favoured) -> this module's (negative = favoured)."""
    return -float(team_spread_favoured_positive)


# --------------------------------------------------------------------------------------------- game script weights
@dataclass(frozen=True)
class ScriptWeights:
    trail: float
    neutral: float
    lead: float


def script_weights(team_spread: Optional[float]) -> ScriptWeights:
    """Regime weights from the pre-game spread (negative = favoured). None -> the league-average weights."""
    if team_spread is None or not math.isfinite(team_spread):
        return _league_weights()
    t = float(norm.cdf((team_spread - P["trail_center"]) / P["script_scale"]))
    l = float(norm.cdf((-team_spread - P["lead_center"]) / P["script_scale"]))
    return ScriptWeights(t, max(1.0 - t - l, 0.0), l)


_LG_CACHE: dict = {}


def _league_weights() -> ScriptWeights:
    """Average regime weights over a symmetric league spread distribution N(0, league_spread_sd) (Gauss-Hermite)."""
    key = ("w", P["league_spread_sd"], P["trail_center"], P["lead_center"], P["script_scale"])
    if key not in _LG_CACHE:
        x, w = np.polynomial.hermite_e.hermegauss(41)
        w = w / w.sum()
        spreads = x * P["league_spread_sd"]
        t = float(np.sum(w * norm.cdf((spreads - P["trail_center"]) / P["script_scale"])))
        l = float(np.sum(w * norm.cdf((-spreads - P["lead_center"]) / P["script_scale"])))
        _LG_CACHE[key] = ScriptWeights(t, 1.0 - t - l, l)
    return _LG_CACHE[key]


def _league_mean_shift(kind: str) -> float:
    lw = _league_weights()
    if kind == "att":
        return P["att_shift_trail"] * lw.trail + P["att_shift_lead"] * lw.lead
    return P["car_boost_trail"] * lw.trail + P["car_boost_lead"] * lw.lead


# ------------------------------------------------------------------------------------------------------ distributions
def _nb_pmf(mu: float, alpha: float, n: int = N_MAX) -> np.ndarray:
    """NegBin pmf over 0..n-1 with var = mu + alpha mu^2 (alpha -> 0 is Poisson)."""
    k = np.arange(n)
    if mu <= 1e-9:
        out = np.zeros(n)
        out[0] = 1.0
        return out
    alpha = max(float(alpha), 1e-4)
    r = 1.0 / alpha
    return nbinom.pmf(k, r, r / (r + mu))


def nb_alpha_from_var(mu: float, var: float, floor: float = 0.004, cap: float = 1.5) -> float:
    """Dispersion alpha that gives total variance `var` at mean `mu` (clipped)."""
    if mu <= 0:
        return floor
    return float(min(max((var - mu) / (mu * mu), floor), cap))


@dataclass
class Dist:
    """A one-dimensional prop distribution: a count pmf, or a hurdle-gamma (mass p0 at 0, gamma above)."""
    mean: float
    var: float
    pmf: Optional[np.ndarray] = None
    g_shape: float = 0.0
    g_scale: float = 0.0
    p0: float = 0.0
    meta: dict = field(default_factory=dict)

    def sf(self, line: float) -> float:
        """P(X > line). Lines are x.5 almost everywhere; an integer line means 'strictly more'."""
        if line is None or not math.isfinite(line):
            return float("nan")
        k = math.floor(line)
        if self.pmf is not None:
            if k < 0:
                return 1.0
            if k + 1 >= len(self.pmf):
                return 0.0
            return float(min(max(self.pmf[k + 1:].sum(), 0.0), 1.0))
        x = k + 0.5                                   # continuity correction: integer yards, P(Y > k) = P(Y >= k + 1)
        if x < 0:
            return 1.0
        if self.g_shape <= 0:
            return 0.0 if x >= 0 else 1.0
        return float((1.0 - self.p0) * _gamma.sf(x, self.g_shape, scale=self.g_scale))

    def cdf(self, x) -> np.ndarray:
        """P(X <= x) for an array of x (integer yards / counts; the gamma side uses the same continuity correction as sf)."""
        x = np.asarray(x, dtype=float)
        k = np.floor(x)
        if self.pmf is not None:
            c = np.concatenate([[0.0], np.cumsum(self.pmf)])
            idx = np.clip(k + 1, 0, len(self.pmf)).astype(int)
            return np.where(k < 0, 0.0, c[idx])
        if self.g_shape <= 0:
            return np.where(k < 0, 0.0, 1.0)
        g = _gamma.cdf(np.maximum(k + 0.5, 0.0), self.g_shape, scale=self.g_scale)
        return np.where(k < 0, 0.0, self.p0 + (1.0 - self.p0) * g)

    def mass(self, y) -> np.ndarray:
        """Probability of exactly y (integer): pmf, or the 1-unit bin of the hurdle-gamma. Comparable across families."""
        y = np.round(np.asarray(y, dtype=float))
        return np.clip(self.cdf(y) - self.cdf(y - 1.0), 1e-12, 1.0)

    def ppf(self, q: float) -> float:
        if self.pmf is not None:
            c = np.cumsum(self.pmf)
            return float(min(int(np.searchsorted(c, q - 1e-12)), len(self.pmf) - 1))
        if q <= self.p0 or self.g_shape <= 0:
            return 0.0
        return float(_gamma.ppf(min((q - self.p0) / (1.0 - self.p0), 1.0 - 1e-9), self.g_shape, scale=self.g_scale))


def _pmf_dist(pmf: np.ndarray, **meta) -> Dist:
    pmf = np.maximum(pmf, 0.0)
    pmf = pmf / pmf.sum()
    k = np.arange(len(pmf))
    mean = float(np.sum(k * pmf))
    var = float(np.sum((k - mean) ** 2 * pmf))
    return Dist(mean=mean, var=var, pmf=pmf, p0=float(pmf[0]), meta=meta)


def compound_gamma(n_pmf: np.ndarray, mu_x: float, sd_x: float, **meta) -> Dist:
    """Sum of N iid positive draws (mean mu_x, sd sd_x), N ~ n_pmf, as a hurdle-gamma.

    Law of total variance: mean = E[N] mu_x, var = E[N] sd_x^2 + Var(N) mu_x^2 (exact). The positive part (N >= 1) is
    moment-matched to a gamma, so P(Y = 0) = P(N = 0) is exact and the right skew of the sum survives."""
    k = np.arange(len(n_pmf))
    en = float(np.sum(k * n_pmf))
    vn = float(np.sum((k - en) ** 2 * n_pmf))
    mean = en * mu_x
    var = en * sd_x ** 2 + vn * mu_x ** 2
    p0 = float(n_pmf[0])
    if mean <= 1e-9 or p0 >= 1.0 - 1e-9:
        return Dist(mean=0.0, var=0.0, p0=1.0, meta=meta)
    m_c = mean / (1.0 - p0)                               # moments of Y given N >= 1
    second_c = (var + mean * mean) / (1.0 - p0)
    v_c = max(second_c - m_c * m_c, 1e-6)
    return Dist(mean=mean, var=var, g_shape=m_c * m_c / v_c, g_scale=v_c / m_c, p0=p0,
                meta={**meta, "e_n": en, "var_n": vn})


# ------------------------------------------------------------------------------------------------------- inputs
@dataclass
class GameContext:
    """Game-level facts (shared by every player in the game). Any field may be None = unknown / neutral."""
    team_spread: Optional[float] = None            # NEGATIVE = favoured (see module docstring)
    implied_team_total: Optional[float] = None
    wind_mph: Optional[float] = None
    dome: bool = False                             # closed roof / dome: wind and cold are ignored
    temp_f: Optional[float] = None
    # opponent defence (trailing, as of the game week; NaN / None = league average)
    def_two_high_rate: Optional[float] = None
    def_man_rate: Optional[float] = None
    def_single_high_rate: Optional[float] = None
    def_yac_factor: Optional[float] = None         # def yac per reception allowed / league
    def_stuff_factor: Optional[float] = None       # def stuff rate / league
    net_pressure_mismatch: Optional[float] = None  # 1.0 = league-average pressure matchup (context_gaps.net_pressure_mismatch)
    n_ol_out: float = 0.0                          # own starting offensive linemen out / doubtful

    @property
    def weights(self) -> ScriptWeights:
        return script_weights(self.team_spread)

    @property
    def eff_wind(self) -> float:
        if self.dome or self.wind_mph is None or not math.isfinite(self.wind_mph):
            return 0.0
        return float(self.wind_mph)


@dataclass
class PlayerInputs:
    """One player's trailing inputs. Every *_ewm is the player's own trailing mean (script-average)."""
    pos: str = ""
    # passing
    pass_att_ewm: float = 0.0
    comp_rate: float = 0.63                         # trailing completions / attempts
    cpoe_dev: float = 0.0                           # recent CPOE minus longer-run CPOE, percentage points
    qb_adot: float = 8.0                            # QB average depth of target
    ypc_ewm: float = 11.0                           # yards per completion
    int_rate: float = 0.025                         # interceptions per attempt
    pass_td_ewm: float = 0.0                        # passing TDs per game
    alpha_att: float = 0.008
    alpha_pass_td: float = 0.25
    alpha_int: float = 0.6
    comp_kappa: Optional[float] = None
    # rushing
    carries_ewm: float = 0.0
    rush_share: float = 0.0                         # own share of team carries
    team_carries: float = 26.0
    vacated_rush_share: float = 0.0                 # role-allocated vacated share added to this player (0..1)
    ypc_rush_ewm: float = 4.3
    alpha_car: float = 0.18
    # receiving
    team_pass_att: float = 35.0                     # team trailing attempts / game (script-average)
    target_share: float = 0.0
    vacated_target_share: float = 0.0
    adot: float = 8.5
    catch_rate: float = 0.65
    ypr_ewm: float = 11.5
    yac_per_rec_ewm: float = 4.5
    alpha_rec: float = 0.30
    # touchdowns
    td_ewm: float = 0.0                             # rush + receiving TDs per game (the anytime_td prior)
    team_td_prior_sum: float = 0.0                  # sum of teammates' td_ewm incl. own; 0 = unknown -> no anchoring
    td_zone: Optional[dict] = None                  # {"gl": share, "rz": share, "ex": share} of the TEAM's zone usage
    vacated_rz_share: float = 0.0                   # role-allocated vacated red-zone touch share
    # volume calibration (nfl.props.projection_calibration) applied to the primary volume input
    vol_mult: float = 1.0


# ------------------------------------------------------------------------------------------------ passing funnel
def attempts_pmf(mu_ewm: float, game: GameContext, alpha: float = 0.008) -> np.ndarray:
    """3-regime mixture of NegBins. mu_ewm is the trailing mean; the neutral mean is mu_ewm minus the league-average shift."""
    mu0 = mu_ewm - _league_mean_shift("att")
    w = game.weights
    pmf = (w.neutral * _nb_pmf(mu0, alpha) + w.trail * _nb_pmf(mu0 + P["att_shift_trail"], alpha)
           + w.lead * _nb_pmf(max(mu0 + P["att_shift_lead"], 0.1), alpha))
    wind_cut = 1.0 - P["att_wind_per_mph"] * min(max(game.eff_wind - 12.0, 0.0), 20.0)
    if wind_cut < 1.0:                                  # wind trims attempts a little (rushing replaces them)
        pmf = _scale_pmf_mean(pmf, wind_cut)
    return pmf


def _scale_pmf_mean(pmf: np.ndarray, factor: float) -> np.ndarray:
    """Re-express a pmf at mean * factor by thinning (binomial) -- keeps a valid, slightly narrower count distribution."""
    out = np.zeros_like(pmf)
    for n in np.nonzero(pmf > 1e-12)[0]:
        out[: n + 1] += pmf[n] * binom.pmf(np.arange(n + 1), n, factor)
    return out


def completion_rate(pi: PlayerInputs, game: GameContext) -> float:
    """Expected completion probability per attempt, clipped to (comp_lo, comp_hi)."""
    p = pi.comp_rate
    p += P["comp_cpoe_slope"] * pi.cpoe_dev
    p -= P["comp_adot_slope"] * (pi.qb_adot - 8.0)
    p -= P["comp_wind_per_mph"] * max(game.eff_wind - 12.0, 0.0)
    nmm = game.net_pressure_mismatch
    if nmm is not None and math.isfinite(nmm):
        p -= P["comp_pressure_slope"] * (nmm - 1.0)
    return float(min(max(p, P["comp_lo"]), P["comp_hi"]))


def completions_pmf(att_pmf: np.ndarray, p_hat: float, kappa: float) -> np.ndarray:
    """Beta-Binomial(N, p_hat kappa, (1 - p_hat) kappa) marginalised over the attempts pmf: P(C > A) = 0 by construction."""
    a, b = p_hat * kappa, (1.0 - p_hat) * kappa
    out = np.zeros(len(att_pmf))
    out[0] += att_pmf[0]
    for n in range(1, len(att_pmf)):
        w = att_pmf[n]
        if w < 1e-13:
            continue
        out[: n + 1] += w * betabinom.pmf(np.arange(n + 1), n, a, b)
    return out


def _yac_two_high_factor(game: GameContext) -> float:
    f = 1.0
    if game.def_yac_factor is not None and math.isfinite(game.def_yac_factor):
        f *= 1.0 + P["ypc_yac_share"] * (min(max(game.def_yac_factor, 0.85), 1.15) - 1.0)
    th = game.def_two_high_rate
    if th is not None and math.isfinite(th):
        f *= 1.0 - P["ypc_two_high_slope"] * min(max((th - P["lg_two_high"]) / P["shell_scale"], -1.0), 1.0)
    return f


def price_pass_funnel(pi: PlayerInputs, game: GameContext) -> dict:
    """-> {'pass_attempts', 'pass_completions', 'pass_yds', 'pass_tds', 'interceptions'} Dists, mutually coherent."""
    mu_att = pi.pass_att_ewm * pi.vol_mult
    att = attempts_pmf(mu_att, game, pi.alpha_att)
    att_d = _pmf_dist(att)
    p_hat = completion_rate(pi, game)
    comp = completions_pmf(att, p_hat, pi.comp_kappa or P["comp_kappa"])
    comp_d = _pmf_dist(comp, p_hat=p_hat)
    ypc = pi.ypc_ewm * _yac_two_high_factor(game)
    ypc *= 1.0 - P["ypc_wind_per_mph"] * min(max(game.eff_wind - 12.0, 0.0), 20.0)
    sd_ypc = ypc * (P["pass_sd_base"] + P["pass_sd_adot_slope"] * (pi.qb_adot - P["pass_sd_adot_ref"]))
    yds_d = compound_gamma(comp_d.pmf, ypc, max(sd_ypc, 1e-6), mu_ypc=ypc, sd_ypc=sd_ypc)

    w = game.weights
    lw = _league_weights()
    lam_int = att_d.mean * pi.int_rate
    nmm = game.net_pressure_mismatch if (game.net_pressure_mismatch is not None
                                         and math.isfinite(game.net_pressure_mismatch)) else 1.0
    lam_int *= max(1.0 + P["int_trail_slope"] * (w.trail * nmm - lw.trail), 0.2)
    int_d = _pmf_dist(_nb_pmf(lam_int, pi.alpha_int))

    lam_ptd = pi.pass_td_ewm * pi.vol_mult
    it = game.implied_team_total
    if it is not None and math.isfinite(it) and it > 0:
        anchor = it / P["pass_td_div"] * P["pass_td_share"]
        lam_ptd = P["td_prior_w"] * lam_ptd + P["td_anchor_w"] * anchor
    ptd_d = _pmf_dist(_nb_pmf(lam_ptd, pi.alpha_pass_td))
    return {"pass_attempts": att_d, "pass_completions": comp_d, "pass_yds": yds_d, "pass_tds": ptd_d,
            "interceptions": int_d}


# ------------------------------------------------------------------------------------------------ rushing funnel
def price_rush_funnel(pi: PlayerInputs, game: GameContext) -> dict:
    """-> {'rush_attempts', 'rush_yds'} Dists."""
    w = game.weights
    boost = (P["car_boost_lead"] * w.lead + P["car_boost_trail"] * w.trail) - _league_mean_shift("car")
    boost *= min(pi.rush_share / P["car_share_ref"], P["car_share_cap"]) if pi.rush_share > 0 else 0.0
    mu_car = pi.carries_ewm * pi.vol_mult + 0.55 * pi.vacated_rush_share * pi.team_carries + boost
    if game.eff_wind > 12.0:
        mu_car *= 1.0 + 0.005 * min(game.eff_wind - 12.0, 20.0)
    mu_car = max(mu_car, 0.0)
    car_d = _pmf_dist(_nb_pmf(mu_car, pi.alpha_car))
    ypc = pi.ypc_rush_ewm * max(1.0 - P["rush_ol_slope"] * 0.025 * game.n_ol_out, 0.7)
    sf = game.def_stuff_factor
    if sf is not None and math.isfinite(sf):
        ypc *= 1.0 - min(max(P["rush_stuff_slope"] * (sf - 1.0), -P["rush_stuff_clip"]), P["rush_stuff_clip"])
    sd_step = P["rush_sd_mult"] * ypc
    yds_d = compound_gamma(car_d.pmf, ypc, sd_step, mu_ypc=ypc, sd_ypc=sd_step)
    return {"rush_attempts": car_d, "rush_yds": yds_d}


# ---------------------------------------------------------------------------------------------- receiving funnel
def receiver_role(adot: float, pos: str = "") -> str:
    """'rb' | 'slot' (short, aDOT < 9) | 'perimeter' (deep, aDOT >= 11) | 'mid'."""
    if str(pos).upper() in ("RB", "FB"):
        return "rb"
    if adot < P["slot_adot_max"] or str(pos).upper() == "TE" and adot < P["deep_adot_min"]:
        return "slot"
    if adot >= P["deep_adot_min"]:
        return "perimeter"
    return "mid"


def shell_multiplier(role: str, game: GameContext) -> float:
    """Target-rate multiplier from the opponent's coverage shell: short roles gain against two-high, deep against single-high / man."""
    sc = P["shell_scale"]
    if role in ("slot", "rb"):
        th = game.def_two_high_rate
        if th is None or not math.isfinite(th):
            return 1.0
        return 1.0 + P["shell_short_boost"] * min(max((th - P["lg_two_high"]) / sc, -1.0), 1.0)
    if role == "perimeter":
        parts = []
        sh, man = game.def_single_high_rate, game.def_man_rate
        if sh is not None and math.isfinite(sh):
            parts.append((sh - P["lg_single_high"]) / sc)
        elif game.def_two_high_rate is not None and math.isfinite(game.def_two_high_rate):
            parts.append(-(game.def_two_high_rate - P["lg_two_high"]) / sc)
        if man is not None and math.isfinite(man):
            parts.append((man - P["lg_man"]) / sc)
        if not parts:
            return 1.0
        return 1.0 + P["shell_deep_boost"] * min(max(sum(parts) / len(parts), -1.0), 1.0)
    return 1.0


def price_receiving_funnel(pi: PlayerInputs, game: GameContext) -> dict:
    """-> {'receptions', 'rec_yds'} Dists (targets mean in `meta`)."""
    w = game.weights
    mean_shift = P["att_shift_trail"] * w.trail + P["att_shift_lead"] * w.lead - _league_mean_shift("att")
    team_att = max(pi.team_pass_att * pi.vol_mult + mean_shift, 1.0)
    role = receiver_role(pi.adot, pi.pos)
    share = max(pi.target_share + pi.vacated_target_share, 0.0)
    mu_tgt = team_att * share * shell_multiplier(role, game)
    p_catch = _catch_rate(pi)
    mu_rec = mu_tgt * p_catch
    rec_d = _pmf_dist(_nb_pmf(mu_rec, pi.alpha_rec), targets=mu_tgt, p_catch=p_catch, role=role)
    yac_f = game.def_yac_factor if (game.def_yac_factor is not None and math.isfinite(game.def_yac_factor)) else 1.0
    ypr = (P["ypr_ewm_w"] * pi.ypr_ewm
           + P["yac_w"] * (P["ypr_adot_w"] * pi.adot + pi.yac_per_rec_ewm * min(max(yac_f, 0.85), 1.15)))
    sd_mult = min(max(P["rec_sd_base"] + P["rec_sd_adot_slope"] * max(pi.adot - P["rec_sd_adot_ref"], 0.0),
                      P["rec_sd_lo"]), P["rec_sd_hi"])
    sd_ypr = ypr * sd_mult
    yds_d = compound_gamma(rec_d.pmf, ypr, max(sd_ypr, 1e-6), mu_ypr=ypr, sd_ypr=sd_ypr, role=role)
    return {"receptions": rec_d, "rec_yds": yds_d}


def _catch_rate(pi: PlayerInputs) -> float:
    base = min(max(pi.catch_rate, 0.2), 0.95)
    z = math.log(base / (1.0 - base))
    z += P["catch_cpoe_slope"] * pi.cpoe_dev
    return float(1.0 / (1.0 + math.exp(-z)))


# --------------------------------------------------------------------------------------------------- touchdowns
def team_td_expectation(implied_team_total: Optional[float]) -> Optional[float]:
    """Market-implied offensive touchdowns: max(0.8, (implied total - 4.5) / 6.9)."""
    if implied_team_total is None or not math.isfinite(implied_team_total):
        return None
    return max(P["team_td_floor"], (float(implied_team_total) - P["team_td_offset"]) / P["team_td_div"])


def td_lambda_priors(players: list) -> list:
    """Raw per-player TD intensities for ONE team's skill players (list of PlayerInputs).

    With zone shares: lam_i = T * (0.45 gl_i + 0.35 rz_i + 0.20 ex_i) + 0.35 T vacated_rz_i, where T = sum of the team's own
    trailing TD rates and the zone shares are renormalised over the list so they sum to 1 per zone. Without zone data a
    player's lam is his own trailing rate (+ the vacated term)."""
    t_pool = sum(max(p.td_ewm, 0.0) for p in players)
    zs = {z: sum(max((p.td_zone or {}).get(z, 0.0), 0.0) for p in players) for z in ("gl", "rz", "ex")}
    out = []
    for p in players:
        if p.td_zone and all(zs[z] > 0 for z in zs):
            shares = {z: max(p.td_zone.get(z, 0.0), 0.0) / zs[z] for z in zs}
            lam = t_pool * (P["td_w_gl"] * shares["gl"] + P["td_w_rz"] * shares["rz"] + P["td_w_ex"] * shares["ex"])
        else:
            lam = max(p.td_ewm, 0.0)
        lam += P["td_w_rz"] * t_pool * max(p.vacated_rz_share, 0.0)
        out.append(lam * p.vol_mult)
    return out


def anchor_team_tds(lams: list, implied_team_total: Optional[float], anchor_w: Optional[float] = None) -> list:
    """Shrink a team's summed intensity toward the market-implied team touchdowns:
    lam_final_i = lam_i * (1 - w + w * Lambda / sum(lam)). w = 0.35 (empirical-Bayes prior); w = 1 is hard conservation
    (sum(lam_final) == Lambda). No total -> unchanged."""
    lam_team = team_td_expectation(implied_team_total)
    s = float(sum(lams))
    if lam_team is None or s <= 1e-9:
        return list(lams)
    w = P["td_anchor_w"] if anchor_w is None else anchor_w
    f = (1.0 - w) + w * lam_team / s
    return [l * f for l in lams]


def anytime_td_prob(lam: float) -> float:
    return float(1.0 - math.exp(-max(lam, 0.0)))


def price_anytime_td(pi: PlayerInputs, game: GameContext, teammates: Optional[list] = None) -> Dist:
    """P(1+ TD) = 1 - exp(-lam); returned as a Poisson count Dist so P(2+) etc. also price. `teammates` = the team's other
    skill players' inputs (for the zone shares and the team anchor); None -> own trailing rate anchored by
    team_td_prior_sum when that is set."""
    if teammates is not None:
        team = [pi] + list(teammates)
        lams = anchor_team_tds(td_lambda_priors(team), game.implied_team_total)
        lam = lams[0]
    else:
        lam = max(pi.td_ewm, 0.0) * pi.vol_mult + P["td_w_rz"] * max(pi.team_td_prior_sum, 0.0) * max(pi.vacated_rz_share, 0.0)
        lam_team = team_td_expectation(game.implied_team_total)
        if lam_team is not None and pi.team_td_prior_sum > 1e-9:
            lam *= (1.0 - P["td_anchor_w"]) + P["td_anchor_w"] * lam_team / (pi.team_td_prior_sum * pi.vol_mult)
    return _pmf_dist(_nb_pmf(lam, 1e-4, 12), lam=lam)           # alpha -> 0: Poisson


# ------------------------------------------------------------------------------------------------------ dispatch
_FUNNEL_OF = {"pass_attempts": price_pass_funnel, "pass_completions": price_pass_funnel, "pass_yds": price_pass_funnel,
              "pass_tds": price_pass_funnel, "interceptions": price_pass_funnel, "rush_attempts": price_rush_funnel,
              "rush_yds": price_rush_funnel, "receptions": price_receiving_funnel, "rec_yds": price_receiving_funnel}


def price(stat: str, pi: PlayerInputs, game: GameContext, teammates: Optional[list] = None) -> Dist:
    """The distribution of `stat` for one player-game under the hierarchical model."""
    if stat == "anytime_td":
        return price_anytime_td(pi, game, teammates)
    fn = _FUNNEL_OF.get(stat)
    if fn is None:
        raise ValueError(f"no hierarchical pricer for stat {stat!r}; known: {STATS}")
    return fn(pi, game)[stat]
