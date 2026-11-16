"""RES-10 - distribution pop screen.

Luc's direction (Nov 16, 2026): "Even if we're modeling accurately we can see if something
pops outside of our distribution and is therefore worth betting."

The production distributions are FROZEN (no refitting on the evaluation panel) and every
book's posted price on the genuine Action Network panels (NBA, NHL, MLB) is scanned against
them. A bet fires when the book's implied probability is at least delta BELOW our
distribution's fair probability for that side, delta in {3pp, 5pp, 8pp}. Flat 1-unit stakes
at the book's posted price, settled on banked outcomes, date-clustered CIs on ROI per cell.

POP STANDARD (pre-registered, do not move): a cell pops only if n >= 100 settled bets AND
its date-clustered 95% CI on ROI excludes zero on the positive side. A pop is a candidate
for confirmation on forward data, not a promotion.

Cells are (league, stat, book, side, delta) - books are never pooled into a "best price".
Families: main lines, milestone exceedances (Yes only), NHL goal-scorer rungs (Yes only).
Named pre-registered family views: RES-08 unders-at-close (main-line unders pooled by
league/stat) and the LAD-04 NHL SOG tail (SOG milestone Yes at +180..+220, 5pp gate family).

Inputs are two parquet frames assembled by the run's stage scripts (see the RES-10 memo):
  dists  [league, stat, date, name_key, mu, disp, family, y, source]
         family: 'negbin' (disp = alpha, var = mu + alpha*mu^2) or 'normal' (disp = sd).
         date is the game-date key of the league's master (NBA: UTC tip date;
         NHL/MLB: local slate date). y is the banked realized outcome.
  quotes [league, stat, surface, date, game_date, name_key, book_id, side, line, threshold,
          odds]
         surface: 'main' | 'milestone' | 'rung'; side: 'over'/'under' for main, 'yes' for
         milestone/rung; line for main, threshold for milestone/rung; date = partition
         (slate) date, game_date = the resolved game-date key used for the dist join.

Outputs (into --out dir): grid.csv (every pre-registered cell), families.csv (named family
views), calibration.csv, bets.parquet (every fired bet at delta=3pp with its settlement),
summary.json (coverage, multiplicity, pops, nearest cells).
"""
from __future__ import annotations

import argparse
import json
import math
import os

import numpy as np
import pandas as pd
from scipy import stats

BOOKS = {15: "consensus", 30: "open", 68: "draftkings", 69: "fanduel",
         75: "betmgm", 79: "bet365", 123: "caesars"}
DELTAS = (0.03, 0.05, 0.08)
POP_MIN_N = 100
BOOT_REPS = 2000
SEED = 20261116

MAIN_STATS = [("nba", "points"), ("nba", "rebounds"), ("nba", "assists"),
              ("nba", "threes"), ("nba", "pra"),
              ("nhl", "saves"), ("nhl", "sog"), ("nhl", "assists"), ("nhl", "points"),
              ("mlb", "pitching_outs")]
MILESTONE_STATS = [("nba", "points"), ("nba", "rebounds"), ("nba", "assists"),
                   ("nba", "threes"), ("nhl", "sog"), ("nhl", "assists")]
RUNG_STATS = [("nhl", "goals")]


# ------------------------------------------------------------------ odds / settlement
def american_to_prob(odds: float) -> float:
    """Raw implied probability of an American price (no de-vig)."""
    o = float(odds)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def american_profit(odds: float) -> float:
    """Profit on a 1-unit stake when the bet wins."""
    o = float(odds)
    return o / 100.0 if o > 0 else 100.0 / (-o)


def settle_main(side: str, y: float, line: float) -> str:
    if y > line:
        return "win" if side == "over" else "loss"
    if y < line:
        return "win" if side == "under" else "loss"
    return "push"


def settle_yes(y: float, threshold: float) -> str:
    return "win" if y >= threshold else "loss"


