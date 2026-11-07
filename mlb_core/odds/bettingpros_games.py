"""
mlb_core.odds.bettingpros_games -- BettingPros GAME-line history (moneyline / spread-or-puck-line / total), long format.

Same provider, client, pacing and storage conventions as the player-prop crawl (`mlb_core.odds.bettingpros_props`),
applied to the `game-odds` markets. Built for NHL (2021 -> now); the sport table is the only thing a second sport needs.
The NBA copy of this crawl (`Odds/bettingpros_nba_games`) was written by `nba/games/bp_games.py` on branch
feat/nba-game-models and has the same schema, so one reader serves both.

Markets (category game-odds, period game; ids resolved from the LIVE catalog, they differ per sport; NHL 2026-11-05:
193 moneyline / 195 puck-line / 194 total):
    moneyline  side home|away    line NaN (BettingPros stores a meaningless 1 there)
    spread     side home|away    line = that side's handicap (home -1.5 = home must win by 2+); the NHL puck line
    total      side over|under   line = goals total (regulation + OT, shootout excluded)

Storage: {prefix}/{market}/{YYYY-MM-DD}.parquet, default prefix Odds/bettingpros_{sport}_games. One row per
(event, market, side, book, quoted line) plus the selection's opening line as `is_open` rows. A 0-row parquet is the
"crawled, nothing there" marker, so an empty day is never re-asked. Today's partition is never written.

What a partition holds: BettingPros keeps ONE snapshot per book per event, the LAST quote, with the stamp `updated`
(UTC). Crawled after the game, that is a closing-line PROXY, not a time series: a book that re-posted a line after the
tip leaves an in-play quote and the real close is gone (callers drop quotes stamped after the grace window; see
`close_quotes`). The opening line is ONE quote per side (`book_id` = the book that posted it, `updated` = its creation
time), not one per book.

Rate limiting / politeness: the offers endpoint caps a page at 10 offers (a whole NHL slate fits in one page), jittered
sleeps between every request group, newest first, resume-exact, and a circuit breaker: 401 / 403 / a CAPTCHA-looking body,
or `MAX_CONSECUTIVE_ERRORS` failed days in a row, STOPS the crawl (exit 3). It never rotates identity or retries around a
block.

    PYTHONPATH=. GCS_BUCKET=<bucket> python -m mlb_core.odds.bettingpros_games --sport NHL --start 2021-01-01
"""
from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

import pandas as pd

from mlb_core.odds import bettingpros as bp
from mlb_core.odds import bettingpros_props as bpp

log = logging.getLogger("bettingpros_games")

MARKETS = ("moneyline", "spread", "total")
# sport -> {our market label: catalog slug}. Slugs, not ids (ids are per-sport and resolved live).
SPORTS: Dict[str, dict] = {
    "NHL": {"slugs": {"moneyline": "moneyline", "spread": "puck-line", "total": "total"},
            "start": "2021-01-01",                                  # offers exist from 2021-01 (probed 2026-11-05); earlier: events, no offers
            "months": {10, 11, 12, 1, 2, 3, 4, 5, 6},
            "extra_days": [("2021-07-01", "2021-07-08")]},          # the 2020-21 Cup final ran into July
    "NBA": {"slugs": {"moneyline": "moneyline", "spread": "spread", "total": "total"},
            "start": "2021-10-01", "months": {10, 11, 12, 1, 2, 3, 4, 5, 6}, "extra_days": []},
}

COLUMNS = ["date", "market", "market_id", "event_id", "scheduled", "season_type", "event_status", "away", "home",
           "side", "line", "book_id", "book", "book_kind", "odds", "is_main", "is_open", "updated"]
DEDUP_KEY = ["market", "event_id", "side", "line", "book_id", "is_open"]

MAX_CONSECUTIVE_ERRORS = 5
GRACE_MIN = 10.0                  # a quote stamped more than this after the scheduled start is in-play, not a close


class Blocked(RuntimeError):
    """The provider refused us (401 / 403 / CAPTCHA). Stop; do not work around it."""


def default_prefix(sport: str) -> str:
    return f"Odds/bettingpros_{sport.lower()}_games"


# ------------------------------------------------------------------------------------------------------ HTTP
def _is_block(exc: Exception) -> bool:
    s = str(exc).lower()                      # bp.api_get raises "<status> <url> params=...: <body>"
    return s.startswith(("401 ", "403 ")) or any(t in s for t in ("captcha", "cf-chl", "just a moment", "access denied"))


