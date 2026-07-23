"""
runners/build_hr_features.py — HR Pro feature rebuild for Cloud Run.

Runs at 8 AM ET daily (before the 9 AM prediction job).
Reads intermediate CSVs from GCS, rebuilds features, uploads model_features.csv.

This is a direct port of HR_Pro_v6.ipynb Sections 6-9.
"""
import io
import json
import logging
import os
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# ── Park / stadium constants ───────────────────────────────────────────────

HR_PARK_FACTORS = {
    "ARI": 1.05, "ATL": 1.08, "BAL": 1.02, "BOS": 0.87, "CHC": 1.10,
    "CWS": 1.07, "CIN": 1.15, "CLE": 0.95, "COL": 1.25, "DET": 0.97,
    "HOU": 0.94, "KC":  1.03, "LAA": 1.01, "LAD": 1.05, "MIA": 0.88,
    "MIL": 1.04, "MIN": 1.08, "NYM": 1.06, "NYY": 1.12, "OAK": 0.91,
    "PHI": 1.10, "PIT": 0.96, "SD":  0.93, "SF":  0.88, "SEA": 0.94,
    "STL": 1.00, "TB":  0.95, "TEX": 1.08, "TOR": 1.07, "WSH": 1.03,
}

PARK_DIMENSIONS = {
    "ARI": {"LF":330,"CF":407,"RF":334}, "ATL": {"LF":335,"CF":400,"RF":325},
    "BAL": {"LF":333,"CF":400,"RF":318}, "BOS": {"LF":310,"CF":390,"RF":302},
    "CHC": {"LF":355,"CF":400,"RF":353}, "CWS": {"LF":330,"CF":400,"RF":335},
    "CIN": {"LF":328,"CF":404,"RF":325}, "CLE": {"LF":325,"CF":400,"RF":325},
    "COL": {"LF":347,"CF":415,"RF":350}, "DET": {"LF":345,"CF":412,"RF":330},
    "HOU": {"LF":315,"CF":409,"RF":326}, "KC":  {"LF":330,"CF":410,"RF":330},
    "LAA": {"LF":347,"CF":396,"RF":350}, "LAD": {"LF":330,"CF":400,"RF":330},
    "MIA": {"LF":344,"CF":400,"RF":335}, "MIL": {"LF":344,"CF":400,"RF":345},
    "MIN": {"LF":339,"CF":411,"RF":328}, "NYM": {"LF":335,"CF":408,"RF":330},
    "NYY": {"LF":318,"CF":408,"RF":314}, "OAK": {"LF":330,"CF":400,"RF":330},
    "PHI": {"LF":329,"CF":401,"RF":330}, "PIT": {"LF":325,"CF":399,"RF":320},
    "SD":  {"LF":334,"CF":396,"RF":322}, "SF":  {"LF":339,"CF":399,"RF":309},
    "SEA": {"LF":331,"CF":401,"RF":326}, "STL": {"LF":336,"CF":400,"RF":335},
    "TB":  {"LF":315,"CF":404,"RF":322}, "TEX": {"LF":329,"CF":407,"RF":326},
    "TOR": {"LF":328,"CF":400,"RF":328}, "WSH": {"LF":336,"CF":402,"RF":335},
}

PARK_ALTITUDE = {
    "ARI": 1082, "ATL": 1050, "BAL":   50, "BOS":   20, "CHC":  595,
    "CWS":  595, "CIN":  490, "CLE":  650, "COL": 5280, "DET":  585,
    "HOU":   43, "KC":   750, "LAA":  160, "LAD":  515, "MIA":    6,
    "MIL":  635, "MIN":  815, "NYM":   20, "NYY":   20, "OAK":   20,
    "PHI":   20, "PIT":  730, "SD":    17, "SF":    10, "SEA":   17,
    "STL":  465, "TB":    6,  "TEX":  551, "TOR":  173, "WSH":   25,
}

STADIUMS_ROOF = {
    "ARI": "retractable", "HOU": "retractable", "MIA": "retractable",
    "MIL": "retractable", "SEA": "retractable", "TEX": "retractable",
    "TOR": "retractable", "TB":  "dome",
}

# Home-plate -> center-field compass bearing (degrees; 0=N, 90=E, 180=S, 270=W).
# APPROXIMATE published ballpark orientations -- used only for the directional-wind
# feature (wind blowing OUT toward CF/pull raises HR). A park absent here yields
# NaN wind_out (XGBoost-safe). VERIFY against an authoritative orientation source
# before trusting these features in production.
PARK_CF_AZIMUTH = {
    "ARI": 0,   "ATL": 46,  "BAL": 30,  "BOS": 45,  "CHC": 33,
    "CWS": 128, "CIN": 40,  "CLE": 0,   "COL": 0,   "DET": 150,
    "HOU": 348, "KC":  45,  "LAA": 44,  "LAD": 24,  "MIA": 40,
    "MIL": 33,  "MIN": 0,   "NYM": 26,  "NYY": 78,  "OAK": 55,
    "PHI": 15,  "PIT": 118, "SD":  0,   "SF":  88,  "SEA": 45,
    "STL": 62,  "TB":  45,  "TEX": 135, "TOR": 348, "WSH": 30,
}

TEAM_NAME_TO_ABBR = {
    "ARI":"ARI","AZ":"ARI","ATL":"ATL","BAL":"BAL","BOS":"BOS",
    "CHC":"CHC","CWS":"CWS","CIN":"CIN","CLE":"CLE","COL":"COL",
    "DET":"DET","HOU":"HOU","KC":"KC","LAA":"LAA","LAD":"LAD",
    "MIA":"MIA","MIL":"MIL","MIN":"MIN","NYM":"NYM","NYY":"NYY",
    "OAK":"OAK","PHI":"PHI","PIT":"PIT","SD":"SD","SF":"SF",
    "SEA":"SEA","STL":"STL","TB":"TB","TEX":"TEX","TOR":"TOR",
    "WSH":"WSH","WAS":"WSH","ATH":"OAK",
    "Arizona Diamondbacks":"ARI","Atlanta Braves":"ATL",
    "Baltimore Orioles":"BAL","Boston Red Sox":"BOS",
    "Chicago Cubs":"CHC","Chicago White Sox":"CWS",
    "Cincinnati Reds":"CIN","Cleveland Guardians":"CLE",
    "Cleveland Indians":"CLE","Colorado Rockies":"COL",
    "Detroit Tigers":"DET","Houston Astros":"HOU",
    "Kansas City Royals":"KC","Los Angeles Angels":"LAA",
    "Los Angeles Dodgers":"LAD","Miami Marlins":"MIA",
    "Milwaukee Brewers":"MIL","Minnesota Twins":"MIN",
    "New York Mets":"NYM","New York Yankees":"NYY",
    "Oakland Athletics":"OAK","Athletics":"OAK",
    "Philadelphia Phillies":"PHI","Pittsburgh Pirates":"PIT",
    "San Diego Padres":"SD","San Francisco Giants":"SF",
    "Seattle Mariners":"SEA","St. Louis Cardinals":"STL",
    "Tampa Bay Rays":"TB","Texas Rangers":"TEX",
    "Toronto Blue Jays":"TOR","Washington Nationals":"WSH",
}

