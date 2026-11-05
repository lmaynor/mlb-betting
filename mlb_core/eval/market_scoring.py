"""Score a probability model against a de-vigged market on the SAME events, the way a sharp shop would.

One row per (game, market) event. Required columns:
    date     str   game day (the CLUSTER for every standard error: games on one day share news / weather / noise)
    p_model  float model P(event), event = home wins / home covers / over (push removed, or renormalised)
    p_mkt    float de-vigged closing P(event) from the market
    y        0/1   realised outcome of the event (pushes and ties already dropped)
Optional price columns (decimal odds; a = the event, b = the opposite side):
    dec_a_med, dec_b_med    median book price     dec_a_best, dec_b_best   best book price
    dec_a_open, dec_b_open, line_open, line_close   opening price on the identical line (CLV)

Everything is UNVALIDATED output of a research tool. What it computes:
    paired log-loss difference vs the market with date-clustered z (positive = model better)
    Brier skill score vs the market; ECE; the logit blend weight c on (model - market) with clustered SE
    edge buckets (model side vs market): win-rate minus implied, ROI at median and best price, monotonicity
    the NO-MODEL line-shopping control (bet both sides everywhere) so best-price artifacts are not read as alpha
    price CLV of the model's side, opening price vs close, same line only
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

EPS = 1e-6
DEFAULT_EDGE_BUCKETS = (0.0, 0.02, 0.04, 0.06, 0.08, 1.0)


# ---------------------------------------------------------------------------------------------------- prices
def american_to_decimal_safe(odds) -> float:
    o = float(odds)
    return 1.0 + (o / 100.0 if o > 0 else 100.0 / abs(o))


def devig_pair(odds_a, odds_b, method: str = "proportional"):
    """(p_a, p_b) fair probabilities from two American prices."""
    return devig_two_way(american_to_implied_prob(odds_a), american_to_implied_prob(odds_b), method)


# ---------------------------------------------------------------------------------------------------- statistics
def clustered_mean(x: Sequence[float], groups: Sequence) -> Dict[str, float]:
    """Mean with a cluster-robust standard error (clusters = groups, small-sample G/(G-1) correction)."""
    x = np.asarray(x, dtype=float)
    ok = ~np.isnan(x)
    x = x[ok]
    g = np.asarray(groups)[ok]
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float("nan"), "se": float("nan"), "z": float("nan"), "clusters": 0}
    m = float(x.mean())
    df = pd.DataFrame({"x": x, "g": g}).groupby("g")["x"].agg(["sum", "count"])
    G = len(df)
    resid = df["sum"] - df["count"] * m
    se = math.sqrt(float((resid ** 2).sum()) * (G / (G - 1) if G > 1 else 1.0)) / n
    return {"n": n, "mean": m, "se": se, "z": (m / se) if se > 0 else float("nan"), "clusters": G}


def _ll(p, y):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def paired_logloss(df: pd.DataFrame) -> Dict[str, float]:
    """mean(ll_market - ll_model): positive = the model beats the market. Date-clustered."""
    d = _ll(df["p_mkt"], df["y"]) - _ll(df["p_model"], df["y"])
    out = clustered_mean(d, df["date"])
    out["ll_model"] = float(_ll(df["p_model"], df["y"]).mean())
    out["ll_market"] = float(_ll(df["p_mkt"], df["y"]).mean())
    return out


def brier_skill(df: pd.DataFrame) -> Dict[str, float]:
    """1 - Brier_model / Brier_market (positive = better than the market); z on the paired Brier difference."""
    y = df["y"].to_numpy(dtype=float)
    bm = (df["p_model"].to_numpy(dtype=float) - y) ** 2
    bk = (df["p_mkt"].to_numpy(dtype=float) - y) ** 2
    out = clustered_mean(bk - bm, df["date"])
    out["bss"] = float(1.0 - bm.mean() / bk.mean())
    return out


def ece(p: Sequence[float], y: Sequence[float], bins: int = 10) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(tot)


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def blend_weight(df: pd.DataFrame, iters: int = 50) -> Dict[str, float]:
    """Logistic fit  y ~ a*logit(p_mkt) + c*(logit(p_model) - logit(p_mkt))  (no intercept), cluster-robust SEs.
    c is the weight on what the model knows beyond the market: ~0 = nothing, >0 = information, <0 = noise."""
    lm, lk = _logit(df["p_model"]), _logit(df["p_mkt"])
    X = np.column_stack([lk, lm - lk])
    y = df["y"].to_numpy(dtype=float)
    beta = np.array([1.0, 0.0])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ beta))
        W = np.clip(p * (1 - p), 1e-9, None)
        H = X.T @ (X * W[:, None])
        step = np.linalg.solve(H, X.T @ (y - p))
        beta = beta + step
        if np.abs(step).max() < 1e-9:
            break
    p = 1 / (1 + np.exp(-X @ beta))
    W = np.clip(p * (1 - p), 1e-9, None)
    Hinv = np.linalg.inv(X.T @ (X * W[:, None]))
    score = X * (y - p)[:, None]
    sc = pd.DataFrame(score).groupby(np.asarray(df["date"])).sum().to_numpy()
    G = len(sc)
    meat = sc.T @ sc * (G / (G - 1) if G > 1 else 1.0)
    cov = Hinv @ meat @ Hinv
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    return {"a_market": float(beta[0]), "c_model": float(beta[1]), "se_a": float(se[0]), "se_c": float(se[1]),
            "z_c": float(beta[1] / se[1]) if se[1] > 0 else float("nan"), "n": int(len(y)), "clusters": int(G)}


# ---------------------------------------------------------------------------------------------------- bets
def _side_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Per event: the side the model prefers over the market (a = the event, b = the opposite), with its outcome,
    de-vigged market probability and prices."""
    d = df.copy()
    edge_a = d["p_model"] - d["p_mkt"]
    on_a = edge_a > 0
    d["edge"] = edge_a.abs()
    d["win"] = np.where(on_a, d["y"], 1 - d["y"]).astype(float)
    d["p_mkt_side"] = np.where(on_a, d["p_mkt"], 1 - d["p_mkt"])
    for kind in ("med", "best"):
        a, b = f"dec_a_{kind}", f"dec_b_{kind}"
        d[f"dec_{kind}"] = np.where(on_a, d[a], d[b]) if a in d and b in d else np.nan
    if "dec_a_open" in d and "dec_b_open" in d:
        d["dec_open"] = np.where(on_a, d["dec_a_open"], d["dec_b_open"])
        d["dec_close"] = np.where(on_a, d["dec_a_med"], d["dec_b_med"]) if "dec_a_med" in d else np.nan
    return d


