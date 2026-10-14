"""
mlb_core.odds.bettingpros_props -- BettingPros player-prop history for NFL / NHL / NBA (long format).

The MLB parsers in mlb_core.odds.bettingpros emit a WIDE csv (one column per book from a fixed, trimmed book map).
That cannot serve these sports: Novig / ProphetX / Kalshi / Pinnacle ids are dropped by the trimmed map, DFS books are
mixed in, and the book list differs by sport and season. This module keeps EVERYTHING in a long table -- one row per
(event, player, market, line, side, book) -- and classifies books at read time (`book_kind`), so a later consumer can
decide what is a consensus input without re-crawling.

Historical depth (probed 2026-10-14, docs/analysis/bettingpros_multisport_backfill_2026-10-14.md):
  NFL props  2021 -> now   (2019: consensus+FanDuel only; 2020: 5 books)
  NBA props  2021-12 -> now
  NHL props  2022-11 -> now (earlier: events but no offers)
Novig / ProphetX start appearing in 2025. Pinnacle NEVER quotes in this feed (use ParlayAPI's archive for that).

No file I/O of its own beyond `write_day`/`read_props` (which go through mlb_core.storage, so GCS-or-local).
"""
from __future__ import annotations

import random
import time
from datetime import date as _date
from datetime import datetime, timedelta

import pandas as pd

from mlb_core.odds import bettingpros as bp

# sport -> stat label -> catalog slug (category player-props, period game). Slugs, not ids: ids are per-sport and the
# catalog is resolved live (`resolve_markets`). The `most-*` "who has the most" specials are deliberately absent.
SPORTS = {
    "NFL": {
        "pass_yds": "passing-yards", "pass_tds": "passing-touchdowns", "pass_att": "passing-attempts",
        "pass_cmp": "passing-completions", "pass_int": "interceptions", "rec_yds": "receiving-yards",
        "receptions": "receptions", "rush_yds": "rushing-yards", "rush_att": "rushing-attempts",
        "rush_rec_yds": "rushing-receiving-yards", "anytime_td": "touchdown-scored",
    },
    "NHL": {
        "goals": "goals", "points": "points", "assists": "assists", "sog": "shots", "saves": "saves",
        "blocks": "blocked-shots",
    },
    "NBA": {
        "pts": "points", "reb": "rebounds", "ast": "assists", "threes": "threes", "pra": "points-assists-rebounds",
        "pr": "points-rebounds", "pa": "points-assists", "ra": "rebounds-assists", "stl": "steals", "blk": "blocks",
        "double_double": "double-double",
    },
}

# First date worth crawling (earlier dates return events but no offers, or consensus+FanDuel only).
SPORT_START = {"NFL": "2021-09-01", "NBA": "2021-10-01", "NHL": "2022-10-01"}
# Calendar months a sport has games (offseason days are skipped without a request).
SPORT_MONTHS = {"NFL": {9, 10, 11, 12, 1, 2}, "NBA": {10, 11, 12, 1, 2, 3, 4, 5, 6},
                "NHL": {10, 11, 12, 1, 2, 3, 4, 5, 6}}

# /v3/books (2026-10-14). Ids only matter for classification; names come back on the row from BOOK_NAMES / the live
# catalog, and an unknown id is kept as f"book_{id}" -- never dropped (the MLB map's mistake).
BOOK_NAMES = {
    0: "Consensus", 2: "Pinnacle", 10: "FanDuel", 12: "DraftKings", 13: "Caesars", 14: "Fanatics", 15: "SugarHouse",
    18: "BetRivers", 19: "BetMGM", 24: "bet365", 26: "Tipico", 27: "PartyCasino", 32: "Borgata", 33: "theScore Bet",
    35: "Betsafe", 36: "Underdog", 37: "PrizePicks", 38: "ProphetX", 39: "Fliff", 40: "Bet99", 41: "TonyBet",
    42: "High 5 Casino", 43: "BetFred", 44: "ThriveFantasy", 45: "Betr", 46: "Betano", 48: "Bet99", 49: "Hard Rock",
    50: "BetMGM Casino", 51: "Proline", 52: "OLG", 53: "Dabble", 54: "Playsqor", 55: "Underdog Sportsbook",
    56: "Wildz Sports", 57: "Thrillzz", 58: "Jackpota", 60: "Novig", 61: "Iso", 62: "BetMGM Poker", 63: "Sleeper",
    64: "Kutt", 65: "BetOpenly", 66: "Spree", 67: "Bally Bet", 68: "Kalshi", 69: "FanDuel Picks",
    70: "DraftKings Pick6", 71: "PointsBet", 72: "Crypto.com", 73: "Polymarket", 74: "DraftKings Predictions",
    75: "Polymarket US", 76: "Underdog Predict", 77: "Bet105", 78: "Plus500", 79: "Acebet", 80: "ClubWPT Gold",
}
# DFS / pick'em / social: prices are not two-sided sportsbook quotes -- never a consensus input.
DFS_IDS = {36, 37, 42, 44, 45, 53, 54, 57, 58, 63, 64, 65, 66, 69, 70, 76, 80}
# Exchanges / prediction markets. Kalshi is the venue the +EV sweep TRADES against -- exclude it from any consensus.
EXCHANGE_IDS = {38, 60, 68, 72, 73, 74, 75}
SHARP_IDS = {2}   # in the catalog, but never quotes here; kept so a future feed change classifies correctly


