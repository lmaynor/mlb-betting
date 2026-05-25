"""
runners/settle_bets.py — Nightly bet settlement for all systems.

Settles bets whose game_date == settle_date (default: yesterday).
All settlement uses MLB Stats API via fetch_game_result().
Bets for games not yet Final are skipped and retried on the next run.

Systems:
  NRFI / YRFI    → 1st inning runs (MLB API linescore)
  F5             → First 5 innings moneyline (MLB API linescore)
  F3/F1H/F7/GAME → Innings window moneylines (MLB API linescore)
  HR             → Home run yes/no (MLB API boxscore, starter rule)
  K / OUTS       → Strikeout / outs recorded O/U (MLB API boxscore)
  BATTER_K/TB/HITS → Batter prop O/U (MLB API boxscore, starter rule)
  PITCHER_ER     → Earned runs O/U (MLB API boxscore)

Ops alerts route to #ops-alerts via DISCORD_WEBHOOK_OPS.
Settlement recap routes to #daily-recap via DISCORD_WEBHOOK_SUMMARY.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta, datetime

import pandas as pd

logger = logging.getLogger(__name__)

import unicodedata as _unicodedata


def _norm(s: str) -> str:
    """Normalise a player name: NFD, strip diacritics, ASCII-fold, lower, strip."""
    if not isinstance(s, str):
        return ""
    n = _unicodedata.normalize("NFD", s)
    n = "".join(c for c in n if _unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


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


# ── Settlement functions ──────────────────────────────────────────────────────

def _settle_nrfi(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle NRFI/YRFI and 1st inning 3-way ML bets via MLB API."""
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
    """
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
      K_OVER_7.5 / K_UNDER_7.5
      OUTS_OVER_14.5 / OUTS_UNDER_14.5

    DK uses half-point lines for K/OUTS — whole-number line signals parsing
    error. Log warning but still grade as push rather than error.
    """
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
            logger.info(f"settle K: {bet['player']} not found in boxscore for game_pk={gpk} -- voiding")
            results.append({"id": bet["id"], "result": "void", "profit": 0.0})
            continue

        actual = pitcher_data[stat_key]
        if actual == line:
            if line == int(line):
                logger.warning(
                    f"settle K: {bet['player']} game_pk={gpk} {stat_key}={actual} "
                    f"line={line} — whole-number line suggests parsing error; "
                    f"grading push but verify bet_type string: {bt}"
                )
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


def _settle_innings_window(pending: pd.DataFrame, game_cache: dict, system: str) -> list[dict]:
    """Settle F3/F1H/F7/GAME moneyline bets via MLB API linescore.

    bet_type format: F3_HOME, F3_AWAY, F1H_HOME, F1H_AWAY, F7_HOME, F7_AWAY,
                     GAME_HOME, GAME_AWAY
    Push (tie) -> result="push", profit=0.
    Void if game not complete through required inning.
    """
    WINDOWS = {"F3": 3, "F1H": 4, "F7": 7, "GAME": 9}
    inn_max = WINDOWS.get(system, 5)
    results = []
    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle {system}: game_pk={gpk} not Final yet -- skipping")
            continue
        innings = r.get("innings") or []
        if system == "GAME":
            if len(innings) < 8:
                results.append({"id": int(bet["id"]), "result": "void", "profit": 0.0})
                logger.info(f"settle {system}: game_pk={gpk} only {len(innings)} innings -- voiding")
                continue
        else:
            if len(innings) < inn_max:
                results.append({"id": int(bet["id"]), "result": "void", "profit": 0.0})
                logger.info(f"settle {system}: game_pk={gpk} only {len(innings)} innings -- voiding")
                continue
        away_r = sum(i["away_runs"] for i in innings[:inn_max])
        home_r = sum(i["home_runs"] for i in innings[:inn_max])
        parts = (bet["bet_type"] or "").upper().rsplit("_", 1)
        if len(parts) != 2:
            logger.warning(f"settle {system}: unrecognised bet_type '{bet['bet_type']}' -- skipping")
            continue
        side = parts[1]
        if away_r == home_r:
            result = "push"
        elif side == "HOME":
            result = "win" if home_r > away_r else "loss"
        elif side == "AWAY":
            result = "win" if away_r > home_r else "loss"
        else:
            logger.warning(f"settle {system}: unknown side '{side}' -- skipping")
            continue
        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
        logger.info(f"settle {system}: {side} game_pk={gpk} away={away_r} home={home_r} -> {result}")
    return results