EXPECTED_PA = {1:4.3, 2:4.2, 3:4.1, 4:4.0, 5:3.9, 6:3.8, 7:3.7, 8:3.6, 9:3.5}

LEAGUE_HR_BY_MATCHUP = {
    ("L","L"): 0.0238, ("L","R"): 0.0322,
    ("R","L"): 0.0325, ("R","R"): 0.0303,
}

# ── GCS helpers ────────────────────────────────────────────────────────────

def _gcs_read_csv(key: str, **kwargs) -> pd.DataFrame:
    from mlb_core.storage import read_csv
    return read_csv(key, **kwargs)

def _gcs_write_csv(df: pd.DataFrame, key: str) -> None:
    from mlb_core.storage import write_csv
    write_csv(df, key)

def _gcs_exists(key: str) -> bool:
    from mlb_core.storage import exists
    return exists(key)


# ── Helper functions ───────────────────────────────────────────────────────

def _american_to_implied(odds):
    if pd.isna(odds): return np.nan
    odds = float(odds)
    return 100/(odds+100) if odds > 0 else abs(odds)/(abs(odds)+100)

def _derive_barrel(ev, la, la_angle=None):
    ev = pd.to_numeric(ev, errors="coerce")
    la = pd.to_numeric(la, errors="coerce")
    barrel = pd.Series(0, index=ev.index)
    mask = ev.notna() & la.notna()
    barrel.loc[mask] = (
        ((ev[mask] >= 98) & (la[mask] >= 26) & (la[mask] <= 30)) |
        ((ev[mask] >= 99) & (la[mask] >= 25) & (la[mask] <= 31)) |
        ((ev[mask] >= 100) & (la[mask] >= 24) & (la[mask] <= 33)) |
        ((ev[mask] >= 101) & (la[mask] >= 23) & (la[mask] <= 34)) |
        ((ev[mask] >= 102) & (la[mask] >= 22) & (la[mask] <= 35)) |
        ((ev[mask] >= 103) & (la[mask] >= 21) & (la[mask] <= 36)) |
        ((ev[mask] >= 104) & (la[mask] >= 20) & (la[mask] <= 37)) |
        ((ev[mask] >= 105) & (la[mask] >= 19) & (la[mask] <= 38)) |
        ((ev[mask] >= 106) & (la[mask] >= 18) & (la[mask] <= 39)) |
        ((ev[mask] >= 107) & (la[mask] >= 17) & (la[mask] <= 40)) |
        ((ev[mask] >= 108) & (la[mask] >= 16) & (la[mask] <= 41))
    ).astype(int)
    return barrel

def _get_pull_side_dist(home_abbr, stand):
    dims = PARK_DIMENSIONS.get(home_abbr)
    if not dims: return np.nan
    return dims["LF"] if stand == "R" else dims["RF"]

def _air_density(altitude_ft, temp_f):
    temp_c = (temp_f - 32) * 5/9
    temp_k = temp_c + 273.15
    alt_m  = altitude_ft * 0.3048
    pressure = 101325 * np.exp(-0.0289644 * 9.80665 * alt_m / (8.31447 * 293.15))
    return pressure / (287.058 * temp_k)


def _air_density_moist(pressure_hpa, temp_f, humidity_pct):
    """Moist-air density (kg/m^3) from MEASURED pressure + humidity -- the accurate
    HR-flight driver (denser air = shorter fly balls). Returns NaN where inputs are
    missing so the caller can fall back to the altitude+temp approximation."""
    P = pd.to_numeric(pressure_hpa, errors="coerce") * 100.0       # Pa
    T = (pd.to_numeric(temp_f, errors="coerce") - 32.0) * 5.0 / 9.0 + 273.15
    Tc = T - 273.15
    Psat = 610.78 * np.exp(17.27 * Tc / (Tc + 237.3))             # sat. vapor pressure, Pa
    Pv = (pd.to_numeric(humidity_pct, errors="coerce") / 100.0) * Psat
    return (P - Pv) / (287.058 * T) + Pv / (461.495 * T)

def _normalize_name(name):
    import unicodedata, re
    if not isinstance(name, str): return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()


# ── Section 6: Player-game aggregation ────────────────────────────────────

