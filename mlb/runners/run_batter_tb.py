"""
runners/run_batter_tb.py - BATTER_TB Pro v1 daily runner.

Uses the dedicated BATTER_TB count model to estimate expected total bases and
convert that lambda to O/U probabilities with a negative-binomial CDF.
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

from mlb.runners.run_batter_hits import (
    _fetch_today_weather,
    _join_pitcher_features,
    _join_weather,
    _negbin_p_over,
    _negbin_p_under,
    _normalize_name,
)
from mlb_core.risk.threshold_bets import score_threshold_bet

logger = logging.getLogger(__name__)

LOG_ONLY = False


def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict, float]:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(cfg["gcs_model_xgb"], Path(tmpdir) / "xgb_tb.json")
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_xgb"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("BATTER_TB model_meta missing 'features' key")
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    nb_alpha = float(meta.get("nb_alpha", 0.15))
    feature_means = meta.get("feature_means", {}) or {}
    logger.info(
        "BATTER_TB model loaded | features=%d | nb_alpha=%.4f | MAE=%s",
        len(features), nb_alpha, meta.get("mae_oos", "?"),
    )
    return booster, features, feature_means, nb_alpha


def _load_calibrator(cfg: dict):
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import exists, read_bytes

    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            return pickle.loads(read_bytes(gcs_key))
        except Exception as e:
            logger.warning("BATTER_TB calibrator load failed: %s", e)
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning("BATTER_TB calibrator load failed: %s", e)
    return None


def _candidates_from_lineups(sched, lineups, batter_latest):
    rows = []

    if not lineups.empty:
        for _, r in lineups.iterrows():
            game_pk = r["game_pk"]
            batter_id = r["player_id"]
            bat_row = batter_latest[batter_latest["batter"] == batter_id]
            if bat_row.empty:
                continue
            bat = bat_row.iloc[0].to_dict()
            bat.update({
                "game_pk": game_pk,
                "home_team": r.get("home_team"),
                "away_team": r.get("away_team"),
                "batter_team_side": r["team_side"],
                "batting_order": int(r["batting_order"]),
                "player_name": r["player_name"],
            })
            rows.append(bat)

    if not rows:
        logger.warning("BATTER_TB: no confirmed lineup candidates; skipping unsafe historical-team fallback")
        return pd.DataFrame()
    out = pd.DataFrame(rows).drop_duplicates(subset=["batter", "game_pk"], keep="first")
    out["is_home"] = (out["batter_team_side"] == "home").astype(int)
    return out


def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.data.lineups import _pull_lineup_date, get_today_schedule
    from mlb_core.storage import read_csv
    from mlb.runners.build_batter_tb_features import TB_PARK_FACTORS
    from mlb.runners.build_batter_hits_features import STADIUMS_ROOF, TEAM_NAME_TO_ABBR

    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning("BATTER_TB: no games for %s", run_date)
        return pd.DataFrame()

    feat_key = cfg.get("gcs_model_features", cfg["model_features"])
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error("BATTER_TB: feature load failed: %s", e)
        return pd.DataFrame()

    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])
    batter_latest = feat_df.sort_values("game_date").groupby("batter", as_index=False).last()

    pitcher_latest = pd.DataFrame()
    pf_key = cfg.get("gcs_pitcher_tb_features", cfg.get("pitcher_tb_features", ""))
    if pf_key:
        try:
            pf_raw = read_csv(pf_key, low_memory=False) if GCS_BUCKET else pd.read_csv(cfg["pitcher_tb_features"], low_memory=False)
            if not pf_raw.empty:
                pf_raw["game_date"] = pd.to_datetime(pf_raw["game_date"])
                pitcher_latest = pf_raw.sort_values("game_date").groupby("pitcher", as_index=False).last()
        except Exception as e:
            logger.warning("BATTER_TB: pitcher feature load failed: %s", e)

    try:
        lineups = _pull_lineup_date(run_date, verbose=False)
    except Exception as e:
        logger.warning("BATTER_TB: lineup fetch failed: %s", e)
        lineups = pd.DataFrame()

    candidates = _candidates_from_lineups(sched, lineups, batter_latest)
    if candidates.empty:
        return pd.DataFrame()

    out = _join_pitcher_features(candidates, sched, pitcher_latest)
    out = _join_weather(out, _fetch_today_weather(sched))

    home_abbr = out["home_team"].map(TEAM_NAME_TO_ABBR)
    out["tb_park_factor"] = home_abbr.map(TB_PARK_FACTORS).fillna(1.0)
    out["is_dome"] = home_abbr.map(lambda t: 1 if STADIUMS_ROOF.get(t) else 0).fillna(0).astype(int)
    out["temperature_f"] = out.get("temperature_f", pd.Series(70, index=out.index)).fillna(70)
    out["post_pitch_clock"] = 1
    return out


def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.notify.discord import post_error
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_batter_tb_odds
    from mlb_core.risk.exposure import apply_cap, prefetch_exposure
    from mlb_core.storage import check_build_sentinel
    from mlb_core.tracking.bet_tracker import _make_engine

    fresh, reason = _sgo.check_snapshot_freshness("Odds/sgo/latest.json")
    if not fresh:
        logger.error("BATTER_TB: aborting - %s", reason)
        return pd.DataFrame()

    sok, sreason = check_build_sentinel(GCS_BUCKET, "BATTER_TB_System")
    if not sok:
        msg = f"BATTER_TB: aborting - stale/failed feature build: {sreason}"
        logger.error(msg)
        post_error("BATTER_TB", msg)
        return pd.DataFrame()

    booster, features, feature_means, nb_alpha = _load_model(cfg)
    calibrator = _load_calibrator(cfg)
    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        return pd.DataFrame()

    events = _sgo.load_snapshot("Odds/sgo/latest.json")
    tb_odds = extract_batter_tb_odds(events) if events else {}
    if not tb_odds:
        logger.warning("BATTER_TB: no odds in SGO snapshot")
        return pd.DataFrame()
    # 2+/3+ threshold sub-markets (2026-08-19). Only scored for players who
    # also have a main-line quote this run (the loop below iterates
    # tb_odds.items()) -- a book offering an alt line for a player without
    # also offering its main line is rare enough not to be worth a separate
    # iteration path for v1.
    tb_2plus_odds = _sgo.extract_batter_tb_alt_line_odds(events, 2) if events else {}
    tb_3plus_odds = _sgo.extract_batter_tb_alt_line_odds(events, 3) if events else {}

    X = feat_df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    for col in features:
        mean = feature_means.get(col)
        if mean is not None:
            X[col] = X[col].fillna(float(mean))
    dm = xgb.DMatrix(X.astype(float), feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    preds = booster.predict(dm, iteration_range=(0, ntree)) if ntree else booster.predict(dm)

    feat_df = feat_df.copy()
    feat_df["lambda_tb"] = np.clip(preds, 0.01, cfg.get("mc_cap", 14))
    feat_df["raw_lambda_tb"] = feat_df["lambda_tb"]
    if calibrator is not None:
        try:
            raw = feat_df["lambda_tb"].values.copy()
            x_min = getattr(calibrator, "X_min_", None)
            x_max = getattr(calibrator, "X_max_", None)
            in_range = np.ones(len(raw), dtype=bool)
            if x_min is not None and x_max is not None:
                in_range = (raw >= x_min) & (raw <= x_max)
            cal = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            feat_df["lambda_tb"] = np.clip(cal, 0.01, cfg.get("mc_cap", 14))
            feat_df["calibrator_in_range"] = in_range
        except Exception as e:
            logger.warning("BATTER_TB calibrator predict failed: %s", e)
            feat_df["calibrator_in_range"] = False
    else:
        feat_df["calibrator_in_range"] = False

    if "player_name" not in feat_df.columns:
        return pd.DataFrame()
    feat_df["_name_key"] = feat_df["player_name"].apply(_normalize_name)
    name_to_idx = {n: i for i, n in enumerate(feat_df["_name_key"]) if n}
    all_keys = list(name_to_idx)

    def _resolve(raw_name):
        import difflib

        key = _normalize_name(raw_name)
        if not key:
            return None
        idx = name_to_idx.get(key)
        if idx is not None:
            return idx
        matches = difflib.get_close_matches(key, all_keys, n=1, cutoff=0.85)
        return name_to_idx[matches[0]] if matches else None

    engine = _make_engine("unused")
    game_pks = list(feat_df["game_pk"].dropna().astype(int).unique())
    bankroll, prefetched = prefetch_exposure(engine, game_pks, run_date, system="BATTER_TB")
    pending: dict[int, float] = {}
    from mlb_core.risk.gates import is_suppressed as _is_suppressed
    from mlb_core.risk.calibration import apply as _cal_apply, EDGE_CAP as _EDGE_CAP
    _gate_suppressed = _is_suppressed("BATTER_TB")
    if _gate_suppressed:
        logger.warning("BATTER_TB gate active -- logging only, no staked bets this run")
    results = []

    for player_name, odds_info in tb_odds.items():
        idx = _resolve(player_name)
        if idx is None:
            continue
        row = feat_df.iloc[idx]
        try:
            event_id = int(odds_info.get("event_id"))
            row_game_pk = int(row.get("game_pk"))
            if event_id != row_game_pk:
                logger.warning(
                    "BATTER_TB: skipping %s due event/game mismatch odds_event=%s feature_game=%s",
                    player_name, event_id, row_game_pk,
                )
                continue
        except (TypeError, ValueError):
            # Fixed 2026-08-17 (finding C5.4): was `pass` -- a None/non-
            # numeric event_id (can't even run the check above) silently
            # fell through to scoring the prop WITHOUT ever having
            # validated the match, on the exact market class that produced
            # the historically-quantified ~$933 fake-P&L incident this
            # guard exists to prevent a repeat of. `continue` instead: no
            # validated match, no bet.
            logger.warning(
                "BATTER_TB: skipping %s -- event_id/game_pk not comparable "
                "(odds_event=%r feature_game=%r)",
                player_name, odds_info.get("event_id"), row.get("game_pk"),
            )
            continue
        mu = float(row["lambda_tb"])
        line = odds_info.get("line")
        if line is None:
            continue
        # SANITY GUARD vs cross-line mixing (per-book main-line collapse):
        # an everyday player's u1.5 TB at better than -120, or u0.5 shorter
        # than -120, is not a real market price -- it is a wrong-line quote.
        _uo = odds_info.get("under_odds")
        if _uo is not None and ((float(line) >= 1.5 and int(_uo) > -120)
                                or (float(line) <= 0.5 and int(_uo) < -120)):
            logger.warning(
                "BATTER_TB %s: implausible u%.1f at %s -- cross-line quote, skipping",
                row.get("player_name", "?"), float(line), _uo)
            continue

        p_over = _negbin_p_over(line, mu, nb_alpha)
        p_under = _negbin_p_under(line, mu, nb_alpha)
        # Shin devig (not proportional) -- favorite-longshot correction,
        # matches the odds_history ingest devig.
        from mlb_core.odds.utils import devig_two_way
        mkt_over = american_to_implied_prob(odds_info["over_odds"])
        mkt_under = american_to_implied_prob(odds_info["under_odds"])
        if not (mkt_over + mkt_under):
            continue
        fair_over, fair_under = devig_two_way(mkt_over, mkt_under, method="shin")
        if pd.isna(fair_over) or pd.isna(fair_under):
            continue
        edge_over = p_over - fair_over
        edge_under = p_under - fair_under

        if edge_over >= edge_under:
            side, edge, fair, odds, model_prob = "OVER", edge_over, fair_over, odds_info["over_odds"], p_over
        else:
            side, edge, fair, odds, model_prob = "UNDER", edge_under, fair_under, odds_info["under_odds"], p_under

        # Calibrate against realized outcomes (corrects overconfidence) and
        # recompute edge before sizing. Edge cap only applies once calibrated,
        # matching every other system's winner's-curse defense (see
        # docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md
        # finding A3 -- BATTER_TB previously had none of this at all).
        model_prob, _cal = _cal_apply("BATTER_TB", model_prob)
        edge = model_prob - fair
        _edge_capped = _cal and edge > _EDGE_CAP

        k_pct_val = kpct(model_prob, odds, cfg["kelly_fraction"])
        bankroll, cap = apply_cap(
            bankroll, int(row["game_pk"]), prefetched, pending,
            cap_units=cfg.get("cap_units", 10.0),
        )
        raw_stake = kelly_stake(
            model_prob, odds,
            bankroll=bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        )
        stake = min(raw_stake, cap)
        triggered = (edge >= cfg["min_edge"]) and (stake > 0) and (not LOG_ONLY) and (not _gate_suppressed) and (not _edge_capped) and (not _sgo.is_live_event(odds_info.get("commence_time")))
        if triggered:
            gp = int(row.get("game_pk", 0))
            pending[gp] = pending.get(gp, 0.0) + stake

        results.append({
            "player": player_name,
            "game_pk": int(row.get("game_pk", 0)),
            "away_team": odds_info["away_team"],
            "home_team": odds_info["home_team"],
            "line": float(line),
            "side": side,
            "bet_type": f"BATTER_TB_{side}_{line}",
            "raw_lambda_tb": round(float(row.get("raw_lambda_tb", mu)), 4),
            "lambda_tb": round(mu, 4),
            "model_prob": round(model_prob, 4),
            "market_prob": round(fair, 4),
            "edge": round(edge, 4),
            "kelly_pct": round(k_pct_val, 4),
            "odds": odds,
            "stake": stake if triggered else 0.0,
            "kelly_triggered": triggered,
            "bookmaker": odds_info.get("bookmaker"),
        })

        for n, alt_info in ((2, tb_2plus_odds.get(player_name)),
                           (3, tb_3plus_odds.get(player_name))):
            if alt_info is None:
                continue
            trow, bankroll = score_threshold_bet(
                model_prob_raw=_negbin_p_over(n - 0.5, mu, nb_alpha),
                alt_odds_info=alt_info,
                vig_market_key=f"btb_{n}plus",
                game_pk=int(row.get("game_pk", 0)),
                bankroll=bankroll,
                prefetched_stakes=prefetched,
                pending_stakes=pending,
                cfg=cfg,
                gate_suppressed=_gate_suppressed or LOG_ONLY,
            )
            if trow is None:
                continue
            logger.info(
                "BATTER_TB pred | %s | %d+ TB | lam=%.3f | model=%.3f fair=%.3f edge=%+.3f",
                player_name, n, mu, trow["model_prob"], trow["market_prob"], trow["edge"],
            )
            results.append({
                "player": player_name,
                "game_pk": int(row.get("game_pk", 0)),
                "away_team": trow["away_team"] or odds_info["away_team"],
                "home_team": trow["home_team"] or odds_info["home_team"],
                "line": float(n),
                "side": f"{n}PLUS",
                "bet_type": f"BATTER_TB_{n}PLUS_{float(n)}",
                "raw_lambda_tb": round(float(row.get("raw_lambda_tb", mu)), 4),
                "lambda_tb": round(mu, 4),
                "model_prob": trow["model_prob"],
                "market_prob": trow["market_prob"],
                "edge": trow["edge"],
                "kelly_pct": trow["kelly_pct"],
                "odds": trow["odds"],
                "stake": trow["stake"],
                "kelly_triggered": trow["kelly_triggered"],
                "bookmaker": trow["bookmaker"],
            })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("edge", ascending=False)


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info("BATTER_TB run | type=%s | date=%s | log_only=%s", run_type, run_date, LOG_ONLY)

    from mlb.systems.BATTER_TB_System.config_batter_tb import cfg
    from mlb_core.notify.discord import post_bets
    from mlb_core.tracking import BetTracker

    today_df = _build_predictions(cfg, run_date)
    if today_df.empty:
        post_bets([], system="BATTER_TB", run_date=run_date)
        return {"bets_logged": 0}

    tracker = BetTracker(cfg["bet_db"], system="BATTER_TB")
    bets_logged = 0
    bet_rows = []
    for _, row in today_df.iterrows():
        triggered = bool(row.get("kelly_triggered", False))
        bet_id = tracker.log_bet(
            game_date=run_date,
            game_pk=row.get("game_pk"),
            player=row.get("player"),
            away_team=row.get("away_team"),
            home_team=row.get("home_team"),
            bet_type=row.get("bet_type"),
            model_prob=row.get("model_prob"),
            market_prob=row.get("market_prob"),
            edge=row.get("edge"),
            kelly_pct=row.get("kelly_pct"),
            odds=row.get("odds"),
            stake=row.get("stake"),
            kelly_triggered=triggered,
            paper=cfg["PAPER"],
            book=row.get("bookmaker"),
        )
        if bet_id == -1:
            continue
        bets_logged += 1
        if triggered:
            bet_rows.append(row.to_dict())

    post_bets(bet_rows, system="BATTER_TB", run_date=run_date)
    return {"bets_logged": bets_logged, "log_only": LOG_ONLY, "bet_rows": bet_rows}
