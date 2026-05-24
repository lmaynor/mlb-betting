"""
runners/build_batter_hits_features.py — BATTER_HITS Pro v1 nightly feature builder.

Builds per-batter-per-game hit count features from Statcast for the NegBin
count regressor (target: batter_hits). Mirrors build_hr_features.py structure
but focused on contact quality / BABIP / batted ball profile rather than
launch angle / HR zone metrics.

Output GCS keys (matches BATTER_HITS_System/config_batter_hits.py):
  - BATTER_HITS_System/data/batter_hits_features.csv   (batter rolling)
  - BATTER_HITS_System/data/pitcher_hits_features.csv  (pitcher rolling)
  - BATTER_HITS_System/data/model_features.csv         (joined, used by retrain + runner)

Entrypoint: run(run_date) — called by main.py for {"system": "BATTER_HITS"}.
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


# ── Park hits factors ─────────────────────────────────────────────────────────
# Approximate park factors for singles + doubles + triples.
# Turf parks (TB, TOR, KC) and hitter-friendly environments (COL, BOS, TEX).
HITS_PARK_FACTORS = {
    "ARI": 1.03, "ATL": 1.01, "BAL": 1.00, "BOS": 1.10, "CHC": 1.02,
    "CWS": 1.03, "CIN": 1.02, "CLE": 0.97, "COL": 1.20, "DET": 0.98,
    "HOU": 0.96, "KC":  1.04, "LAA": 0.99, "LAD": 0.98, "MIA": 0.97,
    "MIL": 1.00, "MIN": 0.99, "NYM": 1.01, "NYY": 1.05, "OAK": 0.95,
    "PHI": 1.03, "PIT": 0.99, "SD":  0.97, "SF":  0.94, "SEA": 0.97,
    "STL": 1.01, "TB":  1.05, "TEX": 1.05, "TOR": 1.04, "WSH": 1.01,
}

STADIUMS_ROOF = {
    "ARI": "retractable", "HOU": "retractable", "MIA": "retractable",
    "MIL": "retractable", "SEA": "retractable", "TEX": "retractable",
    "TOR": "retractable", "TB":  "dome",
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

HIT_EVENTS = frozenset(["single", "double", "triple", "home_run"])
K_EVENTS    = frozenset(["strikeout", "strikeout_double_play"])


def _normalize_name(name: str) -> str:
    import unicodedata, re
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()


# ── Section 1: Statcast load ──────────────────────────────────────────────────

def _load_statcast(cfg: dict) -> pd.DataFrame:
    from mlb_core.storage import read_csv
    df = read_csv("Statcast/statcast_master.csv", low_memory=False)

    if "bat_speed" in df.columns:
        df = df.drop(columns=["bat_speed"])

    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df[df["game_date"].dt.year >= cfg["season_start"]].copy()

    logger.info(
        f"BATTER_HITS build: statcast {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"{df['batter'].nunique():,} batters | {df['game_pk'].nunique():,} games"
    )
    return df


# ── Section 2: Batter rolling features ───────────────────────────────────────

def build_batter_hits_rolling(
    sc: pd.DataFrame,
    existing: pd.DataFrame,
    lookback_days: int = 60,
    run_date: str | None = None,
) -> pd.DataFrame:
    """
    Per-batter-per-game rolling feature aggregation.

    Computes: hits count, PA, BABIP components, batted ball profile, swing
    metrics. Then rolls each metric over L20/L50 game windows with shift(1)
    to prevent leakage. Also computes platoon splits (hits rate vs LHP/RHP).

    One row per (batter, game_pk) for training history.
    Today's slate rows are appended in build_model_features() with NaN target.
    """
    logger.info("BATTER_HITS: building batter rolling features...")

    _ref    = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff  = _ref - pd.Timedelta(days=lookback_days)
    keep    = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()
    logger.info(f"  keeping {len(keep):,} rows outside {lookback_days}d window")

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    # Plate appearances
    pa = sc[sc["events"].notna()].copy()
    if pa.empty:
        return existing

    pa["hit"]     = pa["events"].isin(HIT_EVENTS).astype(int)
    pa["non_hr_hit"] = pa["events"].isin(["single", "double", "triple"]).astype(int)
    pa["hr"]      = (pa["events"] == "home_run").astype(int)
    pa["k"]       = pa["events"].isin(K_EVENTS).astype(int)

    pa["launch_speed"] = pd.to_numeric(pa["launch_speed"], errors="coerce")
    pa["hard_hit"]     = (pa["launch_speed"] >= 95).astype(int)

    # Batted ball type
    if "bb_type" in pa.columns:
        pa["ld"]  = (pa["bb_type"] == "line_drive").astype(int)
        pa["gb"]  = (pa["bb_type"] == "ground_ball").astype(int)
        pa["fb"]  = (pa["bb_type"] == "fly_ball").astype(int)
        pa["iffb"]= (pa["bb_type"] == "popup").astype(int)
    else:
        la = pd.to_numeric(pa.get("launch_angle", pd.Series(dtype=float)), errors="coerce")
        pa["ld"]   = ((la >= 10) & (la <= 25)).fillna(0).astype(int)
        pa["gb"]   = (la < 10).fillna(0).astype(int)
        pa["fb"]   = (la > 25).fillna(0).astype(int)
        pa["iffb"] = 0

    pa["season"] = pa["game_date"].dt.year

    # Swing / whiff / chase
    swing_game = pd.DataFrame()
    if "description" in sc.columns:
        sw = sc.copy()
        sw["is_swing"] = sw["description"].isin([
            "swinging_strike", "swinging_strike_blocked",
            "foul", "foul_tip", "hit_into_play",
        ]).astype(int)
        sw["is_whiff"] = sw["description"].isin([
            "swinging_strike", "swinging_strike_blocked",
        ]).astype(int)
        if all(c in sw.columns for c in ["plate_x", "plate_z", "sz_top", "sz_bot"]):
            px  = pd.to_numeric(sw["plate_x"], errors="coerce")
            pz  = pd.to_numeric(sw["plate_z"], errors="coerce")
            szt = pd.to_numeric(sw["sz_top"],  errors="coerce")
            szb = pd.to_numeric(sw["sz_bot"],  errors="coerce")
            sw["in_zone"] = ((px.abs() <= 0.83) & (pz >= szb) & (pz <= szt)).astype(int)
            sw["chase"]   = ((sw["is_swing"] == 1) & (sw["in_zone"] == 0)).astype(int)
        else:
            sw["in_zone"] = sw["chase"] = 0

        sg = sw.groupby(["batter", "game_pk"]).agg(
            pitches_seen    =("is_swing", "count"),
            swings          =("is_swing", "sum"),
            whiffs          =("is_whiff", "sum"),
            in_zone_pitches =("in_zone",  "sum"),
            chases          =("chase",    "sum"),
        ).reset_index()
        sg["contact_pct"] = 1.0 - sg["whiffs"] / sg["swings"].replace(0, np.nan)
        oop = (sg["pitches_seen"] - sg["in_zone_pitches"]).replace(0, np.nan)
        sg["chase_pct"]   = sg["chases"] / oop
        swing_game = sg

    # Game-level PA aggregation
    opp_info = sc.groupby(["batter", "game_pk"]).agg(
        opp_pitcher_id =("pitcher", "first"),
        p_throws       =("p_throws", "first") if "p_throws" in sc.columns else ("batter", "first"),
        stand          =("stand", "first") if "stand" in sc.columns else ("batter", "first"),
        game_date      =("game_date", "first"),
        home_team      =("home_team", "first"),
        away_team      =("away_team", "first"),
        player_name    =("player_name", "first") if "player_name" in sc.columns else ("batter", "first"),
    ).reset_index()

    game_agg = pa.groupby(["batter", "game_pk", "season"]).agg(
        game_date    =("game_date",     "first"),
        batter_hits  =("hit",           "sum"),    # TARGET
        non_hr_hits  =("non_hr_hit",    "sum"),
        hr_game      =("hr",            "sum"),
        k_game       =("k",             "sum"),
        pa_count     =("hit",           "count"),  # plate appearances
        ld_game      =("ld",            "mean"),
        gb_game      =("gb",            "mean"),
        fb_game      =("fb",            "mean"),
        hard_hit_game=("hard_hit",      "mean"),
    ).reset_index()

    # BABIP per game: (non_hr_hits) / (PA - K - HR)
    bip = (game_agg["pa_count"] - game_agg["k_game"] - game_agg["hr_game"]).replace(0, np.nan)
    game_agg["babip_game"] = game_agg["non_hr_hits"] / bip

    # Hits rate = hits / PA
    game_agg["hits_rate_game"] = game_agg["batter_hits"] / game_agg["pa_count"].replace(0, np.nan)

    if not swing_game.empty:
        game_agg = game_agg.merge(
            swing_game[["batter", "game_pk", "contact_pct", "chase_pct"]],
            on=["batter", "game_pk"], how="left",
        )

    game_agg = game_agg.merge(opp_info, on=["batter", "game_pk"], how="left")

    combined = pd.concat([keep, game_agg], ignore_index=True)
    combined.drop_duplicates(subset=["batter", "game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["batter", "game_date"]).reset_index(drop=True)

    # Drop stale rolling cols so they get recomputed on full history
    rolling_cols = [c for c in combined.columns if any(
        c.endswith(s) for s in ["_L20", "_L50", "_season"]
    ) and c not in ("hits_rate_season",)]
    combined = combined.drop(columns=rolling_cols, errors="ignore")

    def _season_mean(col):
        return combined.groupby(["batter", "season"])[col].transform(
            lambda x: x.shift(1).expanding().mean()
        )

    for src, dst in [
        ("batter_hits",   "hits_per_game"),
        ("hits_rate_game","hits_rate"),
        ("babip_game",    "babip"),
        ("ld_game",       "ld_rate"),
        ("gb_game",       "gb_rate"),
        ("hard_hit_game", "hard_hit"),
        ("pa_count",      "batter_pa_per_game"),
    ]:
        if src not in combined.columns:
            continue
        combined[f"{dst}_L20"] = combined.groupby("batter")[src].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )
        combined[f"{dst}_L50"] = combined.groupby("batter")[src].transform(
            lambda x: x.shift(1).rolling(50, min_periods=10).mean()
        )

    combined["hits_rate_season"] = _season_mean("hits_rate_game")

    for src, dst in [
        ("contact_pct", "contact_pct"),
        ("chase_pct",   "chase_pct"),
    ]:
        if src not in combined.columns:
            continue
        combined[f"{dst}_L20"] = combined.groupby("batter")[src].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )

    # Platoon splits: hits/PA by pitcher handedness
    if "p_throws" in combined.columns:
        for hand in ("L", "R"):
            mask = combined["p_throws"] == hand
            combined.loc[mask, f"hits_rate_{hand}"] = (
                combined[mask].groupby(["batter", "season"])["hits_rate_game"].transform(
                    lambda x: x.shift(1).expanding().mean()
                )
            )
        combined["hits_vs_hand_career"] = np.where(
            combined["p_throws"] == "L",
            combined["hits_rate_L"],
            combined["hits_rate_R"],
        )
        combined["hits_vs_hand_season"] = combined.groupby(
            ["batter", "season", "p_throws"]
        )["hits_rate_game"].transform(lambda x: x.shift(1).expanding().mean())

    logger.info(
        f"  +{len(game_agg):,} rows recalculated | total: {len(combined):,} | "
        f"mean hits/game L20 coverage: "
        f"{combined['hits_per_game_L20'].notna().mean():.1%}"
        if "hits_per_game_L20" in combined.columns
        else f"  +{len(game_agg):,} rows recalculated | total: {len(combined):,}"
    )
    return combined


# ── Section 3: Pitcher hits-allowed rolling features ─────────────────────────

def build_pitcher_hits_features(
    sc: pd.DataFrame,
    existing: pd.DataFrame,
    lookback_days: int = 60,
    run_date: str | None = None,
) -> pd.DataFrame:
    """Pitcher BABIP-allowed, H/9, GB%, K% — rolling over last 20 starts."""
    logger.info("BATTER_HITS: building pitcher hits-allowed features...")

    _ref   = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)
    keep   = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    pa = sc[sc["events"].notna() & sc["pitcher"].notna()].copy()
    if pa.empty:
        return existing

    pa["hit"]       = pa["events"].isin(HIT_EVENTS).astype(int)
    pa["non_hr_hit"]= pa["events"].isin(["single", "double", "triple"]).astype(int)
    pa["hr"]        = (pa["events"] == "home_run").astype(int)
    pa["k"]         = pa["events"].isin(K_EVENTS).astype(int)

    if "bb_type" in pa.columns:
        pa["gb"] = (pa["bb_type"] == "ground_ball").astype(int)
    else:
        la = pd.to_numeric(pa.get("launch_angle", pd.Series(dtype=float)), errors="coerce")
        pa["gb"] = (la < 10).fillna(0).astype(int)

    game_p = pa.groupby(["pitcher", "game_pk"]).agg(
        game_date    =("game_date", "first"),
        pa_allowed   =("hit",       "count"),
        hits_allowed =("hit",       "sum"),
        non_hr_hits  =("non_hr_hit","sum"),
        hr_allowed   =("hr",        "sum"),
        k_allowed    =("k",         "sum"),
        gb_rate_game =("gb",        "mean"),
    ).reset_index()
    game_p["season"] = pd.to_datetime(game_p["game_date"]).dt.year

    # H/9: hits_allowed / (PA_allowed/27) — rough IP proxy
    game_p["pitcher_hits_per_9_game"] = (
        game_p["hits_allowed"] / (game_p["pa_allowed"] / 27.0).replace(0, np.nan)
    )
    # BABIP allowed
    bip = (game_p["pa_allowed"] - game_p["k_allowed"] - game_p["hr_allowed"]).replace(0, np.nan)
    game_p["pitcher_babip_game"] = game_p["non_hr_hits"] / bip
    # K pct
    game_p["pitcher_k_pct_game"] = game_p["k_allowed"] / game_p["pa_allowed"].replace(0, np.nan)

    # We want *starters* only for the rolling features. Filter to pitchers with
    # >= 12 PA in the game (starter heuristic — same as K Pro's _identify_starters
    # threshold implicit in "most BF per game").
    game_p = game_p[game_p["pa_allowed"] >= 12].copy()

    game_p = game_p.sort_values(["pitcher", "game_date"]).reset_index(drop=True)
    for src, dst in [
        ("pitcher_hits_per_9_game", "pitcher_hits_per_9"),
        ("pitcher_babip_game",      "pitcher_babip_allowed"),
        ("gb_rate_game",            "pitcher_gb_rate"),
        ("pitcher_k_pct_game",      "pitcher_k_pct"),
    ]:
        game_p[f"{dst}_L20"] = game_p.groupby("pitcher")[src].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )

    new_rows = game_p[game_p["game_date"] >= cutoff]
    combined = pd.concat([keep, new_rows], ignore_index=True)
    combined.drop_duplicates(subset=["pitcher", "game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["game_date", "pitcher"]).reset_index(drop=True)

    logger.info(
        f"  +{len(new_rows):,} recalculated | total: {len(combined):,} pitcher-game rows"
    )
    return combined


# ── Section 4: Final feature join ─────────────────────────────────────────────

def build_model_features(
    bf: pd.DataFrame,
    pf: pd.DataFrame,
    wx: pd.DataFrame,
    order_map: pd.DataFrame,
) -> pd.DataFrame:
    """Join batter rolling, pitcher rolling, weather, and context into model_features."""
    logger.info(f"BATTER_HITS: building model features from {len(bf):,} batter-game rows...")

    df = bf.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["game_pk"]   = pd.to_numeric(df["game_pk"], errors="coerce")

    # Pitcher join — opp_pitcher_id matches pitcher column in pf
    if not pf.empty:
        pf_keyed = pf.rename(columns={"pitcher": "opp_pitcher_id"})
        pitcher_cols = ["opp_pitcher_id", "game_pk"] + [
            c for c in pf_keyed.columns
            if c.startswith("pitcher_") and c.endswith("_L20")
        ]
        pf_dedup = (
            pf_keyed[[c for c in pitcher_cols if c in pf_keyed.columns]]
            .drop_duplicates(subset=["opp_pitcher_id", "game_pk"])
        )
        pf_dedup["game_pk"] = pd.to_numeric(pf_dedup["game_pk"], errors="coerce")
        df = df.merge(pf_dedup, on=["opp_pitcher_id", "game_pk"], how="left")
        logger.info(
            f"  after pitcher join: {df['pitcher_hits_per_9_L20'].notna().sum():,} matched"
            if "pitcher_hits_per_9_L20" in df.columns else "  pitcher join done"
        )

    # Weather join
    if not wx.empty:
        wx_c = wx.copy()
        wx_c["game_pk"] = pd.to_numeric(wx_c["game_pk"], errors="coerce")
        wx_cols = ["game_pk", "temperature_f", "is_outdoor", "roof"]
        wx_keep = [c for c in wx_cols if c in wx_c.columns]
        df = df.merge(wx_c[wx_keep].drop_duplicates("game_pk"), on="game_pk", how="left")

        if "roof" in df.columns:
            dome_mask = df["roof"].isin(["dome", "retractable"])
            df.loc[dome_mask, "temperature_f"] = df.loc[dome_mask, "temperature_f"].fillna(70)
        df["temperature_f"] = df["temperature_f"].fillna(70)

    # Dome flag
    home_abbr = df["home_team"].map(TEAM_NAME_TO_ABBR)
    df["is_dome"] = home_abbr.map(
        lambda t: 1 if STADIUMS_ROOF.get(t) else 0
    ).fillna(0).astype(int)
    df["temperature_f"] = df.get("temperature_f", pd.Series(70, index=df.index)).fillna(70)

    # Park hits factor
    df["home_abbr"]      = home_abbr
    df["hits_park_factor"] = df["home_abbr"].map(HITS_PARK_FACTORS).fillna(1.0)

    # is_home: batter's team side
    # Derive from batter_team_side if present; otherwise use home_team == away_team heuristic
    if "batter_team_side" in df.columns:
        df["is_home"] = (df["batter_team_side"] == "home").astype(int)
    else:
        df["is_home"] = 0  # conservative default; corrected at score time

    # Batting order
    if not order_map.empty:
        om = order_map.rename(columns={"batter_id": "batter"})
        if "batter" not in om.columns and "batter_id" in order_map.columns:
            om = om.rename(columns={"batter_id": "batter"})
        df = df.merge(om[["batter", "ewma_batting_order"]], on="batter", how="left")
    df["ewma_batting_order"] = df.get(
        "ewma_batting_order", pd.Series(5.0, index=df.index)
    ).fillna(5.0)

    # Pitch clock regime indicator
    df["post_pitch_clock"] = (
        pd.to_datetime(df["game_date"]) >= pd.Timestamp("2023-03-30")
    ).astype(int)

    logger.info(f"  model_features: {len(df):,} rows")
    return df


# ── Main runner ───────────────────────────────────────────────────────────────

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"BATTER_HITS feature build | date={run_date}")

    from BATTER_HITS_System.config_batter_hits import cfg
    from mlb_core.storage import read_csv, write_csv, exists, write_build_sentinel
    from mlb_core.config import GCS_BUCKET

    def _load_or_empty(gcs_key, local_path=""):
        try:
            if GCS_BUCKET and exists(gcs_key):
                return read_csv(gcs_key, low_memory=False)
            elif local_path and Path(local_path).exists():
                return pd.read_csv(local_path, low_memory=False)
        except Exception as e:
            logger.warning(f"Could not load {gcs_key}: {e}")
        return pd.DataFrame()

    # 1. Load Statcast master
    logger.info("BATTER_HITS: loading Statcast master")
    try:
        sc = _load_statcast(cfg)
    except Exception as e:
        return {"status": "error", "error": f"Statcast load: {e}"}

    # 2. Load existing intermediate files
    bf_existing = _load_or_empty(
        cfg["gcs_batter_hits_features"], cfg["batter_hits_features"]
    )
    pf_existing = _load_or_empty(
        cfg["gcs_pitcher_hits_features"], cfg["pitcher_hits_features"]
    )
    wx = _load_or_empty(
        cfg.get("gcs_weather_master", cfg["weather_master"]), cfg["weather_master"]
    )
    order_map = _load_or_empty(
        cfg.get("gcs_player_order_map", ""), cfg.get("player_order_map", "")
    )

    for frame, col in [(bf_existing, "game_date"), (pf_existing, "game_date")]:
        if not frame.empty and col in frame.columns:
            frame[col] = pd.to_datetime(frame[col])
    if not wx.empty and "game_pk" in wx.columns:
        wx["game_pk"] = pd.to_numeric(wx["game_pk"], errors="coerce")

    # 3. Rebuild
    bf = build_batter_hits_rolling(sc, bf_existing, run_date=run_date)
    pf = build_pitcher_hits_features(sc, pf_existing, run_date=run_date)
    model_features = build_model_features(bf, pf, wx, order_map)

    # 4. Upload
    def _save(df, gcs_key, local_path):
        try:
            if GCS_BUCKET:
                write_csv(df, gcs_key)
                logger.info(f"  uploaded {gcs_key} ({len(df):,} rows)")
            else:
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(local_path, index=False)
                logger.info(f"  saved {local_path} ({len(df):,} rows)")
        except Exception as e:
            logger.warning(f"Save failed {gcs_key}: {e}")

    _save(bf,             cfg["gcs_batter_hits_features"],  cfg["batter_hits_features"])
    _save(pf,             cfg["gcs_pitcher_hits_features"], cfg["pitcher_hits_features"])
    _save(model_features, cfg["gcs_model_features"],        cfg["model_features"])

    result = {"status": "ok", "rows": len(model_features)}
    write_build_sentinel("BATTER_HITS", result)
    logger.info(f"BATTER_HITS feature build complete | {len(model_features):,} rows")
    return result
