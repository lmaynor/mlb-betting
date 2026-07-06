"""
mlb.analysis.outlier_scan -- find books that LAG the consensus, output +EV alerts.

The edge here is NOT "our model beats the sharp market" (rare -- only OUTS). It's
"one book is out of line with the others" -- lagging new data or the market consensus,
sometimes by 10%+ (classic on HR). The consensus of the OTHER books is the fair-prob
anchor; a book offering meaningfully better-than-fair odds is a +EV mistake to strike
before it corrects.

For each (market, game, player, line, selection) with >= min_books quoting it:
  consensus_fair = median de-vigged prob across books
  for each book:  EV = consensus_fair * book_decimal - 1
  flag books with EV >= min_ev  (i.e. priced below fair = lagging)

Optionally cross-check with the MODEL prob (gen_preds) as an alternate/confirming fair
anchor -- valuable on markets where the model beats consensus (OUTS), and once lineups
lock. Consensus is the default because it needs no lineup and is robust.

Runs on odds_history (any market/date) -- point it at the freshest snapshot the tracker
banked. IMPORTANT: run on the REPAIRED store (or forward tracker data); on the
line-collapse-corrupted store it will surface FAKE outliers.

Run:
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.outlier_scan --markets hr_yn,outs_ou --date 2026-07-01 --min-ev 0.03
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from mlb.analysis import odds_history as oh
from mlb.analysis import backtest_market as bt
from mlb_core.odds.utils import american_to_decimal

# pseudo-books that are not bettable outlets (never alert on these)
_NON_BOOK = bt.OFFSHORE | {"open"}


def scan(market: str, since: str | None = None, until: str | None = None,
         min_ev: float = 0.03, min_books: int = 4, latest_only: bool = True,
         anchor_book: str = "pinnacle") -> pd.DataFrame:
    """Return +EV outlier quotes for one market vs the cross-book consensus.

    anchor_book: when this (sharp, unbettable-for-us) book quotes a selection,
    its de-vigged fair prob is used as the truth anchor instead of the soft-book
    median -- soft books can all be slow together, Pinnacle rarely is. Falls
    back to the median where the anchor has no quote. Set anchor_book=None (or
    env OUTLIER_ANCHOR=none in callers) to disable.
    """
    raw = oh.read_history(market, since=since, until=until)
    if not len(raw):
        return pd.DataFrame()
    raw = oh.dedupe_by_source(raw)
    raw = raw[raw["decimal"].notna() & raw["fair_prob"].notna()]
    if not len(raw):
        return pd.DataFrame()
    # only the latest snapshot per market/selection (freshest lines) unless a window given
    if latest_only and "snapshot_ts" in raw.columns:
        raw = raw.sort_values("snapshot_ts")
        raw = raw.groupby(["game_pk", "player_id", "line", "selection", "book"],
                          dropna=False, as_index=False).tail(1)

    grp = ["game_pk", "player_id", "line", "selection"]

    # sharp-anchor fair probs (kept out of the bettable set below)
    anchor = pd.DataFrame()
    if anchor_book:
        anchor = raw[raw["book"].str.lower() == anchor_book.lower()]
        anchor = (anchor.groupby(grp, dropna=False)["fair_prob"].median()
                  .rename("anchor_fair").reset_index())

    odds = raw[~raw["book"].str.lower().isin(_NON_BOOK)].copy()
    if not len(odds):
        return pd.DataFrame()
    g = odds.groupby(grp, dropna=False)
    odds["consensus_fair"] = g["fair_prob"].transform("median")
    odds["n_books"] = g["book"].transform("nunique")
    if len(anchor):
        odds = odds.merge(anchor, on=grp, how="left")
        odds["anchored"] = odds["anchor_fair"].notna()
        odds["consensus_fair"] = odds["anchor_fair"].fillna(odds["consensus_fair"])
        odds = odds.drop(columns=["anchor_fair"])
    else:
        odds["anchored"] = False
    odds["_impl"] = 1.0 / odds["decimal"]
    # regroup: the anchor merge above replaced the frame `g` was built on
    odds["book_spread"] = odds.groupby(grp, dropna=False)["_impl"].transform(
        lambda s: s.max() - s.min())

    odds = odds[odds["n_books"] >= min_books].copy()
    # EV of striking THIS book at its price, using the consensus as truth
    odds["ev"] = odds["consensus_fair"] * odds["decimal"] - 1.0
    hits = odds[odds["ev"] >= min_ev].copy()
    if not len(hits):
        return hits
    hits["edge_vs_consensus"] = odds["consensus_fair"] - hits["_impl"]  # how far book lags
    cols = ["game_date", "game_pk", "away_team", "home_team", "player_id",
            "selection", "line", "book", "american", "decimal", "consensus_fair",
            "anchored", "ev", "edge_vs_consensus", "n_books", "book_spread",
            "snapshot_ts"]
    cols = [c for c in cols if c in hits.columns]
    return hits[cols].sort_values("ev", ascending=False).reset_index(drop=True)


def scan_markets(markets: list, since=None, until=None, min_ev=0.03,
                 min_books=4, latest_only=True,
                 anchor_book: str | None = "pinnacle") -> pd.DataFrame:
    frames = []
    for m in markets:
        df = scan(m, since=since, until=until, min_ev=min_ev,
                  min_books=min_books, latest_only=latest_only,
                  anchor_book=anchor_book)
        if len(df):
            df.insert(0, "market", m)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("ev", ascending=False)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Scan for +EV outlier (lagging) book prices")
    p.add_argument("--markets", default="hr_yn,outs_ou,k_ou,btb_ou,bhits_ou",
                   help="comma list of canonical odds_history markets")
    p.add_argument("--date", default=None, help="single game_date to scan (else full range)")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-ev", type=float, default=0.03, help="alert threshold, e.g. 0.03 = +3%")
    p.add_argument("--min-books", type=int, default=4, help="need >= N books for a real consensus")
    p.add_argument("--all-snapshots", action="store_true",
                   help="scan every snapshot, not just the freshest per line")
    args = p.parse_args(argv)
    since = args.date or args.since
    until = args.date or args.until
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    out = scan_markets(markets, since=since, until=until, min_ev=args.min_ev,
                       min_books=args.min_books, latest_only=not args.all_snapshots)
    print(f"\n+EV outlier scan  markets={markets}  min_ev={args.min_ev:.0%}  min_books={args.min_books}")
    if not len(out):
        print("  no +EV outliers found (books in line with consensus, or no data).")
        return 0
    print(f"  {len(out)} alerts (book priced below cross-book fair -- a lagging line to strike):\n")
    show = out.copy()
    show["ev"] = (show["ev"] * 100).round(1)
    show["edge_vs_consensus"] = (show["edge_vs_consensus"] * 100).round(1)
    show["consensus_fair"] = show["consensus_fair"].round(3)
    with pd.option_context("display.width", 240, "display.max_columns", 30, "display.max_rows", 40):
        print(show.to_string(index=False))
    print("\nCAVEAT: an outlier is +EV only if the book is LAGGING (corrects toward consensus).\n"
          "If it's the INFORMED book (consensus moves toward it), it's -EV. The tracker's\n"
          "forward snapshots resolve which -- watch whether flagged lines move your way by close.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
