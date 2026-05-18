"""
runners/settle_bets.py — Nightly bet settlement for all four systems.

Settles bets whose game_date == settle_date (default: yesterday).
Reads only GCS sources already in the lake — no new API calls:

  NRFI / YRFI  → Scoring/scoring_master.csv  (inning 1 top+bot runs)
  F5           → Scoring/scoring_master.csv  (sum runs innings 1-5 per team)
  HR           → Statcast/statcast_master.csv (events=="home_run" per batter+game_pk)
  K            → Statcast/statcast_master.csv (count events=="strikeout" per pitcher+game_pk)

Called by main.py /settle. Scheduled nightly at 09:00 UTC (after the
Statcast nightly refresh completes) via the mlb-settle Cloud Scheduler job.

F5 moneyline can push (tied after 5 innings) — those bets get result="push"
and profit=0.

Retry logic: bets older than 1 day that are still pending (result IS NULL)
are retried automatically on each settlement run. This handles Statcast
lag on doubleheaders / late west coast games — they'll be settled on the
next day's run rather than staying pending forever.

run() returns a dict with per-system settlement counts and a stats snapshot
suitable for logging. After settling, posts a cross-system summary embed
to Discord via post_all_systems_summary().
"""
from __future__ import annotations

import logging
from datetime import date, timedelta, datetime

import pandas as pd

logger = logging.getLogger(__name__)


# ── Profit calculation ────────────────────────────────────────────────────────

def _calc_profit(stake: float, odds: int, result: str) -> float:
    if result in ("push", "void"):
        return 0.0
    if result == "loss":
        return -round(stake, 2)
    if odds >= 0:
        return round(stake * odds / 100, 2)
    else:
        return round(stake * 100 / abs(odds), 2)


# ── Load GCS sources ──────────────────────────────────────────────────────────

