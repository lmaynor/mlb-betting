"""
mlb.analysis.parlayapi_to_history -- ParlayAPI capture -> odds_history (roadmap P0.2/P0.3).

FORWARD-ONLY feed: ParlayAPI serves ~7 days of history, so this is NOT a deep
historical backfill -- it normalizes the recently-banked ParlayAPI snapshots
(OddsAccum/baseball_mlb/raw/{date}/props_*.json) into the SAME odds_history
Parquet schema/partitions as the BettingPros loader, tagged source="parlayapi".
Run it on the recent window only, going forward as the snapshot/accumulator
banks data. DEEP HISTORICAL odds_history comes from BettingPros
(mlb.analysis.bettingpros_to_parquet); SGO is the live inning-market fallback,
not an odds_history source.

Reuses: nba.odds.parlay_extract.flatten_parlay_props (raw -> per book/player/
line rows), mlb_core.odds.utils (devig/decimal/implied), mlb_core.data.id_resolver
(game_pk/player_id), mlb_core.odds.dk_scraper.resolve_team, and
mlb.analysis.odds_history.write_partition/coverage_report.

Run (Cloud Shell / Cloud Run; needs GCS + pyarrow) -- recent window only:
  PYTHONPATH=. python3 -m mlb.analysis.parlayapi_to_history \
      --since 2026-06-25 --ingested-at "2026-06-29T00:00:00Z"   # last few days
"""

from __future__ import annotations

import argparse
import json
import sys

from mlb_core import storage
from mlb_core.data import id_resolver
from mlb_core.odds.dk_scraper import resolve_team
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nba.odds.parlay_extract import flatten_parlay_props
from mlb.analysis import odds_history as oh

SPORT = "baseball_mlb"
RAW_PREFIX = f"OddsAccum/{SPORT}/raw"
DEVIG_METHOD = "shin"

# ParlayAPI short market name (parlay_extract._market_short) -> (canonical, system).
# Same canonical markets as bettingpros_to_parquet so the two sources coexist.
PARLAY_TO_HISTORY = {
    "home_runs":   ("hr_yn", "HR"),
    "strikeouts":  ("k_ou", "K"),
    "outs":        ("outs_ou", "OUTS"),
    "hits":        ("bhits_ou", "BATTER_HITS"),
    "total_bases": ("btb_ou", "BATTER_TB"),
    "earned_runs": ("per_ou", "PITCHER_ER"),
}

# ParlayAPI/OddsAPI book key -> canonical (matches SGO/BettingPros naming where they overlap).
# Verified against a live payload 2026-06-29. odds_history keeps ALL books
# (incl. offshore/sharp like pinnacle) for analytics; unknown keys pass through
# lowercased via _canon_book.
PARLAY_BOOK_CANON = {
    "draftkings": "draftkings", "fanduel": "fanduel", "betmgm": "betmgm",
    "caesars": "caesars", "bet365": "bet365", "betrivers": "betrivers",
    "fanatics": "fanatics", "hardrock": "hardrock",
    "espnbet": "thescore", "thescore": "thescore",
    "pointsbet": "pointsbet", "pointsbetus": "pointsbet",
}


def _canon_book(key: str) -> str:
    return PARLAY_BOOK_CANON.get(key, (key or "").lower())


def _resolve_pid(name, away_abbr, home_abbr, date, game_pk=None):
    return (id_resolver.resolve_player_id(name, away_abbr, date, game_pk)
            or id_resolver.resolve_player_id(name, home_abbr, date, game_pk))


def _list_props_snapshots(date: str) -> list:
    """(hhmm, key) for each props_*.json banked for a date, sorted by hhmm."""
    out = []
    for key in storage.list_keys(f"{RAW_PREFIX}/{date}/"):
        stem = key.rsplit("/", 1)[-1]
        if stem.startswith("props_") and stem.endswith(".json"):
            out.append((stem[len("props_"):-len(".json")], key))
    return sorted(out)