def get(sess, path: str, params: dict) -> dict:
    """bp.api_get with the block detector in front. 429 / 5xx are retried inside api_get (2-8 s back-off)."""
    try:
        return bp.api_get(sess, path, params)
    except RuntimeError as exc:
        if _is_block(exc):
            raise Blocked(str(exc)[:300]) from exc
        raise


def resolve_game_markets(sess, sport: str) -> Dict[str, int]:
    """{label: market_id} from the live catalog (game-odds, full game)."""
    catalog = get(sess, "markets", {"sport": sport}).get("markets") or []
    by_slug = {m["slug"]: m["id"] for m in catalog if m.get("category") == "game-odds" and m.get("period") == "game"}
    return {label: by_slug[slug] for label, slug in SPORTS[sport]["slugs"].items() if slug in by_slug}


def fetch_events(sess, sport: str, ds: str) -> dict:
    data = get(sess, "events", {"sport": sport, "date": ds})
    out = {}
    for e in data.get("events") or []:
        if e.get("id") is None:
            continue
        out[e["id"]] = {"away": e.get("visitor") or "", "home": e.get("home") or "",
                        "scheduled": e.get("scheduled") or "", "season_type": e.get("season_type") or "",
                        "status": e.get("status") or ""}
    return out


def fetch_offers(sess, sport: str, market_id: int, event_ids: list, delay: float = 0.8) -> list:
    """All offers for the events. One offer per event per game market, so a slate is 1-2 pages (limit is capped at 10)."""
    if not event_ids:
        return []
    offers, page = [], 1
    while True:
        data = get(sess, "offers", {"sport": sport, "market_id": market_id,
                                    "event_id": ":".join(str(e) for e in event_ids), "location": "ALL",
                                    "limit": 10, "page": page})
        batch = data.get("offers") or []
        offers.extend(batch)
        total = (data.get("_pagination") or {}).get("total_pages") or 1
        if page >= total or not batch:
            return offers
        page += 1
        time.sleep(delay * random.uniform(0.6, 1.4))


# ------------------------------------------------------------------------------------------------------ parsing
def _side(market: str, sel: dict, home: str) -> Optional[str]:
    if market == "total":
        lab = (sel.get("selection") or sel.get("label") or "").strip().lower()
        return "over" if lab.startswith("o") else "under" if lab.startswith("u") else None
    part = (sel.get("participant") or "").strip()
    if not part or not home:
        return None
    return "home" if part == home else "away"


def build_game_rows(market: str, market_id: int, ds: str, offers: list, ev_map: dict) -> List[dict]:
    """Flatten offers into long rows (see module docstring). Off / priceless lines are dropped. Moneyline `line` is NaN."""
    rows: List[dict] = []
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
                rows.append({**base, "side": side, "line": None if market == "moneyline" else op.get("line"),
                             "book_id": bid if bid is not None else -1, "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                             "book_kind": "open", "odds": op["cost"], "is_main": None, "is_open": True,
                             "updated": op.get("created")})
            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("cost") is None or ln.get("is_off") or (market != "moneyline" and ln.get("line") is None):
                        continue
                    rows.append({**base, "side": side, "line": None if market == "moneyline" else ln.get("line"),
                                 "book_id": bid, "book": bpp.BOOK_NAMES.get(bid, f"book_{bid}"),
                                 "book_kind": bpp.book_kind(bid), "odds": ln["cost"], "is_main": ln.get("main"),
                                 "is_open": False, "updated": ln.get("updated")})
    return rows