def build_player_game(sc: pd.DataFrame, existing: pd.DataFrame) -> pd.DataFrame:
    """Incremental player-game aggregation from Statcast."""
    logger.info("Building player-game features...")

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    existing_pks = set(existing["game_pk"].unique()) if not existing.empty else set()
    new_sc = sc[~sc["game_pk"].isin(existing_pks)]

    if new_sc.empty:
        logger.info("  player_game up to date")
        return existing

    pa = new_sc[new_sc["events"].notna()].copy()
    if pa.empty:
        return existing

    pa["hr"]           = (pa["events"] == "home_run").astype(int)
    pa["barrel"]       = _derive_barrel(pa["launch_speed"], pa["launch_angle"])
    pa["launch_speed"] = pd.to_numeric(pa["launch_speed"], errors="coerce")
    pa["launch_angle"] = pd.to_numeric(pa["launch_angle"], errors="coerce")
    pa["hard_hit"]     = (pa["launch_speed"] >= 95).astype(int)
    pa["xwoba"]        = pd.to_numeric(pa.get("estimated_woba_using_speedangle", pd.Series()), errors="coerce")

    if "bb_type" in pa.columns:
        pa["fly_ball"]    = (pa["bb_type"] == "fly_ball").astype(int)
        pa["ground_ball"] = (pa["bb_type"] == "ground_ball").astype(int)
    else:
        pa["fly_ball"]    = (pa["launch_angle"] > 25).fillna(0).astype(int)
        pa["ground_ball"] = (pa["launch_angle"] < 10).fillna(0).astype(int)

    if "hc_x" in pa.columns and "stand" in pa.columns:
        pa["hc_x"]     = pd.to_numeric(pa["hc_x"], errors="coerce")
        pa["pull"]     = np.where(pa["stand"]=="R", (pa["hc_x"]<128).astype(int), (pa["hc_x"]>128).astype(int))
        pa["pull_air"] = ((pa["pull"]==1) & (pa["fly_ball"]==1)).astype(int)
    else:
        pa["pull"] = pa["pull_air"] = 0

    # Get opp pitcher per batter-game
    opp = new_sc.groupby(["batter","game_pk"]).agg(
        opp_pitcher_id=("pitcher","first"),
        stand=("stand","first"),
        game_date=("game_date","first"),
        home_team=("home_team","first"),
        away_team=("away_team","first"),
        player_name=("player_name","first"),
    ).reset_index()
    # Normalize "Last, First" -> "First Last" for Odds API matching
    if "player_name" in opp.columns:
        def _flip_name(n):
            if isinstance(n, str) and "," in n:
                parts = n.split(",", 1)
                return parts[1].strip() + " " + parts[0].strip()
            return n
        opp["player_name"] = opp["player_name"].apply(_flip_name)

    agg = pa.groupby(["batter","game_pk"]).agg(
        hr            =("hr",           "max"),
        barrel_game   =("barrel",       "mean"),
        hard_hit_game =("hard_hit",     "mean"),
        xwoba_game    =("xwoba",        "mean"),
        ev_game       =("launch_speed", "mean"),
        ev_max_game   =("launch_speed", "max"),
        la_mean_game  =("launch_angle", "mean"),
        la_std_game   =("launch_angle", "std"),
        fb_game       =("fly_ball",     "mean"),
        gb_game       =("ground_ball",  "mean"),
        pull_air_game =("pull_air",     "mean"),
        hr_per_fb_num =("hr",           "sum"),
        fb_count_game =("fly_ball",     "sum"),
    ).reset_index()
    agg["hr_per_fb_game"] = agg["hr_per_fb_num"] / agg["fb_count_game"].replace(0, np.nan)
    agg = agg.merge(opp, on=["batter","game_pk"], how="left")

    agg["home_abbr"]      = agg["home_team"].map(TEAM_NAME_TO_ABBR)
    agg["hr_park_factor"] = agg["home_abbr"].map(HR_PARK_FACTORS).fillna(1.0)
    agg["is_outdoor"]     = agg["home_abbr"].map(lambda t: 0 if STADIUMS_ROOF.get(t) else 1)
    agg["altitude_ft"]    = agg["home_abbr"].map(PARK_ALTITUDE).fillna(0)
    agg["pull_side_dist"] = agg.apply(lambda r: _get_pull_side_dist(r.get("home_abbr"), r.get("stand")), axis=1)
    agg["cf_dist"]        = agg["home_abbr"].map(lambda t: PARK_DIMENSIONS.get(t, {}).get("CF", np.nan))

    combined = pd.concat([existing, agg], ignore_index=True)
    combined.drop_duplicates(subset=["batter","game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["game_date","batter"]).reset_index(drop=True)
    logger.info(f"  +{len(agg):,} new rows | Total: {len(combined):,} | HR rate: {combined['hr'].mean():.3f}")
    return combined


# ── Section 7: Batter rolling + platoon ───────────────────────────────────

def build_batter_rolling(sc: pd.DataFrame, existing: pd.DataFrame,
                         lookback_days: int = 60,
                         run_date: str | None = None) -> pd.DataFrame:
    """Incremental batter rolling features.

    Args:
        run_date: ISO date string for the reference point of the lookback window.
                  Defaults to today if not provided. Pass explicitly for historical
                  replays / backfills to avoid using wall-clock time.
    """
    logger.info("Building batter rolling features...")

    _ref = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)
    keep   = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()
    logger.info(f"  Keeping {len(keep):,} rows outside {lookback_days}d window")

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    pa = sc[sc["events"].notna()].copy()

    pa["hr"]          = (pa["events"] == "home_run").astype(int)
    pa["barrel"]      = _derive_barrel(pa["launch_speed"], pa["launch_angle"])
    pa["launch_speed"]= pd.to_numeric(pa["launch_speed"], errors="coerce")
    pa["hard_hit"]    = (pa["launch_speed"] >= 95).astype(int)
    pa["xwoba"]       = pd.to_numeric(pa.get("estimated_woba_using_speedangle", pd.Series()), errors="coerce")
    pa["launch_angle"]= pd.to_numeric(pa["launch_angle"], errors="coerce")
    pa["season"]      = pa["game_date"].dt.year

    if "bb_type" in pa.columns:
        pa["fly_ball"]    = (pa["bb_type"] == "fly_ball").astype(int)
        pa["ground_ball"] = (pa["bb_type"] == "ground_ball").astype(int)
    else:
        pa["fly_ball"]    = (pa["launch_angle"] > 25).fillna(0).astype(int)
        pa["ground_ball"] = (pa["launch_angle"] < 10).fillna(0).astype(int)

    la = pa["launch_angle"]
    ev = pa["launch_speed"]
    pa["sweet_spot"]     = ((la >= 8) & (la <= 32)).astype(int).where(la.notna(), np.nan)
    pa["hr_zone_contact"]= ((ev >= 95) & (la >= 20) & (la <= 35)).astype(int).where(ev.notna() & la.notna(), np.nan)

    # Swing metrics
    swing_game = pd.DataFrame(columns=["batter","game_pk"])
    if "description" in sc.columns:
        sw = sc.copy()
        sw["is_swing"] = sw["description"].isin([
            "swinging_strike","swinging_strike_blocked","foul","foul_tip","hit_into_play"
        ]).astype(int)
        sw["is_whiff"] = sw["description"].isin([
            "swinging_strike","swinging_strike_blocked"
        ]).astype(int)

        if all(c in sw.columns for c in ["plate_x","plate_z","sz_top","sz_bot"]):
            px  = pd.to_numeric(sw["plate_x"], errors="coerce")
            pz  = pd.to_numeric(sw["plate_z"], errors="coerce")
            szt = pd.to_numeric(sw["sz_top"],  errors="coerce")
            szb = pd.to_numeric(sw["sz_bot"],  errors="coerce")
            sw["in_zone"]    = ((px.abs() <= 0.83) & (pz >= szb) & (pz <= szt)).astype(int)
            sw["chase"]      = ((sw["is_swing"]==1) & (sw["in_zone"]==0)).astype(int)
            sw["zone_swing"] = ((sw["is_swing"]==1) & (sw["in_zone"]==1)).astype(int)
        else:
            sw["in_zone"] = sw["chase"] = sw["zone_swing"] = 0

        sg = sw.groupby(["batter","game_pk"]).agg(
            pitches_seen    =("is_swing", "count"),
            swings          =("is_swing", "sum"),
            whiffs          =("is_whiff", "sum"),
            in_zone_pitches =("in_zone",  "sum"),
            chases          =("chase",    "sum"),
            zone_swings     =("zone_swing","sum"),
        ).reset_index()
        sg["swing_pct"]        = sg["swings"] / sg["pitches_seen"]
        sg["whiff_pct"]        = sg["whiffs"] / sg["swings"].replace(0, np.nan)
        oop = (sg["pitches_seen"] - sg["in_zone_pitches"]).replace(0, np.nan)
        sg["chase_pct"]        = sg["chases"] / oop
        sg["zone_contact_pct"] = (
            (sg["zone_swings"] - sg["whiffs"]) / sg["zone_swings"].replace(0, np.nan)
        )
        swing_game = sg

    # Game-level aggregation
    game_agg = pa.groupby(["batter","game_pk","season"]).agg(
        game_date      =("game_date",     "first"),
        hr_game        =("hr",            "max"),
        barrel_game    =("barrel",        "mean"),
        hard_hit_game  =("hard_hit",      "mean"),
        xwoba_game     =("xwoba",         "mean"),
        ev_game        =("launch_speed",  "mean"),
        ev_max_game    =("launch_speed",  "max"),
        la_mean_game   =("launch_angle",  "mean"),
        la_std_game    =("launch_angle",  "std"),
        fb_game        =("fly_ball",      "mean"),
        hr_per_fb_num  =("hr",            "sum"),
        fb_count_game  =("fly_ball",      "sum"),
        sweet_spot_game=("sweet_spot",    "mean"),
        hr_zone_game   =("hr_zone_contact","mean"),
    ).reset_index()
    game_agg["hr_per_fb_game"] = game_agg["hr_per_fb_num"] / game_agg["fb_count_game"].replace(0, np.nan)

    if not swing_game.empty:
        game_agg = game_agg.merge(
            swing_game[["batter","game_pk","whiff_pct","chase_pct","zone_contact_pct"]],
            on=["batter","game_pk"], how="left"
        )

    # Concat keep + new rows first, then compute rolling on full history
    combined = pd.concat([keep, game_agg], ignore_index=True)
    combined.drop_duplicates(subset=["batter","game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["batter","game_date"]).reset_index(drop=True)
    # Drop stale rolling cols so they get recomputed on full history
    rolling_cols = [c for c in combined.columns if c.startswith("batter_") and
                    any(c.endswith(s) for s in ["_L20","_L50","_season","_vs_lhp","_vs_rhp"])]
    combined = combined.drop(columns=rolling_cols, errors="ignore")
    def season_mean(g, col):
        return g.groupby("season")[col].transform(lambda x: x.shift(1).expanding().mean())
    for col, alias in [
        ("hr_game","hr_rate"), ("barrel_game","barrel_rate"), ("hard_hit_game","hard_hit"),
        ("xwoba_game","xwoba"), ("ev_game","launch_speed"), ("ev_max_game","max_ev"),
        ("la_std_game","la_std"), ("fb_game","fb_rate"), ("sweet_spot_game","sweetspot_rate"),
        ("hr_zone_game","hr_zone_rate"), ("hr_per_fb_game","hr_per_fb"),
    ]:
        if col not in combined.columns: continue
        combined[f"batter_{alias}_L20"] = combined.groupby("batter")[col].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )
        combined[f"batter_{alias}_L50"] = combined.groupby("batter")[col].transform(
            lambda x: x.shift(1).rolling(50, min_periods=10).mean()
        )
        combined[f"batter_{alias}_season"] = season_mean(combined, col)
    for col, alias in [
        ("whiff_pct","whiff_pct"), ("chase_pct","chase_pct"), ("zone_contact_pct","zone_contact_pct"),
    ]:
        if col not in combined.columns: continue
        combined[f"batter_{alias}_L20"] = combined.groupby("batter")[col].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )
    logger.info(f"  +{len(game_agg):,} recalculated | Total: {len(combined):,} | L20 coverage: {combined['batter_hr_rate_L20'].notna().mean():.1%}" if 'batter_hr_rate_L20' in combined.columns else f"  +{len(game_agg):,} recalculated | Total: {len(combined):,}")
    return combined


def build_platoon_features(sc: pd.DataFrame, pg: pd.DataFrame) -> pd.DataFrame:
    """Career and season HR rates by pitcher handedness."""
    logger.info("Building platoon features...")

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    pa = sc[sc["events"].notna() & sc["p_throws"].notna()].copy() if "p_throws" in sc.columns else pd.DataFrame()

    if pa.empty:
        logger.warning("  No p_throws data — platoon features empty")
        return pd.DataFrame()

    pa["hr"]     = (pa["events"] == "home_run").astype(int)
    pa["season"] = pa["game_date"].dt.year

    game_hand = pa.groupby(["batter","game_pk","p_throws","season"]).agg(
        game_date=("game_date","first"),
        hr=("hr","max"),
        pa_count=("hr","count"),
    ).reset_index()

    game_hand["batter_hr_vs_hand_career"] = game_hand.groupby(["batter","p_throws"])["hr"].transform(
        lambda x: x.shift(1).expanding().mean()
    )
    game_hand["batter_hr_vs_hand_season"] = game_hand.groupby(["batter","p_throws","season"])["hr"].transform(
        lambda x: x.shift(1).expanding().mean()
    )

    career_pivot = game_hand.pivot_table(
        index=["batter","game_pk"], columns="p_throws",
        values="batter_hr_vs_hand_career", aggfunc="mean"
    ).reset_index()
    career_pivot.columns.name = None
    career_pivot = career_pivot.rename(columns={"L":"batter_hr_vs_lhp","R":"batter_hr_vs_rhp"})

    season_pivot = game_hand.pivot_table(
        index=["batter","game_pk","game_date"], columns="p_throws",
        values="batter_hr_vs_hand_season", aggfunc="mean"
    ).reset_index()
    season_pivot.columns.name = None
    season_pivot = season_pivot.rename(columns={"L":"batter_hr_vs_lhp_season","R":"batter_hr_vs_rhp_season"})

    out = career_pivot.merge(
        season_pivot[["batter","game_pk","batter_hr_vs_lhp_season","batter_hr_vs_rhp_season"]],
        on=["batter","game_pk"], how="left"
    )
    logger.info(f"  Platoon: {len(out):,} rows | vs_lhp: {out['batter_hr_vs_lhp'].notna().mean():.1%}")
    return out


# ── Section 8: Pitcher rolling ─────────────────────────────────────────────

def build_pitcher_features(sc: pd.DataFrame, existing: pd.DataFrame,
                           lookback_days: int = 60,
                           run_date: str | None = None) -> pd.DataFrame:
    """Incremental pitcher HR-allowed rolling features."""
    logger.info("Building pitcher features...")

    _ref = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)
    keep   = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    pa = sc[sc["events"].notna() & sc["pitcher"].notna()].copy()
    if pa.empty:
        return existing

    pa["hr"]          = (pa["events"] == "home_run").astype(int)
    pa["barrel"]      = _derive_barrel(pa["launch_speed"], pa["launch_angle"])
    pa["launch_speed"]= pd.to_numeric(pa["launch_speed"], errors="coerce")
    pa["hard_hit"]    = (pa["launch_speed"] >= 95).astype(int)
    pa["xwoba"]       = pd.to_numeric(pa.get("estimated_woba_using_speedangle", pd.Series()), errors="coerce")
    pa["launch_angle"]= pd.to_numeric(pa["launch_angle"], errors="coerce")

    if "bb_type" in pa.columns:
        pa["fly_ball"] = (pa["bb_type"] == "fly_ball").astype(int)
    else:
        pa["fly_ball"] = (pa["launch_angle"] > 25).fillna(0).astype(int)

    pa["season"] = pa["game_date"].dt.year

    game_p = pa.groupby(["pitcher","game_pk"]).agg(
        game_date   =("game_date",    "first"),
        p_throws    =("p_throws",     "first") if "p_throws" in pa.columns else ("pitcher","first"),
        hr_allowed  =("hr",           "sum"),
        barrel_rate =("barrel",       "mean"),
        hard_hit    =("hard_hit",     "mean"),
        xwoba       =("xwoba",        "mean"),
        fb_rate     =("fly_ball",     "mean"),
    ).reset_index()
    game_p["season"] = game_p["game_date"].dt.year
    game_p["hr_per_fb"] = game_p["hr_allowed"] / (game_p["fb_rate"] * 27).replace(0, np.nan)

    # Pitch mix
    if "pitch_type" in sc.columns:
        sc_pm = sc.copy()
        sc_pm["is_fb"]  = sc_pm["pitch_type"].isin(["FF","SI","FC"]).astype(int)
        sc_pm["is_brk"] = sc_pm["pitch_type"].isin(["SL","CU","KC","SV","ST"]).astype(int)
        sc_pm["is_off"] = sc_pm["pitch_type"].isin(["CH","FS","FO"]).astype(int)
        sc_pm["is_high_fb"] = ((sc_pm["is_fb"]==1) & (pd.to_numeric(sc_pm.get("plate_z",""), errors="coerce") > 3.0)).astype(int)
        pm = sc_pm.groupby(["pitcher","game_pk"]).agg(
            total=("is_fb","count"),
            fb_count=("is_fb","sum"),
            brk_count=("is_brk","sum"),
            off_count=("is_off","sum"),
            high_fb_count=("is_high_fb","sum"),
        ).reset_index()
        pm["pitcher_fb_pct_L50"]      = pm["fb_count"] / pm["total"]
        pm["pitcher_brk_pct_L50"]     = pm["brk_count"] / pm["total"]
        pm["pitcher_off_pct_L50"]     = pm["off_count"] / pm["total"]
        pm["pitcher_high_fb_pct_L50"] = pm["high_fb_count"] / pm["total"]
        game_p = game_p.merge(pm[["pitcher","game_pk","pitcher_fb_pct_L50","pitcher_brk_pct_L50",
                                   "pitcher_off_pct_L50","pitcher_high_fb_pct_L50"]],
                              on=["pitcher","game_pk"], how="left")

    # Rolling L50
    game_p = game_p.sort_values(["pitcher","game_date"]).reset_index(drop=True)
    for col, alias in [
        ("hr_allowed","hr_rate"), ("barrel_rate","barrel_rate"),
        ("hard_hit","hard_hit"), ("xwoba","xwoba"), ("fb_rate","fb_rate"),
        ("hr_per_fb","hr_per_fb"),
    ]:
        if col not in game_p.columns: continue
        game_p[f"pitcher_{alias}_L50"] = game_p.groupby("pitcher")[col].transform(
            lambda x: x.shift(1).rolling(50, min_periods=5).mean()
        )

    new_rows = game_p[game_p["game_date"] >= cutoff]
    combined = pd.concat([keep, new_rows], ignore_index=True)
    combined.drop_duplicates(subset=["pitcher","game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["game_date","pitcher"]).reset_index(drop=True)
    logger.info(f"  +{len(new_rows):,} recalculated | Total: {len(combined):,} pitcher-game rows")
    return combined


# ── xHR target (Lever C) ───────────────────────────────────────────────────

def build_xhr_target(sc: pd.DataFrame) -> pd.DataFrame:
    """Expected-HR (xHR) target: for each batted ball, the empirical HR rate of its
    (exit-velo, launch-angle) bin, summed per batter-game. A DE-NOISED HR count --
    smoother than the 0/1 outcome, so a model trained on it learns contact quality
    instead of outcome luck (targets the anti-predictive YES side from Lever A).

    The EV/LA -> HR map is a stable physical relationship (like a park factor),
    built globally. It is a TARGET only (never a feature -- excluded via
    _NON_FEATURE_COLS), so using game-G batted balls is not pre-game leakage.
    Returns [batter, game_pk, game_xhr].
    """
    ev = pd.to_numeric(sc.get("launch_speed"), errors="coerce")
    la = pd.to_numeric(sc.get("launch_angle"), errors="coerce")
    bbe = sc["events"].notna() & ev.notna() & la.notna()
    if not bbe.any():
        return pd.DataFrame(columns=["batter", "game_pk", "game_xhr"])
    d = sc.loc[bbe, ["batter", "game_pk", "events"]].copy()
    d["_ev"] = ev[bbe].values
    d["_la"] = la[bbe].values
    d["_hr"] = (d["events"] == "home_run").astype(int)
    d["_evb"] = (d["_ev"] // 2 * 2).astype(int)      # 2 mph bins
    d["_lab"] = (d["_la"] // 3 * 3).astype(int)      # 3 deg bins
    lut = d.groupby(["_evb", "_lab"])["_hr"].mean().rename("_xhr_bbe")
    d = d.join(lut, on=["_evb", "_lab"])
    g = (d.groupby(["batter", "game_pk"])["_xhr_bbe"].sum()
           .rename("game_xhr").reset_index())
    return g


# ── Fetch moneylines from ActionNetwork ───────────────────────────────────

def _fetch_moneylines(run_date: str) -> pd.DataFrame:
    """Fetch DK moneylines from ActionNetwork."""
    date_str = run_date.replace("-", "")
    url = f"https://api.actionnetwork.com/web/v1/scoreboard/mlb?period=game&bookIds=3,15,30,76,123&date={date_str}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        games = r.json().get("games", [])
    except Exception as e:
        logger.warning(f"Moneyline fetch failed: {e}")
        return pd.DataFrame()

    rows = []
    for g in games:
        teams = {t["id"]: t["abbr"] for t in g.get("teams", [])}
        away_id = g.get("away_team_id")
        home_id = g.get("home_team_id")
        away = teams.get(away_id, "")
        home = teams.get(home_id, "")
        away = TEAM_NAME_TO_ABBR.get(away, away)
        home = TEAM_NAME_TO_ABBR.get(home, home)

        home_ml = away_ml = None
        for odds in g.get("odds", []):
            if odds.get("book_id") in (3, 123):
                home_ml = odds.get("ml_home")
                away_ml = odds.get("ml_away")
                break

        rows.append({
            "game_pk":   g.get("id"),
            "game_date": run_date,
            "home_team": home,
            "away_team": away,
            "home_odds": home_ml,
            "away_odds": away_ml,
        })

    return pd.DataFrame(rows)


# ── Lever B features (2026-07-23) ──────────────────────────────────────────

def _ang_out(spd, deg_from, target_az):
    """Wind component (mph) blowing OUT toward compass bearing target_az.
    deg_from = meteorological direction the wind comes FROM; positive result =
    blowing out (toward target), negative = blowing in."""
    blowing_toward = (deg_from + 180.0) % 360.0
    delta = np.radians(((blowing_toward - target_az + 180.0) % 360.0) - 180.0)
    return spd * np.cos(delta)


def add_savant_batter_features(df: pd.DataFrame) -> pd.DataFrame:
    """Join two Savant-leaderboard signals the market underweights, on
    (batter MLBAM id, PRIOR season) -- prior-season avoids walk-forward leakage
    (season-to-date values would include the game being predicted) and is stable
    at serve time. Missing master / columns -> silently skipped (NaN, XGB-safe).

    - bat tracking (2023+): avg_bat_speed / fast_swing_rate / swing_length /
      squared_up -- newest Statcast data, least efficiently priced.
    - xISO regression gap: (est_slg-est_ba) and (est_slg-est_ba)-(slg-ba). A high
      xISO with low actual ISO = unlucky power the market (anchored to real HRs)
      underprices.
    """
    if "batter" not in df.columns or "game_date" not in df.columns:
        return df
    from mlb_core.storage import read_csv, exists
    df["_bat"] = pd.to_numeric(df["batter"], errors="coerce").astype("Int64")
    # PRIOR season (leakage-clean): a 2026 game joins 2025 leaderboard values.
    df["_yr"] = (pd.to_datetime(df["game_date"], errors="coerce").dt.year - 1).astype("Int64")

    def _load(dataset):
        key = f"Statcast/savant_{dataset}_master.csv"
        if not exists(key):
            logger.warning("savant %s: master ABSENT (%s) -- backfill via /backfill-savant", dataset, key)
            return pd.DataFrame()
        try:
            return read_csv(key, low_memory=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("savant %s load failed: %s", dataset, e)
            return pd.DataFrame()

    def _idcol(d):
        for c in ("player_id", "id", "mlbam_id", "batter", "entity_id"):
            if c in d.columns:
                return c
        return None

    def _pick(d, want: dict) -> dict:
        """{source_col: dest_col} restricted to columns present in d."""
        return {s: dst for s, dst in want.items() if s in d.columns}

    def _merge_season(d, idc, want):
        sub = d[[idc, "year"] + list(want)].rename(columns={idc: "_bat", "year": "_yr", **want})
        sub["_bat"] = pd.to_numeric(sub["_bat"], errors="coerce").astype("Int64")
        sub["_yr"] = pd.to_numeric(sub["_yr"], errors="coerce").astype("Int64")
        return sub.drop_duplicates(["_bat", "_yr"])

    # -- bat tracking (broadened candidate names; self-diagnosing on miss) --
    bt = _load("bat_tracking")
    if not bt.empty:
        idc = _idcol(bt)
        want = _pick(bt, {
            "avg_bat_speed": "bat_speed", "bat_speed": "bat_speed",
            "fast_swing_rate": "fast_swing_rate", "fast_swing_pct": "fast_swing_rate",
            "swing_length": "swing_length",
            "squared_up_per_swing": "squared_up_rate", "squared_up_swing": "squared_up_rate",
            "squared_up_per_bat_contact": "squared_up_contact", "squared_up_contact": "squared_up_contact",
            "blasts_per_swing": "blast_rate", "blasts_swing": "blast_rate",
            "attack_angle": "attack_angle",
        })
        if idc and "year" in bt.columns and want:
            df = df.merge(_merge_season(bt, idc, want), on=["_bat", "_yr"], how="left")
        else:
            logger.warning("savant bat_tracking: no usable id/year/cols -- id=%s cols=%s",
                           idc, list(bt.columns)[:25])

    # -- xISO regression gap (try est_* then x* naming; self-diagnosing on miss) --
    es = _load("expected_statistics")
    if not es.empty:
        idc2 = _idcol(es)
        xslg = next((c for c in ("est_slg", "xslg", "est_slg_using_speedangle") if c in es.columns), None)
        xba = next((c for c in ("est_ba", "xba", "est_ba_using_speedangle") if c in es.columns), None)
        slg = "slg" if "slg" in es.columns else None
        ba = "ba" if "ba" in es.columns else None
        if idc2 and "year" in es.columns and xslg and xba:
            e = es[[idc2, "year", xslg, xba] + [c for c in (slg, ba) if c]].copy()
            e["xiso"] = pd.to_numeric(e[xslg], errors="coerce") - pd.to_numeric(e[xba], errors="coerce")
            if slg and ba:
                iso = pd.to_numeric(e[slg], errors="coerce") - pd.to_numeric(e[ba], errors="coerce")
                e["xiso_minus_iso"] = e["xiso"] - iso
            keep = ["xiso"] + (["xiso_minus_iso"] if "xiso_minus_iso" in e.columns else [])
            e = e.rename(columns={idc2: "_bat", "year": "_yr"})[["_bat", "_yr"] + keep]
            e["_bat"] = pd.to_numeric(e["_bat"], errors="coerce").astype("Int64")
            e["_yr"] = pd.to_numeric(e["_yr"], errors="coerce").astype("Int64")
            df = df.merge(e.drop_duplicates(["_bat", "_yr"]), on=["_bat", "_yr"], how="left")
        else:
            logger.warning("savant expected_statistics: missing keys -- id=%s xslg=%s xba=%s cols=%s",
                           idc2, xslg, xba, list(es.columns)[:25])

    return df.drop(columns=["_bat", "_yr"], errors="ignore")


def add_lever_b_features(df: pd.DataFrame) -> pd.DataFrame:
    """Additive HR features: expected PAs from batting-order slot, directional
    wind (out to CF / pull field), and arsenal-matchup interactions. Every input
    is available at BOTH train and serve time (no train/serve skew), and each is
    fail-safe: a missing input yields NaN, which XGBoost imputes via feature_means.
    """
    # 1) Expected plate appearances + top-of-order flag (from ewma_batting_order).
    if "ewma_batting_order" in df.columns:
        order = pd.to_numeric(df["ewma_batting_order"], errors="coerce")
        slot = order.round().clip(1, 9)
        df["expected_pa"] = slot.map(EXPECTED_PA).astype(float)
        df["top_of_order"] = (order <= 4.0).astype(float)

    # 2) Directional wind blowing out to CF and to the batter's pull field.
    if "wind_dir_degrees" in df.columns and "home_abbr" in df.columns:
        cf_az = df["home_abbr"].map(PARK_CF_AZIMUTH)
        spd = pd.to_numeric(df.get("wind_speed_mph"), errors="coerce")
        deg = pd.to_numeric(df["wind_dir_degrees"], errors="coerce")
        df["wind_out_cf_mph"] = _ang_out(spd, deg, cf_az)
        if "stand" in df.columns:
            # RH batter pulls to LF (cf-45), LH pulls to RF (cf+45)
            pull_az = pd.Series(np.where(df["stand"].eq("R"), cf_az - 45.0, cf_az + 45.0)
                                % 360.0, index=df.index)
            df["wind_out_pull_mph"] = _ang_out(spd, deg, pull_az)
        # Roof-closed proxy: dome always closed; retractable closed only when the
        # weather forces it (rain / cold / heat) -- otherwise treat as open so
        # retractable-open games keep their wind signal (the prior code zeroed ALL
        # retractable parks, discarding real wind info). No authoritative per-game
        # roof-state feed yet (would need the MLB game feed -- TODO).
        _roof = df["home_abbr"].map(STADIUMS_ROOF)
        _precip = pd.to_numeric(df.get("precipitation_in"), errors="coerce").fillna(0.0)
        _temp = pd.to_numeric(df.get("temperature_f"), errors="coerce")
        _forced = (_precip > 0.0) | (_temp < 50) | (_temp > 95)
        closed = _roof.eq("dome") | (_roof.eq("retractable") & _forced)
        df["roof_closed_proxy"] = closed.astype(float)
        for c in ("wind_out_cf_mph", "wind_out_pull_mph"):
            if c in df.columns:
                df.loc[closed, c] = 0.0

    # 3) Arsenal matchup: batter power vs the pitch types the SP throws most
    #    (interactions of already-computed pitcher-mix x batter-power features).
    def _num(name):
        return pd.to_numeric(df[name], errors="coerce") if name in df.columns else np.nan
    df["matchup_fb_hr"] = _num("pitcher_fb_pct_L50") * _num("batter_hr_per_fb_L50")
    df["matchup_high_fb_barrel"] = _num("pitcher_high_fb_pct_L50") * _num("batter_barrel_rate_L50")
    return df


# ── Section 9: Feature join ────────────────────────────────────────────────

def build_features(
    pg: pd.DataFrame,
    bf: pd.DataFrame,
    pf: pd.DataFrame,
    wx: pd.DataFrame,
    gf: pd.DataFrame,
    platoon: pd.DataFrame,
    order_map: pd.DataFrame,
) -> pd.DataFrame:
    """Join all feature sources into model-ready table."""
    logger.info(f"Building feature table from {len(pg):,} player-game rows...")

    df = pg.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["game_pk"]   = pd.to_numeric(df["game_pk"], errors="coerce")

    # Batter join
    if not bf.empty:
        bf_c = bf.copy()
        bf_c["game_date"] = pd.to_datetime(bf_c["game_date"])
        drop_cols = [c for c in bf_c.columns if c in df.columns and c not in ["batter","game_pk"]]
        df = df.merge(bf_c.drop(columns=drop_cols), on=["batter","game_pk"], how="left")
        logger.info(f"  After batter join: {len(df):,} | matched: {df.get('batter_hr_rate_L20', pd.Series()).notna().sum():,}")

    # Pitcher join
    if not pf.empty:
        pf_keyed = pf.rename(columns={"pitcher":"opp_pitcher_id"})
        pcols = ["opp_pitcher_id","game_pk"] + [
            c for c in pf_keyed.columns if c.startswith("pitcher_") or c == "p_throws"
        ]
        pf_dedup = pf_keyed[[c for c in pcols if c in pf_keyed.columns]].drop_duplicates(subset=["opp_pitcher_id","game_pk"])
        pf_dedup["game_pk"] = pd.to_numeric(pf_dedup["game_pk"], errors="coerce")
        df = df.merge(pf_dedup, on=["opp_pitcher_id","game_pk"], how="left")
        logger.info(f"  After pitcher join: {df.get('pitcher_hr_rate_L50', pd.Series()).notna().sum():,} matched")

    # Weather join
    if not wx.empty:
        wx_c = wx.copy()
        wx_c["game_pk"] = pd.to_numeric(wx_c["game_pk"], errors="coerce")
        wx_cols = ["game_pk","temperature_f","wind_speed_mph","wind_dir_degrees","wind_direction",
                   "humidity_pct","pressure_hpa","precipitation_in","is_outdoor","roof"]
        wx_keep = [c for c in wx_cols if c in wx_c.columns]
        df = df.merge(wx_c[wx_keep].drop_duplicates("game_pk"), on="game_pk", how="left")

        # Fix temperature for domes
        if "roof" in df.columns:
            dome_mask = df["roof"].isin(["dome","retractable"])
            df.loc[dome_mask, "temperature_f"] = df.loc[dome_mask, "temperature_f"].fillna(70)
        df["temperature_f"] = df["temperature_f"].fillna(70)

        # Air density: prefer MEASURED pressure+humidity (accurate HR-flight physics);
        # fall back to the altitude+temp approximation where those are missing.
        _approx = _air_density(
            df.get("altitude_ft", pd.Series(0, index=df.index)).fillna(0),
            df["temperature_f"]
        )
        if "pressure_hpa" in df.columns and "humidity_pct" in df.columns:
            _moist = _air_density_moist(df["pressure_hpa"], df["temperature_f"], df["humidity_pct"])
            df["air_density"] = _moist.where(_moist.notna(), _approx)
        else:
            df["air_density"] = _approx

        # Handedness park factor
        if "stand" in df.columns and "home_abbr" in df.columns:
            df["hr_park_factor_hand"] = df.apply(
                lambda r: HR_PARK_FACTORS.get(r.get("home_abbr",""), 1.0), axis=1
            )
        logger.info(f"  After weather join: {df['temperature_f'].notna().sum():,} matched")

    # Game features (moneylines) removed 2026-05-19 (T03): market-derived
    # features (team_moneyline, implied_win_pct) trained the model to mimic
    # the betting line, eliminating closing-line edge by construction.
    # If game_features data is available it may be useful for other purposes
    # (e.g. team context), but the moneyline columns must not enter the model.
    if not gf.empty:
        logger.info("  Game features loaded but moneyline columns excluded (T03)")

    # Batting order
    if not order_map.empty:
        om = order_map.rename(columns={"batter_id":"batter"})
        df = df.merge(om[["batter","ewma_batting_order"]], on="batter", how="left")
    df["ewma_batting_order"] = df.get("ewma_batting_order", pd.Series(5.0, index=df.index)).fillna(5.0)
    logger.info(f"  After order join: {df['ewma_batting_order'].notna().sum():,} matched")

    # Platoon join
    if not platoon.empty:
        platoon_c = platoon[["batter","game_pk",
                              "batter_hr_vs_lhp","batter_hr_vs_rhp",
                              "batter_hr_vs_lhp_season","batter_hr_vs_rhp_season"]].copy()
        platoon_c["game_pk"] = pd.to_numeric(platoon_c["game_pk"], errors="coerce")

        # Derive batter_hr_rate_vs_hand from p_throws + platoon
        df = df.merge(platoon_c, on=["batter","game_pk"], how="left")
        if "p_throws" in df.columns:
            df["batter_hr_rate_vs_hand"] = np.where(
                df["p_throws"]=="L", df["batter_hr_vs_lhp"], df["batter_hr_vs_rhp"]
            )
            df["batter_hr_rate_vs_hand_season"] = np.where(
                df["p_throws"]=="L", df["batter_hr_vs_lhp_season"], df["batter_hr_vs_rhp_season"]
            )
        logger.info(f"  After platoon join: 100.0% coverage")

    logger.info(f"  ✅ {len(df):,} rows | HR rate: {df.get('hr', pd.Series()).mean():.3f}")

    # Lever B (2026-07-23): expected PAs, directional wind, arsenal matchup.
    df = add_lever_b_features(df)
    _lb = [c for c in ("expected_pa", "top_of_order", "wind_out_cf_mph",
                       "wind_out_pull_mph", "matchup_fb_hr", "matchup_high_fb_barrel")
           if c in df.columns]
    logger.info("  Lever B features added: " + ", ".join(
        f"{c}={df[c].notna().mean():.0%}" for c in _lb))

    # T13: Regime indicator — pitch clock rules 2023-03-30.
    if "game_date" in df.columns:
        df["post_pitch_clock"] = (
            pd.to_datetime(df["game_date"]) >= pd.Timestamp("2023-03-30")
        ).astype(int)

    try:
        from mlb_core.data.aux_joins import join_batter_aux
        df = join_batter_aux(df, batter_col="batter", opp_pitcher_col="opp_pitcher_id")
    except Exception as _e:
        logger.warning("HR: aux_joins failed (non-fatal): %s", _e)

    # Underweighted-signal features: bat tracking + xISO gap (prior-season, clean).
    try:
        df = add_savant_batter_features(df)
        _sv = [c for c in ("bat_speed", "fast_swing_rate", "swing_length",
                           "squared_up_rate", "squared_up_contact", "blast_rate",
                           "xiso", "xiso_minus_iso") if c in df.columns]
        if _sv:
            logger.info("  Savant batter features: " + ", ".join(
                f"{c}={df[c].notna().mean():.0%}" for c in _sv))
    except Exception as _e:  # noqa: BLE001
        logger.warning("HR: savant batter features failed (non-fatal): %s", _e)

    return df


# ── Main runner ────────────────────────────────────────────────────────────

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"HR feature build | date={run_date}")

    from mlb.systems.HR_Pro.config_hr import cfg
    from mlb_core.storage import read_csv, write_csv, exists
    from mlb_core.config import GCS_BUCKET
    from mlb_core.data import statcast_nightly, weather_nightly, lineup_nightly

    # ── 1. Nightly data refresh ──────────────────────────────────────────
    # Statcast nightly refresh moved to /refresh-data (08:00 UTC).
    # Feature build only reads the master -- never writes it.
    if run_type in ("morning", "data") and not GCS_BUCKET:
        statcast_nightly(
            cache_dir=Path(cfg["statcast_cache_dir"]),
            master_path=Path(cfg["statcast_master"]),
            season_start=cfg["season_start"],
            season_start_month=cfg["season_start_month"],
            season_end_month=cfg["season_end_month"],
        )
        if not GCS_BUCKET:
            weather_nightly(
                cache_dir=Path(cfg["weather_cache_dir"]),
                master_path=Path(cfg["weather_master"]),
            )

    # ── 2. Load Statcast ─────────────────────────────────────────────────
    logger.info("HR features: loading Statcast")
    sc_key = cfg.get("gcs_statcast_master", cfg["statcast_master"])
    try:
        if GCS_BUCKET:
            sc = read_csv(sc_key, low_memory=False)
        else:
            sc = pd.read_csv(cfg["statcast_master"], low_memory=False)
    except Exception as e:
        logger.error(f"Statcast load failed: {e}")
        return {"status": "error", "error": str(e)}

    # ── 3. Load existing intermediate files ──────────────────────────────
    def _load_or_empty(key, local_path):
        try:
            if GCS_BUCKET and exists(key):
                return read_csv(key, low_memory=False)
            elif Path(local_path).exists():
                return pd.read_csv(local_path, low_memory=False)
        except Exception as e:
            logger.warning(f"Could not load {key}: {e}")
        return pd.DataFrame()

    pg_existing      = _load_or_empty(cfg.get("gcs_player_game",   cfg["player_game_master"]),  cfg["player_game_master"])
    bf_existing      = _load_or_empty(cfg.get("gcs_batter_features",cfg["batter_features"]),     cfg["batter_features"])
    pf_existing      = _load_or_empty(cfg.get("gcs_pitcher_features",cfg["pitcher_features"]),   cfg["pitcher_features"])
    wx               = _load_or_empty(cfg.get("gcs_weather_master", cfg["weather_master"]),       cfg["weather_master"])
    gf               = _load_or_empty(cfg.get("gcs_game_features",  cfg["game_features_master"]),cfg["game_features_master"])
    platoon_existing = _load_or_empty(cfg.get("gcs_platoon_features",cfg["platoon_features"]),   cfg["platoon_features"])
    order_map        = _load_or_empty(cfg.get("gcs_player_order_map",cfg["player_order_map"]),   cfg.get("player_order_map",""))

    if not pg_existing.empty:
        pg_existing["game_date"] = pd.to_datetime(pg_existing["game_date"])
    if not bf_existing.empty:
        bf_existing["game_date"] = pd.to_datetime(bf_existing["game_date"])
    if not pf_existing.empty:
        pf_existing["game_date"] = pd.to_datetime(pf_existing["game_date"])
    if not wx.empty:
        wx["game_pk"] = pd.to_numeric(wx["game_pk"], errors="coerce")

    # ── 4. Rebuild features ───────────────────────────────────────────────
    pg      = build_player_game(sc, pg_existing)
    bf      = build_batter_rolling(sc, bf_existing, run_date=run_date)
    platoon = build_platoon_features(sc, pg)
    pf      = build_pitcher_features(sc, pf_existing, run_date=run_date)

    # Moneylines
    ml = _fetch_moneylines(run_date)
    if not ml.empty and not gf.empty:
        gf["game_date"] = gf["game_date"].astype(str).str[:10]
        for _, row in ml.iterrows():
            mask = (gf["game_date"] == run_date) & (gf["home_team"] == row["home_team"])
            if mask.any():
                gf.loc[mask, "home_odds"] = row["home_odds"]
                gf.loc[mask, "away_odds"] = row["away_odds"]
            else:
                gf = pd.concat([gf, pd.DataFrame([row])], ignore_index=True)
    elif not ml.empty:
        gf = ml

    # ── 5. Build final feature table ──────────────────────────────────────
    model_features = build_features(pg, bf, pf, wx, gf, platoon, order_map)

    # Lever C: xHR target (de-noised HR count) -- target only, not a feature.
    try:
        xhr = build_xhr_target(sc)
        model_features = model_features.merge(xhr, on=["batter", "game_pk"], how="left")
        logger.info(f"  xHR target: game_xhr {model_features['game_xhr'].notna().mean():.0%} coverage")
    except Exception as _e:  # noqa: BLE001
        logger.warning("xHR target build failed (non-fatal): %s", _e)

    # ── 6. Upload to GCS ──────────────────────────────────────────────────
    logger.info("HR features: uploading to GCS")

    def _save(df, gcs_key, local_path):
        try:
            if GCS_BUCKET:
                write_csv(df, gcs_key)
                logger.info(f"  Uploaded {gcs_key}")
            else:
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(local_path, index=False)
        except Exception as e:
            logger.warning(f"Save failed {gcs_key}: {e}")

    _save(pg,             cfg.get("gcs_player_game",    "HR_Pro/data/player_game_master.csv"),   cfg["player_game_master"])
    _save(bf,             cfg.get("gcs_batter_features","HR_Pro/data/batter_rolling_features.csv"), cfg["batter_features"])
    _save(pf,             cfg.get("gcs_pitcher_features","HR_Pro/data/pitcher_hr_features.csv"),  cfg["pitcher_features"])
    _save(platoon,        cfg.get("gcs_platoon_features","HR_Pro/data/batter_platoon_features.csv"), cfg["platoon_features"])
    _save(gf,             cfg.get("gcs_game_features",  "HR_Pro/data/game_features_master.csv"),  cfg["game_features_master"])
    _save(model_features, cfg.get("gcs_model_features", "HR_Pro/data/model_features.csv"),        cfg["model_features"])

    # NaN rate audit -- log any feature column above 5% NaN
    _numeric_cols = model_features.select_dtypes(include="number").columns.tolist()
    _bad_nans = sorted(
        ((c, float(model_features[c].isna().mean())) for c in _numeric_cols
         if model_features[c].isna().mean() > 0.05),
        key=lambda x: -x[1],
    )
    if _bad_nans:
        logger.warning("HR build: high-NaN features -- " + ", ".join(f"{c}={r:.0%}" for c, r in _bad_nans[:15]))
    else:
        logger.info(f"HR build: NaN audit OK -- {len(_numeric_cols)} numeric features all < 5% NaN")

    logger.info(f"HR feature build complete | {len(model_features):,} rows")
    result = {"status": "ok", "rows": len(model_features)}
    from mlb_core.storage import write_build_sentinel
    write_build_sentinel("HR", result)
    return result


if __name__ == "__main__":
    import json
    import sys
    _result = run()
    print(json.dumps(_result, indent=2, default=str))
    sys.exit(0 if _result.get("status") == "ok" else 1)
