"""Probe ev.props.shift_p_over / POOL_SD_FRAC against historical BettingPros NFL yardage quotes. READ-ONLY.

    GCS_BUCKET=<bucket> python scripts/pooled_shift_probe.py --mode cross  [--start 2023-09-01 --end 2026-02-28]
    GCS_BUCKET=<bucket> python scripts/pooled_shift_probe.py --mode within

--mode cross   pairs of DIFFERENT books quoting different points (|gap| <= 1) for the same player/event; predicts book B's
               de-vigged P(over) at its own point from book A's at A's point, with the production shift.
--mode within  pairs of quotes from the SAME book at different points (an alt ladder), where the mean is shared by construction.

FINDINGS (2026-10-15, docs/analysis/bettingpros_model_vs_market_2026-10-15.md):
  - cross is NOT a validation. Each book posts its own line near a 50/50 price, so two books quoting different points
    genuinely disagree about the mean; the unshifted other-book price beats the shifted one (MAE 0.004-0.010 vs 0.010-0.036)
    and that says nothing about whether the normal shift or POOL_SD_FRAC is right.
  - within is the right test but BettingPros history carries ~one line per book (0 pairs pass_yds, 44 rush_yds, 132 rec_yds),
    so it cannot run. The shift model stays UNVALIDATED from this data; an outcome-based test (nflverse player_stats join)
    is the remaining route.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations, permutations

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.props import POOL_SD_FRAC, shift_p_over  # noqa: E402
from mlb_core import storage  # noqa: E402
from mlb_core.odds import bettingpros_props as bpp  # noqa: E402
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way  # noqa: E402

MARKETS = {"passing-yards": "pass_yds", "rushing-yards": "rush_yds", "receiving-yards": "rec_yds"}
SKIP_BOOKS = {"kalshi", "polymarket"}
MULTS = (0.5, 0.75, 1.0, 1.5, 2.0)


def load(market: str, start: str, end: str) -> pd.DataFrame:
    prefix = bpp.default_prefix("nfl")
    dates = [d for d in sorted(bpp.done_dates(prefix, market)) if start <= d <= end]

    def read(d):
        try:
            return storage.read_parquet(bpp.day_key(prefix, market, d))
        except Exception:
            return None

    with ThreadPoolExecutor(16) as ex:
        frames = [f for f in ex.map(read, dates) if f is not None and len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=bpp.COLUMNS)


def fair_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (event, player, book, line): proportional-devigged P(over) from real two-sided books/exchanges."""
    df = df[df.book_kind.isin(["book", "exchange"]) & ~df.is_open.astype(bool)]
    df = df[~df.book.str.lower().isin(SKIP_BOOKS)]
    key = ["event_id", "player_id", "book", "line"]
    ov = df[df.side == "over"].drop_duplicates(key, keep="last").set_index(key)["odds"]
    un = df[df.side == "under"].drop_duplicates(key, keep="last").set_index(key)["odds"]
    j = pd.concat([ov.rename("o"), un.rename("u")], axis=1).dropna().reset_index()
    j["fair"] = [float(devig_two_way(american_to_implied_prob(o), american_to_implied_prob(u), "proportional")[0])
                 for o, u in zip(j.o, j.u)]
    return j


def cross_pairs(j: pd.DataFrame, gap: float) -> pd.DataFrame:
    rows = []
    for _, g in j.groupby(["event_id", "player_id"]):
        if g.line.nunique() < 2:
            continue
        for (ba, a, fa), (bb, b, fb) in permutations(g[["book", "line", "fair"]].values.tolist(), 2):
            if ba != bb and a != b and abs(a - b) <= gap:
                rows.append((a, b, fa, fb))
    return pd.DataFrame(rows, columns=["a", "b", "fa", "fb"])


def within_pairs(j: pd.DataFrame, gap: float) -> pd.DataFrame:
    rows = []
    for _, g in j.groupby(["event_id", "player_id", "book"]):
        if len(g) < 2:
            continue
        for (a, fa), (b, fb) in combinations(g[["line", "fair"]].values.tolist(), 2):
            if a != b and abs(a - b) <= gap:
                rows += [(a, b, fa, fb), (b, a, fb, fa)]
    return pd.DataFrame(rows, columns=["a", "b", "fa", "fb"])


def report(stat: str, p: pd.DataFrame, mode: str) -> None:
    print(f"\n== {stat} ({mode}): pairs={len(p)}")
    if len(p) < 200:
        print("too few pairs to say anything")
        return
    base = p.fa - p.fb
    print(f"unshifted      bias={base.mean():+.4f} MAE={base.abs().mean():.4f}")
    for mult in MULTS:
        sd = POOL_SD_FRAC[stat] * mult
        pred = np.array([shift_p_over(fa, a, b, sd * max(b, 1.0)) for a, b, fa in zip(p.a, p.b, p.fa)])
        err = pred - p.fb.values
        print(f"sd x{mult:<5}     bias={err.mean():+.4f} MAE={np.abs(err).mean():.4f} "
              f"se={err.std() / np.sqrt(len(err)):.4f}{'  <- production' if mult == 1.0 else ''}")
    d = (p.fb - p.fa) / (p.b - p.a)
    print(f"empirical dP/dpoint median={d.median():+.4f}  (model at production sd, p=0.5: "
          f"{-0.3989 / (POOL_SD_FRAC[stat] * max(p.b.median(), 1)):+.4f})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["cross", "within"], required=True)
    ap.add_argument("--start", default="2023-09-01")
    ap.add_argument("--end", default="2026-02-28")
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--markets", default=",".join(MARKETS), help="comma list of BettingPros slugs")
    a = ap.parse_args(argv)
    for slug in [m.strip() for m in a.markets.split(",") if m.strip()]:
        df = load(slug, a.start, a.end)
        if df.empty:
            print(f"\n== {MARKETS[slug]}: no partitions in range")
            continue
        j = fair_table(df)
        p = (cross_pairs if a.mode == "cross" else within_pairs)(j, a.gap)
        report(MARKETS[slug], p, a.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
