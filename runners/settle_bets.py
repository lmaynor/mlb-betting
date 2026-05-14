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
    if result == "push":
        return 0.0
    if result == "loss":
        return -round(stake, 2)
    if odds >= 0:
        return round(stake * odds / 100, 2)
    else:
        return round(stake * 100 / abs(odds), 2)


# ── Load GCS sources ──────────────────────────────────────────────────────────

def _load_scoring(settle_date: str) -> pd.DataFrame:
    from mlb_core.storage import read_csv, exists
    if not exists("Scoring/scoring_master.csv"):
        logger.warning("settle: scoring_master.csv not found in GCS")
        return pd.DataFrame()
    try:
        df = read_csv("Scoring/scoring_master.csv", low_memory=False)
        df["game_pk"] = pd.to_numeric(df["game_pk"], errors="coerce")
        df["inning"]  = pd.to_numeric(df["inning"],  errors="coerce")
        df["runs"]    = pd.to_numeric(df["runs"],    errors="coerce").fillna(0)
        df = df.dropna(subset=["game_pk", "inning"])
        logger.info(f"settle: scoring_master loaded {len(df):,} rows | {df['game_pk'].nunique():,} games")
        return df
    except Exception as e:
        logger.error(f"settle: scoring_master load failed: {e}")
        return pd.DataFrame()


def _load_statcast_outcomes(game_pks: set) -> pd.DataFrame:
    from mlb_core.storage import read_csv, exists
    if not game_pks:
        return pd.DataFrame()
    if not exists("Statcast/statcast_master.csv"):
        logger.warning("settle: statcast_master.csv not found in GCS")
        return pd.DataFrame()
    try:
        sc = read_csv("Statcast/statcast_master.csv", low_memory=False)
        sc["game_pk"] = pd.to_numeric(sc["game_pk"], errors="coerce")
        sc = sc[sc["game_pk"].isin(game_pks) & sc["events"].notna()].copy()
        return sc[["game_pk", "pitcher", "batter", "events", "player_name"]].copy()
    except Exception as e:
        logger.error(f"settle: statcast_master load failed: {e}")
        return pd.DataFrame()


# ── Per-system settlement logic ───────────────────────────────────────────────

def _settle_nrfi(pending: pd.DataFrame, scoring: pd.DataFrame) -> list[dict]:
    """Settle NRFI/YRFI O/U bets and first-inning 3-way ML bets.

    3-way settlement (bet_type in 1I_AWAY, 1I_HOME, 1I_DRAW):
      1I_AWAY: win if away half (top) > 0 AND home half (bot) == 0
      1I_HOME: win if home half (bot) > 0 AND away half (top) == 0
      1I_DRAW: win if both halves == 0 (same as NRFI)
    """
    results = []
    game_pks = set(pending["game_pk"].dropna().astype(int))
    inn1 = scoring[(scoring["game_pk"].isin(game_pks)) & (scoring["inning"] == 1)]

    logger.info(f"settle NRFI: {len(pending)} pending bets | game_pks={sorted(set(pending['game_pk'].dropna().astype(int).tolist()))}")
    logger.info(f"settle NRFI: scoring has {inn1['game_pk'].nunique()} matching games")
    # Per-half runs for inning 1
    half_runs = inn1.groupby(["game_pk", "half"])["runs"].sum().unstack(fill_value=0)
    for col in ("top", "bot"):
        if col not in half_runs.columns:
            half_runs[col] = 0

    # Total runs (for NRFI/YRFI)
    runs_by_game = half_runs["top"].add(half_runs["bot"], fill_value=0)

    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        if gpk not in half_runs.index:
            logger.info(f"settle NRFI: game_pk={gpk} not in scoring master yet — skipping")
            continue

        away_r = int(half_runs.loc[gpk, "top"])  # away bats in top
        home_r = int(half_runs.loc[gpk, "bot"])  # home bats in bot
        bt     = (bet["bet_type"] or "").upper()

        if bt == "1I_AWAY":
            result = "win" if away_r > 0 and home_r == 0 else "loss"
        elif bt == "1I_HOME":
            result = "win" if home_r > 0 and away_r == 0 else "loss"
        elif bt == "1I_DRAW":
            result = "win" if away_r == 0 and home_r == 0 else "loss"
        else:
            # Standard NRFI/YRFI
            total_runs = away_r + home_r
            actual = "YRFI" if total_runs > 0 else "NRFI"
            result = "win" if bt == actual else "loss"

        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
    return results


