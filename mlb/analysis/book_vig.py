"""
mlb.analysis.book_vig -- empirical vig (hold) per (market, book) from odds_history.

Why: devig_unilateral() assumes a flat 7% vig for one-sided prop quotes. Real
hold ranges ~2% (sharp books) to ~10%+ (soft books' props), so a flat 7%
systematically biases which quotes look +EV: it overstates fair prob on
high-vig books and understates it on low-vig books. This module measures the
actual two-sided hold per (market, book) wherever BOTH sides of a quote exist
at the same snapshot, and persists a lookup that runners/scanners can use in
place of the flat default.

hold = implied(over) + implied(under) - 1   per (book, game, player, line, snapshot)

Outputs:
  - CLI report: per (market, book) median/mean hold + sample size, softest
    books flagged.
  - GCS JSON (--save): Odds/history/_vig/book_vig.json
      {market: {book: {"vig": <median>, "n": <pairs>}}}

Consumers:
  get_vig(market, book, default=0.07) -- drop-in for the vig_pct arg of
  devig_unilateral(). Falls back to the market-level median, then `default`.

Run (Cloud Shell):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.book_vig --markets hr_yn,k_ou,outs_ou,btb_ou,bhits_ou --since 2026-06-01 --save
"""

from __future__ import annotations

import argparse
import json
import logging

import pandas as pd

from mlb.analysis import odds_history as oh

log = logging.getLogger(__name__)

VIG_KEY = "Odds/history/_vig/book_vig.json"

# Two-sided selection pairs we can measure hold on.
_PAIRS = (("OVER", "UNDER"), ("YES", "NO"), ("HOME", "AWAY"), ("NRFI", "YRFI"))

# Quote identity WITHOUT selection: both sides of the same quote share this.
_QUOTE = ["game_pk", "player_id", "line", "book", "snapshot_ts"]


def pair_holds(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per two-sided quote pair with its hold.

    Input: odds_history rows for ONE market (any date range). Output columns:
    book, game_date, snapshot_ts, hold.
    """
    if df is None or not len(df):
        return pd.DataFrame(columns=["book", "game_date", "snapshot_ts", "hold"])
    df = df[df["implied_prob"].notna()].copy()
    out = []
    for side_a, side_b in _PAIRS:
        a = df[df["selection"] == side_a]
        b = df[df["selection"] == side_b]
        if not len(a) or not len(b):
            continue
        keys = [c for c in _QUOTE if c in df.columns]
        m = a.merge(b, on=keys, suffixes=("_a", "_b"))
        if not len(m):
            continue
        m["hold"] = m["implied_prob_a"] + m["implied_prob_b"] - 1.0
        gd = "game_date_a" if "game_date_a" in m.columns else None
        cols = {"book": m["book"], "snapshot_ts": m["snapshot_ts"], "hold": m["hold"]}
        cols["game_date"] = m[gd] if gd else (m["game_date"] if "game_date" in m.columns else "")
        out.append(pd.DataFrame(cols))
    if not out:
        return pd.DataFrame(columns=["book", "game_date", "snapshot_ts", "hold"])
    res = pd.concat(out, ignore_index=True)
    # Physical bounds: a real two-way hold sits in (-2%, +25%); outside = bad
    # ingest rows (mixed lines, stale one side), not real prices.
    return res[(res["hold"] > -0.02) & (res["hold"] < 0.25)].reset_index(drop=True)


def fit_market(market: str, since: str | None = None, until: str | None = None) -> pd.DataFrame:
    """Per-book hold stats for one market: book, n, vig_median, vig_mean, vig_p90."""
    odds = oh.read_history(market, since=since, until=until)
    if odds is None or not len(odds):
        return pd.DataFrame()
    odds = oh.dedupe_by_source(odds)
    holds = pair_holds(odds)
    if not len(holds):
        return pd.DataFrame()
    g = holds.groupby("book")["hold"]
    stats = pd.DataFrame({
        "n": g.size(),
        "vig_median": g.median().round(4),
        "vig_mean": g.mean().round(4),
        "vig_p90": g.quantile(0.90).round(4),
    }).reset_index()
    stats.insert(0, "market", market)
    return stats.sort_values("vig_median").reset_index(drop=True)


def fit_markets(markets: list, since=None, until=None, min_pairs: int = 30) -> pd.DataFrame:
    frames = [fit_market(m, since=since, until=until) for m in markets]
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out[out["n"] >= min_pairs].reset_index(drop=True)


def save_book_vig(stats: pd.DataFrame) -> str:
    """Persist {market: {book: {vig, n}, "_market": {vig, n}}} to GCS."""
    from mlb_core import storage

    payload: dict = {}
    for market, grp in stats.groupby("market"):
        payload[market] = {
            row["book"]: {"vig": float(row["vig_median"]), "n": int(row["n"])}
            for _, row in grp.iterrows()
        }
        payload[market]["_market"] = {
            "vig": float(grp["vig_median"].median()),
            "n": int(grp["n"].sum()),
        }
    storage.write_bytes(json.dumps(payload, indent=2).encode(), VIG_KEY)
    return VIG_KEY


_VIG_CACHE: dict | None = None


def load_book_vig(force: bool = False) -> dict:
    """Load the persisted vig lookup (cached per process). {} if absent."""
    global _VIG_CACHE
    if _VIG_CACHE is not None and not force:
        return _VIG_CACHE
    from mlb_core import storage

    try:
        _VIG_CACHE = json.loads(storage.read_bytes(VIG_KEY).decode())
    except Exception:  # noqa: BLE001 -- missing file -> empty lookup
        _VIG_CACHE = {}
    return _VIG_CACHE


def get_vig(market: str, book: str, default: float = 0.07) -> float:
    """Empirical vig for (market, book); falls back market-median, then default.

    Drop-in for devig_unilateral(market_prob, vig_pct=get_vig(...)).
    """
    table = load_book_vig().get(market, {})
    for entry in (table.get((book or "").lower()), table.get("_market")):
        if entry and entry.get("n", 0) >= 30:
            return float(entry["vig"])
    return default


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fit empirical per-(market,book) vig from odds_history")
    p.add_argument("--markets", default="hr_yn,k_ou,outs_ou,btb_ou,bhits_ou,nrfi_ou,game_ml")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-pairs", type=int, default=30)
    p.add_argument("--save", action="store_true", help="persist lookup JSON to GCS")
    args = p.parse_args(argv)
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    stats = fit_markets(markets, since=args.since, until=args.until, min_pairs=args.min_pairs)
    if not len(stats):
        print("no two-sided pairs found -- nothing to fit.")
        return 1
    print(f"\nempirical hold per (market, book)  [pairs >= {args.min_pairs}]")
    for market, grp in stats.groupby("market"):
        print(f"\n  {market}  (flat assumption = 7.0%)")
        for _, r in grp.iterrows():
            flag = "  <- SOFT" if r["vig_median"] >= 0.08 else ("  <- SHARP" if r["vig_median"] <= 0.035 else "")
            print(f"    {r['book']:<14} vig {r['vig_median']*100:5.1f}%  "
                  f"(mean {r['vig_mean']*100:5.1f}%, p90 {r['vig_p90']*100:5.1f}%, n={r['n']}){flag}")
    if args.save:
        key = save_book_vig(stats)
        print(f"\nsaved lookup -> {key}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
