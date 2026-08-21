"""
mlb_core.data.aux_joins -- Shared helpers for wiring auxiliary_features data
into feature-builder DataFrames.

All joins are left joins: missing auxiliary data yields NaN rather than
dropped rows.  Safe to call even if GCS files don't yet exist (each
load call is wrapped in try/except and returns unchanged df on failure).

Four entry points:

  join_pitcher_aux(df, ...)   -- pitcher-grain (one row per pitcher-game)
                                  NRFI, K
  join_game_aux(df, ...)      -- game-grain   (one row per game)
                                  F5, GAME
  join_batter_aux(df, ...)    -- batter-grain (one row per batter-game)
                                  HR, BATTER_HITS, BATTER_TB
  join_catcher_aux(df, ...)   -- batter-grain, OPPOSING catcher's own stats
                                  SB only -- added 2026-08-20. This is the
                                  first join in this codebase bringing a
                                  THIRD player-entity (neither the batter
                                  nor the pitcher) onto a row -- no existing
                                  system needed catcher identity/skill at
                                  all before the SB model.

Sources attached per call:

  join_pitcher_aux:
    bref_pitching  -- FIP, WHIP, SO9, BB9         join on (name_norm, year)
    team_schedule  -- travel_miles, home_away_streak, series_game_num
                      join on (pitcher_team, game_pk)
    manager_hooks  -- avg_starter_outs_L30, pct_quick_hooks_L30, pct_quality_starts_L30
                      join on (pitcher_team, game_pk)

  join_game_aux:
    team_schedule  -- home_sched_* / away_sched_*  join on (team, game_pk) x2
    manager_hooks  -- home_hooks_* / away_hooks_*   join on (team, game_pk) x2

  join_batter_aux:
    swing_take     -- batter_runs_chase/heart/shadow/waste  join on (batter MLBAM, year)
    team_schedule  -- home_sched_* / away_sched_*           join on (team, game_pk) x2

Note: the Savant swing-take leaderboard is batter-only (player_id = batter MLBAM ID).
Joining on pitcher ID produces 0 matches, so swing_take is excluded from pitcher/game joins.
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_BREF_COLS  = ["FIP", "WHIP", "SO9", "BB9"]
_ST_COLS    = ["runs_chase", "runs_heart", "runs_shadow", "runs_waste"]
# days_rest deliberately excluded -- many builders compute it from statcast already.
_SCHED_COLS = ["travel_miles", "home_away_streak", "series_game_num"]
_HOOKS_COLS = ["avg_starter_outs_L30", "pct_quick_hooks_L30", "pct_quality_starts_L30"]
_POP_COLS   = ["maxeff_arm_2b_3b_sba", "exchange_2b_3b_sba",
               "pop_2b_sba", "pop_2b_cs", "pop_2b_sb",
               "pop_3b_sba", "pop_3b_cs", "pop_3b_sb"]


def _safe_int(series: pd.Series) -> pd.Series:
    """Cast to nullable Int64 so int/float mismatches don't break merges."""
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _year_from(df: pd.DataFrame, date_col: str) -> pd.Series:
    return pd.to_datetime(df[date_col], errors="coerce").dt.year.astype("Int64")


# ---------------------------------------------------------------------------
# 1. Pitcher-grain joins
# ---------------------------------------------------------------------------

