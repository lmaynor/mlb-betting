"""
mlb_core.alerts.sport_alerts -- member-facing +EV alerts for NFL / NHL / NBA props (the #<sport>-alerts channels).

The prop sweeps (ev.sweep_props_alerts, ev.sweep_nfl_props Sink A) post EVERY flagged candidate into the sport's daily signals
thread as evidence. This module also posts the Beezy-Score-gated subset to the channel itself, labelled paper / unvalidated:
the repo's own studies show no proven edge on these books yet, so the gate (ALERTS_MIN_SCORE, default 45 = LEAN and up) and the
label are the honesty mechanism, not decoration.

Every alert carries the same two things the MLB pagers do:
  * plain slip text (one pick per line, no markdown) that a member can copy, and
  * a thread where the slip bots (GamblyBot, Playbook) are @mentioned -- they only react to a mention on a real bot-user message.
Top-level highlights get one message + one thread per alert (like fast_alert_loop). The daily signals thread cannot nest a thread,
so its messages carry the mention inline (signal_message(), tag_slip_bots).

Delivery mirrors the MLB pagers: a real bot-user message first (slip bots can read it), the sport's card webhook as the
fallback (DISCORD_WEBHOOK_CARD_<SPORT>; the env names predate the -alerts rename). A webhook cannot tag a bot, so the fallback
keeps the plain slip text and drops the tag.
"""
from __future__ import annotations

import logging
import os

from mlb_core.alerts.beezy_score import beezy_score, tier
from mlb_core.alerts.discord_routes import (
    bot_configured, post_via_bot, slip_text, webhook_for, with_deep_links,
)
from ev.daily_card import (
    build_daily_card, is_ev_outlier, is_official_card_play,
    load_locked_card, save_locked_card,
)

log = logging.getLogger("mlb_core.alerts.sport_alerts")

DEFAULT_MIN_SCORE = 45
_TIER_COLOR = {"strong": 0x57F287, "lean": 0xE3B261, "watch": 0x8B949E}
MAX_ALERTS = 10            # top-level highlights per pass (best score first); every row is still in the daily thread
SLIP_LIMIT = 1700          # plain slip text per message, leaving room for the two bot mentions the bot prefixes
LEGEND = "paper / unvalidated | not a recommendation"

# Mirrors ev.daily_projections.STAT_LABEL (tests/test_sport_alerts.py pins them equal). Unknown codes fall back to the
# lower-cased code with underscores removed.
STAT_NAMES = {
    "pass_yds": "Pass Yds", "rush_yds": "Rush Yds", "rec_yds": "Rec Yds", "receptions": "Receptions",
    "pass_tds": "Pass TDs", "anytime_td": "Anytime TD", "pass_completions": "Completions",
    "pass_attempts": "Pass Attempts", "rush_attempts": "Carries", "interceptions": "Interceptions",
    "sog": "Shots on Goal", "goals": "Goals", "points": "Points",
    "assists": "Assists", "saves": "Saves", "blocked_shots": "Blocked Shots",
    "rebounds": "Rebounds", "threes": "3-Pointers", "pra": "Pts+Reb+Ast", "pr": "Pts+Reb", "pa": "Pts+Ast",
    "ra": "Reb+Ast", "steals": "Steals", "blocks": "Blocks",
}


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


# -- copy ------------------------------------------------------------------------------------------------------------

def stat_name(stat) -> str:
    return STAT_NAMES.get(str(stat)) or str(stat).replace("_", " ")


def _what(r: dict) -> str:
    side = str(r.get("side") or "").capitalize()
    return f"{r['player']} {side} {r['point']:g} {stat_name(r['stat'])}"


def matchup(r: dict) -> str:
    """'nfl:2026-10-25:KC@BUF' or 'nhl:BOS@NYR' -> 'KC @ BUF'; '' when the key has no teams."""
    teams = str(r.get("game_key") or "").rsplit(":", 1)[-1]
    return teams.replace("@", " @ ") if "@" in teams else ""


def kickoff(r: dict) -> str:
    """'starts in 3h 10m' from whichever minutes-to-start field the sweep set; '' when unknown or already started."""
    for k in ("mins_to_kick", "mins_to_start"):
        try:
            m = float(r[k])
        except (KeyError, TypeError, ValueError):
            continue
        if m <= 0:
            return ""
        h, mm = divmod(int(m), 60)
        return f"starts in {h}h {mm:02d}m" if h else f"starts in {mm}m"
    return ""


def slip_line(r: dict) -> str:
    from mlb_core.notify.discord import book_display
    return f"{_what(r)} @ {book_display(r['book'])} {int(r['american']):+d}"


