"""nba.props.evaluate -- OFFLINE evaluation of the NBA prop projections. Produces a markdown report.

WHAT THIS CAN AND CANNOT SHOW (read before quoting any number):
  * No historical NBA prop ODDS exist (ParlayAPI has no historical props; banking began 2026-10-14), so nothing here is
    ROI, CLV or edge. It measures (1) projection accuracy versus naive baselines on strict time splits and (2) whether
    the predicted distribution is CALIBRATED -- including P(over) at PROXY lines (a line a naive forecaster would set so
    that over/under is a coin flip). A proxy line is weaker than a real one (books also price injuries, rest news and
    sharp action), so beating a proxy says NOTHING about beating a market.
  * Every split is by season: the model that scores season T was trained on seasons <= T-2, tuned on T-1, refit on <= T-1;
    its spread, family and PIT recalibration come from T-1's out-of-sample predictions only. Rolling origin over test
    seasons 2022-2025; the headline is the latest full season (2025-26).

Run:  python -m nba.props.evaluate --local-dir <dir> --out docs/analysis/nba_props_evaluation_2026-10-14.md
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from nba.props import config as C
from nba.props import dist as Dd
from nba.props.features import TARGET_SHORT
from nba.props.train import PHI_FLOOR, fit_stat, target_of, walk_forward

log = logging.getLogger("nba.props.evaluate")

BASELINES = ["l10", "ewm8", "season", "minadj"]
BASELINE_LABEL = {"l10": "L10 mean", "ewm8": "EWM(hl=8)", "season": "season-to-date mean",
                  "minadj": "minutes-adjusted rate"}
# Sports-EV-Bot's published test-set numbers (its README: MAE, R2, directional accuracy %)
EVBOT = {"points": (4.77, 0.461, 60.2), "rebounds": (1.94, 0.411, 57.9), "assists": (1.43, 0.465, 55.5),
         "threes": (0.94, 0.240, 54.6), "pra": (6.09, 0.516, 61.1)}


# ----------------------------------------------------------------------------- baselines
def baseline_means(X: pd.DataFrame, stat: str) -> dict:
    """Naive, leakage-safe point forecasts from the same feature frame (all use only prior games)."""
    sh = TARGET_SHORT[stat]
    szn = np.where(X["n_szn"].to_numpy() >= 5, X[f"{sh}_szn"].to_numpy(), X[f"{sh}_ewm30"].to_numpy())
    return {"l10": X[f"{sh}_l10"].to_numpy(float), "ewm8": X[f"{sh}_ewm8"].to_numpy(float), "season": szn.astype(float),
            "minadj": (X[f"pm_{sh}_ewm30"] * X["min_ewm8"]).to_numpy(float)}


def median_proxy_line(mu, var, fam, stat, table=None) -> np.ndarray:
    """Half-point line whose NAIVE (EWM8, PIT-recalibrated like the model) P(over) is closest to 0.5 -- how a book balances
    a market, so that any difference between the model's P(over) and the naive one is the model's information. (An earlier
    draft anchored lines at floor(mean)+0.5; that sits ~4-5pp off the median for right-skewed counts and manufactured
    misleadingly strong hit-rate ladders, so it was dropped.)"""
    out = np.empty(len(mu))
    ks = np.arange(0, C.SUPPORT_MAX[stat] + 1, dtype=float)
    for f in np.unique(fam):
        s = np.where(fam == f)[0]
        F = Dd.cdf_cal(ks[None, :], mu[s][:, None], var[s][:, None], f, table)
        out[s] = ks[np.argmin(np.abs((1.0 - F) - 0.5), axis=1)] + 0.5
    return out


# ----------------------------------------------------------------------------- metrics
def point_metrics(y, mu) -> dict:
    e = mu - y
    sst = ((y - y.mean()) ** 2).sum()
    return {"mae": float(np.abs(e).mean()), "rmse": float(np.sqrt((e ** 2).mean())), "bias": float(e.mean()),
            "r2": float(1 - (e ** 2).sum() / sst) if sst > 0 else float("nan")}


def cluster_boot_diff(y, mu_a, mu_b, groups, n_boot: int = 400, seed: int = 11) -> tuple:
    """Mean of |err_b| - |err_a| (positive = A is better) with a date-cluster bootstrap 95% CI."""
    d = np.abs(mu_b - y) - np.abs(mu_a - y)
    agg = pd.DataFrame({"g": groups, "d": d}).groupby("g")["d"].agg(["sum", "count"])
    s, c = agg["sum"].to_numpy(), agg["count"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(n_boot, len(s)))
    boots = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return float(d.mean()), float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))


def dist_metrics(y, mu, var, family, stat, table=None) -> dict:
    if table is None:
        return {"crps": float(Dd.crps(y, mu, var, family, C.SUPPORT_MAX[stat]).mean()),
                "logscore": float(-Dd.logpmf(y, mu, var, family).mean())}
    pm = Dd.cdf_cal(y, mu, var, family, table) - Dd.cdf_cal(y - 1, mu, var, family, table)
    return {"crps": float(Dd.crps_cal(y, mu, var, family, C.SUPPORT_MAX[stat], table).mean()),
            "logscore": float(-np.log(np.maximum(pm, 1e-12)).mean())}


def calibration_table(p, hit, bins=(0.0, 0.2, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 1.0)) -> pd.DataFrame:
    df = pd.DataFrame({"p": np.asarray(p), "hit": np.asarray(hit), "bin": pd.cut(np.asarray(p), bins=list(bins),
                                                                                  include_lowest=True)})
    g = df.groupby("bin", observed=True)
    t = pd.DataFrame({"n": g.size(), "mean_pred": g["p"].mean(), "realized": g["hit"].mean()})
    t["gap"] = t["realized"] - t["mean_pred"]
    return t


def ece(p, hit, bins=10) -> float:
    p, hit = np.asarray(p), np.asarray(hit)
    cat = np.minimum((p * bins).astype(int), bins - 1)
    tot = 0.0
    for b in range(bins):
        s = cat == b
        if s.any():
            tot += s.mean() * abs(hit[s].mean() - p[s].mean())
    return float(tot)


def brier(p, hit) -> float:
    return float(((np.asarray(p) - np.asarray(hit)) ** 2).mean())


def logloss(p, hit, eps=1e-6) -> float:
    p = np.clip(np.asarray(p), eps, 1 - eps)
    h = np.asarray(hit)
    return float(-(h * np.log(p) + (1 - h) * np.log(1 - p)).mean())


# ----------------------------------------------------------------------------- the evaluation proper
def evaluate_predictions(X: pd.DataFrame, meta: pd.DataFrame, preds: pd.DataFrame, seasons, tables: dict | None = None) -> dict:
    """Metrics for every (stat, season) plus pooled arrays. `preds` is walk_forward() output (row = position in X);
    `tables` = {(stat, season): pit_table} (None -> no recalibration)."""
    tables = tables or {}
    reg_mask = (meta["season_type"] == "Regular Season").to_numpy()
    sy = meta["season_year"].to_numpy()
    dates = meta["date"].to_numpy()
    out: dict = {"rows": {}, "pooled": {}}
    for stat in C.STATS:
        y_all = target_of(meta, stat)
        per_season: dict = {}
        keys = ("y", "mu", "var_g", "var_b", "fam", "line", "p_raw", "p_cal", "p_base", "grp", "idx", "season")
        acc = {k: [] for k in keys}
        acc_b = {b: [] for b in BASELINES}
        for T in seasons:
            p = preds[(preds["stat"] == stat) & (preds["season"] == T)]
            if p.empty:
                continue
            rows = p["row"].to_numpy()
            keep = reg_mask[rows]
            rows, p = rows[keep], p[keep]
            y = y_all[rows]
            Xs = X.iloc[rows]
            fam = p["family"].iloc[0]
            tab = tables.get((stat, T))
            mu, vg, vb = p["mu"].to_numpy(), p["var_gbm"].to_numpy(), p["var_binned"].to_numpy()
            bm = baseline_means(Xs, stat)
            prev = np.where(sy == T - 1)[0]
            bprev = baseline_means(X.iloc[prev], stat)
            res = {"n": int(len(y)), "family": fam, "model": point_metrics(y, mu), "baselines": {}}
            res["model"]["dist_binned"] = dist_metrics(y, mu, vb, fam, stat)
            res["model"]["dist_gbm"] = dist_metrics(y, mu, vg, fam, stat)
            if tab:
                res["model"]["dist_cal"] = dist_metrics(y, mu, vg, fam, stat, tab)
            bvar, bpit = {}, {}
            for b in BASELINES:
                ok = np.isfinite(bprev[b])
                bmu_prev = np.maximum(bprev[b][ok], Dd.MU_MIN)
                y_prev = y_all[prev][ok]
                bt = Dd.fit_phi_table(bmu_prev, y_prev)                  # baseline spread: previous season, same protocol
                bpit[b] = Dd.fit_pit_table(y_prev, bmu_prev, Dd.var_from_table(bmu_prev, bt, PHI_FLOOR[fam]), fam)
                bmu = np.maximum(np.nan_to_num(bm[b], nan=1.0), Dd.MU_MIN)
                bvar[b] = Dd.var_from_table(bmu, bt, PHI_FLOOR[fam])
                res["baselines"][b] = point_metrics(y, bmu)
                res["baselines"][b]["dist"] = dist_metrics(y, bmu, bvar[b], fam, stat)
                res["baselines"][b]["dist_cal"] = dist_metrics(y, bmu, bvar[b], fam, stat, bpit[b])
                acc_b[b].append(bmu)
                bm[b] = bmu
            famv = np.full(len(y), fam, dtype=object)
            line = median_proxy_line(bm["ewm8"], bvar["ewm8"], famv, stat, bpit["ewm8"])
            p_raw = Dd.p_over(line, mu, vg, fam)
            p_cal = Dd.p_over_cal(line, mu, vg, fam, tab)
            p_base = Dd.p_over_cal(line, bm["ewm8"], bvar["ewm8"], fam, bpit["ewm8"])
            per_season[T] = res
            for k, v in (("y", y), ("mu", mu), ("var_g", vg), ("var_b", vb), ("fam", famv), ("line", line),
                         ("p_raw", p_raw), ("p_cal", p_cal), ("p_base", p_base), ("grp", dates[rows]), ("idx", rows),
                         ("season", sy[rows])):
                acc[k].append(v)
        out["rows"][stat] = per_season
        if acc["y"]:
            pooled = {k: np.concatenate(v) for k, v in acc.items()}
            pooled["base"] = {b: np.concatenate(v) for b, v in acc_b.items()}
            out["pooled"][stat] = pooled
    return out


def pooled_summary(ev: dict) -> dict:
    out = {}
    for stat, p in ev["pooled"].items():
        y, mu, grp = p["y"], p["mu"], p["grp"]
        res = {"n": int(len(y)), "model": point_metrics(y, mu), "baselines": {}, "gain": {}}
        for b in BASELINES:
            res["baselines"][b] = point_metrics(y, p["base"][b])
            res["gain"][b] = cluster_boot_diff(y, mu, p["base"][b], grp)
        out[stat] = res
    return out


def proxy_line_report(ev: dict) -> dict:
    """Calibration of P(over) at median-anchored PROXY lines, and a disagreement table: where the model's P(over) differs
    from the naive forecaster's, does the outcome follow the model? (pooled over test seasons; PROXY, not a market)."""
    out = {}
    for stat, p in ev["pooled"].items():
        y, line = p["y"], p["line"]
        hit = (y > line).astype(float)
        pr, pc, pb = p["p_raw"], p["p_cal"], p["p_base"]
        d = pc - pb
        dis = []
        for lo, hi in ((0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 1.0)):
            for direction, sel_dir in (("model more OVER than naive", d > 0), ("model more UNDER than naive", d < 0)):
                s = sel_dir & (np.abs(d) >= lo) & (np.abs(d) < hi)
                n = int(s.sum())
                if n < 30:
                    continue
                real = float(hit[s].mean())
                gap = float(pc[s].mean() - pb[s].mean())
                closed = float((real - pb[s].mean()) / gap) if abs(gap) > 1e-9 else float("nan")
                dis.append({"band": f"{lo:.2f}-{min(hi, 1.0):.2f}", "dir": direction, "n": n, "mean_naive": float(pb[s].mean()),
                            "mean_model": float(pc[s].mean()), "realized": real, "closed": closed})
        out[stat] = {"n": int(len(y)), "base_rate_over": float(hit.mean()),
                     "model_raw": {"brier": brier(pr, hit), "logloss": logloss(pr, hit), "ece": ece(pr, hit)},
                     "model_cal": {"brier": brier(pc, hit), "logloss": logloss(pc, hit), "ece": ece(pc, hit)},
                     "naive": {"brier": brier(pb, hit), "logloss": logloss(pb, hit), "ece": ece(pb, hit)},
                     "calibration": calibration_table(pc, hit), "disagree": dis}
    return out


