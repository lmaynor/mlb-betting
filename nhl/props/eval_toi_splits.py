"""Walk-forward ACCURACY check: do prior-only TOI-split features (nhl.props.toi_feat) improve the skater goals / assists / points / SOG models?

    PYTHONPATH=. python -m nhl.props.eval_toi_splits --data-dir <dir with NHL/> --xg-dir <nhl.xg.build output> \
        --splits <toi_splits.csv | official_toi.csv> --out-dir <dir> [--stats goals assists points sog] [--test-from 2022-10-01]

LOCAL ONLY (reads local copies, writes parquet/json/markdown to --out-dir; no GCS, bets table, Discord or job).

Same count model, same walk-forward schedule, same rows, two feature sets:
    base   nhl.props.xgfeat.SKATER_FEATURES (the PR #70 research set, the current features)
    toi    base + nhl.props.toi_feat.TOI_FEATURES
Each variant is fitted by nhl.props.countmodel.walk_forward (every row is scored by a model that never saw its date). The comparison is
PAIRED per skater-game: NegBin log score (each variant with its own per-period dispersion) and absolute error of the mean. The standard
error clusters by GAME (the two teams' skaters in one game are not independent). Adoption bar (the repo's: docs/solutions/conventions/
nfl-projection-engine-changes-need-walkforward-evidence.md): the paired log-score gain must be >= ADOPT_Z standard errors (two-sided,
Bonferroni over the stats tested), MAE must agree, and the log-score gain must be positive in at least ADOPT_SEASONS of the test seasons.
ACCURACY only: no price, no edge, nothing here is a bet.

--splits accepts either the shift-derived table (nhl.data.toi_splits) or the NHL's own published split (`official_toi.csv`, one request per
14 days); the schemas are bridged by toi_splits.official_as_splits.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd
from scipy.stats import nbinom

from nhl.data import toi_splits as TS
from nhl.props import countmodel as CM
from nhl.props import eval_xg as EX
from nhl.props import xgfeat as X
from nhl.props.toi_feat import TOI_FEATURES, toi_features

NTHREAD = int(os.environ.get("NHL_NTHREAD", "6"))
STEP_MONTHS = 4
STATS = ["goals", "assists", "points", "sog"]
ADOPT_SEASONS = 3                      # the log-score gain must be positive in at least this many test seasons
MIN_SEASON_ROWS = 5000                 # a season with fewer scored rows (a partial current season) does not count toward the replication bar


def adopt_z(n_tests: int) -> float:
    """Two-sided normal critical value at 0.05 / n_tests (Bonferroni): 2.50 for 4 stats (the NFL study used 2.64 for 6)."""
    from scipy.stats import norm
    return float(norm.ppf(1 - 0.05 / n_tests / 2))


def load_splits(path: str) -> pd.DataFrame:
    s = pd.read_csv(path)
    return TS.official_as_splits(s) if "off_ev" in s.columns else s


def build_frame(data_dir: str, xg_dir: str, splits: pd.DataFrame) -> pd.DataFrame:
    """The xgfeat skater feature frame + `sog` + TOI_FEATURES (prior-only, from `splits`)."""
    fr = EX.build_frames(data_dir, xg_dir)
    f = fr["skater"].copy()
    box = fr["masters"]["skaters"][["game_id", "player_id", "sog"]].drop_duplicates(["game_id", "player_id"])
    f = f.merge(box, on=["game_id", "player_id"], how="left")
    tf = toi_features(f[["game_id", "player_id", "date"]], splits).drop_duplicates(["game_id", "player_id"])
    return f.merge(tf, on=["game_id", "player_id"], how="left")


def predict(f: pd.DataFrame, stat: str, variant: str, test_from: str, out_dir: str) -> pd.DataFrame:
    path = os.path.join(out_dir, f"pred_{stat}_{variant}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    feats = X.SKATER_FEATURES if variant == "base" else X.SKATER_FEATURES + TOI_FEATURES
    pred = CM.walk_forward(f, stat, feats, test_from, step_months=STEP_MONTHS, params={"nthread": NTHREAD}, prior_col="n_prior")
    pred.to_parquet(path, index=False)
    return pred


def nb_logpmf(y, mu, alpha) -> np.ndarray:
    y, mu, alpha = np.asarray(y, float), np.maximum(np.asarray(mu, float), 1e-6), np.asarray(alpha, float)
    n = 1.0 / np.maximum(alpha, 1e-6)
    return nbinom.logpmf(y, n, n / (n + mu))


def paired(f: pd.DataFrame, stat: str, p_base: pd.DataFrame, p_toi: pd.DataFrame) -> pd.DataFrame:
    """One row per scored skater-game with both variants' mu / alpha, log score and absolute error."""
    k = ["game_id", "player_id"]
    d = (p_base[k + ["date", "mu", "alpha"]].merge(p_toi[k + ["mu", "alpha"]], on=k, suffixes=("_b", "_t"))
         .merge(f[k + [stat]], on=k).dropna(subset=[stat, "mu_b", "mu_t"]))
    d["ll_b"] = nb_logpmf(d[stat], d["mu_b"], d["alpha_b"])
    d["ll_t"] = nb_logpmf(d[stat], d["mu_t"], d["alpha_t"])
    d["ae_b"] = (d[stat] - d["mu_b"]).abs()
    d["ae_t"] = (d[stat] - d["mu_t"]).abs()
    d["season"] = pd.to_datetime(d["date"]).map(lambda x: x.year if x.month >= 8 else x.year - 1)
    return d


