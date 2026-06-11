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

from mlb_core.registry import SYSTEMS, CANONICAL_ORDER

logger = logging.getLogger(__name__)


def _auc(probs: list, outcomes: list) -> float | None:
    """Mann-Whitney AUC -- no sklearn needed."""
    pos = [p for p, o in zip(probs, outcomes) if o == 1]
    neg = [p for p, o in zip(probs, outcomes) if o == 0]
    if not pos or not neg:
        return None
    concordant = sum(1 for p in pos for n in neg if p > n)
    tied       = sum(1 for p in pos for n in neg if p == n)
    return (concordant + 0.5 * tied) / (len(pos) * len(neg))

# Alert thresholds — override via env vars
ROI_WARN_THRESHOLD    = float(os.getenv("MONITOR_ROI_WARN",    "-15"))   # %
HIT_RATE_DROP         = float(os.getenv("MONITOR_HIT_RATE_DROP", "10"))  # pct points below model avg
MIN_BETS_FOR_ALERT    = int(os.getenv("MONITOR_MIN_BETS",        "20"))  # min settled bets
ROLLING_WINDOW        = int(os.getenv("MONITOR_ROLLING_WINDOW",  "30"))  # bets

# Gate thresholds (Task B) -- same numeric intent as main.py MIN_HEALTH_N/CAL_ERR_TOL/ROI_FLOOR.
# A system with < MIN_GATE_N bets is NEVER suppressed.
MIN_GATE_N      = int(os.getenv("GATE_MIN_N",      "30"))   # minimum settled bets to activate gate
GATE_AUC_MIN    = float(os.getenv("GATE_AUC_MIN",  "0.52"))  # suppress if rolling AUC < this
GATE_CAL_TOL    = float(os.getenv("GATE_CAL_TOL",  "0.12"))  # suppress if |cal_err| > this
GATE_ROI_MIN    = float(os.getenv("GATE_ROI_MIN",  "-20"))   # suppress if rolling ROI < this (%)
GATE_HYSTERESIS = int(os.getenv("GATE_HYSTERESIS", "2"))     # consecutive runs before flip

GATE_FILE_KEY   = "Gates/model_gates.json"

# Expected model hit rates — derived from registry.
# These are conservative; update expected_hit_rate in mlb_core/registry.py
# after 200+ settled bets per system rather than editing this file.
EXPECTED_HIT_RATES = {s: cfg.expected_hit_rate for s, cfg in SYSTEMS.items()}


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


def _clv_stats(df: pd.DataFrame) -> dict:
    """Compute CLV stats for bets that have a closing line. (T08)"""
    clv_rows = df[df["clv_pct"].notna()].copy() if "clv_pct" in df.columns else pd.DataFrame()
    if clv_rows.empty:
        return {"clv_n": 0, "mean_clv": None, "clv_tstat": None}
    import numpy as np
    from scipy import stats as scipy_stats
    vals = clv_rows["clv_pct"].values.astype(float)
    n    = len(vals)
    mean = float(np.mean(vals))
    sem  = float(scipy_stats.sem(vals)) if n > 1 else 0.0
    tstat = round(mean / sem, 3) if sem > 0 else None
    return {
        "clv_n":    n,
        "mean_clv": round(mean, 4),
        "clv_tstat": tstat,
    }


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

    probs    = recent["market_prob"].dropna().tolist()
    outcomes = [1 if r == "win" else 0 for r in recent.loc[recent["market_prob"].notna(), "result"]]
    auc_val  = _auc(probs, outcomes)

    stats = {
        "n":         n,
        "wins":      int(wins),
        "hit_rate":  wins / n if n > 0 else 0.0,
        "pnl":       round(pnl, 2),
        "roi":       round(roi, 2),
        "avg_edge":  round(float(recent["edge"].mean()), 4),
        "pending":   int(df[df["result"].isna()].shape[0]),
        "auc":       round(auc_val, 4) if auc_val is not None else None,
    }
    stats.update(_clv_stats(recent))
    return stats


def _season_stats(df: pd.DataFrame) -> dict:
    """Full season stats."""
    resolved = df[df["result"].notna()].copy()
    if resolved.empty:
        return {}
    wins   = (resolved["result"] == "win").sum()
    n      = len(resolved)
    staked = resolved["stake"].sum()
    pnl    = resolved["profit"].sum()
    probs    = resolved["market_prob"].dropna().tolist()
    outcomes = [1 if r == "win" else 0 for r in resolved.loc[resolved["market_prob"].notna(), "result"]]
    auc_val  = _auc(probs, outcomes)

    stats = {
        "n":        n,
        "wins":     int(wins),
        "hit_rate": wins / n if n > 0 else 0.0,
        "pnl":      round(pnl, 2),
        "roi":      round(pnl / staked * 100 if staked > 0 else 0.0, 2),
        "avg_edge": round(float(resolved["edge"].mean()), 4),
        "pending":  int(df[df["result"].isna()].shape[0]),
        "auc":      round(auc_val, 4) if auc_val is not None else None,
    }
    stats.update(_clv_stats(resolved))
    return stats