def bet_profit(odds: float, outcome: str) -> float:
    if outcome == "win":
        return american_profit(odds)
    if outcome == "loss":
        return -1.0
    return 0.0


# ------------------------------------------------------------------ fair probabilities
def _negbin_cdf(k, mu, alpha):
    """NegBin CDF with the repo convention (var = mu + alpha*mu^2); alpha <= 0 degrades
    to Poisson, matching nhl/props/negbin.py."""
    mu = np.asarray(mu, dtype=float)
    alpha = np.asarray(alpha, dtype=float)
    pois = stats.poisson.cdf(k, mu)
    a = np.where(alpha > 0, alpha, 1.0)
    n = 1.0 / a
    nb = stats.nbinom.cdf(k, n, n / (n + mu))
    return np.where(alpha > 0, nb, pois)


def _negbin_pmf(k, mu, alpha):
    mu = np.asarray(mu, dtype=float)
    alpha = np.asarray(alpha, dtype=float)
    pois = stats.poisson.pmf(k, mu)
    a = np.where(alpha > 0, alpha, 1.0)
    n = 1.0 / a
    nb = stats.nbinom.pmf(k, n, n / (n + mu))
    return np.where(alpha > 0, nb, pois)


def main_win_probs(family: str, mu, disp, line):
    """(p_over_win, p_push, p_under_win) for a two-sided main line under the distribution.

    NegBin: exact counts. Normal: continuity correction on integer lines (push mass is the
    half-unit band); x.5 lines have no push mass under either family.
    """
    mu = np.asarray(mu, dtype=float)
    disp = np.asarray(disp, dtype=float)
    line = np.asarray(line, dtype=float)
    integer = np.abs(line - np.round(line)) < 1e-9
    if family == "negbin":
        cdf_floor = _negbin_cdf(np.floor(line), mu, disp)
        p_over = 1.0 - cdf_floor
        p_push = np.where(integer, _negbin_pmf(np.round(line), mu, disp), 0.0)
        p_under = cdf_floor - p_push
        return p_over, p_push, p_under
    if family == "normal":
        sd = np.maximum(disp, 1e-9)
        z = (line - mu) / sd
        cdf_line = stats.norm.cdf(z)
        p_over = np.where(integer, 1.0 - stats.norm.cdf((line + 0.5 - mu) / sd), 1.0 - cdf_line)
        p_under = np.where(integer, stats.norm.cdf((line - 0.5 - mu) / sd), cdf_line)
        p_push = np.where(integer, 1.0 - p_over - p_under, 0.0)
        return p_over, p_push, p_under
    raise ValueError(family)


def exceedance_prob(family: str, mu, disp, threshold):
    """P(X >= threshold): NegBin exact; Normal with continuity correction at T - 0.5."""
    mu = np.asarray(mu, dtype=float)
    disp = np.asarray(disp, dtype=float)
    t = np.asarray(threshold, dtype=float)
    if family == "negbin":
        return 1.0 - _negbin_cdf(t - 1.0, mu, disp)
    if family == "normal":
        return 1.0 - stats.norm.cdf((t - 0.5 - mu) / np.maximum(disp, 1e-9))
    raise ValueError(family)


# ------------------------------------------------------------------ inference
def cluster_bootstrap_ci(dates, profits, reps=BOOT_REPS, seed=SEED):
    """Percentile 95% CI for mean profit, resampling whole game dates with replacement."""
    dates = np.asarray(dates)
    profits = np.asarray(profits, dtype=float)
    uniq, inv = np.unique(dates, return_inverse=True)
    g = len(uniq)
    if g < 2 or len(profits) == 0:
        return (float("nan"), float("nan"))
    p_sum = np.bincount(inv, weights=profits, minlength=g)
    n_cnt = np.bincount(inv, minlength=g).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, g, size=(reps, g))
    w = np.zeros((reps, g))
    np.add.at(w, (np.arange(reps)[:, None], idx), 1.0)
    tot_p = w @ p_sum
    tot_n = w @ n_cnt
    roi = tot_p / np.maximum(tot_n, 1.0)
    return (float(np.percentile(roi, 2.5)), float(np.percentile(roi, 97.5)))


