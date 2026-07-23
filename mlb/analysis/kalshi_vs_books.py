"""
mlb.analysis.kalshi_vs_books -- soft-book +EV vs the Kalshi sharp reference.

The strategy (2026-07 profit review): there is NO capturable model-vs-line edge;
the edge is intraday SOFT-LINE +EV measured against a sharp reference. Kalshi is
a no-vig exchange, so its (normalized) mid is that reference. This scans
odds_history and, for every book quote that shares a
(market, game_pk, player_id, selection, line) with a Kalshi mid, computes:

    p_true  = Kalshi mid, normalized so the two sides of the market sum to 1
              (removes the half-spread double-count)
    ev_pct  = p_true * book_decimal - 1        # EV of betting THIS side at the book
    edge    = p_true - book_implied_prob       # how much too-generous the book is

Positive ev_pct = the book is offering better than fair per the sharp exchange
= a soft-line +EV bet. We bet the BOOK (which has size); Kalshi is only the
truth estimate, so Kalshi's taker fee does NOT apply (it would only matter if we
also traded the Kalshi side to arb -- reported as kalshi_fee for reference).

Books are flagged soft/sharp via mlb.analysis.book_vig.get_vig (>=8% hold=soft).
Trust the signal most on the liquid Kalshi markets (nrfi_ou/game_ml/total_ou/
runline); prop mids (hr_yn/k_ou/btb_ou/...) are thin -> treat as soft evidence.

Run (Cloud Shell; needs GCS):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --date 2026-07-23 --min-ev 0.03
  # closing-line divergence only:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --date 2026-07-23 --closing
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from mlb.analysis import book_vig, odds_history as oh


def _player_names(date: str) -> dict:
    """Best-effort {player_id: name} from the latest banked Kalshi raw snapshot
    (Odds/kalshi/raw/<date>/), so prop rows show the pitcher/batter, not an id."""
    from mlb_core import storage
    try:
        keys = sorted(storage.list_keys(f"Odds/kalshi/raw/{date}/"))
        raw = json.loads(storage.read_bytes(keys[-1])) if keys else []
        return {r["player_id"]: r["player"] for r in raw
                if r.get("player_id") is not None and r.get("player")}
    except Exception:  # noqa: BLE001 -- names are a nicety; fall back to ids
        return {}

DEFAULT_MARKETS = ["nrfi_ou", "game_ml", "game_total", "game_rl", "f5_ml",
                   "hr_yn", "k_ou", "outs_ou", "btb_ou", "bhits_ou"]
LIQUID = {"nrfi_ou", "game_ml", "game_total", "game_rl"}   # trustworthy Kalshi mids
# Markets whose two selections are NOT complementary (do not normalize to sum 1).
# Run line: "HOME by >N" and "AWAY by >N" are both false when margin < N+.5.
NO_NORM = {"game_rl"}
# Per-PLAYER prop markets (a row is one pitcher/batter, NOT a game-level line).
# The join keys on player_id; we also drop unresolved players (_pid == -1) here
# so the sentinel can never cross-match one player's Kalshi quote to another's.
PLAYER_MARKETS = {"hr_yn", "k_ou", "outs_ou", "btb_ou", "bhits_ou"}
_JOIN = ["market", "game_pk", "_pid", "_line", "selection"]
_PAIR = ["market", "game_pk", "_pid", "_line"]           # a two-sided quote


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    """Latest snapshot per (quote, book); NaN-safe join keys."""
    df = df[df["implied_prob"].notna()].copy()
    df["_pid"] = df["player_id"].fillna(-1)
    df["_line"] = df["line"].fillna(-99.0)
    df = df.sort_values("snapshot_ts")
    keys = ["market", "game_pk", "_pid", "_line", "selection", "book"]
    return df.drop_duplicates(subset=keys, keep="last")


def _kalshi_truth(k: pd.DataFrame, normalize: bool = True) -> pd.DataFrame:
    """p_true from Kalshi mids. For complementary 2-way markets, normalize each
    quote's sides to sum to 1 (removes the half-spread double-count). For non-
    complementary markets (normalize=False, e.g. run line) the raw mid already
    IS the probability of that side, so use it directly."""
    k = k.copy()
    if normalize:
        pair_sum = k.groupby(_PAIR)["fair_prob"].transform("sum")
        n_sides = k.groupby(_PAIR)["selection"].transform("size")
        k["p_true"] = k["fair_prob"].where(n_sides < 2, k["fair_prob"] / pair_sum)
    else:
        k["p_true"] = k["fair_prob"]
    return k[_JOIN + ["p_true", "fair_prob", "snapshot_ts"]].rename(
        columns={"fair_prob": "k_mid", "snapshot_ts": "k_ts"})


def scan(markets, date=None, since=None, until=None, closing=False,
         min_ev=0.0) -> pd.DataFrame:
    frames, cov = [], []
    for mkt in markets:
        odds = oh.read_history(mkt, since=since or date, until=until or date)
        if odds is None or not len(odds):
            continue
        odds = oh.dedupe_by_source(odds)
        if closing and "is_closing" in odds.columns:
            odds = odds[odds["is_closing"] == True]  # noqa: E712
        if not len(odds):
            continue
        d = _prep(odds)
        if mkt in PLAYER_MARKETS:            # keep only resolved players
            d = d[d["_pid"] != -1]
        k = d[d["book"] == "kalshi"]
        books = d[d["book"] != "kalshi"]
        cov.append({"market": mkt, "kalshi_sel": len(k), "book_rows": len(books),
                    "books": ",".join(sorted(books["book"].unique()))})
        if not len(k) or not len(books):
            continue
        truth = _kalshi_truth(k, normalize=(mkt not in NO_NORM))
        # per-selection book consensus: how many books, and their median implied.
        # Lets us tell "one book is off (real edge)" from "Kalshi is the lone
        # outlier vs the whole book pack (suspect mid)".
        cons = (books.groupby(_JOIN)["implied_prob"]
                .agg(cons_impl="median", n_books="size").reset_index())
        m = (books.merge(truth, on=_JOIN, how="inner", suffixes=("", "_k"))
                  .merge(cons, on=_JOIN, how="left"))
        if not len(m):
            continue
        m["ev_pct"] = (m["p_true"] * m["decimal"] - 1.0).round(4)
        m["edge"] = (m["p_true"] - m["implied_prob"]).round(4)
        m["cons_impl"] = m["cons_impl"].round(4)
        m["k_dev"] = (m["p_true"] - m["cons_impl"]).round(4)   # +/- = Kalshi vs book pack
        m["bk_dev"] = (m["implied_prob"] - m["cons_impl"]).round(4)  # book vs pack (neg=cheap)
        m["kalshi_fee"] = (0.07 * m["p_true"] * (1 - m["p_true"])).round(4)
        m["vig"] = [book_vig.get_vig(mkt, b) for b in m["book"]]
        m["soft"] = m["vig"] >= 0.08
        m["game"] = m["away_team"].astype(str) + "@" + m["home_team"].astype(str)
        frames.append(m)

    print("coverage (rows in odds_history for this window):")
    for c in cov:
        print(f"  {c['market']:10} kalshi_sel={c['kalshi_sel']:4} "
              f"book_rows={c['book_rows']:4}  books=[{c['books']}]")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out[out["ev_pct"] >= min_ev]
    return out.sort_values("ev_pct", ascending=False).reset_index(drop=True)


def main(argv=None) -> int:
    from datetime import datetime, timezone
    p = argparse.ArgumentParser(description="Soft-book +EV vs Kalshi sharp reference")
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    p.add_argument("--date", default=None, help="single game_date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--closing", action="store_true", help="compare closing snapshots only")
    p.add_argument("--min-ev", type=float, default=0.03, help="min ev_pct to show (default 0.03)")
    p.add_argument("--soft-only", action="store_true", help="only soft books (vig>=8%)")
    p.add_argument("--liquid-only", action="store_true",
                   help="only markets with trustworthy Kalshi mids (nrfi/game/total/runline)")
    p.add_argument("--top", type=int, default=40)
    args = p.parse_args(argv)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    if args.liquid_only:
        markets = [m for m in markets if m in LIQUID]
    date = args.date or (None if (args.since or args.until)
                         else datetime.now(timezone.utc).date().isoformat())

    df = scan(markets, date=date, since=args.since, until=args.until,
              closing=args.closing, min_ev=args.min_ev)
    if not len(df):
        print("\nno +EV divergences (or no overlapping kalshi+book quotes in window).")
        return 0
    if args.soft_only:
        df = df[df["soft"]]
    names = _player_names(date) if date else {}
    df["player"] = df["player_id"].map(names).fillna(
        df["player_id"].map(lambda p: "" if pd.isna(p) else str(int(p))))
    cols = ["market", "game", "player", "selection", "line", "book", "american",
            "implied_prob", "cons_impl", "n_books", "p_true", "k_dev",
            "ev_pct", "soft"]
    cols = [c for c in cols if c in df.columns]
    print(f"\n{len(df)} soft-book +EV candidates vs Kalshi (min_ev={args.min_ev}, "
          f"{'CLOSING' if args.closing else 'latest'} snapshots):")
    print("  read: k_dev = Kalshi p_true - book-pack median. |k_dev| small => Kalshi")
    print("  agrees with the pack and the lone cheap book is a real edge; |k_dev|")
    print("  large => Kalshi is the outlier (thin/suspect mid), discount the row.\n")
    with pd.option_context("display.max_rows", args.top, "display.width", 200):
        print(df[cols].head(args.top).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
