"""Discord announcements for the contest: kickoff (picks on that game are now visible), final (with the standings), and an entry going out.

OFF unless CONTEST_NOTIFY is on AND CONTEST_DISCORD_WEBHOOK is set. Each message is claimed in `contest_notice` (contest, key) BEFORE it is
sent, so two overlapping passes can never post the same one twice; a failed send releases the claim so the next pass retries it. Messages
carry display names only (never a user id, a stake or a pick that is still hidden) and disable all mentions.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from typing import Callable

import sqlalchemy.exc as sa_exc
from sqlalchemy import text

from contest import store

_OFF = {"", "0", "false", "off", "no"}
KICKOFF_WINDOW = timedelta(minutes=30)       # a kickoff older than this is not announced (a late first run must not replay history)


def enabled(env=os.environ) -> bool:
    return (env.get("CONTEST_NOTIFY") or "").strip().lower() not in _OFF and bool((env.get("CONTEST_DISCORD_WEBHOOK") or "").strip())


def post_discord(webhook: str, content: str) -> None:
    body = json.dumps({"content": content[:1900], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json", "User-Agent": "beezy-contest/1.0"})
    urllib.request.urlopen(req, timeout=15).close()


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def pending_messages(engine, contest_id: int, now: datetime) -> list[tuple[str, str]]:
    """[(key, text)] for everything worth announcing right now. Pure reads."""
    out: list[tuple[str, str]] = []
    games = store.games_of(engine, contest_id)
    board = store.leaderboard(engine, contest_id)
    for g in games:
        ct = store.parse_ts(g["commence_time"])
        name = f"{g['away_team']} at {g['home_team']}"
        if g["status"] == "scheduled" and ct <= now <= ct + KICKOFF_WINDOW:
            out.append((f"kickoff:{g['id']}", f"Kickoff: {name}. Picks on this game are now visible in the Pool."))
        if g["status"] == "final" and g["home_score"] is not None:
            top = ", ".join(f"{r['rank']}. {r['display_name']} {_money(r['bankroll_cents'])}" for r in board[:3])
            out.append((f"final:{g['id']}", f"Final: {g['away_team']} {g['away_score']}, {g['home_team']} {g['home_score']}. Bets on it are settled. Top of the board: {top}."))
        if g["status"] == "void":
            out.append((f"void:{g['id']}", f"Postponed or cancelled: {name}. Bets on it are refunded."))
    for e in store.entries_of(engine, contest_id):
        if e["status"] == "eliminated":
            out.append((f"out:{e['entry_id']}", f"{e['display_name']} is out of the contest."))
    return out


def _claim(engine, contest_id: int, key: str, now: datetime) -> bool:
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO contest_notice (contest_id, key, sent_at) VALUES (:c, :k, :t)"), {"c": contest_id, "k": key, "t": store.iso(now)})
        return True
    except sa_exc.IntegrityError:
        return False


def _release(engine, contest_id: int, key: str) -> None:
    with engine.begin() as c:
        c.execute(text("DELETE FROM contest_notice WHERE contest_id = :c AND key = :k"), {"c": contest_id, "k": key})


def notify_pass(engine, contest_id: int, now: datetime, send: Callable[[str], None], first_run_quiet: bool = True) -> dict:
    """Send every message not yet sent. On the very first pass for a contest, finals and voids that already happened are recorded as sent
    without posting (`first_run_quiet`), so switching notifications on mid-season does not flood the channel with history."""
    with engine.connect() as c:
        first = c.execute(text("SELECT COUNT(*) FROM contest_notice WHERE contest_id = :c"), {"c": contest_id}).scalar_one() == 0
    sent = skipped = failed = 0
    for key, msg in pending_messages(engine, contest_id, now):
        if not _claim(engine, contest_id, key, now):
            continue
        if first and first_run_quiet and not key.startswith("kickoff:"):
            skipped += 1
            continue
        try:
            send(msg)
            sent += 1
        except Exception:                                    # noqa: BLE001 - a dead webhook must never break the refresh
            _release(engine, contest_id, key)
            failed += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}
