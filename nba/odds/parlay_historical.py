"""ParlayAPI historical closing-line backfill (NHL / NBA / NFL props + Pinnacle game lines).

ParlayAPI keeps a rolling archive behind /v1/historical/sports/{sport}/closing-odds. How far back you can read is a
TIER limit (/v1/pricing `historical_hours`: Starter = 168h = 7 days), so every day this is not run, one more day of
props history is gone for good. Unlike the live accumulator this archive carries Pinnacle and Novig prop closes --
Pinnacle never quotes in the BettingPros feed, so this is the only free sharp-close anchor for these sports.

Cost (verified 2026-10-14): 10 credits per CALL regardless of how many markets are listed, and each paged call
(offset=) is billed again. The pool is SHARED with every other ParlayAPI consumer, so the run is metered:
  --max-credits   hard cap on credits spent by THIS run (checked before every call)
  --floor         stop if the shared pool's x-requests-remaining would drop below this (default 2500, the same
                  floor nba.odds.credit_ledger uses)

Rows are PER (book, player, market, line): no teams, no event id -- join to box scores on (game_date, player).
`commence_time` is local (-04:00). DFS books are excluded server-side (their rows carry null odds).

Writes (resume-safe: a finished (sport, date, kind) has a `_done.json` marker and is skipped):
  OddsAccum/{sport}/historical/{game_date}/{kind}_p{NNN}.json   raw rows, one file per page
  OddsAccum/{sport}/historical/{game_date}/{kind}_done.json     {pages, rows, credits, books, fetched_at}

  python3 -m nba.odds.parlay_historical --plan                         # print the calls + worst-case credits
  python3 -m nba.odds.parlay_historical --sports icehockey_nhl --days 7 --max-credits 600
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import requests

from mlb_core import storage
from nba.config import PARLAY_API_BASE

logger = logging.getLogger(__name__)

PAGE_LIMIT = 5000        # the archive's own per-leg row cap (x-archive-row-cap)
CALL_COST = 10           # credits per call, observed in x-requests-last
DEFAULT_FLOOR = 2500
HISTORY_DAYS = 7         # Starter tier: 168h

# Real books + exchanges only. DFS (prizepicks/underdog/sleeper/pick6/betr/...) rows have null odds.
PROP_BOOKS = ("pinnacle,novig,prophetx,kalshi,draftkings,fanduel,betmgm,caesars,bet365,betrivers,fanatics,"
              "hardrock,parx,bovada,fliff")

# Several keys per stat on purpose: ParlayAPI names the same market differently per book family (NFL receiving
# yards is player_rec_yds for US books, player_receiving_yards for Pinnacle, player_reception_yds for Novig), and a
# key outside this list silently drops that book. Unknown keys return nothing and cost nothing extra (one call).
PROP_MARKETS = {
    "icehockey_nhl": ["player_goals", "player_assists", "player_points", "player_shots_on_goal", "player_saves",
                      "player_anytime_goal"],
    "basketball_nba": ["player_points", "player_rebounds", "player_assists", "player_pts_rebs_asts",
                       "player_three_pointers", "player_threes_made"],
    "americanfootball_nfl": ["player_pass_yds", "player_rush_yds", "player_rush_yards", "player_rushing_yards",
                             "player_rec_yds", "player_receiving_yards", "player_reception_yds", "player_receptions",
                             "player_pass_tds", "player_anytime_td", "player_pass_attempts",
                             "player_pass_completions", "player_rush_attempts"],
}
GAME_MARKETS = "h2h,spreads,totals"   # default book for game lines is Pinnacle -- the sharp anchor


def history_key(sport: str, game_date: str, kind: str, suffix: str) -> str:
    return f"OddsAccum/{sport}/historical/{game_date}/{kind}_{suffix}.json"


class Budget:
    """Credits spent by THIS run vs a hard cap, plus the shared pool's remaining balance."""

    def __init__(self, max_credits: int, floor: int):
        self.max_credits, self.floor = max_credits, floor
        self.spent = 0
        self.remaining = None   # shared pool, from x-requests-remaining

    def can_afford(self) -> tuple[bool, str]:
        if self.spent + CALL_COST > self.max_credits:
            return False, f"run cap {self.max_credits} reached (spent {self.spent})"
        if self.remaining is not None and self.remaining - CALL_COST < self.floor:
            return False, f"shared pool {self.remaining} would fall below floor {self.floor}"
        return True, ""

    def record(self, headers) -> None:
        try:
            self.spent += int(headers.get("x-requests-last", CALL_COST))
        except (TypeError, ValueError):
            self.spent += CALL_COST
        try:
            self.remaining = int(headers.get("x-requests-remaining"))
        except (TypeError, ValueError):
            pass


