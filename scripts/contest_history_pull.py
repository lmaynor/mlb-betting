"""contest_history_pull -- cache final scores plus closing / opening lines for past college games (research tool for the contest's
same-game-parlay, alt-line and teaser experiments; see docs/analysis/contest_sportsbook_experiments_2026-11-17.md).

Source: ESPN's public scoreboard (which games, final scores) and ESPN's core odds API (spread, total, moneyline, juice, and the
opening line) for each completed game. The scoreboard drops odds once a game is final; the core API keeps them. Both are
UNOFFICIAL endpoints: no ToS check is recorded for them in this repo yet (CLAUDE.md asks for one before a scraped source is built
on), so this is research-only: stdlib, one polite request at a time, one cache file, resumable, nothing is written to GCS.

  python3 scripts/contest_history_pull.py --league cfb --start 2025-08-23 --end 2026-01-20 --out ~/contest_history/cfb_2025.jsonl
  python3 scripts/contest_history_pull.py --league cbb --start 2026-01-05 --end 2026-01-18 --max-games 600 --out ~/contest_history/cbb_sample.jsonl

One JSON object per line: {id, date, league, home, away, neutral, home_score, away_score, spread_home, total, over_odds, under_odds,
home_spread_odds, away_spread_odds, ml_home, ml_away, open_spread_home, open_ml_home, provider}. `spread_home` is the HOME handicap
(-6.5 = home favoured by 6.5), the same convention as the contest board.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

UA = "beezy-research-probe"
SB = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
CORE = "https://sports.core.api.espn.com/v2/sports/{path}/events/{eid}/competitions/{eid}/odds"
LEAGUES = {
    "cfb": {"sb": "football/college-football", "core": "football/leagues/college-football", "groups": 80},
    "cbb": {"sb": "basketball/mens-college-basketball", "core": "basketball/leagues/mens-college-basketball", "groups": 50},
}


def get(url: str, params: dict | None = None, attempts: int = 4):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for i in range(attempts):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
        except urllib.error.URLError:
            if i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
    return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _american(x):
    """'+195' / '-110' / 195 -> int, else None."""
    try:
        return int(float(str(x).replace("+", "")))
    except (TypeError, ValueError):
        return None


def parse_odds_item(it: dict) -> dict | None:
    """One ESPN core odds item -> the lines we keep, or None when it is not a usable pre-game line (live-odds providers, no spread)."""
    name = (it.get("provider") or {}).get("name", "")
    if "live" in name.lower():
        return None
    spread, total = _num(it.get("spread")), _num(it.get("overUnder"))
    if spread is None or total is None:
        return None
    home, away = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}
    open_home = (home.get("open") or {})
    return {
        "provider": name, "spread_home": spread, "total": total,
        "over_odds": _american(it.get("overOdds")), "under_odds": _american(it.get("underOdds")),
        "home_spread_odds": _american(home.get("spreadOdds")), "away_spread_odds": _american(away.get("spreadOdds")),
        "ml_home": _american(home.get("moneyLine")), "ml_away": _american(away.get("moneyLine")),
        "open_spread_home": _num(re.sub(r"[^0-9.+-]", "", str(((open_home.get("pointSpread") or {}).get("american")) or "")) or None),
        "open_ml_home": _american(((open_home.get("moneyLine") or {}).get("american"))),
    }


def parse_event(ev: dict, league: str) -> dict | None:
    """A completed scoreboard event -> {id, date, teams, final score}, or None."""
    if not (ev.get("status") or {}).get("type", {}).get("completed"):
        return None
    comp = (ev.get("competitions") or [{}])[0]
    side = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if "home" not in side or "away" not in side:
        return None
    hs, as_ = _num(side["home"].get("score")), _num(side["away"].get("score"))
    if hs is None or as_ is None:
        return None
    return {"id": str(ev["id"]), "date": str(ev.get("date", ""))[:10], "league": league, "neutral": bool(comp.get("neutralSite")),
            "home": side["home"]["team"].get("displayName"), "away": side["away"]["team"].get("displayName"),
            "home_score": int(hs), "away_score": int(as_)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--league", choices=sorted(LEAGUES), required=True)
    ap.add_argument("--start", required=True, help="first date, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="last date, YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-games", type=int, default=0, help="stop after this many games with lines (0 = no limit)")
    ap.add_argument("--sleep", type=float, default=0.25, help="seconds between requests (default 0.25)")
    a = ap.parse_args(argv)
    cfg = LEAGUES[a.league]
    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    have = set()
    if os.path.exists(out):
        have = {json.loads(l)["id"] for l in open(out) if l.strip()}
    d, end, n_new, n_nolines = date.fromisoformat(a.start), date.fromisoformat(a.end), 0, 0
    print(f"{a.league}: {a.start}..{a.end}, {len(have)} games already cached in {out}", flush=True)
    with open(out, "a") as sink:
        while d <= end:
            sb = get(SB.format(path=cfg["sb"]), {"dates": d.strftime("%Y%m%d"), "groups": cfg["groups"], "limit": 400})
            time.sleep(a.sleep)
            for ev in (sb or {}).get("events", []):
                g = parse_event(ev, a.league)
                if g is None or g["id"] in have:
                    continue
                core = get(CORE.format(path=cfg["core"], eid=g["id"]))
                time.sleep(a.sleep)
                lines = next((p for p in (parse_odds_item(it) for it in (core or {}).get("items", [])) if p), None)
                if lines is None:
                    n_nolines += 1
                    continue
                sink.write(json.dumps({**g, **lines}) + "\n")
                sink.flush()
                have.add(g["id"])
                n_new += 1
                if a.max_games and n_new >= a.max_games:
                    print(f"stopped at --max-games {a.max_games}", flush=True)
                    return 0
            if d.day == 1 or d == end:
                print(f"  through {d}: +{n_new} games with lines, {n_nolines} without", flush=True)
            d += timedelta(days=1)
    print(f"done: +{n_new} games with lines, {n_nolines} completed games without lines, {len(have)} cached", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
