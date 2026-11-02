"""Would the TRAINED NHL models still beat the market with only what a live scorer can know?

    PYTHONPATH=. python -m nhl.props.eval_serving --data-dir <dir with NHL/ and bp/> --xg-dir <nhl.xg.build output> \
        --out-dir <dir> --stat goals [--test-from 2022-10-01] [--json r.json]

PR #70's skater models (goals / assists / points) were fed the goalie who ACTUALLY started for the opponent, which is public
before the puck drops only once a starter is confirmed (no feed here). A served model has to use the EXPECTED starter
(nhl.props.xgfeat.expected_starter_features: most starts in the last 10, backup on a back-to-back). This script fits the same
walk-forward count models under the settings a server could use and scores every one on the SAME historical BettingPros props
(nhl.props.bp_eval; blend coefficient c, log loss vs the market, disagreement ROI at the best price):

  skater stats   xg_actual   PR #70 as published (actual opposing starter; the research ceiling)
                 xg_exp      same features, expected opposing starter
                 noxg_exp    no xG-derived feature, expected starter (needs only the box score + shot-attempt counts)
  saves          xg / noxg   (a goalie's own features: no starter assumption to make)

Predictions are cached in --out-dir (pred_{stat}_{variant}.parquet); delete them to refit. Local only: reads local copies,
writes parquet / json to --out-dir, never touches GCS, the bets table or a job.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from nhl.props import bp_eval as BE
from nhl.props import countmodel as CM
from nhl.props import eval_xg as EX
from nhl.props import xgfeat as X
from nhl.xg import data as D

NTHREAD = int(os.environ.get("NHL_NTHREAD", "3"))
SKATER_VARIANTS = (("xg_actual", "actual", "xg"), ("xg_exp", "expected", "xg"), ("noxg_exp", "expected", "noxg"))
SAVES_VARIANTS = (("xg", None, "xg"), ("noxg", None, "noxg"))


def build_frames(data_dir: str, xg_dir: str) -> dict:
    m = D.load_masters(data_dir)
    xs = pd.read_parquet(os.path.join(xg_dir, "xg_skater_game.parquet"))
    xgl = pd.read_parquet(os.path.join(xg_dir, "xg_goalie_game.parquet"))
    xt = pd.read_parquet(os.path.join(xg_dir, "xg_team_game.parquet"))
    tt = X.team_table(m["games"], xt)
    gt = X.goalie_table(m["games"], m["goalies"], xgl)
    st = X.skater_table(m["games"], m["skaters"], xs)
    return {"masters": m, "tt": tt, "gt": gt, "saves": X.saves_features(gt, tt),
            "actual": X.skater_features(st, tt, gt, "actual"), "expected": X.skater_features(st, tt, gt, "expected")}


def predict(stat: str, f: pd.DataFrame, feats: list, tag: str, test_from: str, out_dir: str) -> pd.DataFrame:
    path = os.path.join(out_dir, f"pred_{stat}_{tag}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    c = EX.CFG[stat]
    pred = CM.walk_forward(f, c["target"], feats, test_from, step_months=c["step"], params={"nthread": NTHREAD},
                           prior_col=c["prior"])
    pred.to_parquet(path, index=False)
    return pred


def run(data_dir: str, xg_dir: str, out_dir: str, stat: str, test_from: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    fr = build_frames(data_dir, xg_dir)
    m = fr["masters"]
    names, games = BE.name_table(m), BE.games_table(m["games"])
    c = EX.CFG[stat]
    long_df = BE.load_bp_long(os.path.join(data_dir, "bp"), BE.SLUG[stat], start=test_from)
    cons = BE.build_consensus(long_df, stat)
    src = fr["gt"] if stat == "saves" else fr["actual"]
    outcome = src.rename(columns={c["target"]: "actual"})[["game_id", "player_id", "actual"]]
    if stat == "saves":
        outcome = fr["saves"].rename(columns={"saves": "actual"})[["game_id", "player_id", "actual"]]    # starters only
    props, cov = BE.join_market(cons, names, games, outcome, "actual")
    variants = SAVES_VARIANTS if stat == "saves" else SKATER_VARIANTS
    preds = {}
    for tag, mode, fs in variants:
        f = fr["saves"] if stat == "saves" else fr[mode]
        preds[tag] = predict(stat, f, c[fs], tag, test_from, out_dir)
    key = ["game_id", "player_id"]
    common = props[key].drop_duplicates()
    for p in preds.values():
        common = common.merge(p[key].drop_duplicates(), on=key)
    pc = props.merge(common, on=key, how="inner")
    rep = {"stat": stat, "test_from": test_from, "coverage": cov, "n_common_props": int(len(pc)), "variants": {}}
    for tag in preds:
        pv = BE.attach_model(pc, preds[tag])
        rep["variants"][tag] = {"market": BE.score(pv, f"{stat}/{tag}"), "by_season": BE.score_by_season(pv)}
        pv.to_parquet(os.path.join(out_dir, f"props_{stat}_{tag}.parquet"), index=False)
    return rep


def summary_rows(rep: dict) -> list:
    out = []
    for tag, v in rep["variants"].items():
        for r in v["by_season"]:
            out.append({"stat": rep["stat"], "variant": tag, "season": r["season"], "n": r["n"],
                        "ll_model": r["logloss_model"], "ll_market": r["logloss_market"], "c": r["blend_c"],
                        "t": r["blend_t"], "roi5": r["roi5"], "roi5_n": r["roi5_n"]})
    return out


SERVED = {"saves": "noxg", "goals": "xg_exp", "assists": "xg_exp"}


def served_summary(out_dir: str) -> dict:
    """The configuration nhl.props.trained serves, scored from the cached walk-forward predictions in `out_dir`: saves = no-xG,
    goals / assists = xG with the expected starter, points = trained goals + assists (nhl.props.trained.coherent_points)."""
    from nhl.props.negbin import p_over_vec
    rep = {}
    for stat, tag in SERVED.items():
        p = pd.read_parquet(os.path.join(out_dir, f"props_{stat}_{tag}.parquet"))
        rep[stat] = {"n": int(len(p)), "by_season": BE.score_by_season(p), "market": BE.score(p, f"{stat}/served")}
    pp = pd.read_parquet(os.path.join(out_dir, "props_points_xg_exp.parquet"))
    g = pd.read_parquet(os.path.join(out_dir, "pred_goals_xg_exp.parquet"))[["game_id", "player_id", "mu"]].rename(columns={"mu": "mu_g"})
    a = pd.read_parquet(os.path.join(out_dir, "pred_assists_xg_exp.parquet"))[["game_id", "player_id", "mu"]].rename(columns={"mu": "mu_a"})
    m = pp.merge(g, on=["game_id", "player_id"]).merge(a, on=["game_id", "player_id"])
    m["p_model"] = p_over_vec(m["line"].to_numpy(), (m["mu_g"] + m["mu_a"]).to_numpy(), m["alpha"].to_numpy())
    rep["points"] = {"n": int(len(m)), "by_season": BE.score_by_season(m), "market": BE.score(m, "points/served")}
    return rep


def _default(o):
    return o.item() if isinstance(o, (np.floating, np.integer)) else str(o)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--xg-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stat", choices=list(EX.CFG))
    ap.add_argument("--summarize-served", action="store_true", help="score the served configuration from cached predictions")
    ap.add_argument("--test-from", default="2022-10-01")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    if a.summarize_served:
        rep = served_summary(a.out_dir)
        if a.json:
            with open(a.json, "w") as fh:
                json.dump(rep, fh, indent=1, default=_default)
        for stat, v in rep.items():
            for r in v["by_season"]:
                print(stat, {k: (round(x, 4) if isinstance(x, float) else x) for k, x in r.items()
                             if k in ("season", "n", "logloss_model", "logloss_market", "blend_c", "blend_t", "roi5", "roi5_n")})
        return 0
    if not a.stat:
        ap.error("--stat is required unless --summarize-served")
    rep = run(a.data_dir, a.xg_dir, a.out_dir, a.stat, a.test_from)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=1, default=_default)
    for r in summary_rows(rep):
        if r["season"] == "ALL":
            print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