def edge_buckets(df: pd.DataFrame, edges: Iterable[float] = DEFAULT_EDGE_BUCKETS) -> Dict[str, object]:
    """Win rate minus the market's implied probability, and ROI, by model-vs-market edge bucket (flat 1u)."""
    d = _side_frame(df)
    e = list(edges)
    rows: List[dict] = []
    for lo, hi in zip(e[:-1], e[1:]):
        s = d[(d["edge"] >= lo) & (d["edge"] < hi)]
        if len(s) == 0:
            continue
        row = {"bucket": f"{lo:.0%}-{hi:.0%}" if hi < 1 else f"{lo:.0%}+", "n": int(len(s)),
               "mean_edge": float(s["edge"].mean()), "win_rate": float(s["win"].mean()),
               "mkt_implied": float(s["p_mkt_side"].mean())}
        row["gap"] = row["win_rate"] - row["mkt_implied"]
        for kind in ("med", "best"):
            col = f"dec_{kind}"
            if s[col].notna().any():
                r = clustered_mean(s["win"] * s[col] - 1.0, s["date"])
                row[f"roi_{kind}"], row[f"roi_{kind}_se"] = r["mean"], r["se"]
        rows.append(row)
    tab = pd.DataFrame(rows)
    mono = {}
    ok = tab[tab["n"] >= 30] if len(tab) else tab
    if len(ok) >= 3:
        order = pd.Series(range(len(ok)), index=ok.index, dtype=float)
        mono["gap_spearman"] = float(order.corr(ok["gap"], method="spearman"))
        if "roi_med" in ok:
            mono["roi_med_spearman"] = float(order.corr(ok["roi_med"], method="spearman"))
    mono["monotonic_gap"] = bool(len(ok) >= 3 and ok["gap"].is_monotonic_increasing)
    return {"table": tab, "monotonic": mono}