def join_pitcher_aux(
    df: pd.DataFrame,
    game_date_col: str = "game_date",
    player_name_col: str | None = "player_name",
    pitcher_col: str = "pitcher",
    game_pk_col: str = "game_pk",
    home_team_col: str = "home_team",
    away_team_col: str = "away_team",
    pitcher_is_home_col: str | None = None,
    pitcher_team_col: str | None = None,
) -> pd.DataFrame:
    """Attach bref, swing_take, team_schedule, and manager_hooks to a pitcher-game DataFrame.

    Pitcher team resolution (first match wins):
      1. pitcher_team_col   -- explicit pre-computed column
      2. pitcher_is_home_col + home/away_team -- derive from is_home flag
      3. Neither provided   -- schedule/hooks joins are skipped

    Usage (NRFI):
        df = join_pitcher_aux(df, pitcher_is_home_col="pitcher_is_home")

    Usage (K):
        df = join_pitcher_aux(pf, player_name_col="player_name", pitcher_is_home_col="is_home")
    """
    from mlb_core.data.auxiliary_features import (
        load_fangraphs_pitching, load_swing_take,
        load_team_schedule, load_manager_hooks, norm_statcast_name,
    )

    year = _year_from(df, game_date_col)

    # -- bref: FIP, WHIP, SO9, BB9 -----------------------------------------
    if player_name_col and player_name_col in df.columns:
        try:
            bref = load_fangraphs_pitching()
            if not bref.empty:
                bref_cols = [c for c in _BREF_COLS if c in bref.columns]
                if bref_cols:
                    df = df.copy()
                    df["_bref_key"] = df[player_name_col].apply(norm_statcast_name)
                    df["_aux_year"] = year
                    bref_slim = (
                        bref[["name_norm", "year"] + bref_cols]
                        .rename(columns={"name_norm": "_bref_key", "year": "_aux_year"})
                        .drop_duplicates(subset=["_bref_key", "_aux_year"])
                    )
                    bref_slim["_aux_year"] = _safe_int(bref_slim["_aux_year"])
                    df["_aux_year"] = _safe_int(df["_aux_year"])
                    df = df.merge(bref_slim, on=["_bref_key", "_aux_year"], how="left")
                    df = df.drop(columns=["_bref_key", "_aux_year"], errors="ignore")
                    nan_pct = df["FIP"].isna().mean() if "FIP" in df.columns else float("nan")
                    logger.info("aux_joins: bref join -- FIP NaN=%.1f%%", 100 * nan_pct)
        except Exception as exc:
            logger.warning("aux_joins: bref join failed (non-fatal): %s", exc)

    # -- derive pitcher_team -------------------------------------------------
    pitcher_team: pd.Series | None = None
    if pitcher_team_col and pitcher_team_col in df.columns:
        pitcher_team = df[pitcher_team_col]
    elif (pitcher_is_home_col and pitcher_is_home_col in df.columns
          and home_team_col in df.columns and away_team_col in df.columns):
        pitcher_team = np.where(
            df[pitcher_is_home_col].astype(float).fillna(0).astype(int) == 1,
            df[home_team_col],
            df[away_team_col],
        )

    if pitcher_team is None:
        logger.debug("aux_joins: pitcher_team not resolvable -- schedule/hooks skipped")
        return df

    df = df.copy()
    df["_pitcher_team"] = pitcher_team

    # -- team_schedule: travel_miles, home_away_streak, series_game_num -----
    try:
        sched = load_team_schedule()
        if not sched.empty:
            sched_cols = [c for c in _SCHED_COLS if c in sched.columns]
            if sched_cols:
                sched_slim = (
                    sched[["team", "game_pk"] + sched_cols]
                    .rename(columns={"team": "_pitcher_team"})
                    .drop_duplicates(subset=["_pitcher_team", "game_pk"])
                )
                sched_slim["game_pk"] = _safe_int(sched_slim["game_pk"])
                df[game_pk_col] = _safe_int(df[game_pk_col])
                df = df.merge(sched_slim, on=["_pitcher_team", game_pk_col], how="left")
                nan_pct = df["travel_miles"].isna().mean() if "travel_miles" in df.columns else float("nan")
                logger.info("aux_joins: team_schedule join -- travel_miles NaN=%.1f%%", 100 * nan_pct)
    except Exception as exc:
        logger.warning("aux_joins: team_schedule join failed (non-fatal): %s", exc)

    # -- manager_hooks -------------------------------------------------------
    try:
        hooks = load_manager_hooks()
        if not hooks.empty:
            hook_cols = [c for c in _HOOKS_COLS if c in hooks.columns]
            if hook_cols:
                hooks_slim = (
                    hooks[["team", "game_pk"] + hook_cols]
                    .rename(columns={"team": "_pitcher_team"})
                    .drop_duplicates(subset=["_pitcher_team", "game_pk"])
                )
                hooks_slim["game_pk"] = _safe_int(hooks_slim["game_pk"])
                df[game_pk_col] = _safe_int(df[game_pk_col])
                df = df.merge(hooks_slim, on=["_pitcher_team", game_pk_col], how="left")
                nan_pct = df["avg_starter_outs_L30"].isna().mean() if "avg_starter_outs_L30" in df.columns else float("nan")
                logger.info("aux_joins: manager_hooks join -- avg_starter_outs NaN=%.1f%%", 100 * nan_pct)
    except Exception as exc:
        logger.warning("aux_joins: manager_hooks join failed (non-fatal): %s", exc)

    df = df.drop(columns=["_pitcher_team"], errors="ignore")
    return df


# ---------------------------------------------------------------------------
# 2. Game-grain joins  (F5, GAME)
# ---------------------------------------------------------------------------

