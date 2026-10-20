"""
nba.games.bp_games -- BettingPros NBA GAME-line history (moneyline / spread / total), long format.

The existing NBA BettingPros backfill (`mlb.runners.backfill_bettingpros_props`) is PLAYER PROPS only. Game lines need
their own crawl, and NOT the wide MLB row builders in `mlb_core.odds.bettingpros` (those collapse every book onto one
line -- the per-book line-collapse bug, docs/solutions/integration-issues/bettingpros-per-book-line-collapse.md). This
keeps one row per (event, market, side, book, quoted line), like the props crawl.

Markets (game-odds category, period game; ids resolved from the live catalog, 2026-10-20: 127/128/129):
    moneyline  side home|away            line empty
    spread     side home|away            line = that side's handicap (home -2.5 means home favoured by 2.5)
    total      side over|under           line = points total

Storage: {prefix}/{market}/{YYYY-MM-DD}.parquet, default prefix Odds/bettingpros_nba_games. A 0-row parquet is the
"crawled, nothing there" marker. Today's partition is never written (prices not closed). The partition for a settled day
holds each book's LAST quote (BettingPros keeps one snapshot per day), i.e. a closing-line proxy, not a time series.

    PYTHONPATH=. python -m nba.games.bp_games --start 2021-10-01 --end 2026-06-30
"""
from __future__ import annotations

import argparse
import logging
import random
import time

import pandas as pd

from mlb_core.odds import bettingpros as bp
from mlb_core.odds import bettingpros_props as bpp

log = logging.getLogger("nba.games.bp_games")

SPORT = "NBA"
DEFAULT_PREFIX = "Odds/bettingpros_nba_games"
# catalog slug -> our market label
SLUGS = {"moneyline": "moneyline", "spread": "spread", "total": "total"}

COLUMNS = ["date", "market", "market_id", "event_id", "scheduled", "season_type", "event_status", "away", "home",
           "side", "line", "book_id", "book", "book_kind", "odds", "is_main", "is_open", "updated"]


def resolve_game_markets(sess) -> dict:
    """{label: market_id} from the live catalog (game-odds category, full game)."""
    catalog = bp.api_get(sess, "markets", {"sport": SPORT}).get("markets") or []
    by_slug = {m["slug"]: m["id"] for m in catalog if m.get("category") == "game-odds" and m.get("period") == "game"}
    return {label: by_slug[slug] for label, slug in SLUGS.items() if slug in by_slug}


def _side(market: str, sel: dict, home: str) -> str | None:
    if market == "total":
        lab = (sel.get("selection") or sel.get("label") or "").strip().lower()
        return "over" if lab.startswith("o") else "under" if lab.startswith("u") else None
    part = (sel.get("participant") or "").strip()
    if not part or not home:
        return None
    return "home" if part == home else "away"


def build_game_rows(market: str, market_id: int, ds: str, offers: list, ev_map: dict) -> list[dict]:
    rows: list[dict] = []
    for o in offers:
        ev = ev_map.get(o.get("event_id"))
        if not ev:
            continue
        base = {"date": ds, "market": market, "market_id": market_id, "event_id": o.get("event_id"),
                "scheduled": ev["scheduled"], "season_type": ev["season_type"], "event_status": ev["status"],
                "away": ev["away"], "home": ev["home"]}
        for sel in o.get("selections") or []:
            side = _side(market, sel, ev["home"])
            if side is None:
                continue
            op = sel.get("opening_line") or {}
            if op.get("cost") is not None and (market == "moneyline" or op.get("line") is not None):
                bid = op.get("book_id")
                rows.append({**base, "side": side, "line": op.get("line"), "book_id": bid if bid is not None else -1,
                             "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"), "book_kind": "open", "odds": op["cost"],
                             "is_main": None, "is_open": True, "updated": op.get("created")})
            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("cost") is None or ln.get("is_off") or (market != "moneyline" and ln.get("line") is None):
                        continue
                    rows.append({**base, "side": side, "line": ln.get("line"), "book_id": bid,
                                 "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"), "book_kind": bpp.book_kind(bid),
                                 "odds": ln["cost"], "is_main": ln.get("main"), "is_open": False,
                                 "updated": ln.get("updated")})
    return rows


def to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in ("market_id", "event_id", "book_id", "odds"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    for c in ("is_main", "is_open"):
        df[c] = df[c].astype("boolean")
    return df


def read_games(start: str | None = None, end: str | None = None, prefix: str = DEFAULT_PREFIX,
               markets: tuple = ("moneyline", "spread", "total")) -> pd.DataFrame:
    """Concatenate the daily partitions. One event can sit under two adjacent `date` partitions (local-day window over
    UTC stamps); the later partition wins because it was fetched nearer the close."""
    from mlb_core import storage
    frames = []
    for m in markets:
        for key in sorted(storage.list_keys(f"{prefix}/{m}/")):
            ds = key.rsplit("/", 1)[-1][:-len(".parquet")]
            if (start and ds < start) or (end and ds > end):
                continue
            f = storage.read_parquet(key)
            if len(f):
                frames.append(f)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=["market", "event_id", "side", "line", "book_id", "is_open"],
                              keep="last").reset_index(drop=True)


def crawl(start: str, end: str, prefix: str = DEFAULT_PREFIX, delay: float = 0.8, date_sleep: float = 1.0,
          max_dates: int = 0, newest_first: bool = True) -> dict:
    from mlb_core import storage
    sess = bpp.make_session(SPORT)
    markets = resolve_game_markets(sess)
    if set(markets) != set(SLUGS):
        raise SystemExit(f"catalog is missing game markets: resolved {markets}")
    log.info("resolved %s", markets)
    done = {m: bpp.done_dates(prefix, m) for m in markets}
    dates = [d for d in bpp.crawl_dates(SPORT, start, end, newest_first) if bpp.is_closed_day(d)]
    stats = {"dates": 0, "rows": 0, "empty_days": 0, "errors": 0}
    for ds in dates:
        need = {m: mid for m, mid in markets.items() if ds not in done[m]}
        if not need:
            continue
        if max_dates and stats["dates"] >= max_dates:
            break
        try:
            ev_map = bpp.fetch_events_detail(sess, SPORT, ds)
            for m, mid in need.items():
                offers = bpp.fetch_offers_paged(sess, SPORT, mid, list(ev_map), delay) if ev_map else []
                rows = build_game_rows(m, mid, ds, offers, ev_map)
                storage.write_parquet(to_frame(rows), f"{prefix}/{m}/{ds}.parquet")
                stats["rows"] += len(rows)
                stats["empty_days"] += 0 if rows else 1
        except Exception as exc:  # one bad day must not stop a multi-hour crawl; it is retried on the next run
            stats["errors"] += 1
            log.warning("%s failed: %s", ds, exc)
            continue
        stats["dates"] += 1
        if stats["dates"] % 10 == 0:
            log.info("progress %s %s", ds, stats)
        time.sleep(date_sleep * random.uniform(0.6, 1.4))
    log.info("done %s", stats)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-10-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--max-dates", type=int, default=0)
    ap.add_argument("--oldest-first", action="store_true")
    a = ap.parse_args()
    crawl(a.start, a.end, a.prefix, a.delay, max_dates=a.max_dates, newest_first=not a.oldest_first)


if __name__ == "__main__":
    main()
