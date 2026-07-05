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
import pickle
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


def _load_calibrator(cfg: dict):
    """Load the isotonic calibrator if present in GCS. Returns None if absent."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists
    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            return pickle.loads(read_bytes(gcs_key))
        except Exception as e:
            logger.warning(f"F5 calibrator load failed: {e}")
            return None
    return None


def _fetch_today_weather(sched: pd.DataFrame) -> dict:
    """Live weather per game keyed by game_pk.

    Delegates to mlb_core.data.weather.fetch_live_weather_for_slate which
    uses _fetch_weather's 4-attempt exponential backoff.
    """
    from mlb_core.data.weather import fetch_live_weather_for_slate
    out = fetch_live_weather_for_slate(sched)
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
    from mlb_core.data.lineups import fetch_il_pitcher_ids
    _il_ids = fetch_il_pitcher_ids()
    from mlb_core.odds import american_to_implied_prob, remove_vig
    from mlb_core.odds.utils import devig_two_way

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
        row["game_pk"]        = g["game_pk"]
        row["game_date"]      = pd.Timestamp(run_date)
        row["home_game_date"] = run_date
        row["away_game_date"] = run_date
        row["home_team"]      = home
        row["away_team"]      = away

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
            fh, _ = devig_two_way(ih, ia, method="shin")
            row["implied_home_win_pct"] = fh
            row["_home_odds"] = odds_info["home_odds"]
            row["_away_odds"] = odds_info["away_odds"]
            row["_event_id"]  = odds_info.get("event_id")
            row["bookmaker"]  = odds_info.get("bookmaker")
        else:
            row["_home_odds"] = None
            row["_away_odds"] = None
            row["_event_id"]  = None
            row["bookmaker"]  = None
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
    from mlb_core.odds.utils import devig_two_way
    from mlb_core.odds.dk_scraper import resolve_team

    booster, features, feature_means = _load_model(cfg)
    calibrator = _load_calibrator(cfg)

    # Abort if snapshot is stale — stale lines produce fictitious edge.
    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"aborting run — {_reason}")
        return pd.DataFrame()
    # Sentinel check -- abort if feature build is stale or failed
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "F5_Pro_System")
    if not _sok:
        msg = f"F5: aborting run -- stale/failed feature build: {_sreason}"
        logger.error(msg)
        from mlb_core.notify.discord import post_error
        post_error("F5", msg)
        return pd.DataFrame()
    logger.info("F5: sentinel ok -- %s", _sreason)
    from mlb_core.data.lineups import fetch_il_pitcher_ids
    _il_ids = fetch_il_pitcher_ids()
    # E10: load morning snapshot for line movement signal
    from mlb_core.odds.line_movement import load_morning_odds
    _morning_odds = load_morning_odds(run_date)

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
    if calibrator is not None:
        try:
            raw      = p_home.copy()
            x_min    = getattr(calibrator, "X_min_", 0.0)
            x_max    = getattr(calibrator, "X_max_", 1.0)
            in_range = (raw >= x_min) & (raw <= x_max)
            cal      = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            p_home = np.clip(cal, 0.05, 0.95)
            logger.info(
                f"F5: isotonic calibrator applied to "
                f"{int(in_range.sum())}/{len(raw)} games"
            )
        except Exception as e:
            logger.warning(f"F5 calibrator predict failed: {e} -- using raw probs")
    feat_df = feat_df.copy()
    feat_df["p_home"] = p_home.clip(0.05, 0.95)

    results = []
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _exposure_engine = _make_engine("unused")
    _exposure_game_pks = list(feat_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched_stakes = prefetch_exposure(_exposure_engine, _exposure_game_pks, run_date, system="F5")
    _pending_stakes: dict[int, float] = {}
    from mlb_core.risk.gates import is_suppressed as _is_suppressed
    from mlb_core.risk.calibration import apply as _cal_apply, EDGE_CAP as _EDGE_CAP
    _gate_suppressed = _is_suppressed("F5")
    if _gate_suppressed:
        logger.warning("F5 gate active -- logging only, no staked bets this run")
    _IL_DAYS = 15

    def _starter_stale(row, side: str, run_date: str) -> bool:
        """Return True if starter's last appearance exceeds IL threshold."""
        from datetime import date as _d
        raw = row.get(f"{side}_game_date")
        if raw is None or pd.isna(raw):
            return False
        try:
            return (_d.fromisoformat(run_date) - pd.Timestamp(raw).date()).days > _IL_DAYS
        except Exception:
            return False

    for _, row in feat_df.iterrows():
        if _starter_stale(row, "home", run_date) or _starter_stale(row, "away", run_date):
            _home_pid = int(row.get("home_pitcher_id") or 0)
            _away_pid = int(row.get("away_pitcher_id") or 0)
            _on_il = (_home_pid and _home_pid in _il_ids) or (_away_pid and _away_pid in _il_ids)
            if _on_il:
                logger.info(
                    f"F5: skipping game_pk={int(row['game_pk'])} -- "
                    f"starter gap >{_IL_DAYS}d + confirmed on IL"
                )
                continue
            logger.info(
                f"F5: game_pk={int(row['game_pk'])} has starter gap >{_IL_DAYS}d "
                f"but NOT on IL -- including"
            )
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
        fair_home, fair_away = devig_two_way(ih, ia, method="shin")
        if pd.isna(fair_home) or pd.isna(fair_away):
            continue
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

        model_prob, _cal = _cal_apply("F5", model_prob)
        edge = model_prob - fair
        _edge_capped = _cal and edge > _EDGE_CAP

        k_pct = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(_bankroll, int(row["game_pk"]), _prefetched_stakes, _pending_stakes, cap_units=cfg.get("cap_units", 2.0))
        stake = min(kelly_stake(
            edge, odds,
            bankroll=_bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        ), _cap)
        kelly_triggered = edge >= cfg["min_edge"] and stake > 0 and not _gate_suppressed and not _edge_capped
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
            # rationale features
            "k_pct_L3":          row.get("k_pct_L3"),
            "xwoba_allowed_L3":  row.get("xwoba_allowed_L3"),
            "velo_mean_L3":      row.get("velo_mean_L3"),
            "bb_pct_L3":         row.get("bb_pct_L3"),
            "wind_out":          row.get("wind_out"),
            "wind_in":           row.get("wind_in"),
            "park_factor":       row.get("park_factor"),
            "is_cold":           row.get("is_cold"),
            "days_rest":         row.get("days_rest"),
            "short_rest":        row.get("short_rest"),
            "pitcher_is_home":   row.get("pitcher_is_home"),
        })

    if not results:
        return pd.DataFrame()
    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"F5: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


