"""
ev.ncaaf_banker -- bank college football (NCAAF) full-game ML / spread / total prices from BettingPros and Kalshi.

The NCAAF sibling of ev.ncaab_banker (same module shape, same parsers imported from it): both feeds are free (BettingPros'
public /v3/offers, Kalshi's public market data: no key, no ParlayAPI credits). Each writes the same ladder file BestBook's
crawl writes (ev.bestbook_events.GAME_LINES_KEY shape), one per source:

    Odds/bettingpros_live/ncaaf/game_lines_latest.json
    Odds/kalshi_live/ncaaf/game_lines_latest.json
    {sport, source, fetched_at, window, games: [{src_key, away_team, home_team, away_name, home_name, commence_time, quotes}],
     counts}      quote = {"market": "ml"|"spread"|"total", "line": float | None, "book": str, "a": int | None, "b": int | None}

Same conventions as ev.bestbook_game_lines: spread `line` is the HOME team's handicap, a = AWAY / OVER, b = HOME / UNDER.

Series / market facts were read LIVE on 2026-11-10: BettingPros /v3 markets for NCAAF are 198 moneyline / 200 spread /
199 total points (the catalog also carries 1H / quarter markets; only the full-game three are banked, matching the Kalshi
full-game series the RFQ fair book prices). Kalshi's full-game series are KXNCAAFGAME / KXNCAAFSPREAD / KXNCAFTOTAL --
note the total series drops an A. Team identity is each source's OWN code (BettingPros 'MOH', Kalshi 'MOH' for Miami (OH)
by luck, not by rule): the files are banked raw with `away_name` / `home_name` kept; the cross-source map lives in
ev.sports.ncaa_teams and is applied by the CONSUMER (ev.kalshi_game_banked), never here. Nothing here is a model input
and nothing is validated.

Kalshi prices are the price to BUY each side (ask), the placeholder sides (<= 3c / >= 97c) are dropped, and a rung with
neither side is dropped. BettingPros keeps every sportsbook / exchange line, and drops the 'consensus' pseudo-book and
DFS / pick'em apps. The parse helpers are shared with ev.ncaab_banker in spirit: bp_parse_event is the same code,
parameterised on this module's market ids (ev.ncaab_banker.bp_parse_event takes a markets override for exactly this).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from mlb_core.alerts.discord_routes import canonical_book
from mlb_core.odds import bettingpros as bp
from mlb_core.odds import bettingpros_props as bpp

log = logging.getLogger("ev.ncaaf_banker")

SPORT = "ncaaf"
BP_SPORT = "NCAAF"
BP_MARKETS = {198: "ml", 199: "total", 200: "spread"}          # /v3/markets?sport=NCAAF, probed 2026-11-10
OUT_KEY = "Odds/{source}_live/ncaaf/game_lines_latest.json"
DEFAULT_DAYS = 4                                                # ET calendar days including today (a Tue crawl reaches Saturday)
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = {"KXNCAAFGAME": "ml", "KXNCAAFSPREAD": "spread", "KXNCAFTOTAL": "total"}
KALSHI_MIN_PROB, KALSHI_MAX_PROB = 0.03, 0.97
_ET = ZoneInfo("America/New_York")


def prob_to_american(p: float | None) -> int | None:
    if p is None or not 0.0 < p < 1.0:
        return None
    return int(round(-100.0 * p / (1.0 - p))) if p >= 0.5 else int(round(100.0 * (1.0 - p) / p))


def _int(v) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if abs(n) >= 100 else None


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------------------- BettingPros


def bp_parse_event(event: dict, offers: list, markets: dict | None = None) -> list[dict]:
    """One BettingPros event's ML / spread / total offers -> per-book two-way quotes.

    Offer selections are one per side (ML / spread: participant = the team code; total: selection 'over' / 'under'); each carries
    books[{id, lines[{cost, line, active, is_off}]}]. A spread selection's `line` is that TEAM's own handicap, so the home line is
    read from the home team's selection (or the negated away line when only that side is posted)."""
    away, home = event.get("away"), event.get("home")
    out: dict[tuple, dict] = {}
    markets = BP_MARKETS if markets is None else markets
    for o in offers or []:
        market = markets.get(o.get("market_id"))
        if market is None or o.get("player_id") is not None:
            continue
        for sel in o.get("selections") or []:
            if market == "total":
                side = {"over": "a", "under": "b"}.get(str(sel.get("selection") or "").lower())
            else:
                part = sel.get("participant")
                side = "a" if part == away else "b" if part == home else None
            if side is None:
                continue
            for b in sel.get("books") or []:
                bid = b.get("id")
                if bid is None or bpp.book_kind(bid) in ("consensus", "dfs", "sharp"):
                    continue
                book = canonical_book(bpp.BOOK_NAMES.get(bid, f"book_{bid}"))
                for ln in b.get("lines") or []:
                    if ln.get("is_off") or ln.get("active") is False:
                        continue
                    price, line = _int(ln.get("cost")), _f(ln.get("line"))
                    if price is None:
                        continue
                    if market == "ml":
                        line = None
                    elif line is None:
                        continue
                    elif market == "spread" and side == "a":
                        line = -line                                  # away handicap -> the board's HOME handicap
                    key = (market, line, book)
                    q = out.setdefault(key, {"market": market, "line": line, "book": book, "a": None, "b": None})
                    if q[side] is None:
                        q[side] = price
    return list(out.values())


