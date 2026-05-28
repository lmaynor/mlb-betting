"""
runners/build_batter_tb_features.py - BATTER_TB Pro v1 feature builder.

Builds per-batter total-bases count features from Statcast for a count:poisson
model. The target is batter_total_bases.
"""
from __future__ import annotations

import logging
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from runners.build_batter_hits_features import (
    HITS_PARK_FACTORS,
    K_EVENTS,
    STADIUMS_ROOF,
    TEAM_NAME_TO_ABBR,
)
from runners.build_hr_features import _derive_barrel

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

TB_PARK_FACTORS = {
    **HITS_PARK_FACTORS,
    "BOS": 1.12, "CIN": 1.08, "COL": 1.24, "NYY": 1.08,
    "SF": 0.92, "SEA": 0.95, "SD": 0.95,
}

TB_BY_EVENT = {
    "single": 1,
    "double": 2,
    "triple": 3,
    "home_run": 4,
}


def _load_statcast(cfg: dict) -> pd.DataFrame:
    from mlb_core.storage import read_csv

    df = read_csv("Statcast/statcast_master.csv", low_memory=False)
    if "bat_speed" in df.columns:
        df = df.drop(columns=["bat_speed"])
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df[df["game_date"].dt.year >= cfg["season_start"]].copy()
    logger.info(
        "BATTER_TB build: statcast %s rows | %s -> %s",
        f"{len(df):,}",
        df["game_date"].min().date(),
        df["game_date"].max().date(),
    )
    return df


