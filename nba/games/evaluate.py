"""
nba.games.evaluate -- walk-forward evaluation of the NBA moneyline / spread / totals models against the closing market.

For each test season S (START year): train on every game before S's first date (from `train_from`), predict S, join the
BettingPros consensus. Reports, per market:
  * accuracy vs the line: log-loss (ML), RMSE of the margin/total vs actual for model and for the market's implied value
  * INCREMENTAL information: leave-one-season-out blend of the de-vigged market probability with the model (ML), and an
    OLS of the actual outcome on [market value, model - market] (spread/total) -- the coefficient on the model-minus-market
    term is the share of the model's disagreement that is real. ~0 = the market already has it.
  * flat-stake ROI of betting the model's side when its edge over the de-vigged market >= a threshold, at the MEDIAN book
    price and at the BEST book price (line-shopping upper bound).
No claim of edge is made unless the incremental term is positive with a CI that excludes zero AND ROI holds out of sample.

    PYTHONPATH=. python -m nba.games.evaluate --hoopr-dir <dir> --tsf-csv <csv> --bp-prefix-dir <MLB_BASE_DATA> --out <md>
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from scipy.special import logit
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression

from nba.games import features as F
from nba.games import models as M

THRESHOLDS = (0.0, 0.02, 0.04, 0.06)


def walk_forward(feat: pd.DataFrame, test_seasons, kind: str = "ridge", train_from: int = 2008) -> pd.DataFrame:
    out = []
    for s in test_seasons:
        first = feat.loc[feat["season"] == s, "date"].min()
        tr = feat[(feat["season"] >= train_from) & (feat["date"] < first)]
        te = feat[feat["season"] == s]
        gm = M.fit(tr, kind)
        out.append(pd.concat([te, gm.predict(te)], axis=1))
    return pd.concat(out)


def _ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _roi(ret: np.ndarray) -> dict:
    n = len(ret)
    if n == 0:
        return {"n": 0, "roi": np.nan, "se": np.nan}
    return {"n": n, "roi": float(ret.mean()), "se": float(ret.std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan}


def _bets(p_model_a, p_mkt_a, dec_a, dec_b, win_a, win_b, push, thr):
    """Back side A when p_model-p_mkt >= thr, side B when <= -thr. Returns per-bet net return (stake 1)."""
    edge = p_model_a - p_mkt_a
    on_a, on_b = edge >= thr, edge <= -thr
    ret_a = np.where(push, 0.0, np.where(win_a, dec_a - 1.0, -1.0))[on_a]
    ret_b = np.where(push, 0.0, np.where(win_b, dec_b - 1.0, -1.0))[on_b]
    return np.concatenate([ret_a, ret_b])


def moneyline(df: pd.DataFrame) -> dict:
    d = df.dropna(subset=["ml_p_home", "p_ml"]).copy()
    y = d["home_win"].values
    res = {"n": len(d), "ll_market": _ll(y, d["ml_p_home"].values), "ll_model": _ll(y, d["p_ml"].values),
           "ll_model_margin": _ll(y, d["p_ml_margin"].values), "ll_home_rate": _ll(y, np.full(len(d), y.mean()))}
    # leave-one-season-out blend: logit(p) ~ logit(market) + logit(model)
    blend = np.full(len(d), np.nan)
    X = np.column_stack([logit(np.clip(d["ml_p_home"].values, 1e-4, 1 - 1e-4)),
                         logit(np.clip(d["p_ml"].values, 1e-4, 1 - 1e-4))])
    for s in (d["season"].unique() if d["season"].nunique() > 1 else []):
        te = (d["season"] == s).values
        lr = LogisticRegression(C=1e6, max_iter=500).fit(X[~te], y[~te])
        blend[te] = lr.predict_proba(X[te])[:, 1]
    res["ll_blend_loso"] = _ll(y, blend) if not np.isnan(blend).any() else float("nan")
    lr = LogisticRegression(C=1e6, max_iter=500).fit(X, y)
    res["blend_coef_market"], res["blend_coef_model"] = float(lr.coef_[0][0]), float(lr.coef_[0][1])
    win_h, win_a = d["home_win"].values == 1, d["home_win"].values == 0
    nopush = np.zeros(len(d), bool)
    for thr in THRESHOLDS:
        for tag in ("med", "best"):
            r = _roi(_bets(d["p_ml"].values, d["ml_p_home"].values, d[f"ml_dec_home_{tag}"].values,
                           d[f"ml_dec_away_{tag}"].values, win_h, win_a, nopush, thr))
            res[f"roi_{tag}_{thr}"] = r
    return res


def _line_market(df, pre, side_a, mu_col, sig_col, kind):
    cols = [f"{pre}_line", f"{pre}_p_{side_a}", mu_col]
    d = df.dropna(subset=cols).copy()
    out = {"n": len(d)}
    if kind == "spread":
        actual, mkt_val = d["margin"].values, -d[f"{pre}_line"].values
        p_mod = M.p_home_cover(d[mu_col], d[sig_col], d[f"{pre}_line"])
        res_a = d["margin"] + d[f"{pre}_line"]       # >0 home covers
    else:
        actual, mkt_val = d["total"].values, d[f"{pre}_line"].values
        p_mod = M.p_over(d[mu_col], d[sig_col], d[f"{pre}_line"])
        res_a = d["total"] - d[f"{pre}_line"]        # >0 over
    mu = d[mu_col].values
    out["rmse_market"] = float(np.sqrt(np.mean((actual - mkt_val) ** 2)))
    out["rmse_model"] = float(np.sqrt(np.mean((actual - mu) ** 2)))
    out["mean_model_minus_market"] = float(np.mean(mu - mkt_val))
    # actual - market = c * (model - market) + e  -> c = share of the disagreement that is real (SE is HC1-ish via np)
    x = mu - mkt_val
    yv = actual - mkt_val
    c = float((x * yv).sum() / (x * x).sum())
    e = yv - c * x
    se = float(np.sqrt(((x * e) ** 2).sum()) / (x * x).sum())
    out["incr_coef"], out["incr_se"] = c, se
    push = np.isclose(res_a.values, 0.0)
    win_a, win_b = (res_a.values > 0), (res_a.values < 0)
    sfx = {"spread": ("home", "away"), "total": ("over", "under")}[kind]
    for thr in THRESHOLDS:
        for tag in ("med", "best"):
            r = _roi(_bets(np.asarray(p_mod), d[f"{pre}_p_{side_a}"].values, d[f"{pre}_dec_{sfx[0]}_{tag}"].values,
                           d[f"{pre}_dec_{sfx[1]}_{tag}"].values, win_a, win_b, push, thr))
            out[f"roi_{tag}_{thr}"] = r
    out["push_rate"] = float(push.mean())
    return out


def spread(df):
    return _line_market(df, "sp", "home", "mu_margin", "sigma_margin", "spread")


def total(df):
    return _line_market(df, "tot", "over", "mu_total", "sigma_total", "total")


def by_season(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for s, d in df.groupby("season"):
        r = {"season": s, "games": len(d)}
        if d["ml_p_home"].notna().any():
            m = moneyline(d)
            r.update(ml_n=m["n"], ml_ll_mkt=m["ll_market"], ml_ll_model=m["ll_model"])
        if d["sp_line"].notna().any():
            sp = spread(d)
            r.update(sp_rmse_mkt=sp["rmse_market"], sp_rmse_model=sp["rmse_model"])
        if d["tot_line"].notna().any():
            t = total(d)
            r.update(tot_rmse_mkt=t["rmse_market"], tot_rmse_model=t["rmse_model"])
        rows.append(r)
    return pd.DataFrame(rows)


def _md(df: pd.DataFrame) -> str:
    """Markdown table without the optional `tabulate` dependency."""
    d = df.round(4).astype(object).where(df.notna(), "")
    rows = ["| " + " | ".join(map(str, d.columns)) + " |", "|" + "---|" * len(d.columns)]
    rows += ["| " + " | ".join(str(v) for v in r) + " |" for r in d.values.tolist()]
    return "\n".join(rows)


def _fmt_roi(res: dict, tag: str) -> str:
    cells = []
    for thr in THRESHOLDS:
        r = res[f"roi_{tag}_{thr}"]
        cells.append(f"{100 * r['roi']:+.1f}% +/-{100 * r['se']:.1f} (n={r['n']})" if r["n"] > 1 else "n/a")
    return " | ".join(cells)


def report(ml, sp, tot, per_season: pd.DataFrame, meta: dict) -> str:
    L = ["# NBA game-model walk-forward vs BettingPros closing consensus", "",
         f"Test seasons {meta['seasons']}; train from {meta['train_from']} (strictly before each test season); model "
         f"`{meta['kind']}`. Market = median de-vigged book consensus (BettingPros last quote of day, a closing proxy).",
         f"Coverage: {meta['n_games']} test games, {meta['n_priced']} with a market.", "",
         "## Moneyline", f"- n={ml['n']}  log-loss: market **{ml['ll_market']:.4f}**, logistic model {ml['ll_model']:.4f}, "
         f"Phi(margin/sigma) {ml['ll_model_margin']:.4f}, home-rate {ml['ll_home_rate']:.4f}",
         f"- LOSO blend log-loss {ml['ll_blend_loso']:.4f}; blend coefficients on logit: market {ml['blend_coef_market']:.3f}, "
         f"model {ml['blend_coef_model']:.3f}",
         f"- ROI by edge threshold {list(THRESHOLDS)} (median price): {_fmt_roi(ml, 'med')}",
         f"- ROI (best price, optimistic): {_fmt_roi(ml, 'best')}", "",
         "## Spread", f"- n={sp['n']}  RMSE vs actual margin: market {sp['rmse_market']:.3f}, model {sp['rmse_model']:.3f}; "
         f"mean(model - market margin) {sp['mean_model_minus_market']:+.2f}",
         f"- incremental coefficient on (model - market): {sp['incr_coef']:+.3f} (se {sp['incr_se']:.3f}); push rate {sp['push_rate']:.3f}",
         f"- ROI (median price): {_fmt_roi(sp, 'med')}", f"- ROI (best price): {_fmt_roi(sp, 'best')}", "",
         "## Totals", f"- n={tot['n']}  RMSE vs actual total: market {tot['rmse_market']:.3f}, model {tot['rmse_model']:.3f}; "
         f"mean(model - market total) {tot['mean_model_minus_market']:+.2f}",
         f"- incremental coefficient on (model - market): {tot['incr_coef']:+.3f} (se {tot['incr_se']:.3f}); push rate {tot['push_rate']:.3f}",
         f"- ROI (median price): {_fmt_roi(tot, 'med')}", f"- ROI (best price): {_fmt_roi(tot, 'best')}", "",
         "## By season", _md(per_season), ""]
    return "\n".join(L)


def main() -> None:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--hoopr-dir", required=True)
    ap.add_argument("--tsf-csv", default=None)
    ap.add_argument("--base-data", required=True, help="MLB_BASE_DATA dir that holds Odds/bettingpros_nba_games")
    ap.add_argument("--kind", default="ridge", choices=["ridge", "hgb"])
    ap.add_argument("--seasons", default="2021,2022,2023,2024,2025")
    ap.add_argument("--train-from", type=int, default=2008)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    os.environ["MLB_BASE_DATA"] = a.base_data
    from nba.games import bp_games, data, market
    g = data.load(a.hoopr_dir, a.tsf_csv)
    feat = F.build(g[g["season"] >= 2005].reset_index(drop=True))
    seasons = [int(x) for x in a.seasons.split(",")]
    pred = walk_forward(feat, seasons, a.kind, a.train_from)
    cons = market.consensus(bp_games.read_games())
    df = market.attach(pred, cons)
    ml, sp, tot = moneyline(df), spread(df), total(df)
    meta = {"seasons": seasons, "train_from": a.train_from, "kind": a.kind, "n_games": len(df),
            "n_priced": int(df["ml_p_home"].notna().sum())}
    txt = report(ml, sp, tot, by_season(df), meta)
    print(txt)
    if a.out:
        open(a.out, "w").write(txt)
        df.to_parquet(a.out.replace(".md", ".parquet"))


if __name__ == "__main__":
    main()