def _settle_f5(pending: pd.DataFrame, scoring: pd.DataFrame) -> list[dict]:
    results = []
    game_pks = set(pending["game_pk"].dropna().astype(int))
    f5 = scoring[(scoring["game_pk"].isin(game_pks)) & (scoring["inning"] <= 5)]
    logger.info(f"settle F5: {len(pending)} pending bets | game_pks={sorted(set(pending['game_pk'].dropna().astype(int).tolist()))}")
    logger.info(f"settle F5: scoring has {f5['game_pk'].nunique()} matching games")
    runs = f5.groupby(["game_pk", "half"])["runs"].sum().unstack(fill_value=0)
    for col in ("top", "bot"):
        if col not in runs.columns:
            runs[col] = 0
    runs = runs.rename(columns={"top": "away_runs", "bot": "home_runs"})
    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        if gpk not in runs.index:
            logger.info(f"settle F5: game_pk={gpk} not in scoring master yet — skipping")
            continue
        home_r = int(runs.loc[gpk, "home_runs"])
        away_r = int(runs.loc[gpk, "away_runs"])
        side = (bet["bet_type"] or "").upper()
        if home_r == away_r:
            result = "push"
        elif side == "HOME":
            result = "win" if home_r > away_r else "loss"
        elif side == "AWAY":
            result = "win" if away_r > home_r else "loss"
        else:
            logger.warning(f"settle F5: unrecognised bet_type '{bet['bet_type']}' — skipping")
            continue
        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
    return results


def _settle_hr(pending: pd.DataFrame, sc: pd.DataFrame) -> list[dict]:
    import unicodedata
    def _norm(s):
        if not isinstance(s, str): return ""
        n = unicodedata.normalize("NFD", s)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.encode("ascii", "ignore").decode().lower().strip()
    if sc.empty:
        return []
    hr_events = sc[sc["events"] == "home_run"].copy()
    hr_events["_name"] = hr_events["player_name"].apply(_norm)
    hr_set = set(zip(hr_events["game_pk"].astype(int), hr_events["_name"]))
    sc["_name"] = sc["player_name"].apply(_norm)
    appeared = set(zip(sc["game_pk"].astype(int), sc["_name"]))
    results = []
    for _, bet in pending.iterrows():
        gpk  = int(bet["game_pk"])
        name = _norm(bet["player"] or "")
        if not name:
            continue
        if (gpk, name) not in appeared:
            logger.info(f"settle HR: {bet['player']} not in Statcast for game_pk={gpk} — skipping (DNP?)")
            continue
        result = "win" if (gpk, name) in hr_set else "loss"
        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result)})
    return results