def book_kind(book_id: int) -> str:
    if book_id == 0:
        return "consensus"
    if book_id in SHARP_IDS:
        return "sharp"
    if book_id in EXCHANGE_IDS:
        return "exchange"
    if book_id in DFS_IDS:
        return "dfs"
    return "book"


DEDUP_KEY = ["market_id", "event_id", "player_id", "line", "side", "book_id", "is_open"]

COLUMNS = ["date", "sport", "market", "market_id", "event_id", "scheduled", "season_type", "event_status", "away",
           "home", "player_id", "player", "team", "position", "line", "side", "book_id", "book", "book_kind", "odds",
           "is_main", "is_best", "is_open", "updated"]


# ----------------------------------------------------------------------------------------------------------------
# HTTP (thin: bp.api_get already retries 429/5xx)
# ----------------------------------------------------------------------------------------------------------------

def make_session(sport: str):
    s = bp.make_session()
    s.headers["Referer"] = f"https://www.bettingpros.com/{sport.lower()}/odds/"
    return s


def resolve_markets(sess, sport: str, wanted: list[str] | None = None) -> dict:
    """{stat_label: {"slug", "market_id"}} resolved from the LIVE catalog for the sport's configured stats."""
    catalog = bp.api_get(sess, "markets", {"sport": sport}).get("markets") or []
    props = {m["slug"]: m for m in catalog if m.get("category") == "player-props" and m.get("period") == "game"}
    out = {}
    for stat, slug in SPORTS[sport].items():
        if wanted and stat not in wanted and slug not in wanted:
            continue
        if slug in props:
            out[stat] = {"slug": slug, "market_id": props[slug]["id"]}
    return out


def fetch_events_detail(sess, sport: str, ds: str) -> dict:
    """{event_id: {away, home, scheduled, season_type, status}}."""
    data = bp.api_get(sess, "events", {"sport": sport, "date": ds})
    out = {}
    for e in data.get("events") or []:
        if e.get("id") is None:
            continue
        out[e["id"]] = {"away": e.get("visitor") or "", "home": e.get("home") or "",
                        "scheduled": e.get("scheduled") or "", "season_type": e.get("season_type") or "",
                        "status": e.get("status") or ""}
    return out


def fetch_offers_paged(sess, sport: str, market_id: int, event_ids: list, delay: float = 0.8) -> list:
    """All offers for the given events; the API hard-caps limit at 10, so this paginates. SLOW BY DESIGN: sleeps
    `delay` (+-40% jitter) between pages."""
    if not event_ids:
        return []
    offers, page = [], 1
    while True:
        data = bp.api_get(sess, "offers", {
            "sport": sport, "market_id": market_id, "event_id": ":".join(str(e) for e in event_ids),
            "location": "ALL", "limit": 10, "page": page})
        batch = data.get("offers") or []
        offers.extend(batch)
        total = (data.get("_pagination") or {}).get("total_pages") or 1
        if page >= total or not batch:
            return offers
        page += 1
        time.sleep(delay * random.uniform(0.6, 1.4))


# ----------------------------------------------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------------------------------------------

