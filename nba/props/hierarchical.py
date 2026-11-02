"""nba.props.hierarchical -- pure, offline building blocks for the NBA serving projection engine (ev/daily_projections.py).

Four pieces, none of which touches the network, a bucket or a trained artifact:

  1. Role-weighted teammate-absence redistribution (`compute_vacated_shares`). A teammate who is Out vacates a share of the
     team's minutes / points / rebounds / assists / three-point attempts / steals / blocks. Each ACTIVE rotation player
     absorbs that vacated production in proportion to HIS OWN per-minute rate in that specific stat, damped by an
     elasticity, so an absent point guard lifts the backup guard's assists but barely touches a center's, and an absent
     center lifts the remaining big's rebounds and blocks but not a guard's. This replaces the old flat
     `1.0 + 0.04 * teammate_abs_wt` multiplier that was applied to every stat alike.
  2. A minutes distribution (`project_minutes_distribution`): the EWM minutes mean trimmed for blowout risk (pre-game spread)
     and back-to-back fatigue, plus a regime-mixture standard deviation (competitive / blowout / foul-trouble) that the
     engine turns into extra stat variance ONLY above what the history already contains.
  3. Attempt-first threes (`project_nba_threes`): EWM of three-point ATTEMPTS times an empirical-Bayes shrunken 3P%, and a
     shot-diet scale for the points spread (`shot_diet_sd_scale`).
  4. Covariance-coherent combos (`within_player_corr`, `combo_moments`): pra / pr / pa / ra are derived from the component
     (points, rebounds, assists) moments and a shrunken within-player correlation matrix, so the combo mean is exactly the
     sum of the component means.

LEAKAGE: every function that reads a player's history takes `as_of` ('YYYY-MM-DD', in the ROWS' date system) and uses only
rows dated STRICTLY BEFORE it. NB the cockpit master labels each game with the UTC date of its tip (see
docs/solutions/integration-issues/sportsblaze-master-date-is-utc.md), so an ET game day is the wrong cutoff for it: live
serving passes as_of=None (the master holds only finished games), and only replays pass a cutoff. tests/test_nba_props_hierarchical.py mutates rows on / after `as_of` and asserts nothing moves.

EVIDENCE STATUS: none of the constants below was fitted or validated here. The elasticities, the blowout curve, the
foul-trouble weight, the shot-diet coefficients and the correlation priors are the specification's PRIORS; no walk-forward
run on real data backs them (the repo has no historical NBA prop lines before the BettingPros crawl, and this module was
built without access to the box-score master). The module changes the PROJECTION; it does not claim an edge.
Rows are the cockpit_form shape (`mlb_core.data.cockpit_form._load_nba`): chronological dicts with `_date`, `minutes`,
`points`, `rebounds`, `assists`, `three_points_made`, `three_points_attempted`, `free_throws_made`, `steals`, `blocks`,
`fouls`. A missing column degrades that piece to "no adjustment" -- it never invents a value.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
from scipy.stats import norm

# ------------------------------------------------------------------------------------------------ constants
STATS = ("minutes", "points", "rebounds", "assists", "threes", "steals", "blocks")
STAT_COLS = {"minutes": ("minutes",), "points": ("points",), "rebounds": ("rebounds",), "assists": ("assists",),
             "threes": ("three_points_attempted", "three_points_made"), "steals": ("steals",), "blocks": ("blocks",)}

ROTATION_MIN_MPG = 12.0            # a rotation regular averages at least this many minutes over his last 10 games
ACTIVE_MAX_ABSENCE = 0.8           # absence probability at / above this = not available to absorb anything
# damped elasticity: the share of a vacated stat that the active players re-absorb (spec priors, UNVALIDATED)
ETA = {"minutes": 0.55, "points": 0.60, "assists": 0.65, "rebounds": 0.60, "threes": 0.60, "steals": 0.55,
       "blocks": 0.60}
AFFINITY_POWER = 3.0               # absorb weight = minutes * (per-minute rate)^3: specialists take the lion's share
MAX_ROLE_BOOST = 1.40              # one player's multiplier for one stat never exceeds this (old flat rule capped at 1.20)
MAX_MINUTES_GAIN = 6.0             # nor does a vacated-minutes boost add more than this many minutes

STARTER_MIN = 28.0                 # EWM minutes at / above this = a starter for the blowout trim
BLOWOUT_TRIM = -3.8                # minutes shaved from a starter at p(blowout) = 1
BLOWOUT_CENTER, TRIM_SCALE, WEIGHT_SCALE = 13.5, 10.0, 11.5   # |spread| curve: trim uses /10.0, regime weight /11.5
B2B_TRIM, B2B_MIN_MPG = -1.2, 32.0
MAX_MEAN_MIN = 46.0
BASE_MIN_CV = 0.15                 # regulation-regime sd as a fraction of the mean minutes
BLOWOUT_MIN_CV = 0.22              # the blowout regime is looser: starters are pulled anywhere from the 3rd quarter on
FOUL_WEIGHT, FOUL_SHIFT, PF36_REF = 0.06, -7.5, 2.8
BLOWOUT_BENCH_GAIN = 6.0
# Overtime: a right-tail regime (+5 minutes scaled to the player's load). The history already contains typical overtime, so
# it moves VARIANCE only (the mean is fixed by the caller); its weight is the league rate at a typical spread and rises in
# tight games. Spec PRIOR -- no historical spreads are in the box-score master, so it cannot be walk-forward tested.
OT_WEIGHT_REF, OT_SPREAD_REF, OT_SPREAD_DECAY, OT_WEIGHT_CLIP, OT_MINUTES = 0.06, 9.0, 12.0, (0.02, 0.12), 5.0
STARTER_BLOWOUT_CAP = 29.0         # starters are right-censored near here when the game is out of reach

LEAGUE_3P_PCT = 0.362
EB_K_3P = 150.0                    # prior attempts behind the league 3P%
EB_WINDOW = 60                     # games of attempts / makes behind the shrunk 3P%
THREE_HALFLIFE = 8.0               # games
MIN_GAMES = 4

SHOT_DIET_WINDOW = 20
SHOT_DIET_3_COEF, SHOT_DIET_3_REF = 0.22, 0.30
SHOT_DIET_FT_COEF, SHOT_DIET_FT_REF = 0.15, 0.18
SHOT_DIET_CLIP = (0.80, 1.25)

COMBOS = {"pra": ("points", "rebounds", "assists"), "pr": ("points", "rebounds"), "pa": ("points", "assists"),
          "ra": ("rebounds", "assists")}
CORR_ORDER = ("points", "rebounds", "assists")
CORR_PRIOR = np.array([[1.00, 0.20, 0.18],
                       [0.20, 1.00, 0.22],
                       [0.18, 0.22, 1.00]])
CORR_WINDOW, CORR_SHRINK, CORR_MIN_GAMES = 30, 0.50, 10


# ------------------------------------------------------------------------------------------------ row helpers
def _num(row: dict, col: str) -> float:
    try:
        return float(row.get(col))
    except (TypeError, ValueError):
        return float("nan")


def _stat_value(row: dict, stat: str) -> float:
    """First finite column of the stat's candidates (threes: attempts, else makes)."""
    for col in STAT_COLS[stat]:
        v = _num(row, col)
        if v == v:
            return v
    return float("nan")


