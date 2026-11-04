"""probe_soccer_odds -- Day-1 odds coverage truth test for the soccer vertical (READ-ONLY diagnostic).

In the style of the odds-provider scan: do not trust a provider's book list, MEASURE what actually comes back.
Checks, per league (EPL / UCL):
  * ParlayAPI (needs PARLAY_API_KEY): the league slate for soccer_epl / soccer_uefa_champs_league -- number of
    upcoming matches, how many books quote each market (h2h THREE-way with a Draw outcome, totals, btts), and
    the soccer_other player-prop row counts (Phase 2 recon only; props stay gated OFF).
  * Kalshi public API (no key): which series exist under the league prefixes (KXEPL*, KXUCL*), the exact
    event / market ticker grammar of live match contracts, and whether a TIE / DRAW outcome contract exists
    alongside the home / away contracts of the same event.
  * BestBook: whether the local crawl readers expose soccer at all (sport soccer / epl / ucl).

Nothing here writes odds, banks data, or spends beyond the slate pulls it reports. Run:
    PARLAY_API_KEY=... python scripts/probe_soccer_odds.py [--skip-parlay] [--skip-kalshi]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
LEAGUES = {"epl": {"parlay_key": "soccer_epl", "kalshi_prefix": "KXEPL"},
           "ucl": {"parlay_key": "soccer_uefa_champs_league", "kalshi_prefix": "KXUCL"}}


def probe_parlay(league: str, parlay_key: str) -> dict:
    from nba.odds.parlayapi import ParlayApiClient
    client = ParlayApiClient()
    out: dict = {"league": league, "parlay_key": parlay_key}
    slate = client.get_slate(parlay_key, markets=["h2h", "totals", "btts"])
    out["upcoming_matches"] = len(slate or [])
    books_by_market: dict[str, set] = {"h2h": set(), "totals": set(), "btts": set()}
    three_way = 0
    for ev in slate or []:
        for b in ev.get("bookmakers", []) or []:
            for mk in b.get("markets", []) or []:
                key = mk.get("key")
                if key in books_by_market:
                    books_by_market[key].add(b.get("key"))
                    if key == "h2h" and len(mk.get("outcomes", []) or []) == 3:
                        three_way += 1
    out["books_per_market"] = {k: len(v) for k, v in books_by_market.items()}
    out["three_way_h2h_book_markets"] = three_way
    try:
        props = client.get_slate("soccer_other", markets=["player_goals", "player_goals_assists",
                                                          "player_shots_on_target"])
        out["soccer_other_prop_events"] = len(props or [])
    except Exception as e:  # noqa: BLE001 -- recon only
        out["soccer_other_prop_events"] = f"error: {e!r}"[:120]
    return out


def probe_kalshi(league: str, prefix: str) -> dict:
    import requests
    out: dict = {"league": league, "kalshi_prefix": prefix}
    resp = requests.get(f"{KALSHI_BASE}/series", params={"limit": 1000}, timeout=30)
    resp.raise_for_status()
    series = [s for s in resp.json().get("series", []) if str(s.get("ticker", "")).startswith(prefix)]
    out["series"] = sorted(s.get("ticker") for s in series)
    game_series = next((s.get("ticker") for s in series if str(s.get("ticker", "")).endswith("GAME")), None)
    out["game_series"] = game_series
    if game_series:
        resp = requests.get(f"{KALSHI_BASE}/markets", params={"series_ticker": game_series, "status": "open",
                                                               "limit": 200}, timeout=30)
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
        out["open_game_markets_sampled"] = len(markets)
        by_event: dict[str, list[str]] = {}
        for mk in markets:
            by_event.setdefault(mk.get("event_ticker", ""), []).append(mk.get("ticker", ""))
        out["sample_events"] = dict(list(by_event.items())[:3])
        tie_events = [ev for ev, tickers in by_event.items()
                      if any(t.endswith("-TIE") or t.endswith("-DRAW") for t in tickers)]
        out["events_with_tie_contract_in_sample"] = len(tie_events)
    return out


def probe_bestbook() -> dict:
    try:
        from ev import bestbook_events
        games, meta = bestbook_events.read_latest_game_lines("soccer", __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc), 180.0)
        return {"soccer_games_in_latest_crawl": len(games or []), "meta_reason": meta.get("reason")}
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:160]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Soccer odds coverage truth test (read-only)")
    ap.add_argument("--skip-parlay", action="store_true")
    ap.add_argument("--skip-kalshi", action="store_true")
    args = ap.parse_args(argv)
    report: dict = {"parlayapi": {}, "kalshi": {}, "bestbook": None}
    if not args.skip_parlay:
        if not os.environ.get("PARLAY_API_KEY"):
            report["parlayapi"] = {"skipped": "PARLAY_API_KEY not set"}
        else:
            for league, cfg in LEAGUES.items():
                try:
                    report["parlayapi"][league] = probe_parlay(league, cfg["parlay_key"])
                except Exception as e:  # noqa: BLE001
                    report["parlayapi"][league] = {"error": repr(e)[:200]}
    if not args.skip_kalshi:
        for league, cfg in LEAGUES.items():
            try:
                report["kalshi"][league] = probe_kalshi(league, cfg["kalshi_prefix"])
            except Exception as e:  # noqa: BLE001
                report["kalshi"][league] = {"error": repr(e)[:200]}
    report["bestbook"] = probe_bestbook()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
