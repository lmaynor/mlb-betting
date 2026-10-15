"""Leakage-proof walk-forward evaluation of the NFL prop models.

    python -m nfl.models.walkforward --stats pass_yds,rush_yds,rec_yds,receptions,pass_tds,anytime_td \\
        --seasons 2021-2026 [--features-cache /path/feat.parquet] [--out report.md]

For each test season S: train on seasons < S ONLY (boosting rounds from early stopping on season S-1, dispersion
from walk-forward OOF residuals inside the training window), score season S. 2021..2025 are full seasons; the
current season (2026) is reported separately as to-date.

!!! THERE ARE NO HISTORICAL NFL PROP LINES IN THIS REPO !!!
This report measures PREDICTIVE ACCURACY and CALIBRATION against outcomes and against trailing-mean baselines. It
does NOT measure betting edge. "Proxy lines" are the baseline prediction rounded to the nearest x.5 -- a stand-in
for where a book might hang a number, NOT a real line (real books shade, move, and carry vig). Edge can only be
assessed against live / banked market lines (built elsewhere).

Baselines (pre-game, no fitted parameters except a dispersion, fitted on the same training seasons):
  L5   = mean of the player's previous 5 appearances, SZN = season-to-date mean, EWM = halflife-4-game EWM.
Baseline means are floored at 10% of the training-mean outcome (a 0.0 trailing mean is degenerate under any
distribution). Comparison rows are restricted to eligible test rows where all three baselines exist, so every method is scored
on the SAME rows.
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import mean_gamma_deviance, mean_poisson_deviance, mean_tweedie_deviance

from nfl.models import props as P

logger = logging.getLogger(__name__)

BASE_COL = {"pass_yds": "pass_yds", "rush_yds": "rush_yds", "rec_yds": "rec_yds", "receptions": "rec",
            "pass_tds": "pass_tds", "anytime_td": "td_count",
            "pass_attempts": "pass_att", "completions": "pass_cmp", "rush_attempts": "carries"}
BASELINES = ("l5", "szn", "ewm")
MIN_ROWS_FOR_TEST = 150


def proxy_line(base_mu, stat: str) -> np.ndarray:
    """Nearest x.5 to the baseline mean (>= 0.5). anytime_td is always 0.5 (the market is Yes/No)."""
    b = np.asarray(base_mu, dtype=float)
    if stat == "anytime_td":
        return np.full_like(b, 0.5)
    return np.maximum(np.round(b - 0.5) + 0.5, 0.5)


def _deviances(spec, y, mu):
    mu = np.clip(mu, P.MU_FLOOR, None)
    out = {"mae": float(np.mean(np.abs(y - mu))), "pdev": float(mean_poisson_deviance(y, mu))}
    if spec.kind == "yards":
        out["tdev"] = float(mean_tweedie_deviance(y, mu, power=1.5))
        pos = y > 0
        out["gdev"] = float(mean_gamma_deviance(y[pos], mu[pos])) if pos.sum() > 10 else float("nan")
    return out


def _reliability(p: np.ndarray, o: np.ndarray, bins=(0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0001)) -> list:
    rows = []
    idx = np.digitize(p, bins) - 1
    for b in range(len(bins) - 1):
        m = idx == b
        if m.sum() >= 20:
            rows.append((f"{bins[b]:.2f}-{min(bins[b + 1], 1):.2f}", int(m.sum()), float(p[m].mean()),
                         float(o[m].mean())))
    return rows


def _ece(p, o) -> float:
    rel = _reliability(p, o)
    n = sum(r[1] for r in rel)
    return float(sum(r[1] * abs(r[2] - r[3]) for r in rel) / n) if n else float("nan")


def eval_season(df: pd.DataFrame, stat: str, test_season: int, family: str = None, overrides: dict = None,
                include_experimental: bool = False):
    """Train on seasons < test_season, return (row-level result frame, model)."""
    spec = P.SPECS[stat]
    train = df[df["season"] < test_season]
    model = P.train_stat(train, stat, family=family, overrides=overrides, include_experimental=include_experimental)
    fam = model.dispersion["family"]
    bcol = BASE_COL[stat]
    te = P.training_rows(df[df["season"] == test_season], stat)
    ok = np.ones(len(te), dtype=bool)
    for b in BASELINES:
        ok &= te[f"{bcol}_{b}"].notna().to_numpy()
    te = te[ok]
    if len(te) < MIN_ROWS_FOR_TEST:
        return None, model
    y = P._y(te, spec)
    res = pd.DataFrame({"season": test_season, "y": y, "mu_model": P.predict_mu(model, te)}, index=te.index)
    tr_all = P.training_rows(train, stat)
    tr_all = tr_all[np.all([tr_all[f"{bcol}_{b}"].notna() for b in BASELINES], axis=0)]
    ytr = P._y(tr_all, spec)
    disp = {"model": model.dispersion}
    # A trailing mean of exactly 0 is a degenerate prediction (infinite loss on any non-zero outcome). Give the
    # baselines a floor of 10% of the training-mean outcome -- generous to them, so beating them is not an artefact.
    floor = max(0.1 * float(ytr.mean()), P.MU_FLOOR)
    for b in BASELINES:
        res[f"mu_{b}"] = np.clip(te[f"{bcol}_{b}"].to_numpy(dtype=float), floor, None)
        disp[b] = P.fit_dispersion(fam, np.clip(tr_all[f"{bcol}_{b}"].to_numpy(dtype=float), floor, None), ytr)
    # log scores under each method's own distribution
    res["ls_model"] = _row_logscore(fam, res["mu_model"], y, disp["model"])
    for b in BASELINES:
        res[f"ls_{b}"] = _row_logscore(fam, res[f"mu_{b}"], y, disp[b])
    for fam_name, alt in (model.meta.get("alt_dispersions") or {}).items():
        res[f"ls_model_fam_{fam_name}"] = _row_logscore(fam_name, res["mu_model"], y, alt)
    # proxy lines + P(over)
    line = proxy_line(res["mu_l5"], stat)
    res["line"] = line
    res["over"] = (y > line).astype(float)
    res["p_model"] = P.p_over(stat, res["mu_model"], line, disp["model"])[0]
    for b in BASELINES:
        res[f"p_{b}"] = P.p_over(stat, res[f"mu_{b}"], line, disp[b])[0]
    res.attrs["family"] = fam
    res["family"] = fam
    res.attrs["dispersion"] = disp
    return res, model


def _row_logscore(family, mu, y, params) -> np.ndarray:
    return P.row_logscore(family, mu, y, params)


def market_compare(res: pd.DataFrame, df: pd.DataFrame, stat: str, consensus: pd.DataFrame, resolver,
                   min_books: int = 3) -> dict:
    """OPTIONAL model-vs-REAL-market hook (the default path never calls it). Joins one season's `eval_season` result to
    the historical BettingPros consensus (mlb_core.odds.bettingpros_loader) and scores the model at the market's own line.

    Returns {"coverage": {matched / dropped-by-reason, see nfl.props.history_join}, "scores": {Brier, log-loss for model
    and market, blend c}, "joined": frame}. Identity is fail-closed (nfl.props.resolve); unresolved rows are dropped and
    counted. Selection safety: the consensus is the last pre-start quote BettingPros held for the day, and `res` comes
    from a model trained on earlier seasons only. Not an edge claim -- CLV is the go/no-go."""
    from nfl.props import history_join as HJ
    joined, cov = HJ.join_consensus(res, df, consensus, resolver, stat, min_books=min_books)
    return {"coverage": cov, "scores": HJ.score(joined), "joined": joined}


def eval_season_vs_market(df: pd.DataFrame, stat: str, test_season: int, consensus: pd.DataFrame, resolver,
                          min_books: int = 3, family: str = None, overrides: dict = None) -> dict:
    """eval_season + market_compare in one call ({"coverage": None, ...} when the season has too few eligible rows)."""
    res, _ = eval_season(df, stat, test_season, family=family, overrides=overrides)
    if res is None:
        return {"coverage": None, "scores": None, "joined": None}
    return market_compare(res, df, stat, consensus, resolver, min_books=min_books)


def render_market_section(market: dict) -> str:
    """Markdown for the optional section: market = {stat: {season: market_compare result or None}}."""
    from mlb_core.odds import model_vs_market as MVM
    from nfl.props import history_join as HJ
    L = ["\n## Model vs historical BettingPros market (OPTIONAL; real per-book lines)\n",
         "Consensus = median of de-vigged two-sided books (no Kalshi / DFS / BettingPros-consensus rows) at the modal "
         "line, the LAST quote BettingPros held for the day. Model scored at that line; pushes removed. Brier / "
         "log-loss lower = better; blend `c` > 0 (significant) = the model adds information beyond the market. This is "
         "calibration vs a market, NOT CLV and NOT an edge claim.\n"]
    for stat, by_season in market.items():
        L.append(f"### {stat}")
        frames = []
        for s in sorted(by_season):
            r = by_season[s]
            if r is None or r["coverage"] is None:
                L.append(f"- {s}: too few eligible model rows")
                continue
            L.append(MVM.render_coverage(str(s), r["coverage"]))
            L.append(MVM.render_scores(str(s), r["scores"]))
            frames.append(r["joined"])
        if frames:
            L.append(MVM.render_scores("pooled", HJ.score(pd.concat(frames, ignore_index=True))))
        L.append("")
    return "\n".join(L) + "\n"


def summarize(res: pd.DataFrame, stat: str) -> dict:
    spec = P.SPECS[stat]
    y = res["y"].to_numpy()
    out = {"n": len(res), "mean_y": float(y.mean()), "over_rate": float(res["over"].mean()),
           "family": str(res["family"].iloc[0]) if "family" in res else None}
    for name in ("model", *BASELINES):
        d = _deviances(spec, y, res[f"mu_{name}"].to_numpy())
        out[f"mu_{name}"] = float(res[f"mu_{name}"].mean())
        for k, v in d.items():
            out[f"{k}_{name}"] = v
        out[f"ls_{name}"] = float(res[f"ls_{name}"].mean())
        p = res[f"p_{name}"].to_numpy()
        o = res["over"].to_numpy()
        out[f"brier_{name}"] = float(np.mean((p - o) ** 2))
        pc = np.clip(p, 1e-6, 1 - 1e-6)
        out[f"logloss_{name}"] = float(-np.mean(o * np.log(pc) + (1 - o) * np.log(1 - pc)))
        out[f"ece_{name}"] = _ece(p, o)
    for c in res.columns:
        if c.startswith("ls_model_fam_"):
            out[c] = float(res[c].mean())
    # paired differences vs L5 (positive = model better)
    d_abs = np.abs(y - res["mu_l5"]) - np.abs(y - res["mu_model"])
    d_ls = res["ls_model"] - res["ls_l5"]
    d_br = (res["p_l5"] - res["over"]) ** 2 - (res["p_model"] - res["over"]) ** 2
    for k, d in (("abs", d_abs), ("ls", d_ls), ("brier", d_br)):
        d = np.asarray(d, float)
        se = d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else float("nan")
        out[f"t_{k}_vs_l5"] = float(d.mean() / se) if se and se > 1e-12 else float("nan")
    return out


def _fmt(x, nd=3):
    return "nan" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def render_report(results: dict, params: dict) -> str:
    L = []
    L.append("# NFL prop models -- walk-forward report\n")
    L.append("> **NO HISTORICAL NFL PROP LINES EXIST IN THIS REPO.** Everything below measures predictive accuracy "
             "and calibration versus outcomes and versus trailing-mean baselines. It does **NOT** measure betting "
             "edge. 'Proxy lines' = baseline L5 mean rounded to the nearest x.5 (anytime TD: 0.5); they are not "
             "real market numbers. Edge can only be assessed against live / banked market lines.\n")
    L.append("> Population: eligible players (trailing-usage thresholds), CONDITIONAL ON THE PLAYER PLAYING "
             "offense in that game. Train = all earlier seasons; test rows = eligible rows with L5, SZN and EWM "
             "baselines all defined (same rows for every method).\n")
    L.append("Note: ECE on a single season carries sampling noise of roughly 0.02-0.04 (small bins); read the pooled "
             "reliability table and the t-statistics, not single-season ECE.\n")
    L.append("Parameters: " + str({k: v for k, v in params.items() if k != "pooled"}) + "\n")
    for stat, by_season in results.items():
        spec = P.SPECS[stat]
        L.append(f"\n## {stat}  (target `{spec.target}`, {spec.objective}, dist=`{P.SPECS[stat].family}`)\n")
        hdr = ("| test season | n | mean y | mu model / L5 / SZN / EWM | MAE model / L5 / SZN / EWM | "
               "Poisson dev model / L5 / SZN / EWM |")
        L.append(hdr)
        L.append("|---|---|---|---|---|---|")
        pooled = []
        for s in sorted(by_season):
            r = by_season[s]
            if r is None:
                L.append(f"| {s} | (too few eligible rows) | | | | |")
                continue
            lab = f"{s} (to date)" if s == params.get("partial_season") else str(s)
            L.append(f"| {lab} | {r['n']} | {_fmt(r['mean_y'])} | "
                     + " / ".join(_fmt(r[f'mu_{m}'], 2) for m in ("model", *BASELINES)) + " | "
                     + " / ".join(_fmt(r[f'mae_{m}']) for m in ("model", *BASELINES)) + " | "
                     + " / ".join(_fmt(r[f'pdev_{m}']) for m in ("model", *BASELINES)) + " |")
        L.append("")
        L.append("| test season | mean log-score model / L5 / SZN / EWM (higher=better) | "
                 + ("Tweedie1.5 dev model / L5 / SZN / EWM | " if spec.kind == "yards" else "")
                 + "t(abs err vs L5) | t(logscore vs L5) |")
        L.append("|---|---|" + ("---|" if spec.kind == "yards" else "") + "---|---|")
        for s in sorted(by_season):
            r = by_season[s]
            if r is None:
                continue
            L.append(f"| {s} | " + " / ".join(_fmt(r[f'ls_{m}']) for m in ("model", *BASELINES)) + " | "
                     + ((" / ".join(_fmt(r[f'tdev_{m}']) for m in ("model", *BASELINES)) + " | ")
                        if spec.kind == "yards" else "")
                     + f"{_fmt(r['t_abs_vs_l5'], 1)} | {_fmt(r['t_ls_vs_l5'], 1)} |")
        L.append("\n**P(over) at PROXY lines** (Brier / logloss / ECE; lower=better; model vs L5-baseline distribution)\n")
        L.append("| test season | over rate | Brier model / L5 / SZN | logloss model / L5 / SZN | ECE model / L5 | t(Brier vs L5) |")
        L.append("|---|---|---|---|---|---|")
        for s in sorted(by_season):
            r = by_season[s]
            if r is None:
                continue
            L.append(f"| {s} | {_fmt(r['over_rate'])} | "
                     + " / ".join(_fmt(r[f'brier_{m}'], 4) for m in ("model", "l5", "szn")) + " | "
                     + " / ".join(_fmt(r[f'logloss_{m}'], 4) for m in ("model", "l5", "szn")) + " | "
                     + f"{_fmt(r['ece_model'], 4)} / {_fmt(r['ece_l5'], 4)} | {_fmt(r['t_brier_vs_l5'], 1)} |")
        pooled_df = params["pooled"].get(stat)
        if pooled_df is not None:
            ps = summarize(pooled_df, stat)
            L.append(f"\n**Pooled test seasons {params['pooled_seasons']}** (n={ps['n']}): "
                     f"MAE model {_fmt(ps['mae_model'])} vs L5 {_fmt(ps['mae_l5'])} / SZN {_fmt(ps['mae_szn'])} / EWM "
                     f"{_fmt(ps['mae_ewm'])}; Poisson dev {_fmt(ps['pdev_model'])} vs {_fmt(ps['pdev_l5'])} / "
                     f"{_fmt(ps['pdev_szn'])} / {_fmt(ps['pdev_ewm'])}; log-score {_fmt(ps['ls_model'])} vs "
                     f"{_fmt(ps['ls_l5'])} / {_fmt(ps['ls_szn'])} / {_fmt(ps['ls_ewm'])}; "
                     f"Brier@proxy {_fmt(ps['brier_model'], 4)} vs L5 {_fmt(ps['brier_l5'], 4)}; "
                     f"t(abs err)={_fmt(ps['t_abs_vs_l5'], 1)}, t(logscore)={_fmt(ps['t_ls_vs_l5'], 1)}, "
                     f"t(Brier)={_fmt(ps['t_brier_vs_l5'], 1)} (positive t = model better than L5).")
            fam_cols = sorted(k for k in ps if k.startswith("ls_model_fam_"))
            if fam_cols:
                chosen = {s: (results[stat][s] or {}).get("family") for s in results[stat]}
                L.append("\nDistribution-family check (same mu, pooled mean log-score): "
                         + ", ".join(f"`{c[len('ls_model_fam_'):]}` {_fmt(ps[c])}" for c in fam_cols)
                         + f"; family auto-selected per season on OOF: {chosen}.")
            L.append("\nPooled reliability of model P(over) at proxy lines (bin, n, mean predicted, observed):\n")
            L.append("| bin | n | predicted | observed |")
            L.append("|---|---|---|---|")
            for b, n, pm, om in _reliability(pooled_df["p_model"].to_numpy(), pooled_df["over"].to_numpy()):
                L.append(f"| {b} | {n} | {pm:.3f} | {om:.3f} |")
            wins = [s for s in by_season if by_season[s] is not None and s != params.get("partial_season")]
            nb = sum(1 for s in wins if by_season[s]["mae_model"] < by_season[s]["mae_l5"])
            nd = sum(1 for s in wins if by_season[s]["ls_model"] > by_season[s]["ls_l5"])
            nbr = sum(1 for s in wins if by_season[s]["brier_model"] < by_season[s]["brier_l5"])
            L.append(f"\nFull-season wins vs L5 baseline: MAE {nb}/{len(wins)}, log-score {nd}/{len(wins)}, "
                     f"Brier@proxy {nbr}/{len(wins)}.")
    return "\n".join(L) + "\n"


def parse_seasons(spec: str) -> list:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stats", default=",".join(P.DEFAULT_STATS), help="available: " + ",".join(P.SPECS))
    ap.add_argument("--experimental-features", action="store_true",
                    help="also feed the teammate-absence usage-share features to the models (default: off)")
    ap.add_argument("--seasons", default="2021-2026", help="test seasons; the newest is reported as to-date")
    ap.add_argument("--features-cache", default=None, help="parquet path: load if exists, else build + save")
    ap.add_argument("--out", default=None, help="write the markdown report here")
    ap.add_argument("--no-pbp", action="store_true")
    ap.add_argument("--market-parquet", default=None,
                    help="OPTIONAL: BettingPros consensus parquet (scripts/join_bettingpros_history.py --sport nfl). "
                         "Adds a model-vs-real-market section; omitted = report unchanged")
    ap.add_argument("--market-min-books", type=int, default=3)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import os
    stats = [s.strip() for s in args.stats.split(",") if s.strip()]
    test_seasons = parse_seasons(args.seasons)
    if args.features_cache and os.path.exists(args.features_cache):
        df = pd.read_parquet(args.features_cache)
    else:
        from nfl.features.props_features import build_player_week_features
        df = build_player_week_features(range(2018, max(test_seasons) + 1), use_pbp=not args.no_pbp)
        if args.features_cache:
            df.to_parquet(args.features_cache)
    df["is_upcoming"] = df["is_upcoming"].astype(bool)
    last = int(df["season"].max())
    full_weeks = df[df["season"] == last]["week"].max()
    partial = last if last in test_seasons and full_weeks < 17 else None

    market, cons, resolver = {}, None, None
    if args.market_parquet:
        from nfl.props.resolve import PlayerResolver
        cons = pd.read_parquet(args.market_parquet)
        resolver = PlayerResolver.from_mirror(range(2018, max(test_seasons) + 1))
    results, pooled = {}, {}
    for stat in stats:
        results[stat] = {}
        market[stat] = {}
        frames = []
        for s in test_seasons:
            res, _ = eval_season(df, stat, s, include_experimental=args.experimental_features)
            if res is None:
                results[stat][s] = None
                market[stat][s] = None
                continue
            if cons is not None:
                market[stat][s] = market_compare(res, df, stat, cons, resolver, min_books=args.market_min_books)
            results[stat][s] = summarize(res, stat)
            if s != partial:
                frames.append(res)
            logger.info("%s %s done n=%d", stat, s, len(res))
        pooled[stat] = pd.concat(frames, ignore_index=True) if frames else None
    full = [s for s in test_seasons if s != partial]
    report = render_report(results, {"train": "all seasons < test season", "test_seasons": test_seasons,
                                     "partial_season": partial, "pooled": pooled,
                                     "pooled_seasons": f"{full[0]}-{full[-1]}" if full else "-"})
    if cons is not None:
        report += render_market_section(market)
    print(report)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