def _day(row: dict) -> str | None:
    d = row.get("_date") or row.get("_sort")
    return None if d is None else str(d)[:10]


def prior_rows(rows: list, as_of: str | None = None) -> list:
    """Chronological rows dated STRICTLY before `as_of` ('YYYY-MM-DD'); everything when `as_of` is None. A row with no
    date is kept (the cockpit shape always carries one; this only matters for hand-built fixtures)."""
    if as_of is None:
        return list(rows)
    out = []
    for r in rows:
        d = _day(r)
        if d is None or d < as_of:
            out.append(r)
    return out


def _played(rows: list) -> list:
    return [r for r in rows if (_num(r, "minutes") > 0)]


def ewm(values, halflife: float) -> float:
    v = np.asarray(values, dtype=float)
    n = len(v)
    w = 0.5 ** ((n - 1 - np.arange(n)) / max(halflife, 1e-6))
    return float(np.sum(w * v) / np.sum(w))


def ewm_minutes(rows: list, as_of: str | None = None, halflife: float = 8.0, n: int = 20) -> float | None:
    """EWM of the player's minutes over his last `n` prior played games; None below MIN_GAMES."""
    mins = [_num(r, "minutes") for r in _played(prior_rows(rows, as_of))[-n:]]
    mins = [m for m in mins if m == m]
    return ewm(mins, halflife) if len(mins) >= MIN_GAMES else None