def bp_names(offers: list) -> dict:
    """{team code: school name} from the offers' participants ('TENN' -> 'Tennessee'); a code without a city is left out."""
    out: dict = {}
    for o in offers or []:
        for p in o.get("participants") or []:
            city = ((p.get("team") or {}).get("city") or "").strip()
            if p.get("id") and city:
                out[str(p["id"])] = city
    return out


def bp_fetch(now: datetime, days: int = DEFAULT_DAYS, delay: float = 0.8) -> dict:
    """Crawl today .. today + days - 1 (ET) of BettingPros NCAAF game lines. Network only through bettingpros(_props)."""
    sess = bpp.make_session(BP_SPORT)
    d0 = now.astimezone(_ET).date()
    games: list[dict] = []
    counts = {"events_seen": 0, "events_started": 0, "events_priced": 0, "quotes": 0, "requests_failed": 0}
    for i in range(days):
        ds = (d0 + timedelta(days=i)).isoformat()
        events = bpp.fetch_events_detail(sess, BP_SPORT, ds)
        counts["events_seen"] += len(events)
        live = {}
        for eid, e in events.items():
            try:
                start = datetime.fromisoformat(str(e["scheduled"]).replace("Z", "+00:00").replace(" ", "T"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
            except ValueError:
                start = None
            if start is not None and start <= now:
                counts["events_started"] += 1
                continue
            live[eid] = (e, start)
        offers: list = []
        for mid in BP_MARKETS:
            try:
                offers.extend(bpp.fetch_offers_paged(sess, BP_SPORT, mid, list(live), delay=delay))
            except Exception as exc:  # noqa: BLE001 - one market failing must not sink the day
                counts["requests_failed"] += 1
                log.warning("bettingpros ncaaf market %s failed: %s", mid, exc)
            time.sleep(1.0)
        by_event: dict = {}
        for o in offers:
            by_event.setdefault(o.get("event_id"), []).append(o)
        nm = {eid: bp_names(offs) for eid, offs in by_event.items()}
        for eid, (e, start) in live.items():
            quotes = bp_parse_event(e, by_event.get(eid, []), BP_MARKETS)
            if not quotes:
                continue
            games.append({"src_key": f"bp{eid}", "away_team": e["away"], "home_team": e["home"],
                          "away_name": nm.get(eid, {}).get(e["away"]), "home_name": nm.get(eid, {}).get(e["home"]),
                          "commence_time": start.astimezone(timezone.utc).isoformat() if start else None, "quotes": quotes})
            counts["events_priced"] += 1
            counts["quotes"] += len(quotes)
    return {"source": "bettingpros", "games": games, "counts": counts, "window": {"days": days}}


# ---------------------------------------------------------------------------------------------------------------- Kalshi


def _kalshi_side_price(m: dict, side: str) -> int | None:
    """American price to BUY `side` ('yes' / 'no'): the ask. Placeholder sides (<= 3c / >= 97c) are dropped."""
    p = _f(m.get(f"{side}_ask_dollars"))
    if p is None or not KALSHI_MIN_PROB <= p <= KALSHI_MAX_PROB:
        return None
    return prob_to_american(p)


_EVENT_RE = re.compile(r"^(?P<series>KX[A-Z]+)-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<teams>[A-Z0-9]+)$")


def kalshi_parse_event(series: str, event_ticker: str, markets: list) -> tuple[dict | None, list[dict]]:
    """One Kalshi event's markets -> ({away_team, home_team, commence_time}, quotes).

    Event tickers glue the two codes with no separator ('...26NOV07HAMPGW'); the game series' two market suffixes ('-HAMP', '-GW')
    give the split, in away-then-home order. Spread tickers end '-{TEAM}{n}' ('wins by over {n - .5}'); totals' yes = Over."""
    kind = KALSHI_SERIES.get(series)
    mt = _EVENT_RE.match(event_ticker)
    if kind is None or mt is None:
        return None, []
    teams_glued = mt["teams"]
    codes = [str(m.get("ticker", "")).rsplit("-", 1)[-1] for m in markets] if kind == "ml" else []
    away = home = None
    if kind == "ml" and len(set(codes)) == 2:
        c1, c2 = (c for c in dict.fromkeys(codes))
        if c1 + c2 == teams_glued:
            away, home = c1, c2
        elif c2 + c1 == teams_glued:
            away, home = c2, c1
    out: dict[tuple, dict] = {}
    for m in markets:
        suffix = str(m.get("ticker", "")).rsplit("-", 1)[-1]
        yes, no = _kalshi_side_price(m, "yes"), _kalshi_side_price(m, "no")
        if yes is None and no is None:
            continue
        if kind == "ml":
            out[("ml", None, suffix)] = {"market": "ml", "line": None, "book": "kalshi", "_team": suffix, "yes": yes, "no": no}
        elif kind == "total":
            x = _f(m.get("floor_strike"))
            if x is not None:
                out[("total", x, "")] = {"market": "total", "line": x, "book": "kalshi", "a": yes, "b": no}
        else:
            x = _f(m.get("floor_strike"))
            team = re.match(r"[A-Z]+", suffix)
            if x is not None and team:
                out[("spread", x, team.group(0))] = {"market": "spread", "line": x, "book": "kalshi", "_team": team.group(0),
                                                     "yes": yes, "no": no}
    return {"glued": teams_glued, "away_team": away, "home_team": home}, list(out.values())


def _kalshi_get(s: requests.Session, params: dict, attempts: int = 5) -> dict:
    """GET /markets with exponential back-off on 429 / 5xx (Kalshi rate-limits the unauthenticated market-data endpoint)."""
    for i in range(attempts):
        r = s.get(f"{KALSHI_BASE}/markets", params=params, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(30.0, 2.0 * 2 ** i))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    raise RuntimeError("kalshi markets: retries exhausted")


def mt_date(event_ticker: str) -> str | None:
    """'KXNCAAFGAME-26NOV14OHIOMOH' -> '2026-11-14' (Kalshi lists a game date, not a start time)."""
    m = _EVENT_RE.match(event_ticker or "")
    try:
        return datetime.strptime(m["date"], "%y%b%d").date().isoformat() if m else None
    except ValueError:
        return None


def kalshi_fetch(now: datetime, days: int = DEFAULT_DAYS, session: requests.Session | None = None) -> dict:
    """All open NCAAMB game / spread / total markets, grouped by event, for games starting within the window."""
    s = session or requests.Session()
    events: dict[str, dict] = {}
    for series in KALSHI_SERIES:
        cursor = ""
        while True:
            d = _kalshi_get(s, {"series_ticker": series, "status": "open", "limit": 1000, "cursor": cursor})
            for m in d.get("markets") or []:
                events.setdefault(m.get("event_ticker"), {"series": series, "markets": []})["markets"].append(m)
            cursor = d.get("cursor") or ""
            time.sleep(1.0)                                  # polite pacing: Kalshi 429s a tight loop over three series
            if not cursor:
                break
    # Kalshi's game series carries the matchup; spread / total events share its event suffix, so teams come from the ML event.
    ml_split: dict[str, tuple] = {}
    parsed: dict[str, tuple] = {}
    for et, ev in events.items():
        meta, quotes = kalshi_parse_event(ev["series"], et, ev["markets"])
        parsed[et] = (ev, meta, quotes)
        if ev["series"] == "KXNCAAFGAME" and meta and meta["away_team"]:
            ml_split[et.split("-", 1)[1]] = (meta["away_team"], meta["home_team"])
    games: dict[str, dict] = {}
    for et, (ev, meta, quotes) in parsed.items():
        if not meta or not quotes:
            continue
        suffix = et.split("-", 1)[1]
        split = ml_split.get(suffix)
        if split is None:
            continue
        away, home = split
        g = games.setdefault(suffix, {"src_key": f"kx{suffix}", "away_team": away, "home_team": home, "away_name": None,
                                      "home_name": None, "commence_time": None, "date": mt_date(et), "quotes": []})
        for q in quotes:
            team = q.pop("_team", None)
            if q["market"] == "ml":
                # one row per team: fold the two 'X wins' markets into one two-way quote (a = away wins, b = home wins)
                side = "a" if team == away else "b" if team == home else None
                if side is None:
                    continue
                prev = next((x for x in g["quotes"] if x["market"] == "ml"), None)
                if prev is None:
                    prev = {"market": "ml", "line": None, "book": "kalshi", "a": None, "b": None}
                    g["quotes"].append(prev)
                prev[side] = q.pop("yes")
            elif q["market"] == "spread":
                x = q["line"]
                if team == home:
                    q.update(line=-x, a=q.pop("no"), b=q.pop("yes"))
                elif team == away:
                    q.update(line=x, a=q.pop("yes"), b=q.pop("no"))
                else:
                    continue
                g["quotes"].append(q)
            else:
                g["quotes"].append(q)
    last = (now.astimezone(_ET).date() + timedelta(days=days - 1)).isoformat()      # Kalshi lists a week ahead: keep the window
    out = [g for g in games.values() if g["quotes"] and (g["date"] or "") <= last]
    return {"source": "kalshi", "games": out, "window": {"days": days},
            "counts": {"events_seen": len(events), "events_priced": len(out), "quotes": sum(len(g["quotes"]) for g in out)}}


# ---------------------------------------------------------------------------------------------------------------- storage / CLI

FETCHERS = {"bettingpros": bp_fetch, "kalshi": kalshi_fetch}


def write_latest(result: dict, key: str | None = None) -> str:
    from mlb_core import storage

    key = key or OUT_KEY.format(source=result["source"])
    storage.write_bytes(json.dumps({
        "sport": SPORT, "source": result["source"], "fetched_at": datetime.now(timezone.utc).isoformat(),
        "window": result.get("window"), "games": result["games"], "counts": result["counts"],
    }, default=str).encode(), key)
    return key


def run(source: str, days: int = DEFAULT_DAYS, out: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    t0 = time.monotonic()
    result = FETCHERS[source](now, days)
    result["seconds"] = round(time.monotonic() - t0, 1)
    n = len(result["games"])
    log.info("NCAAF_BANK %s", json.dumps({"source": source, "games": n, **result["counts"], "seconds": result["seconds"]}))
    if n == 0:                                        # an empty crawl keeps the previous good file
        log.warning("not publishing %s: no priced games", source)
        result["publish"] = False
        return result
    if out:
        with open(out, "w") as fh:
            json.dump(result, fh, default=str)
    else:
        log.info("wrote %s", write_latest(result))
    result["publish"] = True
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bank NCAAF game-line prices from BettingPros or Kalshi.")
    ap.add_argument("--source", choices=sorted(FETCHERS), required=True)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--out", default=None, help="write the result to this local file instead of the bucket")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run(a.source, a.days, a.out)
    except Exception:  # noqa: BLE001
        log.exception("ncaaf bank failed for %s", a.source)
        return 1
    return 0 if result["publish"] else 1


if __name__ == "__main__":
    sys.exit(main())
