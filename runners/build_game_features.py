"""
runners/build_game_features.py -- GAME Pro v1 nightly feature builder.

Builds per-game team-level features from Statcast for the full-game home win
binary classifier (target: home_win). Key differentiator from the F5 system:
dedicated bullpen features (xwOBA allowed, K%, BB%, fatigue IP) that F5 ignores.

Output GCS keys (matches GAME_Pro_System/config_game.py):
  - GAME_Pro_System/data/starter_game_features.csv   (starter rolling)
  - GAME_Pro_System/data/bullpen_game_features.csv   (bullpen rolling)
  - GAME_Pro_System/data/team_offense_features.csv   (offense rolling)
  - GAME_Pro_System/data/model_features.csv          (joined, used by retrain + runner)

Entrypoint: run(run_date) -- called by main.py for {"system": "GAME"}.
"""
from __future__ import annotations

import logging
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# -- Park run factors ----------------------------------------------------------
# Run-scoring park factors (not hits). Used as game-level context feature.
PARK_FACTORS = {
    "COL": 1.12, "BOS": 1.08, "CIN": 1.07, "TEX": 1.06, "MIL": 1.05,
    "PHI": 1.04, "BAL": 1.03, "NYY": 1.03, "ATL": 1.02, "HOU": 1.02,
    "STL": 1.01, "WSH": 1.01, "LAD": 1.00, "NYM": 1.00, "TOR": 1.00,
    "MIN": 1.00, "DET": 0.99, "CLE": 0.99, "OAK": 0.99, "CHC": 0.99,
    "LAA": 0.98, "PIT": 0.98, "MIA": 0.97, "SEA": 0.97, "CHW": 0.97,
    "KC":  0.97, "ARI": 0.96, "SDP": 0.96, "SFG": 0.95, "TB":  0.95,
}

# Teams that play in a dome or retractable roof (no weather impact)
DOME_TEAMS = {"TB", "HOU", "MIA", "SEA", "ARI", "MIL"}

# Canonical abbreviation normalisation -- mirrors build_batter_hits_features.py
TEAM_NAME_TO_ABBR = {
    "ARI": "ARI", "AZ": "ARI",  "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE", "COL": "COL",
    "DET": "DET", "HOU": "HOU", "KC":  "KC",  "LAA": "LAA", "LAD": "LAD",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NYM": "NYM", "NYY": "NYY",
    "OAK": "OAK", "PHI": "PHI", "PIT": "PIT", "SD":  "SDP", "SF":  "SFG",
    "SEA": "SEA", "STL": "STL", "TB":  "TB",  "TEX": "TEX", "TOR": "TOR",
    "WSH": "WSH", "WAS": "WSH", "ATH": "OAK",
    "SDP": "SDP", "SFG": "SFG", "CHW": "CHW",
    "Arizona Diamondbacks":  "ARI", "Atlanta Braves":        "ATL",
    "Baltimore Orioles":     "BAL", "Boston Red Sox":         "BOS",
    "Chicago Cubs":          "CHC", "Chicago White Sox":      "CWS",
    "Cincinnati Reds":       "CIN", "Cleveland Guardians":    "CLE",
    "Cleveland Indians":     "CLE", "Colorado Rockies":       "COL",
    "Detroit Tigers":        "DET", "Houston Astros":         "HOU",
    "Kansas City Royals":    "KC",  "Los Angeles Angels":     "LAA",
    "Los Angeles Dodgers":   "LAD", "Miami Marlins":          "MIA",
    "Milwaukee Brewers":     "MIL", "Minnesota Twins":        "MIN",
    "New York Mets":         "NYM", "New York Yankees":       "NYY",
    "Oakland Athletics":     "OAK", "Athletics":              "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates":     "PIT",
    "San Diego Padres":      "SDP", "San Francisco Giants":   "SFG",
    "Seattle Mariners":      "SEA", "St. Louis Cardinals":    "STL",
    "Tampa Bay Rays":        "TB",  "Texas Rangers":          "TEX",
    "Toronto Blue Jays":     "TOR", "Washington Nationals":   "WSH",
}

K_EVENTS  = frozenset(["strikeout", "strikeout_double_play"])
BB_EVENTS = frozenset(["walk", "hit_by_pitch"])


# -- Section 1: Statcast load -------------------------------------------------

def _load_statcast(lookback_days: int = 90, run_date: str | None = None) -> pd.DataFrame:
    from mlb_core.storage import read_csv
    df = read_csv("Statcast/statcast_master.csv", low_memory=False)

    if "bat_speed" in df.columns:
        df = df.drop(columns=["bat_speed"])

    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    _ref    = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff  = _ref - pd.Timedelta(days=lookback_days)
    df      = df[df["game_date"] >= cutoff].copy()

    logger.info(
        "GAME build: statcast %d rows | %s -> %s | %d pitchers | %d games",
        len(df),
        df["game_date"].min().date() if not df.empty else "?",
        df["game_date"].max().date() if not df.empty else "?",
        df["pitcher"].nunique() if not df.empty else 0,
        df["game_pk"].nunique() if not df.empty else 0,
    )
    return df


