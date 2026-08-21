"""
runners/build_sb_features.py -- SB (stolen base) Pro v1 nightly feature builder.

Builds per-batter-per-game stolen-base count features for a NegBin count
regressor (target: stolen_bases). Mirrors build_batter_hits_features.py
structure, with two things no prior system in this codebase needed:

1. The TARGET does not come from statcast_master. Verified live 2026-08-20
   (see handoffs/scope_stolen_base_model_2026-08-20.md s2) that this repo's
   Statcast pull cannot see stolen_base_*/caught_stealing_*/pickoff_* events
   at all -- only genuine plate-appearance-ending outcomes ever appear.
   Real per-batter-game SB/CS counts come from mlb_core.data.sb_boxscore's
   MLB Stats API boxscore backfill (Scoring/sb_boxscore_master.csv) instead.

2. A catcher join. No existing system (HR, BATTER_HITS, BATTER_TB) needed a
   third player-entity on the row -- mlb_core.data.aux_joins.join_catcher_aux()
   is new for this. Opposing-catcher identity per historical game comes from
   mlb_core.data.lineups.catcher_backfill_gcs() (AuxData/catcher_identity_master.csv).

"On-base ability" here specifically means singles/walks/HBP -- events that
leave a runner ON FIRST BASE with a stealing opportunity ahead of them. A
double/triple/HR advances PAST that opportunity instead of creating one.

Output GCS keys (matches SB_Pro_System/config_sb.py):
  - SB_Pro_System/data/sb_batter_features.csv  (batter rolling)
  - SB_Pro_System/data/model_features.csv      (joined, used by retrain + runner)

Entrypoint: run(run_date) -- called by main.py for {"system": "SB"}.
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

# Opportunity-generating events specifically: leaves the runner ON FIRST with
# a stealing opportunity ahead. Doubles/triples/HRs are NOT here -- they
# advance past 1B/2B instead of creating a stolen-base opportunity there.
ON_BASE_EVENTS = frozenset(["single", "walk", "hit_by_pitch"])
K_EVENTS       = frozenset(["strikeout", "strikeout_double_play"])

# Columns actually used by this builder (usecols optimization, same pattern
# as every other builder -- full-width statcast_master load OOMs at 2Gi).
_STATCAST_COLS = frozenset([
    "game_date", "game_pk", "batter", "pitcher",
    "events", "p_throws", "stand",
    "home_team", "away_team", "player_name", "inning_topbot",
])


# -- Section 1: Statcast load -------------------------------------------------

def _load_statcast(cfg: dict) -> pd.DataFrame:
    from mlb_core.storage import read_csv
    df = read_csv(
        "Statcast/statcast_master.csv",
        low_memory=False,
        usecols=lambda c: c in _STATCAST_COLS,
    )
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df[df["game_date"].dt.year >= cfg["season_start"]].copy()
    logger.info(
        f"SB build: statcast {len(df):,} rows | "
        f"{df['game_date'].min().date()} -> {df['game_date'].max().date()} | "
        f"{df['batter'].nunique():,} batters | {df['game_pk'].nunique():,} games"
    )
    return df


def _load_sb_boxscore(cfg: dict) -> pd.DataFrame:
    """Load the real SB/CS target from MLB Stats API boxscores (NOT statcast --
    see module docstring)."""
    from mlb_core.storage import read_csv, exists
    key = cfg["gcs_sb_boxscore_master"]
    if not exists(key):
        logger.warning(f"SB: boxscore master not found at {key} -- targets will be all-zero")
        return pd.DataFrame()
    df = read_csv(key, low_memory=False)
    df["batter_mlbam_id"] = pd.to_numeric(df["batter_mlbam_id"], errors="coerce")
    df["game_pk"] = pd.to_numeric(df["game_pk"], errors="coerce")
    logger.info(
        f"SB build: sb_boxscore {len(df):,} rows | "
        f"{int(df['stolen_bases'].sum())} real SB | {int(df['caught_stealing'].sum())} real CS"
    )
    return df


def _load_catcher_identity(cfg: dict) -> pd.DataFrame:
    """Load starting-catcher identity per game (mlb_core.data.lineups.catcher_backfill_gcs())."""
    from mlb_core.storage import read_csv, exists
    key = cfg["gcs_catcher_master"]
    if not exists(key):
        logger.warning(f"SB: catcher identity master not found at {key} -- catcher features will be NaN")
        return pd.DataFrame()
    df = read_csv(key, low_memory=False)
    df["game_pk"] = pd.to_numeric(df["game_pk"], errors="coerce")
    return df


def _load_sprint_speed() -> pd.DataFrame:
    """Load sprint speed leaderboard -> (batter, year, sprint_speed_ft_sec).
    Already fetched nightly by savant_leaderboards.py -- no new data pipeline."""
    try:
        from mlb_core.data.savant_leaderboards import load_savant_leaderboard
        df = load_savant_leaderboard("sprint_speed")
    except Exception as e:
        logger.warning(f"SB: could not load sprint_speed leaderboard: {e}")
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    id_col = next((c for c in ("player_id", "mlbam_id") if c in df.columns), None)
    if id_col is None or "sprint_speed" not in df.columns or "year" not in df.columns:
        logger.warning(f"SB: sprint_speed leaderboard missing expected columns (got: {list(df.columns)})")
        return pd.DataFrame()
    out = df[[id_col, "year", "sprint_speed"]].copy()
    out = out.rename(columns={id_col: "batter", "sprint_speed": "sprint_speed_ft_sec"})
    out["batter"] = pd.to_numeric(out["batter"], errors="coerce")
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out["sprint_speed_ft_sec"] = pd.to_numeric(out["sprint_speed_ft_sec"], errors="coerce")
    out = out.dropna(subset=["batter", "year", "sprint_speed_ft_sec"])
    return out.sort_values(["batter", "year"]).reset_index(drop=True)


# -- Section 2: Batter rolling features ---------------------------------------

def build_sb_batter_rolling(
    sc: pd.DataFrame,
    sb_box: pd.DataFrame,
    existing: pd.DataFrame,
    lookback_days: int = 60,
    run_date: str | None = None,
) -> pd.DataFrame:
    """Per-batter-per-game rolling SB/CS + on-base-opportunity features.

    One row per (batter, game_pk). The real stolen_bases/caught_stealing
    target is merged in from sb_box (MLB Stats API boxscore backfill) --
    statcast's own events column cannot see these plays at all.
    """
    logger.info("SB: building batter rolling features...")

    _ref   = pd.Timestamp(run_date) if run_date else pd.Timestamp.today()
    cutoff = _ref - pd.Timedelta(days=lookback_days)
    keep   = existing[existing["game_date"] < cutoff].copy() if not existing.empty else pd.DataFrame()
    logger.info(f"  keeping {len(keep):,} rows outside {lookback_days}d window")

    sc = sc.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"])

    pa = sc[sc["events"].notna()].copy()
    if pa.empty:
        return existing

    pa["on_base"] = pa["events"].isin(ON_BASE_EVENTS).astype(int)
    pa["single"]  = (pa["events"] == "single").astype(int)
    pa["bb"]      = (pa["events"] == "walk").astype(int)
    pa["hbp"]     = (pa["events"] == "hit_by_pitch").astype(int)
    pa["k"]       = pa["events"].isin(K_EVENTS).astype(int)
    pa["season"]  = pa["game_date"].dt.year

    # Opposing pitcher + handedness + home/away context. player_name here is
    # the PITCHER's own name (this repo's statcast pull is player_type=pitcher,
    # so 'player_name' on every row -- including a batter's own PA rows -- is
    # actually the pitcher who threw it), which is exactly the join key needed
    # for the B-Ref pitcher SB/CS-allowed merge in build_model_features().
    opp_info = sc.groupby(["batter", "game_pk"]).agg(
        opp_pitcher_id =("pitcher", "first"),
        p_throws       =("p_throws", "first") if "p_throws" in sc.columns else ("batter", "first"),
        stand          =("stand", "first") if "stand" in sc.columns else ("batter", "first"),
        game_date      =("game_date", "first"),
        home_team      =("home_team", "first"),
        away_team      =("away_team", "first"),
        player_name    =("player_name", "first") if "player_name" in sc.columns else ("batter", "first"),
        _topbot        =("inning_topbot", "first") if "inning_topbot" in sc.columns else ("batter", "first"),
    ).reset_index()
    if "inning_topbot" in sc.columns:
        opp_info["is_home"] = (opp_info["_topbot"] == "Bot").astype(int)
    else:
        opp_info["is_home"] = 0
    opp_info = opp_info.drop(columns=["_topbot"])

    game_agg = pa.groupby(["batter", "game_pk", "season"]).agg(
        game_date    =("game_date", "first"),
        on_base_game =("on_base",   "sum"),
        single_game  =("single",    "sum"),
        bb_game      =("bb",        "sum"),
        hbp_game     =("hbp",       "sum"),
        k_game       =("k",         "sum"),
        pa_count     =("on_base",   "count"),
    ).reset_index()

    denom = game_agg["pa_count"].replace(0, np.nan)
    game_agg["on_base_rate_game"] = game_agg["on_base_game"] / denom
    game_agg["single_rate_game"]  = game_agg["single_game"]  / denom
    game_agg["bb_rate_game"]      = game_agg["bb_game"]      / denom
    game_agg["hbp_rate_game"]     = game_agg["hbp_game"]     / denom
    game_agg["k_rate_game"]       = game_agg["k_game"]       / denom

    game_agg = game_agg.merge(
        opp_info.drop(columns=["game_date"], errors="ignore"),
        on=["batter", "game_pk"], how="left",
    )

    # Real SB/CS target, from the boxscore master, not statcast.
    if not sb_box.empty:
        sb_slim = (
            sb_box[["batter_mlbam_id", "game_pk", "stolen_bases", "caught_stealing"]]
            .rename(columns={"batter_mlbam_id": "batter"})
            .drop_duplicates(subset=["batter", "game_pk"])
        )
        game_agg["game_pk"] = pd.to_numeric(game_agg["game_pk"], errors="coerce")
        game_agg = game_agg.merge(sb_slim, on=["batter", "game_pk"], how="left")
    else:
        game_agg["stolen_bases"] = np.nan
        game_agg["caught_stealing"] = np.nan

    # A row with no boxscore match yet (backfill still catching up, or a
    # genuine 0-attempt game) is a real zero, not missing -- count models
    # need a real non-negative integer target, and "no attempt" IS 0 SB.
    game_agg["stolen_bases"]    = game_agg["stolen_bases"].fillna(0)
    game_agg["caught_stealing"] = game_agg["caught_stealing"].fillna(0)
    game_agg["sb_attempts_game"] = game_agg["stolen_bases"] + game_agg["caught_stealing"]

    combined = pd.concat([keep, game_agg], ignore_index=True)
    combined.drop_duplicates(subset=["batter", "game_pk"], keep="last", inplace=True)
    combined = combined.sort_values(["batter", "game_date"]).reset_index(drop=True)

    # Drop stale rolling cols so they get recomputed on full history
    rolling_cols = [c for c in combined.columns if any(
        c.endswith(s) for s in ["_L20", "_L50", "_season"]
    )]
    combined = combined.drop(columns=rolling_cols, errors="ignore")

    for src, dst in [
        ("on_base_rate_game", "times_on_base"),
        ("single_rate_game",  "single_rate"),
        ("bb_rate_game",      "bb_rate"),
        ("hbp_rate_game",     "hbp_rate"),
        ("k_rate_game",       "k_rate"),
        ("stolen_bases",      "sb_per_game"),
    ]:
        combined[f"{dst}_L20"] = combined.groupby("batter")[src].transform(
            lambda x: x.shift(1).rolling(20, min_periods=5).mean()
        )
    combined["sb_per_game_L50"] = combined.groupby("batter")["stolen_bases"].transform(
        lambda x: x.shift(1).rolling(50, min_periods=10).mean()
    )
    combined["sb_attempt_rate_L20"] = combined.groupby("batter")["sb_attempts_game"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=5).mean()
    )
    combined["cs_rate_L20"] = combined.groupby("batter")["caught_stealing"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=5).mean()
    )

    # sb_success_pct_L50: ratio of SUMS over the window, not mean-of-ratios --
    # more stable for a rare, mostly-zero event (avoids most players showing
    # NaN or a noisy 0/1 from single-attempt games).
    sb_sum_L50  = combined.groupby("batter")["stolen_bases"].transform(
        lambda x: x.shift(1).rolling(50, min_periods=10).sum()
    )
    att_sum_L50 = combined.groupby("batter")["sb_attempts_game"].transform(
        lambda x: x.shift(1).rolling(50, min_periods=10).sum()
    )
    combined["sb_success_pct_L50"] = sb_sum_L50 / att_sum_L50.replace(0, np.nan)

    combined["sb_season"] = combined.groupby(["batter", "season"])["stolen_bases"].transform(
        lambda x: x.shift(1).expanding().mean()
    )

    # Handedness flags -- direct context, not rolled.
    if "stand" in combined.columns:
        combined["stand_L"] = (combined["stand"] == "L").astype(int)
    if "p_throws" in combined.columns:
        combined["p_throws_L"] = (combined["p_throws"] == "L").astype(int)

    logger.info(
        f"  +{len(game_agg):,} rows recalculated | total: {len(combined):,} | "
        f"sb_per_game_L20 coverage: {combined['sb_per_game_L20'].notna().mean():.1%}"
    )
    return combined


# -- Section 3: Final feature join --------------------------------------------

def build_model_features(
    bf: pd.DataFrame,
    wx: pd.DataFrame,
    order_map: pd.DataFrame,
    sprint_speed: pd.DataFrame,
    catcher_id: pd.DataFrame,
) -> pd.DataFrame:
    """Join weather, batting order, sprint speed, pitcher SB/CS-allowed, and
    the opposing catcher's arm/pop-time into model_features."""
    logger.info(f"SB: building model features from {len(bf):,} batter-game rows...")

    df = bf.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["game_pk"]   = pd.to_numeric(df["game_pk"], errors="coerce")

    # Weather join (same pattern as every other builder)
    if not wx.empty:
        wx_c = wx.copy()
        wx_c["game_pk"] = pd.to_numeric(wx_c["game_pk"], errors="coerce")
        wx_cols = ["game_pk", "temperature_f", "is_outdoor", "roof"]
        wx_keep = [c for c in wx_cols if c in wx_c.columns]
        df = df.merge(wx_c[wx_keep].drop_duplicates("game_pk"), on="game_pk", how="left")
        if "roof" in df.columns:
            dome_mask = df["roof"].isin(["dome", "retractable"])
            df.loc[dome_mask, "temperature_f"] = df.loc[dome_mask, "temperature_f"].fillna(70)
    df["temperature_f"] = df.get("temperature_f", pd.Series(70, index=df.index)).fillna(70)

    home_abbr = df["home_team"].map(TEAM_NAME_TO_ABBR)
    df["is_dome"] = home_abbr.map(lambda t: 1 if STADIUMS_ROOF.get(t) else 0).fillna(0).astype(int)

    df["is_home"] = df.get("is_home", pd.Series(0, index=df.index)).fillna(0).astype(int)

    # Batting order (reuse HR's player order map, same as BATTER_HITS/BATTER_TB)
    if not order_map.empty:
        om = order_map.rename(columns={"batter_id": "batter"})
        df = df.merge(om[["batter", "ewma_batting_order"]], on="batter", how="left")
    df["ewma_batting_order"] = df.get(
        "ewma_batting_order", pd.Series(5.0, index=df.index)
    ).fillna(5.0)

    # Pitch clock regime indicator. Matters more here than for any other
    # system -- the 2023-03-30 rule change (bigger bases, disengagement
    # limits) shifted stolen-base behavior materially, which is also why
    # this system's cfg["season_start"] is 2023, not 2021.
    df["post_pitch_clock"] = (
        pd.to_datetime(df["game_date"]) >= pd.Timestamp("2023-03-30")
    ).astype(int)

    # Sprint speed join: exact-year -> prior-year fallback -> league-median
    # fill. Already-fetched Savant leaderboard, no new pipeline.
    if sprint_speed is not None and not sprint_speed.empty:
        df["season"] = pd.to_datetime(df["game_date"]).dt.year
        latest_speed = (
            sprint_speed.sort_values("year")
            .drop_duplicates(subset="batter", keep="last")
            .set_index("batter")["sprint_speed_ft_sec"]
            .to_dict()
        )
        ss = sprint_speed.rename(columns={"year": "season"})
        df = df.merge(ss[["batter", "season", "sprint_speed_ft_sec"]], on=["batter", "season"], how="left")
        still_nan = df["sprint_speed_ft_sec"].isna()
        if still_nan.any():
            df.loc[still_nan, "sprint_speed_ft_sec"] = df.loc[still_nan, "batter"].map(latest_speed)
        league_median = df["sprint_speed_ft_sec"].median()
        n_filled = df["sprint_speed_ft_sec"].isna().sum()
        df["sprint_speed_ft_sec"] = df["sprint_speed_ft_sec"].fillna(league_median)
        logger.info(
            f"  sprint_speed: {(~still_nan).sum():,} exact-year, "
            f"{still_nan.sum() - n_filled:,} prior-year fallback, "
            f"{n_filled:,} median-filled ({league_median:.2f} ft/sec)"
        )
    else:
        df["sprint_speed_ft_sec"] = np.nan
        logger.warning("  sprint_speed not available -- feature will be NaN at predict time")

    # Opposing pitcher SB/CS-allowed -- season-level B-Ref counting stat,
    # name-keyed (player_name on a batter's own rows is the PITCHER's name --
    # see build_sb_batter_rolling's opp_info comment).
    try:
        from mlb_core.data.auxiliary_features import load_fangraphs_pitching, norm_statcast_name
        bref = load_fangraphs_pitching()
        bref_cols = [c for c in ["pitcher_sb_allowed", "pitcher_cs_allowed"] if c in bref.columns]
        if not bref.empty and bref_cols and "player_name" in df.columns:
            df["_bref_key"]  = df["player_name"].apply(norm_statcast_name)
            df["_aux_year"]  = pd.to_datetime(df["game_date"]).dt.year
            bref_slim = (
                bref[["name_norm", "year"] + bref_cols]
                .rename(columns={"name_norm": "_bref_key", "year": "_aux_year"})
                .drop_duplicates(subset=["_bref_key", "_aux_year"])
            )
            df = df.merge(bref_slim, on=["_bref_key", "_aux_year"], how="left")
            df = df.drop(columns=["_bref_key", "_aux_year"], errors="ignore")
            nan_pct = df["pitcher_sb_allowed"].isna().mean()
            logger.info(f"  pitcher SB/CS-allowed join -- NaN={nan_pct:.1%}")
    except Exception as e:
        logger.warning("SB: pitcher bref SB/CS-allowed join failed (non-fatal): %s", e)

    # Opposing catcher -- the first join in this codebase bringing a THIRD
    # player-entity onto a row. opp_catcher_id resolved from is_home (the
    # batter's own side) + the catcher identity master (home/away catcher
    # per game_pk), then join_catcher_aux() attaches the real pop-time/arm
    # strength stats.
    if not catcher_id.empty:
        cid = catcher_id.copy()
        cid["game_pk"] = pd.to_numeric(cid["game_pk"], errors="coerce")
        df = df.merge(cid, on="game_pk", how="left")
        df["opp_catcher_id"] = np.where(
            df["is_home"] == 1, df.get("away_catcher_id"), df.get("home_catcher_id")
        )
    else:
        df["opp_catcher_id"] = np.nan
        logger.warning("  catcher identity master empty -- opp_catcher_id all-NaN")

    try:
        from mlb_core.data.aux_joins import join_catcher_aux
        df = join_catcher_aux(df, opp_catcher_col="opp_catcher_id")
    except Exception as e:
        logger.warning("SB: catcher aux join failed (non-fatal): %s", e)

    logger.info(f"  model_features: {len(df):,} rows")
    return df