def build_batter_tb_rolling(
    sc: pd.DataFrame,
    existing: pd.DataFrame,
    lookback_days: int = 60,
    run_date: str | None = None,
) -> pd.DataFrame:
    logger.info("BATTER_TB: building batter rolling features...")
    ref = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = ref - pd.Timedelta(days=lookback_days)
    keep = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    pa = sc[sc["events"].notna()].copy()
    if pa.empty:
        return existing

    pa["total_bases"] = pa["events"].map(TB_BY_EVENT).fillna(0).astype(float)
    pa["xbh"] = pa["events"].isin(["double", "triple", "home_run"]).astype(int)
    pa["k"] = pa["events"].isin(K_EVENTS).astype(int)
    pa["launch_speed"] = pd.to_numeric(pa["launch_speed"], errors="coerce")
    pa["launch_angle"] = pd.to_numeric(pa["launch_angle"], errors="coerce")
    pa["hard_hit"] = (pa["launch_speed"] >= 95).astype(int)
    pa["barrel"] = _derive_barrel(pa["launch_speed"], pa["launch_angle"])

    if "bb_type" in pa.columns:
        pa["ld"] = (pa["bb_type"] == "line_drive").astype(int)
        pa["fb"] = (pa["bb_type"] == "fly_ball").astype(int)
    else:
        pa["ld"] = ((pa["launch_angle"] >= 10) & (pa["launch_angle"] <= 25)).fillna(0).astype(int)
        pa["fb"] = (pa["launch_angle"] > 25).fillna(0).astype(int)

    pa["season"] = pa["game_date"].dt.year

    opp_info = sc.groupby(["batter", "game_pk"]).agg(
        opp_pitcher_id=("pitcher", "first"),
        p_throws=("p_throws", "first") if "p_throws" in sc.columns else ("batter", "first"),
        stand=("stand", "first") if "stand" in sc.columns else ("batter", "first"),
        game_date=("game_date", "first"),
        home_team=("home_team", "first"),
        away_team=("away_team", "first"),
        player_name=("player_name", "first") if "player_name" in sc.columns else ("batter", "first"),
    ).reset_index()

    game_agg = pa.groupby(["batter", "game_pk", "season"]).agg(
        game_date=("game_date", "first"),
        batter_total_bases=("total_bases", "sum"),
        xbh_game=("xbh", "sum"),
        pa_count=("total_bases", "count"),
        hard_hit_game=("hard_hit", "mean"),
        barrel_game=("barrel", "mean"),
        ld_game=("ld", "mean"),
        fb_game=("fb", "mean"),
    ).reset_index()
    game_agg["tb_rate_game"] = game_agg["batter_total_bases"] / game_agg["pa_count"].replace(0, np.nan)
    game_agg["xbh_rate_game"] = game_agg["xbh_game"] / game_agg["pa_count"].replace(0, np.nan)
    game_agg["slg_contact_game"] = game_agg["batter_total_bases"] / game_agg["pa_count"].replace(0, np.nan)

    game_agg = game_agg.merge(
        opp_info.drop(columns=["game_date"], errors="ignore"),
        on=["batter", "game_pk"],
        how="left",
    )

    combined = pd.concat([keep, game_agg], ignore_index=True)
    combined.drop_duplicates(subset=["batter", "game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["batter", "game_date"]).reset_index(drop=True)

    stale = [
        c for c in combined.columns
        if any(c.endswith(s) for s in ["_L20", "_L50", "_season"])
    ]
    combined = combined.drop(columns=stale, errors="ignore")

    def season_mean(col: str):
        return combined.groupby(["batter", "season"])[col].transform(
            lambda x: x.shift(1).expanding().mean()
        )

    for src, dst in [
        ("batter_total_bases", "tb_per_game"),
        ("tb_rate_game", "tb_rate"),
        ("xbh_rate_game", "xbh_rate"),
        ("slg_contact_game", "slg_contact"),
        ("hard_hit_game", "hard_hit"),
        ("barrel_game", "barrel_rate"),
        ("ld_game", "ld_rate"),
        ("fb_game", "fb_rate"),
        ("pa_count", "batter_pa_per_game"),
    ]:
        combined[f"{dst}_L20"] = combined.groupby("batter")[src].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )
        combined[f"{dst}_L50"] = combined.groupby("batter")[src].transform(
            lambda x: x.shift(1).rolling(50, min_periods=10).mean()
        )

    combined["tb_rate_season"] = season_mean("tb_rate_game")

    if "p_throws" in combined.columns:
        for hand in ("L", "R"):
            mask = combined["p_throws"] == hand
            combined.loc[mask, f"tb_rate_{hand}"] = (
                combined[mask].groupby(["batter", "season"])["tb_rate_game"].transform(
                    lambda x: x.shift(1).expanding().mean()
                )
            )
        combined["tb_vs_hand_career"] = np.where(
            combined["p_throws"] == "L",
            combined["tb_rate_L"],
            combined["tb_rate_R"],
        )
        combined["tb_vs_hand_season"] = combined.groupby(
            ["batter", "season", "p_throws"]
        )["tb_rate_game"].transform(lambda x: x.shift(1).expanding().mean())

    logger.info("BATTER_TB: batter rows=%s", f"{len(combined):,}")
    return combined


def build_pitcher_tb_features(
    sc: pd.DataFrame,
    existing: pd.DataFrame,
    lookback_days: int = 60,
    run_date: str | None = None,
) -> pd.DataFrame:
    logger.info("BATTER_TB: building pitcher TB-allowed features...")
    ref = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = ref - pd.Timedelta(days=lookback_days)
    keep = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    pa = sc[sc["events"].notna() & sc["pitcher"].notna()].copy()
    if pa.empty:
        return existing

    pa["total_bases_allowed"] = pa["events"].map(TB_BY_EVENT).fillna(0).astype(float)
    pa["xbh_allowed"] = pa["events"].isin(["double", "triple", "home_run"]).astype(int)
    pa["launch_speed"] = pd.to_numeric(pa["launch_speed"], errors="coerce")
    pa["launch_angle"] = pd.to_numeric(pa["launch_angle"], errors="coerce")
    pa["hard_hit"] = (pa["launch_speed"] >= 95).astype(int)
    pa["barrel"] = _derive_barrel(pa["launch_speed"], pa["launch_angle"])

    game_p = pa.groupby(["pitcher", "game_pk"]).agg(
        game_date=("game_date", "first"),
        pa_allowed=("total_bases_allowed", "count"),
        total_bases_allowed=("total_bases_allowed", "sum"),
        xbh_allowed=("xbh_allowed", "sum"),
        hard_hit_allowed=("hard_hit", "mean"),
        barrel_allowed=("barrel", "mean"),
    ).reset_index()
    game_p = game_p[game_p["pa_allowed"] >= 12].copy()
    game_p["pitcher_tb_per_9_game"] = game_p["total_bases_allowed"] / (
        game_p["pa_allowed"] / 27.0
    ).replace(0, np.nan)
    game_p["pitcher_xbh_rate_game"] = game_p["xbh_allowed"] / game_p["pa_allowed"].replace(0, np.nan)

    game_p = game_p.sort_values(["pitcher", "game_date"]).reset_index(drop=True)
    for src, dst in [
        ("pitcher_tb_per_9_game", "pitcher_tb_per_9"),
        ("pitcher_xbh_rate_game", "pitcher_xbh_rate"),
        ("hard_hit_allowed", "pitcher_hard_hit"),
        ("barrel_allowed", "pitcher_barrel_rate"),
    ]:
        game_p[f"{dst}_L20"] = game_p.groupby("pitcher")[src].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )

    new_rows = game_p[game_p["game_date"] >= cutoff]
    combined = pd.concat([keep, new_rows], ignore_index=True)
    combined.drop_duplicates(subset=["pitcher", "game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["game_date", "pitcher"]).reset_index(drop=True)
    logger.info("BATTER_TB: pitcher rows=%s", f"{len(combined):,}")
    return combined


def build_model_features(
    bf: pd.DataFrame,
    pf: pd.DataFrame,
    wx: pd.DataFrame,
    order_map: pd.DataFrame,
) -> pd.DataFrame:
    logger.info("BATTER_TB: joining model features from %s batter-game rows", f"{len(bf):,}")
    df = bf.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["game_pk"] = pd.to_numeric(df["game_pk"], errors="coerce")

    if not pf.empty:
        pf_keyed = pf.rename(columns={"pitcher": "opp_pitcher_id"})
        pcols = ["opp_pitcher_id", "game_pk"] + [
            c for c in pf_keyed.columns if c.startswith("pitcher_") and c.endswith("_L20")
        ]
        pjoin = pf_keyed[[c for c in pcols if c in pf_keyed.columns]].drop_duplicates(
            subset=["opp_pitcher_id", "game_pk"]
        )
        pjoin["game_pk"] = pd.to_numeric(pjoin["game_pk"], errors="coerce")
        df = df.merge(pjoin, on=["opp_pitcher_id", "game_pk"], how="left")

    if not wx.empty:
        wx_c = wx.copy()
        wx_c["game_pk"] = pd.to_numeric(wx_c["game_pk"], errors="coerce")
        wx_keep = [c for c in ["game_pk", "temperature_f", "roof"] if c in wx_c.columns]
        df = df.merge(wx_c[wx_keep].drop_duplicates("game_pk"), on="game_pk", how="left")
        if "roof" in df.columns:
            dome = df["roof"].isin(["dome", "retractable"])
            df.loc[dome, "temperature_f"] = df.loc[dome, "temperature_f"].fillna(70)

    home_abbr = df["home_team"].map(TEAM_NAME_TO_ABBR)
    df["home_abbr"] = home_abbr
    df["tb_park_factor"] = home_abbr.map(TB_PARK_FACTORS).fillna(1.0)
    df["is_dome"] = home_abbr.map(lambda t: 1 if STADIUMS_ROOF.get(t) else 0).fillna(0).astype(int)
    df["temperature_f"] = df.get("temperature_f", pd.Series(70, index=df.index)).fillna(70)
    df["is_home"] = (df.get("batter_team_side", pd.Series("", index=df.index)) == "home").astype(int)

    if not order_map.empty:
        om = order_map.rename(columns={"batter_id": "batter"})
        df = df.merge(om[["batter", "ewma_batting_order"]], on="batter", how="left")
    df["ewma_batting_order"] = df.get("ewma_batting_order", pd.Series(5.0, index=df.index)).fillna(5.0)
    df["post_pitch_clock"] = (df["game_date"] >= pd.Timestamp("2023-03-30")).astype(int)
    logger.info("BATTER_TB: model_features rows=%s", f"{len(df):,}")
    return df


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info("BATTER_TB feature build | date=%s", run_date)

    from BATTER_TB_System.config_batter_tb import cfg
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import exists, read_csv, write_build_sentinel, write_csv

    def _load_or_empty(gcs_key: str, local_path: str = "") -> pd.DataFrame:
        try:
            if GCS_BUCKET and exists(gcs_key):
                return read_csv(gcs_key, low_memory=False)
            if local_path and Path(local_path).exists():
                return pd.read_csv(local_path, low_memory=False)
        except Exception as e:
            logger.warning("Could not load %s: %s", gcs_key, e)
        return pd.DataFrame()

    try:
        sc = _load_statcast(cfg)
    except Exception as e:
        return {"status": "error", "error": f"Statcast load: {e}"}

    bf_existing = _load_or_empty(cfg["gcs_batter_tb_features"], cfg["batter_tb_features"])
    pf_existing = _load_or_empty(cfg["gcs_pitcher_tb_features"], cfg["pitcher_tb_features"])
    wx = _load_or_empty(cfg.get("gcs_weather_master", "Weather/weather_master.csv"), cfg["weather_master"])
    order_map = _load_or_empty(cfg.get("gcs_player_order_map", ""), "")

    for frame in (bf_existing, pf_existing):
        if not frame.empty and "game_date" in frame.columns:
            frame["game_date"] = pd.to_datetime(frame["game_date"])

    bf = build_batter_tb_rolling(sc, bf_existing, run_date=run_date)
    pf = build_pitcher_tb_features(sc, pf_existing, run_date=run_date)
    model_features = build_model_features(bf, pf, wx, order_map)

    def _save(df: pd.DataFrame, gcs_key: str, local_path: str) -> None:
        if GCS_BUCKET:
            write_csv(df, gcs_key)
            logger.info("uploaded %s (%s rows)", gcs_key, f"{len(df):,}")
        else:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(local_path, index=False)

    _save(bf, cfg["gcs_batter_tb_features"], cfg["batter_tb_features"])
    _save(pf, cfg["gcs_pitcher_tb_features"], cfg["pitcher_tb_features"])
    _save(model_features, cfg["gcs_model_features"], cfg["model_features"])

    result = {"status": "ok", "rows": len(model_features)}
    write_build_sentinel("BATTER_TB", result)
    return result


if __name__ == "__main__":
    import json
    import sys

    _result = run()
    print(json.dumps(_result, indent=2, default=str))
    sys.exit(0 if _result.get("status") == "ok" else 1)
