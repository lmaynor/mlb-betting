"""
runners/run_hr.py — HR Pro daily runner for Cloud Run.

Odds source: The Odds API (batter_home_runs market, over 0.5 = anytime HR)
Feature source: GCS model_features.csv (pre-built, updated nightly)
Model: xgb_hr_v6.json loaded from GCS

run() is called by main.py.
"""
import json
import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xgboost as xgb

logger = logging.getLogger(__name__)

# ── The Odds API ──────────────────────────────────────────────────────────
ODDS_API_KEY = "1ad27b4115b12e9893ffed40a7e2cd27"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# HR_FEATURES — 35 features from full retrain (post-SHAP prune)
HR_FEATURES = [
    "batter_hr_rate_L50",
    "batter_barrel_rate_L50",
    "batter_hr_rate_season",
    "batter_hr_rate_vs_hand",
    "batter_max_ev_L20",
    "hr_park_factor_hand",
    "temperature_f",
    "hr_park_factor",
    "batter_launch_speed_L20",
    "pitcher_fb_rate_L50",
    "batter_zone_contact_pct_L20",
    "batter_chase_pct_L20",
    "batter_hr_rate_vs_hand_season",
    "pitcher_hard_hit_L50",
    "batter_hr_per_fb_L20",
    "pitcher_xwoba_L50",
    "altitude_ft",
    "air_density",
    "pitcher_barrel_rate_L50",
    "batter_sweetspot_rate_L20",
    "batter_xwoba_season",
    "pitcher_fb_pct_L50",
    "pull_side_dist",
    "pitcher_high_fb_pct_L50",
    "batter_barrel_rate_L20",
    "batter_launch_speed_L20",
    "batter_whiff_pct_L20",
    "batter_hr_zone_rate_L20",
    "cf_dist",
    "batter_xwoba_L20",
    "ewma_batting_order",
    "implied_win_pct",
    "batter_hr_vs_lhp",
    "batter_hr_vs_rhp",
    "batter_hr_vs_lhp_season",
]


def _fetch_hr_odds(run_date: str) -> dict:
    """
    Fetch anytime HR odds from The Odds API.
    Returns dict: {player_name: {"odds": int, "game_id": str}}
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "mlb-betting/1.0"})

    # Get today's events
    events_url = f"{ODDS_API_BASE}/sports/baseball_mlb/events"
    r = session.get(events_url, params={"apiKey": ODDS_API_KEY}, timeout=30)
    r.raise_for_status()
    events = r.json()

    if not events:
        logger.warning("HR odds: no MLB events found")
        return {}

    player_odds = {}
    requests_used = 0

    for event in events:
        event_id   = event["id"]
        away_team  = event["away_team"]
        home_team  = event["home_team"]

        url = f"{ODDS_API_BASE}/sports/baseball_mlb/events/{event_id}/odds"
        params = {
            "apiKey":      ODDS_API_KEY,
            "regions":     "us",
            "markets":     "batter_home_runs",
            "oddsFormat":  "american",
            "bookmakers":  "draftkings",
        }
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            requests_used += 1
        except Exception as e:
            logger.warning(f"HR odds fetch failed for {away_team}@{home_team}: {e}")
            continue

        data = r.json()
        for bm in data.get("bookmakers", []):
            if bm["key"] != "draftkings":
                continue
            for market in bm.get("markets", []):
                if market["key"] != "batter_home_runs":
                    continue
                for outcome in market.get("outcomes", []):
                    # over 0.5 = anytime HR
                    if outcome.get("name") == "Over" and outcome.get("point") == 0.5:
                        player_name = outcome.get("description", "")
                        if player_name:
                            player_odds[player_name] = {
                                "odds":      outcome["price"],
                                "away_team": away_team,
                                "home_team": home_team,
                                "event_id":  event_id,
                            }

    logger.info(f"HR odds: {len(player_odds)} players | {requests_used} API calls used")
    return player_odds


def _load_model(cfg: dict) -> xgb.Booster:
    """Load XGB model from GCS or local path."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model

    if GCS_BUCKET:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = download_model(
                cfg.get("gcs_model_xgb", cfg["model_xgb"]),
                Path(tmpdir) / "xgb_hr.json",
            )
            booster = xgb.Booster()
            booster.load_model(str(local))
    else:
        booster = xgb.Booster()
        booster.load_model(cfg["model_xgb"])

    meta_path = Path(cfg["model_meta"])
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        booster.best_ntree_limit = meta.get("best_iteration", 0)
        logger.info(f"HR model loaded | AUC={meta.get('auc_oos','?')} | "
                    f"features={len(meta.get('features', HR_FEATURES))}")
    return booster


def _load_features(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Load today's feature rows from GCS or local.
    Filters to rows where game_date == run_date.
    Falls back to yesterday if today not yet available.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv

    key = cfg.get("gcs_model_features", cfg["model_features"])

    try:
        if GCS_BUCKET:
            df = read_csv(key, low_memory=False)
        else:
            df = pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"HR features load failed: {e}")
        return pd.DataFrame()

    df["game_date"] = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d")

    # Try today, fall back to yesterday (features built from yesterday's data)
    today_df = df[df["game_date"] == run_date]
    if today_df.empty:
        yesterday = (date.fromisoformat(run_date) - timedelta(days=1)).isoformat()
        today_df = df[df["game_date"] == yesterday]
        if not today_df.empty:
            logger.info(f"HR features: using {yesterday} (today not available yet)")

    logger.info(f"HR features: {len(today_df)} player-game rows for {run_date}")
    return today_df


