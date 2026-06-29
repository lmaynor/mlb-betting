"""Validate the 'skip the dead mid-edge band' rule out-of-sample. [CLOUD SHELL]

The full-holdout backtest showed a non-monotonic edge-bucket ROI U-shape: the
5-10% band lost (-9.6%) while neighbors won. Skipping it roughly doubled pooled
ROI -- but a band chosen and evaluated on the SAME data can be overfit (you can
always find a losing slice in-sample). This script does the honest test:

  FIT split   (2024-08-20 .. boundary, default 2026-01-01): pick the band here.
  TEST split  (boundary .. end, i.e. 2026):                 validate it here.

Both splits are model-holdout (v18 test_from = 2024-08-20), so the model never
trained on either; the split is purely to keep the BAND RULE honest. A band is
trustworthy only if it loses on FIT *and* skipping it helps (or at least does not
hurt) on TEST.

Reports per-split edge-bucket ROI, and on TEST compares strategies:
  bet-all  vs  skip[5,10)  vs  skip[5,12)  vs  skip[5,15)
with ROI, units, and a rough t-stat (mean/se of per-bet unit P&L).

Run:
    PYTHONPATH=. python3 -m mlb.analysis.validate_skip_band \
        --odds yrfi_master_2026.csv --preds nrfi_preds.csv --price consensus
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from mlb.analysis.nrfi_market import load_yrfi_master, backtest_vs_lines

_BUCKETS = [0, 0.05, 0.10, 0.15, 0.20, 1.0]
_LABELS = ["0-5%", "5-10%", "10-15%", "15-20%", "20%+"]


def _ledger(df: pd.DataFrame, preds: pd.DataFrame, min_edge: float, price: str) -> pd.DataFrame:
    """Bet ledger (one row per placed bet) with game date attached."""
    res = backtest_vs_lines(df, preds, min_edge=min_edge, price=price)
    led = res.get("_bets")
    if led is None or not len(led):
        return pd.DataFrame()
    dates = df[["game_key", "date"]].drop_duplicates("game_key")
    led = led.merge(dates, on="game_key", how="left")
    led["date"] = pd.to_datetime(led["date"])
    return led


def _bucket_table(led: pd.DataFrame) -> pd.DataFrame:
    g = led.copy()
    g["edge_bucket"] = pd.cut(g["edge"], _BUCKETS, labels=_LABELS)
    return g.groupby("edge_bucket", observed=True).agg(
        n=("won", "size"), win_rate=("won", "mean"),
        roi=("profit_units", "mean"), units=("profit_units", "sum"),
    )


def _strategy(led: pd.DataFrame, skip: tuple[float, float] | None) -> dict:
    """ROI of a strategy that drops bets whose edge is in [skip_lo, skip_hi)."""
    s = led
    if skip is not None:
        lo, hi = skip
        s = led[~((led["edge"] >= lo) & (led["edge"] < hi))]
    n = len(s)
    if n == 0:
        return {"strategy": skip, "n": 0, "roi": float("nan"), "units": 0.0, "t": float("nan")}
    roi = s["profit_units"].mean()
    se = s["profit_units"].std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
    return {"n": n, "roi": round(roi, 4), "units": round(s["profit_units"].sum(), 2),
            "roi_se": round(se, 4) if se == se else se,
            "t": round(roi / se, 2) if se and se == se else float("nan")}


def main() -> None:
    ap = argparse.ArgumentParser(description="OOS-validate the skip-band rule")
    ap.add_argument("--odds", default="yrfi_master_2026.csv")
    ap.add_argument("--preds", required=True, help="game_key,p_yrfi[,yrfi]")
    ap.add_argument("--price", default="consensus", choices=["best", "consensus"])
    ap.add_argument("--min-edge", type=float, default=0.03)
    ap.add_argument("--fit-start", default="2024-08-20", help="model test_from")
    ap.add_argument("--boundary", default="2026-01-01",
                    help="FIT = [fit-start, boundary); TEST = [boundary, end)")
    args = ap.parse_args()

    df = load_yrfi_master(args.odds)
    preds = pd.read_csv(args.preds)
    led = _ledger(df, preds, args.min_edge, args.price)
    if led.empty:
        print("no bets -- check inputs"); return

    fit_start = pd.Timestamp(args.fit_start)
    boundary = pd.Timestamp(args.boundary)
    fit = led[(led["date"] >= fit_start) & (led["date"] < boundary)]
    test = led[led["date"] >= boundary]
    print(f"FIT  {fit_start.date()}..{boundary.date()}: {len(fit)} bets | "
          f"TEST {boundary.date()}+: {len(test)} bets  (price={args.price})")

    print("\n=== FIT split (choose the band here) -- edge-bucket ROI ===")
    print(_bucket_table(fit))
    print("\n=== TEST split (validate here) -- edge-bucket ROI ===")
    print(_bucket_table(test))

    print("\n=== TEST-split strategy comparison (does the band generalize?) ===")
    strs: list = []
    for label, skip in [("bet-all", None), ("skip[5,10)", (0.05, 0.10)),
                        ("skip[5,12)", (0.05, 0.12)), ("skip[5,15)", (0.05, 0.15))]:
        row = _strategy(test, skip)
        row["strategy"] = label
        strs.append(row)
    print(pd.DataFrame(strs).set_index("strategy"))

    # Verdict heuristic.
    bt = _bucket_table(test)
    band_roi = bt.loc["5-10%", "roi"] if "5-10%" in bt.index else float("nan")
    all_roi = test["profit_units"].mean()
    skip_roi = _strategy(test, (0.05, 0.12))["roi"]
    print("\n=== VERDICT ===")
    print(f"TEST 5-10% bucket ROI = {band_roi:+.3f} (n={int(bt.loc['5-10%','n']) if '5-10%' in bt.index else 0})")
    print(f"TEST bet-all ROI = {all_roi:+.3f} | skip[5,12) ROI = {skip_roi:+.3f}")
    if band_roi < 0 and skip_roi > all_roi:
        print("GENERALIZES: band loses on TEST and skipping it improves OOS ROI.")
    elif band_roi >= 0:
        print("DOES NOT GENERALIZE: the band is NOT a loser on TEST -- likely "
              "in-sample noise. Do not hard-skip it; prefer a calibration fix.")
    else:
        print("WEAK: band is negative on TEST but skipping does not clearly help "
              "-- treat as inconclusive on this sample.")


if __name__ == "__main__":
    main()
