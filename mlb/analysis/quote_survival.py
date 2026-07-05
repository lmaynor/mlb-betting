"""
mlb.analysis.quote_survival -- how long do +EV outlier quotes actually live?

The soft-line strategy (outlier_scan / odds_alert) only pays if a lagging
quote survives long enough to strike AND the consensus was right (the book
corrects toward consensus). This module measures both from the intraday
snapshots banked in odds_history (ParlayAPI 8x/day + BettingPros tracker
5x/day):

For each (market, game, player, line, selection, book), walk the snapshot
sequence. An OUTLIER EVENT opens at the first snapshot where
    ev = consensus_fair * book_decimal - 1 >= min_ev   (>= min_books quoting)
and closes at the first later snapshot where ev < exit_ev (hysteresis), or is
CENSORED at the last snapshot of the day (never corrected before close --
best case: you could have held the price all day).

At close we attribute WHO moved:
    book_moved      -- the book's implied prob rose toward consensus
                       (consensus was right; the EV was real)
    consensus_moved -- consensus fell toward the book
                       (the "outlier" book was right; the EV was fake)
using whichever side moved more between open and close of the event.

Output per (market, book): n events, median survival minutes, % surviving
>= 30/60 min, % censored (lasted to final snapshot), % book_moved. Books with
high survival + high book_moved are the ones worth striking; high
consensus_moved books are leaders you should NOT bet against.

Run (Cloud Shell):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.quote_survival --markets hr_yn,k_ou,outs_ou --since 2026-06-25
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from mlb.analysis import odds_history as oh
from mlb.analysis import backtest_market as bt

log = logging.getLogger(__name__)

_NON_BOOK = bt.OFFSHORE | {"open", "consensus"}
_KEY = ["game_pk", "player_id", "line", "selection"]


def _prep(odds: pd.DataFrame, min_books: int) -> pd.DataFrame:
    """Attach per-snapshot consensus_fair / n_books / ev to every quote row."""
    odds = odds[~odds["book"].str.lower().isin(_NON_BOOK)]
    odds = odds[odds["decimal"].notna() & odds["fair_prob"].notna()].copy()
    if not len(odds):
        return odds
    odds["snapshot_dt"] = pd.to_datetime(odds["snapshot_ts"], errors="coerce")
    odds = odds[odds["snapshot_dt"].notna()]
    grp = _KEY + ["snapshot_ts"]
    g = odds.groupby(grp, dropna=False)
    odds["consensus_fair"] = g["fair_prob"].transform("median")
    odds["n_books"] = g["book"].transform("nunique")
    odds = odds[odds["n_books"] >= min_books].copy()
    odds["impl"] = 1.0 / odds["decimal"]
    odds["ev"] = odds["consensus_fair"] * odds["decimal"] - 1.0
    return odds


def events_for_market(market: str, since=None, until=None,
                      min_ev: float = 0.03, exit_ev: float | None = None,
                      min_books: int = 4) -> pd.DataFrame:
    """One row per outlier event. Columns: book, game_date, selection, line,
    open_ts, close_ts, survival_min, censored, open_ev, mover."""
    if exit_ev is None:
        exit_ev = min_ev / 2.0
    odds = oh.read_history(market, since=since, until=until)
    if odds is None or not len(odds):
        return pd.DataFrame()
    odds = oh.dedupe_by_source(odds)
    odds = _prep(odds, min_books=min_books)
    if not len(odds):
        return pd.DataFrame()

    rows = []
    for (gpk, pid, line, sel, book), seq in odds.groupby(_KEY + ["book"], dropna=False):
        seq = seq.sort_values("snapshot_dt")
        if len(seq) < 2:
            continue  # need at least one later snapshot to observe survival
        open_row = None
        for _, r in seq.iterrows():
            if open_row is None:
                if r["ev"] >= min_ev:
                    open_row = r
                continue
            if r["ev"] < exit_ev:  # corrected
                rows.append(_event(open_row, r, censored=False, market=market))
                open_row = None
        if open_row is not None:  # still open at last snapshot -> censored
            rows.append(_event(open_row, seq.iloc[-1], censored=True, market=market))
    return pd.DataFrame(rows)


def _event(o: pd.Series, c: pd.Series, censored: bool, market: str) -> dict:
    surv = (c["snapshot_dt"] - o["snapshot_dt"]).total_seconds() / 60.0
    d_book = c["impl"] - o["impl"]                      # book impl rising = book corrected
    d_cons = o["consensus_fair"] - c["consensus_fair"]  # consensus falling = market came to book
    if censored:
        mover = "held"
    else:
        mover = "book_moved" if d_book >= d_cons else "consensus_moved"
    return {
        "market": market, "book": o["book"], "game_date": o.get("game_date", ""),
        "game_pk": o["game_pk"], "player_id": o["player_id"],
        "selection": o["selection"], "line": o["line"],
        "open_ts": str(o["snapshot_ts"]), "close_ts": str(c["snapshot_ts"]),
        "survival_min": round(max(surv, 0.0), 1), "censored": censored,
        "open_ev": round(float(o["ev"]), 4), "mover": mover,
    }


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    """Per (market, book) survival + attribution stats."""
    if events is None or not len(events):
        return pd.DataFrame()
    def _agg(g: pd.DataFrame) -> pd.Series:
        closed = g[~g["censored"]]
        return pd.Series({
            "n_events": len(g),
            "median_surv_min": g["survival_min"].median(),
            "pct_surv_30m": (g["survival_min"] >= 30).mean(),
            "pct_surv_60m": (g["survival_min"] >= 60).mean(),
            "pct_censored": g["censored"].mean(),
            "pct_book_moved": (closed["mover"] == "book_moved").mean() if len(closed) else np.nan,
            "mean_open_ev": g["open_ev"].mean(),
        })
    out = (events.groupby(["market", "book"])
           .apply(_agg, include_groups=False)
           .reset_index())
    return out.sort_values(["market", "n_events"], ascending=[True, False]).reset_index(drop=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Stale +EV quote survival from intraday snapshots")
    p.add_argument("--markets", default="hr_yn,k_ou,outs_ou,btb_ou,bhits_ou")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-ev", type=float, default=0.03)
    p.add_argument("--exit-ev", type=float, default=None,
                   help="event closes when ev drops below this (default min_ev/2)")
    p.add_argument("--min-books", type=int, default=4)
    p.add_argument("--dump-events", default=None, help="optional CSV path for raw events")
    args = p.parse_args(argv)
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    frames = []
    for m in markets:
        ev = events_for_market(m, since=args.since, until=args.until,
                               min_ev=args.min_ev, exit_ev=args.exit_ev,
                               min_books=args.min_books)
        if len(ev):
            frames.append(ev)
        else:
            print(f"  {m}: no outlier events (thin snapshots or books in line)")
    if not frames:
        print("no events anywhere -- need more intraday snapshot density.")
        return 1
    events = pd.concat(frames, ignore_index=True)
    if args.dump_events:
        events.to_csv(args.dump_events, index=False)
        print(f"raw events -> {args.dump_events}")

    stats = summarize(events)
    print(f"\nstale-quote survival  min_ev={args.min_ev:.0%}  min_books={args.min_books}")
    print("  (strike books with HIGH survival + HIGH book_moved; AVOID high consensus_moved)\n")
    for market, grp in stats.groupby("market"):
        print(f"  {market}")
        for _, r in grp.iterrows():
            print(f"    {r['book']:<14} n={int(r['n_events']):<4} "
                  f"median {r['median_surv_min']:6.1f}m  "
                  f">=30m {r['pct_surv_30m']*100:4.0f}%  >=60m {r['pct_surv_60m']*100:4.0f}%  "
                  f"held-to-close {r['pct_censored']*100:4.0f}%  "
                  f"book-corrected {r['pct_book_moved']*100 if pd.notna(r['pct_book_moved']) else float('nan'):4.0f}%")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