def build_prop_rows(sport: str, market: str, market_id: int, ds: str, offers: list, ev_map: dict) -> list[dict]:
    """Flatten offers into long rows. One row per (offer, selection side, book, quoted line); the selection's own
    `opening_line` becomes one extra row per side with is_open=True and the opening book's id."""
    rows: list[dict] = []
    for o in offers:
        parts = o.get("participants") or []
        if not parts:
            continue
        p = parts[0]
        player = p.get("player") or {}
        ev = ev_map.get(o.get("event_id"), {})
        base = {
            "date": ds, "sport": sport, "market": market, "market_id": market_id, "event_id": o.get("event_id"),
            "scheduled": ev.get("scheduled", ""), "season_type": ev.get("season_type", ""),
            "event_status": ev.get("status", ""), "away": ev.get("away", ""), "home": ev.get("home", ""),
            "player_id": o.get("player_id") if o.get("player_id") is not None else p.get("id"),
            "player": p.get("name") or "", "team": player.get("team") or "", "position": player.get("position") or "",
        }
        if not base["player"]:
            continue
        for sel in o.get("selections") or []:
            side = (sel.get("selection") or sel.get("label") or "").strip().lower()
            if not side:
                continue
            op = sel.get("opening_line") or {}
            if op.get("cost") is not None and op.get("line") is not None:
                bid = op.get("book_id")
                rows.append({**base, "line": op["line"], "side": side, "book_id": bid if bid is not None else -1,
                             "book": BOOK_NAMES.get(bid, f"book_{bid}"), "book_kind": "open", "odds": op["cost"],
                             "is_main": None, "is_best": None, "is_open": True, "updated": op.get("created")})
            for book in sel.get("books") or []:
                bid = book.get("id")
                for ln in book.get("lines") or []:
                    if ln.get("cost") is None or ln.get("line") is None or ln.get("is_off"):
                        continue
                    rows.append({**base, "line": ln["line"], "side": side, "book_id": bid,
                                 "book": BOOK_NAMES.get(bid, f"book_{bid}"), "book_kind": book_kind(bid),
                                 "odds": ln["cost"], "is_main": ln.get("main"), "is_best": ln.get("best"),
                                 "is_open": False, "updated": ln.get("updated")})
    return rows


def to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in ("market_id", "event_id", "player_id", "book_id", "odds"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    for c in ("is_main", "is_best", "is_open"):
        df[c] = df[c].astype("boolean")
    return df


# ----------------------------------------------------------------------------------------------------------------
# Storage layout: {prefix}/{market_slug}/{YYYY-MM-DD}.parquet   (prefix default Odds/bettingpros_{sport lower})
# A 0-row parquet is the "crawled, nothing there" marker so an empty day is never re-crawled.
# ----------------------------------------------------------------------------------------------------------------

def default_prefix(sport: str) -> str:
    return f"Odds/bettingpros_{sport.lower()}"


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


def write_day(prefix: str, market: str, ds: str, rows: list[dict]) -> None:
    from mlb_core import storage
    storage.write_parquet(to_frame(rows), day_key(prefix, market, ds))


def read_props(sport: str, market: str, start: str | None = None, end: str | None = None,
               prefix: str | None = None) -> pd.DataFrame:
    """Concatenate a market's daily parquet partitions (inclusive date filter)."""
    from mlb_core import storage
    prefix = prefix or default_prefix(sport)
    dates = sorted(done_dates(prefix, market))
    dates = [d for d in dates if (not start or d >= start) and (not end or d <= end)]
    frames = [storage.read_parquet(day_key(prefix, market, d)) for d in dates]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    # The API's `date` is a local-day window over UTC-stamped events, so one event can appear under two adjacent date
    # partitions (observed: NHL 2026-10-13 returned events scheduled 2026-10-14 UTC). Keep one row per quote; the later
    # partition wins because it was fetched nearer the close.
    return df.drop_duplicates(subset=DEDUP_KEY, keep="last").reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------
# Dates
# ----------------------------------------------------------------------------------------------------------------

def crawl_dates(sport: str, start: str, end: str, newest_first: bool = True) -> list[str]:
    """In-season calendar days in [start, end], clamped to the sport's first useful date."""
    lo = max(datetime.strptime(start, "%Y-%m-%d").date(),
             datetime.strptime(SPORT_START[sport], "%Y-%m-%d").date())
    hi = datetime.strptime(end, "%Y-%m-%d").date()
    out, cur = [], lo
    while cur <= hi:
        if cur.month in SPORT_MONTHS[sport]:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out[::-1] if newest_first else out


def is_closed_day(ds: str, today: _date | None = None) -> bool:
    """Only days strictly before today are final; a same-day partition would freeze pre-close prices."""
    return ds < (today or _date.today()).isoformat()
