"""
mlb_core.notify.discord — Discord webhook publisher.
"""
import os
import logging
from datetime import date
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

_SYSTEM_COLORS = {
    "NRFI": 0x5865F2,
    "HR":   0xED4245,
    "F5":   0x57F287,
    "K":    0xFEE75C,
}
_DEFAULT_COLOR = 0x99AAB5

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
    """Return canonical sportsbook-style headline for one bet row.

    Handles all side values:
      NRFI system:
        NRFI / YRFI          — existing O/U bets
        1I_AWAY              — away team scores 1st inning, home doesn't
        1I_HOME              — home team scores 1st inning, away doesn't
        1I_DRAW              — neither team scores 1st inning (3-way draw)
      F5 system:
        HOME / AWAY          — F5 moneyline
      HR system:
        (player + team)      — To Hit A Home Run
      K system:
        OVER / UNDER         — strikeout O/U
        OUTS_OVER/UNDER      — outs recorded O/U (bet_type starts with OUTS_)
    """
    away = b.get("away_team", "")
    home = b.get("home_team", "")
    away_full = TEAM_NICKNAME.get(away, away)
    home_full = TEAM_NICKNAME.get(home, home)

    sys = (system or "").upper()
    bt  = (b.get("bet_type") or "").upper()
    side = (b.get("side") or "").upper()

    if sys == "NRFI":
        # 3-way first-inning ML
        if side == "1I_AWAY":
            return f"{away_full} @ {home_full} - 1st Inning - {away_full} Score"
        if side == "1I_HOME":
            return f"{away_full} @ {home_full} - 1st Inning - {home_full} Score"
        if side == "1I_DRAW":
            return f"{away_full} @ {home_full} - 1st Inning - Neither Scores"
        # Standard NRFI/YRFI O/U
        ou_side = "Under 0.5 Runs" if "NRFI" in (side or bt) else "Over 0.5 Runs"
        return f"{away_full} @ {home_full} - 1st Inning - {ou_side}"

    if sys == "F5":
        if side == "HOME":
            team_full = home_full
        elif side == "AWAY":
            team_full = away_full
        else:
            tokens = bt.replace("_", " ").split()
            team_abbr = next((t for t in tokens if t in TEAM_NICKNAME), None)
            team_full = TEAM_NICKNAME.get(team_abbr,
                        b.get("player") or f"{away_full}/{home_full}")
        return f"{team_full} - 1st 5 Innings Moneyline"

    if sys == "HR":
        player   = b.get("player", "")
        team     = b.get("team") or b.get("batter_team") or ""
        team_tag = f" ({team})" if team else ""
        return f"{player}{team_tag} - To Hit A Home Run"

    if sys == "K":
        player   = b.get("player", "")
        team     = b.get("team") or ""
        team_tag = f" ({team})" if team else ""
        line     = b.get("line")
        line_str = f" {line}" if line is not None else ""

        # Outs recorded market (bet_type starts with OUTS_)
        if bt.startswith("OUTS_"):
            side_word = "Over" if side == "OVER" else ("Under" if side == "UNDER" else side)
            return f"{player}{team_tag} - {side_word}{line_str} Outs Recorded"

        # Standard strikeout O/U
        side_word = "Over" if side == "OVER" else ("Under" if side == "UNDER" else side)
        return f"{player}{team_tag} - {side_word}{line_str} Strikeouts"

    # Fallback
    matchup = f"{b.get('player','')} - {away} @ {home}".strip(" -")
    return f"{bt} - {matchup}"


def _get_webhook(system: str) -> Optional[str]:
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
    webhook_url = _get_webhook(system)
    if not webhook_url:
        return

    run_date = run_date or date.today().isoformat()

    if isinstance(bets, pd.DataFrame):
        bets = bets.to_dict("records")

    if not bets:
        _post(webhook_url, {
            "embeds": [{
                "title":       f"{system} | {run_date}",
                "description": "No qualifying bets today.",
                "color":       _DEFAULT_COLOR,
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
        "fields":      fields[:25],
        "footer":      {"text": "mlb-betting | paper mode" if bets[0].get("paper") else "mlb-betting | LIVE"},
    }
    _post(webhook_url, {"embeds": [embed]})


def post_summary(stats: dict, system: str, run_date: str = None) -> None:
    webhook_url = _get_webhook(system)
    if not webhook_url:
        return

    run_date = run_date or date.today().isoformat()
    if not stats:
        return

    color     = _SYSTEM_COLORS.get(system.upper(), _DEFAULT_COLOR)
    pnl       = stats.get("pnl", 0)
    roi       = stats.get("roi", 0)
    bets      = stats.get("bets", 0)
    wins      = stats.get("wins", 0)
    hit       = stats.get("hit_rate", 0)
    edge      = stats.get("avg_edge")
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    edge_str  = f"{edge:+.1%}" if edge is not None else "N/A"

    embed = {
        "title":  f"{system} Summary | {run_date}",
        "color":  color,
        "fields": [
            {"name": "Record",   "value": f"{wins}/{bets} ({hit:.1%})", "inline": True},
            {"name": "P&L",      "value": f"{pnl_emoji} ${pnl:+.2f}",  "inline": True},
            {"name": "ROI",      "value": f"{roi:+.1f}%",              "inline": True},
            {"name": "Avg edge", "value": edge_str,                     "inline": True},
        ],
    }
    _post(webhook_url, {"embeds": [embed]})


def post_all_systems_summary(
    system_stats: dict,
    settle_date: str = None,
) -> None:
    """Post a cross-system profitability summary to Discord.

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
        color_dot = {"HR": "🔴", "NRFI": "🔵", "F5": "🟢", "K": "🟡"}.get(system, "⚪")

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
