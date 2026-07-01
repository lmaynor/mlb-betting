"""
mlb.analysis.backtest_market -- the NRFI edge-bucket method, generalized.

Joins a system's model output (mlb.analysis.gen_preds) to the REAL historical
lines (mlb.analysis.odds_history) and answers the only question that matters:

    In the region where the model claims an edge, did betting the best available
    price actually make money -- and did we beat the closing line (CLV)?

This is deliberately NOT a global-AUC / calibration check (those can look fine
while the bettable slice bleeds -- exactly the NRFI live-drift trap). It measures
ROI *conditioned on model edge*, bucketed, on the real prices you could have taken.

Market shapes handled (via the pred's `kind` + market):
  - binary player prop  (hr_yn):   YES<->p_model,  NO<->1-p_model
  - count  player O/U   (k_ou, outs_ou, bhits_ou, btb_ou):
        OVER<->p_over(line,mu,alpha),  UNDER<->1-p_over   (NegBin, see gen_preds)
  - game moneyline      (game_ml):  HOME<->p_model, AWAY<->1-p_model  (join on game_pk)

Pipeline per bet candidate (one quote = one book's price for a side/line):
  1. model_prob for that exact side+line
  2. fair_prob = de-vigged market prob (row's fair_prob, else unilateral de-vig)
  3. edge = model_prob - fair_prob
  4. keep, per (game_pk, player_id), the single MAX-edge quote = the bet you'd place
     at its best cross-book price (line shopping)
  5. settle vs realized label; ROI = win ? (decimal-1) : -1  (push = 0)
  6. CLV vs the closing snapshot for that same side+line

Then bucket bets by edge and print ROI/hit/CLV per bucket. A healthy system shows
ROI rising with edge and >0 in the +EV buckets; a drifted one is flat/negative
there regardless of headline AUC.

Run (Cloud Shell; needs the same env as gen_preds + odds_history):
    export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
    PYTHONPATH=. python3 -m mlb.analysis.backtest_market --system HR --since 2026-04-01
    PYTHONPATH=. python3 -m mlb.analysis.backtest_market --system K  --since 2026-04-01 --split 2026-06-01
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from mlb.analysis import gen_preds as gp
from mlb.analysis import odds_history as oh
from mlb_core.odds.utils import american_to_decimal, devig_unilateral

# Offshore / non-bettable pseudo-books excluded from line shopping (user rule:
# "all US books qualify as onshore, we just don't want books like Bovada/Betfair").
OFFSHORE = {"pinnacle", "bovada", "betfair", "matchbook", "betonline", "consensus", "average"}

EDGE_BINS = [-1.0, 0.0, 0.02, 0.04, 0.06, 0.10, 1.0]
EDGE_LABELS = ["<0", "0-2%", "2-4%", "4-6%", "6-10%", "10%+"]


# ── settling a candidate: did the bet side win? --------------------------------

def _won(kind: str, market: str, selection: str, line, realized) -> float | None:
    """1.0 win / 0.0 loss / 0.5 push / None if unresolvable."""
    if realized is None or (isinstance(realized, float) and np.isnan(realized)):
        return None
    sel = (selection or "").upper()
    if market == "hr_yn":
        hit = realized >= 1
        return 1.0 if (sel == "YES") == hit else 0.0
    if market == "game_ml":
        home_won = realized >= 1   # realized = home_win
        return 1.0 if (sel == "HOME") == home_won else 0.0
    # count O/U
    if line is None or (isinstance(line, float) and np.isnan(line)):
        return None
    if realized == line:
        return 0.5  # push (integer line landing exactly)
    over_hit = realized > line
    return 1.0 if (sel == "OVER") == over_hit else 0.0


def _model_prob(kind: str, market: str, selection: str, line, row) -> float | None:
    """Model probability for THIS side+line."""
    sel = (selection or "").upper()
    if kind == "binary":
        p = row["p_model"]
        if pd.isna(p):
            return None
        if market == "hr_yn":
            return float(p) if sel == "YES" else float(1.0 - p)
        if market == "game_ml":
            return float(p) if sel == "HOME" else float(1.0 - p)
        return float(p)
    # count
    mu, alpha = row["mu"], row["nb_alpha"]
    if pd.isna(mu) or line is None or (isinstance(line, float) and np.isnan(line)):
        return None
    po = gp.p_over(float(line), float(mu), float(alpha))
    return po if sel == "OVER" else (1.0 - po)


def _fair_prob(row) -> float | None:
    """De-vigged market prob for the quote. Prefer the stored two-way fair_prob;
    else strip a nominal 7% vig unilaterally (single-sided quote)."""
    fp = row.get("fair_prob")
    if fp is not None and not pd.isna(fp):
        return float(fp)
    ip = row.get("implied_prob")
    if ip is None or pd.isna(ip):
        return None
    try:
        return float(devig_unilateral(float(ip)))
    except Exception:  # noqa: BLE001
        return None


# ── closing-line lookup for CLV ------------------------------------------------

def _closing_index(odds: pd.DataFrame) -> dict:
    """Best (max decimal) CLOSING price per (game_pk, player_id, selection, line)."""
    c = odds[odds["is_closing"] == True]  # noqa: E712
    if not len(c):
        return {}
    idx = {}
    for _, r in c.iterrows():
        key = (r["game_pk"], r.get("player_id"), (r["selection"] or "").upper(), r["line"])
        dec = r["decimal"] if not pd.isna(r["decimal"]) else american_to_decimal(r["american"])
        if key not in idx or dec > idx[key][0]:
            idx[key] = (dec, r["american"])
    return idx


# ── core ------------------------------------------------------------------------

def backtest(system: str, since: str | None = None, until: str | None = None,
             split: str | None = None, min_edge: float = 0.0,
             books: set | None = None) -> dict:
    spec = gp.SPECS[system]
    preds = gp.gen_preds(system, since=since, until=until)
    preds = preds[preds["game_pk"].notna()]
    if spec.id_col:
        preds = preds[preds["player_id"].notna()]

    odds = oh.read_history(spec.market, since=since, until=until)
    if not len(odds):
        return {"error": f"no odds_history rows for market={spec.market}"}
    odds = oh.dedupe_by_source(odds)
    # onshore, bettable books only
    deny = OFFSHORE if books is None else (OFFSHORE - books)
    odds = odds[~odds["book"].str.lower().isin(deny)]
    odds = odds[odds["game_pk"].notna()]
    odds["game_pk"] = pd.to_numeric(odds["game_pk"], errors="coerce").astype("Int64")
    if spec.id_col:
        odds = odds[odds["player_id"].notna()]
        odds["player_id"] = pd.to_numeric(odds["player_id"], errors="coerce").astype("Int64")

    # join key
    keys = ["game_pk"] + (["player_id"] if spec.id_col else [])
    pmap = preds.set_index(keys)[["kind", "p_model", "mu", "nb_alpha", "realized",
                                  "game_date"]].to_dict("index")

    closing = _closing_index(odds)
    kind = spec.kind
    rows = []
    for _, q in odds.iterrows():
        k = (q["game_pk"], q["player_id"]) if spec.id_col else (q["game_pk"],)
        k = k[0] if len(k) == 1 else k
        pr = pmap.get(k)
        if pr is None:
            continue
        sel, line = q["selection"], q["line"]
        mp = _model_prob(kind, spec.market, sel, line, pr)
        fp = _fair_prob(q)
        if mp is None or fp is None:
            continue
        dec = q["decimal"] if not pd.isna(q["decimal"]) else american_to_decimal(q["american"])
        rows.append({
            "game_pk": q["game_pk"], "player_id": q["player_id"],
            "game_date": pr["game_date"], "selection": (sel or "").upper(),
            "line": line, "book": q["book"], "american": q["american"], "decimal": dec,
            "model_prob": mp, "fair_prob": fp, "edge": mp - fp,
            "realized": pr["realized"],
        })
    if not rows:
        return {"error": "no joinable model<->odds candidates"}
    cand = pd.DataFrame(rows)

    # line shopping: per (game_pk, player_id) keep the single max-edge quote = the bet
    cand = cand.sort_values("edge", ascending=False).drop_duplicates(subset=keys, keep="first")
    cand = cand[cand["edge"] >= min_edge].copy()

    # settle + CLV
    cand["won"] = [
        _won(kind, spec.market, s, l, r)
        for s, l, r in zip(cand["selection"], cand["line"], cand["realized"])
    ]
    cand = cand[cand["won"].notna()].copy()
    cand["roi"] = np.where(cand["won"] == 1.0, cand["decimal"] - 1.0,
                           np.where(cand["won"] == 0.5, 0.0, -1.0))

    def _clv(r):
        key = (r["game_pk"], r["player_id"] if spec.id_col else pd.NA,
               r["selection"], r["line"])
        cl = closing.get(key)
        if not cl:
            return np.nan
        from mlb_core.odds.utils import clv_pct_from_prices
        try:
            return clv_pct_from_prices(int(r["american"]), int(cl[1]))
        except Exception:  # noqa: BLE001
            return np.nan
    cand["clv_pct"] = cand.apply(_clv, axis=1)

    train = cand[cand["game_date"] < split] if split else cand
    test = cand[cand["game_date"] >= split] if split else cand.iloc[0:0]

    return {"system": system, "market": spec.market, "kind": kind,
            "candidates": cand, "train": train, "test": test, "split": split}


# ── reporting ------------------------------------------------------------------

def _bucket_table(df: pd.DataFrame) -> pd.DataFrame:
    if not len(df):
        return pd.DataFrame()
    b = df.copy()
    b["bucket"] = pd.cut(b["edge"], bins=EDGE_BINS, labels=EDGE_LABELS, include_lowest=True)
    g = b.groupby("bucket", observed=False)
    out = pd.DataFrame({
        "bets": g.size(),
        "hit%": g["won"].apply(lambda s: (s == 1.0).mean() * 100),
        "avg_odds": g["decimal"].mean(),
        "roi%": g["roi"].mean() * 100,
        "clv%": g["clv_pct"].mean(),
        "total_units": g["roi"].sum(),
    })
    return out


def _print_report(res: dict, label: str, df: pd.DataFrame):
    if not len(df):
        print(f"  [{label}] no bets")
        return
    tbl = _bucket_table(df)
    print(f"  [{label}] {len(df)} bets  overall ROI {df['roi'].mean()*100:+.2f}%  "
          f"units {df['roi'].sum():+.1f}  CLV {df['clv_pct'].mean():+.2f}%")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(tbl.to_string(float_format=lambda x: f"{x:,.2f}"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Edge-bucket ROI/CLV backtest vs real lines")
    p.add_argument("--system", required=True, help=", ".join(gp.SPECS))
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--split", default=None,
                   help="YYYY-MM-DD time-split; before=train, on/after=test (holdout)")
    p.add_argument("--min-edge", type=float, default=0.0,
                   help="only bet candidates with model edge >= this (default 0)")
    args = p.parse_args(argv)

    res = backtest(args.system, since=args.since, until=args.until,
                   split=args.split, min_edge=args.min_edge)
    if "error" in res:
        print(f"backtest[{args.system}] ERROR: {res['error']}")
        return 1

    print(f"\nbacktest {res['system']} ({res['market']}, {res['kind']}) "
          f"-- {len(res['candidates'])} bets  since={args.since} split={args.split}\n")
    if args.split:
        _print_report(res, "TRAIN <" + args.split, res["train"])
        print()
        _print_report(res, "TEST >=" + args.split, res["test"])
    else:
        _print_report(res, "ALL", res["candidates"])
    print("\n(+EV buckets should show rising, positive ROI% and CLV%; flat/negative "
          "there = drift, regardless of headline accuracy.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
