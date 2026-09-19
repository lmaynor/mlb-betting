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

THE MIRROR (2026-09-18, --side kalshi): everywhere else in this repo Kalshi is
excluded as a bettable book (backtest_market.OFFSHORE) because it's a sharp
reference, not a sportsbook we can place at. But it IS an exchange with a
public order-placement API -- something no sportsbook here offers -- so the
reverse question is worth asking: is KALSHI's own contract mispriced vs the
real books, i.e. is buying Kalshi's ask +EV against the devigged book-pack
consensus? scan_kalshi_side()/_kalshi_ev() compute exactly that (same shape as
scan() above, roles swapped); validate_kalshi_side() settles flagged historical
quotes vs real outcomes for the markets with a known ground-truth source
(currently game_ml, nrfi_ou -- see _REALIZED_SOURCE). This is a market-
structure question, not a trading action: it does not place any order.

First-pass backtest (2026-09-19, game_ml+nrfi_ou): over Kalshi's full history
(2026-05-17..today) this looked strongly -EV (ROI -32.8%, t=-4.56, n=970) --
but that's dominated by stale/thin prints in the 2026-05-17..07-22 CLOSING-
CANDLE historical backfill (see docs/solutions/integration-issues/
kalshi-historical-backfill-stale-closing-candles.md); on the clean live
forward-capture window alone (--since 2026-08-10, n=441) it's ROI -8.2%,
t=-0.645 -- NOT statistically significant, i.e. no proven edge either way yet.
Always bound --since to 2026-08-10+ for this market until more live history
accumulates; the pre-08-10 window will manufacture a fake, much-worse-looking
"edge" than what real intraday data shows.

Run (Cloud Shell; needs GCS):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --date 2026-07-23 --min-ev 0.03
  # closing-line divergence only:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --date 2026-07-23 --closing
  # the mirror direction, on the clean live-capture window, with a real-outcome go/no-go:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_vs_books --side kalshi \
      --markets game_ml,nrfi_ou --since 2026-08-10 --min-ev 0.03 --validate
