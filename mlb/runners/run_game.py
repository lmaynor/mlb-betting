"""
runners/run_game.py -- GAME Pro v1 daily runner.

Odds source: SGO snapshot (extract_game_ml_odds) at Odds/sgo/latest.json
Feature source: GAME_Pro_System/data/model_features.csv (built by
  runners.build_game_features).
Model: xgb_game_v1.json -- binary:logistic -> P(home win full game).
  P(away win) = 1 - P(home win).
Calibration: isotonic_calibrator_game_v1.pkl maps raw prob -> calibrated prob.

Two-sided bet: HOME or AWAY moneyline.
Edge = model_prob - fair_prob (after vig removal from both sides).
Kelly sizing with cap from mlb_core.risk.exposure.

Active sizing is enabled; bets trigger when edge and Kelly sizing clear
configured gates.

run() is called by main.py for {"system": "GAME"}.
"""
from __future__ import annotations

import json
import logging
import pickle
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)

# Active sizing enabled.
LOG_ONLY = False


# -- Model + calibrator load --------------------------------------------------

def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict]:
    """Load the GAME XGBoost model + features + feature_means.

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
                cfg["gcs_model_xgb"],
                Path(tmpdir) / "xgb_game.json",
            )
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_xgb"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("GAME model_meta missing 'features' key")
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(
        "GAME model loaded | features=%d | feature_means=%d | AUC=%s",
        len(features), len(feature_means),
        meta.get("auc_oos", meta.get("wf_auc", "?")),
    )
    return booster, features, feature_means


def _load_calibrator(cfg: dict):
    """Load the isotonic calibrator if present in GCS. Returns None if absent."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists

    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            cal = pickle.loads(read_bytes(gcs_key))
            logger.info("GAME calibrator loaded from GCS")
            return cal
        except Exception as e:
            logger.warning("GAME calibrator GCS load failed: %s", e)
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                cal = pickle.load(f)
            logger.info("GAME calibrator loaded from local path")
            return cal
        except Exception as e:
            logger.warning("GAME calibrator local load failed: %s", e)
    return None


# -- Today's feature rows -----------------------------------------------------

def _fetch_today_weather(sched: pd.DataFrame) -> dict:
    """Live weather per game keyed by game_pk."""
    try:
        from mlb_core.data.weather import fetch_live_weather_for_slate
        out = fetch_live_weather_for_slate(sched)
        logger.info("GAME weather: %d/%d games", len(out), len(sched))
        return out
    except Exception as e:
        logger.warning("GAME: weather fetch failed: %s", e)
        return {}


