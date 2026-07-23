"""
mlb.analysis.kalshi_history -- backfill Kalshi CLOSING lines from candlesticks.

Kalshi's candlestick + trade endpoints are PUBLIC (no key; verified 2026-07-23),
and settled markets keep their full price history. For every settled MLB market
in a date range we pull its candlesticks, take the last two-sided quote before
close, and write it as a source="kalshi", is_closing=True row into odds_history
-- a season-long SHARP CLOSING reference to backtest CLV against (complements the
BettingPros historical backfill on the book side).

Candle shape (period_interval minutes):
  {end_period_ts, price:{...trade OHLC, {} if no trades}, volume_fp, open_interest_fp,
   yes_bid:{open/high/low/close_dollars}, yes_ask:{open/high/low/close_dollars}}
Closing mid = (yes_bid.close + yes_ask.close)/2 of the last two-sided candle.

SLOW BY DESIGN (won't trip rate limits): one candlestick call per market with a
--sleep between calls, and RESUMABLE -- a per-(series, game_date) sentinel at
Odds/kalshi/_backfill_done/ is written when a date completes, so a re-run skips
finished dates. Validate on a small range, then run the full history as a
long-lived Cloud Run Job.

Reuses the live driver's market->rows mapping (SERIES_MAP / _selections /
_resolve_pid) so historical and forward rows are schema-identical.

Run (Cloud Shell / Cloud Run; needs GCS):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  # bounded validation first:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_history --since 2026-07-18 --until 2026-07-22 --dry-run
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_history --since 2026-07-18 --until 2026-07-22
  # liquid markets only, slower crawl:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_history --series KXMLBGAME,KXMLBRFI,KXMLBTOTAL --since 2024-04-01 --until 2026-07-22 --sleep 0.6
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time

from mlb_core import storage
from mlb_core.data import id_resolver
from mlb_core.odds import kalshi
from mlb.analysis import odds_history as oh
from mlb.analysis.kalshi_to_history import (
    SERIES_MAP, PLAYER_SERIES, _resolve_pid, _selections)

DONE_PREFIX = "Odds/kalshi/_backfill_done"


def _to_ts(iso: str):
    if not iso:
        return None
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _daterange(since: str, until: str):
    d0 = dt.date.fromisoformat(since)
    d1 = dt.date.fromisoformat(until)
    d = d0
    while d <= d1:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def settled_markets(series: str, min_ts: int, max_ts: int) -> list:
    """All settled markets for a series whose close_ts is in [min_ts, max_ts]."""
    out, cursor = [], None
    for _ in range(300):
        p = {"series_ticker": series, "status": "settled", "limit": 1000,
             "min_close_ts": min_ts, "max_close_ts": max_ts}
        if cursor:
            p["cursor"] = cursor
        d = kalshi.get("/markets", **p)
        if not d:
            break
        out += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def closing_candle(series: str, m: dict, period: int) -> dict | None:
    """Fetch the last ~24h of candles and return the last two-sided one, as a
    price-dict shaped like mlb_core.odds.kalshi.prices() (mids + a synthetic
    two-sided top-of-book from the closing bid/ask). None if no valid quote."""
    end_ts = _to_ts(m.get("close_time"))
    open_ts = _to_ts(m.get("open_time"))
    if not end_ts:
        return None
    start_ts = max(open_ts or 0, end_ts - 24 * 3600)
    tk = m.get("ticker")
    d = kalshi.get(f"/series/{series}/markets/{tk}/candlesticks",
                   start_ts=start_ts, end_ts=end_ts, period_interval=period)
    candles = (d or {}).get("candlesticks", []) if d else []
    for c in reversed(candles):
        yb = kalshi.ff((c.get("yes_bid") or {}).get("close_dollars"))
        ya = kalshi.ff((c.get("yes_ask") or {}).get("close_dollars"))
        if yb > 0 and ya > 0 and ya >= yb:
            yes_mid = (yb + ya) / 2
            return {
                "yes_bid": yb, "yes_ask": ya,
                "no_bid": round(1 - ya, 4), "no_ask": round(1 - yb, 4),
                "yes_mid": yes_mid, "no_mid": 1 - yes_mid,
                "yes_bid_size": 0.0, "yes_ask_size": 0.0,
                "volume": kalshi.ff(c.get("volume_fp")),
                "open_interest": kalshi.ff(c.get("open_interest_fp")),
            }
    return None


def _rows_for_market(m, series, market_canon, system, kind, p, snapshot_ts, ingested_at):
    game_date, away, home = kalshi.parse_event_ticker(m.get("event_ticker", ""))
    if not game_date:
        return []
    game_pk = (id_resolver.resolve_game_pk(game_date, away, home)
               if (away and home) else None)
    line = kalshi.ff(m.get("floor_strike")) if kind in ("over", "spread") else None
    player_id = (_resolve_pid(kalshi.player_from_title(m), away, home, game_date, game_pk)
                 if series in PLAYER_SERIES else None)
    rows = []
    for sel, ask, mid in _selections(kind, m, p):
        if kind in ("ml", "spread"):
            selection = ("TIE" if sel == "TIE" else "HOME" if sel == home
                         else "AWAY" if sel == away else None)
            if selection is None:
                continue
        else:
            selection = sel
        if not ask or ask <= 0 or mid is None:
            continue
        rows.append({
            "sport": "mlb", "market": market_canon, "system": system,
            "game_pk": game_pk, "game_date": game_date,
            "event_id": m.get("event_ticker"), "away_team": away, "home_team": home,
            "player_id": player_id, "selection": selection, "line": line,
            "book": "kalshi", "american": kalshi.prob_to_american(ask),
            "decimal": round(1.0 / ask, 4), "implied_prob": round(ask, 6),
            "fair_prob": round(mid, 6), "snapshot_ts": snapshot_ts,
            "is_open": False, "is_closing": True, "source": "kalshi",
            "ingested_at": ingested_at,
        })
    return rows


def convert(series_list, since, until, period=60, sleep=0.4, ingested_at="",
            force=False, dry_run=False) -> dict:
    import pandas as pd
    pad = 2 * 24 * 3600  # widen the close-ts fetch so ET game-dates near edges are covered
    lo = _to_ts(f"{since}T00:00:00Z") - pad
    hi = _to_ts(f"{until}T23:59:59Z") + pad
    stats = {"markets": 0, "rows": 0, "dates_done": 0, "no_quote": 0}

    for series in series_list:
        mh = SERIES_MAP.get(series)
        if not mh:
            print(f"  skip {series}: not in SERIES_MAP"); continue
        market_canon, system, kind = mh
        markets = settled_markets(series, lo, hi)
        by_date: dict = {}
        for m in markets:
            gd, _, _ = kalshi.parse_event_ticker(m.get("event_ticker", ""))
            if gd and since <= gd <= until:
                by_date.setdefault(gd, []).append(m)
        print(f"{series} -> {market_canon}: {len(markets)} settled in window, "
              f"{len(by_date)} game-dates in [{since},{until}]")

        for gdate in sorted(by_date):
            if not force and storage.exists(f"{DONE_PREFIX}/{series}/{gdate}.json"):
                continue
            rows = []
            for m in by_date[gdate]:
                p = closing_candle(series, m, period)
                time.sleep(sleep)                      # THROTTLE
                if not p:
                    stats["no_quote"] += 1; continue
                snap = (m.get("close_time") or "")[:19].replace("T", " ")
                rows += _rows_for_market(m, series, market_canon, system, kind,
                                         p, snap, ingested_at)
                stats["markets"] += 1
            if rows and not dry_run:
                df = pd.DataFrame(rows, columns=oh.SCHEMA_COLUMNS)
                for (mkt, gd2), part in df.groupby(["market", "game_date"]):
                    oh.write_partition(part, mkt, gd2, append=True)
                storage.write_bytes(json.dumps({"markets": len(by_date[gdate]),
                                                "rows": len(rows)}).encode(),
                                    f"{DONE_PREFIX}/{series}/{gdate}.json")
            stats["rows"] += len(rows)
            stats["dates_done"] += 1
            print(f"  {series} {gdate}: {len(by_date[gdate])} mkts -> {len(rows)} rows"
                  f"{' (dry run)' if dry_run else ''}")
        if not dry_run and market_canon:
            oh.coverage_report(market_canon)
    return stats


def main(argv=None) -> int:
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="Backfill Kalshi closing lines -> odds_history")
    ap.add_argument("--series", default=",".join(SERIES_MAP),
                    help="comma-list of Kalshi series (default: all mapped)")
    ap.add_argument("--since", required=True, help="game_date YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", required=True, help="game_date YYYY-MM-DD (inclusive)")
    ap.add_argument("--period", type=int, default=60, help="candle minutes (60 default)")
    ap.add_argument("--sleep", type=float, default=0.4, help="seconds between candle calls")
    ap.add_argument("--ingested-at", default=None)
    ap.add_argument("--force", action="store_true", help="re-do dates even if sentinel exists")
    ap.add_argument("--dry-run", action="store_true", help="fetch + build, write NOTHING")
    args = ap.parse_args(argv)

    series_list = [s.strip() for s in args.series.split(",") if s.strip()]
    ingested_at = args.ingested_at or datetime.now(timezone.utc).isoformat()
    res = convert(series_list, args.since, args.until, period=args.period,
                  sleep=args.sleep, ingested_at=ingested_at, force=args.force,
                  dry_run=args.dry_run)
    print(f"\nDONE. markets={res['markets']} rows={res['rows']} "
          f"dates={res['dates_done']} no_quote={res['no_quote']}"
          f"{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
