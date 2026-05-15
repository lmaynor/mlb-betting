"""
runners/build_nrfi_features.py — NRFI Pro feature rebuild for Cloud Run.

Runs at 8 AM ET daily (before the 9 AM prediction job).
Reads Statcast + weather + umpires from GCS, rebuilds features, uploads:
  - NRFI_Pro_System/data/pitcher_start_features.csv  (intermediate; F5 reuses)
  - NRFI_Pro_System/data/model_features.csv          (prediction input)

Direct port of NRFI_Pro_Complete_v17.ipynb sections 4, 6, 6b, 7. Key
differences from notebook:
  - No global `data` dict; functions take explicit DataFrame arguments.
  - No local file round-trips; intermediate DataFrames pass in memory.
  - No `live` mode; this runner is historical-mode only. Live prediction
    happens in run_nrfi.py.
  - No lineup_features step; live lineups will come from MLB Stats API in
    the runner. Historical lineup signal is captured via Statcast-derived
    `lineup_pct_L` in build_pitcher_handedness_splits.
  - Umpire join is optional — if the master CSV is missing in GCS, umpire
    feature columns are dropped (XGBoost handles missing features at predict
    time).
"""
import logging
import warnings
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# Park factors copied from notebook Section 0 (cell 5). Kept here to avoid
# importing the notebook config module.
PARK_FACTORS = {
    "ARI": 0.98, "ATL": 1.02, "BAL": 0.98, "BOS": 0.99, "CHC": 1.02,
    "CWS": 1.01, "CIN": 1.08, "CLE": 0.97, "COL": 1.33, "DET": 0.98,
    "HOU": 0.97, "KC":  1.01, "LAA": 1.04, "LAD": 1.00, "MIA": 0.95,
    "MIL": 1.02, "MIN": 1.02, "NYM": 1.02, "NYY": 1.03, "OAK": 0.96,
    "PHI": 1.05, "PIT": 0.99, "SD":  0.98, "SF":  0.97, "SEA": 0.97,
    "STL": 1.02, "TB":  0.97, "TEX": 1.03, "TOR": 1.05, "WSH": 1.02,
}


# ── Feature builders ──────────────────────────────────────────────────────


