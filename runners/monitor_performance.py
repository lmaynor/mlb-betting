"""
runners/monitor_performance.py — Rolling performance monitor.

Fires after the nightly settle job. Reads the bets table, computes
rolling metrics per system over the last N settled bets, and posts a
Discord alert if any system shows signs of degradation.

Alert thresholds (all configurable via MONITOR_* env vars):
  ROI over last 30 settled bets < -15%   → warning
  Hit rate over last 30 < expected - 10% → warning
  Edge retention < 0.0 (avg edge - avg profit/stake) → warning
  Fewer than MIN_BETS settled → "not enough data" (no alert)

Also posts a weekly digest every Monday with full season stats.

Called by main.py /monitor. Scheduled at 09:30 UTC daily (30 min after
settle) via the mlb-monitor Cloud Scheduler job.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# Alert thresholds — override via env vars
ROI_WARN_THRESHOLD    = float(os.getenv("MONITOR_ROI_WARN",    "-15"))   # %
HIT_RATE_DROP         = float(os.getenv("MONITOR_HIT_RATE_DROP", "10"))  # pct points below model avg
MIN_BETS_FOR_ALERT    = int(os.getenv("MONITOR_MIN_BETS",        "20"))  # min settled bets
ROLLING_WINDOW        = int(os.getenv("MONITOR_ROLLING_WINDOW",  "30"))  # bets

# Expected model hit rates (rough baselines for alert comparison)
# These are conservative — the models haven't been live long enough
# to establish true baselines. Update these after 200 bets per system.
EXPECTED_HIT_RATES = {
    "HR":   0.07,   # HR props are rare hits
    "NRFI": 0.55,   # roughly coin flip + edge
    "F5":   0.52,
    "K":    0.52,
    "OUTS": 0.52,
}


def _load_season_bets(season: str) -> pd.DataFrame:
    """Load all settled bets for the season from Postgres."""
    from mlb_core.tracking.bet_tracker import _make_engine
    from sqlalchemy import text

    engine = _make_engine(db_path="unused")
    with engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT * FROM bets
                WHERE game_date LIKE :y
                ORDER BY game_date ASC, id ASC
            """),
            conn, params={"y": f"{season}%"},
        )
    return df


def _rolling_stats(df: pd.DataFrame, window: int) -> dict:
    """Compute rolling stats over the last `window` settled bets."""
    resolved = df[df["result"].notna()].copy()
    if resolved.empty:
        return {}

    recent = resolved.tail(window)
    wins   = (recent["result"] == "win").sum()
    n      = len(recent)
    staked = recent["stake"].sum()
    pnl    = recent["profit"].sum()
    roi    = pnl / staked * 100 if staked > 0 else 0.0

    return {
        "n":         n,
        "wins":      int(wins),
        "hit_rate":  wins / n if n > 0 else 0.0,
        "pnl":       round(pnl, 2),
        "roi":       round(roi, 2),
        "avg_edge":  round(float(recent["edge"].mean()), 4),
        "pending":   int(df[df["result"].isna()].shape[0]),
    }


def _season_stats(df: pd.DataFrame) -> dict:
    """Full season stats."""
    resolved = df[df["result"].notna()].copy()
    if resolved.empty:
        return {}
    wins   = (resolved["result"] == "win").sum()
    n      = len(resolved)
    staked = resolved["stake"].sum()
    pnl    = resolved["profit"].sum()
    return {
        "n":        n,
        "wins":     int(wins),
        "hit_rate": wins / n if n > 0 else 0.0,
        "pnl":      round(pnl, 2),
        "roi":      round(pnl / staked * 100 if staked > 0 else 0.0, 2),
        "avg_edge": round(float(resolved["edge"].mean()), 4),
        "pending":  int(df[df["result"].isna()].shape[0]),
    }


def _check_alerts(system: str, stats: dict) -> list[str]:
    """Return list of alert messages for this system. Empty = healthy."""
    alerts = []
    n = stats.get("n", 0)
    if n < MIN_BETS_FOR_ALERT:
        return []  # not enough data

    roi      = stats.get("roi", 0)
    hit_rate = stats.get("hit_rate", 0)
    expected = EXPECTED_HIT_RATES.get(system, 0.52)

    if roi < ROI_WARN_THRESHOLD:
        alerts.append(
            f"ROI over last {n} bets: **{roi:+.1f}%** "
            f"(threshold: {ROI_WARN_THRESHOLD:+.0f}%)"
        )
    if hit_rate < expected - (HIT_RATE_DROP / 100):
        alerts.append(
            f"Hit rate over last {n} bets: **{hit_rate:.1%}** "
            f"(expected ≥ {expected - HIT_RATE_DROP/100:.1%})"
        )
    return alerts