import json as _json
from mlb_core.rationale import build_rationale
import numpy as _np

# F1H ships as an active sub-market. GAME runs through runners/run_game.py
# to avoid duplicate logging from this F5-derived proxy path.
# F1H retired 2026-06-24: no live edge (bet-sample AUC ~0.50, net-negative trend);
# it is a scalar proxy off F5, which is also retired. Log-only (stake=0) until a
# real F1H model exists. Remove from this set to re-enable.
LOG_ONLY_SYSTEMS = {"F1H"}
_SCALAR_FALLBACKS = {"F1H": 0.94, "GAME": 0.82}
_INNINGS_SUBMARKET_CONFIG = [
    ("F1H",  "extract_f1h_ml_odds",  "F1H",  "F1H"),
]


def _load_innings_scalars() -> dict:
    from mlb_core.storage import read_bytes, exists
    gcs_key = "F5_Pro_System/data/innings_window_scalars.json"
    if not exists(gcs_key):
        logger.info("innings scalars not in GCS -- using fallbacks")
        return dict(_SCALAR_FALLBACKS)
    try:
        data = _json.loads(read_bytes(gcs_key))
    except Exception as e:
        logger.warning(f"innings scalars load failed: {e} -- using fallbacks")
        return dict(_SCALAR_FALLBACKS)
    scalars = {}
    for window, info in data.items():
        if window not in _SCALAR_FALLBACKS:
            continue
        if isinstance(info, dict) and info.get("scalar") is not None:
            scalars[window] = float(info["scalar"])
            logger.info(f"innings scalar {window}: {info['scalar']:.4f}")
        else:
            scalars[window] = _SCALAR_FALLBACKS[window]
    return scalars