def rest_days(rows: list, day: str | None) -> int | None:
    """Days off between the newest row in `rows` (the caller passes rows already restricted to PRIOR games) and `day`,
    both in the rows' own date system, floored at 0 (0 = back-to-back). None if unknown. The cockpit master labels a game
    with the UTC date of its tip, so a back-to-back can read as a 0- or 1-day gap and a 2-day gap can hide one; the
    result is only used for a small fatigue trim, and the error direction is to miss it, never to invent one."""
    if day is None or not rows:
        return None
    last = _day(rows[-1])
    try:
        return max((date.fromisoformat(day) - date.fromisoformat(last)).days - 1, 0)
    except (TypeError, ValueError):
        return None


def pf_per36(rows: list, as_of: str | None = None, n: int = 20) -> float | None:
    sel = [r for r in _played(prior_rows(rows, as_of))[-n:] if _num(r, "fouls") == _num(r, "fouls")]
    mins = sum(_num(r, "minutes") for r in sel)
    if len(sel) < MIN_GAMES or mins <= 0:
        return None
    return 36.0 * sum(_num(r, "fouls") for r in sel) / mins


# ------------------------------------------------------------------------------------ 1. role-weighted redistribution
def _l10_means(rows: list, as_of: str | None) -> dict | None:
    games = _played(prior_rows(rows, as_of))[-10:]
    if len(games) < 3:
        return None
    out = {}
    for stat in STATS:
        vals = [v for v in (_stat_value(r, stat) for r in games) if v == v]
        out[stat] = float(np.mean(vals)) if vals else 0.0
    out["_dates"] = [d for d in (_day(r) for r in games) if d is not None]
    return out


def _fresh_fraction(active_dates: list, absent_rows: list, as_of: str | None) -> float:
    """Share of the active player's recent game days on which the absent teammate PLAYED. A teammate who has already been
    missing for weeks is baked into the active player's recent numbers (his EWM has adapted), so counting his absence again
    would double count; only the part of the absence that is NEW relative to the baseline window earns a boost. 1.0 when the
    rows carry no dates (cannot tell)."""
    if not active_dates:
        return 1.0
    played = {d for d in (_day(r) for r in prior_rows(absent_rows, as_of)[-60:]) if d is not None}
    if not played:
        return 1.0
    return sum(1 for d in active_dates if d in played) / len(active_dates)


