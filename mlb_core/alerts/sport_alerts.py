"""
mlb_core.alerts.sport_alerts -- member-facing +EV alerts for NFL / NHL / NBA props (the #<sport>-alerts channels).

The prop sweeps (ev.sweep_props_alerts, ev.sweep_nfl_props Sink A) keep posting EVERY flagged candidate to the admin
channel as evidence. This module posts the Beezy-Score-gated subset to the sport's member channel, labelled paper /
unvalidated: the repo's own studies show no proven edge on these books yet, so the gate (ALERTS_MIN_SCORE, default 45 =
LEAN and up) and the label are the honesty mechanism, not decoration.

Delivery mirrors the MLB pagers: a real bot-user message first (slip bots can read it), the sport's card webhook as the
fallback (DISCORD_WEBHOOK_CARD_<SPORT>; the env names predate the -alerts rename).
"""
from __future__ import annotations

import logging
import os

from mlb_core.alerts.beezy_score import beezy_score, tier
from mlb_core.alerts.discord_routes import (
    bot_configured, post_via_bot, slip_text, webhook_for, with_deep_links,
)

log = logging.getLogger("mlb_core.alerts.sport_alerts")

DEFAULT_MIN_SCORE = 45
_TIER_COLOR = {"strong": 0x57F287, "lean": 0xE3B261, "watch": 0x8B949E}
MAX_LINES = 15


def min_score() -> int:
    try:
        return int(os.environ.get("ALERTS_MIN_SCORE", DEFAULT_MIN_SCORE))
    except ValueError:
        return DEFAULT_MIN_SCORE


def row_score(r: dict) -> int:
    """Score a sweep outlier row: edge = ev, hit prob = fair probability of the side, books = reference-book count."""
    return beezy_score(r.get("ev"), r.get("n_books"), r.get("p_fair"))


def gate(rows: list[dict], floor: int | None = None) -> list[dict]:
    """Rows at/above the score floor, best first, each annotated with its `score`."""
    floor = min_score() if floor is None else floor
    scored = [{**r, "score": row_score(r)} for r in rows]
    return sorted((r for r in scored if r["score"] >= floor), key=lambda r: (-r["score"], -(r.get("ev") or 0)))


def _what(r: dict) -> str:
    side = str(r.get("side") or "").capitalize()
    return f"{r['player']} {side} {r['point']:g} {str(r['stat']).replace('_', ' ')}"


def slip_line(r: dict) -> str:
    from mlb_core.notify.discord import book_display
    return f"{_what(r)} @ {book_display(r['book'])} {int(r['american']):+d}"


def build_embed(sport: str, rows: list[dict]) -> dict:
    from mlb_core.notify.discord import book_display
    lines = []
    for r in rows[:MAX_LINES]:
        lines.append(f"**{r['score']}** {tier(r['score']).upper()} | **{_what(r)}** @ **{book_display(r['book'])} "
                     f"{int(r['american']):+d}** | EV {r['ev'] * 100:+.1f}% | fair {r['p_fair']:.1%} | {r['n_books']} bks")
    extra = f"\n+{len(rows) - MAX_LINES} more on the site" if len(rows) > MAX_LINES else ""
    best = tier(rows[0]["score"]) if rows else "watch"
    return with_deep_links({
        "title": f"{sport.upper()} +EV alerts: {len(rows)} new (PAPER / UNVALIDATED)",
        "description": "\n".join(lines) + extra,
        "color": _TIER_COLOR[best],
        "footer": {"text": f"Beezy Score >= {min_score()} | edge vs other books' consensus | not a recommendation"},
    })


def post_sport_alerts(sport: str, rows: list[dict]) -> bool:
    """Post already-gated rows (see gate()) to the sport's alerts channel. True only if something was delivered, so
    callers can leave undelivered rows un-notified and retry on the next pass. Never raises."""
    if not rows:
        return False
    embed = build_embed(sport, rows)
    slip = slip_text(slip_line(r) for r in rows)
    if bot_configured() and post_via_bot("card", slip, [embed], sport=sport):
        return True
    url = webhook_for("card", sport=sport)
    if not url:
        log.warning("%s alerts: no bot and no card webhook configured; %d row(s) not posted", sport, len(rows))
        return False
    try:
        import requests
        return requests.post(url, json={"embeds": [embed]}, timeout=20).status_code < 300
    except Exception as e:  # noqa: BLE001 -- a Discord outage must not break the sweep
        log.warning("%s alerts webhook failed: %r", sport, e)
        return False
