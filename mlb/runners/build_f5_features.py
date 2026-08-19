"""
runners/build_f5_features.py — F5 Pro feature rebuild for Cloud Run.

Port of F5_Pro_v5.ipynb cells 23, 25, 27, 33. Two divergences from the
notebook for GCP-friendliness:

  1. `home_wins_f5` is derived from `Scoring/scoring_master.csv` by summing
     runs in innings 1-5 per side, instead of calling MLB Stats API
     per-game. This is much faster and avoids the 30-min API loop the
     notebook does in Section 6e. Same source the NRFI yrfi fix uses.

  2. Cross-system dependencies are read from GCS:
       - NRFI_Pro_System/data/pitcher_start_features.csv (handedness joins)
       - NRFI_Pro_System/data/lineups_master.csv (top-3 batter join — if present)
       - HR_Pro/data/game_features_master.csv (home/away win% to date — if present)

Outputs to GCS:
  - F5_Pro_System/data/pitcher_starts.csv          (intermediate)
  - F5_Pro_System/data/team_offense.csv            (intermediate)
  - F5_Pro_System/data/model_features.csv          (model input)

Run order matters: NRFI feature build must precede F5 feature build so
pitcher_start_features.csv is fresh on GCS before F5 reads it.
"""
import logging
import warnings
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# Pitch type groupings — match the notebook
FASTBALL_TYPES = {"FF", "SI", "FC"}
BREAKING_TYPES = {"SL", "CU", "ST", "SV", "KC"}
OFFSPEED_TYPES = {"CH", "FS", "FO"}

# Park / altitude constants
PARK_FACTORS = {
    "ARI": 0.98, "ATL": 1.02, "BAL": 0.98, "BOS": 0.99, "CHC": 1.02,
    "CWS": 1.01, "CIN": 1.08, "CLE": 0.97, "COL": 1.33, "DET": 0.98,
    "HOU": 0.97, "KC":  1.01, "LAA": 1.04, "LAD": 1.00, "MIA": 0.95,
    "MIL": 1.02, "MIN": 1.02, "NYM": 1.02, "NYY": 1.03, "OAK": 0.96,
    "PHI": 1.05, "PIT": 0.99, "SD":  0.98, "SF":  0.97, "SEA": 0.97,
    "STL": 1.02, "TB":  0.97, "TEX": 1.03, "TOR": 1.05, "WSH": 1.02,
}
ALTITUDE_FT = {"COL": 5280, "ARI": 1082, "ATL": 1050}


def _safe_rate(n, d):
    return n / d if d and d > 0 else np.nan


# ── Feature builders (port of notebook cells 23, 25) ─────────────────────────