def compute_vacated_shares(team_rows: dict, absences: dict, as_of: str | None = None,
                           min_mpg: float = ROTATION_MIN_MPG, eta_scale: float = 1.0, eta_off: tuple = ()) -> dict:
    """Stat-specific vacated-production shares and each active player's resulting multipliers.

    team_rows : {player_norm: chronological rows} for the players of ONE team
    absences  : {player_norm: P(absent tonight)} for that team (the injury report's weights)
    -> {"vacated": {stat: V}, "boost": {player_norm: {stat: multiplier >= 1}}, "rotation": [player_norm, ...]}

    V_stat = sum_j w_j * Xbar_j / sum_k Xbar_k over the L10 means of the rotation (min >= min_mpg). Active player i then
    gains  eta_stat * (sum_j w_j * f_ij * Xbar_j) * a_i / sum_a a   in that stat, f_ij = the share of i's last 10 games
    that j played in (an absence that has lasted for weeks is already in i's numbers and earns no boost), where a_i = (1 - w_i) * minutes_i * rate_i^3 and
    rate_i = Xbar_i / minutes_i. Only players already in the rotation absorb anything, and a one-stat boost is capped at
    MAX_ROLE_BOOST (vacated production is therefore NOT conserved when one player would otherwise take all of it).
    `eta_scale` multiplies every elasticity and `eta_off` zeroes the named stats (nba/props/engine_eval.py tunes both: the
    specification's elasticities were measured to be several times too strong)."""
    rot = {}
    for pn, rows in team_rows.items():
        m = _l10_means(rows, as_of)
        if m is not None and m["minutes"] >= min_mpg:
            rot[pn] = m
    result = {"vacated": {s: 0.0 for s in STATS}, "boost": {}, "rotation": sorted(rot)}
    absent = {}
    for pn, w in (absences or {}).items():
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if pn in rot and w > 0:
            absent[pn] = min(w, 1.0)
    if not absent:
        return result
    boost = {pn: {} for pn in rot}
    fresh = {(pn, j): _fresh_fraction(rot[pn]["_dates"], team_rows[j], as_of) for pn in rot for j in absent if j != pn}
    for stat in STATS:
        total = sum(m[stat] for m in rot.values())
        vacated = sum(w * rot[pn][stat] for pn, w in absent.items())
        result["vacated"][stat] = vacated / total if total > 0 else 0.0
        affinity = {}
        for pn, m in rot.items():
            w_abs = absent.get(pn, 0.0)
            if w_abs >= ACTIVE_MAX_ABSENCE:
                continue
            mins = m["minutes"]
            rate = m[stat] / mins if mins > 0 else 0.0
            affinity[pn] = (1.0 - w_abs) * mins * rate ** AFFINITY_POWER
        tot_aff = sum(affinity.values())
        for pn, a in affinity.items():
            x = rot[pn][stat]
            # only the NEW part of each absence counts for this player (see _fresh_fraction)
            vac_i = sum(w * rot[j][stat] * fresh[(pn, j)] for j, w in absent.items() if j != pn)
            if x <= 0 or tot_aff <= 0 or vac_i <= 0:
                b = 1.0
            else:
                gain = (0.0 if stat in eta_off else ETA[stat] * eta_scale) * vac_i * (a / tot_aff)
                if stat == "minutes":
                    gain = min(gain, MAX_MINUTES_GAIN)
                b = min(1.0 + gain / x, MAX_ROLE_BOOST)
            boost[pn][stat] = max(b, 1.0)
    result["boost"] = boost
    return result


# ------------------------------------------------------------------------------------------ 2. minutes distribution
def _minutes_var(mean: float, spread: float | None, starter: bool, pf36: float | None) -> float:
    """Variance of the four-regime (regulation / blowout / foul trouble / overtime) minutes mixture around its own (recentred) mean: the regimes' own sd^2 (weighted) plus
    the spread of the blowout / foul-trouble offsets. The mixture mean is NOT used -- the caller fixes the mean (the history already holds
    typical foul trouble and typical blowouts, so the mixture only supplies the extra left-tail variance)."""
    w_blow = 0.0 if spread is None else float(norm.cdf((spread - BLOWOUT_CENTER) / WEIGHT_SCALE))
    scale = 1.0 if pf36 is None else min(max(pf36 / PF36_REF, 0.6), 1.8)
    w_foul = FOUL_WEIGHT * scale
    w_ot = OT_WEIGHT_REF if spread is None else min(max(
        OT_WEIGHT_REF * math.exp(-(spread - OT_SPREAD_REF) / OT_SPREAD_DECAY), OT_WEIGHT_CLIP[0]), OT_WEIGHT_CLIP[1])
    w_reg = max(1.0 - w_blow - w_foul - w_ot, 0.2)
    tot = w_reg + w_blow + w_foul + w_ot
    w_reg, w_blow, w_foul, w_ot = w_reg / tot, w_blow / tot, w_foul / tot, w_ot / tot
    if starter:
        o_blow = -max(mean - STARTER_BLOWOUT_CAP, 4.0)
    else:
        o_blow = min(BLOWOUT_BENCH_GAIN, max(MAX_MEAN_MIN - mean, 0.0))
    o_foul = -min(mean, -FOUL_SHIFT)
    o_ot = OT_MINUTES * min(mean / 36.0, 1.0)
    offs = np.array([0.0, o_blow, o_foul, o_ot])
    ws = np.array([w_reg, w_blow, w_foul, w_ot])
    sds = mean * np.array([BASE_MIN_CV, BLOWOUT_MIN_CV, BASE_MIN_CV, BASE_MIN_CV])
    centre = float(np.sum(ws * offs))
    return float(np.sum(ws * sds ** 2) + np.sum(ws * (offs - centre) ** 2))


