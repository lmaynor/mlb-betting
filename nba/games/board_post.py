"""
nba.games.board_post -- the NBA games-board digest for Discord #nba-alerts. OFF unless explicitly enabled.

Posts at most ONE digest per Central day, only RELIABLE rows (both teams >= EARLY_SEASON_GAMES games; ratings are last
season's before that), only games where the model and the market differ by at least `MIN_MARGIN_GAP` points of margin or
`MIN_TOTAL_GAP` points of total, and with the label + accuracy note the board payload carries. It never says edge, EV, value
or pick: in the 2021-25 walk-forward the closing line beat these models on every market
(docs/analysis/nba_game_models_2026-10-20.md), so a gap is "where our number differs", not "where to bet". It does not use
`with_deep_links` (those links point at the +EV terminal).

Enable: `python -m nba.games.board --post` (needs the bot or DISCORD_WEBHOOK_CARD_NBA at runtime). The deploy script does NOT
turn this on. Delivery mirrors mlb_core.alerts.sport_alerts (bot first, card webhook fallback), never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mlb_core.alerts.discord_routes import bot_configured, post_via_bot, webhook_for

log = logging.getLogger("nba.games.board_post")

STATE_KEY = "EVSweep/nba_games/posted.json"
MIN_MARGIN_GAP = 3.0
MIN_TOTAL_GAP = 5.0
MAX_LINES = 10
COLOR = 0x8B949E                      # neutral grey: this is not a tiered signal
SITE_URL = "https://beezy.fyi/picks/nba"


def _sgn(x: float) -> str:
    return f"{x:+.1f}"


def pick_rows(payload: dict) -> list[dict]:
    rows = []
    for g in payload.get("games") or []:
        if not g.get("reliable"):
            continue
        d = g.get("diff") or {}
        mg, tg = d.get("margin_pts"), d.get("total_pts")
        if (mg is not None and abs(mg) >= MIN_MARGIN_GAP) or (tg is not None and abs(tg) >= MIN_TOTAL_GAP):
            rows.append(g)
    rows.sort(key=lambda g: -max(abs((g["diff"].get("margin_pts") or 0)) / MIN_MARGIN_GAP,
                                abs((g["diff"].get("total_pts") or 0)) / MIN_TOTAL_GAP))
    return rows


def line_for(g: dict) -> str:
    m, mo, d = g["market"], g["model"], g["diff"]
    parts = [f"**{g['away']} @ {g['home']}**"]
    if m.get("spread_home") is not None:
        parts.append(f"spread {g['home']} {_sgn(m['spread_home'])} mkt / {_sgn(mo['spread_home'])} ours")
    if m.get("total") is not None:
        parts.append(f"total {m['total']:g} mkt / {mo['total']:g} ours")
    if m.get("home_win_p") is not None:
        parts.append(f"{g['home']} win {m['home_win_p']:.0%} mkt / {mo['home_win_p']:.0%} ours")
    return " | ".join(parts)


def build_embed(payload: dict, rows: list[dict]) -> dict:
    acc = payload.get("accuracy") or {}
    lines = [line_for(g) for g in rows[:MAX_LINES]]
    extra = f"\n+{len(rows) - MAX_LINES} more on the site" if len(rows) > MAX_LINES else ""
    note = (f"Our lines vs the market consensus ({payload.get('lines_age_minutes') or '?'} min old). "
            f"{payload.get('label') or ''}")
    return {
        "title": f"NBA model lines vs market: {len(rows)} game(s) where we differ (UNVALIDATED)",
        "url": SITE_URL,
        "description": "\n".join(lines) + extra + f"\n\n{note}\nFull board: {SITE_URL}",
        "color": COLOR,
        "footer": {"text": f"{acc.get('seasons', '')} walk-forward: {acc.get('verdict', 'market line is more accurate')}"[:2048]},
    }


def _central_day(now: datetime) -> str:
    return now.astimezone(ZoneInfo("America/Chicago")).date().isoformat()


def _read_state() -> dict:
    from mlb_core import storage
    if not storage.exists(STATE_KEY):
        return {}
    return json.loads(storage.read_bytes(STATE_KEY).decode())


def post_digest(payload: dict, now: datetime | None = None, dry_run: bool = False) -> dict:
    """-> {"status": "posted"|"dry_run"|"already_posted"|"nothing_to_post"|"stale"|"undelivered", ...}. Never raises."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if payload.get("status") != "ok" or payload.get("stale"):
        return {"status": "stale"}
    rows = pick_rows(payload)
    if not rows:
        return {"status": "nothing_to_post"}
    day = _central_day(now)
    try:
        if _read_state().get("day") == day:
            return {"status": "already_posted", "day": day}
    except Exception:  # noqa: BLE001 -- an unreadable state file must not cause a duplicate-post storm: skip this run
        log.exception("nba board digest: state unreadable; not posting")
        return {"status": "undelivered", "reason": "state_unreadable"}
    embed = build_embed(payload, rows)
    if dry_run:
        return {"status": "dry_run", "embed": embed, "rows": len(rows)}
    delivered = False
    try:
        if bot_configured() and post_via_bot("card", None, [embed], sport="nba"):
            delivered = True
        else:
            url = webhook_for("card", sport="nba")
            if url:
                import requests
                delivered = requests.post(url, json={"embeds": [embed]}, timeout=20).status_code < 300
            else:
                log.warning("nba board digest: no bot and no card webhook configured; not posted")
    except Exception as exc:  # noqa: BLE001 -- a Discord outage must not break the job
        log.warning("nba board digest failed: %r", exc)
    if not delivered:
        return {"status": "undelivered", "rows": len(rows)}
    from mlb_core import storage
    storage.write_bytes(json.dumps({"day": day, "posted_at": now.isoformat(timespec="seconds"),
                                    "game_keys": [g["game_key"] for g in rows]}).encode(), STATE_KEY)
    return {"status": "posted", "day": day, "rows": len(rows)}