def _settle_k(pending: pd.DataFrame, sc: pd.DataFrame) -> list[dict]:
    """Settle K strikeout O/U bets and OUTS_ pitcher outs O/U bets.

    bet_type format:
      K_OVER_7.5 / K_UNDER_7.5    — strikeout O/U (vs line)
      OUTS_OVER_14.5 / OUTS_UNDER_14.5 — outs recorded O/U (vs line)

    Both use Statcast per (game_pk, pitcher). Outs = count of all PA-ending
    events where the pitcher recorded an out (events != walk/HBP/HR/single etc.)
    — more precisely, total outs = (innings pitched × 3) which we approximate
    as count of all plate appearances that ended in an out event.
    """
    if sc.empty:
        return []
    from mlb_core.storage import read_csv, exists

    # Strikeout counts
    k_counts = (
        sc[sc["events"] == "strikeout"]
        .groupby(["game_pk", "pitcher"]).size()
        .reset_index(name="actual_ks")
    )
    k_counts["game_pk"] = k_counts["game_pk"].astype(int)

    # Outs recorded: PA-ending events that are outs (not hits, walks, HBP, errors)
    _OUT_EVENTS = {
        "strikeout", "strikeout_double_play", "field_out", "force_out",
        "grounded_into_double_play", "double_play", "triple_play",
        "fielders_choice_out", "sac_fly", "sac_bunt", "sac_fly_double_play",
    }
    outs_counts = (
        sc[sc["events"].isin(_OUT_EVENTS)]
        .groupby(["game_pk", "pitcher"]).size()
        .reset_index(name="actual_outs")
    )
    outs_counts["game_pk"] = outs_counts["game_pk"].astype(int)

    # Resolve pitcher id from K feature CSV
    gpk_to_pitcher = {}
    if exists("K_Pro_System/data/model_features.csv"):
        try:
            kf = read_csv("K_Pro_System/data/model_features.csv", low_memory=False)
            kf["game_pk"] = pd.to_numeric(kf["game_pk"], errors="coerce")
            kf = kf.dropna(subset=["game_pk", "pitcher"])
            kf["game_pk"] = kf["game_pk"].astype(int)
            kf["pitcher"] = kf["pitcher"].astype(int)
            gpk_to_pitcher = kf.groupby("game_pk")["pitcher"].first().to_dict()
        except Exception as e:
            logger.warning(f"settle K: feature CSV load failed: {e}")

    k_lookup    = k_counts.set_index(["game_pk", "pitcher"])["actual_ks"].to_dict()
    outs_lookup = outs_counts.set_index(["game_pk", "pitcher"])["actual_outs"].to_dict()

    results = []
    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        bt  = (bet["bet_type"] or "").upper()
        parts = bt.split("_")

        # Determine market type from prefix
        if bt.startswith("OUTS_"):
            # OUTS_OVER_14.5 → parts = ["OUTS", "OVER", "14.5"]
            if len(parts) < 3:
                continue
            side = parts[1]
            try:
                line = float(parts[2])
            except ValueError:
                continue
            pitcher_id = gpk_to_pitcher.get(gpk)
            if pitcher_id is None:
                logger.info(f"settle OUTS: no pitcher_id for game_pk={gpk} — skipping")
                continue
            actual = outs_lookup.get((gpk, pitcher_id))
            if actual is None:
                logger.info(f"settle OUTS: no Statcast data for game_pk={gpk} — skipping")
                continue
        else:
            # K_OVER_7.5 → parts = ["K", "OVER", "7.5"]
            if len(parts) < 3:
                continue
            side = parts[1]
            try:
                line = float(parts[2])
            except ValueError:
                continue
            pitcher_id = gpk_to_pitcher.get(gpk)
            if pitcher_id is None:
                logger.info(f"settle K: no pitcher_id for game_pk={gpk} — skipping")
                continue
            actual = k_lookup.get((gpk, pitcher_id))
            if actual is None:
                logger.info(f"settle K: no Statcast data for game_pk={gpk} — skipping")
                continue

        if actual == line:
            result = "push"
        elif side == "OVER":
            result = "win" if actual > line else "loss"
        else:
            result = "win" if actual < line else "loss"

        results.append({"id": int(bet["id"]), "result": result,
                        "profit": _calc_profit(float(bet["stake"]), int(bet["odds"]), result),
                        "actual": actual})
    return results


# ── Entry point ──────────────────────────────────────────────────────────────

def run(settle_date: str = None) -> dict:
    """Settle all pending bets for settle_date (default: yesterday).

    Also retries any bets from prior dates still pending (Statcast lag retry).
    """
    from mlb_core.tracking.bet_tracker import _make_engine
    from mlb_core.notify.discord import post_all_systems_summary
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
        logger.info(f"settle: pending breakdown — game_date={gd} | {len(grp)} bets | systems={grp['system'].unique().tolist()}")

    all_game_pks = set(pending_all["game_pk"].dropna().astype(int))
    needs_scoring  = pending_all["system"].isin({"NRFI", "F5"}).any()
    needs_statcast = pending_all["system"].isin({"HR", "K"}).any()

    scoring = _load_scoring(settle_date)          if needs_scoring  else pd.DataFrame()
    sc      = _load_statcast_outcomes(all_game_pks) if needs_statcast else pd.DataFrame()

    all_outcomes: list[dict] = []
    for system, grp in pending_all.groupby("system"):
        sys = system.upper()
        logger.info(f"settle: processing {sys} — {len(grp)} bets")
        if sys == "NRFI":   outcomes = _settle_nrfi(grp, scoring)
        elif sys == "F5":   outcomes = _settle_f5(grp, scoring)
        elif sys == "HR":   outcomes = _settle_hr(grp, sc)
        elif sys == "K":    outcomes = _settle_k(grp, sc)
        else:
            logger.warning(f"settle: unknown system '{system}' — skipping")
            continue
        logger.info(f"settle: {sys} → {len(outcomes)} settled "
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
    for system in ["HR", "NRFI", "F5", "K"]:
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