def cluster_mean_se(x: np.ndarray, cluster: np.ndarray) -> tuple[float, float]:
    """Mean of x and its standard error with observations clustered (summed within `cluster`)."""
    n = len(x)
    if n == 0:
        return float("nan"), float("nan")
    m = float(np.mean(x))
    df = pd.DataFrame({"r": x - m, "c": cluster}).groupby("c")["r"].sum()
    return m, float(np.sqrt((df ** 2).sum()) / n)


def summarize(d: pd.DataFrame) -> dict:
    """Paired effect sizes of `toi` over `base` (positive log-score gain / negative MAE change = toi better)."""
    out = {"n": int(len(d))}
    g = d["game_id"].to_numpy()
    for key, a, b, sign in (("log_score_gain", "ll_t", "ll_b", 1.0), ("mae_change", "ae_t", "ae_b", 1.0)):
        m, se = cluster_mean_se(sign * (d[a] - d[b]).to_numpy(), g)
        out[key] = {"mean": m, "se": se, "z": m / se if se > 0 else float("nan")}
    out["mae_base"], out["mae_toi"] = float(d["ae_b"].mean()), float(d["ae_t"].mean())
    out["ll_base"], out["ll_toi"] = float(d["ll_b"].mean()), float(d["ll_t"].mean())
    out["mae_rel_change"] = out["mae_toi"] / out["mae_base"] - 1.0 if out["mae_base"] else float("nan")
    return out


def verdict(per_stat: dict, n_tests: int) -> dict:
    """Apply the adoption bar to every stat's summary (`summary` + `by_season`)."""
    zc = adopt_z(n_tests)
    out = {}
    for stat, e in per_stat.items():
        s = e["overall"]
        seasons = [r for r in e["by_season"] if r["n"] >= MIN_SEASON_ROWS]
        pos = sum(1 for r in seasons if r["log_score_gain"]["mean"] > 0)
        gain_ok = s["log_score_gain"]["z"] >= zc
        mae_ok = s["mae_change"]["mean"] < 0
        rep_ok = pos >= min(ADOPT_SEASONS, len(seasons))
        out[stat] = {"adopt": bool(gain_ok and mae_ok and rep_ok), "log_score_z": s["log_score_gain"]["z"], "z_needed": zc,
                     "mae_improves": bool(mae_ok), "seasons_positive": f"{pos}/{len(seasons)}"}
    return out


def run(data_dir: str, xg_dir: str, splits_path: str, out_dir: str, stats=STATS, test_from: str = "2022-10-01") -> dict:
    os.makedirs(out_dir, exist_ok=True)
    splits = load_splits(splits_path)
    f = build_frame(data_dir, xg_dir, splits)
    cov = {c: float(f.loc[f["date"] >= test_from, c].notna().mean()) for c in TOI_FEATURES}
    rep = {"test_from": test_from, "splits": splits_path, "n_splits_rows": int(len(splits)), "toi_feature_coverage_test_rows": cov,
           "stats": {}}
    for stat in stats:
        pb, pt = predict(f, stat, "base", test_from, out_dir), predict(f, stat, "toi", test_from, out_dir)
        d = paired(f, stat, pb, pt)
        rep["stats"][stat] = {"overall": summarize(d),
                              "by_season": [{"season": int(s), **summarize(g)} for s, g in d.groupby("season")]}
    rep["verdict"] = verdict(rep["stats"], len(stats))
    return rep


def _f(x, d=4):
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def render_markdown(rep: dict) -> str:
    L = ["| stat | rows | log score base -> toi | gain per row (SE) | z | MAE base -> toi | MAE change | seasons gain > 0 | adopt |",
         "|---|---|---|---|---|---|---|---|---|"]
    for stat, e in rep["stats"].items():
        s, v = e["overall"], rep["verdict"][stat]
        L.append(f"| {stat} | {s['n']:,} | {_f(s['ll_base'])} -> {_f(s['ll_toi'])} | {s['log_score_gain']['mean']:+.5f} ({s['log_score_gain']['se']:.5f}) | "
                 f"{_f(s['log_score_gain']['z'], 2)} | {_f(s['mae_base'])} -> {_f(s['mae_toi'])} | {100 * s['mae_rel_change']:+.2f}% | "
                 f"{v['seasons_positive']} | {'YES' if v['adopt'] else 'no'} |")
    L += ["", f"Bonferroni z needed over {len(rep['stats'])} stats: {next(iter(rep['verdict'].values()))['z_needed']:.2f}.", ""]
    for stat, e in rep["stats"].items():
        L += [f"`{stat}` by season:", "", "| season | rows | log-score gain (SE) | z | MAE change |", "|---|---|---|---|---|"]
        for r in e["by_season"]:
            L.append(f"| {r['season']} | {r['n']:,} | {r['log_score_gain']['mean']:+.5f} ({r['log_score_gain']['se']:.5f}) | "
                     f"{_f(r['log_score_gain']['z'], 2)} | {r['mae_change']['mean']:+.5f} ({100 * r['mae_rel_change']:+.2f}%) |")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--xg-dir", required=True)
    ap.add_argument("--splits", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stats", nargs="+", default=STATS, choices=STATS)
    ap.add_argument("--test-from", default="2022-10-01")
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    a = ap.parse_args(argv)
    rep = run(a.data_dir, a.xg_dir, a.splits, a.out_dir, a.stats, a.test_from)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    md = render_markdown(rep)
    if a.markdown:
        with open(a.markdown, "w") as fh:
            fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
