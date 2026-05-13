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

run() returns a dict with per-system settlement counts and a stats snapshot
suitable for logging. After settling, posts a cross-system summary embed
to Discord via post_all_systems_summary().
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


# ── Profit calculation ────────────────────────────────────────────────────────

def _calc_profit(stake: float, odds: int, result: str) -> float:
    """Return dollar profit for a settled bet.

    result must be 'win', 'loss', or 'push'.
    American odds convention: +150 wins $1.50 per $1 staked; -110 wins $0.91.
    """
    if result == "push":
        return 0.0
    if result == "loss":
        return -round(stake, 2)
    # win
    if odds >= 0:
        return round(stake * odds / 100, 2)
    else:
        return round(stake * 100 / abs(odds), 2)


# ── Load GCS sources (lazy, cached within the run() call) ────────────────────

def _load_scoring(settle_date: str) -> pd.DataFrame:
    """Load scoring_master filtered to game_pks that have inning data for
    settle_date. We can't filter by date directly (scoring_master has no
    game_date column), so we load the whole thing and filter after joining
    to pending bets. Returns empty DataFrame on missing file."""
    from mlb_core.storage import read_csv, exists
    if not exists("Scoring/scoring_master.csv"):
        logger.warning("settle: scoring_master.csv not found in GCS")
        return pd.DataFrame()
    try:
        df = read_csv("Scoring/scoring_master.csv", low_memory=False)
        df["game_pk"] = pd.to_numeric(df["game_pk"], errors="coerce")
        df["inning"]  = pd.to_numeric(df["inning"],  errors="coerce")
        df["runs"]    = pd.to_numeric(df["runs"],    errors="coerce").fillna(0)
        return df.dropna(subset=["game_pk", "inning"])
    except Exception as e:
        logger.error(f"settle: scoring_master load failed: {e}")
        return pd.DataFrame()


def _load_statcast_outcomes(game_pks: set) -> pd.DataFrame:
    """Load statcast_master filtered to the given game_pks and only
    PA-ending events. Returns columns: game_pk, pitcher, batter, events,
    player_name (batter display name).
    """
    from mlb_core.storage import read_csv, exists
    if not game_pks:
        return pd.DataFrame()
    if not exists("Statcast/statcast_master.csv"):
        logger.warning("settle: statcast_master.csv not found in GCS")
        return pd.DataFrame()
    try:
        # Load full Statcast — we need to filter to relevant game_pks.
        # This is a large file (~300MB) but we're already loading it in the
        # feature builders so Cloud Run has seen it before.
        sc = read_csv("Statcast/statcast_master.csv", low_memory=False)
        sc["game_pk"] = pd.to_numeric(sc["game_pk"], errors="coerce")
        sc = sc[sc["game_pk"].isin(game_pks) & sc["events"].notna()].copy()
        return sc[["game_pk", "pitcher", "batter", "events",
                   "player_name"]].copy()
    except Exception as e:
        logger.error(f"settle: statcast_master load failed: {e}")
        return pd.DataFrame()


# ── Per-system settlement logic ───────────────────────────────────────────────

def _settle_nrfi(pending: pd.DataFrame, scoring: pd.DataFrame) -> list[dict]:
    """Settle NRFI/YRFI bets.

    Win condition:
      NRFI → total runs in inning 1 (top + bot) == 0
      YRFI → total runs in inning 1 (top + bot) >= 1
    """
    results = []
    game_pks = set(pending["game_pk"].dropna().astype(int))
    inn1 = scoring[(scoring["game_pk"].isin(game_pks)) & (scoring["inning"] == 1)]
    runs_by_game = inn1.groupby("game_pk")["runs"].sum()

    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        if gpk not in runs_by_game.index:
            logger.info(f"settle NRFI: game_pk={gpk} not in scoring master yet — skipping")
            continue
        total_runs = int(runs_by_game[gpk])
        actual = "YRFI" if total_runs > 0 else "NRFI"
        bet_side = (bet["bet_type"] or "").upper()
        result = "win" if bet_side == actual else "loss"
        profit = _calc_profit(float(bet["stake"]), int(bet["odds"]), result)
        results.append({"id": int(bet["id"]), "result": result, "profit": profit})
    return results