def _settle_batter_props(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle BATTER_K, BATTER_TB, BATTER_HITS O/U bets via MLB API boxscore.

    bet_type format: BATTER_K_OVER_1.5, BATTER_TB_UNDER_2.5, BATTER_HITS_OVER_0.5
    DK rule: batter must be a starter -- non-starters void.
    """
    STAT_MAP = {
        "BATTER_K":    "strikeouts",
        "BATTER_TB":   "total_bases",
        "BATTER_HITS": "hits",
    }
    results = []
    for _, bet in pending.iterrows():
        gpk    = int(bet["game_pk"])
        bt     = (bet["bet_type"] or "").upper()
        player = _norm(bet.get("player") or "")
        prefix = next((p for p in STAT_MAP if bt.startswith(p + "_")), None)
        if prefix is None:
            logger.warning(f"settle batter_props: unrecognised bet_type '{bt}' -- skipping")
            continue
        remainder = bt[len(prefix) + 1:]
        parts = remainder.split("_")
        if len(parts) < 2:
            continue
        side = parts[0]
        try:
            line = float(parts[1])
        except ValueError:
            continue
        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle batter_props: game_pk={gpk} not Final yet -- skipping")
            continue
        batters = r.get("batters", {})
        bdata = batters.get(player) or next(
            (v for k, v in batters.items() if player in k or k in player), None
        )
        if bdata is None:
            results.append({"id": int(bet["id"]), "result": "void", "profit": 0.0})
            logger.info(f"settle batter_props: {bet['player']} not in boxscore game_pk={gpk} -- voiding")
            continue
        if not bdata.get("starter", False):
            results.append({"id": int(bet["id"]), "result": "void", "profit": 0.0})
            logger.info(f"settle batter_props: {bet['player']} not starter game_pk={gpk} -- voiding")
            continue
        actual = int(bdata.get(STAT_MAP[prefix], 0))
        if actual == line:
            result = "push"
        elif side == "OVER":
            result = "win" if actual > line else "loss"
        else:
            result = "win" if actual < line else "loss"
        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
        logger.info(f"settle batter_props: {bet['player']} {prefix} game_pk={gpk} actual={actual} line={line} -> {result}")
    return results


def _settle_pitcher_er(pending: pd.DataFrame, game_cache: dict) -> list[dict]:
    """Settle PITCHER_ER O/U bets via MLB API boxscore.

    bet_type format: PITCHER_ER_OVER_2.5, PITCHER_ER_UNDER_2.5
    DK rule: pitcher must appear in boxscore (threw at least one pitch) -- else void.
    """
    results = []
    for _, bet in pending.iterrows():
        gpk    = int(bet["game_pk"])
        bt     = (bet["bet_type"] or "").upper()
        player = _norm(bet.get("player") or "")
        parts  = bt.split("_")
        if len(parts) < 4:
            continue
        side = parts[2]
        try:
            line = float(parts[3])
        except ValueError:
            continue
        r = game_cache.get(gpk)
        if r is None:
            logger.info(f"settle pitcher_er: game_pk={gpk} not Final yet -- skipping")
            continue
        pitchers = r.get("pitchers", {})
        pdata = pitchers.get(player) or next(
            (v for k, v in pitchers.items() if player in k or k in player), None
        )
        if pdata is None:
            results.append({"id": int(bet["id"]), "result": "void", "profit": 0.0})
            logger.info(f"settle pitcher_er: {bet['player']} not in boxscore game_pk={gpk} -- voiding")
            continue
        actual = int(pdata.get("earned_runs", 0))
        if actual == line:
            result = "push"
        elif side == "OVER":
            result = "win" if actual > line else "loss"
        else:
            result = "win" if actual < line else "loss"
        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
        logger.info(f"settle pitcher_er: {bet['player']} game_pk={gpk} actual={actual} line={line} -> {result}")
    return results


# ── Main run ──────────────────────────────────────────────────────────────────

def run(settle_date: str = None) -> dict:
    """Settle all pending bets for settle_date (default: yesterday).

    Fetches MLB API results in parallel across game_pks.
    Posts cross-system recap to #daily-recap via DISCORD_WEBHOOK_SUMMARY.
    Posts ops alert to #ops-alerts via DISCORD_WEBHOOK_OPS on failure.
    """
    from mlb_core.tracking.bet_tracker import _make_engine
    from mlb_core.notify.discord import post_all_systems_summary, post_ops_alert
    from mlb_core.data.game_result import fetch_game_result
    from sqlalchemy import text
    from concurrent.futures import ThreadPoolExecutor, as_completed

    settle_date = settle_date or (date.today() - timedelta(days=1)).isoformat()
    logger.info(f"settle: starting for settle_date={settle_date}")

    try:
        engine = _make_engine(db_path="unused")
        with engine.connect() as conn:
            pending_all = pd.read_sql(
                text("SELECT * FROM bets WHERE result IS NULL AND game_date <= :d"),
                conn, params={"d": settle_date},
            )
    except Exception as e:
        msg = f"settle: DB read failed for {settle_date}: {e}"
        logger.error(msg)
        post_ops_alert(msg, run_date=settle_date)
        raise

    if pending_all.empty:
        logger.info(f"settle: no pending bets for {settle_date} or earlier")
        return {"status": "ok", "settle_date": settle_date, "settled": 0}

    today_count = (pending_all["game_date"] == settle_date).sum()
    retry_count = (pending_all["game_date"] < settle_date).sum()
    logger.info(f"settle: {today_count} bets for {settle_date}, "
                f"{retry_count} stale pending bets being retried")
    for gd, grp in pending_all.groupby("game_date"):
        logger.info(
            f"settle: pending breakdown -- game_date={gd} | "
            f"{len(grp)} bets | systems={grp['system'].unique().tolist()}"
        )

    # Fetch game results in parallel
    all_game_pks = set(pending_all["game_pk"].dropna().astype(int))
    logger.info(f"settle: fetching MLB API results for {len(all_game_pks)} game_pks")
    game_cache: dict = {}

    _MAX_WORKERS = min(8, len(all_game_pks)) if all_game_pks else 1
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_to_gpk = {pool.submit(fetch_game_result, gpk): gpk
                         for gpk in sorted(all_game_pks)}
        for future in as_completed(future_to_gpk):
            gpk = future_to_gpk[future]
            try:
                game_cache[gpk] = future.result()
            except Exception as e:
                logger.warning(f"settle: fetch_game_result({gpk}) raised: {e}")
                game_cache[gpk] = None
            logger.info(f"settle: game_pk={gpk} final={game_cache[gpk] is not None}")

    # Dispatch to per-system settlers
    SYSTEM_MAP = {
        "NRFI":       _settle_nrfi,
        "1I":         _settle_nrfi,   # 1I_AWAY/HOME/DRAW settled by same linescore logic
        "F5":         _settle_f5,
        "HR":         _settle_hr,
        "K":          _settle_k,
        "OUTS":       _settle_k,
        "F3":         lambda p, c: _settle_innings_window(p, c, "F3"),
        "F1H":        lambda p, c: _settle_innings_window(p, c, "F1H"),
        "F7":         lambda p, c: _settle_innings_window(p, c, "F7"),
        "GAME":       lambda p, c: _settle_innings_window(p, c, "GAME"),
        "BATTER_K":   _settle_batter_props,
        "BATTER_TB":  _settle_batter_props,
        "BATTER_HITS":_settle_batter_props,
        "PITCHER_ER": _settle_pitcher_er,
    }

    # Group batter props so they are settled together (single pass over boxscore)
    BATTER_PROP_SYSTEMS = {"BATTER_K", "BATTER_TB", "BATTER_HITS"}

    all_outcomes: list[dict] = []
    batter_prop_pending = pending_all[pending_all["system"].isin(BATTER_PROP_SYSTEMS)]
    non_batter_pending  = pending_all[~pending_all["system"].isin(BATTER_PROP_SYSTEMS)]

    for system, grp in non_batter_pending.groupby("system"):
        sys = system.upper()
        settler = SYSTEM_MAP.get(sys)
        if settler is None:
            logger.warning(f"settle: unknown system '{system}' -- skipping")
            continue
        logger.info(f"settle: processing {sys} -- {len(grp)} bets")
        try:
            outcomes = settler(grp, game_cache)
        except Exception as e:
            msg = f"settle: {sys} settler raised: {e}"
            logger.error(msg)
            post_ops_alert(msg, run_date=settle_date)
            continue
        logger.info(f"settle: {sys} -> {len(outcomes)} settled "
                    f"({len(grp) - len(outcomes)} still pending)")
        all_outcomes.extend(outcomes)

    if not batter_prop_pending.empty:
        logger.info(f"settle: processing batter props -- {len(batter_prop_pending)} bets")
        try:
            outcomes = _settle_batter_props(batter_prop_pending, game_cache)
            all_outcomes.extend(outcomes)
            logger.info(f"settle: batter props -> {len(outcomes)} settled")
        except Exception as e:
            msg = f"settle: batter_props settler raised: {e}"
            logger.error(msg)
            post_ops_alert(msg, run_date=settle_date)

    # Write outcomes to DB
    if all_outcomes:
        settled_at = datetime.now().isoformat()
        try:
            with engine.begin() as conn:
                for o in all_outcomes:
                    conn.execute(
                        text("UPDATE bets SET result=:r, profit=:p, settled_at=:s WHERE id=:id"),
                        {"r": o["result"], "p": o["profit"], "s": settled_at, "id": o["id"]},
                    )
            logger.info(f"settle: wrote {len(all_outcomes)} outcomes to DB")
        except Exception as e:
            msg = f"settle: DB write failed after settling {len(all_outcomes)} bets: {e}"
            logger.error(msg)
            post_ops_alert(msg, run_date=settle_date)
            raise

    # Build season stats for Discord recap
    with engine.connect() as conn:
        season_bets = pd.read_sql(
            text("SELECT * FROM bets WHERE game_date LIKE :y"),
            conn, params={"y": f"{settle_date[:4]}%"},
        )

    ALL_SYSTEMS = [
        "HR", "NRFI", "F5", "K", "OUTS",
        "F3", "F1H", "F7", "GAME",
        "BATTER_K", "BATTER_TB", "BATTER_HITS", "PITCHER_ER",
    ]
    system_stats = {}
    for system in ALL_SYSTEMS:
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
            "pnl":      round(float(pnl), 2),
            "roi":      round(float(roi), 2),
            "avg_edge": float(resolved["edge"].mean()),
            "pending":  int(len(rows[rows["result"].isna()])),
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