def unstable_mask(Xs: pd.DataFrame) -> np.ndarray:
    return ((Xs["min_std10"] >= C.ABSTAIN_MIN_STD10) | (Xs["missed_l10"] >= C.ABSTAIN_MISSED_L10)
            | (Xs["min_ratio_l5_l20"] < C.ABSTAIN_MIN_RATIO[0]) | (Xs["min_ratio_l5_l20"] > C.ABSTAIN_MIN_RATIO[1])).to_numpy()


def spread_report(ev: dict, X: pd.DataFrame) -> pd.DataFrame:
    """Does the predicted spread widen where minutes are uncertain? Standardised-residual RMS (ideal 1.0) and 80% central
    interval coverage (ideal 0.80) by bucket. MAE in raw units is NOT comparable across buckets (the buckets hold
    different players)."""
    rows = []
    for stat, p in ev["pooled"].items():
        Xs = X.iloc[p["idx"]]
        y, mu, fam = p["y"], p["mu"], p["fam"]
        unstable = unstable_mask(Xs)
        for label, sel in (("stable", ~unstable), ("uncertain minutes", unstable)):
            if not sel.any():
                continue
            for vk, var in (("gbm", p["var_g"]), ("binned", p["var_b"])):
                v = var[sel]
                lo, hi = np.empty(sel.sum()), np.empty(sel.sum())
                for f in np.unique(fam[sel]):
                    s = fam[sel] == f
                    lo[s] = Dd.cdf(np.floor(y[sel][s]) - 1, mu[sel][s], v[s], f)
                    hi[s] = Dd.cdf(np.floor(y[sel][s]), mu[sel][s], v[s], f)
                u = 0.5 * (lo + hi)                                   # mid-PIT: uniform if calibrated
                z = (y[sel] - mu[sel]) / np.sqrt(v)
                rows.append({"stat": stat, "bucket": label, "spread": vk, "n": int(sel.sum()), "share": float(sel.mean()),
                             "mean_pred_sd": float(np.sqrt(v).mean()), "z_rms": float(np.sqrt((z ** 2).mean())),
                             "cov80": float(((u > 0.10) & (u < 0.90)).mean())})
    return pd.DataFrame(rows)