def _score_innings_submarkets(predictions_df, scalars: dict,
                               cfg: dict, run_date: str) -> dict:
    from mlb_core.odds import sgo
    from mlb_core.odds.sgo import extract_f1h_ml_odds, extract_game_ml_odds
    from mlb_core.odds.utils import american_to_implied_prob, remove_vig
    from mlb_core.odds.dk_scraper import resolve_team
    from mlb_core.odds.utils import kelly_stake, kelly_pct as kpct
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine

    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("innings submarkets: SGO snapshot empty")
        return {s: [] for s, _, _, _ in _INNINGS_SUBMARKET_CONFIG}

    extractor_fns = {
        "extract_f1h_ml_odds":  extract_f1h_ml_odds,
        "extract_game_ml_odds": extract_game_ml_odds,
    }
    results = {s: [] for s, _, _, _ in _INNINGS_SUBMARKET_CONFIG}
    _engine = _make_engine("unused")
    _all_game_pks = list(predictions_df["game_pk"].dropna().astype(int).unique())

    game_probs: dict = {}
    game_pks:   dict = {}
    for _, row in predictions_df.iterrows():
        p_h = float(row["p_home"]) if "p_home" in row and not pd.isna(row.get("p_home"))               else (float(row["model_prob"]) if row["side"] == "HOME"
                    else 1.0 - float(row["model_prob"]))
        key = (row["away_team"], row["home_team"])
        game_probs[key] = p_h
        game_pks[key]   = int(row["game_pk"])

    for sys_key, extractor_name, bt_prefix, scalar_key in _INNINGS_SUBMARKET_CONFIG:
        extractor = extractor_fns[extractor_name]
        odds_map  = extractor(events)
        scalar    = scalars.get(scalar_key, _SCALAR_FALLBACKS.get(scalar_key, 1.0))
        from mlb_core.risk.gates import is_suppressed as _is_suppressed
        from mlb_core.risk.calibration import apply as _cal_apply, EDGE_CAP as _EDGE_CAP
        log_only  = (sys_key in LOG_ONLY_SYSTEMS) or _is_suppressed(sys_key)
        bankroll, prefetched = prefetch_exposure(_engine, _all_game_pks, run_date, system=sys_key)
        pending: dict[int, float] = {}

        for event_id, odds_info in odds_map.items():
            away_abbr = resolve_team(odds_info["away_team"])
            home_abbr = resolve_team(odds_info["home_team"])
            if not away_abbr or not home_abbr:
                continue
            key    = (away_abbr, home_abbr)
            p_home = game_probs.get(key)
            if p_home is None:
                continue
            game_pk = game_pks[key]

            p_home_s = float(_np.clip(0.5 + (p_home - 0.5) * scalar, 0.02, 0.98))
            p_away_s = 1.0 - p_home_s

            ih = american_to_implied_prob(odds_info["home_odds"])
            ia = american_to_implied_prob(odds_info["away_odds"])
            if pd.isna(ih) or pd.isna(ia) or (ih + ia) <= 0:
                continue
            fair_home, fair_away = devig_two_way(ih, ia, method="shin")
            if pd.isna(fair_home) or pd.isna(fair_away):
                continue

            if p_home_s - fair_home >= p_away_s - fair_away:
                side, edge, fair, odds, model_prob = (
                    "HOME", p_home_s - fair_home, fair_home,
                    odds_info["home_odds"], p_home_s)
            else:
                side, edge, fair, odds, model_prob = (
                    "AWAY", p_away_s - fair_away, fair_away,
                    odds_info["away_odds"], p_away_s)

            # Calibrate PRE-edge against realized outcomes (per sub-market), then
            # re-derive edge so the min_edge filter + Kelly act on the calibrated gap.
            model_prob, _cal = _cal_apply(sys_key, model_prob)
            edge = model_prob - fair
            _edge_capped = _cal and edge > _EDGE_CAP

            if edge < cfg["min_edge"]:
                continue

            k_pct_val = round(kpct(edge, odds, cfg["kelly_fraction"]), 4)
            bankroll, cap = apply_cap(
                bankroll, int(game_pk),
                prefetched, pending,
                cap_units=cfg.get("cap_units", 2.0),
            )
            would_be = min(kelly_stake(
                edge, odds,
                bankroll=bankroll,
                fraction=cfg["kelly_fraction"],
                min_pct=cfg["min_kelly_pct"],
                max_pct=cfg["max_kelly_pct"],
            ), cap)
            stake = 0.0 if log_only else would_be
            kelly_triggered = (edge >= cfg["min_edge"]) and (stake > 0) and not _edge_capped
            if kelly_triggered:
                pending[game_pk] = pending.get(game_pk, 0.0) + stake

            results[sys_key].append({
                "player":          f"{away_abbr} @ {home_abbr} ({(home_abbr if side=='HOME' else away_abbr)} {bt_prefix})",
                "game_pk":         game_pk,
                "away_team":       away_abbr,
                "home_team":       home_abbr,
                "side":            side,
                "bet_type":        f"{bt_prefix}_{side}",
                "model_prob":      round(model_prob, 4),
                "market_prob":     round(fair, 4),
                "edge":            round(edge, 4),
                "kelly_pct":       k_pct_val,
                "odds":            int(odds),
                "stake":           stake,
                "kelly_triggered": kelly_triggered,
                "bookmaker":       odds_info.get("bookmaker"),
                "notes":           build_rationale(dict(row), "F5"),
            })

        logger.info(f"innings submarkets {sys_key}: {len(results[sys_key])} qualifying bets "
                    f"(scalar={scalar:.4f}, log_only={log_only})")
    return results


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"F5 run | type={run_type} | date={run_date}")

    from mlb.systems.F5_Pro_System.config_f5 import cfg
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
            book             = row.get("bookmaker"),
            morning_odds     = row.get("morning_odds"),
            notes            = build_rationale(row.to_dict(), "F5"),
        )
        if bet_id == -1:
            continue
        if triggered:
            bets_logged += 1
            bet_rows.append(row.to_dict())

    post_bets(bet_rows, system="F5", run_date=run_date)

    # Innings sub-markets. GAME is handled by the standalone GAME runner.
    scalars     = _load_innings_scalars()
    sub_results = _score_innings_submarkets(today_df, scalars, cfg, run_date)
    sub_logged  = {}
    for sys_key, _, _, _ in _INNINGS_SUBMARKET_CONFIG:
        sub_tracker  = BetTracker(cfg["bet_db"], system=sys_key)
        logged       = 0
        sub_rows     = []
        for bet in sub_results.get(sys_key, []):
            ret = sub_tracker.log_bet(
                game_date        = run_date,
                game_pk          = bet["game_pk"],
                player           = bet["player"],
                away_team        = bet["away_team"],
                home_team        = bet["home_team"],
                bet_type         = bet["bet_type"],
                model_prob       = bet["model_prob"],
                market_prob      = bet["market_prob"],
                edge             = bet["edge"],
                kelly_pct        = bet["kelly_pct"],
                odds             = bet["odds"],
                stake            = bet["stake"],
                kelly_triggered  = bet["kelly_triggered"],
                paper            = cfg["PAPER"],
                book             = bet.get("bookmaker"),
                notes            = bet.get("notes", ""),
            )
            if ret != -1:
                logged += 1
                sub_rows.append(bet)
        sub_logged[sys_key] = logged
        post_bets(sub_rows, system=sys_key, run_date=run_date)

    logger.info(f"F5: {bets_logged} bets logged | " +
                " | ".join(f"{k}: {v}" for k, v in sub_logged.items()))
    return {"bets_logged": bets_logged, "sub_logged": sub_logged, "bet_rows": bet_rows}