def build_pitcher_rolling_features(sc: pd.DataFrame) -> pd.DataFrame:
    """
    Per-start pitcher rolling stats filtered to inning <= 5.

    Direct port of F5_Pro_v5.ipynb cell 23 (`build_pitcher_rolling_features`).
    Output is one row per (pitcher, game_pk) for starting pitchers only,
    with rolling L3/L10/L5 and STD windows.
    """
    if sc is None or sc.empty:
        logger.warning("F5: no Statcast data — empty pitcher_starts")
        return pd.DataFrame()

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    # Null inning_topbot fix (port from notebook — 2025 cache issue)
    null_mask = sc["inning_topbot"].isna()
    if null_mask.any():
        logger.info(f"F5: fixing {null_mask.sum():,} null inning_topbot rows")
        sc_sorted = sc.sort_values(["game_pk", "at_bat_number"])
        first_ab = (sc_sorted.groupby(["game_pk", "pitcher"])["at_bat_number"]
                              .min().reset_index())
        game_first = (sc_sorted.groupby("game_pk")["at_bat_number"]
                                .min().reset_index()
                                .rename(columns={"at_bat_number": "game_first_ab"}))
        first_ab = first_ab.merge(game_first, on="game_pk")
        first_ab["inferred_topbot"] = np.where(
            first_ab["at_bat_number"] == first_ab["game_first_ab"], "Top", "Bot")
        topbot_map = first_ab.set_index(["game_pk", "pitcher"])["inferred_topbot"].to_dict()
        sc.loc[null_mask, "inning_topbot"] = sc[null_mask].apply(
            lambda r: topbot_map.get((r["game_pk"], r["pitcher"]), np.nan), axis=1)

    # Keep only starting pitcher per (game_pk, inning_topbot) — same notebook logic
    sc_sorted = sc.sort_values(["game_pk", "inning_topbot", "at_bat_number"])
    starter_ids = (sc_sorted.dropna(subset=["inning_topbot"])
                            .groupby(["game_pk", "inning_topbot"])["pitcher"]
                            .first().reset_index()
                            .rename(columns={"pitcher": "starter_id"}))
    sc = sc.merge(starter_ids, on=["game_pk", "inning_topbot"], how="left")
    sc = sc[sc["pitcher"] == sc["starter_id"]].copy()

    # Outcome flags
    sc["is_k"]   = sc["events"].isin(["strikeout", "strikeout_double_play"]).astype(int)
    sc["is_bb"]  = sc["events"].isin(["walk", "intent_walk"]).astype(int)
    sc["is_hard_hit"] = (pd.to_numeric(sc["launch_speed"], errors="coerce") >= 95).astype(int)
    sc["is_barrel"] = (
        (pd.to_numeric(sc["launch_speed"], errors="coerce") >= 98) &
        (pd.to_numeric(sc["launch_angle"], errors="coerce").between(26, 30))
    ).astype(int)
    sc["is_swinging_strike"] = sc["description"].str.contains(
        "swinging_strike", na=False).astype(int)
    sc["is_swing"] = sc["description"].str.contains(
        "swinging_strike|foul|hit_into_play|swinging_pitchout", na=False).astype(int)
    sc["bbe"] = sc["launch_speed"].notna().astype(int)
    PA_ENDINGS = {
        "strikeout", "strikeout_double_play", "walk", "intent_walk",
        "single", "double", "triple", "home_run", "field_out",
        "force_out", "grounded_into_double_play", "fielders_choice",
        "sac_fly", "sac_bunt", "hit_by_pitch", "double_play",
        "field_error", "sac_fly_double_play",
    }
    sc["is_pa_end"] = sc["events"].isin(PA_ENDINGS).astype(int)
    sc["is_fastball"] = sc["pitch_type"].isin(FASTBALL_TYPES).astype(int)
    sc["is_breaking"] = sc["pitch_type"].isin(BREAKING_TYPES).astype(int)
    sc["is_offspeed"] = sc["pitch_type"].isin(OFFSPEED_TYPES).astype(int)
    sc["has_pitch_type"] = sc["pitch_type"].notna().astype(int)

    def agg_start(g):
        pa = g["is_pa_end"].sum()
        sw = g["is_swing"].sum()
        bb_c = g["bbe"].sum()
        n_pitches = g["has_pitch_type"].sum()
        return pd.Series({
            "pitcher":       g.name[0], "game_pk": g.name[1],
            "game_date":     g["game_date"].iloc[0],
            "home_team":     g["home_team"].iloc[0], "away_team": g["away_team"].iloc[0],
            "p_throws":      g["p_throws"].iloc[0], "inning_topbot": g["inning_topbot"].iloc[0],
            "season":        g["game_date"].iloc[0].year,
            "days_rest":     g["pitcher_days_since_prev_game"].iloc[0]
                             if "pitcher_days_since_prev_game" in g.columns else np.nan,
            "arm_angle":     g["arm_angle"].mean() if "arm_angle" in g.columns else np.nan,
            "k_pct":         _safe_rate(g["is_k"].sum(), pa),
            "bb_pct":        _safe_rate(g["is_bb"].sum(), pa),
            "xwoba":         g["estimated_woba_using_speedangle"].mean(),
            "hard_hit_pct":  _safe_rate(g["is_hard_hit"].sum(), bb_c),
            "barrel_pct":    _safe_rate(g["is_barrel"].sum(), bb_c),
            "whiff_pct":     _safe_rate(g["is_swinging_strike"].sum(), sw),
            "velo_mean":     g["release_speed"].mean() if "release_speed" in g.columns else np.nan,
            "pfxz_mean":     g["pfx_z"].mean() if "pfx_z" in g.columns else np.nan,
            "spin_mean":     g["release_spin_rate"].mean() if "release_spin_rate" in g.columns else np.nan,
            "fb_pct":        _safe_rate(g["is_fastball"].sum(), n_pitches),
            "breaking_pct":  _safe_rate(g["is_breaking"].sum(), n_pitches),
            "offspeed_pct":  _safe_rate(g["is_offspeed"].sum(), n_pitches),
        })

    logger.info("F5: aggregating pitcher starts")
    starts = (sc.sort_values("at_bat_number")
                .groupby(["pitcher", "game_pk"])
                .apply(agg_start)
                .reset_index(drop=True))
    starts = starts.sort_values(["pitcher", "game_date"]).reset_index(drop=True)
    # pitcher_days_since_prev_game is not in the statcast master; compute from dates.
    starts["days_rest"] = starts.groupby("pitcher")["game_date"].diff().dt.days

    # Rolling windows
    RATE_COLS = ["k_pct", "bb_pct", "xwoba", "hard_hit_pct", "barrel_pct", "whiff_pct"]
    for col in RATE_COLS:
        for w in [3, 10]:
            starts[f"{col}_L{w}"] = (
                starts.groupby("pitcher")[col]
                      .transform(lambda x: x.shift(1).rolling(w, min_periods=1).mean()))
    for col in ["k_pct", "bb_pct", "xwoba", "velo_mean"]:
        starts[f"{col}_STD"] = (
            starts.groupby(["pitcher", "season"])[col]
                  .transform(lambda x: x.shift(1).expanding().mean()))
    NEW_COLS = ["velo_mean", "pfxz_mean", "spin_mean",
                "fb_pct", "breaking_pct", "offspeed_pct"]
    for col in NEW_COLS:
        starts[f"{col}_L5"] = (
            starts.groupby("pitcher")[col]
                  .transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean()))

    logger.info(f"F5: ✅ {len(starts):,} pitcher starts")
    return starts