def build_pitcher_features(statcast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregates pitch-level Statcast (inning=1) to one row per (pitcher, game_pk).
    Computes rolling L3/L10 windows + season-to-date with shift(1) for no
    leakage. Adds `yrfi` target.

    Direct port of notebook cell 24, with the global-state read removed.
    """
    if statcast_df is None or statcast_df.empty:
        logger.warning("No Statcast data — empty pitcher_features")
        return pd.DataFrame()

    logger.info(f"Aggregating {len(statcast_df):,} pitches to per-start features")
    sc = statcast_df.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    if "release_spin" in sc.columns and "release_spin_rate" not in sc.columns:
        sc = sc.rename(columns={"release_spin": "release_spin_rate"})

    # Outcome flags
    sc["in_zone"]  = sc["zone"].between(1, 9).astype(int)
    sc["out_zone"] = sc["zone"].between(11, 14).astype(int)

    swing_desc = ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
                  "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
                  "foul_bunt", "missed_bunt"]
    whiff_desc = ["swinging_strike", "swinging_strike_blocked", "missed_bunt"]
    chase_desc = ["swinging_strike", "swinging_strike_blocked"]

    sc["is_swing"]         = sc["description"].isin(swing_desc).astype(int)
    sc["is_whiff"]         = sc["description"].isin(whiff_desc).astype(int)
    sc["is_chase"]         = ((sc["out_zone"] == 1) & sc["description"].isin(chase_desc)).astype(int)
    sc["is_called_strike"] = (sc["description"] == "called_strike").astype(int)
    sc["is_k"]             = sc["events"].isin(["strikeout", "strikeout_double_play"]).astype(int)
    sc["is_bb"]            = sc["events"].isin(["walk", "intent_walk"]).astype(int)
    sc["is_pa_end"]        = sc["events"].notna().astype(int)
    sc["is_hard_hit"]      = (sc["launch_speed"] >= 95).astype(int)
    sc["is_barrel"]        = (sc["launch_speed_angle"] == 6).astype(int)

    def safe_rate(n, d):
        return n / d if d > 0 else np.nan

    def agg_start(g):
        pitches  = len(g)
        pa_count = g["is_pa_end"].sum()
        swings   = g["is_swing"].sum()
        in_zone  = g["in_zone"].sum()
        out_zone = g["out_zone"].sum()
        bbe      = g[g["launch_speed"].notna()]
        bbe_ct   = len(bbe)

        pitch_vc  = g["pitch_type"].value_counts()
        pri_pitch = pitch_vc.index[0] if len(pitch_vc) > 0 else np.nan
        pri_pct   = safe_rate(pitch_vc.iloc[0], pitches) if len(pitch_vc) > 0 else np.nan
        pri_mask  = g["pitch_type"] == pri_pitch
        pri_whiff = safe_rate(g.loc[pri_mask, "is_whiff"].sum(),
                              g.loc[pri_mask, "is_swing"].sum())

        # YRFI: any run scored in inning 1 by batting team
        run_scored = int(g["post_bat_score"].max() > g["bat_score"].iloc[0])

        return pd.Series({
            "pitcher":            g.name[0],
            "player_name":        g["player_name"].iloc[0],
            "game_pk":            g.name[1],
            "game_date":          g["game_date"].iloc[0],
            "home_team":          g["home_team"].iloc[0],
            "away_team":          g["away_team"].iloc[0],
            "p_throws":           g["p_throws"].iloc[0],
            "inning_topbot":      g["inning_topbot"].iloc[0],
            "days_rest":          g["pitcher_days_since_prev_game"].iloc[0] if "pitcher_days_since_prev_game" in g.columns else np.nan,
            "n_thruorder":        g["n_thruorder_pitcher"].iloc[0] if "n_thruorder_pitcher" in g.columns else np.nan,
            "arm_angle":          g["arm_angle"].mean() if "arm_angle" in g.columns else np.nan,
            "pitch_count":        pitches,
            "pa_count":           pa_count,
            "bbe_count":          bbe_ct,
            "zone_pct":           safe_rate(in_zone, pitches),
            "chase_pct":          safe_rate(g["is_chase"].sum(), out_zone),
            "called_strike_pct":  safe_rate(g["is_called_strike"].sum(), pitches),
            "swing_pct":          safe_rate(swings, pitches),
            "whiff_pct":          safe_rate(g["is_whiff"].sum(), swings),
            "k_pct":              safe_rate(g["is_k"].sum(), pa_count),
            "bb_pct":             safe_rate(g["is_bb"].sum(), pa_count),
            "hard_hit_pct":       safe_rate(g["is_hard_hit"].sum(), bbe_ct),
            "barrel_pct":         safe_rate(g["is_barrel"].sum(), bbe_ct),
            "xwoba_allowed":      bbe["estimated_woba_using_speedangle"].mean() if bbe_ct > 0 else np.nan,
            "woba_allowed":       g["woba_value"].mean(),
            "velo_mean":          g["release_speed"].mean(),
            "velo_max":           g["release_speed"].max(),
            "spin_mean":          g["release_spin_rate"].mean() if "release_spin_rate" in g.columns else np.nan,
            "extension_mean":     g["release_extension"].mean(),
            "primary_pitch":      pri_pitch,
            "primary_pitch_pct":  pri_pct,
            "primary_whiff_rate": pri_whiff,
            "bat_speed_mean":     g["bat_speed"].mean() if "bat_speed" in g.columns else np.nan,
            "yrfi":               run_scored,
        })

    # Filter to inning=1 before grouping (NRFI is a 1st-inning model)
    sc_i1 = sc[pd.to_numeric(sc["inning"], errors="coerce") == 1].copy()
    starts = (
        sc_i1.sort_values("at_bat_number")
             .groupby(["pitcher", "game_pk"])
             .apply(agg_start)
             .reset_index(drop=True)
    )

    # inning_topbot == "Top" means the AWAY team is batting top of the 1st;
    # the pitcher facing them is the HOME pitcher. Verified 16/16 starts in
    # diagnostics (2026-05-12). Previous formula (== "Bot") was inverted.
    starts["pitcher_is_home"] = (starts["inning_topbot"] == "Top").astype(int)

    # Overwrite the in-loop yrfi computed from bat_score / post_bat_score —
    # those Statcast columns are 100% null for 2021-2025 in the GCS master,
    # producing yrfi=0 across the board. Authoritative 1st-inning run totals
    # come from Scoring/scoring_master.csv (MLB Stats API).
    #
    # A home pitcher faces the away team batting (top half); an away pitcher
    # faces the home team batting (bottom half). yrfi = runs allowed by this
    # pitcher in the 1st inning > 0.
    try:
        from mlb_core.storage import read_csv as _read_csv, exists as _exists
        if _exists("Scoring/scoring_master.csv"):
            scoring = _read_csv("Scoring/scoring_master.csv", low_memory=False)
            scoring_1st = scoring[scoring["inning"] == 1][["game_pk", "half", "runs"]].copy()
            scoring_1st = scoring_1st.drop_duplicates(["game_pk", "half"], keep="first")
            starts["_half_key"] = np.where(starts["pitcher_is_home"] == 1, "top", "bot")
            starts = starts.merge(
                scoring_1st.rename(columns={"half": "_half_key", "runs": "_runs_against"}),
                on=["game_pk", "_half_key"], how="left",
            )
            matched = starts["_runs_against"].notna().sum()
            starts["yrfi"] = (starts["_runs_against"].fillna(0) > 0).astype(int)
            starts = starts.drop(columns=["_half_key", "_runs_against"])
            logger.info(f"  yrfi rebuilt from scoring_master: matched {matched:,}/{len(starts):,} "
                        f"starts | rate={starts['yrfi'].mean():.3f}")
        else:
            logger.warning("  Scoring/scoring_master.csv missing — yrfi kept from bat_score "
                           "(likely zeros for 2021-2025 due to null source columns)")
    except Exception as e:
        logger.warning(f"  yrfi rebuild from scoring_master failed: {e} — keeping in-loop value")
    starts["season"]          = pd.to_datetime(starts["game_date"]).dt.year
    starts = starts.sort_values(["pitcher", "game_date"]).reset_index(drop=True)

    # Rolling features (L3, L10) — shift(1) prevents leakage
    RATE_COLS = ["zone_pct", "chase_pct", "whiff_pct", "k_pct", "bb_pct",
                 "hard_hit_pct", "barrel_pct", "xwoba_allowed", "woba_allowed",
                 "velo_mean", "spin_mean", "primary_whiff_rate", "called_strike_pct"]

    for col in RATE_COLS:
        for w in [3, 10]:
            starts[f"{col}_L{w}"] = (
                starts.groupby("pitcher")[col]
                      .transform(lambda x: x.shift(1).rolling(w, min_periods=1).mean())
            )

    # Velocity trend: slope over last 5 starts
    def velo_trend(series):
        result = pd.Series(index=series.index, dtype=float)
        shifted = series.shift(1)
        for i in range(len(series)):
            window = shifted.iloc[max(0, i - 4):i + 1].dropna()
            if len(window) >= 3:
                result.iloc[i] = np.polyfit(range(len(window)), window, 1)[0]
        return result

    logger.info("  Computing velocity trend")
    starts["velo_trend_L5"] = starts.groupby("pitcher")["velo_mean"].transform(velo_trend)

    # Season-to-date averages
    for col in ["zone_pct", "whiff_pct", "k_pct", "bb_pct", "xwoba_allowed", "velo_mean"]:
        starts[f"{col}_STD"] = (
            starts.groupby(["pitcher", "season"])[col]
                  .transform(lambda x: x.shift(1).expanding().mean())
        )

    logger.info(f"  ✅ {len(starts):,} pitcher starts | YRFI rate: {starts['yrfi'].mean():.3f}")
    return starts


def build_pitcher_handedness_splits(statcast_df: pd.DataFrame,
                                     pitcher_features: pd.DataFrame) -> pd.DataFrame:
    """
    Computes:
      woba_vs_L_STD / woba_vs_R_STD / woba_split_STD — pitcher season-to-date
        wOBA allowed vs LHB / RHB, with shift(1) for no leakage
      lineup_pct_L — fraction of 1st-inning batters who were LHB this game
      platoon_edge — woba_split_STD * (lineup_pct_L - 0.40)

    Returns pitcher_features with these 5 columns appended.

    Direct port of notebook cell 27. Differs in that:
      - Reads input from arguments instead of disk / global state.
      - Returns the merged DataFrame instead of mutating a file.
    """
    if pitcher_features is None or pitcher_features.empty:
        logger.warning("No pitcher_features — skipping handedness splits")
        return pitcher_features

    sc = statcast_df.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    pa = sc[(pd.to_numeric(sc["inning"], errors="coerce") == 1) &
            sc["woba_value"].notna()].copy()
    pa["season"] = pa["game_date"].dt.year
    logger.info(f"  Handedness splits: {len(pa):,} PA-level rows")

    # Per-start split wOBA
    split_rows = []
    for (pitcher, game_pk), grp in pa.groupby(["pitcher", "game_pk"]):
        row = {
            "pitcher":   pitcher,
            "game_pk":   game_pk,
            "game_date": grp["game_date"].iloc[0],
            "season":    grp["season"].iloc[0],
        }
        for hand in ["L", "R"]:
            sub = grp[grp["stand"] == hand]
            row[f"woba_vs_{hand}_this_start"] = sub["woba_value"].mean() if len(sub) >= 2 else np.nan
        split_rows.append(row)

    split_df = (pd.DataFrame(split_rows)
                  .sort_values(["pitcher", "game_date"])
                  .reset_index(drop=True))

    # Season-to-date expanding mean with shift(1)
    out_rows = []
    for pitcher, grp in split_df.groupby("pitcher"):
        grp = grp.sort_values("game_date").reset_index(drop=True)
        for season, sgrp in grp.groupby("season"):
            sgrp = sgrp.reset_index(drop=True)
            for hand in ["L", "R"]:
                col = f"woba_vs_{hand}_this_start"
                sgrp[f"woba_vs_{hand}_STD"] = sgrp[col].expanding().mean().shift(1)
            out_rows.append(sgrp)

    splits_out = pd.concat(out_rows, ignore_index=True) if out_rows else pd.DataFrame()
    if not splits_out.empty:
        splits_out["woba_split_STD"] = (splits_out["woba_vs_R_STD"]
                                        - splits_out["woba_vs_L_STD"])

    # Lineup LHB% derived from Statcast stand column
    lineup_hand = (
        sc[pd.to_numeric(sc["inning"], errors="coerce") == 1]
          .groupby(["game_pk", "inning_topbot", "stand"])
          .size().unstack(fill_value=0)
          .reset_index()
    )
    lineup_hand.columns.name = None
    for h in ["L", "R"]:
        if h not in lineup_hand.columns:
            lineup_hand[h] = 0
    lineup_hand["total_pa"] = lineup_hand["L"] + lineup_hand["R"]
    lineup_hand["pct_L"]    = lineup_hand["L"] / lineup_hand["total_pa"].clip(lower=1)

    # Merge into pitcher features
    pf = pitcher_features.copy()
    pf["game_date"] = pd.to_datetime(pf["game_date"])
    drop_cols = ["woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD",
                 "lineup_pct_L", "platoon_edge"]
    pf = pf.drop(columns=[c for c in drop_cols if c in pf.columns])

    if not splits_out.empty:
        pf = pf.merge(
            splits_out[["pitcher", "game_pk", "woba_vs_L_STD",
                        "woba_vs_R_STD", "woba_split_STD"]],
            on=["pitcher", "game_pk"], how="left",
        )
    pf = pf.merge(
        lineup_hand[["game_pk", "inning_topbot", "pct_L"]]
            .rename(columns={"pct_L": "lineup_pct_L"}),
        on=["game_pk", "inning_topbot"], how="left",
    )
    if "woba_split_STD" in pf.columns and "lineup_pct_L" in pf.columns:
        pf["platoon_edge"] = pf["woba_split_STD"] * (pf["lineup_pct_L"] - 0.40)
    else:
        pf["platoon_edge"] = np.nan

    new_cols = ["woba_vs_L_STD", "woba_vs_R_STD", "woba_split_STD",
                "lineup_pct_L", "platoon_edge"]
    coverage = {c: float(pf[c].notna().mean()) if c in pf.columns else 0.0
                for c in new_cols}
    logger.info(f"  Handedness coverage: " +
                " | ".join(f"{c}={v:.2f}" for c, v in coverage.items()))
    return pf


def build_umpire_features(umpires_master: pd.DataFrame) -> pd.DataFrame:
    """
    Compute rolling L10/L30/STD features per umpire from raw scorecard data.

    Direct port of notebook cell 19 (load_umpires), but takes the DataFrame
    as an argument instead of reading from disk + a cfg dict.

    Returns DataFrame keyed on game_pk with all ump_* feature columns
    plus `umpire`, `date`.
    """
    if umpires_master is None or umpires_master.empty:
        logger.warning("No umpire master — skipping umpire features")
        return pd.DataFrame()

    ump = umpires_master.copy()
    if "fully_valid" in ump.columns:
        ump = ump[ump["fully_valid"] == True].copy()
    ump["date"] = pd.to_datetime(ump["date"])
    ump = ump.sort_values(["umpire", "date"]).reset_index(drop=True)
    ump["season"] = ump["date"].dt.year

    # Rolling features — shift(1) prevents leakage
    for col in ["overall_accuracy", "total_run_impact", "consistency", "favor"]:
        if col not in ump.columns:
            continue
        for window in [10, 30]:
            ump[f"ump_{col}_L{window}"] = (
                ump.groupby("umpire")[col]
                   .transform(lambda x: x.shift(1).rolling(window, min_periods=3).mean())
            )

    # Season-to-date
    for col in ["overall_accuracy", "total_run_impact"]:
        if col not in ump.columns:
            continue
        ump[f"ump_{col}_STD"] = (
            ump.groupby(["umpire", "season"])[col]
               .transform(lambda x: x.shift(1).expanding().mean())
        )

    keep = ["game_pk", "umpire", "date"] + [c for c in ump.columns if c.startswith("ump_")]
    result = ump[keep].copy()
    logger.info(f"  Umpires: {len(result):,} games | {result['umpire'].nunique()} umpires")
    return result


def build_batter_rolling_stats(statcast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-batter rolling stats over last 50 PA. shift(1) prevents leakage.
    Returns (batter, game_pk)-keyed DataFrame.

    Direct port of notebook cell 30.
    """
    logger.info("Building batter rolling stats")
    sc = statcast_df[statcast_df["events"].notna()].copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    sc["is_hit"]      = sc["events"].isin(["single", "double", "triple", "home_run"]).astype(int)
    sc["is_bb"]       = sc["events"].isin(["walk", "intent_walk"]).astype(int)
    sc["is_k"]        = sc["events"].isin(["strikeout", "strikeout_double_play"]).astype(int)
    sc["is_hard_hit"] = (sc["launch_speed"] >= 95).astype(int)
    sc["is_barrel"]   = (sc["launch_speed_angle"] == 6).astype(int)
    sc = sc.sort_values(["batter", "game_date", "at_bat_number"])

    def rolling_batter(g, w=50):
        res = {}
        for col in ["woba_value", "is_hit", "is_bb", "is_k", "is_hard_hit", "is_barrel"]:
            if col in g.columns:
                res[f"batter_{col}_L{w}"] = g[col].shift(1).rolling(w, min_periods=5).mean()
        return pd.DataFrame(res, index=g.index)

    rolled = sc.groupby("batter", group_keys=False).apply(rolling_batter)
    sc = pd.concat([sc[["batter", "game_pk", "stand", "p_throws"]], rolled], axis=1)
    sc = sc.drop_duplicates(subset=["batter", "game_pk"], keep="last")

    logger.info(f"  ✅ {len(sc):,} (batter, game) pairs")
    return sc


def build_feature_table(pitcher_features: pd.DataFrame,
                        weather: pd.DataFrame = None,
                        umpire_features: pd.DataFrame = None) -> pd.DataFrame:
    """
    Master join: pitcher start features + weather + umpire features.

    NOTE: Lineup features (top-3 batter rolling stats) are NOT joined here.
    The notebook joined those from MLB Stats API lineup data, which we don't
    have in GCS yet. When that's added, the lineup join becomes a follow-up
    here.

    Returns the model-ready feature table.

    Direct port of notebook cell 32, simplified for historical-mode only.
    """
    if pitcher_features is None or pitcher_features.empty:
        logger.error("No pitcher_features — cannot build feature table")
        return pd.DataFrame()

    df = pitcher_features.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    start_rows = len(df)

    # Weather join
    if weather is not None and not weather.empty:
        wx = weather.copy()
        wx["game_date"] = pd.to_datetime(wx["game_date"])
        wx_cols = ["game_pk", "temperature_f", "wind_speed_mph", "wind_dir_degrees",
                   "wind_direction", "precipitation_in", "is_outdoor",
                   "wind_out", "wind_in", "game_time_utc", "roof"]
        wx_keep = [c for c in wx_cols if c in wx.columns]
        df = df.merge(wx[wx_keep].drop_duplicates("game_pk"), on="game_pk", how="left")
        matched = df.get("temperature_f", pd.Series(dtype=float)).notna().sum()
        logger.info(f"  After weather join: {len(df):,} rows | matched: {matched:,}")
    else:
        logger.warning("  Weather data unavailable — weather features will be NaN")

    # Umpire join (optional)
    if umpire_features is not None and not umpire_features.empty:
        ump = umpire_features.drop_duplicates("game_pk")
        df = df.merge(ump, on="game_pk", how="left")
        ump_match = df["umpire"].notna().sum() if "umpire" in df.columns else 0
        logger.info(f"  After umpire join: {len(df):,} rows | matched: {ump_match:,}")
    else:
        logger.warning("  Umpire data unavailable — umpire features will be NaN")

    assert len(df) == start_rows, f"Row count changed during join: {start_rows} → {len(df)}"

    # Park factor
    df["park_factor"] = df["home_team"].map(PARK_FACTORS).fillna(1.0)

    # Derived weather features (the notebook had these in weather_master already
    # for some rows; recompute here to be safe and consistent)
    if "temperature_f" in df.columns:
        df["is_cold"] = (df["temperature_f"] < 50).astype("Int64")
        df["is_hot"]  = (df["temperature_f"] > 85).astype("Int64")
    if "wind_speed_mph" in df.columns:
        df["high_wind"] = (df["wind_speed_mph"] > 15).astype("Int64")
    if "days_rest" in df.columns:
        df["short_rest"] = (df["days_rest"] <= 4).astype("Int64")
    if "ump_overall_accuracy_L30" in df.columns and "ump_total_run_impact_L30" in df.columns:
        acc_thresh = df["ump_overall_accuracy_L30"].quantile(0.6)
        run_thresh = df["ump_total_run_impact_L30"].quantile(0.4)
        df["ump_tight_zone"] = (
            (df["ump_overall_accuracy_L30"] > acc_thresh) &
            (df["ump_total_run_impact_L30"]  < run_thresh)
        ).astype("Int64")

    logger.info(f"  ✅ Final feature table: {len(df):,} rows | {len(df.columns)} columns")
    return df


# ── Main runner ────────────────────────────────────────────────────────────


# GCS keys for NRFI artifacts. Match the convention used by HR Pro.
GCS_STATCAST_MASTER         = "Statcast/statcast_master.csv"
GCS_WEATHER_MASTER          = "Weather/weather_master.csv"
GCS_UMPIRES_MASTER          = "Umpires/umpscorecards_master.csv"
GCS_NRFI_PITCHER_FEATURES   = "NRFI_Pro_System/data/pitcher_start_features.csv"
GCS_NRFI_MODEL_FEATURES     = "NRFI_Pro_System/data/model_features.csv"


def run(run_date: str = None) -> dict:
    """
    Rebuild NRFI feature tables and upload to GCS.
    Returns a result dict suitable for the Flask endpoint to return.
    """
    run_date = run_date or date.today().isoformat()
    logger.info(f"NRFI feature build | date={run_date}")

    from mlb_core.storage import read_csv, write_csv, exists
    from mlb_core.config import GCS_BUCKET

    if not GCS_BUCKET:
        return {"status": "error",
                "error": "GCS_BUCKET not set — feature build requires GCS mode"}

    # 1. Load Statcast (full master, all innings — functions filter internally)
    logger.info("Loading Statcast master from GCS")
    try:
        sc = read_csv(GCS_STATCAST_MASTER, low_memory=False)
    except Exception as e:
        logger.error(f"Statcast load failed: {e}")
        return {"status": "error", "error": f"statcast load: {e}"}
    logger.info(f"  Loaded {len(sc):,} pitch rows")

    # 2. Build pitcher start features (filters to inning=1 internally)
    pf = build_pitcher_features(sc)
    if pf.empty:
        return {"status": "error", "error": "pitcher_features empty"}

    # 3. Add handedness splits to pitcher_features
    pf = build_pitcher_handedness_splits(sc, pf)

    # 4. Save pitcher_features intermediate (F5 reuses this)
    try:
        write_csv(pf, GCS_NRFI_PITCHER_FEATURES)
        logger.info(f"  Uploaded {GCS_NRFI_PITCHER_FEATURES}")
    except Exception as e:
        logger.warning(f"pitcher_features upload failed: {e}")

    # 5. Load weather (optional)
    wx = pd.DataFrame()
    if exists(GCS_WEATHER_MASTER):
        try:
            wx = read_csv(GCS_WEATHER_MASTER, low_memory=False)
            logger.info(f"  Weather rows loaded: {len(wx):,}")
        except Exception as e:
            logger.warning(f"Weather load failed: {e}")

    # 6. Load + compute umpire features (optional)
    ump_features = pd.DataFrame()
    if exists(GCS_UMPIRES_MASTER):
        try:
            ump_raw = read_csv(GCS_UMPIRES_MASTER, low_memory=False)
            ump_features = build_umpire_features(ump_raw)
        except Exception as e:
            logger.warning(f"Umpires load failed: {e}")

    # 7. Free statcast reference before final join (memory hygiene; HR hit OOM)
    del sc

    # 8. Master join
    model_features = build_feature_table(
        pitcher_features=pf,
        weather=wx,
        umpire_features=ump_features,
    )
    if model_features.empty:
        return {"status": "error", "error": "model_features empty after join"}

    # 9. Upload final model_features.csv
    try:
        write_csv(model_features, GCS_NRFI_MODEL_FEATURES)
        logger.info(f"  Uploaded {GCS_NRFI_MODEL_FEATURES}")
    except Exception as e:
        logger.error(f"model_features upload failed: {e}")
        return {"status": "error", "error": f"upload: {e}"}

    logger.info(f"NRFI feature build complete | {len(model_features):,} rows")
    result = {"status": "ok", "rows": int(len(model_features)), "columns": int(len(model_features.columns))}
    from mlb_core.storage import write_build_sentinel
    write_build_sentinel("NRFI", result)
    return result