def fetch_page(session, api_key: str, sport: str, game_date: str, markets: str, bookmakers: str | None,
               offset: int, base: str = PARLAY_API_BASE, retries: int = 3):
    """One closing-odds call -> (rows, headers). Raises on non-200 after retries."""
    params = {"markets": markets, "date": game_date, "limit": PAGE_LIMIT, "offset": offset}
    if bookmakers:
        params["bookmakers"] = bookmakers
    url = f"{base.rstrip('/')}/historical/sports/{sport}/closing-odds"
    for attempt in range(retries):
        resp = session.get(url, params=params, headers={"X-API-Key": api_key}, timeout=90)
        if resp.status_code == 200:
            rows = resp.json()
            return (rows if isinstance(rows, list) else []), resp.headers
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(3 * (attempt + 1))
            continue
        raise RuntimeError(f"{resp.status_code} {url} date={game_date}: {resp.text[:200]}")
    raise RuntimeError(f"giving up after {retries} tries: {url} date={game_date}")


def backfill_day(session, api_key: str, sport: str, game_date: str, kind: str, budget: Budget,
                 delay: float = 0.5, force: bool = False) -> dict:
    """Page one (sport, date, kind) to completion or until the budget refuses. Returns a summary dict."""
    done_key = history_key(sport, game_date, kind, "done")
    if not force and storage.exists(done_key):
        return {"status": "skipped_done"}
    markets = ",".join(PROP_MARKETS[sport]) if kind == "props" else GAME_MARKETS
    books = PROP_BOOKS if kind == "props" else None
    offset, page, total_rows, books_seen = 0, 0, 0, Counter()
    while True:
        ok, why = budget.can_afford()
        if not ok:
            return {"status": "budget_stop", "reason": why, "pages": page, "rows": total_rows}
        rows, headers = fetch_page(session, api_key, sport, game_date, markets, books, offset)
        budget.record(headers)
        if not rows:
            break
        storage.write_bytes(json.dumps(rows).encode(), history_key(sport, game_date, kind, f"p{page:03d}"))
        books_seen.update(r.get("bookmaker") for r in rows)
        total_rows += len(rows)
        page += 1
        offset += len(rows)
        if len(rows) < PAGE_LIMIT:
            break
        time.sleep(delay)
    summary = {"pages": page, "rows": total_rows, "credits_run_total": budget.spent, "books": dict(books_seen),
               "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    storage.write_bytes(json.dumps(summary).encode(), done_key)
    return {"status": "ok", **summary}


def plan(sports, days: int, include_game_lines: bool) -> list[tuple[str, str, str]]:
    today = date.today()
    # today is excluded: its props have not closed yet
    dates = [(today - timedelta(days=d)).isoformat() for d in range(days, 0, -1)]
    kinds = ["props"] + (["game_lines"] if include_game_lines else [])
    return [(s, d, k) for d in dates for s in sports for k in kinds]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sports", default=",".join(PROP_MARKETS))
    ap.add_argument("--days", type=int, default=HISTORY_DAYS, help="how many days back from today (tier max 7)")
    ap.add_argument("--max-credits", type=int, default=600)
    ap.add_argument("--floor", type=int, default=DEFAULT_FLOOR)
    ap.add_argument("--no-game-lines", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--plan", action="store_true", help="print the calls and worst-case credits, spend nothing")
    a = ap.parse_args(argv)
    sports = [s.strip() for s in a.sports.split(",") if s.strip()]
    unknown = set(sports) - set(PROP_MARKETS)
    if unknown:
        ap.error(f"unknown sports {sorted(unknown)}; known: {sorted(PROP_MARKETS)}")
    calls = plan(sports, min(a.days, HISTORY_DAYS), not a.no_game_lines)
    if a.plan:
        for c in calls:
            print(*c)
        print(f"{len(calls)} (sport,date,kind) units, >= {len(calls) * CALL_COST} credits (one call each; "
              f"busy days page at {PAGE_LIMIT} rows)")
        return 0
    api_key = os.environ.get("PARLAY_API_KEY")
    if not api_key:
        ap.error("set PARLAY_API_KEY (Secret Manager parlay-api-key)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    session, budget, results = requests.Session(), Budget(a.max_credits, a.floor), Counter()
    for sport, day, kind in calls:
        try:
            out = backfill_day(session, api_key, sport, day, kind, budget, force=a.force)
        except Exception as exc:  # noqa: BLE001 -- a date outside the tier window 4xxs; keep going
            logger.error("%s %s %s: %s", sport, day, kind, exc)
            results["error"] += 1
            continue
        results[out["status"]] += 1
        logger.info("%s %s %s: %s (run spent=%d, pool=%s)", sport, day, kind,
                    {k: v for k, v in out.items() if k != "books"}, budget.spent, budget.remaining)
        if out["status"] == "budget_stop":
            break
    logger.info("DONE %s credits_spent=%d pool_remaining=%s", dict(results), budget.spent, budget.remaining)
    return 1 if results["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