def build_team_offense_rolling(sc: pd.DataFrame) -> pd.DataFrame:
    """Per-game offensive rolling stats per team. Port of cell 25.

    Returns one row per game_pk with home_off_*_L5 / away_off_*_L5 columns
    (and L20 for v5 bat_speed / swing_length if available)."""
    if sc is None or sc.empty:
        logger.warning("F5: no Statcast — empty team_offense")
        return pd.DataFrame()

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    sc["is_hard_hit"] = (pd.to_numeric(sc["launch_speed"], errors="coerce") >= 95).astype(int)
    sc["bbe"] = sc["launch_speed"].notna().astype(int)
    sc["batting_team"] = np.where(sc["inning_topbot"] == "Bot",
                                  sc["home_team"], sc["away_team"])
    sc["is_swing"] = sc["description"].str.contains(
        "swinging_strike|foul|hit_into_play|swinging_pitchout", na=False).astype(int)
    has_bat_speed = "bat_speed" in sc.columns and "swing_length" in sc.columns

    agg_dict = {
        "off_xwoba":   ("estimated_woba_using_speedangle", "mean"),
        "off_bbe":     ("bbe", "sum"),
        "off_hardhit": ("is_hard_hit", "sum"),
    }
    if has_bat_speed:
        sc["bat_speed_swing"]    = sc["bat_speed"].where(sc["is_swing"] == 1)
        sc["swing_length_swing"] = sc["swing_length"].where(sc["is_swing"] == 1)
        agg_dict["off_bat_speed"]    = ("bat_speed_swing", "mean")
        agg_dict["off_swing_length"] = ("swing_length_swing", "mean")

    game_off = (sc.groupby(["batting_team", "game_pk", "game_date",
                            "home_team", "away_team"])
                  .agg(**agg_dict).reset_index())
    game_off["off_hard_hit_pct"] = game_off.apply(
        lambda r: _safe_rate(r["off_hardhit"], r["off_bbe"]), axis=1)
    game_off = game_off.sort_values(["batting_team", "game_date"])

    for col in ["off_xwoba", "off_hard_hit_pct"]:
        game_off[f"{col}_L5"] = (
            game_off.groupby("batting_team")[col]
                    .transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean()))
    if has_bat_speed:
        for col in ["off_bat_speed", "off_swing_length"]:
            game_off[f"{col}_L20"] = (
                game_off.groupby("batting_team")[col]
                        .transform(lambda x: x.shift(1).rolling(20, min_periods=5).mean()))
        game_off["bat_speed_has_data"] = game_off["off_bat_speed"].notna().astype(int)

    def _pivot_side(side_col, prefix):
        mask = game_off["batting_team"] == game_off[side_col]
        keep_cols = ["game_pk"] + [c for c in game_off.columns
                                   if c.endswith(("_L5", "_L20")) or c == "bat_speed_has_data"]
        keep_cols = [c for c in keep_cols if c in game_off.columns]
        side = (game_off[mask][keep_cols]
                .drop_duplicates(subset=["game_pk"], keep="last")
                .rename(columns={c: f"{prefix}_{c}" for c in keep_cols if c != "game_pk"}))
        return side

    away_off = _pivot_side("away_team", "away_off")
    home_off = _pivot_side("home_team", "home_off")
    # Clean up double-prefixed cols ("away_off_off_xwoba_L5" → "away_off_xwoba_L5")
    away_off = away_off.rename(columns=lambda c: c.replace("away_off_off_", "away_off_"))
    home_off = home_off.rename(columns=lambda c: c.replace("home_off_off_", "home_off_"))

    result = away_off.merge(home_off, on="game_pk", how="outer")
    logger.info(f"F5: ✅ team offense {len(result):,} games")
    return result


