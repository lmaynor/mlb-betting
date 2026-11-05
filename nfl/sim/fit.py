"""Walk-forward estimation of the drive simulator's engine parameters from play-by-play.

    params = fit_params(prep(load_pbp(range(2018, S))))         # seasons STRICTLY BEFORE the evaluated season S

Everything is maximum likelihood (logistic / softmax by L-BFGS with an L2 ridge) or empirical (resampling pools with
hierarchical shrinkage of cell frequencies toward their parents):

  call model      P(pass | down, distance, field position, 9 clock states x 9 score states)       logistic MLE
  4th-down model  P(go / field goal / punt | distance, field position, score, clock)              softmax MLE
  field goal      P(make | kick distance)                                                          logistic MLE
  pass outcome    P(sack), P(interception), P(completion) per (zone, distance, script) cell      shrunk frequencies
  yards           completion-yards, sack-yards and rush-yards pools per cell                       empirical resampling
  clock           snap-to-snap seconds per (outcome, hurry class), end-of-drive gaps               empirical pools
  field position  next-drive start after punts / turnovers / downs / missed FGs / kickoffs         empirical pools

No coefficient here is hand-set; nothing reads a season >= the evaluated one (the caller chooses the seasons, and
`tests/test_nfl_sim.py` pins that perturbing later data does not move the fit).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from nfl.sim import pbp_prep as PP

MIN_CELL = 150                    # a pool / frequency cell with fewer observations borrows from its parent level
SHRINK_M = 60.0                   # pseudo-observations pulling a cell frequency toward its parent
FG_RANGE_YL = 55                  # a field goal is only offered within this yardline_100 (kick distance yl + 17 <= 72)


# -- tiny numerical helpers ------------------------------------------------------------------------------------------
def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0) -> np.ndarray:
    """Ridge logistic regression by L-BFGS; the first column (intercept) is not penalised."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    pen = np.ones(X.shape[1]) * l2
    pen[0] = 0.0

    def f(w):
        z = X @ w
        ll = np.sum(np.logaddexp(0.0, z) - y * z)
        p = 1.0 / (1.0 + np.exp(-z))
        return ll + 0.5 * np.sum(pen * w * w), X.T @ (p - y) + pen * w
    w0 = np.zeros(X.shape[1])
    r = minimize(f, w0, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return r.x


def fit_softmax(X: np.ndarray, y: np.ndarray, k: int, l2: float = 1.0) -> np.ndarray:
    """Ridge multinomial logit (class 0 is the reference); returns W of shape (k - 1, n_features)."""
    X = np.asarray(X, dtype=float)
    n, d = X.shape
    Y = np.zeros((n, k))
    Y[np.arange(n), np.asarray(y, dtype=int)] = 1.0
    pen = np.ones(d) * l2
    pen[0] = 0.0

    def unpack(w):
        return np.vstack([np.zeros(d), w.reshape(k - 1, d)])

    def f(w):
        W = unpack(w)
        Z = X @ W.T
        Z -= Z.max(1, keepdims=True)
        E = np.exp(Z)
        P = E / E.sum(1, keepdims=True)
        ll = -np.sum(np.log(P[np.arange(n), np.asarray(y, dtype=int)] + 1e-12))
        G = (P - Y).T @ X + W * pen[None, :]
        return ll + 0.5 * np.sum(pen[None, :] * W * W), G[1:].ravel()
    r = minimize(f, np.zeros((k - 1) * d), jac=True, method="L-BFGS-B", options={"maxiter": 400})
    return unpack(r.x)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


# -- feature maps ----------------------------------------------------------------------------------------------------
N_BASE_CALL = 13


def call_design(down, ytg, yl, tb=None, sb=None) -> np.ndarray:
    """Design matrix of the play-call model. With (tb, sb) it carries the 81-cell clock x score state one-hot; without it
    it is the 'neutral situation' model (the script-free expected pass rate)."""
    down = np.asarray(down, dtype=float)
    ytg = np.asarray(ytg, dtype=float)
    yl = np.asarray(yl, dtype=float)
    n = len(down)
    ly = np.log(np.clip(ytg, 1, 30))
    cols = [np.ones(n)] + [(down == d).astype(float) for d in (2, 3, 4)] + [ly] + [ly * (down == d) for d in (2, 3, 4)] + [
        (ytg >= 8).astype(float), (yl <= 20).astype(float), (yl <= 5).astype(float), (yl >= 90).astype(float),
        np.clip(yl, 1, 99) / 100.0]
    X = np.column_stack(cols)
    if tb is None:
        return X
    cell = np.asarray(tb, dtype=int) * PP.N_SB + np.asarray(sb, dtype=int)
    S = np.zeros((n, PP.N_TB * PP.N_SB))
    S[np.arange(n), cell] = 1.0
    return np.hstack([X, S])


def fourth_design(ytg, yl, sd, tb) -> np.ndarray:
    ytg = np.asarray(ytg, dtype=float)
    yl = np.asarray(yl, dtype=float)
    sd = np.asarray(sd, dtype=float)
    tb = np.asarray(tb, dtype=int)
    n = len(ytg)
    late = (tb >= 6).astype(float)
    sdc = np.clip(sd, -24, 24) / 8.0
    cols = [np.ones(n), np.log(np.clip(ytg, 1, 30)), (ytg >= 5).astype(float), yl / 10.0, (yl <= 35).astype(float),
            (yl <= 20).astype(float), (yl <= 35) * np.log(np.clip(ytg, 1, 30)), sdc, (sd < 0).astype(float),
            (sd < 0) * late, (sd > 0) * late, (sd <= -9) * late, (sd >= 9) * late, (sd == 0).astype(float)]
    D = np.column_stack(cols).astype(float)
    T = np.zeros((n, PP.N_TB - 1))
    m = tb > 0
    T[np.where(m)[0], tb[m] - 1] = 1.0
    return np.hstack([D, T])


def fg_design(kick_distance) -> np.ndarray:
    k = (np.asarray(kick_distance, dtype=float) - 40.0) / 10.0
    return np.column_stack([np.ones(len(k)), k, k * k])


# -- pools -----------------------------------------------------------------------------------------------------------
@dataclass
class Pool:
    """Flattened per-cell empirical samples. `draw(cell, u)` resamples uniformly within the cell."""
    values: np.ndarray
    start: np.ndarray
    length: np.ndarray

    def draw(self, cell: np.ndarray, u: np.ndarray) -> np.ndarray:
        idx = self.start[cell] + np.minimum((u * self.length[cell]).astype(np.int64), self.length[cell] - 1)
        return self.values[idx]

    def mean(self, cell: int) -> float:
        s, n = int(self.start[cell]), int(self.length[cell])
        return float(self.values[s:s + n].mean())


def _cell_levels_play(c: np.ndarray) -> list:
    """(zone*3 + ytg_bucket)*3 + script -> [fine, (zone,ytg), zone, global]."""
    c = np.asarray(c, dtype=int)
    return [c, c // 3, c // 9, np.zeros_like(c)]


def play_cell(yl, ytg, script) -> np.ndarray:
    return (PP.zone_of(yl) * 3 + PP.ytg_bucket(ytg)) * 3 + np.asarray(script, dtype=int)


N_PLAY_CELL = PP.N_ZONE * 9


def build_pool(values: np.ndarray, levels: list, n_fine: int, min_n: int = MIN_CELL, parent_of=None) -> Pool:
    """levels[L] = per-observation cell id at level L (0 = finest). For each fine cell, the finest level with >= min_n
    observations supplies the samples."""
    values = np.asarray(values, dtype=np.float32)
    idx_by_level = []
    for lv in levels:
        order = np.argsort(lv, kind="stable")
        sl = lv[order]
        ids, first, cnt = np.unique(sl, return_index=True, return_counts=True)
        idx_by_level.append({int(i): order[f:f + c] for i, f, c in zip(ids, first, cnt)})
    pieces, start, length = [], np.zeros(n_fine, dtype=np.int64), np.zeros(n_fine, dtype=np.int64)
    pos = 0
    for c in range(n_fine):
        chosen = None
        for L, idx in enumerate(idx_by_level):
            cell = parent_of(c, L) if parent_of else c
            ix = idx.get(int(cell))
            if ix is not None and len(ix) >= min_n:
                chosen = ix
                break
        if chosen is None:
            chosen = np.arange(len(values))
        pieces.append(values[chosen])
        start[c], length[c] = pos, len(chosen)
        pos += len(chosen)
    return Pool(np.concatenate(pieces), start, length)


@dataclass
class KMPool:
    """Right-censored yardage distribution per cell (Kaplan-Meier). A touchdown play's yards equal the distance to the goal
    line, i.e. the play was CAPPED there: its raw gain is only known to be >= that. Treating those yards as exact (a plain
    resampling pool) understates touchdowns at every field position other than the one a pool happens to average over, so
    the pool stores the uncapped distribution and the simulator caps at the CURRENT yardline. The mass beyond the largest
    uncensored gain sits at 100 (always a touchdown)."""
    values: np.ndarray
    key: np.ndarray            # cell + cumulative probability within the cell, ascending over the whole array
    n: np.ndarray

    def draw(self, cell: np.ndarray, u: np.ndarray) -> np.ndarray:
        i = np.searchsorted(self.key, np.asarray(cell, dtype=float) + np.clip(u, 0.0, 1.0 - 1e-9), side="right")
        return self.values[np.minimum(i, len(self.values) - 1)]


def _km_cell(v: np.ndarray, cens: np.ndarray) -> tuple:
    order = np.lexsort((cens, v))                      # events before censored at equal values
    v, cens = v[order], cens[order]
    uniq, first, cnt = np.unique(v, return_index=True, return_counts=True)
    n_total = len(v)
    vals, probs, surv = [], [], 1.0
    for u_, f, c in zip(uniq, first, cnt):
        at_risk = n_total - f
        d = int(np.sum(~cens[f:f + c]))
        if d > 0:
            jump = surv * d / at_risk
            vals.append(u_)
            probs.append(jump)
            surv -= jump
    vals.append(100.0)
    probs.append(max(surv, 0.0))
    probs = np.asarray(probs)
    return np.asarray(vals, dtype=np.float32), probs / probs.sum()


def build_km_pool(values: np.ndarray, censored: np.ndarray, levels: list, n_fine: int, min_n: int = MIN_CELL, parent_of=None) -> KMPool:
    values = np.asarray(values, dtype=float)
    censored = np.asarray(censored, dtype=bool)
    idx_by_level = []
    for lv in levels:
        order = np.argsort(lv, kind="stable")
        sl = lv[order]
        ids, first, cnt = np.unique(sl, return_index=True, return_counts=True)
        idx_by_level.append({int(i): order[f:f + c] for i, f, c in zip(ids, first, cnt)})
    vs, ks, ns = [], [], np.zeros(n_fine, dtype=np.int64)
    for c in range(n_fine):
        chosen = None
        for L, idx in enumerate(idx_by_level):
            cell = parent_of(c, L) if parent_of else c
            ix = idx.get(int(cell))
            if ix is not None and len(ix) >= min_n:
                chosen = ix
                break
        if chosen is None:
            chosen = np.arange(len(values))
        v, p = _km_cell(values[chosen], censored[chosen])
        vs.append(v)
        ks.append(c + np.cumsum(p) * (1.0 - 1e-12))
        ns[c] = len(chosen)
    return KMPool(np.concatenate(vs), np.concatenate(ks), ns)


def shrunk_rate(flag: np.ndarray, levels: list, n_fine: int, m: float = SHRINK_M, parent_of=None) -> np.ndarray:
    """Cell frequencies of a 0/1 flag with each level shrunk toward its parent (global at the root)."""
    flag = np.asarray(flag, dtype=float)
    rates = [None] * len(levels)
    for L in range(len(levels) - 1, -1, -1):
        lv = levels[L]
        nc = int(lv.max()) + 1
        s = np.bincount(lv, weights=flag, minlength=nc)
        n = np.bincount(lv, minlength=nc).astype(float)
        if L == len(levels) - 1:
            rates[L] = (s + m * flag.mean()) / (n + m)
        else:
            # parent cell of each level-L cell: take it from any observation in that cell
            par = np.zeros(nc, dtype=int)
            par[lv] = levels[L + 1]
            pr = rates[L + 1]
            par_rate = pr[np.clip(par, 0, len(pr) - 1)]
            rates[L] = (s + m * par_rate) / (n + m)
    return np.array([rates[0][c] if c < len(rates[0]) else flag.mean() for c in range(n_fine)])


# -- the parameter bundle ----------------------------------------------------------------------------------------------
@dataclass
class SimParams:
    train_seasons: tuple
    call_w: np.ndarray                  # play-call logistic weights (with the 81-cell state)
    neutral_w: np.ndarray               # same model without clock / score state
    fourth_w: np.ndarray                # (2, d) softmax: field goal, punt vs go
    fg_w: np.ndarray
    pass_probs: np.ndarray              # (54, 3): P(sack), P(int | throw), P(complete | throw)
    p_target: float                     # P(a receiver is targeted | throw)
    cmp_pool: KMPool                    # (uncapped) yards on completions
    sack_pool: Pool
    rush_pool: KMPool
    fumb: dict                          # P(fumble lost) by play kind, P(defensive TD | int / fumble)
    el_pool: Pool                       # snap-to-snap seconds: cell = kind(4)*3 + hurry
    gap_pool: Pool                      # end-of-drive gap seconds: cell = etype(6)*3 + hurry
    kick_pool: Pool                     # next-drive start after a score / end of half (single cell)
    start_pool: Pool                    # next-drive start after punt / turnover / downs / missed FG: cell = (etype-2)*10 + bucket
    td_extra: dict
    league: dict = field(default_factory=dict)      # league-average rates the ratings are measured against
    n_plays: int = 0
    pace_scale: float = 1.0            # global clock multiplier set by evaluate.calibrate_pace on the last TRAINING season


def _kind(is_pass, sack, complete) -> np.ndarray:
    return np.where(np.asarray(is_pass) == 0, 0, np.where(np.asarray(sack) == 1, 3, np.where(np.asarray(complete) == 1, 1, 2)))


def fit_params(t: "PP.PbpTables", seasons: Optional[tuple] = None, l2: float = 2.0) -> SimParams:
    pl = t.plays
    # ---- play-call model --------------------------------------------------------------------------------------------
    X = call_design(pl["down"], pl["ytg"], pl["yl"], pl["tb"], pl["sb"])
    y = pl["is_pass"].to_numpy(float)
    call_w = fit_logistic(X, y, l2)
    neutral_w = fit_logistic(call_design(pl["down"], pl["ytg"], pl["yl"]), y, l2)

    # ---- 4th down ---------------------------------------------------------------------------------------------------
    f4 = t.fourth
    fourth_w = fit_softmax(fourth_design(f4["ydstogo"], f4["yardline_100"], f4["score_differential"], f4["tb"]),
                           f4["choice"].to_numpy(int), 3, l2=1.0)[1:]
    fg = t.fg
    fg_w = fit_logistic(fg_design(fg["kick_distance"]), fg["made"].to_numpy(float), 0.5)

    # ---- pass / rush outcomes ---------------------------------------------------------------------------------------
    cell = play_cell(pl["yl"], pl["ytg"], pl["script"])
    pas = pl["is_pass"].to_numpy() == 1
    parent = lambda c, L: c if L == 0 else (c // 3 if L == 1 else (c // 9 if L == 2 else 0))               # noqa: E731
    lv_p = _cell_levels_play(cell[pas])
    sack_p = pl["sack"].to_numpy()[pas]
    throws = sack_p == 0
    int_p = pl["intc"].to_numpy()[pas]
    cmp_p = pl["complete"].to_numpy()[pas]
    p_sack = shrunk_rate(sack_p, lv_p, N_PLAY_CELL, parent_of=None)
    lv_t = _cell_levels_play(cell[pas][throws])
    p_int = shrunk_rate(int_p[throws], lv_t, N_PLAY_CELL)
    p_cmp = shrunk_rate(cmp_p[throws], lv_t, N_PLAY_CELL)
    pass_probs = np.column_stack([p_sack, p_int, p_cmp])
    p_target = float(pl["target"].to_numpy()[pas][throws].mean())

    yards = pl["yards"].to_numpy()
    comp_mask = pas & (pl["complete"].to_numpy() == 1)
    td_flag = (pl["pass_td"].to_numpy() == 1) | (pl["rush_td"].to_numpy() == 1)
    cmp_pool = build_km_pool(yards[comp_mask], td_flag[comp_mask], _cell_levels_play(cell[comp_mask]), N_PLAY_CELL, parent_of=parent)
    sack_mask = pas & (pl["sack"].to_numpy() == 1)
    sack_pool = build_pool(yards[sack_mask], [np.zeros(int(sack_mask.sum()), dtype=int)], 1)
    rush_mask = ~pas
    rush_pool = build_km_pool(yards[rush_mask], td_flag[rush_mask], _cell_levels_play(cell[rush_mask]), N_PLAY_CELL, parent_of=parent)

    fum = pl["fumb"].to_numpy()
    intc = pl["intc"].to_numpy()
    retd = pl["ret_td"].to_numpy()
    fumb = {"cmp": float(fum[comp_mask].mean()), "sack": float(fum[sack_mask].mean()), "rush": float(fum[rush_mask].mean()),
            "pick6": float(retd[intc == 1].mean()) if (intc == 1).any() else 0.1,
            "fumb_td": float(retd[(fum == 1) & (intc == 0)].mean()) if ((fum == 1) & (intc == 0)).any() else 0.05}

    # ---- clock ---------------------------------------------------------------------------------------------------------
    kind = _kind(pl["is_pass"], pl["sack"], pl["complete"])
    inside = (pl["last_in_drive"].to_numpy() == False) & pl["elapsed"].notna().to_numpy()                   # noqa: E712
    ec = kind * 3 + pl["hurry"].to_numpy()
    el_levels = [ec[inside], (ec // 3)[inside], np.zeros(int(inside.sum()), dtype=int)]
    el_pool = build_pool(pl["elapsed"].to_numpy()[inside], el_levels, 12, min_n=100,
                         parent_of=lambda c, L: c if L == 0 else (c // 3 if L == 1 else 0))
    gp = t.gaps
    gp = gp[gp["etype"].between(0, 6)]
    gc = gp["etype"].to_numpy(int) * 3 + gp["hurry"].to_numpy(int)
    gap_pool = build_pool(gp["elapsed"].to_numpy(), [gc, gc // 3, np.zeros(len(gc), dtype=int)], 21, min_n=60,
                          parent_of=lambda c, L: c if L == 0 else (c // 3 if L == 1 else 0))

    # ---- field position after the drive ends ------------------------------------------------------------------------
    gk = gp[gp["etype"].isin([0, 1, 6]) & gp["next_yl"].notna()]
    kick_pool = build_pool(gk["next_yl"].to_numpy(), [np.zeros(len(gk), dtype=int)], 1)
    gs = gp[gp["etype"].isin([2, 3, 4, 5]) & gp["next_yl"].notna()]
    sc = (gs["etype"].to_numpy(int) - 2) * 10 + PP.yl_bucket10(gs["end_yl"].to_numpy())
    start_pool = build_pool(gs["next_yl"].to_numpy(), [sc, sc // 10, np.zeros(len(sc), dtype=int)], 40, min_n=40,
                            parent_of=lambda c, L: c if L == 0 else (c // 10 if L == 1 else 0))

    # ---- league rates (the ratings' reference) -----------------------------------------------------------------------
    pas_df = pl[pas]
    thr = pas_df[pas_df["sack"] == 0]
    league = {
        "sack": float(pas_df["sack"].mean()), "int": float(thr["intc"].mean()), "cmp": float(thr["complete"].mean()),
        "ypcmp": float(pas_df.loc[pas_df["complete"] == 1, "yards"].mean()),
        "ypr": float(pl.loc[~pas, "yards"].mean()), "proe": 0.0,
        "pace": float(pl.loc[inside & (pl["hurry"].to_numpy() == 0), "elapsed"].mean()),
        "pass_rate": float(y.mean()),
    }
    return SimParams(train_seasons=tuple(seasons or sorted(pl["season"].unique())), call_w=call_w, neutral_w=neutral_w,
                     fourth_w=fourth_w, fg_w=fg_w, pass_probs=pass_probs, p_target=p_target, cmp_pool=cmp_pool,
                     sack_pool=sack_pool, rush_pool=rush_pool, fumb=fumb, el_pool=el_pool, gap_pool=gap_pool,
                     kick_pool=kick_pool, start_pool=start_pool, td_extra=t.td_extra, league=league, n_plays=len(pl))
