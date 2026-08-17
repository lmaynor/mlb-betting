"""
mlb_core.notify.discord — Discord webhook publisher.

Usage:
    from mlb_core.notify.discord import post_bets, post_summary

    post_bets(bets_df, system="1IOU", date="2026-04-22")
    post_summary(stats, system="1IOU")

Webhook URLs read from environment variables:
    DISCORD_WEBHOOK_URL         — default picks webhook (#daily-picks)
    DISCORD_WEBHOOK_SUMMARY     — recap webhook (#daily-recap)
    DISCORD_WEBHOOK_OPS         — ops/error webhook (#ops-alerts)
    DISCORD_WEBHOOK_PERFORMANCE — perf webhook (#performance); read directly by
                                  monitor_performance.py / weekly_survival_report.py
    DISCORD_WEBHOOK_ALERTS      — intraday +EV pager (mlb.runners.fast_alert_loop);
                                  falls back to DISCORD_WEBHOOK_URL if unset
    DISCORD_WEBHOOK_<SYSTEM>    — per-system override (e.g. DISCORD_WEBHOOK_NRFI)

If no webhook is configured, calls are no-ops and a warning is logged.
"""
import os
import logging
from datetime import date
from typing import Optional

import requests
import pandas as pd

from mlb_core.registry import SYSTEMS, CANONICAL_ORDER

logger = logging.getLogger(__name__)

# Colour codes per system for the embed sidebar
_SYSTEM_COLORS = {
    "1IOU":        0x5865F2,   # blurple
    "HR":          0xED4245,   # red
    "F5":          0x57F287,   # green
    "K":           0xFEE75C,   # yellow
    "OUTS":        0xE67E22,   # orange
    "F3":          0x1ABC9C,   # teal
    "F1H":         0x3498DB,   # sky blue
    "F7":          0x8E44AD,   # purple
    "GAME":        0x2C3E50,   # dark slate
    "BATTER_K":    0xF0B232,   # amber
    "BATTER_TB":   0x3BA55D,   # lime green
    "BATTER_HITS": 0x16A085,   # teal green
    "PITCHER_ER":  0x9B59B6,   # violet
}

_DEFAULT_COLOR = 0x99AAB5  # grey

# Abbrev -> sportsbook-canonical nickname
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

# Canonical book key (odds_history / bet_tracker / mlb_core.odds.sgo.ONSHORE_BOOKS)
# -> display name for Discord. Covers every onshore book plus the sharp/reference
# books used as fair-value anchors (pinnacle) and BettingPros-only softs that
# aren't in SGO (fliff, sugarhouse, partycasino). An unmapped book falls back to
# Title Case in book_display() rather than showing up raw/lowercase.
BOOK_DISPLAY = {
    "draftkings":  "DraftKings",
    "fanduel":     "FanDuel",
    "caesars":     "Caesars",
    "betmgm":      "BetMGM",
    "espnbet":     "theScore",   # rebranded; BOOK_CANONICAL maps this upstream too
    "thescore":    "theScore",
    "pointsbet":   "PointsBet",
    "bet365":      "Bet365",
    "betrivers":   "BetRivers",
    "fanatics":    "Fanatics",
    "hardrock":    "Hard Rock Bet",
    "novig":       "Novig",
    "parx":        "Parx",
    "underdog":    "Underdog",
    "fliff":       "Fliff",
    "sugarhouse":  "SugarHouse",
    "partycasino": "PartyCasino",
    "pinnacle":    "Pinnacle",
    "consensus":   "Consensus",
}


def book_display(book: Optional[str]) -> str:
    """Canonical book key -> display name. Falls back to Title Case for an
    unmapped/unknown book instead of leaving it raw (e.g. "hardrock")."""
    if not book:
        return ""
    return BOOK_DISPLAY.get(book.lower(), book.title())


def _edge_emoji(edge: Optional[float]) -> str:
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


# odds_history canonical market code (see mlb/analysis/*_to_history.py producers
# and mlb/analysis/outlier_scan.py) -> human noun phrase for alert headlines.
MARKET_LABELS = {
    "hr_yn":      "Home Run",
    "k_ou":       "Strikeouts",
    "outs_ou":    "Outs Recorded",
    "btb_ou":     "Total Bases",
    "bhits_ou":   "Hits",
    "per_ou":     "Earned Runs",
    "phits_ou":   "Hits Allowed",
    "pwalks_ou":  "Walks Allowed",
    "nrfi_ou":    "1st Inning Runs",
    "game_ml":    "Moneyline",
    "f5_ml":      "1st 5 Innings Moneyline",
    "game_rl":    "Run Line",
    "game_total": "Total Runs",
    "runs_ou":    "Runs",
}