def _derive_f5_outcomes(scoring_master: pd.DataFrame) -> pd.DataFrame:
    """Build (game_pk, home_wins_f5) from scoring_master innings 1-5.

    Replaces the notebook's per-game MLB Stats API call. Returns rows
    only for games where both halves of inning 5 played (otherwise the
    F5 result isn't defined — e.g. PPD / suspended / no top-of-5).
    """
    if scoring_master is None or scoring_master.empty:
        return pd.DataFrame()
    sc = scoring_master[scoring_master["inning"].between(1, 5)].copy()
    pivot = (sc.pivot_table(index="game_pk", columns="half",
                            values="runs", aggfunc="sum", fill_value=0)
               .reset_index())
    pivot.columns.name = None
    # Need both halves present for a meaningful F5 result
    if "top" not in pivot.columns or "bot" not in pivot.columns:
        logger.warning("F5: scoring_master missing top/bot half columns")
        return pd.DataFrame()
    # Drop games that don't have full 1-5 coverage on both sides
    games_with_full = (sc.groupby(["game_pk", "half"])["inning"]
                         .nunique().unstack(fill_value=0))
    games_with_full = games_with_full[
        (games_with_full.get("top", 0) >= 5) & (games_with_full.get("bot", 0) >= 5)
    ]
    pivot = pivot[pivot["game_pk"].isin(games_with_full.index)].copy()

    pivot["home_runs_f5"] = pivot["bot"]   # home bats in bot
    pivot["away_runs_f5"] = pivot["top"]   # away bats in top
    # Drop ties — F5 moneyline is push, not a usable training label
    pivot = pivot[pivot["home_runs_f5"] != pivot["away_runs_f5"]].copy()
    pivot["home_wins_f5"] = (pivot["home_runs_f5"] > pivot["away_runs_f5"]).astype(int)
    return pivot[["game_pk", "home_wins_f5"]]


def _apply_joins(outcomes_df: pd.DataFrame,
                 pitcher_starts: pd.DataFrame,
                 team_off_game: pd.DataFrame,
                 weather: pd.DataFrame,
                 umpire: pd.DataFrame,
                 hr_game_features: pd.DataFrame) -> pd.DataFrame:
    """Join everything into the master F5 feature table. Port of cell 27.

    All joins are on game_pk; dedup after every merge.
    """
    ROLLING_COLS = [c for c in pitcher_starts.columns
                    if c.endswith(("_L3", "_L5", "_L10", "_STD", "_L20"))]
    BASE_COLS = (["pitcher", "game_pk", "game_date", "p_throws", "days_rest", "arm_angle"]
                 + ROLLING_COLS)
    BASE_COLS = [c for c in BASE_COLS if c in pitcher_starts.columns]

    # away pitcher = pitcher facing top half (which is the away batting half;
    # the pitcher in that half is the home pitcher) — WAIT, this matches the
    # NOTEBOOK exactly. Notebook: inning_topbot=='Top' → "away_p" prefix.
    # This is the F5 notebook's convention; not the bug we fixed in NRFI.
    # The pitcher who pitches the "Top" half is the home pitcher, but the
    # notebook labels these columns "away_p" because it joins the *opposing*
    # side's features under the team batting in that half. Keep the labels
    # as the notebook uses them so feature names match the trained model.
    away_p = (pitcher_starts[pitcher_starts["inning_topbot"] == "Top"][BASE_COLS]
              .drop_duplicates(subset=["game_pk"], keep="first").copy())
    home_p = (pitcher_starts[pitcher_starts["inning_topbot"] == "Bot"][BASE_COLS]
              .drop_duplicates(subset=["game_pk"], keep="first").copy())
    away_p = away_p.rename(columns={c: f"away_{c}" for c in BASE_COLS if c != "game_pk"})
    home_p = home_p.rename(columns={c: f"home_{c}" for c in BASE_COLS if c != "game_pk"})

    gf = (outcomes_df.merge(away_p, on="game_pk", how="left")
                     .drop_duplicates("game_pk", keep="first"))
    gf = gf.merge(home_p, on="game_pk", how="left").drop_duplicates("game_pk", keep="first")
    logger.info(f"F5: after pitcher join {len(gf):,} rows")

    gf = gf.merge(team_off_game, on="game_pk", how="left").drop_duplicates("game_pk", keep="first")
    logger.info(f"F5: after offense join {len(gf):,} rows")

    if weather is not None and not weather.empty:
        wx_keep = ["game_pk"] + [c for c in weather.columns if c in [
            "temperature_f", "wind_speed_mph", "wind_dir_degrees",
            "is_outdoor", "wind_out", "wind_in", "is_cold", "is_hot", "high_wind"]]
        wx2 = weather[wx_keep].drop_duplicates(subset=["game_pk"], keep="last")
        gf = gf.merge(wx2, on="game_pk", how="left").drop_duplicates("game_pk", keep="first")
        for col in ["temperature_f", "wind_speed_mph", "wind_out", "wind_in",
                    "is_cold", "is_hot", "high_wind", "is_outdoor"]:
            if col in gf.columns:
                gf[col] = gf[col].fillna(0 if col != "temperature_f" else 70)
        if "temperature_f" in gf.columns:
            logger.info(f"F5: after weather join {len(gf):,} rows | "
                        f"temp coverage {gf['temperature_f'].notna().mean():.1%}")

    if umpire is not None and not umpire.empty:
        ump_cols = [c for c in umpire.columns if c.startswith("ump_")]
        if "game_pk" in umpire.columns and ump_cols:
            ump2 = umpire[["game_pk"] + ump_cols].drop_duplicates("game_pk", keep="last")
            gf = gf.merge(ump2, on="game_pk", how="left").drop_duplicates("game_pk", keep="first")
            logger.info(f"F5: after umpire join {len(gf):,} rows")

    if hr_game_features is not None and not hr_game_features.empty:
        keep = [c for c in ["game_pk", "home_win_pct_to_date", "away_win_pct_to_date"]
                if c in hr_game_features.columns]
        if "home_win_pct_to_date" in keep:
            for col in ["home_win_pct_to_date", "away_win_pct_to_date"]:
                if col in gf.columns:
                    gf = gf.drop(columns=[col])
            hr_gf2 = hr_game_features[keep].drop_duplicates("game_pk", keep="first")
            gf = gf.merge(hr_gf2, on="game_pk", how="left").drop_duplicates("game_pk", keep="first")
            logger.info(f"F5: after HR win-pct join {len(gf):,} rows | "
                        f"coverage {gf['home_win_pct_to_date'].notna().mean():.1%}")

    gf["park_factor"] = gf["home_team"].map(PARK_FACTORS).fillna(1.0)
    gf["altitude_ft"] = gf["home_team"].map(ALTITUDE_FT).fillna(0)

    # T13: Regime indicator — pitch clock rules 2023-03-30.
    if "game_date" in gf.columns:
        gf["post_pitch_clock"] = (
            pd.to_datetime(gf["game_date"]) >= pd.Timestamp("2023-03-30")
        ).astype(int)

    if "home_k_pct_L10" in gf.columns and "away_k_pct_L10" in gf.columns:
        gf["k_pct_diff_L10"] = gf["home_k_pct_L10"] - gf["away_k_pct_L10"]
    if "home_xwoba_L10" in gf.columns and "away_xwoba_L10" in gf.columns:
        gf["xwoba_allowed_diff"] = gf["away_xwoba_L10"] - gf["home_xwoba_L10"]

    try:
        from mlb_core.data.aux_joins import join_game_aux
        gf = join_game_aux(
            gf,
            home_pitcher_col="home_pitcher" if "home_pitcher" in gf.columns else None,
            away_pitcher_col="away_pitcher" if "away_pitcher" in gf.columns else None,
        )
    except Exception as _e:
        logger.warning("F5: aux_joins failed (non-fatal): %s", _e)

    gf = (gf.drop_duplicates("game_pk", keep="first")
            .sort_values("game_date")
            .reset_index(drop=True))
    return gf


