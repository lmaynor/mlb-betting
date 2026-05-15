"""
runners/run_nrfi.py — NRFI Pro daily runner for Cloud Run.

Odds source: SGO snapshot at Odds/sgo/latest.json
Feature source: GCS NRFI_Pro_System/data/model_features.csv (built by
  runners.build_nrfi_features)
Model: xgb_halfinn_v17.json loaded from GCS. Per-half YRFI probabilities
  are combined to game-level NRFI: P(NRFI) = (1 - p_home) * (1 - p_away)
  where each probability is the half-inning yrfi prob for the pitcher
  facing that half. Optional isotonic calibrator applied to game-level
  NRFI prob if present in GCS.

run() is called by main.py.
"""
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


def _load_halfinn_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict]:
    """Load the half-inning XGBoost model + its expected feature list + training means.

    Returns (booster, features, feature_means). features comes from
    model_meta.json's `features` key. feature_means is the per-feature
    training-set mean used at predict time to fill NaN values (matches
    F5/K notebook behavior). Empty dict if meta doesn't have it — that
    falls back to NaN which XGBoost handles natively.
    Raises if features key is missing — without it we can't build a DMatrix.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(
                cfg["gcs_model_halfinn"],
                Path(tmpdir) / "xgb_halfinn.json",
            )
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_halfinn"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("model_meta_v17.json missing 'features' key")
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(f"NRFI halfinn loaded | features={len(features)} | "
                f"feature_means={len(feature_means)} | "
                f"AUC={meta.get('auc_oos', '?')}")
    return booster, features, feature_means


def _load_calibrator(cfg: dict):
    """Load the isotonic calibrator if present in GCS. Returns None if absent."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists

    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            return pickle.loads(read_bytes(gcs_key))
        except Exception as e:
            logger.warning(f"NRFI calibrator load failed: {e}")
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"NRFI calibrator load failed: {e}")
    return None