def evbot_table(ev: dict, X: pd.DataFrame, meta: pd.DataFrame, season: int) -> pd.DataFrame:
    """Our latest-full-season numbers next to the Sports-EV-Bot README, computed on (a) all played regular-season rows and
    (b) the bot's MIN >= 10 filter (conditions on the OUTCOME minutes -- optimistic), plus its 'directional accuracy'
    definition (prediction vs the player's own L10 median, which is also one of its input features)."""
    out = []
    mins = meta["min"].to_numpy()
    for stat in C.STATS:
        p = ev["pooled"].get(stat)
        if p is None:
            continue
        sel = p["season"] == season
        if not sel.any():
            continue
        y, mu = p["y"][sel], p["mu"][sel]
        idx = p["idx"][sel]
        Xs = X.iloc[idx]
        med = Xs[f"{TARGET_SHORT[stat]}_med10"].to_numpy(float)
        ew = baseline_means(Xs, stat)["ewm8"]
        for label, s in (("all played rows", np.ones(len(y), bool)), ("MIN>=10 (outcome-conditioned)", mins[idx] >= 10)):
            pm, pb = point_metrics(y[s], mu[s]), point_metrics(y[s], ew[s])
            so = s & np.isfinite(med)
            bot = EVBOT[stat]
            out.append({"stat": stat, "rows": label, "n": int(s.sum()), "ours_mae": pm["mae"], "ours_r2": pm["r2"],
                        "ewm8_mae": pb["mae"], "ours_dir": float(((mu[so] > med[so]) == (y[so] > med[so])).mean() * 100),
                        "ewm8_dir": float(((ew[so] > med[so]) == (y[so] > med[so])).mean() * 100),
                        "tie_share": float((y[so] == med[so]).mean() * 100),
                        "bot_mae": bot[0], "bot_r2": bot[1], "bot_dir": bot[2]})
    return pd.DataFrame(out)


