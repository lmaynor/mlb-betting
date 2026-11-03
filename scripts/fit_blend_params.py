"""
Refit the per-stat market blend (a, b, c) of ev.daily_projections.blend_p_over from the NBA engine-vs-market study's priced rows.

    python -m scripts.fit_blend_params --priced $S/work/priced.parquet [--variant ship_noabs] [--holdout-from 2024]

    logit(p_blend) = a + b logit(p_market) + c (logit(p_engine) - logit(p_market))        (nhl.props.market.blend_regression)

Reads the `priced.parquet` that scripts/nba_engine_vs_market.py writes (one row per consensus prop: `stat`, `season`, `game_date`,
`over`, `p_market` and one `p_<variant>` column per engine variant). For every stat it prints

  * the in-sample fit on all seasons (the numbers that would go in BLEND_AB_BY_SPORT_STAT) with date-clustered t on c,
  * the same fit on seasons BEFORE --holdout-from, scored on the seasons from it: mean P(over) of the engine / the c-only blend /
    the (a, b, c) blend against the realised over rate, and log loss of the market / c-only / (a, b, c) blends.

Nothing is written unless --write-json is given; the table is the evidence for whether the intercept earns its place. A refit that
does not beat the c-only form out of sample must not be adopted. Not part of any job.
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np
import pandas as pd

from mlb_core.odds import model_vs_market as MVM
from nhl.props import market as M

MIN_TRAIN = 500


def _ll(y, p) -> float:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def _blend(p_mkt, p_eng, a, b, c, clamp: bool) -> np.ndarray:
    lm = M.logit(np.asarray(p_mkt, float))
    ld = M.logit(np.asarray(p_eng, float)) - lm
    p = np.asarray(MVM._sig(a + b * lm + c * ld))
    return np.clip(p, np.asarray(p_mkt) - MVM.BLEND_MAX_SHIFT, np.asarray(p_mkt) + MVM.BLEND_MAX_SHIFT) if clamp else p


def fit(h: pd.DataFrame, col: str) -> dict:
    f = M.blend_regression(h["over"], h["p_market"], h[col], h["game_date"])
    return {"a": float(f["a"]), "b": float(f["b"]), "c": float(f["c"]), "t_c": float(f["t_c"]), "n": int(len(h))}


def evaluate(priced: pd.DataFrame, col: str, holdout_from: int) -> list[dict]:
    out = []
    for stat, h in priced.groupby("stat"):
        full = fit(h, col)
        tr, te = h[h["season"] < holdout_from], h[h["season"] >= holdout_from]
        row = {"stat": stat, "n": full["n"], "a": full["a"], "b": full["b"], "c": full["c"], "t_c": full["t_c"]}
        if len(tr) >= MIN_TRAIN and len(te):
            f = fit(tr, col)
            c_only = _blend(te["p_market"], te[col], 0.0, 1.0, f["c"], clamp=False)
            abc = _blend(te["p_market"], te[col], f["a"], f["b"], f["c"], clamp=True)
            row.update(n_test=int(len(te)), train_a=f["a"], train_b=f["b"], train_c=f["c"],
                       over_rate=float(te["over"].mean()), mean_mkt=float(te["p_market"].mean()), mean_engine=float(te[col].mean()),
                       mean_c_only=float(c_only.mean()), mean_abc=float(abc.mean()),
                       ll_market=_ll(te["over"], te["p_market"]), ll_c_only=_ll(te["over"], c_only), ll_abc=_ll(te["over"], abc))
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--priced", required=True)
    ap.add_argument("--variant", default="ship_noabs", help="engine variant column p_<variant> (default: ship_noabs, the live pricer's closest)")
    ap.add_argument("--holdout-from", type=int, default=2024, help="first season scored out of sample")
    ap.add_argument("--write-json", help="write {stat: {a, b, c, t_c, n}} of the in-sample fits here")
    a = ap.parse_args()
    priced = pd.read_parquet(a.priced)
    col = f"p_{a.variant}"
    if "p_market" not in priced.columns:                          # priced.parquet predates the study's scored_frame step
        priced = priced.assign(p_market=priced["p_over_cons"].astype(float))
    priced = priced.dropna(subset=[col, "p_market", "over"])
    priced = priced[~priced["push"]] if "push" in priced.columns else priced
    priced = priced.assign(**{col: priced[col].clip(0.005, 0.995), "p_market": priced["p_market"].clip(0.005, 0.995)})
    rows = evaluate(priced, col, a.holdout_from)
    print(f"variant {a.variant}: {len(priced):,} props; out-of-sample = seasons >= {a.holdout_from} (fit on earlier seasons only)")
    print(f"{'stat':9} {'n':>7} | {'a':>7} {'b':>6} {'c':>6} {'t(c)':>6} | {'test n':>7} {'over':>6} {'mkt':>6} {'eng':>6} {'c-only':>7} {'abc':>6} "
          f"| {'LL mkt':>7} {'LL c-only':>9} {'LL abc':>7} {'d(abc-c)':>9}")
    for r in rows:
        if "n_test" not in r:
            print(f"{r['stat']:9} {r['n']:7d} | {r['a']:7.3f} {r['b']:6.3f} {r['c']:6.3f} {r['t_c']:6.1f} | (no holdout)")
            continue
        print(f"{r['stat']:9} {r['n']:7d} | {r['a']:7.3f} {r['b']:6.3f} {r['c']:6.3f} {r['t_c']:6.1f} | {r['n_test']:7d} {r['over_rate']:6.3f} "
              f"{r['mean_mkt']:6.3f} {r['mean_engine']:6.3f} {r['mean_c_only']:7.3f} {r['mean_abc']:6.3f} | {r['ll_market']:7.4f} "
              f"{r['ll_c_only']:9.4f} {r['ll_abc']:7.4f} {r['ll_abc'] - r['ll_c_only']:+9.5f}")
    if a.write_json:
        json.dump({r["stat"]: {k: (round(r[k], 5) if isinstance(r[k], float) else r[k]) for k in ("a", "b", "c", "t_c", "n")} for r in rows},
                  open(a.write_json, "w"), indent=1, sort_keys=True)
        print("wrote", a.write_json)


if __name__ == "__main__":
    main()