# -- Section 2: Identify starter per team per game ----------------------------

def _identify_starters(sc: pd.DataFrame) -> pd.DataFrame:
    """
    Return DataFrame with columns (game_pk, inning_topbot, starter_id).

    A starter is the pitcher with the lowest at_bat_number who appears in
    inning 1. Uses inning_topbot to separate home/away starters:
      Top = away starter pitching, Bot = home starter pitching.

    Falls back to lowest at_bat_number per (game_pk, pitcher) if
    inning_topbot is null (matches the F5 null-fix pattern).
    """
    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"], errors="coerce")

    # Null inning_topbot fix (mirrors build_f5_features.py)
    null_mask = sc["inning_topbot"].isna() if "inning_topbot" in sc.columns else pd.Series(True, index=sc.index)
    if "inning_topbot" not in sc.columns:
        sc["inning_topbot"] = np.nan
    if null_mask.any():
        logger.info("GAME: fixing %d null inning_topbot rows", null_mask.sum())
        sc_sorted  = sc.sort_values(["game_pk", "at_bat_number"])
        first_ab   = (sc_sorted.groupby(["game_pk", "pitcher"])["at_bat_number"]
                               .min().reset_index())
        game_first = (sc_sorted.groupby("game_pk")["at_bat_number"]
                                .min().reset_index()
                                .rename(columns={"at_bat_number": "game_first_ab"}))
        first_ab   = first_ab.merge(game_first, on="game_pk")
        first_ab["inferred"] = np.where(
            first_ab["at_bat_number"] == first_ab["game_first_ab"], "Top", "Bot"
        )
        topbot_map = first_ab.set_index(["game_pk", "pitcher"])["inferred"].to_dict()
        sc.loc[null_mask, "inning_topbot"] = sc[null_mask].apply(
            lambda r: topbot_map.get((r["game_pk"], r["pitcher"]), np.nan), axis=1
        )

    # Restrict to inning 1 to find the true starter
    inn1 = sc[sc.get("inning", sc.get("inning_topbot", pd.Series(dtype=str))).notna()].copy()
    if "inning" in inn1.columns:
        inn1 = inn1[inn1["inning"] == 1]

    starters = (
        inn1.sort_values(["game_pk", "inning_topbot", "at_bat_number"])
            .dropna(subset=["inning_topbot"])
            .groupby(["game_pk", "inning_topbot"])["pitcher"]
            .first()
            .reset_index()
            .rename(columns={"pitcher": "starter_id"})
    )
    return starters


# -- Section 3: Starter features ----------------------------------------------

