"""
runners/run_f5.py — F5 Pro daily runner for Cloud Run.

Odds source: SGO snapshot at Odds/sgo/latest.json
Feature source: GCS F5_Pro_System/data/model_features.csv (built by
  runners.build_f5_features). NRFI features must rebuild first since F5
  features depend on NRFI's pitcher_start_features.csv.
Model: xgb_f5_v5.json — `binary:logistic`, predicts P(home wins F5).
  Single-output classifier; P(away wins) = 1 - P(home wins).

Port of F5_Pro_v5.ipynb Section 13a (live quick refresh).

run() is called by main.py.
"""
import json
import logging
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)


def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict]:
    """Load the F5 XGBoost model + features + feature_means.

    Returns (booster, features, feature_means). features is the model's
    expected input columns; feature_means provides training-set means for
    NaN-filling at predict time.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(
                cfg["gcs_model_f5"],
                Path(tmpdir) / "xgb_f5.json",
            )
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_f5"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("F5 model_meta missing 'features' key")
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(f"F5 model loaded | features={len(features)} | "
                f"feature_means={len(feature_means)} | "
                f"AUC={meta.get('auc_oos', meta.get('wf_auc_mean', '?'))}")
    return booster, features, feature_means


def _fetch_today_weather(sched: pd.DataFrame) -> dict:
    """Live weather per game keyed by game_pk. Same logic as run_hr."""
    import requests
    from mlb_core.data.weather import STADIUMS

    if sched.empty:
        return {}
    session = requests.Session()
    session.headers.update({"User-Agent": "mlb-betting/1.0"})
    out = {}
    for _, g in sched.iterrows():
        home = g["home_team"]
        stadium = STADIUMS.get(home)
        if stadium is None:
            continue
        lat, lon, roof, _ = stadium
        if roof == "dome":
            out[g["game_pk"]] = {
                "temperature_f":   72.0,
                "wind_speed_mph":  0.0,
                "wind_dir_degrees": 0.0,
                "is_outdoor":      0,
                "roof":            roof,
            }
            continue
        try:
            r = session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude":  lat,
                    "longitude": lon,
                    "hourly":    "temperature_2m,windspeed_10m,winddirection_10m",
                    "timezone":  "UTC",
                    "forecast_days": 2,
                },
                timeout=15,
            )
            r.raise_for_status()
            hourly = r.json().get("hourly", {})
        except Exception as e:
            logger.warning(f"F5 weather fetch failed for {home}: {e}")
            continue
        times = hourly.get("time", [])
        start = g.get("game_time_utc", "")
        try:
            target = pd.to_datetime(start).tz_convert(None) if start else pd.to_datetime("today")
            time_dt = pd.to_datetime(times)
            idx = (time_dt - target).abs().argmin()
        except Exception:
            idx = 0
        out[g["game_pk"]] = {
            "temperature_f":   hourly["temperature_2m"][idx] * 9 / 5 + 32,
            "wind_speed_mph":  hourly["windspeed_10m"][idx] * 0.6214,
            "wind_dir_degrees": hourly["winddirection_10m"][idx],
            "is_outdoor":      1,
            "roof":            roof,
        }
    logger.info(f"F5 weather: {len(out)}/{len(sched)} games")
    return out


def _build_today_feature_rows(cfg: dict, run_date: str,
                              ml_odds_by_abbrev: dict) -> pd.DataFrame:
    """Build one feature row per scheduled game on `run_date`.

    Strategy mirrors notebook Section 13a:
      1. Fetch today's schedule
      2. For each game, find the most recent historical row in
         model_features.csv matching (away_team, home_team)
      3. Override live weather (temperature_f, wind_speed_mph, etc.)
      4. Override implied_home_win_pct from DK no-vig home prob
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv
    from mlb_core.data.lineups import get_today_schedule
    from mlb_core.odds import american_to_implied_prob, remove_vig

    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning(f"F5: no games scheduled for {run_date}")
        return pd.DataFrame()
    logger.info(f"F5: {len(sched)} games on the {run_date} slate")

    feat_key = cfg.get("gcs_model_features") if GCS_BUCKET else cfg["model_features"]
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"F5 features load failed: {e}")
        return pd.DataFrame()
    if feat_df.empty:
        logger.error("F5 features CSV is empty")
        return pd.DataFrame()
    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])

    weather_today = _fetch_today_weather(sched)

    rows = []
    for _, g in sched.iterrows():
        away = g["away_team"]
        home = g["home_team"]
        match = feat_df[(feat_df["away_team"] == away) &
                        (feat_df["home_team"] == home)].sort_values("game_date")
        if match.empty:
            logger.info(f"F5: no historical row for {away} @ {home}")
            continue
        row = match.iloc[-1].to_dict()
        row["game_pk"]   = g["game_pk"]
        row["game_date"] = pd.Timestamp(run_date)
        row["home_team"] = home
        row["away_team"] = away

        # Live weather override
        wx = weather_today.get(g["game_pk"])
        if wx:
            for k, v in wx.items():
                row[k] = v

        # Live implied home win pct from DK (no-vig)
        odds_info = ml_odds_by_abbrev.get((away, home))
        if odds_info:
            ih = american_to_implied_prob(odds_info["home_odds"])
            ia = american_to_implied_prob(odds_info["away_odds"])
            fh, _ = remove_vig(ih, ia)
            row["implied_home_win_pct"] = fh
            row["_home_odds"] = odds_info["home_odds"]
            row["_away_odds"] = odds_info["away_odds"]
            row["_event_id"]  = odds_info.get("event_id")
        else:
            row["_home_odds"] = None
            row["_away_odds"] = None
            row["_event_id"]  = None
        rows.append(row)

    if not rows:
        logger.warning("F5: 0 games matched both schedule and feature snapshot")
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    logger.info(f"F5: {len(out)} candidate game rows assembled")
    return out


