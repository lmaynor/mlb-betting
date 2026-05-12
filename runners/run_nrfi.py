"""
runners/run_nrfi.py — NRFI daily runner for Cloud Run.

run() is called by main.py. It:
  1. (morning) Refreshes nightly data via mlb_core.data
  2. Loads model from GCS / local
  3. Builds today's feature rows
  4. Generates predictions and filters by edge threshold
  5. Logs qualifying bets via BetTracker
  6. Posts signals to Discord

This file wires the pieces together. The actual feature engineering and
prediction logic lives in the NRFI notebook — replicate those cells here
once you migrate from notebook to script.  The stubs below show exactly
where each notebook section plugs in.
"""
import logging
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"NRFI run | type={run_type} | date={run_date}")

    from NRFI_Pro_System.config_nrfi import cfg
    from mlb_core.odds import american_to_implied_prob, remove_vig, kelly_stake, kelly_pct
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets, post_summary
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 2. Load model
    # ------------------------------------------------------------------
    logger.info("NRFI: loading model")
    from mlb_core.models import XGBModel

    with tempfile.TemporaryDirectory() as tmpdir:
        halfinn_path = download_model(
            cfg.get("gcs_model_halfinn", cfg["model_halfinn"]),
            Path(tmpdir) / "xgb_halfinn.json",
        ) if GCS_BUCKET else Path(cfg["model_halfinn"])

        gamelevel_path = download_model(
            cfg.get("gcs_model_gamelevel", cfg["model_gamelevel"]),
            Path(tmpdir) / "xgb_gamelevel.json",
        ) if GCS_BUCKET else Path(cfg["model_gamelevel"])

        meta_path = download_model(
            cfg.get("gcs_model_meta", cfg["model_meta"]),
            Path(tmpdir) / "model_meta.json",
        ) if GCS_BUCKET else Path(cfg["model_meta"])

        model_halfinn   = XGBModel.load(halfinn_path, meta_path)
        model_gamelevel = XGBModel.load(gamelevel_path, meta_path)

    # ------------------------------------------------------------------
    # 3 & 4. Feature building + predictions
    # ── REPLACE THIS STUB with your notebook Section 3–8 logic ────────
    # Expected output: today_df — one row per game with columns:
    #   game_pk, away_team, home_team, game_date,
    #   model_prob, market_prob, edge, odds, stake
    # ------------------------------------------------------------------
    logger.info("NRFI: building features and generating predictions")
    today_df = _build_predictions(cfg, model_halfinn, model_gamelevel, run_date)

    if today_df.empty:
        logger.info("NRFI: no qualifying bets today")
        post_bets([], system="NRFI", run_date=run_date)
        return {"bets_logged": 0}

    # ------------------------------------------------------------------
    # 5. Log bets
    # ------------------------------------------------------------------
    tracker   = BetTracker(cfg["bet_db"], system="NRFI")
    bets_logged = 0
    bet_rows  = []

    for _, row in today_df.iterrows():
        bet_id = tracker.log_bet(
            game_date  = run_date,
            game_pk    = row.get("game_pk"),
            away_team  = row.get("away_team"),
            home_team  = row.get("home_team"),
            bet_type   = "NRFI",
            model_prob = row.get("model_prob"),
            market_prob= row.get("market_prob"),
            edge       = row.get("edge"),
            kelly_pct  = row.get("kelly_pct"),
            odds       = row.get("odds"),
            stake      = row.get("stake"),
            paper      = cfg["PAPER"],
        )
        bets_logged += 1
        bet_rows.append(row.to_dict())

    # ------------------------------------------------------------------
    # 6. Discord
    # ------------------------------------------------------------------
    post_bets(bet_rows, system="NRFI", run_date=run_date)

    stats = tracker.summary(season=run_date[:4])
    post_summary(stats, system="NRFI", run_date=run_date)

    logger.info(f"NRFI: {bets_logged} bets logged")
    return {"bets_logged": bets_logged}


def _build_predictions(cfg, model_halfinn, model_gamelevel, run_date: str) -> pd.DataFrame:
    """
    Stub: replace with your notebook Sections 3–8.

    Must return a DataFrame with columns:
        game_pk, away_team, home_team, model_prob,
        market_prob, edge, kelly_pct, odds, stake
    Rows filtered to edge >= cfg["min_edge"].
    """
    # ── PASTE YOUR NOTEBOOK PREDICTION LOGIC HERE ──────────────────────
    logger.warning("NRFI _build_predictions: using stub — replace with notebook logic")
    return pd.DataFrame()