def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    """Build a per-starter feature row for today's slate.

    Returns DataFrame with one row per probable starter (home + away per
    game). Columns include all features from model_meta plus join helpers:
        game_pk, away_team, home_team, pitcher_is_home, _side, _pitcher_id

    Strategy:
      1. Pull each pitcher's most-recent feature snapshot from
         model_features.csv (groupby(pitcher).last())
      2. Fetch today's schedule + probable pitchers (MLB Stats API)
      3. For each (game, side), match probable pitcher by id, copy the
         latest row, override pitcher_is_home for the half being predicted
      4. No live weather override yet — weather columns inherit from the
         most-recent historical snapshot (XGBoost handles missing values).
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv
    from mlb_core.data.lineups import get_today_schedule

    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning(f"NRFI: no games scheduled for {run_date}")
        return pd.DataFrame()
    logger.info(f"NRFI: {len(sched)} games on the {run_date} slate")

    feat_key = cfg.get("gcs_model_features") if GCS_BUCKET else cfg["model_features"]
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"NRFI features load failed: {e}")
        return pd.DataFrame()
    if feat_df.empty:
        logger.error("NRFI features CSV is empty")
        return pd.DataFrame()

    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])
    pitcher_latest = (
        feat_df.sort_values("game_date")
               .groupby("pitcher", as_index=False)
               .last()
    )
    logger.info(f"NRFI: latest snapshot for {len(pitcher_latest):,} pitchers "
                f"(through {feat_df['game_date'].max().date()})")

    rows = []
    for _, g in sched.iterrows():
        for side in ("home", "away"):
            pid = g.get(f"{side}_pitcher_id")
            if pd.isna(pid):
                continue
            match = pitcher_latest[pitcher_latest["pitcher"] == int(pid)]
            if match.empty:
                logger.info(f"NRFI: no feature snapshot for "
                            f"{g.get(f'{side}_pitcher_name')} (id={pid})")
                continue
            row = match.iloc[0].to_dict()
            row["game_pk"]         = g["game_pk"]
            row["game_date"]       = pd.Timestamp(run_date)
            row["home_team"]       = g["home_team"]
            row["away_team"]       = g["away_team"]
            row["pitcher_is_home"] = 1 if side == "home" else 0
            row["_side"]           = side
            row["_pitcher_id"]     = int(pid)
            row["_pitcher_name"]   = g.get(f"{side}_pitcher_name")
            rows.append(row)

    if not rows:
        logger.warning("NRFI: 0 probable starters matched feature snapshots")
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    logger.info(f"NRFI: {len(out)} starter-row predictions ready "
                f"({(out['_side']=='home').sum()} home, "
                f"{(out['_side']=='away').sum()} away)")
    return out


def _score(booster: xgb.Booster, features: list[str], feature_means: dict,
           df: pd.DataFrame) -> np.ndarray:
    """Score df with the booster.

    For each feature: if the column exists and the value is finite, use it.
    Otherwise fall back to feature_means[feature] if known, else NaN.
    XGBoost handles NaN natively, but feature_means gives the model the
    same distribution it saw at training time for missing inputs.
    """
    avail = [f for f in features if f in df.columns]
    missing = set(features) - set(avail)
    if missing:
        logger.warning(f"NRFI: {len(missing)} features missing from input: "
                       f"{sorted(missing)[:5]}")
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    # Fill NaN per-column with feature_means where available
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
    """Build today's NRFI predictions.

    Returns DataFrame with one row per qualifying bet, columns:
        player (label), game_pk, away_team, home_team, side ('NRFI'|'YRFI'),
        model_prob, market_prob, edge, kelly_pct, odds, stake
    """
    from mlb_core.odds import sgo
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.dk_scraper import resolve_team

    booster, features, feature_means = _load_halfinn_model(cfg)
    calibrator = _load_calibrator(cfg)

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        logger.warning("NRFI: no candidate starter rows — skipping predictions")
        return pd.DataFrame()

    p_half = _score(booster, features, feature_means, feat_df)
    feat_df = feat_df.copy()
    feat_df["p_half_yrfi"] = p_half

    # Combine to game-level NRFI: P(NRFI) = (1-p_home)*(1-p_away)
    # Only games with both starters scored get a prediction.
    pivot = feat_df.pivot_table(
        index=["game_pk", "away_team", "home_team"],
        columns="_side",
        values="p_half_yrfi",
        aggfunc="first",
    ).reset_index()
    pivot = pivot.dropna(subset=["home", "away"])
    if pivot.empty:
        logger.warning("NRFI: no games with both starters scored")
        return pd.DataFrame()
    pivot["model_yrfi_prob"] = 1.0 - (1.0 - pivot["home"]) * (1.0 - pivot["away"])
    pivot["model_nrfi_prob"] = 1.0 - pivot["model_yrfi_prob"]

    if calibrator is not None:
        try:
            pivot["model_nrfi_prob"] = calibrator.predict(
                pivot["model_nrfi_prob"].values
            )
            pivot["model_yrfi_prob"] = 1.0 - pivot["model_nrfi_prob"]
            logger.info("NRFI: isotonic calibrator applied")
        except Exception as e:
            logger.warning(f"NRFI calibrator predict failed: {e} -- using raw probs")

        # Clamp to avoid 0/1 saturation inflating edge calculations.
        pivot["model_nrfi_prob"] = pivot["model_nrfi_prob"].clip(0.001, 0.999)
        pivot["model_yrfi_prob"] = pivot["model_yrfi_prob"].clip(0.001, 0.999)
    # 3-way joint probabilities derived from per-half probs
    pivot["p_3way_away"] = pivot["away"] * (1.0 - pivot["home"])
    pivot["p_3way_home"] = pivot["home"] * (1.0 - pivot["away"])
    pivot["p_3way_draw"] = pivot["model_nrfi_prob"]

    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("NRFI: SGO snapshot empty or missing — skipping")
        return pd.DataFrame()
    nrfi_odds_by_event = sgo.extract_nrfi_odds(events)
    if not nrfi_odds_by_event:
        logger.warning("NRFI: 0 events have NRFI/YRFI prices on DK — skipping")
        return pd.DataFrame()

    # Index odds by (away_abbrev, home_abbrev) for matching to today's schedule
    by_abbrev: dict = {}
    for ev_id, info in nrfi_odds_by_event.items():
        away_abbr = resolve_team(info["away_team"])
        home_abbr = resolve_team(info["home_team"])
        if not away_abbr or not home_abbr:
            continue
        by_abbrev[(away_abbr, home_abbr)] = {**info, "event_id": ev_id}

    results = []
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _exposure_engine = _make_engine("unused")
    _exposure_game_pks = list(pivot["game_pk"].dropna().astype(int).unique()) if "pivot" in dir() else []
    _bankroll, _prefetched_stakes = prefetch_exposure(_exposure_engine, _exposure_game_pks, run_date, system="NRFI")
    _pending_stakes: dict[int, float] = {}
    for _, row in pivot.iterrows():
        key = (row["away_team"], row["home_team"])
        odds_info = by_abbrev.get(key)
        if odds_info is None:
            continue
        nrfi_odds = odds_info["nrfi_odds"]
        yrfi_odds = odds_info["yrfi_odds"]
        nrfi_mkt  = american_to_implied_prob(nrfi_odds)
        yrfi_mkt  = american_to_implied_prob(yrfi_odds)
        total = nrfi_mkt + yrfi_mkt
        if not total or pd.isna(total) or total <= 0:
            continue
        # No-vig: split the over-round proportionally
        nrfi_fair = nrfi_mkt / total
        yrfi_fair = yrfi_mkt / total

        edge_nrfi = float(row["model_nrfi_prob"]) - nrfi_fair
        edge_yrfi = float(row["model_yrfi_prob"]) - yrfi_fair

        if edge_nrfi >= edge_yrfi:
            side, edge, fair, odds = "NRFI", edge_nrfi, nrfi_fair, nrfi_odds
            model_prob = float(row["model_nrfi_prob"])
        else:
            side, edge, fair, odds = "YRFI", edge_yrfi, yrfi_fair, yrfi_odds
            model_prob = float(row["model_yrfi_prob"])

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
            "player":          f"{row['away_team']} @ {row['home_team']}",
            "game_pk":         int(row["game_pk"]),
            "away_team":    row["away_team"],
            "home_team":    row["home_team"],
            "side":         side,
            "model_prob":   round(model_prob, 4),
            "market_prob":  round(fair, 4),
            "edge":         round(edge, 4),
            "kelly_pct":    round(k_pct, 4),
            "odds":         odds,
            "stake":        stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
        })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"NRFI: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"NRFI run | type={run_type} | date={run_date}")

    from NRFI_Pro_System.config_nrfi import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("NRFI: no qualifying bets today")
        post_bets([], system="NRFI", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="NRFI")
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

    post_bets(bet_rows, system="NRFI", run_date=run_date)

    logger.info(f"NRFI: {bets_logged} bets logged")
    return {"bets_logged": bets_logged, "bet_rows": bet_rows}