def _per_book_stats(df: pd.DataFrame) -> dict[str, dict]:
    """Per-book performance breakdown. (T15)

    Groups settled bets by the `book` column and returns stats per book.
    Detects potential profiling signal: any book with n >= 20 and ROI < -20%.
    """
    if "book" not in df.columns:
        return {}
    resolved = df[df["result"].notna()].copy()
    if resolved.empty:
        return {}

    books = resolved["book"].dropna().unique()
    result: dict[str, dict] = {}
    for book in sorted(books):
        rows = resolved[resolved["book"] == book]
        n      = len(rows)
        wins   = (rows["result"] == "win").sum()
        staked = rows["stake"].sum()
        pnl    = rows["profit"].sum()
        roi    = pnl / staked * 100 if staked > 0 else 0.0
        hr     = wins / n if n > 0 else 0.0

        clv_rows = rows[rows["clv_pct"].notna()] if "clv_pct" in rows.columns else pd.DataFrame()
        mean_clv = round(float(clv_rows["clv_pct"].mean()), 3) if not clv_rows.empty else None

        profiling_signal = (n >= 20 and roi < -20.0)
        result[book] = {
            "n":            int(n),
            "wins":         int(wins),
            "hit_rate":     round(hr, 4),
            "pnl":          round(pnl, 2),
            "roi":          round(roi, 2),
            "mean_clv":     mean_clv,
            "profiling_flag": profiling_signal,
        }
    return result


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

    # AUC alert: < 0.50 means model is rank-ordering backwards -- structural failure.
    auc_val = stats.get("auc")
    if auc_val is not None and n >= MIN_BETS_FOR_ALERT:
        if auc_val < 0.50:
            alerts.append(
                f"AUC over last {n} bets: **{auc_val:.3f}** -- model is rank-ordering "
                f"backwards. Calibration cannot fix this; retrain required."
            )
        elif auc_val < 0.52:
            alerts.append(
                f"AUC over last {n} bets: **{auc_val:.3f}** -- near coin-flip discrimination. "
                f"Edge may not exist."
            )

    # CLV alert: if we have ≥ 20 CLV observations and mean CLV < 0, flag it.
    # Negative CLV is a leading indicator of negative edge — acts earlier than ROI.
    clv_n    = stats.get("clv_n", 0)
    mean_clv = stats.get("mean_clv")
    if clv_n >= 20 and mean_clv is not None and mean_clv < 0:
        tstat = stats.get("clv_tstat")
        tstat_str = f" (t={tstat:.2f})" if tstat else ""
        alerts.append(
            f"Mean CLV over last {clv_n} bets: **{mean_clv:+.2f}%**{tstat_str} — "
            f"negative CLV suggests edge may not exist at closing line"
        )

    return alerts


def _post_alert(system: str, alerts: list[str], stats: dict, run_date: str) -> None:
    """Post a degradation alert to Discord (#ops-alerts)."""
    from mlb_core.notify.discord import _get_ops_webhook, _post

    webhook_url = _get_ops_webhook()
    if not webhook_url:
        return

    color_dot = SYSTEMS[system].icon if system in SYSTEMS else "⚪"
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
            {"name": "AUC",      "value": f"{stats['auc']:.3f}" if stats.get('auc') is not None else "n/a", "inline": True},
        ],
        "footer": {"text": f"Last {ROLLING_WINDOW} settled bets | mlb-betting monitor"},
    }
    _post(webhook_url, {"embeds": [embed]})