def join_game_aux(
    df: pd.DataFrame,
    home_team_col: str = "home_team",
    away_team_col: str = "away_team",
    game_pk_col: str = "game_pk",
    game_date_col: str = "game_date",
    home_pitcher_col: str | None = "home_pitcher",
    away_pitcher_col: str | None = "away_pitcher",
) -> pd.DataFrame:
    """Attach auxiliary features to a game-grain DataFrame.

    Joins team_schedule and manager_hooks for both home and away sides.
    If home_pitcher_col / away_pitcher_col are present, also joins swing_take
    for each side with home_/away_ prefixes.

    Usage (F5):
        gf = join_game_aux(gf, home_pitcher_col="home_pitcher", away_pitcher_col="away_pitcher")

    Usage (GAME -- no pitcher IDs in final mf):
        mf = join_game_aux(mf, home_pitcher_col=None, away_pitcher_col=None)
    """
    from mlb_core.data.auxiliary_features import load_team_schedule, load_manager_hooks

    # -- team_schedule (both sides) -----------------------------------------
    try:
        sched = load_team_schedule()
        if not sched.empty:
            sched_cols = [c for c in _SCHED_COLS if c in sched.columns]
            if sched_cols:
                for side, team_col in (("home", home_team_col), ("away", away_team_col)):
                    if team_col not in df.columns:
                        continue
                    sched_slim = (
                        sched[["team", "game_pk"] + sched_cols]
                        .rename(columns={"team": team_col, **{c: f"{side}_sched_{c}" for c in sched_cols}})
                        .drop_duplicates(subset=[team_col, "game_pk"])
                    )
                    sched_slim["game_pk"] = _safe_int(sched_slim["game_pk"])
                    df = df.copy()
                    df[game_pk_col] = _safe_int(df[game_pk_col])
                    df = df.merge(sched_slim, on=[team_col, game_pk_col], how="left")
                logger.info("aux_joins: game team_schedule joined (home + away)")
    except Exception as exc:
        logger.warning("aux_joins: game team_schedule join failed (non-fatal): %s", exc)

    # -- manager_hooks (both sides) -----------------------------------------
    try:
        hooks = load_manager_hooks()
        if not hooks.empty:
            hook_cols = [c for c in _HOOKS_COLS if c in hooks.columns]
            if hook_cols:
                for side, team_col in (("home", home_team_col), ("away", away_team_col)):
                    if team_col not in df.columns:
                        continue
                    hooks_slim = (
                        hooks[["team", "game_pk"] + hook_cols]
                        .rename(columns={"team": team_col, **{c: f"{side}_hooks_{c}" for c in hook_cols}})
                        .drop_duplicates(subset=[team_col, "game_pk"])
                    )
                    hooks_slim["game_pk"] = _safe_int(hooks_slim["game_pk"])
                    df = df.copy()
                    df[game_pk_col] = _safe_int(df[game_pk_col])
                    df = df.merge(hooks_slim, on=[team_col, game_pk_col], how="left")
                logger.info("aux_joins: game manager_hooks joined (home + away)")
    except Exception as exc:
        logger.warning("aux_joins: game manager_hooks join failed (non-fatal): %s", exc)

    return df


# ---------------------------------------------------------------------------
# 3. Batter-grain joins  (HR, BATTER_HITS, BATTER_TB)
# ---------------------------------------------------------------------------

def join_batter_aux(
    df: pd.DataFrame,
    batter_col: str | None = None,
    opp_pitcher_col: str = "opp_pitcher_id",
    home_team_col: str = "home_team",
    away_team_col: str = "away_team",
    game_pk_col: str = "game_pk",
    game_date_col: str = "game_date",
) -> pd.DataFrame:
    """Attach auxiliary features to a batter-game DataFrame.

    - swing_take (batter own stats): batter_runs_chase/heart/shadow/waste
      join on (batter MLBAM, year)  -- requires batter_col
    - team_schedule: home_sched_* / away_sched_* for both sides
      join on (home_team / away_team, game_pk)

    Usage:
        df = join_batter_aux(df, batter_col="batter")
    """
    from mlb_core.data.auxiliary_features import load_swing_take, load_team_schedule

    year = _year_from(df, game_date_col)

    # -- batter swing_take (batter's own swing/take tendencies) -------------
    if batter_col and batter_col in df.columns:
        try:
            st = load_swing_take()
            if not st.empty and "player_id" in st.columns:
                st_cols = [c for c in _ST_COLS if c in st.columns]
                if st_cols:
                    df = df.copy()
                    df["_aux_year"] = _safe_int(year)
                    st_slim = (
                        st[["player_id", "year"] + st_cols]
                        .rename(columns={
                            "player_id": "_st_pid",
                            "year": "_aux_year",
                            **{c: f"batter_{c}" for c in st_cols},
                        })
                        .drop_duplicates(subset=["_st_pid", "_aux_year"])
                    )
                    st_slim["_st_pid"] = _safe_int(st_slim["_st_pid"])
                    st_slim["_aux_year"] = _safe_int(st_slim["_aux_year"])
                    df["_st_pid"] = _safe_int(df[batter_col])
                    df = df.merge(st_slim, on=["_st_pid", "_aux_year"], how="left")
                    df = df.drop(columns=["_st_pid", "_aux_year"], errors="ignore")
                    nan_pct = df["batter_runs_chase"].isna().mean() if "batter_runs_chase" in df.columns else float("nan")
                    logger.info("aux_joins: batter swing_take join -- batter_runs_chase NaN=%.1f%%", 100 * nan_pct)
        except Exception as exc:
            logger.warning("aux_joins: batter swing_take join failed (non-fatal): %s", exc)

    # -- team schedule (both sides) -----------------------------------------
    try:
        sched = load_team_schedule()
        if not sched.empty:
            sched_cols = [c for c in _SCHED_COLS if c in sched.columns]
            if sched_cols:
                for side, team_col in (("home", home_team_col), ("away", away_team_col)):
                    if team_col not in df.columns:
                        continue
                    sched_slim = (
                        sched[["team", "game_pk"] + sched_cols]
                        .rename(columns={"team": team_col, **{c: f"{side}_sched_{c}" for c in sched_cols}})
                        .drop_duplicates(subset=[team_col, "game_pk"])
                    )
                    sched_slim["game_pk"] = _safe_int(sched_slim["game_pk"])
                    df = df.copy()
                    df[game_pk_col] = _safe_int(df[game_pk_col])
                    df = df.merge(sched_slim, on=[team_col, game_pk_col], how="left")
                logger.info("aux_joins: batter team_schedule joined (home + away)")
    except Exception as exc:
        logger.warning("aux_joins: batter team_schedule join failed (non-fatal): %s", exc)

    return df


