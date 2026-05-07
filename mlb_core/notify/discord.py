"""
mlb_core.notify.discord — Discord webhook publisher.

Usage:
    from mlb_core.notify.discord import post_bets, post_summary

    post_bets(bets_df, system="NRFI", date="2026-04-22")
    post_summary(stats, system="NRFI")

Webhook URL is read from the DISCORD_WEBHOOK_URL environment variable
(or DISCORD_WEBHOOK_<SYSTEM> for a per-system override, e.g.
DISCORD_WEBHOOK_NRFI).  If no webhook is configured, calls are no-ops
and a warning is logged.
"""
import os
import logging
from datetime import date
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# Colour codes per system for the embed sidebar
_SYSTEM_COLORS = {
    "NRFI": 0x5865F2,   # blurple
    "HR":   0xED4245,   # red
    "F5":   0x57F287,   # green
    "K":    0xFEE75C,   # yellow
}

_DEFAULT_COLOR = 0x99AAB5  # grey


def _get_webhook(system: str) -> Optional[str]:
    """Return webhook URL for this system, or None if not configured."""
    url = (
        os.getenv(f"DISCORD_WEBHOOK_{system.upper()}")
        or os.getenv("DISCORD_WEBHOOK_URL")
    )
    if not url:
        logger.warning(
            f"No Discord webhook configured for {system}. "
            "Set DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_{system}."
        )
    return url


def _post(webhook_url: str, payload: dict) -> bool:
    """POST a Discord webhook payload. Returns True on success."""
    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Discord webhook failed: {e}")
        return False


def _odds_str(odds: Optional[int]) -> str:
    if odds is None:
        return "N/A"
    return f"+{odds}" if odds > 0 else str(odds)


def post_bets(
    bets: list[dict] | pd.DataFrame,
    system: str,
    run_date: str = None,
) -> None:
    """
    Post today's bet signals to Discord.

    Args:
        bets:      List of bet dicts or a DataFrame. Each row must have:
                   away_team, home_team, bet_type, model_prob, edge, odds, stake.
                   Optional: player, kelly_pct, paper.
        system:    System name e.g. "NRFI".
        run_date:  Date string e.g. "2026-04-22". Defaults to today.
    """
    webhook_url = _get_webhook(system)
    if not webhook_url:
        return

    run_date = run_date or date.today().isoformat()

    if isinstance(bets, pd.DataFrame):
        bets = bets.to_dict("records")

    if not bets:
        _post(webhook_url, {
            "embeds": [{
                "title": f"{system} | {run_date}",
                "description": "No qualifying bets today.",
                "color": _DEFAULT_COLOR,
            }]
        })
        return

    color = _SYSTEM_COLORS.get(system.upper(), _DEFAULT_COLOR)

    fields = []
    for b in bets:
        player     = b.get("player", "")
        away       = b.get("away_team", "")
        home       = b.get("home_team", "")
        matchup    = f"{player} — {away} @ {home}" if player else f"{away} @ {home}"
        bet_type   = b.get("bet_type", "")
        model_prob = b.get("model_prob")
        edge       = b.get("edge")
        odds       = b.get("odds")
        stake      = b.get("stake")
        paper      = b.get("paper", True)

        prob_str  = f"{model_prob:.1%}" if model_prob is not None else "N/A"
        edge_str  = f"{edge:+.1%}"      if edge       is not None else "N/A"
        stake_str = f"${stake:.2f}"     if stake      is not None else "N/A"
        paper_tag = " 📄" if paper else " 💵"

        fields.append({
            "name":   f"{bet_type} — {matchup}{paper_tag}",
            "value":  f"prob: **{prob_str}** | edge: **{edge_str}** | odds: **{_odds_str(odds)}** | stake: **{stake_str}**",
            "inline": False,
        })

    embed = {
        "title":       f"{system} Bets | {run_date}",
        "description": f"**{len(bets)}** bet{'s' if len(bets) != 1 else ''} found",
        "color":       color,
        "fields":      fields[:25],   # Discord cap
        "footer":      {"text": "mlb-betting | paper mode" if bets[0].get("paper") else "mlb-betting | LIVE"},
    }

    _post(webhook_url, {"embeds": [embed]})


def post_summary(stats: dict, system: str, run_date: str = None) -> None:
    """
    Post a performance summary embed.

    Args:
        stats:     Dict returned by BetTracker.summary().
        system:    System name.
        run_date:  Date string. Defaults to today.
    """
    webhook_url = _get_webhook(system)
    if not webhook_url:
        return

    run_date = run_date or date.today().isoformat()

    if not stats:
        return

    color   = _SYSTEM_COLORS.get(system.upper(), _DEFAULT_COLOR)
    pnl     = stats.get("pnl", 0)
    roi     = stats.get("roi", 0)
    bets    = stats.get("bets", 0)
    wins    = stats.get("wins", 0)
    hit     = stats.get("hit_rate", 0)
    edge    = stats.get("avg_edge")

    pnl_emoji = "📈" if pnl >= 0 else "📉"
    edge_str  = f"{edge:+.1%}" if edge is not None else "N/A"

    embed = {
        "title":  f"{system} Summary | {run_date}",
        "color":  color,
        "fields": [
            {"name": "Record",   "value": f"{wins}/{bets} ({hit:.1%})", "inline": True},
            {"name": "P&L",      "value": f"{pnl_emoji} ${pnl:+.2f}",   "inline": True},
            {"name": "ROI",      "value": f"{roi:+.1f}%",               "inline": True},
            {"name": "Avg edge", "value": edge_str,                      "inline": True},
        ],
    }
    _post(webhook_url, {"embeds": [embed]})


def post_error(system: str, message: str, run_date: str = None) -> None:
    """Post an error alert to Discord."""
    webhook_url = _get_webhook(system)
    if not webhook_url:
        return
    run_date = run_date or date.today().isoformat()
    _post(webhook_url, {
        "embeds": [{
            "title":       f"⚠️ {system} Error | {run_date}",
            "description": message[:2000],
            "color":       0xED4245,
        }]
    })
