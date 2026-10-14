"""
bettingpros_multisport_probe.py -- READ-ONLY probe of BettingPros per-book player-prop coverage for NFL / NBA / NHL.

Question (docs/designs/multisport_ev_sweep.md s5 P0): can mlb_core.odds.bettingpros -- the free, zero-credit feed
already used for MLB -- serve as a second multi-book prop feed for the other sports? For each sport this finds
the player-prop markets, pulls a few pages of real offers for the nearest slate(s), and reports per market:

  * market_id -> stat mapping (resolved from the LIVE /v3/markets catalog by slug, never hardcoded ids)
  * offers / distinct players
  * books per prop: any line, and TWO-SIDED at the MODAL line (what a consensus actually needs -- books split a
    main line across adjacent half-points, so "present" overstates usable coverage; ev.props pooling exists for this)
  * which books quote (by name), and whether Pinnacle / Novig / ProphetX / Kalshi are among them
  * the same numbers split by days-to-start, because the MLB docstring warns majors populate only on settled dates

Nothing is written to GCS or the DB. ~1 request per (sport, market, <=max_pages) plus catalog/event calls, paced.

  PYTHONPATH=. python3 scripts/bettingpros_multisport_probe.py [--sports NFL,NBA,NHL] [--days 7]
                                                               [--max-events 4] [--max-pages 3] [--out probe.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from mlb_core.odds import bettingpros as bp

# stat -> market slug in the BettingPros catalog (category "player-props", period "game"). Slugs, not ids: ids are
# per-sport and have been re-numbered before; the catalog lookup below resolves them live.
TARGETS = {
    "NFL": {"pass_yds": "passing-yards", "rush_yds": "rushing-yards", "rec_yds": "receiving-yards",
            "receptions": "receptions", "pass_tds": "passing-touchdowns"},
    "NBA": {"pts": "points", "reb": "rebounds", "ast": "assists"},
    "NHL": {"goals": "goals", "points": "points", "assists": "assists", "sog": "shots", "saves": "saves"},
}
# book ids the ticket asked about (names resolved from /v3/books; ids are only the lookup keys here)
WATCH = {"pinnacle": 2, "novig": 60, "prophetx": 38, "kalshi": 68, "polymarket_us": 75}
CONSENSUS_ID = 0


def resolve_markets(catalog: list, sport: str) -> tuple[dict, list]:
    """-> ({stat: {id, slug}} for TARGETS[sport] found, [all player-props market slugs])."""
    props = [m for m in catalog if m.get("category") == "player-props" and m.get("period") == "game"]
    by_slug = {m["slug"]: m for m in props}
    found = {stat: {"market_id": by_slug[slug]["id"], "slug": slug}
             for stat, slug in TARGETS.get(sport, {}).items() if slug in by_slug}
    return found, sorted(by_slug)


def offer_book_lines(offer: dict) -> dict:
    """{book_id: {line: {"over": cost, "under": cost}}} for one player offer (consensus row excluded)."""
    out: dict = defaultdict(lambda: defaultdict(dict))
    for sel in offer.get("selections") or []:
        side = bp._side(sel)
        if side not in ("Over", "Under"):
            continue
        for book in sel.get("books") or []:
            bid = book.get("id")
            if bid == CONSENSUS_ID:
                continue
            for ln in book.get("lines") or []:
                if ln.get("line") is None or ln.get("cost") is None:
                    continue
                out[bid][ln["line"]][side.lower()] = ln["cost"]
    return out


def offer_coverage(offer: dict) -> dict:
    """Per-offer coverage: books quoting anything, the modal line, and books TWO-SIDED at that line."""
    bl = offer_book_lines(offer)
    line_votes = Counter(line for lines in bl.values() for line in lines)
    modal = line_votes.most_common(1)[0][0] if line_votes else None
    two_sided = {b for b, lines in bl.items() if modal in lines and len(lines[modal]) == 2}
    return {"books_any": set(bl), "modal_line": modal, "n_modal_lines": len(line_votes),
            "books_modal_two_sided": two_sided}


def _summ(xs: list) -> dict:
    return {"min": min(xs), "median": statistics.median(xs), "max": max(xs)} if xs else {"min": None, "median": None, "max": None}


def summarize(offers: list, book_names: dict, days_out: int | None = None) -> dict:
    covs = [(o, offer_coverage(o)) for o in offers]
    covs = [(o, c) for o, c in covs if c["books_any"]]
    present = Counter(b for _, c in covs for b in c["books_any"])
    n = len(covs)
    players = {(o.get("participants") or [{}])[0].get("name") for o, _ in covs}
    return {
        "offers": n, "players": len(players - {None}), "days_out": days_out,
        "books_any": _summ([len(c["books_any"]) for _, c in covs]),
        "books_modal_two_sided": _summ([len(c["books_modal_two_sided"]) for _, c in covs]),
        "pct_offers_ge3_two_sided": round(100 * sum(len(c["books_modal_two_sided"]) >= 3 for _, c in covs) / n, 1) if n else None,
        "pct_offers_multi_line": round(100 * sum(c["n_modal_lines"] > 1 for _, c in covs) / n, 1) if n else None,
        "book_presence_pct": {book_names.get(b, str(b)): round(100 * k / n, 1) for b, k in present.most_common()} if n else {},
        "watch": {name: {"offers": present.get(bid, 0), "pct": round(100 * present.get(bid, 0) / n, 1) if n else None,
                         "two_sided_modal": sum(bid in c["books_modal_two_sided"] for _, c in covs)}
                  for name, bid in WATCH.items()},
    }


def fetch_offers_capped(sess, sport: str, market_id: int, event_ids: list, max_pages: int, delay: float) -> list:
    offers: list = []
    for page in range(1, max_pages + 1):
        data = bp.api_get(sess, "offers", {"sport": sport, "market_id": market_id,
                                           "event_id": ":".join(map(str, event_ids)), "location": "ALL",
                                           "limit": 10, "page": page})
        batch = data.get("offers") or []
        offers.extend(batch)
        if page >= ((data.get("_pagination") or {}).get("total_pages") or 1) or not batch:
            break
        time.sleep(delay)
    return offers


def upcoming_events(sess, sport: str, start: date, days: int, delay: float) -> list:
    out = []
    for off in range(days + 1):
        d = (start + timedelta(days=off)).isoformat()
        for e in bp.api_get(sess, "events", {"sport": sport, "date": d}).get("events") or []:
            if e.get("status", "scheduled") == "scheduled":
                out.append({"id": e["id"], "date": d, "scheduled": e.get("scheduled"),
                            "matchup": f"{e.get('visitor')} @ {e.get('home')}", "days_out": off})
        time.sleep(delay)
    return sorted(out, key=lambda e: (e["scheduled"] or "", e["id"]))


def probe_sport(sess, sport: str, book_names: dict, *, start: date, days: int, max_events: int, max_pages: int,
                delay: float) -> dict:
    catalog = bp.api_get(sess, "markets", {"sport": sport}).get("markets") or []
    found, all_props = resolve_markets(catalog, sport)
    events = upcoming_events(sess, sport, start, days, delay)
    res = {"sport": sport, "targets": found, "missing_targets": sorted(set(TARGETS.get(sport, {})) - set(found)),
           "all_player_prop_slugs": all_props, "events_in_window": len(events), "events_probed": [], "markets": {}}
    if not events:
        res["note"] = "no scheduled events in the window (offseason / between slates)"
        return res
    # nearest slate first; one probe per distinct days_out bucket so same-day vs later-week can be compared
    chosen, seen_days = [], Counter()
    for e in events:
        if seen_days[e["days_out"]] < max(1, max_events // 2) and len(chosen) < max_events:
            chosen.append(e)
            seen_days[e["days_out"]] += 1
    res["events_probed"] = chosen
    for stat, info in found.items():
        per_day: dict = {}
        all_offers: list = []
        for dday in sorted({e["days_out"] for e in chosen}):
            ids = [e["id"] for e in chosen if e["days_out"] == dday]
            offs = fetch_offers_capped(sess, sport, info["market_id"], ids, max_pages, delay)
            per_day[dday] = summarize(offs, book_names, dday)
            all_offers.extend(offs)
            time.sleep(delay)
        res["markets"][stat] = {**info, "overall": summarize(all_offers, book_names), "by_days_out": per_day}
    return res


def print_report(results: list) -> None:
    for r in results:
        print(f"\n=== {r['sport']} === events in window: {r['events_in_window']}  probed: "
              f"{[(e['matchup'], e['date']) for e in r['events_probed']]}")
        if r.get("note"):
            print("  ", r["note"])
        print("  target markets:", {s: i["market_id"] for s, i in r["targets"].items()}, "missing:", r["missing_targets"])
        for stat, m in r["markets"].items():
            o = m["overall"]
            print(f"  [{stat}] id={m['market_id']} offers={o['offers']} players={o['players']} "
                  f"books_any med={o['books_any']['median']} (min {o['books_any']['min']}, max {o['books_any']['max']}) | "
                  f"two-sided@modal med={o['books_modal_two_sided']['median']} ge3={o['pct_offers_ge3_two_sided']}% | "
                  f"multi-line={o['pct_offers_multi_line']}%")
            print("      watch:", {k: f"{v['pct']}%/2s={v['two_sided_modal']}" for k, v in o["watch"].items()})
            for dday, s in m["by_days_out"].items():
                print(f"      +{dday}d: offers={s['offers']} two-sided med={s['books_modal_two_sided']['median']} "
                      f"ge3={s['pct_offers_ge3_two_sided']}%")
        if r["markets"]:
            first = next(iter(r["markets"].values()))
            print("  book presence (first market):", first["overall"]["book_presence_pct"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sports", default="NFL,NBA,NHL")
    ap.add_argument("--days", type=int, default=7, help="look-ahead window for the slate")
    ap.add_argument("--max-events", type=int, default=4, help="events probed per sport (split across days-out buckets)")
    ap.add_argument("--max-pages", type=int, default=3, help="offer pages (10 offers each) per market per bucket")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    sess = bp.make_session()
    books = bp.api_get(sess, "books", {})
    books = books.get("books") if isinstance(books, dict) else books
    book_names = {b["id"]: b.get("short_name") or b.get("name") for b in books or []}
    results = [probe_sport(sess, s.strip().upper(), book_names, start=date.today(), days=a.days,
                           max_events=a.max_events, max_pages=a.max_pages, delay=a.delay)
               for s in a.sports.split(",") if s.strip()]
    print_report(results)
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"ts": datetime.now().isoformat(timespec="seconds"), "watch_book_ids": WATCH,
                       "results": results}, f, indent=1, default=str)
        print("\nwrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
