"""nba.props.availability_study -- does a train/live-consistent availability definition help the NBA prop mean model?

Three feature sets, same rolling-origin protocol as nba.props.train.walk_forward (test season T: train <= T-2, early-stop
on T-1, refit on <= T-1 with 1.15x rounds, predict every regular-season row of T), MEAN model only (the spread / PIT stack
is orthogonal to this question and 5x the compute):

  v1             the shipped default feature set (no availability columns)
  history        + team_missing_* where "absent" = a rotation player who did not dress for ANY reason (existing opt-in v2)
  history_excl_scratch
                 the same, minus rotation players ESPN lists as COACH'S DECISION (healthy scratches). A benching is not on
                 any injury list, so this is the training definition closest to what the live list ("Out") can supply.
                 (Counting ONLY listed out-for-a-reason players is not an option: ESPN's box omits 53% of rotation absences.)

History comes from the ESPN/hoopR mirror (nba/data/hoopr.py). Reports MAE per stat/season, the gain versus v1, and a
date-cluster bootstrap 95% CI. ACCURACY ONLY: nothing here is evidence of edge versus prices (no NBA prop odds history).

    python -m nba.props.availability_study --local-dir <dir with player_boxscores/> --out docs/analysis/<file>.md
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from nba.data import hoopr
from nba.props import config as C
from nba.props import espn_data as ED
from nba.props.evaluate import cluster_boot_diff, point_metrics
from nba.props.features import FeatureContext, build_training_frame
from nba.props.train import EARLY_STOP, MAX_ROUNDS, MEAN_PARAMS, target_of

log = logging.getLogger("nba.props.availability_study")
VARIANTS = ("v1", "history", "history_excl_scratch")
_AV = list(C.AVAILABILITY_FEATURES)


def build_variants(espn_box: pd.DataFrame) -> tuple[dict, pd.DataFrame, dict]:
    """-> ({variant: X}, meta, info). All three X share rows/meta/index; only the availability columns differ."""
    rows, tg = ED.prepare_espn(espn_box)
    ina = ED.inactives(espn_box)
    scratch = ED.scratch_frame(ina)
    ctx = FeatureContext(rows, tg, scratches=scratch)
    Xh, meta = build_training_frame(rows, tg, ctx, availability="history")
    Xk, _ = build_training_frame(rows, tg, ctx, availability="history_excl_scratch")
    assert list(Xh.index) == list(Xk.index)
    X = {"v1": Xh.drop(columns=_AV), "history": Xh, "history_excl_scratch": Xk}
    info = {"n_rows": int(len(Xh)), "n_scratches": int(len(scratch)),
            "avail_coverage_history": float(Xh[_AV[0]].notna().mean()),
            "avail_coverage_excl_scratch": float(Xk[_AV[0]].notna().mean()),
            "mean_absent_history": float(Xh["team_missing_n_rotation"].mean()),
            "mean_absent_excl_scratch": float(Xk["team_missing_n_rotation"].mean())}
    return X, meta, info


def fit_mean(Xtr, ytr, Xva, yva, max_rounds: int = MAX_ROUNDS):
    feats = list(Xtr.columns)
    dtr = xgb.DMatrix(Xtr.astype("float32"), label=ytr, feature_names=feats)
    dva = xgb.DMatrix(Xva[feats].astype("float32"), label=yva, feature_names=feats)
    b = xgb.train(MEAN_PARAMS, dtr, max_rounds, evals=[(dva, "val")], early_stopping_rounds=EARLY_STOP,
                  verbose_eval=False)
    best = int(b.best_iteration) + 1
    n = max(50, int(round(best * 1.15)))
    Xall = pd.concat([Xtr, Xva[feats]])
    yall = np.concatenate([np.asarray(ytr, float), np.asarray(yva, float)])
    final = xgb.train({k: v for k, v in MEAN_PARAMS.items() if k != "eval_metric"},
                      xgb.DMatrix(Xall.astype("float32"), label=yall, feature_names=feats), n)
    return final, best


def run(X: dict, meta: pd.DataFrame, stats, test_seasons, progress=print) -> pd.DataFrame:
    seasons = meta["season_year"].to_numpy()
    reg = (meta["season_type"] == "Regular Season").to_numpy()
    out = []
    for T in test_seasons:
        tr, va, te = seasons <= T - 2, seasons == T - 1, (seasons == T) & reg
        if not (tr.any() and va.any() and te.any()):
            progress(f"skip T={T}: empty split")
            continue
        for stat in stats:
            y = target_of(meta, stat)
            for v in VARIANTS:
                m, best = fit_mean(X[v][tr], y[tr], X[v][va], y[va])
                mu = m.predict(xgb.DMatrix(X[v][te].astype("float32"), feature_names=list(X[v].columns)))
                out.append(pd.DataFrame({"stat": stat, "season": T, "variant": v, "row": np.where(te)[0], "mu": mu}))
                progress(f"T={T} {stat:<8} {v:<13} rounds={best}")
    cols = ["stat", "season", "variant", "row", "mu"]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols)


def summarize(preds: pd.DataFrame, meta: pd.DataFrame, variants=VARIANTS) -> pd.DataFrame:
    recs = []
    dates = meta["date"].to_numpy()
    for (stat, season), g in preds.groupby(["stat", "season"]):
        piv = g.pivot(index="row", columns="variant", values="mu")
        y = target_of(meta, stat)[piv.index.to_numpy()]
        grp = dates[piv.index.to_numpy()]
        base = piv["v1"].to_numpy()
        for v in variants:
            mu = piv[v].to_numpy()
            pm = point_metrics(y, mu)
            rec = {"stat": stat, "season": season, "variant": v, "n": len(y), **pm}
            if v != "v1":
                d, lo, hi = cluster_boot_diff(y, mu, base, grp)        # >0 = this variant better than v1
                rec.update(mae_gain_vs_v1=d, ci_lo=lo, ci_hi=hi, gain_pct=100.0 * d / point_metrics(y, base)["mae"])
            recs.append(rec)
    return pd.DataFrame(recs)


def _md_table(df: pd.DataFrame) -> str:
    """Plain markdown table (no tabulate dependency)."""
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in r) + " |")
    return "\n".join(out)


def render(summary: pd.DataFrame, info: dict, seasons: list, variants=VARIANTS) -> str:
    L = ["# NBA availability-definition study (ESPN history)\n",
         "Mean-model MAE only, rolling origin (train <= T-2, early-stop T-1, refit <= T-1), regular-season rows of the test",
         "season. ACCURACY, not edge: no NBA prop odds history exists to price against.\n",
         f"Test seasons (start year): {seasons}. Rows: {info['n_rows']:,}. ESPN-listed healthy-scratch player-games: "
         f"{info['n_scratches']:,}.\n",
         f"Mean rotation players absent per row: history {info['mean_absent_history']:.3f} vs excl-scratch "
         f"{info['mean_absent_excl_scratch']:.3f} (the gap is healthy scratches the live injury list cannot contain).\n",
         "| stat | variant | MAE (all test seasons, row-weighted) | gain vs v1 | 95% CI (date-cluster) |", "|---|---|---|---|---|"]
    for stat, g in summary.groupby("stat", sort=False):
        w = g.groupby("variant").apply(lambda d: np.average(d["mae"], weights=d["n"]), include_groups=False)
        for v in variants:
            gv = g[g["variant"] == v]
            if v == "v1":
                L.append(f"| {stat} | v1 | {w[v]:.4f} | - | - |")
            else:
                n = gv["n"].to_numpy()
                gain = np.average(gv["mae_gain_vs_v1"], weights=n)
                lo = np.average(gv["ci_lo"], weights=n)
                hi = np.average(gv["ci_hi"], weights=n)
                L.append(f"| {stat} | {v} | {w[v]:.4f} | {100 * gain / w['v1']:+.2f}% | "
                         f"[{100 * lo / w['v1']:+.2f}%, {100 * hi / w['v1']:+.2f}%] |")
    L.append("\n## Per season\n")
    L.append(_md_table(summary.round(4)))
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--local-dir", required=True, help="dir holding player_boxscores/player_box_{season}.parquet")
    ap.add_argument("--first-season", type=int, default=2019, help="first hoopR season to load (year the season ends)")
    ap.add_argument("--seasons", nargs="*", type=int, default=[2023, 2024, 2025], help="test seasons (START year)")
    ap.add_argument("--stats", nargs="*", default=list(C.STATS))
    ap.add_argument("--out", help="markdown report path (raw predictions are saved beside it as .preds.parquet)")
    ap.add_argument("--reuse-preds", action="store_true", help="skip training: re-render from <out>.preds.parquet")
    ap.add_argument("--json-out", help="per-season summary json path")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    top = hoopr.current_season()
    box = ED.load_espn(range(a.first_season, top + 1), a.local_dir)
    if box.empty:
        raise SystemExit("no ESPN box scores found under --local-dir")
    X, meta, info = build_variants(box)
    log.info("variants built: %s", json.dumps(info))
    cache = Path(a.out).with_suffix(".preds.parquet") if a.out else None
    if a.reuse_preds and cache is not None and cache.exists():
        preds = pd.read_parquet(cache)
    else:
        preds = run(X, meta, a.stats, a.seasons, progress=log.info)
        if cache is not None:
            preds.to_parquet(cache)
    summary = summarize(preds, meta)
    md = render(summary, info, a.seasons)
    print(md)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"info": info, "summary": summary.to_dict("records")}, indent=1,
                                               default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
