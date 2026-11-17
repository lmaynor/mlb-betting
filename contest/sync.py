"""Refresh a contest from ESPN scoreboard events: the games on the board, their quotes, their live state, and their grades.

One pass does four independent things for each game the contest includes (all idempotent, so a scheduler may call it as often as it
likes):
  1. upsert the game (kickoff, team names, round label) and its display data (team ids / ranks, live score line);
  2. for a game that has NOT started, store the pre-game DraftKings main lines as the board quotes (the feed's own timestamp is `now`:
     ESPN's scoreboard carries no per-quote time, so a quote is only as fresh as the pass that wrote it);
  3. a final game settles every bet its score decides (`store.settle_game`), a postponed / cancelled one voids its legs;
  4. one broken event never stops the pass: it is recorded in `errors` and the rest carry on.

Nothing here talks to the network: `contest.espn.parse_event` has already turned the payload into dicts, and the caller fetches it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

from contest import store
from contest.slips import Quote


def quotes_from_odds(odds: dict, fetched_at: datetime) -> dict[tuple, Quote]:
    """{(market, side): Quote} in the board's convention: a spread quote carries the PICKED side's handicap, a total the total, a moneyline None."""
    out: dict[tuple, Quote] = {}
    sp, tot, ml = odds.get("spread"), odds.get("total"), odds.get("ml")
    if sp:
        out[("spread", "home")] = Quote(float(sp["line"]), int(sp["home"]), fetched_at)
        out[("spread", "away")] = Quote(-float(sp["line"]), int(sp["away"]), fetched_at)
    if tot:
        out[("total", "over")] = Quote(float(tot["line"]), int(tot["over"]), fetched_at)
        out[("total", "under")] = Quote(float(tot["line"]), int(tot["under"]), fetched_at)
    if ml:
        out[("ml", "home")] = Quote(None, int(ml["home"]), fetched_at)
        out[("ml", "away")] = Quote(None, int(ml["away"]), fetched_at)
    return out


def _state_json(ev: dict) -> dict | None:
    if "state" not in ev:
        return None
    st = dict(ev["state"])
    st["home_score"], st["away_score"] = ev["home_score"], ev["away_score"]
    return st


def sync_events(engine, contest_id: int, events: Iterable[dict], now: datetime, include: Callable[[dict], bool] = lambda e: True) -> dict:
    """Apply parsed ESPN events (contest.espn.parse_event output) to the contest. Returns counts and any per-event errors."""
    res = {"games": 0, "quotes": 0, "settled_bets": 0, "voided_games": 0, "finals": 0, "errors": []}
    for ev in events:
        if ev is None or not include(ev):
            continue
        try:
            gid = store.upsert_game(engine, contest_id, ev["espn_id"], ev["commence"], ev["away"]["name"], ev["home"]["name"], round_label=ev["round"])
            store.set_game_meta(engine, gid, teams={"home": ev["home"], "away": ev["away"]}, state=_state_json(ev))
            res["games"] += 1
            if ev["status"] == "scheduled" and ev.get("odds"):
                q = quotes_from_odds(ev["odds"], now)
                if q:
                    store.write_quotes(engine, gid, q, now, source=f"espn:{ev['odds'].get('provider') or 'unknown'}")
                    res["quotes"] += len(q)
            elif ev["status"] == "final" and "home_score" in ev:
                res["settled_bets"] += len(store.settle_game(engine, gid, ev["home_score"], ev["away_score"], now))
                res["finals"] += 1
            elif ev["status"] == "void":
                res["settled_bets"] += len(store.void_game(engine, gid, now))
                res["voided_games"] += 1
        except Exception as exc:                                  # noqa: BLE001 - one bad event must not stop the pass
            res["errors"].append({"espn_id": ev.get("espn_id"), "error": f"{type(exc).__name__}: {exc}"})
    return res
