"""Per-prop price-realism extras for the NBA BettingPros consensus (second-best / median price, quote age of the best price).

    python scripts/nba_bp_price_extras.py --bp-dir <dir with {market-slug}/{date}.parquet> --consensus a.parquet ... --out extras.parquet

The consensus parquet keeps only the BEST price per side. A best price taken across ~7-14 books is an optimistic fill, and
because each book's quote carries its own `updated` stamp, a best price can be a STALE quote from hours before the close
that the (later) consensus then makes look like value. This script re-reads the raw per-book quotes (same filters as
mlb_core.odds.bettingpros_loader.filter_reference_quotes, same modal line as the consensus) and adds, per
(event_id, player_id, stat, line): second-best and median American price per side, and the age (minutes before the scheduled
start) of the best-price quote per side. Read-only; reads local copies (`gcloud storage cp -r`).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlb_core.odds import bettingpros_loader as L  # noqa: E402
from mlb_core.odds import bettingpros_props as bpp  # noqa: E402

SLUG = {stat: slug for stat, slug in bpp.SPORTS["NBA"].items()}


def to_american(dec: np.ndarray) -> np.ndarray:
    dec = np.asarray(dec, dtype=float)
    return np.where(dec >= 2.0, (dec - 1.0) * 100.0, -100.0 / (dec - 1.0))


def one_market(bp_dir: str, stat: str, slug: str, cons: pd.DataFrame) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(bp_dir, slug, "*.parquet")))
    long_df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    long_df = long_df.drop_duplicates(subset=bpp.DEDUP_KEY, keep="last")
    d = L.filter_reference_quotes(long_df)
    d = d.assign(event_id=pd.to_numeric(d["event_id"], errors="coerce").astype("Int64"),
                 player_id=pd.to_numeric(d["player_id"], errors="coerce").astype("Int64"))
    d = d.dropna(subset=["event_id", "player_id"])
    d = d.sort_values("ts_updated", kind="mergesort", na_position="first")
    d = d.drop_duplicates(["event_id", "player_id", "line", "book_id", "side_n"], keep="last")
    sched = L._utc(d["scheduled"])
    d = d.assign(age=(sched - d["ts_updated"]).dt.total_seconds() / 60.0)
    key = ["event_id", "player_id", "line", "book_id"]
    o = d[d.side_n == "over"].set_index(key)[["odds", "age"]].add_suffix("_o")
    u = d[d.side_n == "under"].set_index(key)[["odds", "age"]].add_suffix("_u")
    w = o.join(u, how="inner").reset_index()
    c = cons[cons["stat"] == stat][["event_id", "player_id", "line"]].drop_duplicates()
    c = c.assign(event_id=c["event_id"].astype("Int64"), player_id=pd.to_numeric(c["player_id"]).astype("Int64"))
    w = w.merge(c, on=["event_id", "player_id", "line"], how="inner")
    w["dec_o"] = w["odds_o"].map(L.american_to_decimal)
    w["dec_u"] = w["odds_u"].map(L.american_to_decimal)
    g = ["event_id", "player_id", "line"]
    res = {}
    for side, dcol, acol in (("o", "dec_o", "age_o"), ("u", "dec_u", "age_u")):
        ws = w.sort_values(g + [dcol], ascending=[True] * 3 + [False], kind="mergesort")
        rank = ws.groupby(g).cumcount()
        best = ws[rank == 0].set_index(g)
        second = ws[rank == 1].set_index(g)
        med = ws.groupby(g)[dcol].median()
        res[f"best_{side}"] = best[dcol]
        res[f"second_{side}"] = second[dcol].reindex(best.index)
        res[f"med_{side}"] = med.reindex(best.index)
        res[f"age_best_{side}"] = best[acol]
        res[f"age_med_{side}"] = ws.groupby(g)[acol].median().reindex(best.index)
    out = pd.DataFrame(res).reset_index()
    out["stat"] = stat
    for s in ("o", "u"):
        for k in ("second", "med"):
            out[f"{k}_{s}_am"] = to_american(out[f"{k}_{s}"].to_numpy())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bp-dir", required=True)
    ap.add_argument("--consensus", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    cons = pd.concat([pd.read_parquet(p, columns=["event_id", "player_id", "stat", "line"]) for p in a.consensus], ignore_index=True)
    parts = []
    for stat, slug in SLUG.items():
        if stat not in set(cons["stat"]):
            continue
        parts.append(one_market(a.bp_dir, stat, slug, cons))
        print(stat, len(parts[-1]), flush=True)
    pd.concat(parts, ignore_index=True).to_parquet(a.out, index=False)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