def _post_alert(system: str, alerts: list[str], stats: dict, run_date: str) -> None:
    """Post a degradation alert to Discord."""
    from mlb_core.notify.discord import _get_webhook, _post

    webhook_url = _get_webhook("SUMMARY") or _get_webhook("HR")
    if not webhook_url:
        return

    color_dot = {"HR": "🔴", "NRFI": "🔵", "F5": "🟢", "K": "🟡", "OUTS": "🟠"}.get(system, "⚪")
    alert_text = "\n".join(f"• {a}" for a in alerts)

    embed = {
        "title":       f"⚠️ {color_dot} {system} Performance Alert | {run_date}",
        "description": f"Degradation detected in last {ROLLING_WINDOW} bets:\n{alert_text}",
        "color":       0xED4245,
        "fields": [
            {"name": "Record",   "value": f"{stats['wins']}/{stats['n']} ({stats['hit_rate']:.0%})", "inline": True},
            {"name": "P&L",      "value": f"${stats['pnl']:+.2f}", "inline": True},
            {"name": "ROI",      "value": f"{stats['roi']:+.1f}%", "inline": True},
            {"name": "Avg edge", "value": f"{stats['avg_edge']:+.1%}", "inline": True},
        ],
        "footer": {"text": f"Last {ROLLING_WINDOW} settled bets | mlb-betting monitor"},
    }
    _post(webhook_url, {"embeds": [embed]})


def _post_weekly_digest(system_stats: dict, run_date: str) -> None:
    """Post full season summary every Monday."""
    from mlb_core.notify.discord import _get_webhook, _post

    webhook_url = _get_webhook("SUMMARY") or _get_webhook("HR")
    if not webhook_url:
        return

    fields = []
    total_pnl = 0.0
    for system in ["HR", "NRFI", "F5", "K", "OUTS"]:
        stats = system_stats.get(system)
        dot = {"HR": "🔴", "NRFI": "🔵", "F5": "🟢", "K": "🟡", "OUTS": "🟠"}.get(system, "⚪")
        if not stats or stats.get("n", 0) == 0:
            fields.append({"name": f"{dot} {system}", "value": "_no data_", "inline": False})
            continue
        total_pnl += stats["pnl"]
        fields.append({
            "name":  f"{dot} {system}",
            "value": (f"`{stats['wins']}/{stats['n']} ({stats['hit_rate']:.0%})` "
                      f"P&L: **${stats['pnl']:+.2f}** | ROI: **{stats['roi']:+.1f}%** | "
                      f"edge: {stats['avg_edge']:+.1%} | {stats['pending']} pending"),
            "inline": False,
        })

    emoji = "📈" if total_pnl >= 0 else "📉"
    embed = {
        "title":       f"{emoji} Weekly Digest | {run_date}",
        "description": f"Season combined P&L: **${total_pnl:+.2f}**",
        "color":       0x57F287 if total_pnl >= 0 else 0xED4245,
        "fields":      fields,
        "footer":      {"text": "mlb-betting | weekly digest | paper mode"},
    }
    _post(webhook_url, {"embeds": [embed]})


def run(run_date: str = None) -> dict:
    """Check rolling performance for all systems. Post alerts if degraded.

    Also posts a weekly digest every Monday.
    """
    run_date = run_date or date.today().isoformat()
    season   = run_date[:4]
    is_monday = date.fromisoformat(run_date).weekday() == 0

    logger.info(f"monitor: starting for {run_date} | weekly={is_monday}")

    try:
        all_bets = _load_season_bets(season)
    except Exception as e:
        logger.error(f"monitor: failed to load bets: {e}")
        return {"status": "error", "error": str(e)}

    if all_bets.empty:
        logger.info("monitor: no bets found for season")
        return {"status": "ok", "run_date": run_date, "alerts": 0}

    results = {}
    total_alerts = 0
    weekly_stats = {}

    for system in ["HR", "NRFI", "F5", "K", "OUTS"]:
        sys_bets = all_bets[all_bets["system"] == system]
        if sys_bets.empty:
            results[system] = {"status": "no_data"}
            weekly_stats[system] = None
            continue

        rolling = _rolling_stats(sys_bets, ROLLING_WINDOW)
        season_s = _season_stats(sys_bets)
        weekly_stats[system] = season_s

        alerts = _check_alerts(system, rolling)
        if alerts:
            logger.warning(f"monitor: {system} ALERT — {alerts}")
            _post_alert(system, alerts, rolling, run_date)
            total_alerts += len(alerts)
        else:
            logger.info(f"monitor: {system} healthy | "
                        f"n={rolling.get('n',0)} roi={rolling.get('roi',0):+.1f}%")

        results[system] = {
            "rolling": rolling,
            "season":  season_s,
            "alerts":  alerts,
        }

    if is_monday:
        logger.info("monitor: posting weekly digest")
        _post_weekly_digest(weekly_stats, run_date)

    return {
        "status":       "ok",
        "run_date":     run_date,
        "alerts":       total_alerts,
        "systems":      results,
        "weekly_digest": is_monday,
    }