def cluster_robust_se(dates, profits):
    """Cluster-robust SE of the mean (ratio estimator, clusters = game dates)."""
    dates = np.asarray(dates)
    profits = np.asarray(profits, dtype=float)
    n = len(profits)
    if n == 0:
        return float("nan")
    uniq, inv = np.unique(dates, return_inverse=True)
    g = len(uniq)
    if g < 2:
        return float("nan")
    roi = profits.mean()
    u = profits - roi
    cl = np.bincount(inv, weights=u, minlength=g)
    var = (g / (g - 1.0)) * float(cl @ cl) / (n * n)
    return math.sqrt(max(var, 0.0))


def bh_qvalues(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg q-values."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / (np.arange(m) + 1.0)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


# ------------------------------------------------------------------ pricing the panel
def price_frame(joined: pd.DataFrame) -> pd.DataFrame:
    """Given quotes already joined to distributions (columns: surface, dist_family, mu,
    disp, y, side, line, threshold, odds), compute fair/implied/outcome/profit columns."""
    m = joined.copy()
    m["implied"] = [american_to_prob(o) for o in m["odds"]]
    fair = np.full(len(m), np.nan)
    outcome = np.full(len(m), "", dtype=object)

    for fam, sub in m.groupby("dist_family"):
        idx = sub.index
        is_main = (sub["surface"] == "main").to_numpy()
        if is_main.any():
            s = sub[is_main]
            p_over, _push, p_under = main_win_probs(fam, s["mu"], s["disp"], s["line"])
            fair[idx[is_main]] = np.where(s["side"].to_numpy() == "over", p_over, p_under)
            outcome[idx[is_main]] = [settle_main(sd, y, ln) for sd, y, ln
                                     in zip(s["side"], s["y"], s["line"])]
        if (~is_main).any():
            s = sub[~is_main]
            fair[idx[~is_main]] = exceedance_prob(fam, s["mu"], s["disp"], s["threshold"])
            outcome[idx[~is_main]] = [settle_yes(y, t) for y, t in zip(s["y"], s["threshold"])]
    m["fair"] = fair
    m["outcome"] = outcome
    m["profit"] = [bet_profit(o, oc) for o, oc in zip(m["odds"], m["outcome"])]
    m["gap"] = m["fair"] - m["implied"]
    return m


# ------------------------------------------------------------------ cell statistics
def cell_stats(bets: pd.DataFrame) -> dict:
    """Stats for one cell's fired bets (already filtered to one delta)."""
    n = len(bets)
    if n == 0:
        return {"n_fired": 0, "n_settled": 0, "n_win": 0, "n_loss": 0, "n_push": 0,
                "n_dates": 0, "roi": np.nan, "ci_lo": np.nan, "ci_hi": np.nan,
                "se": np.nan, "mean_fair": np.nan, "mean_implied": np.nan,
                "hit_rate": np.nan}
    wins = int((bets["outcome"] == "win").sum())
    losses = int((bets["outcome"] == "loss").sum())
    pushes = int((bets["outcome"] == "push").sum())
    lo, hi = cluster_bootstrap_ci(bets["game_date"].to_numpy(), bets["profit"].to_numpy())
    se = cluster_robust_se(bets["game_date"].to_numpy(), bets["profit"].to_numpy())
    return {"n_fired": int(n), "n_settled": int(n), "n_win": wins, "n_loss": losses,
            "n_push": pushes, "n_dates": int(bets["game_date"].nunique()),
            "roi": float(bets["profit"].mean()), "ci_lo": lo, "ci_hi": hi, "se": se,
            "mean_fair": float(bets["fair"].mean()),
            "mean_implied": float(bets["implied"].mean()),
            "hit_rate": float(wins / (wins + losses)) if (wins + losses) else np.nan}


def build_grid(priced: pd.DataFrame) -> pd.DataFrame:
    """The full pre-registered grid: every (surface, league, stat, book, side, delta) cell,
    including cells with zero fires."""
    rows = []
    surfaces = [("main", MAIN_STATS, ("over", "under")),
                ("milestone", MILESTONE_STATS, ("yes",)),
                ("rung", RUNG_STATS, ("yes",))]
    for surface, stats_list, sides in surfaces:
        sub_all = priced[priced["surface"] == surface]
        for league, stat in stats_list:
            for book_id, book in BOOKS.items():
                for side in sides:
                    base = sub_all[(sub_all["league"] == league) & (sub_all["stat"] == stat)
                                   & (sub_all["book_id"] == book_id) & (sub_all["side"] == side)]
                    for delta in DELTAS:
                        fired = base[base["gap"] >= delta]
                        st = cell_stats(fired)
                        rows.append({"surface": surface, "league": league, "stat": stat,
                                     "book": book, "book_id": book_id, "side": side,
                                     "delta": delta, "n_scanned": int(len(base)), **st})
    g = pd.DataFrame(rows)
    tested = g[g["n_settled"] > 0]
    z = np.where(tested["se"] > 0, tested["roi"] / tested["se"], np.nan)
    p_one = 1.0 - stats.norm.cdf(z)
    g.loc[tested.index, "p_one_sided"] = p_one
    g.loc[tested.index, "q_bh"] = bh_qvalues(np.nan_to_num(p_one, nan=1.0))
    n_tested = int((g["n_settled"] > 0).sum())
    z_bonf = float(stats.norm.ppf(1.0 - 0.025 / max(n_tested, 1)))
    g["bonf_lo"] = g["roi"] - z_bonf * g["se"]
    g["pop"] = (g["n_settled"] >= POP_MIN_N) & (g["ci_lo"] > 0)
    g["bonf_pop"] = (g["n_settled"] >= POP_MIN_N) & (g["bonf_lo"] > 0)
    return g


def family_views(priced: pd.DataFrame) -> pd.DataFrame:
    """Named pre-registered families: RES-08 unders-at-close (distribution frame) and the
    LAD-04 NHL SOG tail."""
    rows = []

    def add(name, bets, delta):
        st = cell_stats(bets)
        rows.append({"family": name, "delta": delta, **st})

    unders = priced[(priced["surface"] == "main") & (priced["side"] == "under")
                    & (priced["league"].isin(["nba", "nhl"]))]
    for delta in DELTAS:
        add("unders_main_pooled_nba_nhl", unders[unders["gap"] >= delta], delta)
        for (league, stat), sub in unders.groupby(["league", "stat"]):
            add(f"unders_main_{league}_{stat}", sub[sub["gap"] >= delta], delta)
        for league, sub in unders.groupby("league"):
            add(f"unders_main_{league}", sub[sub["gap"] >= delta], delta)
    tail = priced[(priced["surface"] == "milestone") & (priced["stat"] == "sog")
                  & (priced["odds"] >= 180) & (priced["odds"] <= 220)]
    for delta in DELTAS:
        add("sog_tail_pooled", tail[tail["gap"] >= delta], delta)
        for book_id, sub in tail.groupby("book_id"):
            add(f"sog_tail_{BOOKS[int(book_id)]}", sub[sub["gap"] >= delta], delta)
    return pd.DataFrame(rows)


def calibration(priced: pd.DataFrame) -> pd.DataFrame:
    """Per (league, stat): predicted vs realized on all priced quotes (over-side view for
    main lines, exceedance for milestone/rung), and on the fired slice at delta = 3pp."""
    rows = []
    for (league, stat), sub in priced.groupby(["league", "stat"]):
        over = sub[(sub["surface"] == "main") & (sub["side"] == "over")]
        exc = sub[sub["surface"] != "main"]
        base = over if len(over) else exc
        if len(base):
            decided = base[base["outcome"] != "push"]
            rows.append({"league": league, "stat": stat, "slice": "all_priced",
                         "n": int(len(decided)),
                         "mean_pred": float(decided["fair"].mean()),
                         "realized": float((decided["outcome"] == "win").mean())})
        fired = sub[sub["gap"] >= 0.03]
        if len(fired):
            decided = fired[fired["outcome"] != "push"]
            rows.append({"league": league, "stat": stat, "slice": "fired_d3",
                         "n": int(len(decided)),
                         "mean_pred": float(decided["fair"].mean()),
                         "realized": float((decided["outcome"] == "win").mean())})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dists", required=True)
    ap.add_argument("--quotes", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    dists = pd.read_parquet(args.dists)
    quotes = pd.read_parquet(args.quotes)
    cov = {"quotes_in": int(len(quotes)),
           "quotes_by_league_stat": {f"{k[0]}/{k[1]}": int(v) for k, v in
                                     quotes.groupby(["league", "stat"]).size().items()}}
    q = quotes[quotes["book_id"].isin(BOOKS)].copy()
    cov["excluded_other_book"] = int(cov["quotes_in"] - len(q))
    before = len(q)
    q = q[q["odds"].notna() & (q["odds"] != 0)]
    cov["excluded_null_odds"] = int(before - len(q))
    d = dists.rename(columns={"date": "game_date", "family": "dist_family"})
    joined = q.merge(d[["league", "stat", "game_date", "name_key", "mu", "disp",
                        "dist_family", "y"]],
                     on=["league", "stat", "game_date", "name_key"], how="inner")
    cov["no_distribution_or_outcome"] = int(len(q) - len(joined))
    cov["priced"] = int(len(joined))

    priced = price_frame(joined)
    priced.to_parquet(os.path.join(args.out, "priced.parquet"), index=False)

    grid = build_grid(priced)
    grid.to_csv(os.path.join(args.out, "grid.csv"), index=False)
    fams = family_views(priced)
    fams.to_csv(os.path.join(args.out, "families.csv"), index=False)
    cal = calibration(priced)
    cal.to_csv(os.path.join(args.out, "calibration.csv"), index=False)

    fired3 = priced[priced["gap"] >= 0.03]
    fired3.to_parquet(os.path.join(args.out, "bets_d3.parquet"), index=False)

    pops = grid[grid["pop"]].sort_values("ci_lo", ascending=False)
    near = grid[(grid["n_settled"] >= POP_MIN_N) & (~grid["pop"])].sort_values(
        "ci_lo", ascending=False).head(15)
    summary = {
        "coverage": cov,
        "cells_total": int(len(grid)),
        "cells_tested": int((grid["n_settled"] > 0).sum()),
        "cells_n_ge_100": int((grid["n_settled"] >= POP_MIN_N).sum()),
        "pops": pops.drop(columns=["p_one_sided", "q_bh"], errors="ignore")
                    .where(pd.notna(pops), None).to_dict("records"),
        "pops_bonferroni": int(grid["bonf_pop"].sum()),
        "nearest": near.drop(columns=["p_one_sided", "q_bh"], errors="ignore")
                       .where(pd.notna(near), None).to_dict("records"),
        "families": fams.where(pd.notna(fams), None).to_dict("records"),
        "calibration": cal.where(pd.notna(cal), None).to_dict("records"),
    }
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print(json.dumps({"cells_total": summary["cells_total"],
                      "cells_tested": summary["cells_tested"],
                      "cells_n_ge_100": summary["cells_n_ge_100"],
                      "pops": len(summary["pops"]),
                      "pops_bonferroni": summary["pops_bonferroni"],
                      "priced": cov["priced"]}, indent=1))


if __name__ == "__main__":
    main()