def _join_nrfi_features(gf: pd.DataFrame, nrfi_pf: pd.DataFrame) -> pd.DataFrame:
    """Join NRFI handedness/platoon features. Port of cell 33 first half.

    Reads `pitcher_start_features.csv` from NRFI build; pivots by
    inning_topbot to home/away prefixed columns. Uses the F5 notebook's
    "Top→home_" convention (NOT the NRFI runner's pitcher_is_home logic).
    """
    if nrfi_pf is None or nrfi_pf.empty:
        logger.warning("F5: NRFI pitcher_start_features missing — skipping platoon join")
        return gf
    PLAT_COLS = ["game_pk", "inning_topbot", "pitcher_is_home",
                 "woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD",
                 "lineup_pct_L", "platoon_edge"]
    PLAT_COLS = [c for c in PLAT_COLS if c in nrfi_pf.columns]
    nrfi_sub = nrfi_pf[PLAT_COLS].copy()

    rename_cols = [c for c in PLAT_COLS if c not in ["game_pk", "inning_topbot"]]
    home_plat = (nrfi_sub[nrfi_sub["inning_topbot"] == "Top"]
                  .drop(columns=["inning_topbot"])
                  .drop_duplicates("game_pk", keep="first")
                  .rename(columns={c: f"home_{c}" for c in rename_cols}))
    away_plat = (nrfi_sub[nrfi_sub["inning_topbot"] == "Bot"]
                  .drop(columns=["inning_topbot"])
                  .drop_duplicates("game_pk", keep="first")
                  .rename(columns={c: f"away_{c}" for c in rename_cols}))

    home_cols = [c for c in home_plat.columns if c != "game_pk"]
    away_cols = [c for c in away_plat.columns if c != "game_pk"]
    gf = gf.drop(columns=[c for c in home_cols if c in gf.columns], errors="ignore")
    gf = gf.drop(columns=[c for c in away_cols if c in gf.columns], errors="ignore")
    gf = (gf.merge(home_plat, on="game_pk", how="left")
            .drop_duplicates("game_pk", keep="first"))
    gf = (gf.merge(away_plat, on="game_pk", how="left")
            .drop_duplicates("game_pk", keep="first"))
    cov = (gf["home_lineup_pct_L"].notna().mean()
           if "home_lineup_pct_L" in gf.columns else 0.0)
    logger.info(f"F5: platoon/lineup_pct_L joined | home coverage {cov:.1%}")
    return gf