def rows_for_date(date: str, ingested_at: str) -> "list[dict]":
    snaps = _list_props_snapshots(date)
    if not snaps:
        return []
    hhmms = [h for h, _ in snaps]
    first_hhmm, last_hhmm = hhmms[0], hhmms[-1]

    rows = []
    for hhmm, key in snaps:
        try:
            raw_objs = json.loads(storage.read_bytes(key))
        except Exception:  # noqa: BLE001
            continue
        snapshot_ts = f"{date} {hhmm[:2]}:{hhmm[2:4]}:00"
        is_open = hhmm == first_hhmm
        is_closing = hhmm == last_hhmm
        for ev in raw_objs or []:
            flat = flatten_parlay_props(ev, SPORT)  # per (book, player, market, line)
            away_abbr = resolve_team(ev.get("away_team", "")) or ""
            home_abbr = resolve_team(ev.get("home_team", "")) or ""
            game_pk = id_resolver.resolve_game_pk(date, away_abbr, home_abbr)
            pid_cache: dict = {}
            for r in flat:
                mh = PARLAY_TO_HISTORY.get(r["market"])
                if not mh:
                    continue
                market, system = mh
                player = r["player"]
                if player not in pid_cache:
                    pid_cache[player] = _resolve_pid(player, away_abbr, home_abbr, date, game_pk)
                pid = pid_cache[player]
                book = _canon_book(r["book"])
                over_am = r["over_odds"] or None    # treat 0 as missing
                under_am = r["under_odds"] or None
                fair_o = fair_u = None
                if over_am is not None and under_am is not None:
                    fo, fu = devig_two_way(american_to_implied_prob(over_am),
                                           american_to_implied_prob(under_am),
                                           method=DEVIG_METHOD)
                    fair_o, fair_u = round(fo, 6), round(fu, 6)
                for sel, am, fair in (("OVER", over_am, fair_o), ("UNDER", under_am, fair_u)):
                    if not am:
                        continue
                    rows.append({
                        "sport": "mlb", "market": market, "system": system,
                        "game_pk": game_pk, "game_date": date,
                        "event_id": ev.get("id"), "away_team": away_abbr,
                        "home_team": home_abbr, "player_id": pid, "selection": sel,
                        "line": float(r["line"]) if r["line"] is not None else None,
                        "book": book, "american": int(am),
                        "decimal": round(american_to_decimal(am), 4),
                        "implied_prob": round(american_to_implied_prob(am), 6),
                        "fair_prob": fair, "snapshot_ts": snapshot_ts,
                        "is_open": is_open, "is_closing": is_closing,
                        "source": "parlayapi", "ingested_at": ingested_at,
                    })
    return rows


def convert(since=None, until=None, ingested_at="", dry_run=False) -> dict:
    import pandas as pd
    # list_keys is recursive on GCS (full blob paths) but one-level locally (the
    # date dirs); both expose the date at split index 3 (.../raw/<date>[/file]).
    dates = sorted({parts[3] for k in storage.list_keys(f"{RAW_PREFIX}/")
                    if len(parts := k.split("/")) >= 4 and parts[2] == "raw"})
    if since:
        dates = [d for d in dates if d >= since]
    if until:
        dates = [d for d in dates if d <= until]
    markets_touched = set()
    total = 0
    for d in dates:
        rows = rows_for_date(d, ingested_at)
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=oh.SCHEMA_COLUMNS)
        for market in df["market"].unique():
            part = df[df["market"] == market]
            markets_touched.add(market)
            total += len(part) if dry_run else oh.write_partition(part, market, d)
        print(f"  {d}: {len(rows)} rows")
    if not dry_run:
        for m in markets_touched:
            oh.coverage_report(m)
    return {"dates": len(dates), "rows": total, "markets": sorted(markets_touched)}


def main(argv=None) -> int:
    from datetime import datetime, timedelta, timezone
    p = argparse.ArgumentParser(description="Normalize ParlayAPI OddsAccum -> odds_history")
    p.add_argument("--ingested-at", default=None,
                   help="ISO timestamp tag (default: now UTC)")
    p.add_argument("--since", default=None,
                   help="YYYY-MM-DD (default: today - --days-back)")
    p.add_argument("--until", default=None)
    p.add_argument("--days-back", type=int, default=3,
                   help="recent-window lookback when --since omitted (forward feed)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    # Scheduler-friendly defaults: a daily job runs with no args and processes the
    # last --days-back days (catches late-arriving snapshots + re-dedups idempotently).
    ingested_at = args.ingested_at or datetime.now(timezone.utc).isoformat()
    since = args.since or (datetime.now(timezone.utc).date()
                           - timedelta(days=args.days_back)).isoformat()
    res = convert(since, args.until, ingested_at, args.dry_run)
    print(f"DONE. {res['rows']} rows across {res['dates']} dates, markets={res['markets']}"
          f" (since={since}){' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
