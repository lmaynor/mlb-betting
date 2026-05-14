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
    "OUTS": 0xE67E22,   # orange
}

_DEFAULT_COLOR = 0x99AAB5  # grey

# Abbrev -> sportsbook-canonical nickname (the medium form DK/SGO use).
TEAM_NICKNAME = {
    "ARI": "Diamondbacks", "ATL": "Braves",     "BAL": "Orioles",
    "BOS": "Red Sox",      "CHC": "Cubs",       "CWS": "White Sox",
    "CIN": "Reds",         "CLE": "Guardians",  "COL": "Rockies",
    "DET": "Tigers",       "HOU": "Astros",     "KC":  "Royals",
    "LAA": "Angels",       "LAD": "Dodgers",    "MIA": "Marlins",
    "MIL": "Brewers",      "MIN": "Twins",      "NYM": "Mets",
    "NYY": "Yankees",      "OAK": "Athletics",  "PHI": "Phillies",
    "PIT": "Pirates",      "SD":  "Padres",     "SF":  "Giants",
    "SEA": "Mariners",     "STL": "Cardinals",  "TB":  "Rays",
    "TEX": "Rangers",      "TOR": "Blue Jays",  "WSH": "Nationals",
}


def _edge_emoji(edge: Optional[float]) -> str:
    """Return an emoji reflecting edge strength."""
    if edge is None:
        return "📄"
    if edge >= 0.15:
        return "☢️"
    if edge >= 0.10:
        return "🔥"
    if edge >= 0.06:
        return "⚡"
    if edge >= 0.04:
        return "✅"
    return "📄"


def _round_stake(stake: Optional[float]) -> Optional[float]:
    """Round stake to nearest $5."""
    if stake is None:
        return None
    return round(stake / 5) * 5


def _format_bet_headline(b: dict, system: str) -> str:
    """Return the canonical sportsbook-style headline for one bet row."""
    away = b.get("away_team", "")
    home = b.get("home_team", "")
    away_full = TEAM_NICKNAME.get(away, away)
    home_full = TEAM_NICKNAME.get(home, home)

    sys = (system or "").upper()
    bt  = (b.get("bet_type") or "").upper()

    if sys == "NRFI":
        side = "Under 0.5 Runs" if "NRFI" in bt else "Over 0.5 Runs"
        return f"{away_full} @ {home_full} - 1st Inning - {side}"

    if sys == "F5":
        side = (b.get("side") or "").upper()
        if side == "HOME":
            team_full = home_full
        elif side == "AWAY":
            team_full = away_full
        else:
            tokens = bt.replace("_", " ").split()
            team_abbr = next((t for t in tokens if t in TEAM_NICKNAME), None)
            team_full = TEAM_NICKNAME.get(team_abbr, b.get("player") or f"{away_full}/{home_full}")
        return f"{team_full} - 1st 5 Innings Moneyline"

    if sys == "HR":
        player = b.get("player", "")
        team   = b.get("team") or b.get("batter_team") or ""
        team_tag = f" ({team})" if team else ""
        return f"{player}{team_tag} - To Hit A Home Run"

    if sys in ("K", "OUTS"):
        player = b.get("player", "")
        team   = b.get("team") or ""
        team_tag = f" ({team})" if team else ""
        line   = b.get("line")
        side   = (b.get("side") or "").upper()
        side_word = "Over" if side == "OVER" else ("Under" if side == "UNDER" else side)
        line_str  = f" {line}" if line is not None else ""
        market = "Outs Recorded" if sys == "OUTS" or bt.startswith("OUTS_") else "Strikeouts"
        return f"{player}{team_tag} - {side_word}{line_str} {market}"

    # Fallback
    matchup = f"{b.get('player','')} - {away} @ {home}".strip(" -")
    return f"{bt} - {matchup}"


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
    """Post today's bet signals to Discord."""
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
        model_prob = b.get("model_prob")
        edge       = b.get("edge")
        odds       = b.get("odds")
        stake      = _round_stake(b.get("stake"))
        paper      = b.get("paper", True)

        prob_str  = f"{model_prob:.1%}" if model_prob is not None else "N/A"
        edge_str  = f"{edge:+.1%}"      if edge       is not None else "N/A"
        stake_str = f"${stake:.0f}"     if stake is not None else "N/A"
        emoji     = _edge_emoji(edge)
        paper_tag = " 📄" if paper else " 💵"

        headline = _format_bet_headline(b, system)
        fields.append({
            "name":   f"{emoji} {headline}{paper_tag}",
            "value":  f"prob: **{prob_str}** | edge: **{edge_str}** | odds: **{_odds_str(odds)}** | stake: **{stake_str}**",
            "inline": False,
        })

    embed = {
        "title":       f"{system} Bets | {run_date}",
        "description": f"**{len(bets)}** bet{'s' if len(bets) != 1 else ''} found",
        "color":       color,
        "fields":      fields[:25],
        "footer":      {"text": "mlb-betting | paper mode" if bets[0].get("paper") else "mlb-betting | LIVE"},
    }

    _post(webhook_url, {"embeds": [embed]})


def post_summary(stats: dict, system: str, run_date: str = None) -> None:
    """Post a performance summary embed."""
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


def post_all_systems_summary(
    systems_stats: dict,
    run_date: str = None,
    settle_date: str = None,
) -> None:
    """
    Post a cross-system P&L summary embed after settlement.

    Args:
        systems_stats: Dict keyed by system name. Values are summary dicts
                       (pnl, roi, bets, wins, hit_rate, avg_edge, pending)
                       or None if a system has no settled bets yet.
        run_date:      Date string. settle_date is accepted as an alias.
    """
    webhook_url = (
        os.getenv("DISCORD_WEBHOOK_SUMMARY")
        or os.getenv("DISCORD_WEBHOOK_URL")
    )
    if not webhook_url:
        logger.warning("No Discord webhook configured for summary.")
        return

    run_date = run_date or settle_date or date.today().isoformat()

    _SYSTEM_ICONS = {"HR": "🔴", "NRFI": "🔵", "F5": "🟢", "K": "🟡"}

    # Guard: skip None entries before summing
    valid_stats = {k: v for k, v in systems_stats.items() if v}
    total_pnl = sum(s.get("pnl", 0) for s in valid_stats.values())
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"

    lines = []
    for system, stats in systems_stats.items():
        if not stats:
            continue
        icon    = _SYSTEM_ICONS.get(system.upper(), "⚪")
        wins    = stats.get("wins", 0)
        bets    = stats.get("bets", 0)
        hit     = stats.get("hit_rate", 0)
        pnl     = stats.get("pnl", 0)
        roi     = stats.get("roi", 0)
        edge    = stats.get("avg_edge")
        pending = stats.get("pending", 0)

        edge_str    = f"{edge:+.1%}" if edge is not None else "N/A"
        pending_str = f" | {pending} pending" if pending else ""
        lines.append(
            f"{icon} **{system:<4}** {wins}/{bets} ({hit:.0%})  "
            f"P&L: ${pnl:+.2f}  ROI: {roi:+.1f}%  edge: {edge_str}{pending_str}"
        )

    description = (
        f"Combined paper P&L: **${total_pnl:+.2f}**\n\n" + "\n".join(lines)
    ) if lines else "No settled bets yet."

    embed = {
        "title":       f"{pnl_emoji} 2026 Season Summary | {run_date}",
        "description": description,
        "color":       0x57F287 if total_pnl >= 0 else 0xED4245,
        "footer":      {"text": "mlb-betting | paper mode"},
    }
    _post(webhook_url, {"embeds": [embed]})
