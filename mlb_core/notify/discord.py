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

# Abbrev -> sportsbook-canonical nickname (the medium form DK/SGO use).
# Used by the per-system bet-headline formatter below.
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


def _format_bet_headline(b: dict, system: str) -> str:
    """Return the canonical sportsbook-style headline for one bet row.

    System-specific:
      NRFI -> "Yankees @ Guardians - 1st Inning - Under 0.5 Runs"
              (bet_type "NRFI" = Under 0.5; "YRFI" = Over 0.5)
      F5   -> "Dodgers - 1st 5 Innings Moneyline"
              (player field carries the F5 winner team abbrev from run_f5)
      HR   -> "Aaron Judge (NYY) - To Hit A Home Run"
      K    -> "Gerrit Cole (NYY) - Over 7.5 Strikeouts"

    Falls back to the prior generic format if the row doesn't have the
    fields the formatter expects.
    """
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
        # F5 runner puts the winning team abbrev in bet_type or player.
        # The bet_type usually reads e.g. "F5_HOME" / "F5_AWAY" or "COL F5".
        # We need to know WHICH team is the bet — derive from b["side"] if
        # present (the runner sets side="HOME"|"AWAY"), else parse bet_type.
        side = (b.get("side") or "").upper()
        if side == "HOME":
            team_full = home_full
        elif side == "AWAY":
            team_full = away_full
        else:
            # Best-effort parse of bet_type. If e.g. "COL F5" or "F5 COL",
            # pull the abbrev token and look it up.
            tokens = bt.replace("_", " ").split()
            team_abbr = next((t for t in tokens if t in TEAM_NICKNAME), None)
            team_full = TEAM_NICKNAME.get(team_abbr, b.get("player") or f"{away_full}/{home_full}")
        return f"{team_full} - 1st 5 Innings Moneyline"

    if sys == "HR":
        player = b.get("player", "")
        team   = b.get("team") or b.get("batter_team") or ""
        team   = team if team else ""
        team_tag = f" ({team})" if team else ""
        return f"{player}{team_tag} - To Hit A Home Run"

    if sys == "K":
        player = b.get("player", "")
        team   = b.get("team") or ""
        team_tag = f" ({team})" if team else ""
        line   = b.get("line")
        side   = (b.get("side") or "").upper()
        side_word = "Over" if side == "OVER" else ("Under" if side == "UNDER" else side)
        line_str  = f" {line}" if line is not None else ""
        return f"{player}{team_tag} - {side_word}{line_str} Strikeouts"

    # Fallback to prior generic format
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
        model_prob = b.get("model_prob")
        edge       = b.get("edge")
        odds       = b.get("odds")
        stake      = b.get("stake")
        paper      = b.get("paper", True)

        prob_str  = f"{model_prob:.1%}" if model_prob is not None else "N/A"
        edge_str  = f"{edge:+.1%}"      if edge       is not None else "N/A"
        stake_str = f"${stake:.2f}"     if stake      is not None else "N/A"
        paper_tag = " 📄" if paper else " 💵"

        headline = _format_bet_headline(b, system)
        fields.append({
            "name":   f"{headline}{paper_tag}",
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


def post_all_systems_summary(
    system_stats: dict,
    settle_date: str = None,
) -> None:
    """Post a cross-system profitability summary to Discord.

    system_stats: dict keyed by system name ("HR", "NRFI", "F5", "K").
    Each value is either None (no resolved bets yet) or a dict with:
        bets, wins, hit_rate, pnl, roi, avg_edge, pending

    Produces one embed with a field per system — a single daily digest
    showing how all four models are performing this season.

    Reads DISCORD_WEBHOOK_SUMMARY first, falls back to DISCORD_WEBHOOK_URL.
    """
    webhook_url = _get_webhook("SUMMARY") or _get_webhook("HR")
    if not webhook_url:
        return

    settle_date = settle_date or date.today().isoformat()
    season = settle_date[:4]

    fields = []
    total_pnl = 0.0
    for system in ["HR", "NRFI", "F5", "K"]:
        stats = system_stats.get(system)
        color_dot = {
            "HR": "🔴", "NRFI": "🔵", "F5": "🟢", "K": "🟡"
        }.get(system, "⚪")

        if not stats:
            fields.append({
                "name":   f"{color_dot} {system}",
                "value":  "_no settled bets yet_",
                "inline": False,
            })
            continue

        wins     = stats["wins"]
        bets     = stats["bets"]
        hit      = stats["hit_rate"]
        pnl      = stats["pnl"]
        roi      = stats["roi"]
        avg_edge = stats.get("avg_edge")
        pending  = stats.get("pending", 0)
        total_pnl += pnl

        pnl_str  = f"${pnl:+.2f}"
        roi_str  = f"{roi:+.1f}%"
        edge_str = f"{avg_edge:+.1%}" if avg_edge is not None else "N/A"
        rec_str  = f"{wins}/{bets} ({hit:.0%})"
        pend_str = f" | {pending} pending" if pending else ""

        fields.append({
            "name":   f"{color_dot} {system}",
            "value":  f"`{rec_str}` P&L: **{pnl_str}** | ROI: **{roi_str}** | edge: {edge_str}{pend_str}",
            "inline": False,
        })

    total_emoji = "📈" if total_pnl >= 0 else "📉"
    embed = {
        "title":       f"{total_emoji} {season} Season Summary | {settle_date}",
        "description": f"Combined paper P&L: **${total_pnl:+.2f}**",
        "color":       0x57F287 if total_pnl >= 0 else 0xED4245,
        "fields":      fields,
        "footer":      {"text": "mlb-betting | paper mode | all systems"},
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
