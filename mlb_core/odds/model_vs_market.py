"""Shared scoring + coverage bookkeeping for "model vs historical BettingPros market" evaluator hooks.

The per-sport evaluators (nfl/props/history_join.py, nba/props/history_join.py, nhl/props/study.py) each join the
consensus frame from mlb_core.odds.bettingpros_loader to their own prediction frame; this module holds the part that
must not diverge between them:

  * `score_model_vs_market` -- Brier and log-loss for the model and for the de-vigged market consensus on the SAME rows,
    plus the blend regression  y ~ a + b*logit(mkt) + c*(logit(model) - logit(mkt))  with cluster-robust SEs. The math is
    nhl.props.market.blend_regression / brier / logloss (single definition, reused, not copied). c > 0 and significant
    means the model adds information beyond the market.
  * `coverage` -- the join-coverage record every hook returns. INVARIANT: matched + sum(dropped.values()) ==
    consensus_rows, i.e. every consensus row is either scored or counted under exactly one drop reason.

Nothing here claims edge: Brier/log-loss vs the market is a diagnostic. CLV, not ROI, is the repo's go/no-go.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nhl.props import market as M

MIN_SCORE_ROWS = 30
MIN_CLUSTERS = 5


def coverage(consensus_rows: int, matched: int, dropped: dict, **extra) -> dict:
    """Join-coverage record. `dropped` maps reason -> count of consensus rows lost for that reason (each row counted once,
    at the first stage that lost it). Raises if the books do not balance."""
    dropped = {k: int(v) for k, v in dropped.items()}
    if int(matched) + sum(dropped.values()) != int(consensus_rows):
        raise ValueError(f"coverage does not balance: matched={matched} dropped={dropped} rows={consensus_rows}")
    return {"consensus_rows": int(consensus_rows), "matched": int(matched), "dropped": dropped,
            "match_rate": (float(matched) / consensus_rows if consensus_rows else float("nan")), **extra}


def score_model_vs_market(y, p_model, p_market, cluster) -> dict:
    """y in {0,1} (1 = over), probabilities of OVER from the model and from the de-vigged market, `cluster` = game id
    (props in one game are correlated). Pushes must already be removed. Returns n, n_clusters, Brier/log-loss for both,
    their paired differences (model - market; negative = model better), and the blend regression (a, b, c, se_c, t_c)."""
    y = np.asarray(y, dtype=float)
    pm = np.asarray(p_model, dtype=float)
    pk = np.asarray(p_market, dtype=float)
    ok = np.isfinite(y) & np.isfinite(pm) & np.isfinite(pk)
    y, pm, pk = y[ok], pm[ok], pk[ok]
    cl = np.asarray(cluster)[ok]
    n = int(len(y))
    out = {"n": n, "n_clusters": int(len(pd.unique(cl))) if n else 0, "over_rate": float(y.mean()) if n else np.nan,
           "mean_p_model": float(pm.mean()) if n else np.nan, "mean_p_market": float(pk.mean()) if n else np.nan}
    if n == 0:
        out.update(brier_model=np.nan, brier_market=np.nan, logloss_model=np.nan, logloss_market=np.nan,
                   d_brier=np.nan, d_logloss=np.nan, blend=None)
        return out
    out.update(brier_model=M.brier(y, pm), brier_market=M.brier(y, pk), logloss_model=M.logloss(y, pm),
               logloss_market=M.logloss(y, pk))
    out["d_brier"] = out["brier_model"] - out["brier_market"]
    out["d_logloss"] = out["logloss_model"] - out["logloss_market"]
    out["blend"] = None
    if n >= MIN_SCORE_ROWS and out["n_clusters"] >= MIN_CLUSTERS:
        try:
            out["blend"] = {k: float(v) for k, v in M.blend_regression(y, pk, pm, cl).items()}
        except np.linalg.LinAlgError:
            out["blend"] = None
    return out


def render_scores(label: str, scores: dict) -> str:
    """One markdown line for a score dict."""
    if not scores or scores.get("n", 0) == 0:
        return f"- {label}: no scored rows"
    b = scores.get("blend") or {}
    c = f"c={b['c']:+.3f} (se {b['se_c']:.3f}, t={b['t_c']:+.2f})" if b else "c=n/a (too few rows/clusters)"
    return (f"- {label}: n={scores['n']} clusters={scores['n_clusters']} | Brier model {scores['brier_model']:.4f} vs "
            f"market {scores['brier_market']:.4f} | logloss model {scores['logloss_model']:.4f} vs market "
            f"{scores['logloss_market']:.4f} | blend {c}")


def render_coverage(label: str, cov: dict) -> str:
    drops = ", ".join(f"{k}={v}" for k, v in cov["dropped"].items() if v) or "none"
    return (f"- {label} coverage: {cov['matched']}/{cov['consensus_rows']} consensus rows scored; dropped: {drops}")


def screen_consensus(cons: pd.DataFrame, stat_map: dict, min_books: int = 3):
    """Restrict a loader consensus frame to the stats an evaluator models and apply the market-side gates.
    `stat_map` = {loader stat label: evaluator stat name}. Returns (kept frame with a `model_stat` column, n_in, dropped):
    n_in counts every consensus row of a modelled stat; `dropped` has 'low_books' (n_books < min_books) and
    'postgame_quote' (pregame == False: the quote was last updated after the scheduled start)."""
    if cons is None or len(cons) == 0:
        return pd.DataFrame(columns=list(getattr(cons, "columns", [])) + ["model_stat"]), 0, {
            "low_books": 0, "postgame_quote": 0}
    c = cons[cons["stat"].isin(stat_map)].copy()
    c["model_stat"] = c["stat"].map(stat_map)
    n_in = int(len(c))
    low = pd.to_numeric(c["n_books"], errors="coerce").fillna(0) < int(min_books)
    c = c[~low]
    post = c["pregame"].astype("boolean").fillna(True) == False                      # noqa: E712
    dropped = {"low_books": int(low.sum()), "postgame_quote": int(post.sum())}
    return c[~post].reset_index(drop=True), n_in, dropped
