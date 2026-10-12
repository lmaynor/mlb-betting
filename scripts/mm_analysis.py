"""mm_analysis.py -- market-making economics on Kalshi NFL prop/game tapes (public, settled). Read-only analysis.

Core measurement (no model): for every trade, the PASSIVE side's P&L held to settlement,
    taker bought YES at p  -> maker sold YES : p - y        taker bought NO (yes_price p) -> maker bought YES : y - p
y = 1 if the market settled YES. This is the realized, adverse-selection-inclusive return of a maker who provided
liquidity on that trade at the traded price. Kalshi prop series are fee_type 'quadratic' (maker fee 0).
Kickoff = occurrence_datetime - 3h (Kalshi sets expected expiration = scheduled start + 3h; verified on MNF).
Clusters for CIs = games (event suffix), the unit of independence."""
import glob
import gzip
import io
import sys

import numpy as np
import pandas as pd

D = sys.argv[1] if len(sys.argv) > 1 else "."
rng = np.random.default_rng(7)


def load(path):
    raw = b""
    with gzip.open(path, "rb") as fh:                     # tolerate a file still being written
        try:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                raw += chunk
        except (EOFError, OSError):
            pass
    nl = raw.rfind(b"\n")
    return pd.read_csv(io.BytesIO(raw[: nl + 1]))


def prep(df):
    df = df.copy()
    for c in ("created_time", "occurrence"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    df = df.dropna(subset=["created_time", "occurrence"])
    df["kick"] = df["occurrence"] - pd.Timedelta(hours=3)
    df["mtk"] = (df["kick"] - df["created_time"]).dt.total_seconds() / 60.0           # minutes to kickoff
    df["y"] = (df["result"] == "yes").astype(float)
    p, n = df["yes_price"].astype(float), df["count"].astype(float)
    sold_yes = df["taker_side"] == "yes"
    df["p"], df["n"] = p, n
    df["maker_pnl"] = np.where(sold_yes, p - df["y"], df["y"] - p) * n                  # dollars
    df["maker_cap"] = np.where(sold_yes, 1 - p, p) * n                                  # capital at risk
    df["game"] = df["event"].str.split("-").str[1]
    df["day"] = df["game"].str[:7]
    return df


def boot(df, f, B=2000):
    """cluster-bootstrap (by game) 95% CI for the maker edge in cents/contract. Resamples per-game sums (equivalent to
    resampling games with their trades, since the statistic is a ratio of sums)."""
    g = df.groupby("game")[["maker_pnl", "n"]].sum()
    pnl, n = g["maker_pnl"].to_numpy(), g["n"].to_numpy()
    k = len(g)
    idx = rng.integers(0, k, size=(B, k))
    vals = 100 * pnl[idx].sum(axis=1) / np.maximum(n[idx].sum(axis=1), 1e-9)
    return np.percentile(vals, [2.5, 97.5])


def edge(sub):                       # maker pnl per contract (cents)
    return 100 * sub["maker_pnl"].sum() / max(sub["n"].sum(), 1e-9)


def roi(sub):                        # maker pnl / capital at risk (%)
    return 100 * sub["maker_pnl"].sum() / max(sub["maker_cap"].sum(), 1e-9)


def line(label, sub, ci=True):
    if len(sub) == 0:
        print(f"  {label:34s} (none)"); return
    lo, hi = boot(sub, edge, 600) if ci and sub["game"].nunique() > 3 else (np.nan, np.nan)
    print(f"  {label:34s} trades={len(sub):>8,d} contracts={sub['n'].sum():>12,.0f}  maker edge={edge(sub):+6.2f}c/contract "
          f"[{lo:+.2f},{hi:+.2f}]  ROI on capital={roi(sub):+6.2f}%  pnl=${sub['maker_pnl'].sum():>12,.0f}")


if __name__ == "__main__":
    allf = pd.concat([prep(load(f)) for f in sorted(glob.glob(f"{D}/KX*.csv.gz"))], ignore_index=True)
    for ser, df in allf.groupby("series"):
        df = df.reset_index(drop=True)
        print(f"\n=== {ser}: {len(df):,} trades, {df['ticker'].nunique():,} markets, {df['game'].nunique()} games, days {sorted(df['day'].unique())}")
        pre, live = df[df["mtk"] > 0], df[df["mtk"] <= 0]
        print(f"  pre-kick volume share: {pre['n'].sum()/df['n'].sum():.1%} of contracts")
        line("PRE-KICK (all passive fills)", pre)
        line("IN-GAME (all passive fills)", live)
        for lo, hi, lab in ((1440, 1e9, ">24h before"), (360, 1440, "6-24h"), (60, 360, "1-6h"), (15, 60, "15-60m"), (0, 15, "last 15m")):
            line(f"  pre: {lab}", pre[(pre["mtk"] > lo) & (pre["mtk"] <= hi)])
        for lo, hi in ((0, .1), (.1, .3), (.3, .7), (.7, .9), (.9, 1.01)):
            line(f"  pre: price {lo:.1f}-{hi:.1f}", pre[(pre["p"] >= lo) & (pre["p"] < hi)])
        for lo, hi, lab in ((0, 10, "<10"), (10, 100, "10-100"), (100, 1000, "100-1000"), (1000, 1e12, ">=1000")):
            line(f"  pre: size {lab} contracts", pre[(pre["n"] >= lo) & (pre["n"] < hi)])

    # ---- pooled across every series loaded ----
    allf = allf.reset_index(drop=True)
    pre, live = allf[allf["mtk"] > 0], allf[allf["mtk"] <= 0]
    games = allf["game"].nunique()
    print(f"\n=== POOLED ({', '.join(sorted(allf['series'].unique()))}): {len(allf):,} trades, {games} games")
    line("POOLED PRE-KICK", pre)
    line("POOLED IN-GAME", live)
    line("POOLED PRE-KICK, last 60 min", pre[pre["mtk"] <= 60])
    line("POOLED PRE-KICK, >60 min", pre[pre["mtk"] > 60])
    line("POOLED PRE-KICK, size >=1000", pre[pre["n"] >= 1000])
    line("POOLED PRE-KICK, size <100", pre[pre["n"] < 100])
    per_game = pre.groupby("game").agg(contracts=("n", "sum"), pnl=("maker_pnl", "sum"))
    print(f"  pre-kick contracts per game: mean {per_game['contracts'].mean():,.0f}; maker pnl per game (all flow): "
          f"mean ${per_game['pnl'].mean():,.0f}, sd ${per_game['pnl'].std():,.0f}")
    se = (boot(pre, edge)[1] - boot(pre, edge)[0]) / 3.92
    print(f"  std error of the pre-kick edge with {games} games: {se:.2f}c/contract; games needed to resolve a 0.5c edge "
          f"at 1 SE: ~{games * (se / 0.5) ** 2:,.0f}")