def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Build today's HR predictions.

    Returns DataFrame with columns:
        player, game_pk, away_team, home_team,
        model_prob, market_prob, edge, kelly_pct, odds, stake
    Filtered to edge >= cfg["min_edge"].
    """
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct

    # 1. Load model
    booster = _load_model(cfg)

    # 2. Load today's feature rows
    feat_df = _load_features(cfg, run_date)
    if feat_df.empty:
        logger.warning("HR: no feature rows — skipping predictions")
        return pd.DataFrame()

    # 3. Fetch odds
    player_odds = _fetch_hr_odds(run_date)
    if not player_odds:
        logger.warning("HR: no odds available — skipping predictions")
        return pd.DataFrame()

    # 4. Score all players
    avail = [f for f in HR_FEATURES if f in feat_df.columns]
    missing = set(HR_FEATURES) - set(avail)
    if missing:
        logger.warning(f"HR: {len(missing)} features missing: {list(missing)[:5]}")

    X = feat_df[avail].apply(pd.to_numeric, errors="coerce").astype(float)
    dm = xgb.DMatrix(X, feature_names=avail)
    ntree = getattr(booster, "best_ntree_limit", 0)
    preds = booster.predict(dm, iteration_range=(0, ntree) if ntree else None)
    feat_df = feat_df.copy()
    feat_df["model_prob"] = preds

    # 5. Match predictions to odds by player name
    # Normalize names: "Aaron Judge" in odds, player_name in features
    results = []
    for player_name, odds_info in player_odds.items():
        # Try exact match first, then fuzzy
        match = feat_df[feat_df["player_name"] == player_name]
        if match.empty:
            # Try last name match
            last = player_name.split()[-1].lower()
            match = feat_df[feat_df["player_name"].str.lower().str.endswith(last)]

        if match.empty:
            continue

        row = match.iloc[0]
        model_prob  = float(row["model_prob"])
        odds        = odds_info["odds"]
        market_prob = american_to_implied_prob(odds)

        # Vig removal: DK HR market is one-sided, use flat 7% vig strip
        fair_prob   = market_prob / 1.07
        edge        = model_prob - fair_prob
        k_pct       = kpct(edge, odds, cfg["kelly_fraction"])
        stake       = kelly_stake(
            edge, odds,
            bankroll=cfg["BANKROLL"],
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        )

        if edge < cfg["min_edge"]:
            continue

        results.append({
            "player":       player_name,
            "game_pk":      int(row.get("game_pk", 0)),
            "away_team":    odds_info["away_team"],
            "home_team":    odds_info["home_team"],
            "model_prob":   round(model_prob, 4),
            "market_prob":  round(fair_prob, 4),
            "edge":         round(edge, 4),
            "kelly_pct":    round(k_pct, 4),
            "odds":         odds,
            "stake":        stake,
        })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"HR: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"HR run | type={run_type} | date={run_date}")

    from HR_Pro.config_hr import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets, post_summary
    from mlb_core.data import statcast_nightly, weather_nightly, lineup_nightly

    # 1. Nightly data refresh
    if run_type in ("morning", "data"):
        logger.info("HR: refreshing nightly data")
        statcast_nightly(
            cache_dir=Path(cfg["statcast_cache_dir"]),
            master_path=Path(cfg["statcast_master"]),
            season_start=cfg["season_start"],
            season_start_month=cfg["season_start_month"],
            season_end_month=cfg["season_end_month"],
        )
        weather_nightly(
            cache_dir=Path(cfg["weather_cache_dir"]),
            master_path=Path(cfg["weather_master"]),
        )
        lineup_nightly(
            cache_dir=Path(cfg["lineup_cache_dir"]),
            master_path=Path(cfg["lineups_master"]),
        )

    if run_type == "data":
        return {"bets_logged": 0, "message": "data refresh only"}

    # 2. Predictions
    logger.info("HR: generating predictions")
    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("HR: no qualifying bets today")
        post_bets([], system="HR", run_date=run_date)
        return {"bets_logged": 0}

    # 3. Log bets
    tracker     = BetTracker(cfg["bet_db"], system="HR")
    bets_logged = 0
    bet_rows    = []

    for _, row in today_df.iterrows():
        tracker.log_bet(
            game_date   = run_date,
            game_pk     = row.get("game_pk"),
            player      = row.get("player"),
            away_team   = row.get("away_team"),
            home_team   = row.get("home_team"),
            bet_type    = "HR",
            model_prob  = row.get("model_prob"),
            market_prob = row.get("market_prob"),
            edge        = row.get("edge"),
            kelly_pct   = row.get("kelly_pct"),
            odds        = row.get("odds"),
            stake       = row.get("stake"),
            paper       = cfg["PAPER"],
        )
        bets_logged += 1
        bet_rows.append(row.to_dict())

    # 4. Discord
    post_bets(bet_rows, system="HR", run_date=run_date)
    stats = tracker.summary(season=run_date[:4])
    post_summary(stats, system="HR", run_date=run_date)

    logger.info(f"HR: {bets_logged} bets logged")
    return {"bets_logged": bets_logged}
