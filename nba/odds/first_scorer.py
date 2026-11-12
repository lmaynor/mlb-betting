"""ParlayAPI micro-market accumulator for first-scorer markets (first basket / first TD).

WHY (spike: ~/workspace/beezy/first-basket-classifier-spike-2026-11-11.md): `player_first_basket` and
`player_first_team_basket` exist in ParlayAPI's live catalog but have never been requested, ParlayAPI keeps
NO historical props, and no other feed we hold carries the market -- so prices are forward-only and every
unbanked night is unrecoverable. The NFL cousin `player_1st_td` (first touchdown scorer) is the same shape
at the same 1 credit per (event x market), with outcomes already derivable from banked nflverse PBP.

Deliberate deviations from nba.odds.accumulator (the props accumulator):
  * RAW-FIRST archiving. The full per-event payload is stored per snapshot BEFORE any flattening (the
    PR #218 pattern). The flattened best-book CSV used for props destroys exactly what this market is:
    a per-book outcome SET. De-vigging and completeness checks need every book's full list.
  * THREE snapshots per game: T-120, T-30, T-5 minutes. Samples at T-4..6h were thin (one book, partial
    list); the snapshots double as the instrument that learns when complete boards actually post.
  * COMPLETENESS GATE: a game counts as *priced* only if at least one book lists >= FIRST_SCORER_MIN_OUTCOMES
    (>=8) outcomes for a market. Partial lists are banked but flagged priced=False and excluded from grading.

Settlement variants: book definitions of "first basket" differ (first FIELD GOAL vs first POINT scored,
free throws included). Prices are banked for both markets as listed; the outcomes job
(nba.data.first_basket_outcomes / nfl.data.first_td_outcomes, separate PR) stores BOTH variants so grading
matches each book's own definition where known. This module does not settle anything.

Credit cost (worst case, the accumulator charges even when books return nothing):
  NBA ~11 games/night x 2 markets x 3 snapshots ~= 66 credits/night (~2% of the 100k pool);
  NFL ~16 games/week x 1 market x 3 snapshots ~= 48 credits/week.
Spend is metered through nba.odds.credit_ledger under the UNDERLYING sport's ledger, like every other
ParlayAPI caller. No new budget is created here: provisioning carves the micro-market spend out of the
existing basketball_nba cap via the job env (see deploy/setup_first_scorer_banking.sh), and the NFL job
sets ODDS_BANK_BUDGET_AMERICANFOOTBALL_NFL explicitly, because the accumulator ledger for that sport is
0 by default (manual probes only). tests/test_parlay_budget.py's pinned total is unchanged.

Snapshot selection: a run buys an event only when its minutes-to-tip falls inside a snapshot window
(T-120: 105..135, T-30: 20..40, T-5: 0..10). The scheduler fires every 15 minutes during game windows;
runs outside every window for an event spend no prop credits on it (slate discovery only).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from mlb_core import storage
from nba.config import (FIRST_SCORER_MARKETS, FIRST_SCORER_MIN_OUTCOMES, first_scorer_latest_key,
                        first_scorer_raw_key, first_scorer_summary_key)
from nba.odds import credit_ledger
from nba.odds.parlay_extract import split_team_tail

logger = logging.getLogger(__name__)

# snapshot label -> (min_minutes_to_tip, max_minutes_to_tip), inclusive
SNAPSHOT_WINDOWS = {"T-120": (105.0, 135.0), "T-30": (20.0, 40.0), "T-5": (0.0, 10.0)}
SNAPSHOT_LABELS = tuple(SNAPSHOT_WINDOWS)


def snapshot_label(minutes_to_tip):
    """The snapshot window a run at `minutes_to_tip` before tip belongs to, or None (buy nothing)."""
    if minutes_to_tip is None:
        return None
    for label, (lo, hi) in SNAPSHOT_WINDOWS.items():
        if lo <= minutes_to_tip <= hi:
            return label
    return None


def minutes_to_tip(commence_time, now=None):
    """Minutes from `now` (default: now UTC) to an ISO commence_time, or None when unparseable."""
    try:
        start = datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (start - now).total_seconds() / 60.0


def _outcome_player(outcome):
    """(player, team_hint) from a ParlayAPI Yes-only prop outcome, or (None, None) for junk rows."""
    player, team_hint = split_team_tail(outcome.get("description") or outcome.get("name") or "")
    if not player or "{" in player or "}" in player or " @ " in player:
        return None, None
    return player, team_hint


def flatten_micro_market(event_obj, sport=""):
    """Single-event ParlayAPI object -> one row per (book, market, player) Yes price.

    Unlike flatten_parlay_props this keeps EVERY book's full outcome set (no best-book collapse and no
    two-sided requirement): these markets are one-sided Yes lists, and the set itself is the data."""
    if not event_obj:
        return []
    rows = []
    for book in event_obj.get("bookmakers", []) or []:
        bkey = book.get("key", "")
        for market in book.get("markets", []) or []:
            mkey = market.get("key", "")
            if mkey not in FIRST_SCORER_MARKETS.get(sport, []) and mkey not in {
                    m for ms in FIRST_SCORER_MARKETS.values() for m in ms}:
                continue
            seen = set()
            for o in market.get("outcomes", []) or []:
                if (o.get("name") or "").strip().lower() not in ("yes", "") and o.get("description") is None:
                    continue
                player, team_hint = _outcome_player(o)
                if not player or o.get("price") is None or player in seen:
                    continue
                seen.add(player)
                rows.append({
                    "event_id": event_obj.get("id"), "commence_time": event_obj.get("commence_time") or "",
                    "home_team": event_obj.get("home_team"), "away_team": event_obj.get("away_team"),
                    "book": bkey, "market": mkey, "player": player, "team_hint": team_hint,
                    "price": int(o["price"]), "point": o.get("point"),
                })
    return rows


def evaluate_completeness(event_obj, sport="", min_outcomes=FIRST_SCORER_MIN_OUTCOMES):
    """Per (book, market): listed outcome count, team coverage, and the priced gate.

    priced=True iff count >= min_outcomes. team_coverage is 'both' when outcome team hints name >=2
    distinct teams, 'unknown' when books send no team hints (count alone governs, flagged), else 'one'.
    """
    out = {}
    for row in flatten_micro_market(event_obj, sport):
        key = (row["book"], row["market"])
        rec = out.setdefault(key, {"book": row["book"], "market": row["market"], "outcomes": 0,
                                   "players": [], "team_hints": set()})
        rec["outcomes"] += 1
        rec["players"].append(row["player"])
        if row["team_hint"]:
            rec["team_hints"].add(row["team_hint"])
    for rec in out.values():
        hints = rec.pop("team_hints")
        rec["team_coverage"] = "both" if len(hints) >= 2 else ("unknown" if not hints else "one")
        rec["priced"] = rec["outcomes"] >= min_outcomes
    return [out[k] for k in sorted(out)]


def game_is_priced(completeness):
    """A game counts as priced when ANY book clears the gate for ANY banked market."""
    return any(r["priced"] for r in completeness)


def _credits_of(client, default):
    try:
        v = int(getattr(client, "credits_last", None))
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def accumulate_micro(sport, snapshot=None, client=None, guard=True, now=None, max_credits=0):
    """One accumulator pass for `sport`: buy the micro markets for events inside a snapshot window.

    snapshot=None auto-selects per event from minutes-to-tip; an explicit snapshot buys every event
    whose window matches that label (used by targeted manual runs). Raw payloads are written FIRST,
    per event per snapshot; the summary (with completeness + priced flags) is written after.
    Returns the run summary dict (also written to the per-sport latest pointer)."""
    from nba.odds.parlayapi import ParlayApiClient
    client = client or ParlayApiClient()
    markets = list(FIRST_SCORER_MARKETS[sport])
    now = now or datetime.now(timezone.utc)
    date = now.date().isoformat()
    month = f"{now.year:04d}-{now.month:02d}"
    spent = 0
    if guard:
        a0 = credit_ledger.allowance(sport, 1, now.date(), client.credits_remaining)
        if not a0.ok:
            result = {"sport": sport, "kind": "first_scorer", "date": date, "skipped": True,
                      "reason": a0.reason, "credits_spent": 0}
            logger.warning("first_scorer SKIPPED: %s", a0.reason)
            return result
    slate = client.get_slate(sport)
    c = _credits_of(client, 1)
    spent += c
    if guard:
        credit_ledger.record(sport, month, c)
    targets = []
    for ev in slate or []:
        label = snapshot_label(minutes_to_tip(ev.get("commence_time"), now))
        if label is None:
            continue
        if snapshot is not None and label != snapshot:
            continue
        targets.append((label, ev))
    if guard and targets:
        a = credit_ledger.allowance(sport, len(targets) * len(markets), now.date(),
                                     client.credits_remaining, run_cap=max_credits)
        granted = (a.credits // len(markets)) if a.ok else 0
        if granted < len(targets):
            logger.warning("first_scorer: budget buys %d of %d events (%s)", granted, len(targets), a.reason)
        targets = targets[:granted]
    summaries, event_spend = [], 0
    try:
        for label, ev in targets:
            eid = ev.get("id")
            if not eid:
                continue
            obj = client.get_event_props(sport, eid, markets)
            event_spend += _credits_of(client, len(markets))
            if not obj:
                continue
            # RAW FIRST: the untouched per-event payload lands before any derived row exists.
            storage.write_bytes(json.dumps(obj).encode(), first_scorer_raw_key(sport, date, eid, label))
            completeness = evaluate_completeness(obj, sport)
            summaries.append({
                "event_id": eid, "commence_time": ev.get("commence_time"),
                "home_team": ev.get("home_team"), "away_team": ev.get("away_team"),
                "snapshot": label, "completeness": completeness, "priced": game_is_priced(completeness),
                "rows": flatten_micro_market(obj, sport),
            })
    finally:
        if guard:
            credit_ledger.record(sport, month, event_spend)
        spent += event_spend
    if summaries:
        storage.write_bytes(json.dumps(summaries, default=str).encode(),
                            first_scorer_summary_key(sport, date, snapshot or "auto"))
    result = {"sport": sport, "kind": "first_scorer", "date": date, "snapshot": snapshot or "auto",
              "markets": markets, "events_targeted": len(targets), "events_banked": len(summaries),
              "games_priced": sum(1 for s in summaries if s["priced"]),
              "credits_spent": spent, "credits_remaining": client.credits_remaining,
              "timestamp": datetime.now(timezone.utc).isoformat()}
    storage.write_bytes(json.dumps(result, default=str).encode(), first_scorer_latest_key(sport))
    logger.info("first_scorer: %s", result)
    return result