def build_starter_features(
    sc: pd.DataFrame,
    existing_home: pd.DataFrame,
    existing_away: pd.DataFrame,
    lookback_days: int = 90,
    run_date: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Per-game starter rolling features with shift(1) to prevent leakage.

    For each starting pitcher per game:
      - K% = strikeout events / total PA
      - BB% = (walk + HBP) / total PA
      - xwOBA = mean estimated_woba_using_speedangle
      - velo = mean release_speed
      - IP proxy = PA / 3 per start

    Rolls over L3 starts (K%, xwOBA, velo, BB%) and L5 starts (IP avg).
    Returns (home_starter_df, away_starter_df) with home_/away_ prefixes.

    Columns per row: game_date, game_pk, pitcher, team,
      home_k_pct_L3, home_xwoba_allowed_L3, home_velo_mean_L3,
      home_bb_pct_L3, home_starter_ip_avg_L5, home_starter_days_rest
    (or away_ prefix for the away df).
    """
    logger.info("GAME: building starter features...")

    _ref   = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)

    keep_home = existing_home[existing_home["game_date"] < cutoff].copy() \
                if existing_home is not None and not existing_home.empty else pd.DataFrame()
    keep_away = existing_away[existing_away["game_date"] < cutoff].copy() \
                if existing_away is not None and not existing_away.empty else pd.DataFrame()
    logger.info("  keeping %d home / %d away rows outside %dd window",
                len(keep_home), len(keep_away), lookback_days)

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"], errors="coerce")

    # Filter to PA events only
    pa = sc[sc["events"].notna()].copy()
    if pa.empty:
        return existing_home if existing_home is not None else pd.DataFrame(), \
               existing_away if existing_away is not None else pd.DataFrame()

    pa["k"]         = pa["events"].isin(K_EVENTS).astype(int)
    pa["bb"]        = pa["events"].isin(BB_EVENTS).astype(int)
    pa["xwoba"]     = pd.to_numeric(pa.get("estimated_woba_using_speedangle", pd.Series(dtype=float)), errors="coerce")
    pa["velo"]      = pd.to_numeric(pa.get("release_speed", pd.Series(dtype=float)), errors="coerce")

    starters = _identify_starters(sc)
    # Merge starter label onto PA rows
    pa = pa.merge(starters, on=["game_pk", "inning_topbot"], how="left")
    starter_pa = pa[pa["pitcher"] == pa["starter_id"]].copy()

    if starter_pa.empty:
        logger.warning("GAME: no starter PA rows found")
        return keep_home, keep_away

    # Per game-pitcher: aggregate
    agg = (
        starter_pa.groupby(["game_date", "game_pk", "pitcher", "inning_topbot"])
        .agg(
            pa_count   = ("events", "count"),
            k_sum      = ("k", "sum"),
            bb_sum     = ("bb", "sum"),
            xwoba_mean = ("xwoba", "mean"),
            velo_mean  = ("velo", "mean"),
        )
        .reset_index()
    )
    agg["ip_proxy"]  = agg["pa_count"] / 3.0
    agg["k_pct"]     = agg["k_sum"] / agg["pa_count"].clip(lower=1)
    agg["bb_pct"]    = agg["bb_sum"] / agg["pa_count"].clip(lower=1)

    # inning_topbot: Top = away pitcher, Bot = home pitcher
    agg["side"] = agg["inning_topbot"].map({"Top": "away", "Bot": "home"})
    agg = agg.dropna(subset=["side"])
    agg = agg.sort_values(["pitcher", "game_date"]).reset_index(drop=True)

    # Days rest: days since last start per pitcher
    agg["prev_start"] = agg.groupby("pitcher")["game_date"].shift(1)
    agg["days_rest"]  = (agg["game_date"] - agg["prev_start"]).dt.days.fillna(5.0)

    # Rolling per pitcher (shift(1) prevents leakage)
    def _roll_pitcher(grp: pd.DataFrame) -> pd.DataFrame:
        grp = grp.sort_values("game_date").copy()
        for metric, window, suffix in [
            ("k_pct",     3, "k_pct_L3"),
            ("xwoba_mean", 3, "xwoba_allowed_L3"),
            ("velo_mean",  3, "velo_mean_L3"),
            ("bb_pct",    3, "bb_pct_L3"),
            ("ip_proxy",  5, "starter_ip_avg_L5"),
        ]:
            if metric in grp.columns:
                grp[suffix] = (
                    grp[metric].shift(1)
                               .rolling(window, min_periods=1)
                               .mean()
                )
            else:
                grp[suffix] = np.nan
        grp["starter_days_rest"] = grp["days_rest"].shift(1).fillna(5.0)
        return grp

    rolled = agg.groupby("pitcher", group_keys=False).apply(_roll_pitcher)

    feat_cols = [
        "game_date", "game_pk", "pitcher", "side",
        "k_pct_L3", "xwoba_allowed_L3", "velo_mean_L3",
        "bb_pct_L3", "starter_ip_avg_L5", "starter_days_rest",
    ]
    available = [c for c in feat_cols if c in rolled.columns]
    rolled = rolled[available].copy()

    home_df = rolled[rolled["side"] == "home"].copy()
    away_df = rolled[rolled["side"] == "away"].copy()

    # Rename to home_/away_ prefix
    rename_home = {
        "k_pct_L3":         "home_k_pct_L3",
        "xwoba_allowed_L3": "home_xwoba_allowed_L3",
        "velo_mean_L3":     "home_velo_mean_L3",
        "bb_pct_L3":        "home_bb_pct_L3",
        "starter_ip_avg_L5":"home_starter_ip_avg_L5",
        "starter_days_rest":"home_starter_days_rest",
    }
    rename_away = {k: k.replace("home_", "away_") for k in rename_home}
    rename_away_map = {
        "k_pct_L3":         "away_k_pct_L3",
        "xwoba_allowed_L3": "away_xwoba_allowed_L3",
        "velo_mean_L3":     "away_velo_mean_L3",
        "bb_pct_L3":        "away_bb_pct_L3",
        "starter_ip_avg_L5":"away_starter_ip_avg_L5",
        "starter_days_rest":"away_starter_days_rest",
    }
    home_df = home_df.rename(columns=rename_home)
    away_df = away_df.rename(columns=rename_away_map)
    home_df = home_df.drop(columns=["side"], errors="ignore")
    away_df = away_df.drop(columns=["side"], errors="ignore")

    # Merge with kept history
    if not keep_home.empty:
        home_df = pd.concat([keep_home, home_df], ignore_index=True)
    if not keep_away.empty:
        away_df = pd.concat([keep_away, away_df], ignore_index=True)

    home_df = home_df.drop_duplicates(subset=["game_pk", "pitcher"]).reset_index(drop=True)
    away_df = away_df.drop_duplicates(subset=["game_pk", "pitcher"]).reset_index(drop=True)

    logger.info("GAME starter features: %d home rows, %d away rows",
                len(home_df), len(away_df))
    return home_df, away_df


# -- Section 4: Bullpen features (KEY differentiator from F5) -----------------

def build_bullpen_features(
    sc: pd.DataFrame,
    lookback_days: int = 90,
    run_date: str | None = None,
) -> pd.DataFrame:
    """
    Per-team per-game rolling bullpen features with shift(1) to prevent leakage.

    Bullpen = all pitchers for that team in a game EXCEPT the identified starter.
    Aggregates per team per game:
      - bullpen_k_pct: K rate
      - bullpen_bb_pct: BB+HBP rate
      - bullpen_xwoba: mean xwOBA allowed
    Rolling windows:
      - L14 days for rate metrics (enough relief appearances)
      - L7 days for IP (fatigue proxy)

    Output: one row per (game_date, game_pk, team) with:
      bullpen_xwoba_L14, bullpen_k_pct_L14, bullpen_bb_pct_L14, bullpen_ip_L7
    """
    logger.info("GAME: building bullpen features...")

    _ref   = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"], errors="coerce")
    sc_window = sc[sc["game_date"] >= cutoff].copy()

    if sc_window.empty:
        logger.warning("GAME: bullpen window is empty")
        return pd.DataFrame()

    pa = sc_window[sc_window["events"].notna()].copy()
    if pa.empty:
        return pd.DataFrame()

    pa["k"]     = pa["events"].isin(K_EVENTS).astype(int)
    pa["bb"]    = pa["events"].isin(BB_EVENTS).astype(int)
    pa["xwoba"] = pd.to_numeric(pa.get("estimated_woba_using_speedangle", pd.Series(dtype=float)), errors="coerce")

    starters = _identify_starters(sc_window)
    # Build per-(game_pk, pitcher) starter flag
    starter_set: set = set()
    for _, row in starters.iterrows():
        starter_set.add((int(row["game_pk"]), int(row["starter_id"])))

    pa["is_starter"] = pa.apply(
        lambda r: (int(r["game_pk"]), int(r["pitcher"])) in starter_set, axis=1
    )
    bullpen_pa = pa[~pa["is_starter"]].copy()

    # Need pitcher team. Use inning_topbot:
    # Top = away pitcher is pitching -> team = away_team
    # Bot = home pitcher is pitching -> team = home_team
    # Try to get team from the 'fielding_team' column if present, else infer.
    if "fielding_team" in bullpen_pa.columns:
        bullpen_pa["team"] = bullpen_pa["fielding_team"]
    elif all(c in bullpen_pa.columns for c in ["home_team", "away_team", "inning_topbot"]):
        bullpen_pa["team"] = np.where(
            bullpen_pa["inning_topbot"] == "Top",
            bullpen_pa["away_team"],
            bullpen_pa["home_team"],
        )
    else:
        logger.warning("GAME: cannot determine bullpen team; bullpen features will be empty")
        return pd.DataFrame()

    # Per game-team: aggregate bullpen PA
    game_agg = (
        bullpen_pa.groupby(["game_date", "game_pk", "team"])
        .agg(
            bp_pa    = ("events", "count"),
            bp_k     = ("k", "sum"),
            bp_bb    = ("bb", "sum"),
            bp_xwoba = ("xwoba", "mean"),
        )
        .reset_index()
    )
    game_agg["bp_ip"]     = game_agg["bp_pa"] / 3.0
    game_agg["bp_k_pct"]  = game_agg["bp_k"] / game_agg["bp_pa"].clip(lower=1)
    game_agg["bp_bb_pct"] = game_agg["bp_bb"] / game_agg["bp_pa"].clip(lower=1)

    game_agg = game_agg.sort_values(["team", "game_date"]).reset_index(drop=True)

    # Rolling per team with shift(1) -- date-based windows
    def _roll_team(grp: pd.DataFrame) -> pd.DataFrame:
        grp = grp.sort_values("game_date").copy()
        grp["bullpen_k_pct_L14"]  = (
            grp["bp_k_pct"].shift(1)
                           .rolling(14, min_periods=1)
                           .mean()
        )
        grp["bullpen_bb_pct_L14"] = (
            grp["bp_bb_pct"].shift(1)
                            .rolling(14, min_periods=1)
                            .mean()
        )
        grp["bullpen_xwoba_L14"]  = (
            grp["bp_xwoba"].shift(1)
                           .rolling(14, min_periods=1)
                           .mean()
        )
        # Fatigue: sum IP over last 7 games (approx 7 days for daily schedule)
        grp["bullpen_ip_L7"] = (
            grp["bp_ip"].shift(1)
                        .rolling(7, min_periods=1)
                        .sum()
        )
        return grp

    rolled = game_agg.groupby("team", group_keys=False).apply(_roll_team)

    out_cols = [
        "game_date", "game_pk", "team",
        "bullpen_xwoba_L14", "bullpen_k_pct_L14",
        "bullpen_bb_pct_L14", "bullpen_ip_L7",
    ]
    available = [c for c in out_cols if c in rolled.columns]
    rolled    = rolled[available].drop_duplicates(subset=["game_pk", "team"]).reset_index(drop=True)

    logger.info("GAME bullpen features: %d rows, %d teams, %d games",
                len(rolled), rolled["team"].nunique(), rolled["game_pk"].nunique())
    return rolled


# -- Section 5: Team offense features ----------------------------------------

def build_team_offense_features(
    sc: pd.DataFrame,
    lookback_days: int = 90,
    run_date: str | None = None,
) -> pd.DataFrame:
    """
    Per-team per-game rolling offensive features with shift(1) to prevent leakage.

    Uses batting-side events (estimated_woba_using_speedangle not null as proxy).
    Per team per game:
      - team_woba_L20: mean xwOBA (proxy for wOBA) over last 20 games
      - team_k_pct_L20: K% over last 20 games
      - team_run_diff_L20: mean run differential over last 20 games

    Output: one row per (game_date, game_pk, team) with rolling metrics.
    """
    logger.info("GAME: building team offense features...")

    _ref   = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"], errors="coerce")
    sc_window = sc[sc["game_date"] >= cutoff].copy()

    if sc_window.empty:
        logger.warning("GAME: offense window is empty")
        return pd.DataFrame()

    # Batting events (PA) -- use events notna + exclude intentional pass
    pa = sc_window[sc_window["events"].notna()].copy()
    if pa.empty:
        return pd.DataFrame()

    pa["k"]     = pa["events"].isin(K_EVENTS).astype(int)
    pa["bb"]    = pa["events"].isin(BB_EVENTS).astype(int)
    pa["xwoba"] = pd.to_numeric(pa.get("estimated_woba_using_speedangle", pd.Series(dtype=float)), errors="coerce")

    # Batting team: inning_topbot Top = away batting, Bot = home batting
    if "batting_team" in pa.columns:
        pa["team"] = pa["batting_team"]
    elif all(c in pa.columns for c in ["home_team", "away_team", "inning_topbot"]):
        pa["team"] = np.where(
            pa["inning_topbot"] == "Top",
            pa["away_team"],
            pa["home_team"],
        )
    else:
        logger.warning("GAME: cannot determine batting team; offense features will be empty")
        return pd.DataFrame()

    # Per game-team aggregate
    game_agg = (
        pa.groupby(["game_date", "game_pk", "team"])
        .agg(
            pa_count   = ("events", "count"),
            k_sum      = ("k", "sum"),
            xwoba_mean = ("xwoba", "mean"),
        )
        .reset_index()
    )
    game_agg["off_k_pct"] = game_agg["k_sum"] / game_agg["pa_count"].clip(lower=1)

    # Run differential: try to extract from scoring columns or default 0
    # scoring_master is joined downstream; here we default run_diff to 0
    # and let build_model_features overlay it from scoring data if available.
    game_agg["run_diff"] = 0.0

    game_agg = game_agg.sort_values(["team", "game_date"]).reset_index(drop=True)

    def _roll_team(grp: pd.DataFrame) -> pd.DataFrame:
        grp = grp.sort_values("game_date").copy()
        grp["team_woba_L20"]  = (
            grp["xwoba_mean"].shift(1)
                             .rolling(20, min_periods=1)
                             .mean()
        )
        grp["team_k_pct_L20"] = (
            grp["off_k_pct"].shift(1)
                            .rolling(20, min_periods=1)
                            .mean()
        )
        grp["run_diff_L20"]   = (
            grp["run_diff"].shift(1)
                           .rolling(20, min_periods=1)
                           .mean()
        )
        return grp

    rolled = game_agg.groupby("team", group_keys=False).apply(_roll_team)

    out_cols = [
        "game_date", "game_pk", "team",
        "team_woba_L20", "team_k_pct_L20", "run_diff_L20",
    ]
    available = [c for c in out_cols if c in rolled.columns]
    rolled    = rolled[available].drop_duplicates(subset=["game_pk", "team"]).reset_index(drop=True)

    logger.info("GAME offense features: %d rows, %d teams", len(rolled), rolled["team"].nunique())
    return rolled


# -- Section 6: Join all sources into model_features --------------------------

def build_model_features(
    starter_home: pd.DataFrame,
    starter_away: pd.DataFrame,
    bullpen: pd.DataFrame,
    offense: pd.DataFrame,
    wx: dict,
    run_date: str | None = None,
    game_meta: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """
    Join starter, bullpen, offense, and weather features into one row per game.

    Args:
        starter_home: output of build_starter_features() home side
        starter_away: output of build_starter_features() away side
        bullpen:      output of build_bullpen_features()
        offense:      output of build_team_offense_features()
        wx:           dict of {game_pk: {temperature_f, wind_speed_mph,
                                         wind_dir, ...}} from weather fetcher
        run_date:     ISO date string (YYYY-MM-DD)

    Returns:
        DataFrame with one row per (game_date, game_pk) and all GAME_FEATURES.
        Also includes home_win target column (NaN for future games).
    """
    if starter_home is None or starter_home.empty:
        logger.warning("GAME build_model_features: starter_home is empty")
        return pd.DataFrame()

    _ref = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()

    # -- Step 1: starter join (home starter has home_team, away has away_team)
    home_s = starter_home.copy()
    away_s = starter_away.copy() if starter_away is not None else pd.DataFrame()

    home_s["game_date"] = pd.to_datetime(home_s["game_date"], errors="coerce")
    if not away_s.empty:
        away_s["game_date"] = pd.to_datetime(away_s["game_date"], errors="coerce")

    # Merge home and away starters on game_pk
    if not away_s.empty:
        merge_cols_away = [c for c in away_s.columns if c in ["game_date", "game_pk"] or c.startswith("away_")]
        away_s_slim = away_s[[c for c in merge_cols_away if c in away_s.columns]].copy()
        mf = home_s.merge(away_s_slim, on=["game_date", "game_pk"], how="outer")
    else:
        mf = home_s.copy()

    # -- Build game_pk -> {home, away} team map (needed for bullpen + offense pivots)
    # Primary source: game_meta passed from run() (extracted from statcast directly).
    # Fallback: home_team / away_team columns on starter DataFrames (usually absent).
    team_map: dict = {}
    if game_meta is not None and not game_meta.empty:
        for _, r in game_meta.iterrows():
            gp = int(r["game_pk"]) if pd.notna(r.get("game_pk")) else -1
            team_map[gp] = {
                "home": str(r.get("home_team") or ""),
                "away": str(r.get("away_team") or ""),
            }
    elif "home_team" in home_s.columns:
        for _, r in home_s.iterrows():
            team_map.setdefault(int(r["game_pk"]) if pd.notna(r["game_pk"]) else -1, {})["home"] = r.get("home_team")
        if not away_s.empty and "away_team" in away_s.columns:
            for _, r in away_s.iterrows():
                team_map.setdefault(int(r["game_pk"]) if pd.notna(r["game_pk"]) else -1, {})["away"] = r.get("away_team")
    logger.info("GAME team_map: %d games populated", len(team_map))

    # -- Step 2: Bullpen join
    if bullpen is not None and not bullpen.empty:
        bullpen["game_date"] = pd.to_datetime(bullpen["game_date"], errors="coerce")

        # Pivot bullpen to home/away columns
        bp_home_rows = []
        bp_away_rows = []
        for _, bp_row in bullpen.iterrows():
            gp   = int(bp_row["game_pk"]) if pd.notna(bp_row["game_pk"]) else -1
            team = bp_row.get("team", "")
            teams = team_map.get(gp, {})
            home_t = teams.get("home", "")
            away_t = teams.get("away", "")
            if team == home_t:
                bp_home_rows.append({
                    "game_pk":              gp,
                    "home_bullpen_xwoba_L14":   bp_row.get("bullpen_xwoba_L14"),
                    "home_bullpen_k_pct_L14":   bp_row.get("bullpen_k_pct_L14"),
                    "home_bullpen_bb_pct_L14":  bp_row.get("bullpen_bb_pct_L14"),
                    "home_bullpen_ip_L7":        bp_row.get("bullpen_ip_L7"),
                })
            elif team == away_t:
                bp_away_rows.append({
                    "game_pk":              gp,
                    "away_bullpen_xwoba_L14":   bp_row.get("bullpen_xwoba_L14"),
                    "away_bullpen_k_pct_L14":   bp_row.get("bullpen_k_pct_L14"),
                    "away_bullpen_bb_pct_L14":  bp_row.get("bullpen_bb_pct_L14"),
                    "away_bullpen_ip_L7":        bp_row.get("bullpen_ip_L7"),
                })

        if bp_home_rows:
            bp_home_df = pd.DataFrame(bp_home_rows)
            mf = mf.merge(bp_home_df, on="game_pk", how="left")
        if bp_away_rows:
            bp_away_df = pd.DataFrame(bp_away_rows)
            mf = mf.merge(bp_away_df, on="game_pk", how="left")

    # -- Step 3: Offense join
    if offense is not None and not offense.empty:
        offense["game_date"] = pd.to_datetime(offense["game_date"], errors="coerce")

        # Pivot offense to home/away columns using team_map
        off_rows = []
        for _, off_row in offense.iterrows():
            gp   = int(off_row["game_pk"]) if pd.notna(off_row["game_pk"]) else -1
            team = off_row.get("team", "")
            teams = team_map.get(gp, {})
            home_t = teams.get("home", "")
            away_t = teams.get("away", "")
            row_d: dict = {"game_pk": gp}
            if team == home_t:
                row_d["home_team_woba_L20"]  = off_row.get("team_woba_L20")
                row_d["home_team_k_pct_L20"] = off_row.get("team_k_pct_L20")
                row_d["home_run_diff_L20"]   = off_row.get("run_diff_L20")
            elif team == away_t:
                row_d["away_team_woba_L20"]  = off_row.get("team_woba_L20")
                row_d["away_team_k_pct_L20"] = off_row.get("team_k_pct_L20")
            if len(row_d) > 1:
                off_rows.append(row_d)

        if off_rows:
            off_df = pd.DataFrame(off_rows)
            # Aggregate in case multiple rows per game_pk from same side
            agg_cols = [c for c in off_df.columns if c != "game_pk"]
            off_df = off_df.groupby("game_pk")[agg_cols].first().reset_index()
            mf = mf.merge(off_df, on="game_pk", how="left")

    # -- Step 4: Park factor + dome
    # Ensure home_team is on mf — starter_home doesn't carry it, so fall back to game_meta
    if "home_team" not in mf.columns and game_meta is not None and not game_meta.empty \
            and "home_team" in game_meta.columns:
        _ht_map = game_meta.set_index("game_pk")["home_team"].to_dict()
        mf["home_team"] = mf["game_pk"].apply(
            lambda x: _ht_map.get(int(x) if pd.notna(x) else -1)
        )
    if "home_team" in mf.columns:
        mf["park_factor"] = mf["home_team"].map(TEAM_NAME_TO_ABBR).map(PARK_FACTORS).fillna(1.0)
        mf["is_dome"]     = mf["home_team"].map(TEAM_NAME_TO_ABBR).isin(DOME_TEAMS).astype(int)
    else:
        mf["park_factor"] = 1.0
        mf["is_dome"]     = 0

    # -- Step 5: Post pitch clock
    mf["game_date"]     = pd.to_datetime(mf["game_date"], errors="coerce")
    _pitch_clock_start  = pd.Timestamp("2023-03-30")
    mf["post_pitch_clock"] = (mf["game_date"] >= _pitch_clock_start).astype(int)

    # -- Step 6: Weather overlay
    if wx:
        wx_rows = [{"game_pk": gp, **w} for gp, w in wx.items()]
        wx_df   = pd.DataFrame(wx_rows)
        wx_df["game_pk"] = wx_df["game_pk"].astype(int)
        mf["game_pk"]    = mf["game_pk"].astype(int)
        overlap = set(mf.columns) & set(wx_df.columns) - {"game_pk"}
        if overlap:
            wx_df = wx_df.drop(columns=list(overlap))
        mf = mf.merge(wx_df, on="game_pk", how="left")

    # Fill missing weather columns with defaults
    if "temperature_f"  not in mf.columns: mf["temperature_f"]  = 70.0
    if "wind_speed_mph" not in mf.columns: mf["wind_speed_mph"] = 0.0
    if "wind_out"       not in mf.columns: mf["wind_out"]        = 0
    if "wind_in"        not in mf.columns: mf["wind_in"]         = 0
    mf["temperature_f"]  = mf["temperature_f"].fillna(70.0)
    mf["wind_speed_mph"] = mf["wind_speed_mph"].fillna(0.0)
    mf["wind_out"]       = mf["wind_out"].fillna(0).astype(int)
    mf["wind_in"]        = mf["wind_in"].fillna(0).astype(int)

    # -- Step 7: home_win target — derive from scoring_master for historical rows
    # Future / today's slate rows will remain NaN (retrain drops them).
    mf["home_win"] = np.nan
    try:
        from mlb_core.storage import read_csv as _read_csv, exists as _exists
        if _exists("Scoring/scoring_master.csv"):
            _scoring = _read_csv("Scoring/scoring_master.csv", low_memory=False)
            if not _scoring.empty and "half" in _scoring.columns and "runs" in _scoring.columns:
                _fg = (
                    _scoring.pivot_table(
                        index="game_pk", columns="half",
                        values="runs", aggfunc="sum", fill_value=0,
                    ).reset_index()
                )
                _fg.columns.name = None
                if "top" in _fg.columns and "bot" in _fg.columns:
                    _fg = _fg[_fg["top"] != _fg["bot"]].copy()   # drop ties
                    _fg["home_win"] = (_fg["bot"] > _fg["top"]).astype(int)
                    _hw_map = _fg.set_index("game_pk")["home_win"].to_dict()
                    mf["home_win"] = mf["game_pk"].apply(
                        lambda x: _hw_map.get(int(x) if pd.notna(x) else -1)
                    )
                    _n = mf["home_win"].notna().sum()
                    logger.info(
                        "GAME: home_win derived for %d/%d rows (%.1f%%)",
                        _n, len(mf), 100 * _n / max(len(mf), 1),
                    )
            else:
                logger.warning("GAME: scoring_master missing 'half'/'runs' columns — home_win NaN")
        else:
            logger.warning("GAME: scoring_master not found — home_win will be NaN for all rows")
    except Exception as _e:
        logger.warning("GAME: home_win derivation failed: %s", _e)

    mf = mf.sort_values("game_date").reset_index(drop=True)
    logger.info("GAME model_features: %d rows | %d games",
                len(mf), mf["game_pk"].nunique() if not mf.empty else 0)
    return mf


# -- Section 7: Entry point ---------------------------------------------------

def run(run_type: str = "daily", run_date: str | None = None) -> dict:
    """
    Full nightly feature build pipeline.

    Steps:
      1. Load Statcast master from GCS (90-day lookback)
      2. Build starter features (home + away)
      3. Build bullpen features
      4. Build team offense features
      5. Fetch today's weather (if run_type == 'daily')
      6. Join into model_features
      7. Write all CSVs to GCS
      8. Write build sentinel

    Returns dict with status and row counts.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv, write_csv, exists, write_build_sentinel

    if not GCS_BUCKET:
        return {"status": "error", "error": "MLB_GCS_BUCKET not set"}

    run_date = run_date or date.today().isoformat()
    logger.info("GAME feature build | run_type=%s | run_date=%s", run_type, run_date)

    LOOKBACK = 90

    # -- 1. Load Statcast
    try:
        sc = _load_statcast(lookback_days=LOOKBACK, run_date=run_date)
    except Exception as e:
        return {"status": "error", "error": f"statcast load: {e}"}

    if sc.empty:
        return {"status": "error", "error": "statcast master is empty"}

    # Extract game_meta (game_pk -> home_team, away_team) for team_map in build_model_features
    game_meta_df: pd.DataFrame = pd.DataFrame()
    if all(c in sc.columns for c in ["game_pk", "home_team", "away_team"]):
        try:
            game_meta_df = (
                sc.groupby("game_pk")
                  .agg(home_team=("home_team", "first"), away_team=("away_team", "first"))
                  .reset_index()
            )
            logger.info("GAME: game_meta extracted for %d games", len(game_meta_df))
        except Exception as e:
            logger.warning("GAME: game_meta extraction failed: %s", e)
    else:
        logger.warning("GAME: statcast missing home_team/away_team columns — team_map will be empty")

    # -- 2. Load existing features (incremental build)
    def _safe_read(key: str) -> pd.DataFrame:
        try:
            if exists(key):
                df = read_csv(key, low_memory=False)
                if not df.empty and "game_date" in df.columns:
                    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
                return df
        except Exception as e:
            logger.warning("GAME: could not read %s: %s", key, e)
        return pd.DataFrame()

    existing_home = _safe_read("GAME_Pro_System/data/starter_home_features.csv")
    existing_away = _safe_read("GAME_Pro_System/data/starter_away_features.csv")

    # -- 3. Starter features
    try:
        starter_home, starter_away = build_starter_features(
            sc, existing_home, existing_away,
            lookback_days=LOOKBACK, run_date=run_date,
        )
    except Exception as e:
        return {"status": "error", "error": f"starter features: {e}"}

    # -- 4. Bullpen features
    try:
        bullpen = build_bullpen_features(sc, lookback_days=LOOKBACK, run_date=run_date)
    except Exception as e:
        return {"status": "error", "error": f"bullpen features: {e}"}

    # -- 5. Team offense features
    try:
        offense = build_team_offense_features(sc, lookback_days=LOOKBACK, run_date=run_date)
    except Exception as e:
        return {"status": "error", "error": f"offense features: {e}"}

    # -- 6. Weather (live for daily, skip for backfill)
    wx: dict = {}
    if run_type == "daily":
        try:
            from mlb_core.data.lineups import get_today_schedule
            from mlb_core.data.weather import fetch_live_weather_for_slate
            sched = get_today_schedule(run_date)
            if not sched.empty:
                wx = fetch_live_weather_for_slate(sched)
                logger.info("GAME: weather fetched for %d/%d games", len(wx), len(sched))
        except Exception as e:
            logger.warning("GAME: weather fetch failed: %s", e)

    # -- 7. Join into model_features
    try:
        model_feats = build_model_features(
            starter_home, starter_away, bullpen, offense, wx,
            run_date=run_date, game_meta=game_meta_df,
        )
    except Exception as e:
        return {"status": "error", "error": f"model_features join: {e}"}

    if model_feats.empty:
        return {"status": "error", "error": "model_features is empty after join"}

    # -- 8. Write to GCS
    try:
        write_csv(starter_home,  "GAME_Pro_System/data/starter_home_features.csv")
        write_csv(starter_away,  "GAME_Pro_System/data/starter_away_features.csv")
        write_csv(starter_home,  "GAME_Pro_System/data/starter_game_features.csv")  # combined alias
        write_csv(bullpen,       "GAME_Pro_System/data/bullpen_game_features.csv")
        write_csv(offense,       "GAME_Pro_System/data/team_offense_features.csv")
        write_csv(model_feats,   "GAME_Pro_System/data/model_features.csv")
        logger.info("GAME: all feature CSVs written to GCS")
    except Exception as e:
        return {"status": "error", "error": f"GCS write: {e}"}

    # -- 9. Build sentinel
    try:
        write_build_sentinel(GCS_BUCKET, "GAME", run_date)
        logger.info("GAME: build sentinel written")
    except Exception as e:
        logger.warning("GAME: sentinel write failed: %s", e)

    return {
        "status":        "ok",
        "run_date":      run_date,
        "starter_rows":  len(starter_home) + len(starter_away),
        "bullpen_rows":  len(bullpen) if bullpen is not None else 0,
        "offense_rows":  len(offense) if offense is not None else 0,
        "model_rows":    len(model_feats),
        "games":         int(model_feats["game_pk"].nunique()) if not model_feats.empty else 0,
    }


if __name__ == "__main__":
    import json
    import sys
    _result = run()
    print(json.dumps(_result, indent=2, default=str))
    sys.exit(0 if _result.get("status") == "ok" else 1)
