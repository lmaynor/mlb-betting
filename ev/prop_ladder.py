"""
ev.prop_ladder -- fit ONE player's whole ladder of book lines (main line AND alternates) with a parametric distribution, so a Kalshi rung
the books never quoted can be priced, including a little past the lines they did quote.

Why: Kalshi lists every N+ rung for every player; books quote one main line plus a few alternates. ev.odds_board.fit_curve interpolates between
quoted rungs (kept, and tried first) but extrapolates only along its end segment. A ladder fit uses every quoted rung at once (an alternate
line is just another point on the same distribution), weights it by how many books stand behind it, and extends in a way that respects the
shape of the stat:
  normal  yardage / points-type stats      P(X > x) = 1 - Phi((x - mu) / sd)         fit by weighted least squares on the probit scale
  count   receptions, rebounds, assists,   X ~ NegBin(mean m, dispersion r), P(X > x) = P(X >= ceil(x))
          threes, goals, TDs, ...          r picked from a small grid by weighted least squares on the logit scale (Poisson = large r)

Fail closed: a fit whose residual is large returns None; the fit refuses to speak further than MAX_SD model standard deviations from the
nearest quoted rung (`distance`), and callers inflate dispersion with that distance so the pricer's own dispersion gate bites. A lone anchor is
used only when >= SINGLE_MIN_BOOKS books stand behind it and a variance prior exists (UNVALIDATED: the CV table below is rough).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

MAX_SD = 1.5                 # never extrapolate (or interpolate across a gap) more than this many model sds from a quoted rung
MAX_RESID = 0.35             # weighted RMS logit residual above which the family does not describe the ladder
SINGLE_MIN_BOOKS = 3
SPAN_FRAC = 0.6              # anchors spanning less than this fraction of the prior sd cannot identify the spread
DISP_PER_SD = 0.02           # added to a leg's dispersion per model sd of distance from the nearest quoted rung
P_LO, P_HI = 0.01, 0.99
R_GRID = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 20.0, 50.0, 1e6)
SINGLE_R = 8.0               # dispersion prior for a lone count anchor

_NORMAL = {
    "nfl": {"pass_yds", "rush_yds", "rec_yds", "pass_attempts", "pass_completions", "rush_attempts"},
    "nba": {"points", "pra", "pr", "pa", "ra", "minutes"},
    "nhl": {"saves"},
}
# coefficient of variation of the stat around its line, used ONLY for a lone anchor (UNVALIDATED rough values)
_CV = {("nfl", "pass_yds"): 0.22, ("nfl", "rush_yds"): 0.65, ("nfl", "rec_yds"): 0.55, ("nfl", "pass_attempts"): 0.18,
       ("nfl", "pass_completions"): 0.22, ("nfl", "rush_attempts"): 0.35, ("nba", "points"): 0.33, ("nba", "pra"): 0.28,
       ("nba", "pr"): 0.30, ("nba", "pa"): 0.30, ("nba", "ra"): 0.32, ("nba", "minutes"): 0.15, ("nhl", "saves"): 0.20}
_ND = NormalDist()


def family(sport: str, stat: str) -> str:
    return "normal" if stat in _NORMAL.get(sport, ()) else "count"


def _logit(p: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1.0 - p))


def _nb_sf(n: int, m: float, r: float) -> float:
    """P(X >= n) for X ~ NegBin(mean m, dispersion r); Poisson when r is huge."""
    from scipy import stats
    if n <= 0:
        return 1.0
    if r >= 1e5:
        return float(stats.poisson.sf(n - 1, m))
    return float(stats.nbinom.sf(n - 1, r, r / (r + m)))


@dataclass(frozen=True)
class LadderFit:
    fam: str
    mu: float                # normal: mean; count: mean m
    scale: float             # normal: sd; count: dispersion r
    unit: float              # one model sd in the stat's own units (normal: sd; count: sqrt(m + m^2/r))
    lo: float                # lowest / highest quoted line behind the fit
    hi: float
    rmse: float              # weighted RMS logit residual over the anchors
    n_anchors: int

    def p_over(self, x: float) -> float:
        if self.fam == "normal":
            p = 1.0 - _ND.cdf((x - self.mu) / self.scale)
        else:
            p = _nb_sf(int(math.floor(x)) + 1, self.mu, self.scale)         # over x  <=>  X >= floor(x) + 1  (x = N - 0.5)
        return round(min(max(p, P_LO), P_HI), 4)

    def distance(self, x: float) -> float:
        """Model sds from the nearest quoted rung (0 inside the quoted range)."""
        gap = self.lo - x if x < self.lo else (x - self.hi if x > self.hi else 0.0)
        return gap / self.unit if self.unit > 0 else math.inf

    def speaks(self, x: float) -> bool:
        return self.distance(x) <= MAX_SD


def _wls_line(xs, ys, ws):
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if sxx <= 1e-12:
        return None
    a = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / sxx
    return a, my - a * mx


def _resid(pred, anchors, ws) -> float:
    sw = sum(ws)
    return math.sqrt(sum(w * (_logit(pred(x)) - _logit(p)) ** 2 for w, (x, p, _) in zip(ws, anchors)) / sw)


_M_GRID = [0.05 * (2400.0 ** (i / 79.0)) for i in range(80)]            # 0.05 .. 120 geometrically


def _fit_mean(a, ws, r: float):
    """Best NegBin mean for fixed r. The clipped logit makes the SSE flat far from the data, so scan a log grid first (a bounded
    scalar search started in a plateau runs to the bound) and refine inside the winning bracket. Vectorised over the grid: one
    scipy call per anchor instead of one per (anchor, grid point)."""
    import numpy as np
    from scipy import stats
    from scipy.optimize import minimize_scalar
    grid = np.array(_M_GRID)
    n_arr = [int(math.floor(x)) for x, _, _ in a]
    tgt = [_logit(p) for _, p, _ in a]

    def sf_vec(n, m):
        if r >= 1e5:
            return stats.poisson.sf(n, m)
        return stats.nbinom.sf(n, r, r / (r + m))
    lg = lambda v: np.log(np.clip(v, 0.005, 0.995) / (1.0 - np.clip(v, 0.005, 0.995)))
    sse_grid = sum(w * (lg(sf_vec(n, grid)) - t) ** 2 for w, n, t in zip(ws, n_arr, tgt))
    i = int(np.argmin(sse_grid))

    def sse(m):
        return float(sum(w * (_logit(_nb_sf(n + 1, m, r)) - t) ** 2 for w, n, t in zip(ws, n_arr, tgt)))
    res = minimize_scalar(sse, bounds=(_M_GRID[max(i - 1, 0)], _M_GRID[min(i + 1, len(_M_GRID) - 1)]), method="bounded")
    return (float(res.fun), float(res.x)) if res.fun <= float(sse_grid[i]) else (float(sse_grid[i]), _M_GRID[i])


def fit_ladder(anchors, stat: str, sport: str) -> LadderFit | None:
    """anchors = [(point, de-vigged P(over), n_two_sided_books)] for ONE player and stat, EVERY quoted line (main + alternates).
    -> LadderFit, or None when there is nothing trustworthy to fit."""
    pts = {}
    for x, p, n in anchors:
        if not (0.01 < float(p) < 0.99):
            continue                                    # a 1-2% price carries no shape information (and is mostly vig)
        if x not in pts or n > pts[x][2]:
            pts[x] = (float(x), float(p), int(n))
    a = sorted(pts.values())
    if not a:
        return None
    ws = [float(min(n, 6)) for _, _, n in a]
    lo, hi = a[0][0], a[-1][0]
    fam = family(sport, stat)
    if fam == "normal":
        if len(a) >= 2:
            line = _wls_line([x for x, _, _ in a], [_ND.inv_cdf(1.0 - p) for _, p, _ in a], ws)
            if line is None:
                return None
            sd, mu = (1.0 / line[0], -line[1] / line[0]) if line[0] > 1e-6 else (None, None)
            prior = _CV.get((sport, stat))
            if prior is not None:
                psd = prior * max(sum(w * x for w, (x, _, _) in zip(ws, a)) / sum(ws), 1.0)
                # Alternates often sit within a few units of the main line (Stafford 263.5-271.5): the slope across a span that narrow is
                # noise. When the span is small against the prior sd, or the fitted sd is implausible, keep the prior sd and fit only the mean.
                if sd is None or (hi - lo) < SPAN_FRAC * psd or not (0.5 * psd <= sd <= 2.0 * psd):
                    sd = psd
                    mu = sum(w * (x + sd * _ND.inv_cdf(p)) for w, (x, p, _) in zip(ws, a)) / sum(ws)
            elif sd is None:
                return None                              # flat or increasing and no variance prior: not a distribution of this shape
        else:
            x, p, n = a[0]
            cv = _CV.get((sport, stat))
            if cv is None or n < SINGLE_MIN_BOOKS:
                return None
            sd = cv * max(x, 1.0)
            mu = x + sd * _ND.inv_cdf(p)
        fit = LadderFit("normal", mu, sd, sd, lo, hi, 0.0, len(a))
    else:
        best = None
        grid = R_GRID if len(a) >= 2 else (SINGLE_R,)
        if len(a) == 1 and a[0][2] < SINGLE_MIN_BOOKS:
            return None
        for r in grid:
            f, m = _fit_mean(a, ws, r)
            if best is None or f < best[0] - 1e-9:
                best = (f, m, r)
        _, m, r = best
        m = float(m)
        fit = LadderFit("count", m, r, math.sqrt(m + m * m / r), lo, hi, 0.0, len(a))
    rmse = _resid(fit.p_over, a, ws)
    if rmse > MAX_RESID:
        return None
    return LadderFit(fit.fam, fit.mu, fit.scale, fit.unit, fit.lo, fit.hi, round(rmse, 4), fit.n_anchors)