def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Build feature rows for today's games.

    Strategy:
      1. Load model_features.csv from GCS, filter to run_date games.
      2. If no rows found for today, attempt an on-the-fly feature build
         using build_game_features.build_model_features with today's starter
         info + pre-built rolling features (bullpen/offense from yesterday).
      3. Fall back to yesterday's bullpen/offense features if today's build
         fails.

    Returns one row per (game_pk) with home_team, away_team, and all
    GAME_FEATURES columns.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv, exists

    feat_key = cfg.get("gcs_model_features", cfg.get("model_features"))

    # -- Attempt 1: load pre-built model_features for today
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error("GAME: feature load failed: %s", e)
        return pd.DataFrame()

    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"], errors="coerce")
    today_df = feat_df[feat_df["game_date"] == pd.Timestamp(run_date)].copy()

    if not today_df.empty:
        logger.info("GAME: %d pre-built feature rows for %s", len(today_df), run_date)
        return today_df

    logger.info("GAME: no pre-built rows for %s -- attempting on-the-fly build", run_date)

    # -- Attempt 2: on-the-fly build with today's schedule
    try:
        from mlb_core.data.lineups import get_today_schedule
        from mlb_core.storage import read_csv as _rc
        from mlb.runners.build_game_features import (
            build_starter_features, build_model_features,
            PARK_FACTORS, DOME_TEAMS, TEAM_NAME_TO_ABBR,
        )

        sched = get_today_schedule(run_date)
        if sched.empty:
            logger.warning("GAME: no games scheduled for %s", run_date)
            return pd.DataFrame()

        # Load Statcast for the last 7 days (enough for today's starters)
        try:
            from mlb_core.storage import read_csv as _rsc
            sc = _rsc("Statcast/statcast_master.csv", low_memory=False)
            sc["game_date"] = pd.to_datetime(sc["game_date"], errors="coerce")
            cutoff = pd.Timestamp(run_date) - pd.Timedelta(days=10)
            sc = sc[sc["game_date"] >= cutoff].copy()
        except Exception as e:
            logger.warning("GAME: statcast load for on-the-fly build failed: %s", e)
            sc = pd.DataFrame()

        # Load yesterday's bullpen + offense features (rolling already computed)
        bullpen_key = cfg.get("gcs_bullpen_features", "GAME_Pro_System/data/bullpen_game_features.csv")
        offense_key = cfg.get("gcs_team_offense",     "GAME_Pro_System/data/team_offense_features.csv")

        bullpen = pd.DataFrame()
        offense = pd.DataFrame()
        if exists(bullpen_key):
            try:
                bullpen = _rc(bullpen_key, low_memory=False)
                bullpen["game_date"] = pd.to_datetime(bullpen["game_date"], errors="coerce")
                # Use most-recent row per team as today's rolling estimate
                bullpen = bullpen.sort_values("game_date").groupby("team").last().reset_index()
                bullpen["game_date"] = pd.Timestamp(run_date)
            except Exception as e:
                logger.warning("GAME: bullpen load failed: %s", e)

        if exists(offense_key):
            try:
                offense = _rc(offense_key, low_memory=False)
                offense["game_date"] = pd.to_datetime(offense["game_date"], errors="coerce")
                offense = offense.sort_values("game_date").groupby("team").last().reset_index()
                offense["game_date"] = pd.Timestamp(run_date)
            except Exception as e:
                logger.warning("GAME: offense load failed: %s", e)

        # Today's starter features from Statcast
        starter_home = pd.DataFrame()
        starter_away = pd.DataFrame()
        if not sc.empty:
            try:
                starter_home, starter_away = build_starter_features(
                    sc, None, None, lookback_days=10, run_date=run_date
                )
            except Exception as e:
                logger.warning("GAME: on-the-fly starter features failed: %s", e)

        # Weather
        wx = _fetch_today_weather(sched)

        # Assign game_pk from schedule to bullpen/offense rows
        if not sched.empty and not bullpen.empty:
            for _, game in sched.iterrows():
                gp = game.get("game_pk")
                home_t = TEAM_NAME_TO_ABBR.get(game.get("home_team", ""), "")
                away_t = TEAM_NAME_TO_ABBR.get(game.get("away_team", ""), "")
                mask_h = bullpen["team"].apply(
                    lambda t: TEAM_NAME_TO_ABBR.get(str(t), t)) == home_t
                mask_a = bullpen["team"].apply(
                    lambda t: TEAM_NAME_TO_ABBR.get(str(t), t)) == away_t
                bullpen.loc[mask_h | mask_a, "game_pk"] = gp

        if starter_home.empty:
            logger.warning("GAME: starter features empty for on-the-fly build")
            return pd.DataFrame()

        # Attach home_team / away_team from schedule to starter rows
        if not sched.empty and "game_pk" in starter_home.columns:
            gp_to_teams = {}
            for _, g in sched.iterrows():
                gp_to_teams[g["game_pk"]] = {
                    "home_team": g.get("home_team", ""),
                    "away_team": g.get("away_team", ""),
                }
            starter_home["home_team"] = starter_home["game_pk"].map(
                lambda gp: gp_to_teams.get(gp, {}).get("home_team", ""))
            starter_home["away_team"] = starter_home["game_pk"].map(
                lambda gp: gp_to_teams.get(gp, {}).get("away_team", ""))
            if not starter_away.empty:
                starter_away["home_team"] = starter_away["game_pk"].map(
                    lambda gp: gp_to_teams.get(gp, {}).get("home_team", ""))
                starter_away["away_team"] = starter_away["game_pk"].map(
                    lambda gp: gp_to_teams.get(gp, {}).get("away_team", ""))

        today_df = build_model_features(
            starter_home, starter_away, bullpen, offense, wx, run_date=run_date
        )
        if not today_df.empty:
            logger.info("GAME: on-the-fly build produced %d rows for %s",
                        len(today_df), run_date)
        return today_df

    except Exception as e:
        logger.error("GAME: on-the-fly feature build failed: %s", e)
        return pd.DataFrame()


# -- Scoring ------------------------------------------------------------------

