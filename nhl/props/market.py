"""Market-side analytics for paired two-way prop quotes: consensus / leave-self-out fair, Sink A selection, flat-stake
settlement with cluster-robust statistics, calibration + proper-scoring helpers.

Frames use the `history.pair_quotes` columns: event_id, bookmaker, line, over, under, fair_over, + a `pkey` (player id or
normalised name) and, once graded, `won_over` (1/0) with `status == 'ok'`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from mlb_core.odds.utils import american_to_decimal

PROP = ["event_id", "pkey", "line"]


def _mad(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(np.median(np.abs(x - np.median(x)))) if len(x) else 0.0


def add_consensus(p: pd.DataFrame) -> pd.DataFrame:
    """Adds per-quote LEAVE-SELF-OUT reference: `loo_fair` (median fair P(over) of the OTHER books at the same
    (event, player, line)), `loo_n`, `loo_disp` (MAD); and prop-level `cons_fair`, `cons_n`, `cons_disp` over all books.
    (The historical DB has no DFS/pick'em apps; the live scorer drops them upstream, see nhl.props.score.)"""
    p = p.copy()
    loo_f, loo_n, loo_d = np.full(len(p), np.nan), np.zeros(len(p), dtype=int), np.full(len(p), np.nan)
    cons_f, cons_n, cons_d = np.full(len(p), np.nan), np.zeros(len(p), dtype=int), np.full(len(p), np.nan)
    pos = {ix: i for i, ix in enumerate(p.index)}
    for _, g in p.groupby(PROP, sort=False):
        vals = g["fair_over"].to_numpy()
        idx = g.index.to_numpy()
        cf, cd = float(np.median(vals)), _mad(vals)
        for j, ix in enumerate(idx):
            i = pos[ix]
            others = np.delete(vals, j)
            cons_f[i], cons_n[i], cons_d[i] = cf, len(vals), cd
            if len(others):
                loo_f[i], loo_n[i], loo_d[i] = float(np.median(others)), len(others), _mad(others)
    p["loo_fair"], p["loo_n"], p["loo_disp"] = loo_f, loo_n, loo_d
    p["cons_fair"], p["cons_n"], p["cons_disp"] = cons_f, cons_n, cons_d
    return p


def side_returns(df: pd.DataFrame, side: str) -> pd.Series:
    """Per-unit flat-stake return of betting `side` ('over'/'under') at each row's own quoted price. Rows must be graded
    (status ok, no push). NaN otherwise."""
    price = df[side]
    dec = price.map(american_to_decimal)
    won = df["won_over"] if side == "over" else 1.0 - df["won_over"]
    ret = np.where(won == 1.0, dec - 1.0, -1.0)
    ok = (df["status"] == "ok") & (~df["push"]) & df["won_over"].notna()
    return pd.Series(np.where(ok, ret, np.nan), index=df.index)


def sink_a_bets(p: pd.DataFrame, min_others: int = 3, max_disp: float = 0.06, min_ev: float = 0.03,
                max_ev: float | None = 0.15) -> pd.DataFrame:
    """Sink A (ev.props.scan_book_outliers' rule, applied historically): a quote whose own price beats the leave-self-out
    consensus of the other books. One row per (quote, side) that clears the gates, with `ret` settled at flat 1u."""
    rows = []
    for side, fair_fn in (("over", lambda f: f), ("under", lambda f: 1.0 - f)):
        d = p[(p["loo_n"] >= min_others) & (p["loo_disp"] <= max_disp)].copy()
        d["side"] = side
        d["p_ref"] = fair_fn(d["loo_fair"])
        d["ev"] = d["p_ref"] * d[side].map(american_to_decimal) - 1.0
        d = d[d["ev"] >= min_ev]
        if max_ev is not None:
            d = d[d["ev"] <= max_ev]
        d["ret"] = side_returns(d, side)
        rows.append(d)
    return pd.concat(rows, ignore_index=True) if rows else p.iloc[0:0]


def roi_stats(ret: pd.Series, cluster: pd.Series | None = None) -> dict:
    """Mean per-unit return with a cluster-robust standard error (cluster = e.g. game id: correlated quotes within a game
    are NOT independent bets)."""
    d = pd.DataFrame({"r": ret, "c": cluster if cluster is not None else np.arange(len(ret))}).dropna(subset=["r"])
    n = len(d)
    if n < 2:
        return {"n": n, "roi": np.nan, "se": np.nan, "t": np.nan, "n_clusters": 0, "win": np.nan}
    m = float(d["r"].mean())
    cs = d.assign(e=d["r"] - m).groupby("c")["e"].sum()
    g = len(cs)
    se = float(np.sqrt((cs ** 2).sum()) / n * np.sqrt(g / max(g - 1, 1)))
    return {"n": n, "roi": m, "se": se, "t": (m / se if se > 1e-9 else np.nan), "n_clusters": int(g),
            "win": float((d["r"] > 0).mean())}


def logloss(y, p, eps: float = 1e-6) -> float:
    y, p = np.asarray(y, dtype=float), np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def brier(y, p) -> float:
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    return float(((p - y) ** 2).mean())


def reliability(y, p, bins=(0, .2, .3, .4, .5, .6, .7, .8, 1.0)) -> pd.DataFrame:
    d = pd.DataFrame({"y": np.asarray(y, dtype=float), "p": np.asarray(p, dtype=float)})
    d["bin"] = pd.cut(d["p"], list(bins), include_lowest=True)
    g = d.groupby("bin", observed=True).agg(n=("y", "size"), p_mean=("p", "mean"), y_rate=("y", "mean"))
    g["gap"] = g["y_rate"] - g["p_mean"]
    return g.reset_index()


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def blend_regression(y, p_mkt, p_model, cluster) -> dict:
    """y ~ a + b*logit(mkt) + c*(logit(model) - logit(mkt)) by logistic MLE (Newton) with cluster-robust SEs.
    c > 0 (significant) means the model adds information beyond the market; b ~ 1 means the market is calibrated."""
    y = np.asarray(y, dtype=float)
    lm, ld = logit(p_mkt), logit(p_model) - logit(p_mkt)
    X = np.column_stack([np.ones(len(y)), lm, ld])
    beta = np.array([0.0, 1.0, 0.0])
    for _ in range(50):
        z = X @ beta
        pr = 1 / (1 + np.exp(-z))
        w = pr * (1 - pr)
        H = X.T @ (X * w[:, None]) + 1e-9 * np.eye(3)
        step = np.linalg.solve(H, X.T @ (y - pr))
        beta = beta + step
        if np.abs(step).max() < 1e-8:
            break
    pr = 1 / (1 + np.exp(-(X @ beta)))
    H = X.T @ (X * (pr * (1 - pr))[:, None])
    Hinv = np.linalg.inv(H + 1e-9 * np.eye(3))
    scores = X * (y - pr)[:, None]
    cl = pd.DataFrame(scores).groupby(np.asarray(cluster)).sum().to_numpy()
    meat = cl.T @ cl
    g = len(cl)
    cov = Hinv @ meat @ Hinv * (g / max(g - 1, 1))
    se = np.sqrt(np.diag(cov))
    return {"a": beta[0], "b": beta[1], "c": beta[2], "se_a": se[0], "se_b": se[1], "se_c": se[2],
            "t_c": beta[2] / se[2], "t_b_vs_1": (beta[1] - 1) / se[1], "n": len(y), "n_clusters": g}