# ── Main runner ──────────────────────────────────────────────────────────────


GCS_STATCAST_MASTER       = "Statcast/statcast_master.csv"
GCS_SCORING_MASTER        = "Scoring/scoring_master.csv"
GCS_WEATHER_MASTER        = "Weather/weather_master.csv"
GCS_UMPIRES_MASTER        = "Umpires/umpscorecards_master.csv"
GCS_NRFI_PITCHER_FEATURES = "NRFI_Pro_System/data/pitcher_start_features.csv"
GCS_HR_GAME_FEATURES      = "HR_Pro/data/game_features_master.csv"
GCS_F5_PITCHER_STARTS     = "F5_Pro_System/data/pitcher_starts.csv"
GCS_F5_TEAM_OFFENSE       = "F5_Pro_System/data/team_offense.csv"
GCS_F5_MODEL_FEATURES     = "F5_Pro_System/data/model_features.csv"
# GCS_STATCAST_MASTER_FULL removed 2026-08-17 -- it was the literal same
# string as GCS_STATCAST_MASTER and only existed to justify a second,
# redundant read of the same file (finding B1.5). Use GCS_STATCAST_MASTER
# and reuse the already-loaded sc_all instead.


def build_bullpen_rolling(sc_full: pd.DataFrame) -> pd.DataFrame:
    """
    Per-team bullpen rolling stats over last 30 games. (T16)

    Reliever = any appearance with inning > 1 (or starter who only pitches
    1 inning, treated conservatively as an opener/bulk appearance).
    Aggregated to team-game level, then rolled L30 with shift(1).

    Returns one row per (team, game_pk) with columns:
        bullpen_xfip_L30, bullpen_k_pct_L30, bullpen_xwoba_L30

    Note: xFIP proper requires league HR/FB rate lookup; we approximate
    using xwOBA-allowed as a proxy for contact quality since that's already
    in Statcast. True xFIP can be added when FanGraphs data is in the lake.
    """
    if sc_full is None or sc_full.empty:
        logger.warning("F5 bullpen: no Statcast data — skipping bullpen features")
        return pd.DataFrame()

    sc = sc_full.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    sc["inning"]    = pd.to_numeric(sc["inning"], errors="coerce")

    # Relief appearances: inning > 1.
    # Also include inning=1 appearances by pitchers who were NOT the game's
    # primary starter (identified as: same pitcher appears in inning > 1).
    # Simple proxy: just use inning > 1 to avoid complex starter detection.
    relief = sc[sc["inning"] > 1].copy()
    if relief.empty:
        logger.warning("F5 bullpen: no relief appearances found")
        return pd.DataFrame()

    # Identify pitching team
    # Pitcher faces batters from the batting team.
    # inning_topbot == "Top" → away team bats → pitcher is on home team.
    # inning_topbot == "Bot" → home team bats → pitcher is on away team.
    relief["pitching_team"] = np.where(
        relief["inning_topbot"] == "Top",
        relief["home_team"],
        relief["away_team"],
    )

    relief["is_k"]   = relief["events"].isin(["strikeout", "strikeout_double_play"]).astype(int)
    relief["is_pa"]  = relief["events"].notna().astype(int)
    relief["xwoba"]  = pd.to_numeric(
        relief.get("estimated_woba_using_speedangle", pd.Series(dtype=float)),
        errors="coerce",
    )

    # Aggregate to game-level per pitching team
    game_bull = (
        relief.groupby(["pitching_team", "game_pk", "game_date"])
              .agg(
                  bull_pa_count =("is_pa",  "sum"),
                  bull_k_count  =("is_k",   "sum"),
                  bull_xwoba    =("xwoba",  "mean"),
              )
              .reset_index()
    )
    game_bull["bull_k_pct"] = (
        game_bull["bull_k_count"] / game_bull["bull_pa_count"].replace(0, np.nan)
    )
    game_bull = game_bull.sort_values(["pitching_team", "game_date"]).reset_index(drop=True)

    # Rolling L30 with shift(1)
    for col, alias in [("bull_k_pct", "bullpen_k_pct_L30"),
                       ("bull_xwoba",  "bullpen_xwoba_L30")]:
        game_bull[alias] = (
            game_bull.groupby("pitching_team")[col]
                     .transform(lambda x: x.shift(1).rolling(30, min_periods=5).mean())
        )

    keep = ["pitching_team", "game_pk",
            "bullpen_k_pct_L30", "bullpen_xwoba_L30"]
    result = game_bull[keep].copy()
    logger.info(
        f"F5 bullpen: {len(result):,} team-game rows | "
        f"k_pct coverage {result['bullpen_k_pct_L30'].notna().mean():.1%}"
    )
    return result