def detail_line(r: dict) -> str:
    """One compact human line for a row inside a grouped embed (the slip text carries the copyable version)."""
    from mlb_core.notify.discord import book_display
    return (f"**{r['player']}** {str(r.get('side') or '').capitalize()} {r['point']:g} {stat_name(r['stat'])} | "
            f"**{book_display(r['book'])} {int(r['american']):+d}** | EV **{r['ev'] * 100:+.1f}%** | "
            f"fair {r['p_fair']:.1%} | {r['n_books']} books")


def alert_embed(r: dict) -> dict:
    """The embed for ONE alert: what to bet, where, and why in two plain sentences."""
    from mlb_core.notify.discord import book_display
    from mlb_core.odds.utils import american_to_implied_prob
    price = int(r["american"])
    implied = american_to_implied_prob(price)
    where = " | ".join(x for x in (matchup(r), kickoff(r)) if x)
    desc = (f"**{book_display(r['book'])} {price:+d}**  |  EV **{r['ev'] * 100:+.1f}%**\n"
            f"That price needs {implied:.1%} to hit. The other {r['n_books']} books put it at {r['p_fair']:.1%}."
            + (f"\n{where}" if where else ""))
    score = r.get("score")
    t = tier(score) if score is not None else "watch"
    foot = f"Beezy Score {score} {t.upper()} | " if score is not None else ""
    return with_deep_links({
        "title": _what(r)[:256], "description": desc, "color": _TIER_COLOR[t],
        "footer": {"text": f"{foot}{LEGEND}"},
    })


def signal_message(rows: list[dict], sport: str | None = None) -> tuple[str, list[dict]]:
    """(content, embeds) for one message in the daily signals thread: copyable slip lines + one embed per game."""
    content = slip_text((slip_line(r) for r in rows), limit=SLIP_LIMIT)
    by_game: dict = {}
    for r in sorted(rows, key=lambda r: -(r.get("ev") or 0)):
        by_game.setdefault(r.get("game_key") or "", []).append(r)
    embeds, shown = [], 0
    for gk, rs in list(by_game.items())[:9]:
        title = matchup(rs[0]) or str(gk) or "Props"
        extra = kickoff(rs[0])
        shown += min(len(rs), 8)
        embeds.append({"title": f"{title} | {len(rs)} signal{'s' if len(rs) != 1 else ''}"[:256],
                       "description": "\n".join(detail_line(r) for r in rs[:8])[:3800] + (f"\n{extra}" if extra else ""),
                       "color": 0x38BDF8})
    more = len(rows) - shown
    tail = {"title": f"{(sport or '').upper()} props signals".strip(), "description": f"{LEGEND}"
            + (f"\n+{more} more on the site" if more > 0 else ""), "color": 0x8B949E}
    embeds.append(with_deep_links(tail))
    return content, embeds


# -- delivery --------------------------------------------------------------------------------------------------------

def post_sport_alerts(sport: str, rows: list[dict]) -> bool:
    """Post already-gated rows (see gate()) to the sport's alerts channel. True only if something was delivered, so
    callers can leave undelivered rows un-notified and retry on the next pass. Never raises.

    Bot path: one message per alert (best score first, at most MAX_ALERTS) with plain slip text as content and a thread where
    GamblyBot and Playbook are @mentioned. Webhook path: one message, the slip text plus the same embeds (no bot tagging is
    possible from a webhook)."""
    if not rows:
        return False
    shown = rows[:MAX_ALERTS]
    embeds = [alert_embed(r) for r in shown]
    if bot_configured():
        posted = 0
        for r, e in zip(shown, embeds):
            slip = slip_text([slip_line(r)], limit=SLIP_LIMIT)
            if post_via_bot("card", slip, [e], sport=sport, thread={"name": slip[:100], "content": slip}):
                posted += 1
        if posted:
            if len(rows) > len(shown):
                log.info("%s alerts: %d highlighted, %d more only in the daily thread", sport, posted, len(rows) - len(shown))
            return True
    url = webhook_for("card", sport=sport)
    if not url:
        log.warning("%s alerts: no bot and no card webhook configured; %d row(s) not posted", sport, len(rows))
        return False
    try:
        import requests
        body = {"content": slip_text((slip_line(r) for r in shown), limit=SLIP_LIMIT), "embeds": embeds}
        return requests.post(url, json=body, timeout=20).status_code < 300
    except Exception as e:  # noqa: BLE001 -- a Discord outage must not break the sweep
        log.warning("%s alerts webhook failed: %r", sport, e)
        return False