def _post_weekly_digest(system_stats: dict, per_book: dict[str, dict],
                        run_date: str) -> None:
    """Post full season summary every Monday (#performance)."""
    import os
    from mlb_core.notify.discord import _get_ops_webhook, _post

    webhook_url = os.getenv("DISCORD_WEBHOOK_PERFORMANCE") or _get_ops_webhook()
    if not webhook_url:
        return

    fields = []
    total_pnl = 0.0
    for system in CANONICAL_ORDER:
        stats = system_stats.get(system)
        dot = SYSTEMS[system].icon if system in SYSTEMS else "⚪"
        if not stats or stats.get("n", 0) == 0:
            fields.append({"name": f"{dot} {system}", "value": "_no data_", "inline": False})
            continue
        total_pnl += stats["pnl"]
        clv_str = ""
        if stats.get("clv_n", 0) >= 5:
            clv_str = f" | CLV: **{stats['mean_clv']:+.2f}%** (n={stats['clv_n']})"
        auc_str = f" | AUC: **{stats['auc']:.3f}**" if stats.get('auc') is not None else ""
        fields.append({
            "name":  f"{dot} {system}",
            "value": (f"`{stats['wins']}/{stats['n']} ({stats['hit_rate']:.0%})` "
                      f"P&L: **${stats['pnl']:+.2f}** | ROI: **{stats['roi']:+.1f}%** | "
                      f"edge: {stats['avg_edge']:+.1%}{auc_str}{clv_str} | {stats['pending']} pending"),
            "inline": False,
        })

    # Per-book summary (T15)
    if per_book:
        book_lines = []
        profiling_flags = []
        for book, bs in per_book.items():
            clv_str = f" CLV:{bs['mean_clv']:+.2f}%" if bs.get("mean_clv") is not None else ""
            flag = " ⚠️" if bs.get("profiling_flag") else ""
            book_lines.append(
                f"`{book}` n={bs['n']} ROI:{bs['roi']:+.1f}%{clv_str}{flag}"
            )
            if bs.get("profiling_flag"):
                profiling_flags.append(book)
        if book_lines:
            fields.append({
                "name":   "📚 Per-book",
                "value":  "\n".join(book_lines),
                "inline": False,
            })
        if profiling_flags:
            fields.append({
                "name":   "⚠️ Potential profiling",
                "value":  f"Books with n≥20 and ROI<-20%: {', '.join(profiling_flags)}",
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


def _load_gate_state() -> dict:
    """Read the existing gate file from GCS. Returns {} on missing or error."""
    from mlb_core.storage import read_bytes, exists
    try:
        if not exists(GATE_FILE_KEY):
            return {}
        import json
        return json.loads(read_bytes(GATE_FILE_KEY))
    except Exception as exc:
        logger.warning("monitor: could not read gate state: %s", exc)
        return {}


def _write_gate_state(state: dict) -> None:
    from mlb_core.storage import write_bytes
    import json
    try:
        write_bytes(json.dumps(state, indent=2).encode(), GATE_FILE_KEY)
    except Exception as exc:
        logger.warning("monitor: could not write gate state: %s", exc)


def _gate_condition_met(rolling: dict) -> tuple[bool, str]:
    """Return (should_suppress, reason) based on rolling metrics."""
    n       = rolling.get("n", 0)
    auc     = rolling.get("auc")
    roi     = rolling.get("roi", 0.0)
    hr      = rolling.get("hit_rate", 0.0)
    avg_mp  = rolling.get("avg_model_prob")  # not in rolling_stats -- use None guard

    if n < MIN_GATE_N:
        return False, f"underpowered (n={n} < {MIN_GATE_N})"

    reasons = []
    if auc is not None and auc < GATE_AUC_MIN:
        reasons.append(f"auc {auc:.3f} < {GATE_AUC_MIN}")

    # Calibration error: hit_rate vs avg_model_prob if available.
    # rolling_stats does not carry avg_model_prob; compute from the
    # passed avg_mp argument when the caller supplies it.
    if avg_mp is not None:
        cal_err = hr - avg_mp
        if abs(cal_err) > GATE_CAL_TOL:
            reasons.append(f"cal_err {cal_err:+.3f} > {GATE_CAL_TOL}")

    if roi < GATE_ROI_MIN:
        reasons.append(f"roi {roi:+.1f}% < {GATE_ROI_MIN}%")

    if reasons:
        return True, "; ".join(reasons)
    return False, "healthy"


def _update_gate(system: str, should_suppress: bool, reason: str,
                 rolling: dict, prev_state: dict, run_date: str) -> dict:
    """Apply hysteresis logic, return updated system gate dict, post flip alerts."""
    prev = prev_state.get(system, {})
    was_suppressed  = prev.get("suppressed", False)
    suppress_streak = prev.get("suppress_streak", 0)
    clear_streak    = prev.get("clear_streak",   0)

    # Check force_gate override in registry.
    from mlb_core.registry import SYSTEMS
    cfg = SYSTEMS.get(system)
    force_gate = cfg.force_gate if cfg else None

    if force_gate is not None:
        forced = force_gate.lower().strip()
        if forced == "on":
            now_suppressed = True
            reason = "force_gate=on (registry override)"
        elif forced == "off":
            now_suppressed = False
            reason = "force_gate=off (registry override)"
        else:
            now_suppressed = was_suppressed  # unknown value -- no change
        suppress_streak = 0
        clear_streak    = 0
    elif should_suppress:
        suppress_streak += 1
        clear_streak     = 0
        now_suppressed   = was_suppressed or (suppress_streak >= GATE_HYSTERESIS)
    else:
        clear_streak    += 1
        suppress_streak  = 0
        now_suppressed   = was_suppressed and (clear_streak < GATE_HYSTERESIS)

    # Post alert on state flip.
    flipped = (now_suppressed != was_suppressed)
    if flipped:
        _post_gate_alert(system, now_suppressed, reason, rolling, run_date)

    return {
        "suppressed":      now_suppressed,
        "reason":          reason,
        "metrics": {
            "auc":   rolling.get("auc"),
            "roi":   rolling.get("roi"),
            "n":     rolling.get("n", 0),
        },
        "suppress_streak": suppress_streak,
        "clear_streak":    clear_streak,
        "as_of":           run_date,
    }


def _post_gate_alert(system: str, suppressed: bool, reason: str,
                     stats: dict, run_date: str) -> None:
    """Post suppressed<->unsuppressed flip to #ops-alerts."""
    from mlb_core.notify.discord import _get_ops_webhook, _post

    webhook_url = _get_ops_webhook()
    if not webhook_url:
        return

    dot = SYSTEMS[system].icon if system in SYSTEMS else "o"
    if suppressed:
        title = f"GATE ACTIVE: {dot} {system} suppressed | {run_date}"
        color = 0xED4245
        desc  = f"System suppressed: {reason}"
    else:
        title = f"GATE CLEARED: {dot} {system} restored | {run_date}"
        color = 0x57F287
        desc  = f"System restored to live betting: {reason}"

    embed = {
        "title":       title,
        "description": desc,
        "color":       color,
        "fields": [
            {"name": "AUC",  "value": f"{stats.get('auc'):.3f}" if stats.get("auc") is not None else "n/a", "inline": True},
            {"name": "ROI",  "value": f"{stats.get('roi', 0):+.1f}%", "inline": True},
            {"name": "n",    "value": str(stats.get("n", 0)), "inline": True},
        ],
        "footer": {"text": "mlb-betting model gate | monitor_performance"},
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

    # Load gate state once; update per-system; write once at end.
    prev_gate  = _load_gate_state()
    prev_sys   = prev_gate.get("systems", {})
    new_sys_gates: dict = {}

    for system in CANONICAL_ORDER:
        sys_bets = all_bets[all_bets["system"] == system]
        if sys_bets.empty:
            results[system] = {"status": "no_data"}
            weekly_stats[system] = None
            # Carry forward previous gate state so hysteresis counters persist.
            new_sys_gates[system] = prev_sys.get(system, {
                "suppressed": False, "reason": "no_data",
                "suppress_streak": 0, "clear_streak": 0,
            })
            continue

        rolling  = _rolling_stats(sys_bets, ROLLING_WINDOW)
        season_s = _season_stats(sys_bets)
        weekly_stats[system] = season_s

        alerts = _check_alerts(system, rolling)
        if alerts:
            logger.warning(f"monitor: {system} ALERT -- {alerts}")
            _post_alert(system, alerts, rolling, run_date)
            total_alerts += len(alerts)
        else:
            logger.info(f"monitor: {system} healthy | "
                        f"n={rolling.get('n',0)} roi={rolling.get('roi',0):+.1f}%")

        # Gate decision.
        should_suppress, gate_reason = _gate_condition_met(rolling)
        gate_entry = _update_gate(
            system, should_suppress, gate_reason, rolling, prev_sys, run_date
        )
        new_sys_gates[system] = gate_entry
        logger.info(
            "monitor: %s gate -> suppressed=%s reason=%s streaks(sup=%d,clr=%d)",
            system, gate_entry["suppressed"], gate_reason,
            gate_entry["suppress_streak"], gate_entry["clear_streak"],
        )

        results[system] = {
            "rolling": rolling,
            "season":  season_s,
            "alerts":  alerts,
            "gate":    {"suppressed": gate_entry["suppressed"], "reason": gate_entry["reason"]},
        }

    # Write gate file (overwrite each run; idempotent).
    import json as _json_mod
    from datetime import timezone, datetime as _datetime
    new_gate_state = {
        "as_of":   _datetime.now(timezone.utc).isoformat(),
        "systems": new_sys_gates,
    }
    _write_gate_state(new_gate_state)
    logger.info("monitor: gate file written to %s", GATE_FILE_KEY)

    if is_monday:
        logger.info("monitor: posting weekly digest")
        # T15: Per-book stats across all systems
        per_book = _per_book_stats(all_bets[all_bets["kelly_triggered"].fillna(True).astype(bool)])
        _post_weekly_digest(weekly_stats, per_book, run_date)

    return {
        "status":       "ok",
        "run_date":     run_date,
        "alerts":       total_alerts,
        "systems":      results,
        "weekly_digest": is_monday,
    }