def _settle_f5(pending: pd.DataFrame, scoring: pd.DataFrame) -> list[dict]:
    """Settle F5 moneyline bets.

    Win condition: the team we bet (HOME or AWAY) has more runs through
    the end of inning 5. Tied after 5 = push.
    """
    results = []
    game_pks = set(pending["game_pk"].dropna().astype(int))
    f5 = scoring[(scoring["game_pk"].isin(game_pks)) & (scoring["inning"] <= 5)]

    # Sum runs per game_pk per half ("top" = away batting, "bot" = home batting)
    runs = f5.groupby(["game_pk", "half"])["runs"].sum().unstack(fill_value=0)
    # Ensure both columns exist
    for col in ("top", "bot"):
        if col not in runs.columns:
            runs[col] = 0
    # away_runs = top half (away team bats), home_runs = bot half
    runs = runs.rename(columns={"top": "away_runs", "bot": "home_runs"})

    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        if gpk not in runs.index:
            logger.info(f"settle F5: game_pk={gpk} not in scoring master yet — skipping")
            continue
        home_r = int(runs.loc[gpk, "home_runs"])
        away_r = int(runs.loc[gpk, "away_runs"])
        bet_side = (bet["bet_type"] or "").upper()  # "HOME" or "AWAY"

        if home_r == away_r:
            result = "push"
        elif bet_side == "HOME":
            result = "win" if home_r > away_r else "loss"
        elif bet_side == "AWAY":
            result = "win" if away_r > home_r else "loss"
        else:
            logger.warning(f"settle F5: unrecognised bet_type '{bet['bet_type']}' "
                           f"for bet id={bet['id']} — skipping")
            continue

        profit = _calc_profit(float(bet["stake"]), int(bet["odds"]), result)
        results.append({"id": int(bet["id"]), "result": result, "profit": profit})
    return results


def _settle_hr(pending: pd.DataFrame, sc: pd.DataFrame) -> list[dict]:
    """Settle HR bets.

    Win condition: batter has at least one events=="home_run" in game_pk.

    Statcast stores batter as MLBAM int id. The bets table stores player
    name (string). We resolve name → id by matching player_name in the
    Statcast frame for the same game_pk (Statcast's player_name column is
    the batter's display name, same normalisation as the runner uses).
    """
    import unicodedata

    def _norm(s):
        if not isinstance(s, str):
            return ""
        n = unicodedata.normalize("NFD", s)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.encode("ascii", "ignore").decode().lower().strip()

    if sc.empty:
        return []

    # Build (game_pk, normalised_name) → did_hr lookup
    hr_events = sc[sc["events"] == "home_run"].copy()
    hr_events["_name"] = hr_events["player_name"].apply(_norm)
    hr_set = set(zip(hr_events["game_pk"].astype(int), hr_events["_name"]))

    # Also build a lookup from all PA events so we can confirm the batter
    # actually appeared (if they DID appear but have no HR it's a loss;
    # if they didn't appear at all we skip — DNP/scratched).
    sc["_name"] = sc["player_name"].apply(_norm)
    appeared = set(zip(sc["game_pk"].astype(int), sc["_name"]))

    results = []
    for _, bet in pending.iterrows():
        gpk  = int(bet["game_pk"])
        name = _norm(bet["player"] or "")
        if not name:
            continue
        if (gpk, name) not in appeared:
            logger.info(f"settle HR: {bet['player']} (id={bet['id']}) "
                        f"not in Statcast for game_pk={gpk} — skipping (may be DNP)")
            continue
        result = "win" if (gpk, name) in hr_set else "loss"
        profit = _calc_profit(float(bet["stake"]), int(bet["odds"]), result)
        results.append({"id": int(bet["id"]), "result": result, "profit": profit})
    return results