# ---------------------------------------------------------------------------
# 4. Catcher-grain join (SB only) -- the opposing catcher's own arm/pop-time
# ---------------------------------------------------------------------------

def join_catcher_aux(
    df: pd.DataFrame,
    opp_catcher_col: str = "opp_catcher_id",
    game_date_col: str = "game_date",
) -> pd.DataFrame:
    """Attach the OPPOSING catcher's pop time / arm strength to a batter-game
    DataFrame. Added for the SB model -- no existing system (HR, BATTER_HITS,
    BATTER_TB) needed a third player-entity on the row, so this is new,
    not a variant of join_batter_aux().

    Caller must have already resolved `opp_catcher_col` (the opposing
    team's starting catcher MLBAM id for that game) -- e.g. via
    mlb_core.data.lineups.get_starting_catchers(game_pk) at build time.
    This function only does the (player_id, year) -> stats join, exactly
    like join_pitcher_aux()'s bref join.

    Columns attached (see mlb_core.data.auxiliary_features.load_catcher_poptime):
      catcher_maxeff_arm_2b_3b_sba, catcher_exchange_2b_3b_sba,
      catcher_pop_2b_sba, catcher_pop_2b_cs, catcher_pop_2b_sb,
      catcher_pop_3b_sba, catcher_pop_3b_cs, catcher_pop_3b_sb

    Usage:
        df = join_catcher_aux(df, opp_catcher_col="opp_catcher_id")
    """
    from mlb_core.data.auxiliary_features import load_catcher_poptime

    if not opp_catcher_col or opp_catcher_col not in df.columns:
        logger.debug("aux_joins: %s not present -- catcher join skipped", opp_catcher_col)
        return df

    year = _year_from(df, game_date_col)

    try:
        pop = load_catcher_poptime()
        if pop.empty or "player_id" not in pop.columns:
            logger.warning("aux_joins: catcher_poptime master empty/missing -- join skipped")
            return df
        pop_cols = [c for c in _POP_COLS if c in pop.columns]
        if not pop_cols:
            logger.warning("aux_joins: no expected catcher_poptime columns found -- join skipped")
            return df

        df = df.copy()
        df["_aux_year"] = _safe_int(year)
        pop_slim = (
            pop[["player_id", "year"] + pop_cols]
            .rename(columns={
                "player_id": "_catcher_pid",
                "year": "_aux_year",
                **{c: f"catcher_{c}" for c in pop_cols},
            })
            .drop_duplicates(subset=["_catcher_pid", "_aux_year"])
        )
        pop_slim["_catcher_pid"] = _safe_int(pop_slim["_catcher_pid"])
        pop_slim["_aux_year"] = _safe_int(pop_slim["_aux_year"])
        df["_catcher_pid"] = _safe_int(df[opp_catcher_col])
        df = df.merge(pop_slim, on=["_catcher_pid", "_aux_year"], how="left")
        df = df.drop(columns=["_catcher_pid", "_aux_year"], errors="ignore")
        nan_pct = df["catcher_pop_2b_sba"].isna().mean() if "catcher_pop_2b_sba" in df.columns else float("nan")
        logger.info("aux_joins: catcher_poptime join -- catcher_pop_2b_sba NaN=%.1f%%", 100 * nan_pct)
    except Exception as exc:
        logger.warning("aux_joins: catcher_poptime join failed (non-fatal): %s", exc)

    return df