"""

from __future__ import annotations

import argparse
import json

import numpy as np
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


def _kalshi_book_fair(books: pd.DataFrame, min_books: int = 4) -> pd.DataFrame:
    """Devigged real-book consensus per (market, game, player, line, selection):
    median of each book's own already-2-way-devigged fair_prob -- the SAME truth
    estimate outlier_scan.py's consensus_fair uses. NOT scan()'s cons_impl (a raw
    vig-loaded median, fine as a rough corroboration check but not a real
    fair-value anchor -- using it as truth would make every book's own vig look
    like free Kalshi edge). Drops groups under min_books (thin/untrustworthy)."""
    cons = (books.groupby(_JOIN)["fair_prob"]
            .agg(book_fair="median", n_books="size").reset_index())
    return cons[cons["n_books"] >= min_books]


def _kalshi_ev(k: pd.DataFrame, cons: pd.DataFrame) -> pd.DataFrame:
    """Mirror of scan()'s EV math, opposite direction: is buying KALSHI's own
    contract (at its own ask -- executable via its order book/API, something no
    sportsbook offers us) +EV vs the real book pack's devigged consensus (`cons`,
    from _kalshi_book_fair)? `k` = Kalshi's own quote rows (implied_prob=its ask,
    decimal=1/ask, both set at ingestion).

        ev_pct = book_fair * kalshi_decimal - 1     # EV of buying Kalshi's own ask
        edge   = book_fair - kalshi_implied_prob    # +ve = Kalshi looks cheap

    Kalshi's own taker fee is NOT netted in here (same convention as scan()'s
    kalshi_fee column) -- back it out before sizing anything real."""
    m = k.merge(cons, on=_JOIN, how="inner")
    if not len(m):
        return m
    m = m.copy()
    m["ev_pct"] = (m["book_fair"] * m["decimal"] - 1.0).round(4)
    m["edge"] = (m["book_fair"] - m["implied_prob"]).round(4)
    m["book_fair"] = m["book_fair"].round(4)
    return m


def scan_kalshi_side(markets, date=None, since=None, until=None, closing=False,
                      min_ev=0.0, min_books=4) -> pd.DataFrame:
    """Mirror of scan(): scan() treats a sportsbook as bettable and Kalshi's mid
    as the truth anchor; this treats KALSHI as bettable and the real books'
    devigged consensus as the truth anchor -- see _kalshi_book_fair/_kalshi_ev
    for the actual math. Trust the liquid markets (LIQUID) most; prop mids are thin.
    """
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
        if mkt in PLAYER_MARKETS:
            d = d[d["_pid"] != -1]
        k = d[d["book"] == "kalshi"]
        books = d[d["book"] != "kalshi"]
        cov.append({"market": mkt, "kalshi_sel": len(k), "book_rows": len(books)})
        if not len(k) or not len(books):
            continue
        m = _kalshi_ev(k, _kalshi_book_fair(books, min_books))
        if not len(m):
            continue
        m["game"] = m["away_team"].astype(str) + "@" + m["home_team"].astype(str)
        frames.append(m)

    print("coverage (rows in odds_history for this window):")
    for c in cov:
        print(f"  {c['market']:10} kalshi_sel={c['kalshi_sel']:4} book_rows={c['book_rows']:4}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out[out["ev_pct"] >= min_ev]
    return out.sort_values("ev_pct", ascending=False).reset_index(drop=True)


# Realized-outcome ground truth, reused VERBATIM from each system's own training
# table -- never re-derive a label this repo already computes correctly elsewhere.
# Only markets with an existing feature table are covered; game_total/game_rl/
# f5_ml/props have no registered system behind them yet (no ground-truth source),
# so validate_kalshi_side() reports them as unmatched rather than guessing.
_REALIZED_SOURCE = {
    "game_ml": ("GAME_Pro_System/data/model_features.csv", "home_win"),
    "nrfi_ou": ("NRFI_Pro_System/data/model_features.csv", "yrfi"),
}


def _realized_outcomes(market: str) -> dict:
    """{game_pk: realized_label} from the market's own feature table, or {} if
    this market has no known ground-truth source yet."""
    src = _REALIZED_SOURCE.get(market)
    if not src:
        return {}
    import io
    from mlb_core import storage
    feature_csv, label_col = src
    df = pd.read_csv(io.BytesIO(storage.read_bytes(feature_csv)),
                     usecols=["game_pk", label_col]).dropna()
    return {int(g): float(v) for g, v in zip(df["game_pk"], df[label_col])}


def _kalshi_won(market: str, selection: str, realized: float) -> float | None:
    """1.0 win / 0.0 loss for a Kalshi-side bet. Mirrors backtest_market._won's
    binary-market logic for the two markets with a real ground-truth source."""
    if realized is None or pd.isna(realized):
        return None
    sel = (selection or "").upper()
    if market == "game_ml":
        return 1.0 if (sel == "HOME") == (realized >= 1) else 0.0
    if market == "nrfi_ou":
        return 1.0 if (sel == "YRFI") == (realized >= 1) else 0.0
    return None


def _tstat(vals) -> float | None:
    """t-stat of the mean. Guards against float64 rounding noise on
    near-identical values (e.g. three 0.1s) producing a near-zero-but-not-exactly-
    zero sem, which would otherwise blow up into a meaningless ~1e16 t-stat
    instead of the "no real variance, no signal" None a truly flat sample should
    report -- caught via test_none_when_zero_variance, not a hypothetical."""
    from scipy import stats as scipy_stats
    vals = vals.dropna()
    if len(vals) < 2:
        return None
    sem = float(scipy_stats.sem(vals))
    return round(float(vals.mean()) / sem, 3) if sem > 1e-9 else None


def validate_kalshi_side(df: pd.DataFrame) -> None:
    """Settle flagged Kalshi-side +EV quotes (from scan_kalshi_side) vs REAL
    outcomes -- the go/no-go. A Kalshi-side edge is only real if buying these
    flagged contracts actually profits, same bar hr_softline.validate() uses
    (ROI>0 AND t-stat significant). Only settles markets with a known
    ground-truth source (_REALIZED_SOURCE); others are reported unmatched
    rather than guessed at."""
    if df is None or not len(df):
        print("\nvalidate_kalshi_side: no flagged quotes to settle.")
        return
    q = df.copy()
    outcomes = {m: _realized_outcomes(m) for m in q["market"].unique()}
    q["realized"] = [outcomes.get(m, {}).get(int(g)) if pd.notna(g) else None
                     for m, g in zip(q["market"], q["game_pk"])]
    unmatched_markets = sorted(set(q.loc[q["realized"].isna(), "market"]) - set(_REALIZED_SOURCE))
    matched = q[q["realized"].notna()].copy()
    if not len(matched):
        print(f"\nvalidate_kalshi_side: 0/{len(q)} flagged quotes matched a ground-truth "
              f"outcome (no source yet for: {unmatched_markets or 'n/a'}).")
        return
    matched["won"] = [_kalshi_won(m, s, r) for m, s, r in
                      zip(matched["market"], matched["selection"], matched["realized"])]
    matched = matched[matched["won"].notna()]
    matched["roi"] = matched["won"].mul(matched["decimal"] - 1.0).where(matched["won"] == 1, -1.0)

    t = _tstat(matched["roi"])
    print("\n=== REALIZED validation: flagged Kalshi-side +EV quotes settled vs actual outcomes ===")
    print(f"  n={len(matched)} (skipped {len(q) - len(matched)}: no ground truth yet for "
          f"{unmatched_markets or 'none'})")
    print(f"  hit%={matched['won'].mean()*100:.1f}  ROI={matched['roi'].mean()*100:+.1f}%  "
          f"units={matched['roi'].sum():+.1f}  t-stat={t}"
          + ("  (|t|<2 -> NOT yet significant, small-n variance)"
             if t is not None and abs(t) < 2 else ""))
    by = matched.groupby("market").agg(n=("won", "size"), hit=("won", "mean"),
                                       roi=("roi", "mean"), tstat=("roi", _tstat))
    by["hit"] = (by["hit"] * 100).round(1)
    by["roi"] = (by["roi"] * 100).round(1)
    print(by.sort_values("roi", ascending=False).to_string())
    print("  REAL edge => ROI > 0 AND t-stat significant (|t|>=2) on decent n. ROI<=0 or\n"
          "  |t|<2 => not yet distinguishable from noise. This assumes execution at the\n"
          "  historical ask with no slippage/size limit -- Kalshi's own book depth isn't\n"
          "  in odds_history, so treat this as an optimistic upper bound on the real edge.")


def classify(df, kdev_max=0.04, stale_gap=0.15, min_books=4):
    """Add a `verdict` column separating credible edges from artifacts:
    thin_pack (too few books), kalshi_off (|k_dev|>kdev_max => Kalshi disagrees
    with the whole pack), stale? (book >stale_gap below consensus => stale/
    placeholder quote), check (survives all = modest, corroborated edge)."""
    if not len(df):
        return df
    df = df.copy()
    df["verdict"] = np.select(
        [df["n_books"] < min_books,
         df["k_dev"].abs() > kdev_max,
         df["bk_dev"] < -stale_gap],
        ["thin_pack", "kalshi_off", "stale?"],
        default="check")
    return df


def main(argv=None) -> int:
    from datetime import datetime, timezone
    p = argparse.ArgumentParser(description="Soft-book +EV vs Kalshi sharp reference")
    p.add_argument("--side", choices=["books", "kalshi"], default="books",
                   help="'books' (default): is a sportsbook +EV vs Kalshi's mid. "
                        "'kalshi': the mirror -- is KALSHI's own contract +EV vs "
                        "the real book pack (the side we could execute via its API).")
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    p.add_argument("--date", default=None, help="single game_date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--closing", action="store_true", help="compare closing snapshots only")
    p.add_argument("--min-ev", type=float, default=0.03, help="min ev_pct to show (default 0.03)")
    p.add_argument("--soft-only", action="store_true", help="only soft books (vig>=8%)")
    p.add_argument("--liquid-only", action="store_true",
                   help="only markets with trustworthy Kalshi mids (nrfi/game/total/runline)")
    p.add_argument("--kdev-max", type=float, default=0.04,
                   help="max |p_true - book consensus| for a trusted row (default 0.04)")
    p.add_argument("--stale-gap", type=float, default=0.15,
                   help="flag a book quote this far below consensus as stale (default 0.15)")
    p.add_argument("--min-books", type=int, default=4,
                   help="min books for a trustworthy consensus (default 4)")
    p.add_argument("--all", action="store_true",
                   help="show every verdict (default: only credible 'check' rows)")
    p.add_argument("--validate", action="store_true",
                   help="--side kalshi only: settle flagged quotes vs real outcomes (go/no-go)")
    p.add_argument("--top", type=int, default=40)
    args = p.parse_args(argv)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    if args.liquid_only:
        markets = [m for m in markets if m in LIQUID]
    date = args.date or (None if (args.since or args.until)
                         else datetime.now(timezone.utc).date().isoformat())

    if args.side == "kalshi":
        df = scan_kalshi_side(markets, date=date, since=args.since, until=args.until,
                              closing=args.closing, min_ev=args.min_ev,
                              min_books=args.min_books)
        if not len(df):
            print("\nno +EV Kalshi-side divergences (or no overlapping kalshi+book quotes in window).")
            return 0
        names = _player_names(date) if date else {}
        df["player"] = df["player_id"].map(names).fillna(
            df["player_id"].map(lambda p: "" if pd.isna(p) else str(int(p))))
        cols = ["market", "game", "player", "selection", "line", "american",
                "implied_prob", "book_fair", "n_books", "edge", "ev_pct", "snapshot_ts"]
        cols = [c for c in cols if c in df.columns]
        print(f"\n{len(df)} +EV Kalshi-side quotes (buying Kalshi's own ask vs the "
              f"devigged book-pack consensus, min_ev={args.min_ev}, min_books={args.min_books}):")
        print("  ev_pct/edge are gross of Kalshi's own taker fee -- back it out before sizing.\n")
        with pd.option_context("display.max_rows", args.top, "display.width", 220):
            print(df[cols].head(args.top).to_string(index=False))
        if args.validate:
            validate_kalshi_side(df)
        return 0

    df = scan(markets, date=date, since=args.since, until=args.until,
              closing=args.closing, min_ev=args.min_ev)
    if not len(df):
        print("\nno +EV divergences (or no overlapping kalshi+book quotes in window).")
        return 0
    if args.soft_only:
        df = df[df["soft"]]
    df = classify(df, kdev_max=args.kdev_max, stale_gap=args.stale_gap,
                  min_books=args.min_books)
    names = _player_names(date) if date else {}
    df["player"] = df["player_id"].map(names).fillna(
        df["player_id"].map(lambda p: "" if pd.isna(p) else str(int(p))))
    shown = df if args.all else df[df["verdict"] == "check"]
    cols = ["market", "game", "player", "selection", "line", "book", "american",
            "implied_prob", "cons_impl", "n_books", "p_true", "k_dev", "bk_dev",
            "ev_pct", "verdict", "soft"]
    cols = [c for c in cols if c in shown.columns]
    counts = df["verdict"].value_counts().to_dict()
    print(f"\nverdict mix (min_ev={args.min_ev}): {counts}")
    print(f"showing {len(shown)} {'ALL rows' if args.all else 'credible check rows'} "
          f"({'CLOSING' if args.closing else 'latest'} snapshots):")
    print("  check = |k_dev|<=kdev_max (Kalshi corroborates the pack) AND book not")
    print("  stale; kalshi_off = Kalshi is the lone outlier; stale? = junk book quote.\n")
    with pd.option_context("display.max_rows", args.top, "display.width", 220):
        print(shown[cols].head(args.top).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
