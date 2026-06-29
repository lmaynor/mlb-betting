"""
bettingpros_hr_api.py
=====================
API-based BettingPros MLB home-run prop backfill.

Why this exists
---------------
The rendered player-props page virtualizes its list (only ~5-10 rows are
mounted in the DOM at once), and Selenium synthetic-scroll events do not
reliably trigger the Vue virtual scroller -- so the page-scrape scraper got
stuck on the first batch of players. The page populates itself from a public
JSON API; hitting that API directly returns every player paginated, with no
scrolling and no virtualization to fight.

This mirrors the CSV schema of bettingpros_hr_backfill.py so the two are
interchangeable downstream.

Endpoints (BettingPros public API)
----------------------------------
  GET https://api.bettingpros.com/v3/markets?sport=MLB
      -> list of {id, name} markets; find the HR market id once
  GET https://api.bettingpros.com/v3/events?sport=MLB&date=YYYY-MM-DD
      -> {events: [{id, ...}]} for that slate
  GET https://api.bettingpros.com/v3/offers?sport=MLB&market_id=<id>
        &event_id=<csv>&location=ALL&limit=100&page=<n>
      -> {offers: [...], _pagination: {...}} odds rows (one per player)

All requests send the public web key in the `x-api-key` header. If BettingPros
rotates it, grab the current value from a `debug` run of
bettingpros_hr_backfill.py (it dumps API URLs) or the site's network tab.

Usage
-----
  # one-time: confirm the HR market id for MLB
  python scripts/bettingpros_hr_api.py markets

  # single day
  python scripts/bettingpros_hr_api.py daily 2026-06-28 --market-id <ID>

  # full backfill (resumes; completed dates are skipped)
  python scripts/bettingpros_hr_api.py backfill --start 2024-04-01 \
      --end 2026-06-28 --market-id <ID>

Run from Cloud Shell -- local network egress to api.bettingpros.com is blocked.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError as exc:  # pragma: no cover - runtime guard
    raise SystemExit("Missing dependency: requests. pip install requests") from exc


API_BASE = "https://api.bettingpros.com/v3"
# Public web key embedded in the BettingPros frontend. If requests start
# returning 401/403, refresh this from a browser network tab / debug run.
API_KEY = "CHi8Hy5CEE4khd46XNYL23dCFX96oUdw6qOt1Dnh"

DEFAULT_OUTPUT = Path("data/bettingpros_hr_odds.csv")

# BettingPros HR prop market id (MLB). Verified via /v3/markets 2026-06-28.
HR_MARKET_ID = 299

# Map BettingPros book ids -> the column names used by the page-scrape CSV so
# the two outputs stay schema-compatible. Verified against /v3/books 2026-06-28.
# Books outside FIXED_BOOK_COLUMNS (Pinnacle, ProphetX, Novig, DFS apps, etc.)
# are intentionally dropped to keep the CSV schema stable. ESPNBet is absent --
# it rebranded to theScore Bet (id 33).
BOOK_ID_TO_NAME = {
    0: "Consensus",
    12: "DraftKings",
    10: "FanDuel",
    24: "bet365",
    19: "BetMGM",
    13: "Caesars",
    49: "Hard Rock Bet",
    33: "theScore Bet",
    14: "Fanatics",
    39: "Fliff",
    18: "BetRivers",
    15: "SugarHouse",
    27: "PartyCasino",
    71: "PointsBet",
}

FIXED_BOOK_COLUMNS = [
    "Open", "Best Odds", "Consensus", "bet365", "DraftKings", "BetMGM",
    "FanDuel", "theScore Bet", "BetRivers", "SugarHouse", "PartyCasino",
    "Fliff", "Caesars", "PointsBet", "Hard Rock Bet", "ESPNBet", "Fanatics",
]

MLB_SEASON_RANGES = {
    2024: ("2024-03-20", "2024-11-03"),
    2025: ("2025-03-20", "2025-11-03"),
    2026: ("2026-03-20", "2026-11-03"),
    2027: ("2027-03-20", "2027-11-03"),
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "x-api-key": API_KEY,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.bettingpros.com",
        "Referer": "https://www.bettingpros.com/mlb/odds/player-props/homeruns/",
        "Accept": "application/json",
    })
    return s


def _get(sess: requests.Session, path: str, params: dict) -> dict:
    url = f"{API_BASE}/{path}"
    for attempt in range(4):
        resp = sess.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        raise RuntimeError(f"{resp.status_code} {url} params={params}: {resp.text[:200]}")
    raise RuntimeError(f"giving up after retries: {url} params={params}")


# -----------------------------------------------------------------------------
# Discovery helpers
# -----------------------------------------------------------------------------

def list_markets() -> None:
    sess = _session()
    data = _get(sess, "markets", {"sport": "MLB"})
    markets = data.get("markets") or data.get("data") or []
    print(f"{len(markets)} MLB markets:")
    for m in markets:
        mid = m.get("id")
        name = m.get("name") or m.get("market") or ""
        hint = "  <-- likely HR" if "home run" in name.lower() or name.lower() == "home runs" else ""
        print(f"  {mid:>5}  {name}{hint}")


def fetch_events(sess: requests.Session, target_date: str) -> dict[int, dict]:
    """Return {event_id: {"away","home","matchup"}}.

    Offers carry no team info, so we join on event_id. The event has
    `home` (abbr) and a participants list; the visitor is the participant
    whose abbr != home (the `away`/`visitor` scalar fields are unreliable --
    `away` came back null in probing).
    """
    data = _get(sess, "events", {"sport": "MLB", "date": target_date})
    events = data.get("events") or data.get("data") or []
    out: dict[int, dict] = {}
    for e in events:
        eid = e.get("id")
        if eid is None:
            continue
        home = e.get("home") or ""
        abbrs = []
        for part in e.get("participants") or []:
            ab = (part.get("team") or {}).get("abbreviation") or part.get("id") or ""
            if ab:
                abbrs.append(ab)
        away = next((a for a in abbrs if a != home), e.get("visitor") or "")
        if not home and len(abbrs) == 2:
            home = abbrs[0]
        matchup = f"{away} at {home}" if away and home else (e.get("event_name") or "")
        out[eid] = {"away": away, "home": home, "matchup": matchup}
    return out


# -----------------------------------------------------------------------------
# Offers -> rows
# -----------------------------------------------------------------------------

def _fmt_odds(cost) -> str:
    """American odds as a +/- string, matching the page-scrape CSV style."""
    if cost is None:
        return ""
    try:
        n = int(cost)
    except (TypeError, ValueError):
        return str(cost)
    return f"+{n}" if n > 0 else str(n)


def fetch_offers(sess: requests.Session, market_id: int, event_ids: list[int]) -> list[dict]:
    if not event_ids:
        return []
    offers: list[dict] = []
    page = 1
    while True:
        data = _get(sess, "offers", {
            "sport": "MLB",
            "market_id": market_id,
            "event_id": ":".join(str(e) for e in event_ids),
            "location": "ALL",
            "limit": 10,  # API hard-caps limit at 10; we paginate.
            "page": page,
        })
        batch = data.get("offers") or []
        offers.extend(batch)
        pg = data.get("_pagination") or {}
        total_pages = pg.get("total_pages") or 1
        if page >= total_pages or not batch:
            break
        page += 1
        time.sleep(0.4)
    return offers


def offer_to_row(offer: dict, target_date: str, event_map: dict[int, dict]) -> dict | None:
    """Flatten one HR offer (one player) into the page-scrape CSV schema.

    Offer shape (verified 2026-06-28):
      offer = {
        player_id, event_id,
        participants: [{name, player:{short_name, position, team, slug}}],
        selections: [{
          selection: "over"/"under", label: "Over"/"Under",
          opening_line: {line, cost, book_id},        # -> "Open" column
          books: [{id, lines: [{cost, line, best, ...}]}],  # best:true -> "Best Odds"
        }],
      }
    Skips offers with no player name.
    """
    parts = offer.get("participants") or []
    if not parts:
        return None
    p = parts[0]
    player_name = p.get("name") or ""
    if not player_name:
        return None
    player = p.get("player") or {}
    team = player.get("team") or ""
    position = player.get("position") or ""
    slug = player.get("slug") or ""
    player_page = f"/mlb/props/{slug}/" if slug else (p.get("link") or "")

    ev = event_map.get(offer.get("event_id"), {})

    row = {
        "Date": target_date,
        "Player": player_name,
        "Player_Page": player_page,
        "Matchup": ev.get("matchup", ""),
        "Team": team,
        "Position": position,
        "Line": "0.5",
    }
    for book in FIXED_BOOK_COLUMNS:
        row[f"{book}_Over"] = ""
        row[f"{book}_Under"] = ""

    for sel in offer.get("selections") or []:
        label = (sel.get("selection") or sel.get("label") or "").lower()
        side = "Over" if label.startswith("o") else "Under" if label.startswith("u") else None
        if side is None:
            continue

        # Open: opening line cost for this side.
        opening = sel.get("opening_line") or {}
        if opening.get("cost") is not None:
            row[f"Open_{side}"] = _fmt_odds(opening.get("cost"))
            if opening.get("line") is not None and side == "Over":
                row["Line"] = str(opening["line"])

        for book in sel.get("books") or []:
            lines = book.get("lines") or []
            if not lines:
                continue
            ln = lines[0]
            cost = ln.get("cost")
            if cost is None:
                continue
            if ln.get("line") is not None and side == "Over":
                row["Line"] = str(ln["line"])
            if ln.get("best"):
                row[f"Best Odds_{side}"] = _fmt_odds(cost)
            name = BOOK_ID_TO_NAME.get(book.get("id"))
            if name and name in FIXED_BOOK_COLUMNS:
                row[f"{name}_{side}"] = _fmt_odds(cost)
    return row


# -----------------------------------------------------------------------------
# CSV I/O  (schema-compatible with bettingpros_hr_backfill.py)
# -----------------------------------------------------------------------------

def headers() -> list[str]:
    cols = ["Date", "Player", "Player_Page", "Matchup", "Team", "Position", "Line"]
    for book in FIXED_BOOK_COLUMNS:
        cols.extend([f"{book}_Over", f"{book}_Under"])
    return cols


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=headers()).writeheader()


def completed_dates(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open("r", newline="", encoding="utf-8") as f:
        return {r["Date"] for r in csv.DictReader(f) if r.get("Date")}


def append_rows(path: Path, rows: list[dict]) -> None:
    ensure_csv(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers(), extrasaction="ignore")
        for r in rows:
            writer.writerow(r)


# -----------------------------------------------------------------------------
# Date utils
# -----------------------------------------------------------------------------

def date_range(start: str, end: str):
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    fin = datetime.strptime(end, "%Y-%m-%d").date()
    while cur <= fin:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def in_season(ds: str) -> bool:
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    bounds = MLB_SEASON_RANGES.get(d.year)
    if not bounds:
        return True
    lo = datetime.strptime(bounds[0], "%Y-%m-%d").date()
    hi = datetime.strptime(bounds[1], "%Y-%m-%d").date()
    return lo <= d <= hi


# -----------------------------------------------------------------------------
# Drivers
# -----------------------------------------------------------------------------

def collect_date(sess: requests.Session, target_date: str, market_id: int) -> list[dict]:
    event_map = fetch_events(sess, target_date)
    if not event_map:
        return []
    offers = fetch_offers(sess, market_id, list(event_map.keys()))
    rows = []
    for off in offers:
        row = offer_to_row(off, target_date, event_map)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: (r["Matchup"], r["Team"], r["Player"]))
    return rows


def run_daily(args) -> int:
    sess = _session()
    rows = collect_date(sess, args.date, args.market_id)
    if rows:
        append_rows(args.output, rows)
    print(f"{args.date}: {len(rows)} players")
    if rows[:3]:
        for r in rows[:3]:
            print(f"   {r['Player']:<22} {r['Matchup']}  DK={r.get('DraftKings_Over','')}")
    return len(rows)


def run_backfill(args) -> int:
    sess = _session()
    ensure_csv(args.output)
    done = completed_dates(args.output)
    todo = [d for d in date_range(args.start, args.end) if in_season(d) and d not in done]
    print(f"Backfill {args.start} -> {args.end}: {len(todo)} dates remaining")
    errors = 0
    for ds in todo:
        try:
            n = collect_date(sess, ds, args.market_id)
            if n:
                append_rows(args.output, n)
            print(f"{ds}: {len(n)} players")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"{ds}: ERROR {exc}", file=sys.stderr)
        time.sleep(args.delay * random.uniform(0.6, 1.4))
    print(f"Done. errors={errors}, output={args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="API-based BettingPros MLB HR backfill")
    p.add_argument("mode", choices=["markets", "daily", "backfill"])
    p.add_argument("date", nargs="?", help="YYYY-MM-DD for daily mode")
    p.add_argument("--market-id", type=int, default=HR_MARKET_ID,
                   help="HR market id (default 299; see `markets` mode)")
    p.add_argument("--start", default="2024-04-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--delay", type=float, default=0.8)
    args = p.parse_args(argv)

    if args.mode == "markets":
        list_markets()
        return 0

    if args.market_id is None:
        p.error("--market-id is required for daily/backfill (run `markets` mode first)")

    if args.mode == "daily":
        if not args.date:
            p.error("daily mode requires a date")
        run_daily(args)
        return 0

    return run_backfill(args)


if __name__ == "__main__":
    raise SystemExit(main())