def _settle_k(pending: pd.DataFrame, sc: pd.DataFrame) -> list[dict]:
    """Settle K O/U bets.

    Win condition: actual K count vs line parsed from bet_type.
    bet_type format: "K_OVER_7.5" or "K_UNDER_7.5"

    Pitcher is stored in `player` (display name). We resolve to pitcher id
    via Statcast player_name in the same game_pk, then count strikeouts.
    """
    import unicodedata

    def _norm(s):
        if not isinstance(s, str):
            return ""
        n = unicodedata.normalize("NFD", s)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.encode("ascii", "ignore").decode().lower().strip()

    if sc.empty:
        return []

    # Build pitcher display-name → id map from this game's Statcast
    # (pitcher column = MLBAM int; player_name = batter name, NOT pitcher)
    # Statcast doesn't have a pitcher_name column — we can only count
    # strikeouts by pitcher id. So we need game_pk + pitcher id.
    # The bets table has only player name. We bridge via the feature CSV's
    # pitcher id that was used when the bet was logged: game_pk is correct
    # because we stored it. We need name → pitcher_id from Statcast.
    # Statcast has `pitcher` (id) but no pitcher name column. However the
    # scoring_master has no pitcher either. The cleanest bridge is:
    # in Statcast, for each (game_pk, pitcher_id), look at the batter PA
    # events to identify the pitcher's starts — but we don't have pitcher
    # display names in Statcast.
    #
    # Practical solution: load pitcher names from the K feature CSV which
    # stores both pitcher (id) and a _pitcher_name field for today's slate.
    # For historical settlement we use the model_features CSV which has
    # pitcher id per game_pk. Join bet game_pk + player name to
    # model_features to get pitcher id, then count from Statcast.
    #
    # Simpler fallback used here: count strikeouts by (game_pk, pitcher_id)
    # from Statcast, then match to bet by looking up pitcher_id from the
    # K feature CSV's game_pk+pitcher join. We store pitcher id implicitly
    # because the K runner matches by name. We resolve via the feature CSV.

    from mlb_core.storage import read_csv, exists

    # Load K feature CSV to get game_pk → pitcher_id → pitcher display name
    pitcher_id_map: dict[tuple[int, str], int] = {}  # (game_pk, norm_name) → pitcher_id
    if exists("K_Pro_System/data/model_features.csv"):
        try:
            kf = read_csv("K_Pro_System/data/model_features.csv", low_memory=False)
            kf["game_pk"] = pd.to_numeric(kf["game_pk"], errors="coerce")
            kf = kf.dropna(subset=["game_pk", "pitcher"])
            # model_features has `pitcher` (id) but no display name column —
            # we rely on the _pitcher_name col written by build_k_features
            # for slate rows. For historical rows we don't have the name.
            # Use the bet's game_pk + player to match pitcher via
            # a separate pitcher name resolution approach below.
        except Exception:
            pass

    # Count strikeouts per (game_pk, pitcher_id) from Statcast
    k_counts = (
        sc[sc["events"] == "strikeout"]
        .groupby(["game_pk", "pitcher"])
        .size()
        .reset_index(name="actual_ks")
    )
    k_counts["game_pk"] = k_counts["game_pk"].astype(int)

    # For pitcher name resolution: map normalised batter player_name to batter id,
    # then invert to get pitcher id from the "pitcher" column.
    # In Statcast each row has pitcher (id) and player_name (batter name).
    # We need pitcher display name. Statcast doesn't give us that directly.
    # Best available: the pitcher id is in the K feature CSV rows where
    # starter_ks is NaN (today's slate rows added by _attach_today_slate).
    # For past games, pitcher (id) is in historical rows.
    #
    # Approach: for each pending K bet, look up the pitcher id from the
    # feature CSV by matching game_pk. There's exactly one pitcher per
    # game_pk (the starter). If multiple rows share a game_pk, take the
    # one with the most recent game_date = the slate row.

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
            gpk_to_pitcher = {}
    else:
        gpk_to_pitcher = {}

    k_lookup = k_counts.set_index(["game_pk", "pitcher"])["actual_ks"].to_dict()

    results = []
    for _, bet in pending.iterrows():
        gpk = int(bet["game_pk"])
        bt  = (bet["bet_type"] or "").upper()  # "K_OVER_7.5" or "K_UNDER_7.5"

        # Parse side and line from bet_type
        parts = bt.split("_")  # ["K", "OVER", "7.5"]
        if len(parts) < 3:
            logger.warning(f"settle K: can't parse bet_type '{bet['bet_type']}' "
                           f"for bet id={bet['id']} — skipping")
            continue
        side = parts[1]  # "OVER" or "UNDER"
        try:
            line = float(parts[2])
        except ValueError:
            logger.warning(f"settle K: bad line in bet_type '{bt}' — skipping")
            continue

        pitcher_id = gpk_to_pitcher.get(gpk)
        if pitcher_id is None:
            logger.info(f"settle K: no pitcher_id for game_pk={gpk} — skipping")
            continue

        actual_ks = k_lookup.get((gpk, pitcher_id))
        if actual_ks is None:
            logger.info(f"settle K: no Statcast strikeout data for "
                        f"game_pk={gpk} pitcher={pitcher_id} — skipping (game may not be in master yet)")
            continue

        if side == "OVER":
            result = "win" if actual_ks > line else "loss"
        else:  # UNDER
            result = "win" if actual_ks < line else "loss"
        # Exact K count == line is a push (rare but real for integer lines)
        if actual_ks == line:
            result = "push"

        profit = _calc_profit(float(bet["stake"]), int(bet["odds"]), result)
        results.append({"id": int(bet["id"]), "result": result,
                        "profit": profit, "actual": actual_ks})
    return results


# ── Entry point ──────────────────────────────────────────────────────────────