def project_minutes_distribution(mu_min_0: float, spread: float | None = None, rest: int | None = None,
                                 pf36: float | None = None) -> dict:
    """-> {"mean", "sd", "factor", "extra_rel_var", "w_blowout", "delta"}.

    mean   : mu_min_0 trimmed for blowout risk (starters, `|spread|` known) and back-to-back fatigue (rest == 0 and
             mu_min_0 >= 32). With no spread and no back-to-back the mean is mu_min_0 exactly.
    factor : mean / mu_min_0 -- the engine multiplies a stat's projection by this.
    extra_rel_var : added RELATIVE variance (var += extra_rel_var * mu^2) on top of the history's own spread: the mixture's
             relative variance minus the same mixture computed with no spread information. Zero when nothing is known."""
    mu0 = float(mu_min_0)
    if not math.isfinite(mu0) or mu0 <= 0:
        return {"mean": 0.0, "sd": 0.0, "factor": 1.0, "extra_rel_var": 0.0, "w_blowout": 0.0, "delta": 0.0}
    s = None if spread is None or not math.isfinite(float(spread)) else abs(float(spread))
    starter = mu0 >= STARTER_MIN
    delta = 0.0
    if s is not None and starter:
        delta += BLOWOUT_TRIM * float(norm.cdf((s - BLOWOUT_CENTER) / TRIM_SCALE))
    if rest == 0 and mu0 >= B2B_MIN_MPG:
        delta += B2B_TRIM
    mean = min(max(mu0 + delta, 0.0), MAX_MEAN_MIN)
    if mean <= 0:
        return {"mean": 0.0, "sd": 0.0, "factor": 0.0, "extra_rel_var": 0.0, "w_blowout": 0.0, "delta": delta}
    var = _minutes_var(mean, s, starter, pf36)
    var_base = _minutes_var(mu0, None, starter, pf36)
    extra = max(var / mean ** 2 - var_base / mu0 ** 2, 0.0)
    w_blow = 0.0 if s is None else float(norm.cdf((s - BLOWOUT_CENTER) / WEIGHT_SCALE))
    return {"mean": mean, "sd": math.sqrt(var), "factor": mean / mu0, "extra_rel_var": extra, "w_blowout": w_blow,
            "delta": delta}


# ---------------------------------------------------------------------------------------- 3. threes and points shape
def shrunk_three_pct(rows: list, as_of: str | None = None, window: int = EB_WINDOW, k: float = EB_K_3P,
                     prior: float = LEAGUE_3P_PCT) -> tuple[float, float] | None:
    """Empirical-Bayes 3P%: (sum FG3M + k*prior) / (sum FG3A + k) over the last `window` prior games. -> (p3, attempts) or
    None when the rows carry no attempts/makes. A 12-for-20 week moves it a little; it cannot make a 60% shooter."""
    made = att = 0.0
    n = 0
    for r in prior_rows(rows, as_of)[-window:]:
        a, m = _num(r, "three_points_attempted"), _num(r, "three_points_made")
        if a == a and m == m:
            att += a
            made += m
            n += 1
    if n < MIN_GAMES:
        return None
    return (made + k * prior) / (att + k), att


def project_nba_threes(rows: list, as_of: str | None = None, halflife: float = THREE_HALFLIFE) -> dict | None:
    """Attempt-first threes: mu_3PM = EWM(FG3A) * shrunk 3P%. -> {"mu_3pa", "p3", "mu_3pm", "attempts"} or None when the
    rows carry no attempts. Minutes, role and opponent adjustments are the caller's multipliers on mu_3pa."""
    prior = prior_rows(rows, as_of)[-EB_WINDOW:]
    att = [_num(r, "three_points_attempted") for r in prior]
    att = [a for a in att if a == a]
    if len(att) < MIN_GAMES:
        return None
    shrunk = shrunk_three_pct(rows, as_of)
    if shrunk is None:
        return None
    mu_3pa = ewm(att, halflife)
    return {"mu_3pa": mu_3pa, "p3": shrunk[0], "mu_3pm": mu_3pa * shrunk[0], "attempts": shrunk[1]}


