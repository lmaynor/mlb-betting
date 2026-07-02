"""
mlb.runners.odds_alert -- scan for +EV outliers, log alerts, resolve prior ones.

The operational core of the soft-line strategy. Run on a schedule after each odds
refresh. Each run:
  1. FRESHNESS: warn if odds_history's intraday feed is stale (mlb.analysis.odds_freshness).
  2. SCAN: find books lagging the consensus (+EV) at the latest snapshot
     (mlb.analysis.outlier_scan) and append them to Alerts/{date}/log.parquet.
  3. RESOLVE: for every alert logged today, recompute EV against the LATEST (closing-er)
     consensus. If the flagged book was truly LAGGING, the consensus stays put and the
     alert stays +EV (ev_at_close > 0). If the book was INFORMED, the consensus moves
     toward it and ev_at_close goes negative. Aggregated, this is the per-market
     lag-vs-informed scorecard -- the empirical proof of whether "books lag" is bankable.
  4. SCORECARD: alerts today + resolved-EV summary by market, written to
     Alerts/{date}/resolved.parquet and logged.

CLV-style resolution needs no realized outcomes (same/next-day snapshots). True ROI
settlement vs realized stats is a follow-up (needs statcast, next-day).

Config via env: OA_MARKETS (default hr_yn,outs_ou,btb_ou,bhits_ou,k_ou), OA_MIN_EV
(0.03), OA_MIN_BOOKS (4).

Local:
  PYTHONPATH=. python3 -m mlb.runners.odds_alert
"""

from __future__ import annotations

import io
import logging
import os
from datetime import date, datetime, timezone

import pandas as pd

from mlb_core import storage
from mlb.analysis import odds_history as oh
from mlb.analysis import outlier_scan as osc
from mlb.analysis import odds_freshness as fresh

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("odds_alert")

_KEYS = ["market", "game_pk", "player_id", "line", "selection", "book", "snapshot_ts"]


def _read_parquet(key: str):
    try:
        return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))
    except Exception:  # noqa: BLE001
        return None


def _write_parquet(df: pd.DataFrame, key: str) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    storage.write_bytes(buf.getvalue(), key)


def _latest_consensus(markets: list, day: str) -> dict:
    """{(market, game_pk, player_id, line, selection): latest-snapshot consensus fair}."""
    out = {}
    for m in markets:
        df = oh.read_history(m, since=day)
        if not len(df):
            continue
        df = oh.dedupe_by_source(df)
        df = df[~df["book"].str.lower().isin(osc._NON_BOOK)]
        df = df.dropna(subset=["fair_prob", "snapshot_ts"])
        if not len(df):
            continue
        # consensus = median fair at the LATEST snapshot per (g,p,line,sel)
        grp = ["game_pk", "player_id", "line", "selection"]
        latest_ts = df.groupby(grp, dropna=False)["snapshot_ts"].transform("max")
        cur = df[df["snapshot_ts"] == latest_ts]
        cons = cur.groupby(grp, dropna=False)["fair_prob"].median()
        for k, v in cons.items():
            out[(m, *k)] = float(v)
    return out


def run(run_date: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    day = run_date or now.date().isoformat()
    markets = os.environ.get("OA_MARKETS", "hr_yn,outs_ou,btb_ou,bhits_ou,k_ou").split(",")
    min_ev = float(os.environ.get("OA_MIN_EV", "0.03"))
    min_books = int(os.environ.get("OA_MIN_BOOKS", "4"))

    # 1) freshness
    fr = fresh.check(markets, today=day)
    if not fr["ok"]:
        log.warning("FRESHNESS WARN: %s", "; ".join(fr.get("reasons", [fr.get("reason", "?")])))
    else:
        log.info("freshness ok: %d snapshots today (%d ParlayAPI)", fr["today_snaps"], fr["parlay_today"])

    # 2) scan the latest snapshot -> new alerts
    new = osc.scan_markets(markets, since=day, min_ev=min_ev, min_books=min_books, latest_only=True)
    logkey = f"Alerts/{day}/log.parquet"
    prior = _read_parquet(logkey)
    if len(new):
        merged = pd.concat([prior, new], ignore_index=True) if prior is not None else new
        merged = merged.drop_duplicates(subset=[c for c in _KEYS if c in merged.columns], keep="last")
        _write_parquet(merged, logkey)
        log.info("scan: %d new +EV outliers this run (%d total today)", len(new), len(merged))
    else:
        merged = prior if prior is not None else pd.DataFrame()
        log.info("scan: 0 new outliers this run (%d total today)", len(merged))

    # 3) resolve every alert logged today against the latest consensus
    scorecard = None
    if merged is not None and len(merged):
        cons = _latest_consensus(markets, day)
        r = merged.copy()
        r["latest_fair"] = [cons.get((row.market, row.game_pk, row.player_id, row.line, row.selection))
                            for row in r.itertuples(index=False)]
        r = r.dropna(subset=["latest_fair"])
        if len(r):
            r["ev_at_close"] = r["latest_fair"] * r["decimal"] - 1.0
            r["held_up"] = r["ev_at_close"] > 0
            _write_parquet(r, f"Alerts/{day}/resolved.parquet")
            scorecard = (r.groupby("market")
                          .agg(alerts=("ev", "size"),
                               entry_ev=("ev", "mean"),
                               ev_at_close=("ev_at_close", "mean"),
                               held_up_pct=("held_up", "mean"))
                          .round(4))
            log.info("RESOLVE scorecard (ev_at_close>0 => book was lagging = real +EV):\n%s",
                     scorecard.to_string())

    return {"status": "ok", "day": day, "alerts_today": int(len(merged)) if merged is not None else 0,
            "scorecard": scorecard.to_dict() if scorecard is not None else None}


def main() -> int:
    res = run()
    print(f"\nodds_alert {res['day']}: {res['alerts_today']} alerts logged today.")
    if res["scorecard"]:
        print("Resolution by market (ev_at_close>0 => lagging book confirmed, real +EV):")
        print(pd.DataFrame(res["scorecard"]).to_string())
    else:
        print("No alerts to resolve yet (need accumulated intraday snapshots).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