# -- Main runner ---------------------------------------------------------------

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"SB feature build | date={run_date}")

    from mlb.systems.SB_Pro_System.config_sb import cfg
    from mlb_core.storage import read_csv, write_csv, exists, write_build_sentinel
    from mlb_core.config import GCS_BUCKET

    def _load_or_empty(gcs_key, local_path=""):
        try:
            if GCS_BUCKET and gcs_key and exists(gcs_key):
                return read_csv(gcs_key, low_memory=False)
            elif local_path and Path(local_path).exists():
                return pd.read_csv(local_path, low_memory=False)
        except Exception as e:
            logger.warning(f"Could not load {gcs_key}: {e}")
        return pd.DataFrame()

    # 1. Load Statcast master
    logger.info("SB: loading Statcast master")
    try:
        sc = _load_statcast(cfg)
    except Exception as e:
        return {"status": "error", "error": f"Statcast load: {e}"}

    # 2. Load the SB-specific sources + shared context
    sb_box       = _load_sb_boxscore(cfg)
    catcher_id   = _load_catcher_identity(cfg)
    sprint_speed = _load_sprint_speed()
    bf_existing  = _load_or_empty(cfg["gcs_sb_batter_features"], cfg["sb_batter_features"])
    wx           = _load_or_empty(cfg.get("gcs_weather_master", cfg["weather_master"]), cfg["weather_master"])
    order_map    = _load_or_empty(cfg.get("gcs_player_order_map", ""), cfg.get("player_order_map", ""))

    if not bf_existing.empty and "game_date" in bf_existing.columns:
        bf_existing["game_date"] = pd.to_datetime(bf_existing["game_date"])
    if not wx.empty and "game_pk" in wx.columns:
        wx["game_pk"] = pd.to_numeric(wx["game_pk"], errors="coerce")

    # 3. Rebuild
    bf = build_sb_batter_rolling(sc, sb_box, bf_existing, run_date=run_date)
    model_features = build_model_features(bf, wx, order_map, sprint_speed, catcher_id)

    from mlb_core.schemas import validate_df
    validate_df(model_features, "sb_model_features",
                context="SB build_model_features output", raise_on_error=True)

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

    _save(bf,             cfg["gcs_sb_batter_features"], cfg["sb_batter_features"])
    _save(model_features, cfg["gcs_model_features"],     cfg["model_features"])

    result = {"status": "ok", "rows": len(model_features)}
    write_build_sentinel("SB", result)
    logger.info(f"SB feature build complete | {len(model_features):,} rows")
    return result


if __name__ == "__main__":
    import json
    import sys
    _result = run()
    print(json.dumps(_result, indent=2, default=str))
    sys.exit(0 if _result.get("status") == "ok" else 1)