def shot_diet_sd_scale(rows: list, as_of: str | None = None, n: int = SHOT_DIET_WINDOW) -> float:
    """Multiplier on the points standard deviation from the player's scoring mix: three-point reliance widens it, free-
    throw reliance narrows it. 1.0 when the rows lack three_points_made / free_throws_made or have < MIN_GAMES games."""
    pts = three = ft = 0.0
    games = 0
    for r in prior_rows(rows, as_of)[-n:]:
        p, t, f = _num(r, "points"), _num(r, "three_points_made"), _num(r, "free_throws_made")
        if p == p and t == t and f == f:
            pts += p
            three += t
            ft += f
            games += 1
    if games < MIN_GAMES:
        return 1.0
    denom = max(pts, 1.0)
    scale = 1.0 + SHOT_DIET_3_COEF * (3.0 * three / denom - SHOT_DIET_3_REF) \
        - SHOT_DIET_FT_COEF * (ft / denom - SHOT_DIET_FT_REF)
    return float(min(max(scale, SHOT_DIET_CLIP[0]), SHOT_DIET_CLIP[1]))


# ---------------------------------------------------------------------------------------------- 4. coherent combos
def within_player_corr(rows: list, as_of: str | None = None, n: int = CORR_WINDOW, shrink: float = CORR_SHRINK,
                       min_games: int = CORR_MIN_GAMES) -> np.ndarray:
    """3x3 correlation of (points, rebounds, assists) over the player's last `n` prior games, shrunk `shrink` of the way to
    the league prior. Fewer than `min_games` usable games, or a constant column, falls back to the prior for that entry.
    Always positive definite (else the prior)."""
    data = []
    for r in prior_rows(rows, as_of)[-n:]:
        v = [_num(r, c) for c in CORR_ORDER]
        if all(x == x for x in v):
            data.append(v)
    if len(data) < min_games:
        return CORR_PRIOR.copy()
    x = np.asarray(data, dtype=float)
    sd = x.std(axis=0, ddof=1)
    sample = CORR_PRIOR.copy()
    with np.errstate(all="ignore"):
        c = np.corrcoef(x, rowvar=False)
    for i in range(3):
        for j in range(3):
            if i != j and sd[i] > 1e-9 and sd[j] > 1e-9 and math.isfinite(c[i, j]):
                sample[i, j] = c[i, j]
    out = (1.0 - shrink) * sample + shrink * CORR_PRIOR
    np.fill_diagonal(out, 1.0)
    if float(np.linalg.eigvalsh(out).min()) < 1e-6:
        return CORR_PRIOR.copy()
    return out


def combo_moments(components: dict, corr: np.ndarray, members: tuple) -> tuple[float, float]:
    """(mu_C, sigma_C) of a combo from its components' (mu, sd): mu_C = sum mu_k exactly,
    var_C = sum sd_k^2 + 2 sum_{j<k} R[j,k] sd_j sd_k."""
    mu = sum(float(components[k][0]) for k in members)
    var = sum(float(components[k][1]) ** 2 for k in members)
    for a in range(len(members)):
        for b in range(a + 1, len(members)):
            i, j = CORR_ORDER.index(members[a]), CORR_ORDER.index(members[b])
            var += 2.0 * float(corr[i, j]) * float(components[members[a]][1]) * float(components[members[b]][1])
    return mu, math.sqrt(max(var, 0.0))


def normal_p_over(mu: float, sd: float, line: float) -> float | None:
    if mu is None or line is None or not math.isfinite(mu):
        return None
    sd = max(float(sd), 1e-6)
    return float(1.0 - norm.cdf((float(line) - mu) / sd))


def normal_range(mu: float, sd: float, lo: float = 0.10, hi: float = 0.90) -> tuple[float, float]:
    sd = max(float(sd), 1e-6)
    return float(max(norm.ppf(lo, mu, sd), 0.0)), float(norm.ppf(hi, mu, sd))
