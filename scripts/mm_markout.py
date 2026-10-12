"""mm_markout.py -- passive-side MARKOUT on the Kalshi NFL prop tape: the maker's P&L marked to the market price a few
minutes AFTER the fill (instead of held to settlement). This isolates adverse selection (does the price move against the
maker?) from outcome luck and has far lower variance. Reference price at t+d = volume-weighted trade price in the next
`w` minutes (mixes bid-side and ask-side trades ~ mid, so bid/ask bounce mostly cancels).
maker markout per contract: taker bought YES at p -> maker sold YES: p - ref ; taker sold YES -> maker bought: ref - p."""
import glob
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "scripts")
from mm_analysis import load, prep   # noqa: E402

D = sys.argv[1] if len(sys.argv) > 1 else "."
rng = np.random.default_rng(3)


def markout(df, delay_min, win_min=5.0):
    out = []
    d_us, w_us = int(delay_min * 60e6), int(win_min * 60e6)          # pandas 3 stores datetimes in MICROseconds
    for tk, g in df.sort_values("created_time").groupby("ticker", sort=False):
        t = g["created_time"].astype("datetime64[us, UTC]").astype("int64").to_numpy()
        p, n = g["p"].to_numpy(), g["n"].to_numpy()
        cn, cpn = np.concatenate([[0], np.cumsum(n)]), np.concatenate([[0], np.cumsum(n * p)])
        lo = np.searchsorted(t, t + d_us, side="left")
        hi = np.searchsorted(t, t + d_us + w_us, side="right")
        vol = cn[hi] - cn[lo]
        ok = vol > 0
        ref = np.where(ok, (cpn[hi] - cpn[lo]) / np.where(ok, vol, 1), np.nan)
        sold = (g["taker_side"] == "yes").to_numpy()
        mo = np.where(sold, p - ref, ref - p)
        sub = g.assign(ref=ref, mo=mo, ok=ok)
        out.append(sub[sub["ok"]])
    return pd.concat(out) if out else df.iloc[:0]


def stat(sub, B=1500):
    g = sub.assign(w=sub["mo"] * sub["n"]).groupby("game")[["w", "n"]].sum()
    pnl, n = g["w"].to_numpy(), g["n"].to_numpy()
    idx = rng.integers(0, len(g), size=(B, len(g)))
    vals = 100 * pnl[idx].sum(1) / np.maximum(n[idx].sum(1), 1e-9)
    return 100 * pnl.sum() / n.sum(), np.percentile(vals, [2.5, 97.5])


if __name__ == "__main__":
    df = pd.concat([prep(load(f)) for f in sorted(glob.glob(f"{D}/KX*.csv.gz"))], ignore_index=True)
    pre = df[df["mtk"] > 0]
    print(f"{len(pre):,} pre-kick trades, {pre['ticker'].nunique():,} markets, {pre['game'].nunique()} games")
    half = None
    for dm in (1, 5, 15, 30):
        m = markout(pre[pre["mtk"] > dm + 10], dm)
        e, (lo, hi) = stat(m)
        print(f"markout +{dm:>2d}min  trades={len(m):>8,d} contracts={m['n'].sum():>12,.0f}  maker markout={e:+5.2f}c/contract [{lo:+.2f},{hi:+.2f}]")
    m5 = markout(pre[pre["mtk"] > 15], 5)
    print("\n-- by trade size (markout +5min) --")
    for a, b, lab in ((0, 10, "<10"), (10, 100, "10-100"), (100, 1000, "100-1000"), (1000, 1e12, ">=1000")):
        s = m5[(m5["n"] >= a) & (m5["n"] < b)]
        e, (lo, hi) = stat(s) if len(s) else (np.nan, (np.nan, np.nan))
        print(f"  size {lab:9s} trades={len(s):>8,d} contracts={s['n'].sum():>12,.0f}  {e:+5.2f}c [{lo:+.2f},{hi:+.2f}]")
    print("-- by time to kickoff (markout +5min) --")
    for a, b, lab in ((1440, 1e9, ">24h"), (360, 1440, "6-24h"), (60, 360, "1-6h"), (20, 60, "20-60m")):
        s = m5[(m5["mtk"] > a) & (m5["mtk"] <= b)]
        e, (lo, hi) = stat(s) if len(s) else (np.nan, (np.nan, np.nan))
        print(f"  {lab:9s} trades={len(s):>8,d} contracts={s['n'].sum():>12,.0f}  {e:+5.2f}c [{lo:+.2f},{hi:+.2f}]")
    print("-- by series (markout +5min) --")
    for ser, s in m5.groupby("series"):
        e, (lo, hi) = stat(s)
        print(f"  {ser:14s} contracts={s['n'].sum():>12,.0f}  {e:+5.2f}c [{lo:+.2f},{hi:+.2f}]")
    print("-- by price band (markout +5min) --")
    for a, b in ((0, .1), (.1, .3), (.3, .7), (.7, 1.01)):
        s = m5[(m5["p"] >= a) & (m5["p"] < b)]
        e, (lo, hi) = stat(s) if len(s) else (np.nan, (np.nan, np.nan))
        print(f"  price {a:.1f}-{b:.1f} contracts={s['n'].sum():>12,.0f}  {e:+5.2f}c [{lo:+.2f},{hi:+.2f}]")
