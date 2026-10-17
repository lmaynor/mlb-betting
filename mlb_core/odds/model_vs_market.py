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


# ---------------------------------------------------------------------------------------------------------------------
# Walk-forward price test of the market-anchored blend (2026-10-16).
# ---------------------------------------------------------------------------------------------------------------------
PRICE_THRESHOLDS = (0.03, 0.04, 0.05)
BLEND_MAX_SHIFT = 0.08


def _sig(z):
    return 1.0 / (1.0 + np.exp(-z))


def _profit_units(american, win) -> np.ndarray:
    """Flat 1-unit profit at American odds: dec-1 on a win, -1 on a loss."""
    a = np.asarray(american, dtype=float)
    dec = np.where(a > 0, a / 100.0 + 1.0, 100.0 / np.abs(a) + 1.0)
    return np.where(np.asarray(win, dtype=bool), dec - 1.0, -1.0)


def _implied(american) -> np.ndarray:
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, 100.0 / (a + 100.0), np.abs(a) / (np.abs(a) + 100.0))


def _cluster_boot_mean(x: np.ndarray, groups, n_boot: int = 400, seed: int = 1) -> tuple:
    """95% CI of mean(x) resampling whole groups (game dates) with replacement."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return (np.nan, np.nan)
    g = pd.Series(np.asarray(groups)).astype(str)
    sums = pd.DataFrame({"s": x, "g": g.to_numpy()}).groupby("g")["s"].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    k = len(sums)
    idx = rng.integers(0, k, size=(n_boot, k))
    tot, cnt = sums["sum"].to_numpy()[idx].sum(1), sums["count"].to_numpy()[idx].sum(1)
    est = tot / np.clip(cnt, 1, None)
    return (float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5)))


def _price_rows(y, p_prob, o_over, o_under, thr: float):
    """Bets where the better side's probability beats that side's best-price implied probability by >= thr."""
    e_over = p_prob - _implied(o_over)
    e_under = (1.0 - p_prob) - _implied(o_under)
    take_over = e_over >= e_under
    edge = np.where(take_over, e_over, e_under)
    sel = edge >= thr
    won = np.where(take_over, y == 1.0, y == 0.0)
    prof = _profit_units(np.where(take_over, o_over, o_under), won)
    return sel, prof


def blend_price_test(frame: pd.DataFrame, thresholds=PRICE_THRESHOLDS, min_train: int = 500, max_shift: float = BLEND_MAX_SHIFT,
                     n_boot: int = 400, season_col: str = "season") -> dict:
    """Walk-forward test of p_blend = sigmoid(a + b*logit(p_mkt) + c*(logit(p_model) - logit(p_mkt))), clamped to
    p_mkt +/- max_shift. For each season (or year of game_date when `season_col` is absent) the (a, b, c) come from
    blend_regression on EARLIER seasons only; the first season is never scored. `frame` needs: over, p_model, p_market,
    game_date, game_id, best_over_odds, best_under_odds.

    Returns {"n", "fits": {season: {a, b, c, t_c, n_train}}, "scores": Brier/log-loss of blend / market / model on the
    scored rows with the paired Brier difference (blend - market) and its game-date-cluster 95% CI, "price": {rule:
    {threshold: {n_bets, roi, ci_lo, ci_hi}}}} for rules 'blend', 'market' (line shopping vs the consensus alone: the
    control the blend must beat) and 'model' (standalone), all flat 1 unit at the best quoted price per side."""
    need = ["over", "p_model", "p_market", "game_date", "game_id", "best_over_odds", "best_under_odds"]
    miss = [c for c in need if c not in frame.columns]
    if miss:
        raise ValueError(f"frame is missing {miss}")
    f = frame.copy()
    if season_col not in f.columns:
        d = pd.to_datetime(f["game_date"].astype(str).str[:10], errors="coerce")
        f[season_col] = (d.dt.year - (d.dt.month < 8).astype(int)).astype("Int64")
    for c in ("over", "p_model", "p_market", "best_over_odds", "best_under_odds"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f = f.dropna(subset=need + [season_col]).reset_index(drop=True)
    f = f[(f["p_model"] > 0) & (f["p_model"] < 1) & (f["p_market"] > 0) & (f["p_market"] < 1)].reset_index(drop=True)
    out = {"n": 0, "fits": {}, "scores": None, "price": {}}
    parts = []
    for s in sorted(f[season_col].unique()):
        tr = f[f[season_col] < s]
        te = f[f[season_col] == s]
        if len(tr) < min_train or len(te) == 0:
            continue
        try:
            fit = M.blend_regression(tr["over"], tr["p_market"], tr["p_model"], tr["game_id"])
        except np.linalg.LinAlgError:
            continue
        a, b, c = float(fit["a"]), float(fit["b"]), float(fit["c"])
        lm = M.logit(te["p_market"].to_numpy(float))
        ld = M.logit(te["p_model"].to_numpy(float)) - lm
        pb = np.clip(_sig(a + b * lm + c * ld), te["p_market"] - max_shift, te["p_market"] + max_shift)
        out["fits"][int(s)] = {"a": a, "b": b, "c": c, "t_c": float(fit["t_c"]), "n_train": int(len(tr))}
        parts.append(te.assign(p_blend=pb.to_numpy() if hasattr(pb, "to_numpy") else pb))
    if not parts:
        return out
    t = pd.concat(parts, ignore_index=True)
    y = t["over"].to_numpy(float)
    out["n"] = int(len(t))
    sc = {k: {"brier": M.brier(y, t[col].to_numpy(float)), "logloss": M.logloss(y, t[col].to_numpy(float))}
          for k, col in (("blend", "p_blend"), ("market", "p_market"), ("model", "p_model"))}
    dbr = (t["p_blend"] - y) ** 2 - (t["p_market"] - y) ** 2
    lo, hi = _cluster_boot_mean(dbr.to_numpy(float), t["game_date"].astype(str), n_boot)
    sc["d_brier_blend_vs_market"] = {"mean": float(dbr.mean()), "ci_lo": lo, "ci_hi": hi}
    out["scores"] = sc
    oo, ou = t["best_over_odds"].to_numpy(float), t["best_under_odds"].to_numpy(float)
    for rule, col in (("blend", "p_blend"), ("market", "p_market"), ("model", "p_model")):
        out["price"][rule] = {}
        for thr in thresholds:
            sel, prof = _price_rows(y, t[col].to_numpy(float), oo, ou, float(thr))
            r = prof[sel]
            lo, hi = _cluster_boot_mean(r, t.loc[sel, "game_date"].astype(str), n_boot) if len(r) else (np.nan, np.nan)
            out["price"][rule][float(thr)] = {"n_bets": int(sel.sum()), "roi": float(r.mean()) if len(r) else float("nan"),
                                              "ci_lo": lo, "ci_hi": hi}
    return out


def render_price_test(label: str, res: dict) -> str:
    """Markdown for a blend_price_test result."""
    if not res or res.get("n", 0) == 0:
        return f"- {label}: price test not run (too few rows or no earlier season to fit on)"
    sc = res["scores"]
    d = sc["d_brier_blend_vs_market"]
    L = [f"- {label}: walk-forward n={res['n']} (seasons fitted on earlier seasons only) | Brier blend {sc['blend']['brier']:.4f} / "
         f"market {sc['market']['brier']:.4f} / model {sc['model']['brier']:.4f}; logloss blend {sc['blend']['logloss']:.4f} / "
         f"market {sc['market']['logloss']:.4f} / model {sc['model']['logloss']:.4f}; paired dBrier(blend-market) "
         f"{d['mean']:+.5f} [{d['ci_lo']:+.5f}, {d['ci_hi']:+.5f}]",
         "  fitted c by season: " + ", ".join(f"{k}: {v['c']:+.2f} (t={v['t_c']:+.1f})" for k, v in res["fits"].items())]
    for rule in ("blend", "market", "model"):
        cells = []
        for thr, r in res["price"][rule].items():
            cells.append(f">={thr:.0%}: n={r['n_bets']} ROI {r['roi']:+.2%} [{r['ci_lo']:+.2%}, {r['ci_hi']:+.2%}]"
                         if r["n_bets"] else f">={thr:.0%}: n=0")
        L.append(f"  flat 1u at best price, rule={rule}: " + " | ".join(cells))
    return "\n".join(L)