def market_label(market: str) -> str:
    """odds_history market code -> human noun phrase, e.g. "k_ou" -> "Strikeouts"."""
    return MARKET_LABELS.get(market, (market or "").replace("_", " ").title())


def ev_alert_emoji(ev: float) -> str:
    """Tier a soft-line +EV scan alert (mlb.runners.fast_alert_loop) by EV
    magnitude. Alerts are pre-filtered to ev >= FAL_MIN_EV before this is ever
    called, so unlike _edge_emoji there is no "no signal" floor case -- the
    lowest tier is still a qualifying "yes, strike this" checkmark."""
    if ev >= 0.15:
        return "☢️"
    if ev >= 0.10:
        return "🔥"
    if ev >= 0.06:
        return "⚡"
    return "✅"


def _round_stake(stake: Optional[float]) -> Optional[float]:
    if stake is None:
        return None
    if stake < 10:
        return round(stake)
    return round(stake / 5) * 5


def _format_bet_headline(b: dict, system: str) -> str:
    away = b.get("away_team", "")
    home = b.get("home_team", "")
    away_full = TEAM_NICKNAME.get(away, away)
    home_full = TEAM_NICKNAME.get(home, home)

    sys = (system or "").upper()
    bt  = (b.get("bet_type") or "").upper()

    if sys == "1IOU":
        side = "Under 0.5 Runs" if "NRFI" in bt or (b.get("side") or "").upper() == "NRFI" else "Over 0.5 Runs"
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
        away_n = TEAM_NICKNAME.get(away, away)
        home_n = TEAM_NICKNAME.get(home, home)
        matchup = f"{away_n} @ {home_n}" if away_n and home_n else ""
        matchup_tag = f" - {matchup}" if matchup else ""
        return f"{matchup_tag} - {player} - To Hit A Home Run".lstrip(" -")

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

    if sys in ("F3", "F1H", "F7", "GAME"):
        _labels = {"F3": "First 3 Innings", "F1H": "First Half",
                   "F7": "First 7 Innings", "GAME": "Full Game"}
        label = _labels.get(sys, sys)
        parts = bt.rsplit("_", 1)
        side  = parts[1] if len(parts) == 2 else ""
        if side == "HOME":
            team_full = home_full
        elif side == "AWAY":
            team_full = away_full
        else:
            team_full = f"{away_full}/{home_full}"
        return f"{team_full} - {label} Moneyline"

    if sys == "BATTER_K":
        player = b.get("player", "")
        line   = b.get("line")
        side   = (b.get("side") or "").upper()
        side_word = "Over" if side == "OVER" else "Under"
        line_str  = f" {line}" if line is not None else ""
        return f"{player} - {side_word}{line_str} Strikeouts (Batter)"

    if sys == "BATTER_TB":
        player = b.get("player", "")
        line   = b.get("line")
        side   = (b.get("side") or "").upper()
        side_word = "Over" if side == "OVER" else "Under"
        line_str  = f" {line}" if line is not None else ""
        return f"{player} - {side_word}{line_str} Total Bases"

    if sys == "BATTER_HITS":
        player = b.get("player", "")
        line   = b.get("line")
        side   = (b.get("side") or "").upper()
        side_word = "Over" if side == "OVER" else "Under"
        line_str  = f" {line}" if line is not None else ""
        return f"{player} - {side_word}{line_str} Hits"

    if sys == "PITCHER_ER":
        player = b.get("player", "")
        line   = b.get("line")
        side   = (b.get("side") or "").upper()
        side_word = "Over" if side == "OVER" else "Under"
        line_str  = f" {line}" if line is not None else ""
        return f"{player} - {side_word}{line_str} Earned Runs"

    matchup = f"{b.get('player','')} - {away} @ {home}".strip(" -")
    return f"{bt} - {matchup}"


def _get_webhook(system: str) -> Optional[str]:
    """Return picks webhook URL for this system."""
    url = (
        os.getenv(f"DISCORD_WEBHOOK_{system.upper()}")
        or os.getenv("DISCORD_WEBHOOK_URL")
    )
    if not url:
        logger.warning(
            f"No Discord webhook configured for {system}. "
            f"Set DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_{system.upper()}."
        )
    return url