def _settle_nrfi(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle NRFI/YRFI O/U and 1st inning 3-way ML bets via MLB API.

    bet_type: NRFI, YRFI, 1I_AWAY, 1I_HOME, 1I_DRAW
    """
    results = []
    game_pks = sorted(set(pending["game_pk"].dropna().astype(int).tolist()))
    logger.info(f"settle NRFI: {len(pending)} pending bets | game_pks={game_pks}")

    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle NRFI: game_pk={gpk} not Final yet -- skipping")
            continue
        if not r["innings"]:
            logger.info(f"settle NRFI: game_pk={gpk} no innings data -- skipping")
            continue

        away_r = int(r["innings"][0]["away_runs"])
        home_r = int(r["innings"][0]["home_runs"])
        bt = (bet["bet_type"] or "").upper()

        if bt == "1I_AWAY":
            result = "win" if away_r > 0 and home_r == 0 else "loss"
        elif bt == "1I_HOME":
            result = "win" if home_r > 0 and away_r == 0 else "loss"
        elif bt == "1I_DRAW":
            result = "win" if away_r == 0 and home_r == 0 else "loss"
        else:
            actual = "YRFI" if (away_r + home_r) > 0 else "NRFI"
            result = "win" if bt == actual else "loss"

        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
        logger.info(f"settle NRFI: {bet['bet_type']} game_pk={gpk} away={away_r} home={home_r} -> {result}")
    return results


def _settle_f5(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle F5 moneyline bets via MLB API linescore."""
    results = []
    game_pks = sorted(set(pending["game_pk"].dropna().astype(int).tolist()))
    logger.info(f"settle F5: {len(pending)} pending bets | game_pks={game_pks}")

    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle F5: game_pk={gpk} not Final yet -- skipping")
            continue
        if len(r["innings"]) < 5:
            logger.info(f"settle F5: game_pk={gpk} fewer than 5 innings -- skipping")
            continue

        away_r = sum(i["away_runs"] for i in r["innings"][:5])
        home_r = sum(i["home_runs"] for i in r["innings"][:5])
        side = (bet["bet_type"] or "").upper()

        if away_r == home_r:
            result = "push"
        elif side == "HOME":
            result = "win" if home_r > away_r else "loss"
        elif side == "AWAY":
            result = "win" if away_r > home_r else "loss"
        else:
            logger.warning(f"settle F5: unrecognised bet_type '{bet['bet_type']}' -- skipping")
            continue

        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
        logger.info(f"settle F5: {side} game_pk={gpk} away={away_r} home={home_r} -> {result}")
    return results


def _settle_hr(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle HR yes/no bets via MLB API boxscore.

    DK rule: player must start. Non-starters -> void.
    Starter + HR -> win. Starter no HR -> loss.
    """
    import unicodedata

    def _norm(s):
        if not isinstance(s, str): return ""
        n = unicodedata.normalize("NFD", s)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.encode("ascii", "ignore").decode().lower().strip()

    results = []
    for _, bet in pending.iterrows():
        gpk  = int(bet["game_pk"])
        name = _norm(bet["player"] or "")
        if not name:
            continue

        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle HR: game_pk={gpk} not Final yet -- skipping")
            continue

        player_data = r["batters"].get(name)
        if player_data is None:
            matches = [v for k, v in r["batters"].items() if name in k or k in name]
            player_data = matches[0] if len(matches) == 1 else None

        if player_data is None:
            logger.info(f"settle HR: {bet['player']} not found in boxscore for game_pk={gpk} -- skipping")
            continue

        if not player_data["starter"]:
            results.append({"id": int(bet["id"]), "result": "void", "profit": 0.0})
            logger.info(f"settle HR: {bet['player']} did not start game_pk={gpk} -- voiding")
            continue

        result = "win" if player_data["home_runs"] > 0 else "loss"
        results.append({
            "id":     int(bet["id"]),
            "result": result,
            "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result),
        })
        logger.info(f"settle HR: {bet['player']} game_pk={gpk} hrs={player_data['home_runs']} -> {result}")
    return results


def _settle_k(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle K strikeout O/U and OUTS pitcher outs O/U via MLB API boxscore.

    bet_type format:
      K_OVER_7.5 / K_UNDER_7.5       -- strikeout O/U
      OUTS_OVER_14.5 / OUTS_UNDER_14.5 -- outs recorded O/U

    Matches pitcher by name from bet["player"] field.
    """
    import unicodedata

    def _norm(s):
        if not isinstance(s, str): return ""
        n = unicodedata.normalize("NFD", s)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.encode("ascii", "ignore").decode().lower().strip()

    results = []
    for _, bet in pending.iterrows():
        gpk  = int(bet["game_pk"])
        bt   = (bet["bet_type"] or "").upper()
        parts = bt.split("_")

        if bt.startswith("OUTS_"):
            if len(parts) < 3:
                continue
            side, stat_key = parts[1], "outs"
        else:
            if len(parts) < 3:
                continue
            side, stat_key = parts[1], "strikeouts"

        try:
            line = float(parts[2])
        except (ValueError, IndexError):
            continue

        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle K: game_pk={gpk} not Final yet -- skipping")
            continue

        name = _norm(bet["player"] or "")
        pitcher_data = r["pitchers"].get(name)
        if pitcher_data is None:
            matches = [v for k, v in r["pitchers"].items() if name in k or k in name]
            pitcher_data = matches[0] if len(matches) == 1 else None

        if pitcher_data is None:
            # Pitcher not in boxscore -- did not throw a pitch (scratch/void per DK rules)
            logger.info(f"settle K: {bet['player']} not found in boxscore for game_pk={gpk} -- voiding")
            outcomes.append({"id": bet["id"], "result": "void", "profit": 0.0})
            continue

        actual = pitcher_data[stat_key]
        if actual == line:
            result = "push"
        elif side == "OVER":
            result = "win" if actual > line else "loss"
        else:
            result = "win" if actual < line else "loss"

        results.append({
            "id":     int(bet["id"]),
            "result": result,
            "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result),
            "actual": actual,
        })
        logger.info(f"settle K: {bet['player']} game_pk={gpk} {stat_key}={actual} line={line} {side} -> {result}")
    return results


def run(settle_date: str = None) -> dict:
    """Settle all pending bets for settle_date (default: yesterday).

    All settlement now uses MLB Stats API via fetch_game_result().
    Bets for games not yet Final are skipped and retried automatically
    on the next settlement run.
    """
    from mlb_core.tracking.bet_tracker import _make_engine
    from mlb_core.notify.discord import post_all_systems_summary
    from mlb_core.data.game_result import fetch_game_result
    from sqlalchemy import text

    settle_date = settle_date or (date.today() - timedelta(days=1)).isoformat()
    logger.info(f"settle: starting for settle_date={settle_date}")

    engine = _make_engine(db_path="unused")
    with engine.connect() as conn:
        pending_all = pd.read_sql(
            text("SELECT * FROM bets WHERE result IS NULL AND game_date <= :d"),
            conn, params={"d": settle_date},
        )

    if pending_all.empty:
        logger.info(f"settle: no pending bets for {settle_date} or earlier")
        return {"status": "ok", "settle_date": settle_date, "settled": 0}

    today_count = (pending_all["game_date"] == settle_date).sum()
    retry_count = (pending_all["game_date"] < settle_date).sum()
    logger.info(f"settle: {today_count} bets for {settle_date}, "
                f"{retry_count} stale pending bets being retried")
    for gd, grp in pending_all.groupby("game_date"):
        logger.info(f"settle: pending breakdown -- game_date={gd} | {len(grp)} bets | systems={grp['system'].unique().tolist()}")

    # Fetch game results once per game_pk -- shared across all systems
    all_game_pks = set(pending_all["game_pk"].dropna().astype(int))
    logger.info(f"settle: fetching MLB API results for {len(all_game_pks)} game_pks")
    game_cache: dict = {}
    for gpk in sorted(all_game_pks):
        game_cache[gpk] = fetch_game_result(gpk)
        final = game_cache[gpk] is not None
        logger.info(f"settle: game_pk={gpk} final={final}")

    all_outcomes: list[dict] = []
    for system, grp in pending_all.groupby("system"):
        sys = system.upper()
        logger.info(f"settle: processing {sys} -- {len(grp)} bets")
        if sys == "NRFI":    outcomes = _settle_nrfi(grp, game_cache)
        elif sys == "F5":    outcomes = _settle_f5(grp, game_cache)
        elif sys == "HR":    outcomes = _settle_hr(grp, game_cache)
        elif sys in ("K", "OUTS"): outcomes = _settle_k(grp, game_cache)
        else:
            logger.warning(f"settle: unknown system '{system}' -- skipping")
            continue
        logger.info(f"settle: {sys} -> {len(outcomes)} settled "
                    f"({len(grp) - len(outcomes)} still pending)")
        all_outcomes.extend(outcomes)

    if all_outcomes:
        settled_at = datetime.now().isoformat()
        with engine.begin() as conn:
            for o in all_outcomes:
                conn.execute(
                    text("UPDATE bets SET result=:r, profit=:p, settled_at=:s WHERE id=:id"),
                    {"r": o["result"], "p": o["profit"], "s": settled_at, "id": o["id"]},
                )
        logger.info(f"settle: wrote {len(all_outcomes)} outcomes to DB")

    with engine.connect() as conn:
        season_bets = pd.read_sql(
            text("SELECT * FROM bets WHERE game_date LIKE :y"),
            conn, params={"y": f"{settle_date[:4]}%"},
        )

    system_stats = {}
    for system in ["HR", "NRFI", "F5", "K", "OUTS"]:
        rows = season_bets[season_bets["system"] == system]
        resolved = rows[rows["result"].notna()]
        if resolved.empty:
            system_stats[system] = None
            continue
        wins         = (resolved["result"] == "win").sum()
        total_bets   = len(resolved)
        total_staked = resolved["stake"].sum()
        pnl          = resolved["profit"].sum()
        roi          = pnl / total_staked * 100 if total_staked > 0 else 0.0
        system_stats[system] = {
            "bets":     total_bets,
            "wins":     int(wins),
            "hit_rate": wins / total_bets,
            "pnl":      pnl,
            "roi":      roi,
            "avg_edge": resolved["edge"].mean(),
            "pending":  len(rows[rows["result"].isna()]),
        }

    post_all_systems_summary(system_stats, settle_date=settle_date)

    return {
        "status":      "ok",
        "settle_date": settle_date,
        "settled":     len(all_outcomes),
        "retried":     int(retry_count),
        "skipped":     len(pending_all) - len(all_outcomes),
        "systems":     {k: (v or {}) for k, v in system_stats.items()},
    }
