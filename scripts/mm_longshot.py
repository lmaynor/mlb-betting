"""mm_longshot.py -- favorite-longshot bias on Kalshi NFL prop tapes (public, settled). Read-only analysis.

Question: do cheap-YES contracts (the high-strike ladder rungs retail loves) lose more often than their price says, so
that BUYING NO at 1 - yes_price is +EV after Kalshi's fee? Built on the same tape and conventions as
scripts/mm_analysis.py (docs/analysis/market_making_2026-10-12.md): pre-kick = created_time before occurrence - 3h,
game = the unit of independence, CIs are cluster bootstraps by game.

Per trade (yes_price p, size n, outcome y = 1 if the market settled YES), buying NO at price 1 - p pays
    gross = (1 - y) - (1 - p) = p - y                         per contract
    taker NO (taker_side == 'no'):  net = p - y - fee, fee = mult * 0.07 * P * (1 - P) with P = 1 - p paid
                                    (every prop series here is fee_type 'quadratic', multiplier 1, checked 2026-10-14)
    maker NO (taker_side == 'yes', i.e. a RESTING NO bid got hit): net = p - y, maker fee 0
    calibration (every print, any side): gross = p - y; its contract-weighted mean is realized-vs-implied.
Edge is reported in cents per contract; ROI is on capital at risk (1 - p per contract).

CAVEATS that shape how to read it (also printed): (1) a taker-NO print is a REAL fill at that price and size, but the
tape has no order book, so size beyond a print, queue position and price impact are not modelled; (2) the per-order fee
is rounded up to the cent by Kalshi, this uses the per-contract formula; (3) which markets are in the tape was chosen
by FINAL total volume (>= a per-series cutoff), which is correlated with the outcome (a YES-resolving longshot trades
~3.5x more in-game), so the raw tape OVERSTATES the longshot YES rate (confirmed: YES rate rises 13% -> 36% across final-
volume quintiles). The valid sample is SELECTION-SAFE markets: pre-kick volume alone >= the series cutoff, so inclusion
cannot depend on the outcome. All headline numbers below use that sample; the raw-tape number is printed only as a
labelled contrast; (4) one primary pre-specified test, everything else is descriptive (many cells, so expect
some to look significant by chance).

Usage: python -I scripts/mm_longshot.py TAPE_DIR [--min-prekick-volume N] [--boot 2000]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import io
import os

import numpy as np
import pandas as pd

FEE_BASE = 0.07
BUCKET_EDGES = [0.02, 0.10, 0.20, 0.35, 0.65, 0.98 + 1e-9]
BUCKET_LABELS = ["[0.02,0.10)", "[0.10,0.20)", "[0.20,0.35)", "[0.35,0.65)", "[0.65,0.98]"]
LONGSHOT_MAX = 0.20                      # the pre-specified primary slice: yes_price in [0.02, 0.20)
RUNG_RATIO = 1.35                        # "high rung": strike >= 1.35 x the player's main line


# ----------------------------------------------------------------------------- loading / prep
def load(path: str) -> pd.DataFrame:
    """Read a (possibly still-being-written) gz CSV, dropping a truncated last line."""
    raw = b""
    with gzip.open(path, "rb") as fh:
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


def dedupe_markets(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """One copy of every market's trades. Two files can cover the same market (e.g. a SERIES file and a SERIES_OCT
    refetch); trades carry no id, so de-duplicating on content would drop genuine identical prints -- keep, per ticker,
    the file holding the most rows for it instead."""
    best: dict[str, int] = {}
    best_n: dict[str, int] = {}
    for i, f in enumerate(frames):
        for t, c in f["ticker"].value_counts().items():
            if c > best_n.get(t, 0):
                best[t], best_n[t] = i, int(c)
    parts = [f[f["ticker"].map(best) == i] for i, f in enumerate(frames)]
    return pd.concat(parts, ignore_index=True)


def prep(df: pd.DataFrame, fee_mult: float = 1.0) -> pd.DataFrame:
    df = df.copy()
    for c in ("created_time", "occurrence"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    df = df.dropna(subset=["created_time", "occurrence"])
    df["mtk"] = ((df["occurrence"] - pd.Timedelta(hours=3)) - df["created_time"]).dt.total_seconds() / 60.0
    df["y"] = (df["result"] == "yes").astype(float)
    df["p"], df["n"] = df["yes_price"].astype(float), df["count"].astype(float)
    df["game"] = df["event"].str.split("-").str[1]
    df["player"] = df["ticker"].str.rsplit("-", n=1).str[0]            # event + player code (a ladder of strikes)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["gross"] = df["p"] - df["y"]                                     # buy NO at 1 - p
    paid = 1.0 - df["p"]
    df["taker_fee"] = fee_mult * FEE_BASE * paid * (1.0 - paid)
    df["taker_net"] = df["gross"] - df["taker_fee"]
    df["cap"] = paid                                                    # capital at risk per contract
    return df


def bucketize(p: pd.Series) -> pd.Series:
    return pd.cut(p, bins=BUCKET_EDGES, labels=BUCKET_LABELS, right=False)


def add_rung_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """strike / the player's main line, main = the strike whose PRE-kick volume-weighted yes price is closest to 0.5
    (the tape has no sportsbook line; the ladder's own centre is the proxy)."""
    pre = df[df["mtk"] > 0]
    v = pre.assign(pn=pre["p"] * pre["n"]).groupby(["player", "strike"])[["pn", "n"]].sum()
    v["vwap"] = v["pn"] / v["n"]
    v = v.reset_index()
    v["d"] = (v["vwap"] - 0.5).abs()
    main = v.sort_values("d").groupby("player").first()["strike"].rename("main_strike")
    out = df.join(main, on="player")
    out["rung_ratio"] = out["strike"] / out["main_strike"]
    return out


# ----------------------------------------------------------------------------- statistics
def cell(sub: pd.DataFrame, pnl_col: str, rng: np.random.Generator, B: int = 2000, min_games: int = 4,
         wcol: str = "n") -> dict:
    """Weighted edge (cents/unit) and ROI on capital, with game-cluster bootstrap 95% CIs. wcol="n" is contract-weighted
    (the default, matches mm_analysis); wcol="one" weights every trade equally (robust to a few giant prints)."""
    if len(sub) == 0:
        return {"trades": 0, "contracts": 0.0, "markets": 0, "games": 0}
    wt = sub["n"] if wcol == "n" else pd.Series(1.0, index=sub.index)
    d = sub.assign(w=sub[pnl_col] * wt, n=wt, c=sub["cap"] * wt, py=sub["p"] * wt, yy=sub["y"] * wt)
    g = d.groupby("game")[["w", "n", "c", "py", "yy"]].sum()
    tot = g.sum()
    out = {"trades": len(sub), "contracts": float(tot["n"]), "markets": int(sub["ticker"].nunique()),
           "games": int(len(g)), "avg_p": tot["py"] / tot["n"], "yes_rate": tot["yy"] / tot["n"],
           "edge_c": 100.0 * tot["w"] / tot["n"], "roi_pct": 100.0 * tot["w"] / tot["c"],
           "edge_lo": np.nan, "edge_hi": np.nan, "roi_lo": np.nan, "roi_hi": np.nan}
    if len(g) >= min_games:
        arr = g.to_numpy()
        idx = rng.integers(0, len(g), size=(B, len(g)))
        s = arr[idx].sum(axis=1)                                         # B x 5: w, n, c, py, yy
        out["edge_lo"], out["edge_hi"] = np.percentile(100.0 * s[:, 0] / np.maximum(s[:, 1], 1e-9), [2.5, 97.5])
        out["roi_lo"], out["roi_hi"] = np.percentile(100.0 * s[:, 0] / np.maximum(s[:, 2], 1e-9), [2.5, 97.5])
    return out


def fmt(label: str, r: dict) -> str:
    if not r.get("trades"):
        return f"  {label:44s} (none)"
    ci = "" if np.isnan(r["edge_lo"]) else f" [{r['edge_lo']:+.2f},{r['edge_hi']:+.2f}]"
    sig = "" if np.isnan(r["edge_lo"]) else ("  *" if (r["edge_lo"] > 0 or r["edge_hi"] < 0) else "")
    return (f"  {label:44s} trades={r['trades']:>8,d} contr={r['contracts']:>11,.0f} games={r['games']:>3d}  "
            f"yes={100 * r['yes_rate']:5.1f}% vs px={100 * r['avg_p']:5.1f}%  "
            f"edge={r['edge_c']:+6.2f}c{ci}  ROI={r['roi_pct']:+6.2f}%{sig}")


def per_market(sub: pd.DataFrame) -> pd.DataFrame:
    """One row per market from its qualifying prints: size-weighted price, outcome, fee at that price. Weight 1 each, so
    a single huge print cannot dominate (the market is the unit; games are still the bootstrap cluster)."""
    d = sub.assign(pn=sub["p"] * sub["n"])
    g = d.groupby(["game", "ticker"]).agg(pn=("pn", "sum"), n=("n", "sum"), y=("y", "first")).reset_index()
    g["p"] = g["pn"] / g["n"]
    paid = 1.0 - g["p"]
    g["gross"] = g["p"] - g["y"]
    g["taker_net"] = g["gross"] - FEE_BASE * paid * (1.0 - paid)
    g["cap"] = paid
    g["n"] = 1.0
    return g


def concentration(sub: pd.DataFrame, top: int = 10) -> str:
    """How much of the contract volume and the (gross) P&L do the largest prints carry?"""
    s = sub.sort_values("n", ascending=False)
    t = s.head(top)
    return (f"top {top} prints = {100 * t['n'].sum() / s['n'].sum():.1f}% of contracts and "
            f"{100 * (t['gross'] * t['n']).sum() / max(abs((s['gross'] * s['n']).sum()), 1e-9):.0f}% of |total gross pnl| "
            f"(largest print {s['n'].iloc[0]:,.0f} contracts)")


def selection_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Keep markets whose PRE-kick volume alone already clears their series' fetch cutoff (approximated by the series'
    minimum observed final volume -- the cutoffs 3,000/4,000 show up as 3001/4000/4003/4007). Such a market is in the tape
    whatever happened in-game, so inclusion is independent of the outcome. Pre-kick volume itself is pre-outcome."""
    cut = df.groupby("series")["mkt_volume"].min().to_dict()
    pkv = df[df["mtk"] > 0].groupby("ticker")["n"].sum()
    ser = df.groupby("ticker")["series"].first()
    ok = [t for t, v in pkv.items() if v >= cut[ser[t]]]
    return df[df["ticker"].isin(ok)]


def selection_diagnostic(df: pd.DataFrame, rng: np.random.Generator) -> None:
    """Longshot markets (pre-kick yes price < 0.20), market-weighted: YES rate by FINAL volume vs by PRE-kick volume."""
    pre = df[(df["mtk"] > 0) & (df["p"] >= 0.02) & (df["p"] < LONGSHOT_MAX)]
    m = per_market(pre)
    m["final_vol"] = m["ticker"].map(df.groupby("ticker")["mkt_volume"].first())
    m["prekick_vol"] = m["ticker"].map(pre.groupby("ticker")["n"].sum())
    ig = df[df["mtk"] <= 0].groupby("ticker")["n"].sum()
    m["ingame"] = m["ticker"].map(ig).fillna(0)
    print("=== SELECTION DIAGNOSTIC: longshot markets (pre-kick p<0.20), market-weighted ===")
    for col, lab in (("final_vol", "FINAL volume (what the tape was filtered on)"), ("prekick_vol", "PRE-KICK volume")):
        q = pd.qcut(m[col], 5, duplicates="drop")
        t = m.groupby(q, observed=True).agg(markets=("y", "size"), yes_rate=("y", "mean"), avg_px=("p", "mean"))
        print(f" by {lab} quintile:")
        for iv, r in t.iterrows():
            print(f"   {str(iv):28s} markets={int(r['markets']):>4d}  yes={100 * r['yes_rate']:5.1f}%  px={100 * r['avg_px']:5.1f}%")
    print(f" median in-game contracts: YES-resolving {m[m.y == 1]['ingame'].median():,.0f} vs NO-resolving "
          f"{m[m.y == 0]['ingame'].median():,.0f}; median final volume: YES {m[m.y == 1]['final_vol'].median():,.0f} vs "
          f"NO {m[m.y == 0]['final_vol'].median():,.0f}")


# ----------------------------------------------------------------------------- report
def views(pre: pd.DataFrame) -> list[tuple[str, pd.DataFrame, str]]:
    return [("TAKER buys NO (net of fee)", pre[pre["taker_side"] == "no"], "taker_net"),
            ("TAKER buys NO (gross, no fee)", pre[pre["taker_side"] == "no"], "gross"),
            ("MAKER resting NO gets hit (fee 0)", pre[pre["taker_side"] == "yes"], "gross"),
            ("CALIBRATION: every print, NO at 1-p (gross)", pre, "gross")]


def run(df: pd.DataFrame, B: int = 2000, seed: int = 7, min_prekick_volume: float = 0.0) -> None:
    rng = np.random.default_rng(seed)
    df = df[(df["p"] >= 0.02) & (df["p"] <= 0.98)].copy()
    df["bucket"] = bucketize(df["p"])
    df = add_rung_ratio(df)
    pre, live = df[df["mtk"] > 0], df[df["mtk"] <= 0]
    print(f"tape: {len(df):,} trades (0.02<=p<=0.98), {df['ticker'].nunique():,} markets, {df['game'].nunique()} games, "
          f"{df['series'].nunique()} series; pre-kick {len(pre) / len(df):.1%} of trades")

    print("\n=== PRIMARY (pre-specified): pooled PRE-KICK, yes_price in [0.02,0.20), TAKER buys NO, net of fee ===")
    prim = pre[(pre["p"] < LONGSHOT_MAX) & (pre["taker_side"] == "no")]
    print(fmt("PRIMARY", cell(prim, "taker_net", rng, B)))
    print(fmt("  same, gross of fee", cell(prim, "gross", rng, B)))
    print("  (* = 95% game-cluster CI excludes zero; positive = buying NO profits)")
    print(fmt("  TRADE-weighted (every print = 1)", cell(prim, "taker_net", rng, B, wcol="one")))
    print(fmt("  MARKET-weighted (every market = 1)", cell(per_market(prim), "taker_net", rng, B, wcol="one")))
    print("  " + concentration(prim))
    allls = pre[pre["p"] < LONGSHOT_MAX]
    print(fmt("  MARKET-weighted calibration, ALL longshot prints", cell(per_market(allls), "gross", rng, B, wcol="one")))
    # leave the heaviest game days out: is the sign carried by a few slates?
    day_n = prim.assign(day=prim["game"].str[:7]).groupby("day")["n"].sum().sort_values(ascending=False)
    for k in (1, 2, 3):
        drop = set(day_n.index[:k])
        sub = prim[~prim["game"].str[:7].isin(drop)]
        print(fmt(f"  without the {k} biggest slate day(s) {sorted(drop)}", cell(sub, "taker_net", rng, B)))

    for title, base in (("PRE-KICK", pre), ("IN-GAME (descriptive)", live)):
        print(f"\n=== {title}: by yes_price bucket ===")
        for name, sub, col in views(base):
            print(f" {name}")
            for lab in BUCKET_LABELS:
                print(fmt(lab, cell(sub[sub["bucket"] == lab], col, rng, B)))

    print("\n=== PRE-KICK, longshots (p<0.20), TAKER NO net of fee: by series ===")
    for s, sub in pre[(pre["p"] < LONGSHOT_MAX) & (pre["taker_side"] == "no")].groupby("series"):
        print(fmt(s, cell(sub, "taker_net", rng, B)))

    print("\n=== PRE-KICK, longshots (p<0.20), TAKER NO net: ladder rung vs the player's main line ===")
    ls = pre[(pre["p"] < LONGSHOT_MAX) & (pre["taker_side"] == "no") & pre["rung_ratio"].notna()]
    print(fmt(f"strike >= {RUNG_RATIO} x main line", cell(ls[ls["rung_ratio"] >= RUNG_RATIO], "taker_net", rng, B)))
    print(fmt(f"strike <  {RUNG_RATIO} x main line", cell(ls[ls["rung_ratio"] < RUNG_RATIO], "taker_net", rng, B)))

    print("\n=== SENSITIVITY: stricter PRE-kick volume floors within the selection-safe sample ===")
    pkv = pre.groupby("ticker")["n"].sum()
    for thr in (0, 500, 3000, 10000):
        keep = pkv[pkv >= thr].index
        sub = prim[prim["ticker"].isin(keep)]
        print(fmt(f"markets with pre-kick volume >= {thr:,}", cell(sub, "taker_net", rng, B)))
    if min_prekick_volume:
        print(fmt(f"(requested) >= {min_prekick_volume:,.0f}", cell(prim[prim["ticker"].isin(pkv[pkv >= min_prekick_volume].index)],
                                                                     "taker_net", rng, B)))

    print("\n=== SENSITIVITY: stability across game days (primary slice, by game date) ===")
    prim = prim.assign(day=prim["game"].str[:7])
    for dday, sub in prim.groupby("day"):
        print(fmt(dday, cell(sub, "taker_net", rng, B, min_games=3)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Favorite-longshot bias on Kalshi NFL prop tapes (read-only)")
    ap.add_argument("tape_dir")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--min-prekick-volume", type=float, default=0.0)
    a = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.tape_dir, "KX*.csv.gz")))
    if not files:
        raise SystemExit(f"no KX*.csv.gz under {a.tape_dir}")
    print("files:", [os.path.basename(f) for f in files])
    df = prep(dedupe_markets([load(f) for f in files]))
    rng = np.random.default_rng(7)
    selection_diagnostic(df, rng)
    raw = df[(df["p"] >= 0.02) & (df["p"] < LONGSHOT_MAX) & (df["mtk"] > 0) & (df["taker_side"] == "no")]
    print("\n--- CONTRAST ONLY (selection-biased raw tape: all markets above the final-volume cutoff) ---")
    print(fmt("raw tape, primary slice (taker NO, net)", cell(raw, "taker_net", rng, a.boot)))
    safe = selection_safe(df)
    print(f"\nselection-safe markets: {safe['ticker'].nunique():,} of {df['ticker'].nunique():,} "
          f"({safe['n'].sum() / df['n'].sum():.1%} of contracts)\n")
    print("##### ALL FIGURES BELOW: SELECTION-SAFE SAMPLE #####")
    run(safe, B=a.boot, min_prekick_volume=a.min_prekick_volume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