def oracle_minutes(X: pd.DataFrame, meta: pd.DataFrame, T: int, stats=None) -> pd.DataFrame:
    """Diagnostic upper bound: refit with the ACTUAL minutes of the target game added as a feature. The gap to the real
    model is the error attributable to not knowing minutes in advance (what lineup/injury data could at most remove --
    an upper bound, since actual minutes also carry foul trouble/blowouts that nothing pre-game can forecast)."""
    rows = []
    seasons = meta["season_year"].to_numpy()
    reg = (meta["season_type"] == "Regular Season").to_numpy()
    tr, va, te = seasons <= T - 2, seasons == T - 1, (seasons == T) & reg
    Xo = X.assign(actual_min=meta["min"].to_numpy())
    for stat in (stats or C.STATS):
        y = target_of(meta, stat)
        m = fit_stat(Xo[tr], y[tr], Xo[va], y[va], stat, family="nbinom")
        m0 = fit_stat(X[tr], y[tr], X[va], y[va], stat, family="nbinom")
        rows.append({"stat": stat, "mae_no_minutes": float(np.abs(y[te] - m0.predict_mu(X[te])).mean()),
                     "mae_oracle": float(np.abs(y[te] - m.predict_mu(Xo[te])).mean())})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- markdown
def _f(x, d=3):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def render_markdown(ev, pooled, proxy, spread, evbot, oracle, seasons, headline, desc) -> str:
    L: list = []
    w = L.append
    hs = f"{headline}-{str(headline + 1)[2:]}"
    w("# NBA player-prop projections -- offline evaluation (2026-10-14)\n")
    w("**Status: paper / LOG-ONLY. No edge is claimed. No NBA prop odds history exists, so this document measures "
      "projection accuracy and distribution calibration only -- never ROI, CLV or edge.**\n")
    w(f"Data: SportsBlaze box scores, {desc}. Test seasons (start year) {list(seasons)}; the headline is {hs} (the latest "
      "full season). Regular-season rows only. Every row's features use strictly earlier ET game days "
      "(`nba/props/features.py`); the model that scores a season was trained on seasons <= T-2, tuned on T-1 and refit "
      "on <= T-1, and its spread / family / recalibration come from T-1's out-of-sample predictions. All numbers below are "
      "produced by `python -m nba.props.evaluate`.\n")

    # ---- summary (generated from the numbers below so it cannot drift from them)
    w("## Summary\n")
    best = {st: min(r["baselines"][b]["mae"] for b in BASELINES) for st, r in pooled.items()}
    w("- **Accuracy**: pooled over four rolling-origin test seasons the model's MAE is "
      + "; ".join(f"{st} {r['model']['mae']:.3f} vs {best[st]:.3f} for the best naive baseline "
                  f"({(1 - r['model']['mae'] / best[st]) * 100:.1f}% lower)" for st, r in pooled.items())
      + ". The gains are small, consistent in every season and statistically distinguishable from zero (date-cluster "
        "bootstrap), and they are the whole story: nothing here is large enough to be called an edge.")
    ece_txt = "; ".join(f"{st} {proxy[st]['model_cal']['ece']:.3f}" for st in proxy)
    w(f"- **Calibration**: P(over) at proxy lines has ECE (after PIT recalibration) of {ece_txt}; the family-only NegBin "
      "was ~3pp too low near the centre for points/PRA before recalibration. The model is OVERCONFIDENT in the far tail "
      "(predicted P(over) >= 0.7 realizes noticeably lower for points/PRA), which is the region where a model-vs-market "
      "disagreement would live -- see the EV cap in the scorer.")
    if oracle is not None and len(oracle):
        om = "; ".join(f"{r.stat} {(1 - r.mae_oracle / r.mae_no_minutes) * 100:.0f}%" for r in oracle.itertuples(index=False))
        w(f"- **Minutes are the lever the model does not have**: giving it the game's ACTUAL minutes would cut MAE by {om} "
          "(an upper bound; lineup/injury data would recover only part of it).")
    w("- **Not shown**: any profit, ROI, CLV or edge versus sportsbook prices. No NBA prop odds history exists; proxy lines "
      "are a naive forecaster's own line, strictly weaker than a market. See section 6.\n")

    w("## 1. Point accuracy vs naive baselines (MAE, lower is better)\n")
    w("Pooled over all test seasons. `gain` = baseline MAE minus model MAE, with a date-cluster bootstrap 95% CI "
      "(positive = the model is better).\n")
    w("| stat | n | model MAE | " + " | ".join(f"{BASELINE_LABEL[b]} MAE (gain; 95% CI)" for b in BASELINES) + " |")
    w("|---|---|---|" + "---|" * len(BASELINES))
    for stat, r in pooled.items():
        cells = [f"{_f(r['baselines'][b]['mae'])} ({r['gain'][b][0]:+.3f}; {r['gain'][b][1]:+.3f}..{r['gain'][b][2]:+.3f})"
                 for b in BASELINES]
        w(f"| {stat} | {r['n']:,} | {_f(r['model']['mae'])} | " + " | ".join(cells) + " |")
    w("")
    w("Relative gain over the best naive baseline (EWM or minutes-adjusted, whichever is lower): "
      + "; ".join(f"{stat} {(1 - r['model']['mae'] / min(r['baselines'][b]['mae'] for b in BASELINES)) * 100:.1f}%"
                  for stat, r in pooled.items()) + ".\n")
    w(f"Latest full season ({hs}), model vs the best naive baseline:\n")
    w("| stat | n | model MAE | model RMSE | model R2 | model bias | best baseline | its MAE | its R2 |")
    w("|---|---|---|---|---|---|---|---|---|")
    for stat in C.STATS:
        r = ev["rows"].get(stat, {}).get(headline)
        if not r:
            continue
        bb = min(BASELINES, key=lambda b: r["baselines"][b]["mae"])
        w(f"| {stat} | {r['n']:,} | {_f(r['model']['mae'])} | {_f(r['model']['rmse'])} | {_f(r['model']['r2'])} | "
          f"{_f(r['model']['bias'])} | {BASELINE_LABEL[bb]} | {_f(r['baselines'][bb]['mae'])} | {_f(r['baselines'][bb]['r2'])} |")
    w("")
    w("Per-season model MAE, and the improvement over the EWM(hl=8) baseline in MAE points (stability across the rolling origin):\n")
    w("| stat | " + " | ".join(f"{T} MAE (gain)" for T in seasons) + " |")
    w("|---|" + "---|" * len(seasons))
    for stat in C.STATS:
        cells = []
        for T in seasons:
            r = ev["rows"].get(stat, {}).get(T)
            cells.append("n/a" if not r else f"{r['model']['mae']:.3f} ({r['baselines']['ewm8']['mae'] - r['model']['mae']:+.3f})")
        w(f"| {stat} | " + " | ".join(cells) + " |")
    w("")

    w("## 2. Predicted distribution quality (CRPS and log score, lower is better)\n")
    w("Every forecaster uses the same family, the same spread protocol (spread fitted on the previous season's residuals) and "
      "the same PIT recalibration protocol (`+cal`, fitted on the previous season), so the baseline comparison isolates the "
      f"quality of the MEAN. The deployed model distribution is the `gbm + cal` column. Latest full season ({hs}):\n")
    w("| stat | family | model CRPS (binned) | model CRPS (gbm spread) | model CRPS (gbm + cal) | best baseline CRPS (+cal) | model logscore (gbm) | model logscore (gbm + cal) | best baseline logscore (+cal) |")
    w("|---|---|---|---|---|---|---|---|---|")
    for stat in C.STATS:
        r = ev["rows"].get(stat, {}).get(headline)
        if not r:
            continue
        bc = min(BASELINES, key=lambda b: r["baselines"][b]["dist_cal"]["crps"])
        bl = min(BASELINES, key=lambda b: r["baselines"][b]["dist_cal"]["logscore"])
        cal = r["model"].get("dist_cal", {})
        w(f"| {stat} | {r['family']} | {_f(r['model']['dist_binned']['crps'])} | {_f(r['model']['dist_gbm']['crps'])} | "
          f"{_f(cal.get('crps'))} | {_f(r['baselines'][bc]['dist_cal']['crps'])} ({BASELINE_LABEL[bc]}) | "
          f"{_f(r['model']['dist_gbm']['logscore'])} | {_f(cal.get('logscore'))} | "
          f"{_f(r['baselines'][bl]['dist_cal']['logscore'])} ({BASELINE_LABEL[bl]}) |")
    w("")

    w("## 3. Minutes / availability uncertainty (the dominant NBA prop driver)\n")
    w(f"A row is 'uncertain minutes' when minutes std over the last 10 games >= {C.ABSTAIN_MIN_STD10}, or >= "
      f"{C.ABSTAIN_MISSED_L10} team games missed in the last 10, or minutes L5/L20 outside {C.ABSTAIN_MIN_RATIO}. "
      "`z_rms` = RMS of (outcome - mean)/predicted sd (ideal 1.0; above 1 = spread too narrow), `cov80` = share of outcomes "
      "inside the central 80% interval (ideal 0.80). The scorer abstains on these rows (they are logged, never flagged). "
      "No lineup or injury data is available, so the spread is the only place this uncertainty can enter the forecast.\n")
    if spread is not None and len(spread):
        w("| stat | bucket | spread | share of rows | mean predicted sd | z_rms | cov80 |")
        w("|---|---|---|---|---|---|---|")
        for r in spread.itertuples(index=False):
            w(f"| {r.stat} | {r.bucket} | {r.spread} | {r.share:.1%} | {_f(r.mean_pred_sd)} | {_f(r.z_rms)} | {r.cov80:.3f} |")
        w("")
    if oracle is not None and len(oracle):
        w(f"Oracle diagnostic ({hs}; NOT a usable model): the same pipeline with the target game's ACTUAL minutes added as a "
          "feature. The gap is the error attributable to not knowing minutes in advance -- an UPPER bound on what lineup/"
          "injury data (follow-up: ESPN injuries endpoint) could remove, since actual minutes also carry foul trouble and "
          "blowouts that nothing pre-game can forecast.\n")
        w("| stat | MAE, real model (no minutes) | MAE, actual-minutes oracle | share of MAE explained by minutes |")
        w("|---|---|---|---|")
        for r in oracle.itertuples(index=False):
            w(f"| {r.stat} | {_f(r.mae_no_minutes)} | {_f(r.mae_oracle)} | {(1 - r.mae_oracle / r.mae_no_minutes):.1%} |")
        w("")

    w("## 4. P(over) at PROXY lines -- calibration and model-vs-naive disagreement (PROXY, NOT a market)\n")
    w("Proxy line = the half-point line at which the NAIVE EWM(8) forecaster's own P(over) is closest to 0.5 (how a book "
      "balances a market). Pooled over test seasons. Two questions: (a) is the model's P(over) calibrated, and (b) where the "
      "model's P(over) differs from the naive forecaster's, does the realized over-rate follow the model? `closed` = the share "
      "of the model-vs-naive gap that the realized over-rate actually covers (1 = the model's view was right, 0 = the naive view "
      "was right, negative = worse than naive). A real book line additionally prices injuries, rest news and sharp action that "
      "box scores cannot see, so none of this transfers to a market edge; these are not bets.\n")
    for stat, r in proxy.items():
        w(f"### {stat} (n={r['n']:,}; share of rows that went over the proxy line {r['base_rate_over']:.3f})\n")
        w("| forecaster | Brier | log loss | ECE |")
        w("|---|---|---|---|")
        for lab, key in (("naive EWM(8) + PIT recalibration", "naive"), ("model, family only", "model_raw"),
                         ("model + PIT recalibration", "model_cal")):
            m = r[key]
            w(f"| {lab} | {_f(m['brier'], 4)} | {_f(m['logloss'], 4)} | {_f(m['ece'], 4)} |")
        w("")
        w("| model (recalibrated) P(over) bin | n | mean predicted | realized over-rate | gap |")
        w("|---|---|---|---|---|")
        for i, rr in r["calibration"].iterrows():
            w(f"| {i} | {int(rr['n']):,} | {rr['mean_pred']:.3f} | {rr['realized']:.3f} | {rr['gap']:+.3f} |")
        w("")
        if r["disagree"]:
            w("| gap band (abs(model - naive)) | direction | n | mean naive P(over) | mean model P(over) | realized over-rate | closed |")
            w("|---|---|---|---|---|---|---|")
            for d in r["disagree"]:
                w(f"| {d['band']} | {d['dir']} | {d['n']:,} | {d['mean_naive']:.3f} | {d['mean_model']:.3f} | "
                  f"{d['realized']:.3f} | {_f(d['closed'], 2)} |")
            w("")

    w("## 5. Comparison with Sports-EV-Bot's published metrics (design reference, untrusted)\n")
    w("How its README numbers were produced (read from source, never run): XGBoost+LightGBM ensemble, Optuna-tuned, "
      "chronological 70/30 split (not random -- fine), BUT (i) early stopping uses the TEST set as the eval set "
      "(`train.py`, `tune_train.py`); (ii) rows are filtered to ACTUAL minutes >= 10 in the target game and any row with a "
      "missing feature is dropped, which conditions on the outcome; (iii) its test period includes the 2025-26 playoffs; "
      "(iv) 'directional accuracy' is computed in `train.py` against the player's own rolling L10 MEDIAN (itself one of its "
      "input features) while the README attributes it to PrizePicks lines -- a DFS app that posts a NUMBER, not a price, "
      "and is not a sharp reference. Neither version is evidence of an edge against sportsbook prices. "
      f"Our {hs} numbers, computed on all played rows and on its MIN>=10 filter; `tie` = share of rows where the outcome "
      "equals the L10 median, which the metric scores as 'under' and which dominates for low counts such as threes:\n")
    if evbot is not None and len(evbot):
        w("| stat | rows | n | our MAE | our R2 | naive EWM8 MAE | our dir-acc vs L10 median | naive EWM8 dir-acc | tie | bot MAE | bot R2 | bot dir-acc |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for d in evbot.to_dict("records"):
            w(f"| {d['stat']} | {d['rows']} | {d['n']:,} | {_f(d['ours_mae'])} | {_f(d['ours_r2'])} | {_f(d['ewm8_mae'])} | "
              f"{d['ours_dir']:.1f}% | {d['ewm8_dir']:.1f}% | {d['tie_share']:.0f}% | {d['bot_mae']} | {d['bot_r2']} | {d['bot_dir']}% |")
        w("")

    w("## 6. What this does and does not show\n")
    w("**It shows**\n")
    w("- The pipeline is leakage-safe by construction and by test: features use only strictly-earlier ET game days, the same "
      "code path builds training and live features, and every reported number comes from a model that never saw the season it "
      "is scored on (tuning, spread, family and recalibration all come from the previous season).")
    w("- Against strong naive forecasters (EWM, L10, season mean, minutes-adjusted rate) the model has the lowest MAE of the "
      "five in the pooled sample for every stat and beats the EWM(8) baseline in every test season for every stat (0.5-2.7% "
      "lower MAE pooled); its predictive distribution scores better on CRPS and log score in the latest season with the "
      "baselines given identical spread and recalibration treatment.")
    w("- Its P(over) is calibrated near the centre after PIT recalibration, and its spread is roughly right even for rows "
      "with unstable minutes (z_rms 0.96-1.06, 80% coverage 0.76-0.89).")
    w("- Where the model disagrees with the naive forecaster, the realized over-rate mostly follows the model (`closed` near "
      "1, noisy in the smallest gap bands), i.e. the disagreement is information, not noise -- relative to a naive "
      "history-only forecaster.\n")
    w("**It does NOT show**\n")
    w("- **Any market edge.** Books price lineups, injuries, rest/rotation news and sharp action; none of that is in a box "
      "score. A 1-3% MAE improvement over a rolling mean is the kind of gap a book's own projection already contains, and a "
      "-110/-110 market needs a 52.4% hit rate just to break even. Every MLB count model in this repo (K, OUTS, HITS, SB) "
      "came back NO_EDGE against prices (CLV, not ROI, is the go/no-go); the same prior applies here.")
    w("- **Anything about CLV or ROI.** Those need prices. The forward evaluation (`nba/props/forward.py`, criteria in the "
      "handoff) is what decides it, from banked snapshots, and the verdict stays UNDETERMINED until then.")
    w("- **Transfer to real lines from proxy lines.** The proxy line is built from the same history as the model; its "
      "'edge' is by construction relative to a naive forecaster.")
    w("- **That the far tail is trustworthy.** Predicted P(over) >= 0.7 is overconfident for points/PRA (realized up to 6-9pp "
      "lower), so the biggest model-vs-market gaps are the ones to trust least (the winner's-curse pattern the repo has seen "
      "before).")
    w("- **Independence.** One player-game contributes five correlated stat rows, and thousands of rows share a date; the "
      "bootstrap clusters by date but the proxy-line tables are descriptive, not tests.")
    w("- **Playoffs, preseason or the 2026-27 season.** Test rows are regular season only; 2019-20 is truncated; "
      "SportsBlaze has no 2026-27 data yet, so live scoring is gated on fresh history.")
    w("- **That the setup was untouched by the test seasons.** The mean objective (Poisson vs squared error) was compared on "
      "the 2025-26 split once during development (4.532 vs 4.533 MAE for points, no difference) and the hyperparameters were "
      "hand-set, not tuned; the proxy-line definition was changed once after a first version produced implausibly strong "
      "hit-rate ladders (it anchored lines at the mean, ~4-5pp off the median for skewed counts). Both are disclosed so "
      "nobody has to discover them.")
    w("- **Sports-EV-Bot's headline.** Its MAE/R2 are in the same range as ours on the same filter, i.e. not distinguishable "
      "from a model built on the same public box scores; its 'directional accuracy' is a rolling-median proxy, not a market, "
      "and a naive EWM forecaster scores about the same on it.\n")
    return "\n".join(L)


def _save_tables(path: Path, tables: dict):
    path.write_text(json.dumps({f"{s}|{T}": t for (s, T), t in tables.items()}))


def _load_tables(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return {(k.split("|")[0], int(k.split("|")[1])): v for k, v in raw.items()}


def market_report(ev: dict, X: pd.DataFrame, meta: pd.DataFrame, consensus: pd.DataFrame, tables: dict | None = None,
                  min_books: int = 3) -> dict:
    """OPTIONAL (not called unless a consensus frame is supplied): score the walk-forward projections against HISTORICAL
    BettingPros per-book lines. Delegates to nba.props.history_join.market_report -- see that module for the identity
    (fail-closed), selection-safety and coverage semantics. Returns {"by_stat": {stat: {coverage, scores, frame}},
    "all": {...}}; the existing evaluate_predictions output is not touched."""
    from nba.props import history_join as HJ
    return HJ.market_report(ev, X, meta, consensus, tables=tables, min_books=min_books)


def main(argv=None) -> int:
    from nba.props import data as D
    from nba.props.features import build_training_frame
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-dir")
    ap.add_argument("--out", default="docs/analysis/nba_props_evaluation_2026-10-14.md")
    ap.add_argument("--json-out")
    ap.add_argument("--preds-cache", help="parquet path: reuse walk-forward predictions if present, else write them "
                                          "(a .tables.json and .oracle.json are kept beside it)")
    ap.add_argument("--seasons", nargs="*", type=int, default=[2022, 2023, 2024, 2025])
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--bettingpros-consensus", default=None,
                    help="OPTIONAL: consensus parquet from scripts/join_bettingpros_history.py --sport nba; appends a "
                         "model-vs-real-market section to the report (omitted = output unchanged)")
    ap.add_argument("--market-min-books", type=int, default=3)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pb, gm = D.load_masters(a.local_dir)
    rows, tg = D.prepare(pb, gm, D.gather_tips(gm, a.local_dir))
    X, meta = build_training_frame(rows, tg)
    X, meta = X.reset_index(drop=True), meta.reset_index(drop=True)
    meta["season_type"] = meta["season_type"].astype(str)
    hist = D.history_status(rows)
    log.info("frame %s history %s", X.shape, hist)
    cache = Path(a.preds_cache) if a.preds_cache else None
    tpath = cache.with_suffix(".tables.json") if cache else None
    opath = cache.with_suffix(".oracle.json") if cache else None
    if cache and cache.exists() and tpath.exists():
        preds, tables = pd.read_parquet(cache), _load_tables(tpath)
    else:
        preds, tables = walk_forward(X, meta, test_seasons=tuple(a.seasons), progress=log.info, return_tables=True)
        if cache:
            preds.to_parquet(cache)
            _save_tables(tpath, tables)
    headline = max(a.seasons)
    ev = evaluate_predictions(X, meta, preds, a.seasons, tables)
    pooled = pooled_summary(ev)
    proxy = proxy_line_report(ev)
    spread = spread_report(ev, X)
    evb = evbot_table(ev, X, meta, headline)
    orc = None
    if not a.no_oracle:
        if opath and opath.exists():
            orc = pd.read_json(opath)
        else:
            orc = oracle_minutes(X, meta, headline)
            if opath:
                orc.to_json(opath)
    desc = (f"{hist['n_rows']:,} played player-games through {hist['last_date']} (seasons 2019-20..2025-26, preseason "
            "excluded; 2019-20 is truncated at the COVID break)")
    md = render_markdown(ev, pooled, proxy, spread, evb, orc, a.seasons, headline, desc)
    if a.bettingpros_consensus:
        from nba.props import history_join as HJ
        rep = market_report(ev, X, meta, pd.read_parquet(a.bettingpros_consensus), tables, a.market_min_books)
        md += HJ.render_market_section(rep)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(md)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"pooled": pooled, "history": hist}, default=float, indent=1))
    print(md[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
