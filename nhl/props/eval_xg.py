"""End-to-end research run: walk-forward goalie-saves / skater goals-assists-points models vs HISTORICAL BettingPros lines.

    PYTHONPATH=. python -m nhl.props.eval_xg --data-dir <dir with NHL/ and bp/> --xg-dir <nhl.xg.build output> \
        --out-dir <dir> [--stats saves goals points assists] [--test-from 2022-10-01] [--json report.json]

LOCAL ONLY: reads local copies, writes parquet + json to --out-dir; never touches GCS, the bets table, Discord or a job.
Per stat it fits the SAME count model three ways on the same walk-forward schedule (`xg`: full feature set, `noxg`: no
xG-derived input, plus non-ML baselines) so the value of xG is an ablation, not an assumption, and scores every variant on
the SAME market props (the intersection). Predictions are cached in --out-dir (`pred_{stat}_{variant}.parquet`); delete
them to refit.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from nhl.props import bp_eval as BE
from nhl.props import countmodel as CM
from nhl.props import xgfeat as X
from nhl.props.negbin import nb_loglik
from nhl.xg import data as D

NTHREAD = int(os.environ.get("NHL_NTHREAD", "6"))
CFG = {
    "saves": {"target": "saves", "prior": "g_n_prior", "step": 2, "xg": X.SAVES_FEATURES, "noxg": X.SAVES_FEATURES_NOXG},
    "goals": {"target": "goals", "prior": "n_prior", "step": 4, "xg": X.SKATER_FEATURES, "noxg": X.SKATER_FEATURES_NOXG},
    "assists": {"target": "assists", "prior": "n_prior", "step": 4, "xg": X.SKATER_FEATURES, "noxg": X.SKATER_FEATURES_NOXG},
    "points": {"target": "points", "prior": "n_prior", "step": 4, "xg": X.SKATER_FEATURES, "noxg": X.SKATER_FEATURES_NOXG},
}


def build_frames(data_dir: str, xg_dir: str) -> dict:
    m = D.load_masters(data_dir)
    xs = pd.read_parquet(os.path.join(xg_dir, "xg_skater_game.parquet"))
    xgl = pd.read_parquet(os.path.join(xg_dir, "xg_goalie_game.parquet"))
    xt = pd.read_parquet(os.path.join(xg_dir, "xg_team_game.parquet"))
    tt = X.team_table(m["games"], xt)
    gt = X.goalie_table(m["games"], m["goalies"], xgl)
    st = X.skater_table(m["games"], m["skaters"], xs)
    return {"masters": m, "tt": tt, "gt": gt, "saves": X.saves_features(gt, tt), "skater": X.skater_features(st, tt, gt)}


def _frame_for(stat: str, fr: dict) -> pd.DataFrame:
    return fr["saves"] if stat == "saves" else fr["skater"]


def baseline_mu(stat: str, f: pd.DataFrame) -> pd.Series:
    """Non-ML benchmarks: saves -> qrob-style structural projection; skater stats -> L120 per-60 rate x L10 TOI."""
    if stat == "saves":
        return X.analytic_saves_mu(f)
    col = {"goals": "goals_p60_l120", "assists": "assists_p60_l120", "points": "points_p60_l120"}[stat]
    return f[col] * f["toi_l10"] / 60.0


def predict_variant(stat: str, f: pd.DataFrame, variant: str, test_from: str, out_dir: str) -> pd.DataFrame:
    path = os.path.join(out_dir, f"pred_{stat}_{variant}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    c = CFG[stat]
    pred = CM.walk_forward(f, c["target"], c[variant], test_from, step_months=c["step"], params={"nthread": NTHREAD},
                           prior_col=c["prior"])
    pred.to_parquet(path, index=False)
    return pred


def count_metrics(f: pd.DataFrame, pred: pd.DataFrame, target: str) -> dict:
    d = pred.merge(f[["game_id", "player_id", target]], on=["game_id", "player_id"], how="inner").dropna(subset=[target, "mu"])
    if not len(d):
        return {"n": 0}
    y, mu = d[target].to_numpy(dtype=float), d["mu"].to_numpy(dtype=float)
    a = float(d["alpha"].median())
    return {"n": int(len(d)), "nb_loglik": nb_loglik(y, mu, a), "mae": float(np.abs(y - mu).mean()),
            "mean_mu": float(mu.mean()), "mean_y": float(y.mean()), "alpha": a}


def run(data_dir: str, xg_dir: str, out_dir: str, stats, test_from: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    fr = build_frames(data_dir, xg_dir)
    m = fr["masters"]
    names, games = BE.name_table(m), BE.games_table(m["games"])
    bp_dir = os.path.join(data_dir, "bp")
    report = {"test_from": test_from, "stats": {}}
    for stat in stats:
        f = _frame_for(stat, fr)
        c = CFG[stat]
        long_df = BE.load_bp_long(bp_dir, BE.SLUG[stat], start=test_from)
        cons = BE.build_consensus(long_df, stat)
        # players who APPEARED (TOI > 0): every goalie appearance for saves (not only starters), every skater appearance
        src = fr["gt"] if stat == "saves" else f
        outcome = src.rename(columns={c["target"]: "actual"})[["game_id", "player_id", "actual"]]
        props, cov = BE.join_market(cons, names, games, outcome, "actual")
        entry = {"coverage": cov, "variants": {}}
        preds = {v: predict_variant(stat, f, v, test_from, out_dir) for v in ("xg", "noxg")}
        for v in ("xg", "noxg"):
            entry["variants"][v] = {"count": count_metrics(f, preds[v], c["target"])}
        # baselines share the xg model's per-period alpha so only the mean differs
        base = f[["game_id", "player_id", "date"]].copy()
        base["mu"] = baseline_mu(stat, f).to_numpy()
        base = base.merge(preds["xg"][["game_id", "player_id", "alpha"]], on=["game_id", "player_id"], how="inner")
        preds["baseline"] = base.dropna(subset=["mu"])
        entry["variants"]["baseline"] = {"count": count_metrics(f, preds["baseline"], c["target"])}
        # common props: every variant must have a prediction
        key = ["game_id", "player_id"]
        common = props[key].merge(preds["xg"][key], on=key).merge(preds["noxg"][key], on=key).merge(preds["baseline"][key], on=key)
        pc = props.merge(common.drop_duplicates(), on=key, how="inner")
        entry["n_common_props"] = int(len(pc))
        entry["dropped_no_model"] = int(len(props) - len(pc))
        for v in ("xg", "noxg", "baseline"):
            pv = BE.attach_model(pc, preds[v])
            entry["variants"][v]["market"] = BE.score(pv, f"{stat}/{v}")
            entry["variants"][v]["by_season"] = BE.score_by_season(pv)
            pv.to_parquet(os.path.join(out_dir, f"props_{stat}_{v}.parquet"), index=False)
        report["stats"][stat] = entry
    return report


def _f(x, d=3, pct=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{100 * x:+.1f}%" if pct else f"{x:.{d}f}"


def render_markdown(rep: dict) -> str:
    """Markdown tables for a report dict (what `run` returns). Per stat: coverage, count-model fit, then per-season market scores."""
    L = []
    for stat, e in rep["stats"].items():
        cov = e["coverage"]
        drops = ", ".join(f"{k}={v}" for k, v in cov["dropped"].items() if v) or "none"
        L += [f"#### {stat}", "",
              f"Market coverage: {cov['matched']:,} of {cov['consensus_rows']:,} consensus rows scored ({100 * cov['match_rate']:.0f}%); "
              f"dropped: {drops}. Props scored by ALL variants: {e['n_common_props']:,}.", "",
              "| variant | rows | NegBin log-lik | MAE | mean mu | mean actual |", "|---|---|---|---|---|---|"]
        for v, vv in e["variants"].items():
            c = vv["count"]
            L.append(f"| {v} | {c.get('n', 0):,} | {_f(c.get('nb_loglik'), 4)} | {_f(c.get('mae'), 3)} | {_f(c.get('mean_mu'), 3)} | {_f(c.get('mean_y'), 3)} |")
        for v in ("xg", "noxg", "baseline"):
            rows = e["variants"][v].get("by_season") or []
            if not rows:
                continue
            L += ["", f"`{v}` vs the market, by season (c = blend coefficient: > 0 means the model adds information beyond the market; "
                  f"ROI5 = flat 1u on the side the model disagrees with by >= 5pp, best book price; vs-ref = that ROI minus betting BOTH "
                  f"sides of the same props at the same prices):", "",
                  "| season | n | over rate | mkt P(over) | model P(over) | logloss model / market | c (t) | ROI5 (n) | vs-ref (t) | blind Under ROI (t) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for r in rows:
                L.append(f"| {r['season']} | {r['n']:,} | {_f(r['over_rate'])} | {_f(r['mean_p_market'])} | {_f(r['mean_p_model'])} | "
                         f"{_f(r['logloss_model'], 4)} / {_f(r['logloss_market'], 4)} | {_f(r['blend_c'], 2)} ({_f(r['blend_t'], 1)}) | "
                         f"{_f(r['roi5'], pct=True)} ({r['roi5_n']:,}) | {_f(r['roi5_vs_no_skill'], pct=True)} ({_f(r['roi5_vs_no_skill_t'], 1)}) | "
                         f"{_f(r['blind_under_roi'], pct=True)} ({_f(r['blind_under_t'], 1)}) |")
        L.append("")
    return "\n".join(L)


def _default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (pd.Timestamp, pd.Interval)):
        return str(o)
    return str(o)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--xg-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stats", nargs="+", default=["saves", "goals", "points", "assists"], choices=list(CFG))
    ap.add_argument("--test-from", default="2022-10-01")
    ap.add_argument("--json", default=None)
    ap.add_argument("--markdown", default=None, help="also write the per-stat tables as markdown")
    a = ap.parse_args(argv)
    rep = run(a.data_dir, a.xg_dir, a.out_dir, a.stats, a.test_from)
    txt = json.dumps(rep, indent=1, default=_default)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(txt)
    if a.markdown:
        with open(a.markdown, "w") as fh:
            fh.write(render_markdown(rep))
    print(render_markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