def line_shop_control(df: pd.DataFrame) -> Dict[str, float]:
    """NO-MODEL control: bet 1u on BOTH sides of every event. ROI = minus the vig at that price; the best-minus-median
    difference is what line shopping alone is worth, which a model's best-price ROI must be measured against."""
    out: Dict[str, float] = {"n": int(len(df))}
    for kind in ("med", "best"):
        a, b = f"dec_a_{kind}", f"dec_b_{kind}"
        if a in df and b in df and df[a].notna().any():
            y = df["y"].to_numpy(dtype=float)
            both = ((y * df[a].to_numpy(dtype=float) - 1) + ((1 - y) * df[b].to_numpy(dtype=float) - 1)) / 2.0
            r = clustered_mean(both, df["date"])
            out[f"roi_{kind}"], out[f"roi_{kind}_se"] = r["mean"], r["se"]
    if "roi_med" in out and "roi_best" in out:
        out["shopping_worth"] = out["roi_best"] - out["roi_med"]
    return out


def clv_price(df: pd.DataFrame) -> Dict[str, float]:
    """Price CLV of the model's side: opening decimal / closing decimal - 1, ONLY where the line did not move.
    Hypothetical entry at the open; a forward CLV ledger replaces it once picks are logged with timestamps."""
    d = _side_frame(df)
    if "dec_open" not in d:
        return {"n": 0}
    ok = d["dec_open"].notna() & d["dec_close"].notna()
    if "line_open" in d and "line_close" in d:
        ok &= (d["line_open"] == d["line_close"]) | (d["line_open"].isna() & d["line_close"].isna())
    s = d[ok]
    return clustered_mean(s["dec_open"] / s["dec_close"] - 1.0, s["date"]) if len(s) else {"n": 0}


# ---------------------------------------------------------------------------------------------------- report
def score_market(df: pd.DataFrame, name: str, edges: Iterable[float] = DEFAULT_EDGE_BUCKETS) -> Dict[str, object]:
    d = df.dropna(subset=["p_model", "p_mkt", "y"]).copy()
    d["date"] = d["date"].astype(str)
    return {"market": name, "n": int(len(d)), "dates": int(d["date"].nunique()),
            "logloss": paired_logloss(d), "brier": brier_skill(d), "ece_model": ece(d["p_model"], d["y"]),
            "ece_market": ece(d["p_mkt"], d["y"]), "blend": blend_weight(d), "buckets": edge_buckets(d, edges),
            "control": line_shop_control(d), "clv": clv_price(d)}


def render_markdown(res: Dict[str, object]) -> str:
    ll, bs, bl = res["logloss"], res["brier"], res["blend"]
    out = [f"### {res['market']}  (n={res['n']}, {res['dates']} game days)",
           f"- log loss: model {ll['ll_model']:.4f} vs market {ll['ll_market']:.4f}; paired gain (market - model) "
           f"{ll['mean']:+.4f} (date-clustered z {ll['z']:+.2f})  [positive = model better]",
           f"- Brier skill vs market {bs['bss']:+.4f} (z {bs['z']:+.2f}); ECE model {res['ece_model']:.4f} / market "
           f"{res['ece_market']:.4f}",
           f"- blend: weight on the market {bl['a_market']:.2f}, weight on (model - market) c = {bl['c_model']:+.3f} "
           f"(se {bl['se_c']:.3f}, z {bl['z_c']:+.2f})"]
    tab = res["buckets"]["table"]
    if len(tab):
        cols = [c for c in ("bucket", "n", "win_rate", "mkt_implied", "gap", "roi_med", "roi_best") if c in tab]
        out += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for _, r in tab.iterrows():
            out.append("| " + " | ".join(r[c] if c == "bucket" else (f"{int(r[c])}" if c == "n" else f"{r[c]:+.3f}")
                                          for c in cols) + " |")
        mono = res["buckets"]["monotonic"]
        out.append("- edge-bucket monotonicity (rank corr of bucket order with win-minus-implied / ROI; the gate wants a "
                   f"rising gap): gap rho {mono.get('gap_spearman', float('nan')):+.2f}, ROI rho "
                   f"{mono.get('roi_med_spearman', float('nan')):+.2f}, strictly rising gap: {mono['monotonic_gap']}")
    c = res["control"]
    if "roi_med" in c:
        out.append(f"- no-model control (both sides): ROI median {c['roi_med']:+.3%}"
                   + (f", best {c['roi_best']:+.3%}; line shopping alone is worth {c['shopping_worth']:+.3%}"
                      if "roi_best" in c else ""))
    clv = res["clv"]
    if clv.get("n"):
        out.append(f"- open-price CLV of the model side (same line only): {clv['mean']:+.3%} "
                   f"(n={clv['n']}, z {clv['z']:+.2f}) -- hypothetical entry at the open")
    return "\n".join(out)
