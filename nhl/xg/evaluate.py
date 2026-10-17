"""Temporal-holdout report for the xG models:  PYTHONPATH=. python -m nhl.xg.evaluate --data-dir <dir> [--json out.json]

Fit on seasons < `--test-from` (the last of them is the early-stopping slice), score `--test-from` onward. Compared with
a constant (the training goal rate) and a distance+angle logistic regression (the same split). Reported: log-loss, AUC,
Brier, a decile reliability table, and the team-game goal error that the nhl-xG-model archive headlines (aggregate
predicted vs actual goals) -- shown here only for comparison; calibration + log-loss are the real tests.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from nhl.props.market import brier, logloss, reliability
from nhl.xg import data as D
from nhl.xg import features as F
from nhl.xg import model as M


def auc(y, p) -> float:
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    from scipy.stats import rankdata
    r = rankdata(p)
    n1, n0 = y.sum(), len(y) - y.sum()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def _logit_baseline(tr: pd.DataFrame, te: pd.DataFrame) -> np.ndarray:
    from sklearn.linear_model import LogisticRegression
    cols = ["dist_ft", "angle_deg"]
    clf = LogisticRegression(max_iter=300).fit(tr[cols].to_numpy(), tr["goal"].to_numpy())
    return clf.predict_proba(te[cols].to_numpy())[:, 1]


def evaluate_population(prep: pd.DataFrame, population: str, test_from: int) -> dict:
    m = F.population_mask(prep, population)
    seasons = sorted(prep.loc[m, "season"].unique())
    train_s = [s for s in seasons if s < test_from]
    test_s = [s for s in seasons if s >= test_from]
    art = M.fit(prep, population, train_s[:-1], val_season=train_s[-1])
    tr, te = prep[m & prep["season"].isin(train_s)], prep[m & prep["season"].isin(test_s)].copy()
    te["p_xgb"] = M.predict(art, te)
    te["p_lr"] = _logit_baseline(tr, te)
    te["p_const"] = float(tr["goal"].mean())
    y = te["goal"].to_numpy()
    res = {"population": population, "train_seasons": train_s, "test_seasons": test_s, "n_train": int(len(tr)),
           "n_test": int(len(te)), "goal_rate_test": float(y.mean()), "best_iteration": art["meta"]["best_iteration"]}
    for k in ("xgb", "lr", "const"):
        p = te[f"p_{k}"].to_numpy()
        res[k] = {"logloss": logloss(y, p), "brier": brier(y, p), "auc": auc(y, p) if k != "const" else 0.5,
                  "sum_p": float(p.sum()), "sum_goals": float(y.sum())}
    rel = reliability(y, te["p_xgb"], bins=(0, .02, .04, .06, .08, .12, .2, .35, .6, 1.0))
    res["reliability"] = rel.assign(bin=rel["bin"].astype(str)).to_dict("records")
    # per-test-season calibration drift
    res["by_season"] = {int(s): {"n": int(len(g)), "sum_p": float(g["p_xgb"].sum()), "goals": int(g["goal"].sum()),
                                 "logloss": logloss(g["goal"], g["p_xgb"])} for s, g in te.groupby("season")}
    # team-game aggregate error (the archive's headline): sum xG per (game, shooting team) vs goals in the population
    tg = te.groupby(["game_id", "team_id"]).agg(xg=("p_xgb", "sum"), xg_c=("p_const", "sum"), g=("goal", "sum"))
    res["team_game"] = {"n": int(len(tg)), "mae_xgb": float((tg["xg"] - tg["g"]).abs().mean()),
                        "mae_const": float((tg["xg_c"] - tg["g"]).abs().mean()),
                        "corr_xgb": float(np.corrcoef(tg["xg"], tg["g"])[0, 1]),
                        "corr_const": float(np.corrcoef(tg["xg_c"], tg["g"])[0, 1])}
    gain = M_importance(art)
    res["top_features"] = gain[:10]
    return res


def M_importance(art: dict) -> list:
    sc = art["booster"].get_score(importance_type="gain")
    return sorted(([k, round(float(v), 2)] for k, v in sc.items()), key=lambda kv: -kv[1])


def render(r: dict) -> str:
    L = [f"### xG population `{r['population']}`  train {r['train_seasons'][0]}-{r['train_seasons'][-1]} "
         f"(n={r['n_train']:,})  test {r['test_seasons']} (n={r['n_test']:,}, goal rate {r['goal_rate_test']:.4f})", "",
         "| model | log-loss | Brier | AUC | sum p | goals |", "|---|---|---|---|---|---|"]
    for k, lab in (("xgb", "XGBoost xG"), ("lr", "dist+angle logistic"), ("const", "constant")):
        x = r[k]
        L.append(f"| {lab} | {x['logloss']:.5f} | {x['brier']:.5f} | {x['auc']:.4f} | {x['sum_p']:.0f} | {x['sum_goals']:.0f} |")
    tg = r["team_game"]
    L += ["", f"Team-game goals (n={tg['n']:,}): MAE xG {tg['mae_xgb']:.3f} vs constant {tg['mae_const']:.3f}; "
          f"corr xG {tg['corr_xgb']:.3f} vs constant {tg['corr_const']:.3f}", "", "| p bin | n | mean p | goal rate | gap |",
          "|---|---|---|---|---|"]
    for b in r["reliability"]:
        L.append(f"| {b['bin']} | {b['n']:,} | {b['p_mean']:.4f} | {b['y_rate']:.4f} | {b['gap']:+.4f} |")
    L += ["", "By test season (sum p vs goals): " + ", ".join(
        f"{s}: {v['sum_p']:.0f}/{v['goals']}" for s, v in r["by_season"].items()),
        "Top gain: " + ", ".join(f"{k} {v}" for k, v in r["top_features"][:8])]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=None, help="local dir holding NHL/shots/shots_*.csv (default: mlb_core.storage)")
    ap.add_argument("--test-from", type=int, default=2024)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    prep = F.prepare_shots(D.load_shots(a.data_dir))
    out = [evaluate_population(prep, pop, a.test_from) for pop in ("attempt", "sog")]
    for r in out:
        print(render(r), "\n")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=2, default=float)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