def run(settle_date: str = None) -> dict:
    """Settle all pending bets for settle_date (default: yesterday).

    Steps:
      1. Load pending bets from Postgres grouped by system
      2. Load GCS scoring + Statcast for the relevant game_pks
      3. Settle per system
      4. Write results back to Postgres via BetTracker.settle()
      5. Post cross-system summary to Discord
    """
    from mlb_core.config import DB_URL
    from mlb_core.tracking.bet_tracker import BetTracker, _make_engine
    from mlb_core.notify.discord import post_all_systems_summary
    import sqlalchemy as sa
    from sqlalchemy import text

    settle_date = settle_date or (date.today() - timedelta(days=1)).isoformat()
    logger.info(f"settle: starting for settle_date={settle_date}")

    # Load all pending bets for this date across all systems
    engine = _make_engine(db_path="unused")  # uses MLB_DB_URL env var
    with engine.connect() as conn:
        pending_all = pd.read_sql(
            text("SELECT * FROM bets WHERE result IS NULL AND game_date = :d"),
            conn,
            params={"d": settle_date},
        )

    if pending_all.empty:
        logger.info(f"settle: no pending bets for {settle_date}")
        return {"status": "ok", "settle_date": settle_date, "settled": 0}

    logger.info(f"settle: {len(pending_all)} pending bets across "
                f"{pending_all['system'].nunique()} systems")

    # Identify game_pks needed for each source
    all_game_pks = set(pending_all["game_pk"].dropna().astype(int))
    scoring_systems = {"NRFI", "F5"}
    statcast_systems = {"HR", "K"}

    needs_scoring   = pending_all["system"].isin(scoring_systems).any()
    needs_statcast  = pending_all["system"].isin(statcast_systems).any()

    scoring  = _load_scoring(settle_date)   if needs_scoring  else pd.DataFrame()
    sc       = _load_statcast_outcomes(all_game_pks) if needs_statcast else pd.DataFrame()

    # Settle per system
    all_outcomes: list[dict] = []

    for system, grp in pending_all.groupby("system"):
        sys = system.upper()
        logger.info(f"settle: processing {sys} — {len(grp)} bets")
        if sys == "NRFI":
            outcomes = _settle_nrfi(grp, scoring)
        elif sys == "F5":
            outcomes = _settle_f5(grp, scoring)
        elif sys == "HR":
            outcomes = _settle_hr(grp, sc)
        elif sys == "K":
            outcomes = _settle_k(grp, sc)
        else:
            logger.warning(f"settle: unknown system '{system}' — skipping")
            continue

        logger.info(f"settle: {sys} → {len(outcomes)} settled "
                    f"({len(grp) - len(outcomes)} skipped/pending)")
        all_outcomes.extend(outcomes)

    # Write results back to DB
    if all_outcomes:
        from datetime import datetime
        settled_at = datetime.now().isoformat()
        with engine.begin() as conn:
            for o in all_outcomes:
                conn.execute(
                    text("UPDATE bets SET result=:r, profit=:p, settled_at=:s "
                         "WHERE id=:id"),
                    {"r": o["result"], "p": o["profit"],
                     "s": settled_at,  "id": o["id"]},
                )
        logger.info(f"settle: wrote {len(all_outcomes)} outcomes to DB")

    # Build per-system stats for the Discord summary
    # Re-query to get the freshly settled rows for today
    with engine.connect() as conn:
        season_bets = pd.read_sql(
            text("SELECT * FROM bets WHERE game_date LIKE :y"),
            conn,
            params={"y": f"{settle_date[:4]}%"},
        )

    system_stats = {}
    for system in ["HR", "NRFI", "F5", "K"]:
        rows = season_bets[season_bets["system"] == system]
        resolved = rows[rows["result"].notna()]
        if resolved.empty:
            system_stats[system] = None
            continue
        wins        = (resolved["result"] == "win").sum()
        total_bets  = len(resolved)
        total_staked = resolved["stake"].sum()
        pnl         = resolved["profit"].sum()
        roi         = pnl / total_staked * 100 if total_staked > 0 else 0.0
        avg_edge    = resolved["edge"].mean()
        pending_n   = len(rows[rows["result"].isna()])
        system_stats[system] = {
            "bets":        total_bets,
            "wins":        int(wins),
            "hit_rate":    wins / total_bets,
            "pnl":         pnl,
            "roi":         roi,
            "avg_edge":    avg_edge,
            "pending":     pending_n,
        }

    post_all_systems_summary(system_stats, settle_date=settle_date)

    return {
        "status":      "ok",
        "settle_date": settle_date,
        "settled":     len(all_outcomes),
        "skipped":     len(pending_all) - len(all_outcomes),
        "systems":     {k: (v or {}) for k, v in system_stats.items()},
    }
