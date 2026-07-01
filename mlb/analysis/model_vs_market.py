"""
mlb.analysis.model_vs_market -- is the MODEL a better forecaster than the MARKET?

The whole "capture the gap as value" thesis rests on ONE premise: our model's
probability is more accurate than the market's (de-vigged) implied probability.
"Calibrated" does not prove that -- a calibrated model can still be strictly worse
than the closing line. This measures it directly, on the same games, out-of-sample:

  1. HEAD-TO-HEAD proper scoring: log-loss / Brier / AUC of model_prob vs market_prob
     against the realized outcome. Lower loss / higher AUC = better forecaster.
  2. ORTHOGONAL-INFORMATION test (the decisive one): fit
        realized ~ logit(market_prob)              -> baseline log-loss
        realized ~ logit(market_prob) + logit(model_prob)  -> augmented log-loss
     If adding the model MEANINGFULLY lowers log-loss (and its coefficient is > 0),
     the model carries information the market lacks -> a real, capturable edge.
     If not, the market SUBSUMES the model -> there is no gap to capture, however
     confident the model looks. This is the honest verdict on the EV/CLV thesis.

Evaluated on the CLEAN market only (>= min_books agree within max_spread), so the
per-book line-collapse artifact can't contaminate it. Pass OOS preds (walkforward)
for a leakage-proof read; defaults to in-sample gen_preds (model favored -> if it
LOSES in-sample, it certainly loses OOS).

Run:
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.model_vs_market --system BATTER_TB --since 2024-05-01
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from mlb.analysis import gen_preds as gp
from mlb.analysis import odds_history as oh
from mlb.analysis import backtest_market as bt

# the "positive" selection whose probability we compare, per market
POS_SEL = {"OVER", "YES", "HOME"}


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _market_consensus(odds: pd.DataFrame, spec, min_books: int, max_spread: float) -> pd.DataFrame:
    """Per (game_pk[,player_id], line): the consensus (median) de-vigged prob of the
    POSITIVE side, plus book count / spread -- gated to markets where books agree."""
    keys = ["game_pk"] + (["player_id"] if spec.id_col else []) + ["line"]
    pos = odds[odds["selection"].str.upper().isin(POS_SEL)].copy()
    pos = pos.dropna(subset=["fair_prob"])
    if not len(pos):
        return pd.DataFrame()
    pos["_impl"] = 1.0 / pos["decimal"]
    g = pos.groupby(keys, dropna=False)
    out = g.agg(market_p=("fair_prob", "median"),
                n_books=("book", "nunique"),
                spread=("_impl", lambda s: s.max() - s.min())).reset_index()
    out = out[(out["n_books"] >= min_books) & (out["spread"] <= max_spread)]
    return out


def evaluate(system: str, since=None, until=None, preds: pd.DataFrame | None = None,
             min_books: int = 4, max_spread: float = 0.10) -> dict:
    spec = gp.SPECS[system]
    if preds is None:
        preds = gp.gen_preds(system, since=since, until=until)  # in-sample (model-favored)
    preds = preds[preds["game_pk"].notna()].copy()
    preds["game_pk"] = pd.to_numeric(preds["game_pk"], errors="coerce").astype("Int64")
    if spec.id_col:
        preds = preds[preds["player_id"].notna()]
        preds["player_id"] = pd.to_numeric(preds["player_id"], errors="coerce").astype("Int64")

    odds = oh.read_history(spec.market, since=since, until=until)
    if not len(odds):
        return {"error": f"no odds for {spec.market}"}
    odds = oh.dedupe_by_source(odds)
    odds = odds[~odds["book"].str.lower().isin(bt.OFFSHORE)]
    odds = odds[odds["game_pk"].notna()]
    odds["game_pk"] = pd.to_numeric(odds["game_pk"], errors="coerce").astype("Int64")
    if spec.id_col:
        odds = odds[odds["player_id"].notna()]
        odds["player_id"] = pd.to_numeric(odds["player_id"], errors="coerce").astype("Int64")

    cons = _market_consensus(odds, spec, min_books, max_spread)
    if not len(cons):
        return {"error": "no consensus markets pass the gate"}

    keys = ["game_pk"] + (["player_id"] if spec.id_col else [])
    pmap = preds.set_index(keys)[["kind", "p_model", "mu", "nb_alpha", "realized"]].to_dict("index")

    rows = []
    for _, m in cons.iterrows():
        k = (m["game_pk"], m["player_id"]) if spec.id_col else m["game_pk"]
        pr = pmap.get(k)
        if pr is None:
            continue
        realized = pr["realized"]
        if realized is None or (isinstance(realized, float) and np.isnan(realized)):
            continue
        line = m["line"]
        if pr["kind"] == "count":
            if line is None or (isinstance(line, float) and np.isnan(line)):
                continue
            if realized == line:
                continue  # push
            model_p = gp.p_over(float(line), float(pr["mu"]), float(pr["nb_alpha"]))
            y = 1 if realized > line else 0
        else:  # binary: positive event
            if pd.isna(pr["p_model"]):
                continue
            model_p = float(pr["p_model"])
            y = 1 if realized >= 1 else 0
        rows.append({"model_p": model_p, "market_p": float(m["market_p"]), "y": y})

    if len(rows) < 100:
        return {"error": f"only {len(rows)} joinable outcomes -- too few to score"}
    d = pd.DataFrame(rows)
    y = d["y"].values
    mp, kp = d["model_p"].values, d["market_p"].values

    from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
    from sklearn.linear_model import LogisticRegression

    def _auc(p):
        try:
            return roc_auc_score(y, p)
        except Exception:  # noqa: BLE001
            return float("nan")

    res = {
        "system": system, "n": len(d), "base_rate": float(y.mean()),
        "logloss_model": log_loss(y, np.clip(mp, 1e-6, 1 - 1e-6)),
        "logloss_market": log_loss(y, np.clip(kp, 1e-6, 1 - 1e-6)),
        "brier_model": brier_score_loss(y, mp),
        "brier_market": brier_score_loss(y, kp),
        "auc_model": _auc(mp), "auc_market": _auc(kp),
    }
    # orthogonal-information test
    Xk = _logit(kp).reshape(-1, 1)
    Xkm = np.c_[_logit(kp), _logit(mp)]
    ll_market = log_loss(y, LogisticRegression().fit(Xk, y).predict_proba(Xk)[:, 1])
    lr = LogisticRegression().fit(Xkm, y)
    ll_augmented = log_loss(y, lr.predict_proba(Xkm)[:, 1])
    res["ll_market_only"] = ll_market
    res["ll_market_plus_model"] = ll_augmented
    res["model_adds_bits"] = ll_market - ll_augmented        # >0 = model helps
    res["model_coef_in_joint"] = float(lr.coef_[0][1])       # >0 = same-direction info
    return res


def _fmt(res: dict) -> str:
    if "error" in res:
        return f"  ERROR: {res['error']}"
    win_ll = "MODEL" if res["logloss_model"] < res["logloss_market"] else "market"
    win_auc = "MODEL" if res["auc_model"] > res["auc_market"] else "market"
    verdict = ("MODEL beats market -- real edge to capture"
               if res["model_adds_bits"] > 0.0005 and res["model_coef_in_joint"] > 0.05
               else "market SUBSUMES model -- no capturable gap")
    return (
        f"  n={res['n']}  base_rate={res['base_rate']:.3f}\n"
        f"  log-loss : model {res['logloss_model']:.4f}  vs  market {res['logloss_market']:.4f}   -> {win_ll} better\n"
        f"  Brier    : model {res['brier_model']:.4f}  vs  market {res['brier_market']:.4f}\n"
        f"  AUC      : model {res['auc_model']:.4f}  vs  market {res['auc_market']:.4f}   -> {win_auc} better\n"
        f"  orthogonal info: adding model to market lowers log-loss by "
        f"{res['model_adds_bits']:+.5f} (coef {res['model_coef_in_joint']:+.3f})\n"
        f"  VERDICT: {verdict}"
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Model vs market as competing probability forecasters")
    p.add_argument("--system", required=True, help=", ".join(gp.SPECS))
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-books", type=int, default=4)
    p.add_argument("--max-spread", type=float, default=0.10)
    args = p.parse_args(argv)
    res = evaluate(args.system, since=args.since, until=args.until,
                   min_books=args.min_books, max_spread=args.max_spread)
    print(f"\nmodel_vs_market[{args.system}] (clean markets: >={args.min_books} books, "
          f"spread<={args.max_spread})")
    print(_fmt(res))
    print("\nIf market subsumes the model, bigger models on the SAME public data won't help --\n"
          "the lever is different/faster data or less-efficient markets, not model capacity.")
    return 0 if "error" not in res else 1


if __name__ == "__main__":
    raise SystemExit(main())