def _get_ops_webhook() -> Optional[str]:
    """Return ops webhook URL (#ops-alerts). Falls back to main webhook."""
    url = (
        os.getenv("DISCORD_WEBHOOK_OPS")
        or os.getenv("DISCORD_WEBHOOK_URL")
    )
    if not url:
        logger.warning("No Discord ops webhook configured. Set DISCORD_WEBHOOK_OPS.")
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
    """Post today's bet signals to Discord (#daily-picks)."""
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

        prob_str  = f"{model_prob:.1%}" if model_prob is not None else "N/A"
        edge_str  = f"{edge:+.1%}"      if edge       is not None else "N/A"
        stake_str = f"${stake:.0f}"     if stake is not None else "N/A"
        emoji     = _edge_emoji(edge)
        headline  = _format_bet_headline(b, system)
        _book     = book_display(b.get("book") or b.get("bookmaker") or "")
        book_tag  = f" · {_book}" if _book else ""
        fields.append({
            "name":   f"{emoji} {headline}",
            "value":  f"prob: **{prob_str}** | edge: **{edge_str}** | odds: **{_odds_str(odds)}**{book_tag} | stake: **{stake_str}**",
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
    """Post a performance summary embed (#daily-recap)."""
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
            {"name": "P&L",      "value": f"{pnl_emoji} ${pnl:+.2f}",  "inline": True},
            {"name": "ROI",      "value": f"{roi:+.1f}%",              "inline": True},
            {"name": "Avg edge", "value": edge_str,                     "inline": True},
        ],
    }
    _post(webhook_url, {"embeds": [embed]})


def post_error(system: str, message: str, run_date: str = None) -> None:
    """Post an error alert to #ops-alerts (not member-facing channels)."""
    webhook_url = _get_ops_webhook()  # <-- now routes to ops channel
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


def post_ops_alert(message: str, run_date: str = None) -> None:
    """Post a generic ops alert to #ops-alerts. Use for monitor_ops.py failures."""
    webhook_url = _get_ops_webhook()
    if not webhook_url:
        return
    run_date = run_date or date.today().isoformat()
    _post(webhook_url, {
        "embeds": [{
            "title":       f"⚠️ Ops Alert | {run_date}",
            "description": message[:2000],
            "color":       0xED4245,
            "footer":      {"text": "monitor_ops"},
        }]
    })


def post_all_systems_summary(
    systems_stats: dict,
    run_date: str = None,
    settle_date: str = None,
) -> None:
    """Post a daily recap embed after settlement (#daily-recap)."""
    webhook_url = (
        os.getenv("DISCORD_WEBHOOK_SUMMARY")
        or os.getenv("DISCORD_WEBHOOK_URL")
    )
    if not webhook_url:
        logger.warning("No Discord webhook configured for summary.")
        return

    run_date = run_date or settle_date or date.today().isoformat()

    valid_stats = {k: v for k, v in systems_stats.items() if v}
    total_pnl   = sum(s.get("pnl", 0) for s in valid_stats.values())
    pnl_emoji   = "📈" if total_pnl >= 0 else "📉"

    fields = []
    for system in CANONICAL_ORDER:
        stats = systems_stats.get(system)
        icon  = SYSTEMS[system].icon if system in SYSTEMS else "⚪"
        if not stats:
            fields.append({
                "name":   f"{icon} {system}",
                "value":  "_No settled bets yet_",
                "inline": True,
            })
            continue

        wins    = stats.get("wins", 0)
        bets    = stats.get("bets", 0)
        hit     = stats.get("hit_rate", 0)
        pnl     = stats.get("pnl", 0)
        roi     = stats.get("roi", 0)
        edge    = stats.get("avg_edge")
        pending = stats.get("pending", 0)

        pnl_sign    = "📈" if pnl >= 0 else "📉"
        edge_str    = f"{edge:+.1%}" if edge is not None else "N/A"
        pending_str = f"\n⏳ {pending} pending" if pending else ""

        value = (
            f"**{wins}/{bets}** ({hit:.0%} hit rate)\n"
            f"{pnl_sign} **${pnl:+.2f}** | ROI {roi:+.1f}%\n"
            f"Avg edge {edge_str}{pending_str}"
        )
        fields.append({"name": f"{icon} {system}", "value": value, "inline": True})

    if len(fields) % 3 == 2:
        fields.append({"name": "\u200b", "value": "\u200b", "inline": True})

    settled_count = sum(s.get("bets", 0) for s in valid_stats.values())
    description   = (
        f"Combined paper P&L: **${total_pnl:+.2f}**  |  {settled_count} settled bets"
    ) if valid_stats else "No settled bets yet."

    embed = {
        "title":       f"{pnl_emoji} Daily Recap | {run_date}",
        "description": description,
        "color":       0x57F287 if total_pnl >= 0 else 0xED4245,
        "fields":      fields,
        "footer":      {"text": "mlb-betting | paper mode"},
    }
    _post(webhook_url, {"embeds": [embed]})