def to_frame(rows: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in ("market_id", "event_id", "book_id", "odds"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    for c in ("is_main", "is_open"):
        df[c] = df[c].astype("boolean")
    return df


# ------------------------------------------------------------------------------------------------------ storage
def day_key(prefix: str, market: str, ds: str) -> str:
    return f"{prefix}/{market}/{ds}.parquet"


def done_dates(prefix: str, market: str) -> set:
    from mlb_core import storage
    out = set()
    for key in storage.list_keys(f"{prefix}/{market}/"):
        stem = key.rsplit("/", 1)[-1]
        if stem.endswith(".parquet"):
            out.add(stem[:-len(".parquet")])
    return out


_BUCKET_HANDLE = None


def write_day(prefix: str, market: str, ds: str, rows: List[dict]) -> None:
    """Write one partition. With a bucket configured the GCS client is built ONCE and reused (storage.write_parquet builds
    a fresh client per call, which measured ~8 s per object from a laptop and made a 1,400-day crawl a 11-hour job)."""
    global _BUCKET_HANDLE
    from mlb_core import storage
    key, df = day_key(prefix, market, ds), to_frame(rows)
    name = storage._get_bucket()
    if not name:
        storage.write_parquet(df, key)
        return
    if _BUCKET_HANDLE is None or _BUCKET_HANDLE.name != name:
        _BUCKET_HANDLE = storage._gcs_client().bucket(name)
    import io
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    _BUCKET_HANDLE.blob(key).upload_from_string(buf.getvalue(), timeout=120)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """One row per quote. The API's `date` is a local-day window over UTC stamps, so an event can sit under two adjacent
    partitions; keep the row with the LATEST `updated` (the one nearest the close), partition date as the tie-break."""
    if df.empty:
        return df
    d = df.copy()
    d["_u"] = pd.to_datetime(d["updated"], errors="coerce")
    d = d.sort_values(["_u", "date"], kind="stable", na_position="first")
    d = d.drop_duplicates(subset=DEDUP_KEY, keep="last").drop(columns="_u")
    return d.sort_values(["date", "event_id", "market", "side", "book_id"], kind="stable").reset_index(drop=True)


def read_games(sport: str = "NHL", start: Optional[str] = None, end: Optional[str] = None,
               prefix: Optional[str] = None, local_dir: Optional[str] = None,
               markets: Iterable[str] = MARKETS) -> pd.DataFrame:
    """The daily partitions (inclusive date filter), de-duplicated. local_dir = a `gcloud storage cp -r` copy of
    the prefix (each GCS read from a laptop costs ~10 s; the bucket path is for small ranges)."""
    return dedupe(read_raw(sport, start, end, prefix, local_dir, markets))


def read_raw(sport: str = "NHL", start: Optional[str] = None, end: Optional[str] = None,
             prefix: Optional[str] = None, local_dir: Optional[str] = None,
             markets: Iterable[str] = MARKETS) -> pd.DataFrame:
    """Concatenated partitions BEFORE de-duplication (validation counts what `dedupe` removed)."""
    import glob
    import os
    prefix = prefix or default_prefix(sport)
    frames = []
    for m in markets:
        if local_dir:
            keys = sorted(glob.glob(os.path.join(local_dir, m, "*.parquet")))
        else:
            from mlb_core import storage
            keys = sorted(storage.list_keys(f"{prefix}/{m}/"))
        for k in keys:
            ds = os.path.basename(k)[:10]
            if (start and ds < start) or (end and ds > end):
                continue
            if local_dir:
                f = pd.read_parquet(k)
            else:
                from mlb_core import storage
                f = storage.read_parquet(k)
            if len(f):
                frames.append(f)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)


# ------------------------------------------------------------------------------------------------------ dates
def crawl_dates(sport: str, start: str, end: str, newest_first: bool = True) -> List[str]:
    cfg = SPORTS[sport]
    lo = max(datetime.strptime(start, "%Y-%m-%d").date(), datetime.strptime(cfg["start"], "%Y-%m-%d").date())
    hi = datetime.strptime(end, "%Y-%m-%d").date()
    extra = set()
    for a, b in cfg["extra_days"]:
        d = datetime.strptime(a, "%Y-%m-%d").date()
        while d <= datetime.strptime(b, "%Y-%m-%d").date():
            extra.add(d)
            d += timedelta(days=1)
    out, cur = [], lo
    while cur <= hi:
        if cur.month in cfg["months"] or cur in extra:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out[::-1] if newest_first else out


def is_closed_day(ds: str, today: Optional[_date] = None) -> bool:
    """Only days strictly before today are final; a same-day partition would freeze pre-close prices."""
    return ds < (today or _date.today()).isoformat()


# ------------------------------------------------------------------------------------------------------ crawl
def _sleep(base: float) -> None:
    time.sleep(base * random.uniform(0.6, 1.4))


def crawl(sport: str, start: str, end: str, prefix: Optional[str] = None, delay: float = 0.8, market_sleep: float = 0.8,
          date_sleep: float = 1.5, max_dates: int = 0, newest_first: bool = True, sess=None) -> dict:
    """Resumable crawl. A (market, date) partition that exists -- including a 0-row marker -- is skipped. Returns stats;
    stats['blocked'] / stats['aborted'] tell the caller why it stopped early."""
    prefix = (prefix or default_prefix(sport)).rstrip("/")
    sess = sess or bpp.make_session(sport)
    try:
        markets = resolve_game_markets(sess, sport)
    except Blocked as exc:
        log.error("BLOCKED by the provider on the market catalog (%s). Stopping; not retrying or evading.", exc)
        return {"dates": 0, "rows": 0, "empty_days": 0, "errors": 0, "blocked": True, "aborted": False}
    if set(markets) != set(MARKETS):
        raise SystemExit(f"catalog is missing game markets for {sport}: resolved {markets}")
    log.info("%s game markets resolved live: %s -> %s", sport, markets, prefix)
    done = {m: done_dates(prefix, m) for m in markets}
    dates = [d for d in crawl_dates(sport, start, end, newest_first) if is_closed_day(d)]
    stats = {"dates": 0, "rows": 0, "empty_days": 0, "errors": 0, "blocked": False, "aborted": False}
    consecutive = 0
    for ds in dates:
        need = {m: mid for m, mid in markets.items() if ds not in done[m]}
        if not need:
            continue
        if max_dates and stats["dates"] >= max_dates:
            log.info("max_dates=%d reached", max_dates)
            break
        try:
            ev_map = fetch_events(sess, sport, ds)
            day_rows = 0
            for m, mid in need.items():
                offers = fetch_offers(sess, sport, mid, list(ev_map), delay) if ev_map else []
                rows = build_game_rows(m, mid, ds, offers, ev_map)
                write_day(prefix, m, ds, rows)                      # 0 rows = "nothing there" marker
                day_rows += len(rows)
                if ev_map:
                    _sleep(market_sleep)
            stats["dates"] += 1
            stats["rows"] += day_rows
            stats["empty_days"] += 0 if day_rows else 1
            consecutive = 0
            if stats["dates"] % 10 == 0:
                log.info("progress %s %s", ds, stats)
        except Blocked as exc:
            stats["blocked"] = True
            log.error("BLOCKED by the provider at %s (%s). Stopping; not retrying or evading.", ds, exc)
            break
        except Exception as exc:  # noqa: BLE001 -- one bad day must not end a multi-hour crawl; it is retried next run
            stats["errors"] += 1
            consecutive += 1
            log.warning("%s failed (%d in a row): %s", ds, consecutive, exc)
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                stats["aborted"] = True
                log.error("%d consecutive failures; stopping so we do not hammer a failing endpoint", consecutive)
                break
            time.sleep(min(60.0, 5.0 * consecutive))
            continue
        _sleep(date_sleep)
    log.info("DONE %s", stats)
    return stats


# ------------------------------------------------------------------------------------------------------ closes
def close_quotes(q: pd.DataFrame, grace_min: float = GRACE_MIN, main_only: bool = True) -> pd.DataFrame:
    """Real-sportsbook closing quotes: book_kind == 'book' (no Consensus pseudo-book, DFS, exchanges, opening rows),
    one row per (event, market, side, book, line), and NO quote stamped more than `grace_min` after the scheduled start.
    That last rule is the no-leakage definition of 'close': an in-play re-post is dropped, never used as the close.
    Quotes with an unparseable stamp are dropped too (cannot be shown to be pre-game)."""
    b = q[(q["book_kind"] == "book") & (~q["is_open"].astype(bool))].copy()
    if main_only:
        b = b[b["is_main"].fillna(True).astype(bool)]
    upd = pd.to_datetime(b["updated"], errors="coerce")
    sch = pd.to_datetime(b["scheduled"], errors="coerce")
    gap = (upd - sch).dt.total_seconds() / 60.0
    b["gap_min"] = gap
    return b[gap <= grace_min].reset_index(drop=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sport", default="NHL", choices=sorted(SPORTS))
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None, help="default: yesterday")
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between offer pages")
    ap.add_argument("--market-sleep", type=float, default=0.8)
    ap.add_argument("--date-sleep", type=float, default=1.5)
    ap.add_argument("--max-dates", type=int, default=0)
    ap.add_argument("--oldest-first", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    end = a.end or (_date.today() - timedelta(days=1)).isoformat()
    st = crawl(a.sport, a.start or SPORTS[a.sport]["start"], end, a.prefix, a.delay, a.market_sleep, a.date_sleep,
               a.max_dates, not a.oldest_first)
    if st["blocked"]:
        return 3
    return 1 if (st["errors"] or st["aborted"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
