"""
mlb.analysis.kalshi_edge -- soft-book +EV vs the Kalshi sharp no-vig anchor.

The structural edge that does NOT require out-predicting anyone: Kalshi's order-book
MID (stored as fair_prob, book="kalshi", source="kalshi") is a no-vig sharp estimate
of the true probability. Any onshore book offering a price whose implied payout beats
that sharp fair prob is +EV by construction:

    EV = kalshi_fair_prob * book_decimal - 1

This scans odds_history for such gaps, ranks the live opportunities, and -- more
useful long-run -- reports WHICH books are systematically beatable (the soft books).

TRUST NOTE (from kalshi_to_history): Kalshi is DEEP + tight on game_ml / total_ou /
runline / nrfi_ou / f5_ml -> mids are trustworthy. Player props (hr_yn/k_ou/...) are
thin on Kalshi -> treat their mids as soft, not gospel. Default markets = the deep set.

This is EV vs a sharp anchor, not a settled backtest -- it needs no game outcomes,
but it's only as good as the Kalshi mid. Cross-check flagged books against realized
CLV before sizing up.

Run (Cloud Shell; same env as odds_history):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_edge --since 2026-07-01 --min-ev 0.02
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_edge --markets game_ml,total_ou --min-ev 0.03
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from mlb.analysis import odds_history as oh
from mlb.analysis.backtest_market import OFFSHORE

# Kalshi-deep markets whose no-vig mids are trustworthy (tight order books).
DEEP_MARKETS = ["game_ml", "total_ou", "runline", "nrfi_ou", "f5_ml"]
NON_BOOK = OFFSHORE | {"kalshi"}   # exclude sharps/exchanges/aggregates from the bet side


def _decimal(df: pd.DataFrame) -> pd.Series:
    dec = pd.to_numeric(df.get("decimal"), errors="coerce")
    am = pd.to_numeric(df.get("american"), errors="coerce")
    from_am = np.where(am > 0, 1.0 + am / 100.0, 1.0 + 100.0 / am.abs())
    return dec.where(dec.notna() & (dec > 1.0), pd.Series(from_am, index=df.index))


def score_market(df: pd.DataFrame, market: str, min_ev: float) -> pd.DataFrame:
    """Pure core: given one market's odds_history rows (books + kalshi), return the
    per (game,selection,line,book) BEST-EV soft-book quote vs the Kalshi sharp mid.
    EV = kalshi_fair * book_decimal - 1. No I/O."""
    if df.empty or "source" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["book_l"] = df["book"].astype(str).str.lower()
    df["line_k"] = pd.to_numeric(df["line"], errors="coerce").fillna(-999.0)

    kal = df[df["source"].astype(str).str.lower() == "kalshi"]
    bks = df[(df["source"].astype(str).str.lower() != "kalshi") & (~df["book_l"].isin(NON_BOOK))]
    if kal.empty or bks.empty:
        return pd.DataFrame()

    # sharp fair prob per (game, selection, line) = median Kalshi mid across snapshots
    mid = (kal.groupby(["game_pk", "selection", "line_k"])["fair_prob"]
              .median().rename("kalshi_fp"))
    bks = bks.join(mid, on=["game_pk", "selection", "line_k"])
    bks = bks[bks["kalshi_fp"].notna()].copy()
    if bks.empty:
        return pd.DataFrame()

    bks["dec"] = _decimal(bks)
    bks["book_implied"] = pd.to_numeric(bks["implied_prob"], errors="coerce")
    bks["ev"] = bks["kalshi_fp"] * bks["dec"] - 1.0
    bks["edge_prob"] = bks["kalshi_fp"] - bks["book_implied"]   # prob-space edge vs sharp
    bks["market"] = market

    # best (max EV) quote per (game, selection, line, book)
    keys = ["game_pk", "selection", "line_k", "book_l"]
    bks = bks.sort_values("ev", ascending=False).drop_duplicates(keys, keep="first")
    return bks


def scan(markets, since, until, min_ev):
    frames = []
    for m in markets:
        try:
            raw = oh.read_history(m, since=since, until=until)
        except Exception as e:  # noqa: BLE001
            print(f"  {m}: read failed -- {e}")
            continue
        sm = score_market(raw, m, min_ev)
        if len(sm):
            frames.append(sm)
        n_kal = int((raw["source"].astype(str).str.lower() == "kalshi").sum()) if len(raw) else 0
        print(f"  {m:<10} rows={len(raw):>6}  kalshi_rows={n_kal:>5}  scored={len(sm):>5}")
    if not frames:
        print("\nno scored quotes -- has kalshi_to_history run + banked mids for these markets yet?")
        return pd.DataFrame()
    allq = pd.concat(frames, ignore_index=True)

    # 1) live +EV opportunities
    opp = allq[allq["ev"] >= min_ev].sort_values("ev", ascending=False)
    cols = ["market", "game_date", "away_team", "home_team", "selection", "line",
            "book_l", "american", "book_implied", "kalshi_fp", "edge_prob", "ev"]
    cols = [c for c in cols if c in opp.columns]
    print(f"\n=== +EV OPPORTUNITIES vs Kalshi mid (EV >= {min_ev:.0%}) : {len(opp)} ===")
    with pd.option_context("display.width", 220, "display.max_columns", 30, "display.max_rows", 40):
        print(opp[cols].head(40).to_string(index=False, float_format=lambda x: f"{x:,.3f}")
              if len(opp) else "  (none)")

    # 2) which books are systematically beatable (the soft books)
    g = allq.groupby("book_l")
    tbl = pd.DataFrame({
        "n": g.size(),
        "mean_ev%": g["ev"].mean() * 100,
        "pct_+ev": (g["ev"].apply(lambda s: (s >= min_ev).mean())) * 100,
        "mean_edge_prob": g["edge_prob"].mean(),
    }).sort_values("mean_ev%", ascending=False)
    print("\n=== BOOK EXPLOITABILITY vs Kalshi sharp mid (all quotes) ===")
    print("  pct_+ev = share of a book's quotes that beat the sharp fair by >= min_ev.")
    with pd.option_context("display.width", 160):
        print(tbl.to_string(float_format=lambda x: f"{x:,.2f}"))

    print("\nCAVEAT: +EV only if the Kalshi mid is the true prob. Deep markets "
          "(game_ml/total_ou/runline/nrfi_ou/f5_ml) = trustworthy; props = soft. "
          "Confirm flagged books hold up on realized CLV before sizing.")
    return allq


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Soft-book +EV vs the Kalshi sharp anchor")
    p.add_argument("--markets", default=",".join(DEEP_MARKETS),
                   help=f"comma list (default deep set: {','.join(DEEP_MARKETS)})")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-ev", type=float, default=0.02, help="EV threshold, 0.02 = +2%")
    args = p.parse_args(argv)
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    print(f"Kalshi edge scan | markets={markets} | min_ev={args.min_ev:.0%} "
          f"| since={args.since} until={args.until}")
    scan(markets, args.since, args.until, args.min_ev)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