def _score(booster: xgb.Booster, features: list[str], feature_means: dict,
           df: pd.DataFrame) -> np.ndarray:
    """Score df. Missing values filled with feature_means where known."""
    avail = [f for f in features if f in df.columns]
    missing = set(features) - set(avail)
    if missing:
        logger.warning(f"F5: {len(missing)} features missing from input: "
                       f"{sorted(missing)[:5]}")
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            mean = feature_means.get(col)
            if mean is None:
                continue
            X[col] = X[col].fillna(float(mean))
    X = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    if ntree:
        return booster.predict(dm, iteration_range=(0, ntree))
    return booster.predict(dm)


def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    """Build today's F5 predictions.

    Returns DataFrame with one row per qualifying bet, columns:
        player (label), game_pk, away_team, home_team, side ('HOME'|'AWAY'),
        model_prob, market_prob, edge, kelly_pct, odds, stake
    """
    from mlb_core.odds import sgo
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct, remove_vig
    from mlb_core.odds.dk_scraper import resolve_team

    booster, features, feature_means = _load_model(cfg)

    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("F5: SGO snapshot empty or missing — skipping")
        return pd.DataFrame()
    ml_by_event = sgo.extract_f5_ml_odds(events)
    if not ml_by_event:
        logger.warning("F5: 0 events have F5 ML prices on DK — skipping")
        return pd.DataFrame()

    ml_by_abbrev: dict = {}
    for ev_id, info in ml_by_event.items():
        away_abbr = resolve_team(info["away_team"])
        home_abbr = resolve_team(info["home_team"])
        if not away_abbr or not home_abbr:
            continue
        ml_by_abbrev[(away_abbr, home_abbr)] = {**info, "event_id": ev_id}

    feat_df = _build_today_feature_rows(cfg, run_date, ml_by_abbrev)
    if feat_df.empty:
        return pd.DataFrame()

    p_home = _score(booster, features, feature_means, feat_df)
    feat_df = feat_df.copy()
    feat_df["p_home"] = p_home

    results = []
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _exposure_engine = _make_engine("unused")
    _exposure_game_pks = list(feat_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched_stakes = prefetch_exposure(_exposure_engine, _exposure_game_pks, game_date)
    _pending_stakes: dict[int, float] = {}
    for _, row in feat_df.iterrows():
        home_odds = row.get("_home_odds")
        away_odds = row.get("_away_odds")
        if home_odds is None or away_odds is None:
            continue
        p_h = float(row["p_home"])
        p_a = 1.0 - p_h

        ih = american_to_implied_prob(home_odds)
        ia = american_to_implied_prob(away_odds)
        if pd.isna(ih) or pd.isna(ia):
            continue
        fair_home, fair_away = remove_vig(ih, ia)
        edge_home = p_h - fair_home
        edge_away = p_a - fair_away

        if edge_home >= edge_away:
            side, edge, fair, odds = "HOME", edge_home, fair_home, home_odds
            model_prob = p_h
            team = row["home_team"]
        else:
            side, edge, fair, odds = "AWAY", edge_away, fair_away, away_odds
            model_prob = p_a
            team = row["away_team"]

        k_pct = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(_bankroll, int(row["game_pk"]), _prefetched_stakes, _pending_stakes)
        stake = min(kelly_stake(
            edge, odds,
            bankroll=_bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        ), _cap)
        kelly_triggered = edge >= cfg["min_edge"] and stake > 0
        if kelly_triggered and stake > 0:
            _pending_stakes[int(row["game_pk"])] = (
                _pending_stakes.get(int(row["game_pk"]), 0.0) + stake
            )

        results.append({
            "player":          f"{row['away_team']} @ {row['home_team']} ({team} F5)",
            "game_pk":         int(row["game_pk"]),
            "away_team":   row["away_team"],
            "home_team":   row["home_team"],
            "side":        side,
            "model_prob":  round(model_prob, 4),
            "market_prob": round(fair, 4),
            "edge":        round(edge, 4),
            "kelly_pct":   round(k_pct, 4),
            "odds":        int(odds),
            "stake":       stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
        })

    if not results:
        return pd.DataFrame()
    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"F5: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"F5 run | type={run_type} | date={run_date}")

    from F5_Pro_System.config_f5 import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("F5: no qualifying bets today")
        post_bets([], system="F5", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="F5")
    bets_logged = 0
    bet_rows    = []

    for _, row in today_df.iterrows():
        triggered = bool(row.get("kelly_triggered", True))
        bet_id = tracker.log_bet(
            game_date        = run_date,
            game_pk          = int(row["game_pk"]),
            player           = row["player"],
            away_team        = row["away_team"],
            home_team        = row["home_team"],
            bet_type         = row["side"],
            model_prob       = row["model_prob"],
            market_prob      = row["market_prob"],
            edge             = row["edge"],
            kelly_pct        = row["kelly_pct"],
            odds             = row["odds"],
            stake            = row["stake"],
            kelly_triggered  = triggered,
            paper            = cfg["PAPER"],
        )
        if bet_id == -1:
            continue
        if triggered:
            bets_logged += 1
            bet_rows.append(row.to_dict())

    post_bets(bet_rows, system="F5", run_date=run_date)

    logger.info(f"F5: {bets_logged} bets logged")
    return {"bets_logged": bets_logged, "bet_rows": bet_rows}