def run(run_date: str = None) -> dict:
    """Rebuild F5 feature tables and upload to GCS.

    Returns a result dict suitable for the Flask endpoint to return.
    """
    run_date = run_date or date.today().isoformat()
    logger.info(f"F5 feature build | date={run_date}")

    from mlb_core.storage import read_csv, write_csv, exists
    from mlb_core.config import GCS_BUCKET

    if not GCS_BUCKET:
        return {"status": "error",
                "error": "GCS_BUCKET not set — F5 feature build requires GCS mode"}

    # 1. Load Statcast ONCE (full master, all innings). sc_all feeds the
    # bullpen builder (needs innings 6-9 too); sc is the inning<=5 slice the
    # pitcher/offense builders need. Fixed 2026-08-17 (finding B1.5) -- this
    # used to read the identical file a second time, under a second
    # constant (GCS_STATCAST_MASTER_FULL) that was the literal same string,
    # purely to get back the innings the first read's filter had already
    # discarded. See docs/audits/
    # 2026-08-16_cloud_efficiency_and_profitability_review.md.
    logger.info("F5: loading Statcast master")
    try:
        sc_all = read_csv(GCS_STATCAST_MASTER, low_memory=False)
    except Exception as e:
        logger.error(f"Statcast load failed: {e}")
        return {"status": "error", "error": f"statcast load: {e}"}
    logger.info(f"F5: loaded {len(sc_all):,} pitch rows")
    sc_all["inning"] = pd.to_numeric(sc_all["inning"], errors="coerce")
    sc = sc_all[sc_all["inning"] <= 5].copy()
    logger.info(f"F5: filtered to inning<=5: {len(sc):,} rows")

    # 2. Derive F5 outcomes from scoring_master
    if not exists(GCS_SCORING_MASTER):
        return {"status": "error",
                "error": f"{GCS_SCORING_MASTER} missing — required for F5 outcomes"}
    try:
        scoring = read_csv(GCS_SCORING_MASTER, low_memory=False)
    except Exception as e:
        return {"status": "error", "error": f"scoring_master load: {e}"}
    outcomes = _derive_f5_outcomes(scoring)
    if outcomes.empty:
        return {"status": "error",
                "error": "0 F5 outcomes derived from scoring_master"}
    # Attach game_date / home_team / away_team from Statcast for downstream joins
    game_meta = (sc[["game_pk", "game_date", "home_team", "away_team"]]
                  .drop_duplicates("game_pk", keep="first"))
    outcomes = outcomes.merge(game_meta, on="game_pk", how="left")
    home_rate = outcomes["home_wins_f5"].mean()
    logger.info(f"F5: {len(outcomes):,} outcomes | home win rate {home_rate:.3f}")

    # 3. Build pitcher rolling and team offense
    pitcher_starts = build_pitcher_rolling_features(sc)
    if pitcher_starts.empty:
        return {"status": "error", "error": "pitcher_starts empty"}
    try:
        write_csv(pitcher_starts, GCS_F5_PITCHER_STARTS)
    except Exception as e:
        logger.warning(f"F5: pitcher_starts upload failed: {e}")

    team_off = build_team_offense_rolling(sc)
    try:
        write_csv(team_off, GCS_F5_TEAM_OFFENSE)
    except Exception as e:
        logger.warning(f"F5: team_offense upload failed: {e}")

    # 3b. T16: Bullpen rolling features — requires full Statcast (all innings).
    # Reuses sc_all (already in memory, loaded once above) instead of
    # re-reading the identical file a second time.
    bullpen_home = pd.DataFrame()
    bullpen_away = pd.DataFrame()
    try:
        bull = build_bullpen_rolling(sc_all)
        del sc_all  # free memory -- last use
        if not bull.empty:
            # Split into home/away bullpen perspectives for the game-level join
            bullpen_home = bull.rename(columns={
                "pitching_team": "home_team",
                "bullpen_k_pct_L30":  "home_bullpen_k_pct_L30",
                "bullpen_xwoba_L30":  "home_bullpen_xwoba_L30",
            })
            bullpen_away = bull.rename(columns={
                "pitching_team": "away_team",
                "bullpen_k_pct_L30":  "away_bullpen_k_pct_L30",
                "bullpen_xwoba_L30":  "away_bullpen_xwoba_L30",
            })
    except Exception as e:
        logger.warning(f"F5: bullpen feature build failed (non-fatal): {e}")

    # 4. Free Statcast before heavy joins (memory hygiene)
    del sc

    # 5. Load optional joinables
    wx = pd.DataFrame()
    if exists(GCS_WEATHER_MASTER):
        try:
            wx = read_csv(GCS_WEATHER_MASTER, low_memory=False)
        except Exception as e:
            logger.warning(f"F5: weather load failed: {e}")
    ump = pd.DataFrame()
    if exists(GCS_UMPIRES_MASTER):
        try:
            from mlb.runners.build_nrfi_features import build_umpire_features
            ump_raw = read_csv(GCS_UMPIRES_MASTER, low_memory=False)
            ump = build_umpire_features(ump_raw)
        except Exception as e:
            logger.warning(f"F5: umpire build failed: {e}")
    hr_gf = pd.DataFrame()
    if exists(GCS_HR_GAME_FEATURES):
        try:
            hr_gf = read_csv(GCS_HR_GAME_FEATURES,
                              usecols=lambda c: c in
                              ["game_pk", "home_win_pct_to_date", "away_win_pct_to_date"])
        except Exception as e:
            logger.warning(f"F5: HR game_features load failed: {e}")

    # 6. Master join
    gf = _apply_joins(outcomes, pitcher_starts, team_off, wx, ump, hr_gf)
    if gf.empty:
        return {"status": "error", "error": "F5 model_features empty after joins"}

    # 6b. T16: Join bullpen features (home and away pitching teams)
    if not bullpen_home.empty and "home_team" in gf.columns:
        gf = gf.merge(
            bullpen_home[["game_pk", "home_bullpen_k_pct_L30", "home_bullpen_xwoba_L30"]],
            on="game_pk", how="left",
        ).drop_duplicates("game_pk", keep="first")
        cov = gf["home_bullpen_k_pct_L30"].notna().mean()
        logger.info(f"F5: home bullpen joined | coverage {cov:.1%}")
    if not bullpen_away.empty and "away_team" in gf.columns:
        gf = gf.merge(
            bullpen_away[["game_pk", "away_bullpen_k_pct_L30", "away_bullpen_xwoba_L30"]],
            on="game_pk", how="left",
        ).drop_duplicates("game_pk", keep="first")
        cov = gf["away_bullpen_k_pct_L30"].notna().mean()
        logger.info(f"F5: away bullpen joined | coverage {cov:.1%}")

    # 7. NRFI cross-system platoon / handedness join
    if exists(GCS_NRFI_PITCHER_FEATURES):
        try:
            nrfi_pf = read_csv(GCS_NRFI_PITCHER_FEATURES, low_memory=False)
            gf = _join_nrfi_features(gf, nrfi_pf)
        except Exception as e:
            logger.warning(f"F5: NRFI platoon join failed: {e}")
    else:
        logger.warning(f"F5: {GCS_NRFI_PITCHER_FEATURES} missing — "
                       "NRFI features build must run before F5")

    # A schema entry has existed for this since the original audit but was
    # never actually called anywhere in this file -- added 2026-08-19 (along
    # with fixing the schema's own required-column name, which was wrong --
    # see mlb_core/schemas.py). See docs/audits/
    # 2026-08-19_feature_data_pipeline_review.md finding 2.8.
    from mlb_core.schemas import validate_df
    validate_df(gf, "f5_model_features",
                context="F5 model_features output", raise_on_error=True)

    # 8. Upload final
    try:
        write_csv(gf, GCS_F5_MODEL_FEATURES)
    except Exception as e:
        return {"status": "error", "error": f"F5 upload: {e}"}

    # NaN rate audit -- log any feature column above 5% NaN
    _numeric_cols = gf.select_dtypes(include="number").columns.tolist()
    _bad_nans = sorted(
        ((c, float(gf[c].isna().mean())) for c in _numeric_cols
         if gf[c].isna().mean() > 0.05),
        key=lambda x: -x[1],
    )
    if _bad_nans:
        logger.warning("F5 build: high-NaN features -- " + ", ".join(f"{c}={r:.0%}" for c, r in _bad_nans[:15]))
    else:
        logger.info(f"F5 build: NaN audit OK -- {len(_numeric_cols)} numeric features all < 5% NaN")

    logger.info(f"F5: feature build complete | {len(gf):,} games")
    result = {"status": "ok", "rows": int(len(gf)), "columns": int(len(gf.columns)), "home_win_rate": round(float(home_rate), 4)}
    from mlb_core.storage import write_build_sentinel
    write_build_sentinel("F5", result)
    return result


if __name__ == "__main__":
    import json
    import sys
    _result = run()
    print(json.dumps(_result, indent=2, default=str))
    sys.exit(0 if _result.get("status") == "ok" else 1)