def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.utils import remove_vig
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_game_ml_odds

    _SGO_KEY = "Odds/sgo/latest.json"

    # Snapshot freshness check
    _fresh, _reason = _sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error("GAME: aborting -- %s", _reason)
        return pd.DataFrame()

    # Feature build sentinel check
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "GAME_Pro_System")
    if not _sok:
        msg = f"GAME: aborting -- stale/failed feature build: {_sreason}"
        logger.error(msg)
        try:
            from mlb_core.notify.discord import post_error
            post_error("GAME", msg)
        except Exception:
            pass
        return pd.DataFrame()

    booster, features, feature_means = _load_model(cfg)
    calibrator = _load_calibrator(cfg)

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        logger.warning("GAME: no candidate rows -- skipping")
        return pd.DataFrame()

    # Load SGO odds
    events   = _sgo.load_snapshot(_SGO_KEY)
    game_odds = extract_game_ml_odds(events) if events else {}
    if not game_odds:
        logger.warning("GAME: no odds in SGO snapshot")
        return pd.DataFrame()
    logger.info("GAME: %d events with full-game ML odds", len(game_odds))

    # Score all rows
    X = feat_df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            mean = feature_means.get(col)
            if mean is not None:
                X[col] = X[col].fillna(float(mean))
    X  = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    raw_preds = booster.predict(dm, iteration_range=(0, ntree)) if ntree \
                else booster.predict(dm)

    feat_df = feat_df.copy()
    feat_df["raw_prob_home"] = np.clip(raw_preds, 1e-4, 1 - 1e-4)

    # Apply isotonic calibrator only inside its fitted input range. The
    # calibrator is trained with out_of_bounds="clip"; calling it on live
    # out-of-range values would snap probabilities to a boundary and can create
    # artificial 90%+ confidence.
    if calibrator is not None:
        try:
            raw = feat_df["raw_prob_home"].values.copy()
            x_min = getattr(calibrator, "X_min_", None)
            x_max = getattr(calibrator, "X_max_", None)
            in_range = np.ones(len(raw), dtype=bool)
            if x_min is not None and x_max is not None:
                in_range = (raw >= x_min) & (raw <= x_max)
            cal = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            feat_df["prob_home"] = np.clip(cal, 1e-4, 1 - 1e-4)
            feat_df["calibrator_in_range"] = in_range
            logger.info(
                "GAME: calibrator applied to %d/%d games",
                int(in_range.sum()), len(raw),
            )
        except Exception as e:
            logger.warning("GAME calibrator predict failed: %s", e)
            feat_df["prob_home"] = feat_df["raw_prob_home"]
            feat_df["calibrator_in_range"] = False
    else:
        feat_df["prob_home"] = feat_df["raw_prob_home"]
        feat_df["calibrator_in_range"] = False

    # Build a lookup: (home_team_abbr, away_team_abbr) -> row index
    from mlb.runners.build_game_features import TEAM_NAME_TO_ABBR as _ABBR

    def _abbr(name: str) -> str:
        return _ABBR.get(str(name), str(name)).upper()

    feat_df["_home_abbr"] = feat_df.get("home_team", pd.Series("", index=feat_df.index)).apply(_abbr)
    feat_df["_away_abbr"] = feat_df.get("away_team", pd.Series("", index=feat_df.index)).apply(_abbr)
    team_pair_to_idx: dict = {}
    for i, row in feat_df.iterrows():
        key = (_abbr(row.get("home_team", "")), _abbr(row.get("away_team", "")))
        team_pair_to_idx[key] = i

    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _engine   = _make_engine("unused")
    _game_pks = list(feat_df["game_pk"].dropna().astype(int).unique()) \
                if "game_pk" in feat_df.columns else []
    _bankroll, _prefetched = prefetch_exposure(_engine, _game_pks, run_date, system="GAME")
    _pending: dict[int, float] = {}
    from mlb_core.risk.gates import is_suppressed as _is_suppressed
    from mlb_core.risk.calibration import apply as _cal_apply, EDGE_CAP as _EDGE_CAP
    _gate_suppressed = _is_suppressed("GAME")
    if _gate_suppressed:
        logger.warning("GAME gate active -- logging only, no staked bets this run")

    results = []

    for event_id, odds_info in game_odds.items():
        home_t = _abbr(odds_info.get("home_team", ""))
        away_t = _abbr(odds_info.get("away_team", ""))

        row_idx = team_pair_to_idx.get((home_t, away_t))
        if row_idx is None:
            logger.debug("GAME: no feature row for %s vs %s", away_t, home_t)
            continue

        row = feat_df.loc[row_idx]
        prob_home = float(row["prob_home"])
        prob_away = 1.0 - prob_home
        game_pk   = int(row["game_pk"]) if "game_pk" in row and pd.notna(row.get("game_pk")) else 0

        home_odds_raw = odds_info.get("home_odds")
        away_odds_raw = odds_info.get("away_odds")
        if home_odds_raw is None or away_odds_raw is None:
            continue

        # Vig removal on both sides
        mkt_home = american_to_implied_prob(home_odds_raw)
        mkt_away = american_to_implied_prob(away_odds_raw)
        total    = mkt_home + mkt_away
        if not total or total <= 0:
            continue
        # Shin devig (favorite-longshot correction; matches ingest)
        from mlb_core.odds.utils import devig_two_way
        fair_home, fair_away = devig_two_way(mkt_home, mkt_away, method="shin")
        if pd.isna(fair_home) or pd.isna(fair_away):
            continue

        edge_home = prob_home - fair_home
        edge_away = prob_away - fair_away

        if edge_home >= edge_away:
            side      = "HOME"
            edge      = edge_home
            fair      = fair_home
            odds      = home_odds_raw
            model_prob = prob_home
        else:
            side      = "AWAY"
            edge      = edge_away
            fair      = fair_away
            odds      = away_odds_raw
            model_prob = prob_away

        model_prob, _cal = _cal_apply("GAME", model_prob)
        edge = model_prob - fair
        _edge_capped = _cal and edge > _EDGE_CAP

        logger.info(
            "GAME pred | %s @ %s | raw_home=%.3f cal_home=%.3f in_range=%s "
            "%s | model=%.3f fair=%.3f edge=%+.3f",
            away_t, home_t,
            float(row.get("raw_prob_home", prob_home)),
            prob_home,
            bool(row.get("calibrator_in_range", False)),
            side,
            model_prob, fair, edge,
        )

        k_pct_val = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(
            _bankroll, game_pk,
            _prefetched, _pending,
            cap_units=cfg.get("cap_units", 10.0),
        )
        raw_stake = kelly_stake(
            edge, odds,
            bankroll=_bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        )
        stake = min(raw_stake, _cap)

        kelly_triggered = (edge >= cfg["min_edge"]) and (stake > 0) and (not LOG_ONLY) and (not _gate_suppressed) and (not _edge_capped)
        if kelly_triggered and stake > 0:
            _pending[game_pk] = _pending.get(game_pk, 0.0) + stake

        results.append({
            "event_id":          event_id,
            "game_pk":           game_pk,
            "away_team":         odds_info["away_team"],
            "home_team":         odds_info["home_team"],
            "commence_time":     odds_info.get("commence_time", ""),
            "side":              side,
            "bet_type":          f"GAME_ML_{side}",
            "prob_home":         round(prob_home, 4),
            "prob_away":         round(prob_away, 4),
            "model_prob":        round(model_prob, 4),
            "market_prob":       round(fair, 4),
            "edge":              round(edge, 4),
            "kelly_pct":         round(k_pct_val, 4),
            "odds":              odds,
            "home_odds":         home_odds_raw,
            "away_odds":         away_odds_raw,
            "stake":             stake if kelly_triggered else 0.0,
            "kelly_triggered":   kelly_triggered,
            "bookmaker":         odds_info.get("bookmaker"),
            "raw_prob_home":     round(float(row.get("raw_prob_home", prob_home)), 4),
            "cal_prob_home":     round(prob_home, 4),
            "calibrator_in_range": bool(row.get("calibrator_in_range", False)),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("edge", ascending=False)


# -- Entry point --------------------------------------------------------------

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info("GAME run | type=%s | date=%s | log_only=%s", run_type, run_date, LOG_ONLY)

    from mlb.systems.GAME_Pro_System.config_game import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("GAME: no qualifying bets today")
        post_bets([], system="GAME", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="GAME")
    bets_logged = 0
    bet_rows    = []

    for _, row in today_df.iterrows():
        triggered = bool(row.get("kelly_triggered", False))
        bet_id = tracker.log_bet(
            game_date       = run_date,
            game_pk         = row.get("game_pk"),
            player          = None,
            away_team       = row.get("away_team"),
            home_team       = row.get("home_team"),
            bet_type        = row.get("bet_type"),
            model_prob      = row.get("model_prob"),
            market_prob     = row.get("market_prob"),
            edge            = row.get("edge"),
            kelly_pct       = row.get("kelly_pct"),
            odds            = row.get("odds"),
            stake           = row.get("stake"),
            kelly_triggered = triggered,
            paper           = cfg["PAPER"],
            book            = row.get("bookmaker"),
        )
        if bet_id == -1:
            continue
        bets_logged += 1
        if triggered:
            bet_rows.append(row.to_dict())

    log_suffix = " (log-only -- 200-bet gate not yet cleared)" if LOG_ONLY else ""
    logger.info("GAME: %d bets logged%s", bets_logged, log_suffix)
    post_bets(bet_rows, system="GAME", run_date=run_date)

    return {"bets_logged": bets_logged, "log_only": LOG_ONLY, "bet_rows": bet_rows}
