"""
runners/run_batter_hits.py — BATTER_HITS Pro v1 daily runner.

Odds source: SGO snapshot (extract_batter_hits_odds)
Feature source: BATTER_HITS_System/data/model_features.csv
Model: xgb_batter_hits_v1.json — count:poisson -> lambda (expected hits/game)
Scoring: P(hits > line) = 1 - NegBin_CDF(floor(line), lambda, nb_alpha)

Active sizing is enabled; bets trigger when edge and Kelly sizing clear
configured gates.

run() is called by main.py.
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)

# Active sizing enabled.
LOG_ONLY = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


def _negbin_p_over(line: float, mu: float, nb_alpha: float) -> float:
    """P(X > line) for NegBin(mu, alpha). Degrades to Poisson if alpha <= 0."""
    k = int(math.floor(line))   # P(X > line) = P(X >= k+1) = 1 - P(X <= k)
    if mu <= 0:
        return 0.5
    if nb_alpha <= 0:
        from scipy.stats import poisson
        return float(1.0 - poisson.cdf(k, mu))
    from scipy.stats import nbinom
    n = 1.0 / nb_alpha
    p = n / (n + mu)
    return float(1.0 - nbinom.cdf(k, n, p))


def _negbin_p_under(line: float, mu: float, nb_alpha: float) -> float:
    """P(X < line) for NegBin. For half-point lines = P(X <= floor(line))."""
    k = int(math.floor(line))
    if mu <= 0:
        return 0.5
    if nb_alpha <= 0:
        from scipy.stats import poisson
        return float(poisson.cdf(k, mu))
    from scipy.stats import nbinom
    n = 1.0 / nb_alpha
    p = n / (n + mu)
    return float(nbinom.cdf(k, n, p))


# ── Model + calibrator load ───────────────────────────────────────────────────

def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict, float]:
    """Returns (booster, features, feature_means, nb_alpha)."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(cfg["gcs_model_xgb"], Path(tmpdir) / "xgb_bh.json")
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_xgb"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("model_meta missing 'features' key")
    nb_alpha      = float(meta.get("nb_alpha", 0.10))
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(
        f"BATTER_HITS model loaded | features={len(features)} | "
        f"nb_alpha={nb_alpha:.4f} | MAE={meta.get('mae_oos', '?')}"
    )
    return booster, features, feature_means, nb_alpha


def _load_calibrator(cfg: dict):
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists

    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            return pickle.loads(read_bytes(gcs_key))
        except Exception as e:
            logger.warning(f"BATTER_HITS calibrator load failed: {e}")
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"BATTER_HITS calibrator load failed: {e}")
    return None


# ── Today's feature rows ──────────────────────────────────────────────────────

def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Build feature rows for today's batter slate.

    1. Pull latest rolling-stats snapshot per batter from model_features.csv
    2. Fetch today's schedule + probable pitchers
    3. Fetch confirmed lineups (fall back to ewma_batting_order)
    4. Fetch live weather per game
    5. Join pitcher features + weather + park
    Returns one row per (batter, game) with player_name for odds matching.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv, exists
    from mlb_core.data.lineups import get_today_schedule, _pull_lineup_date

    # 1. Schedule
    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning(f"BATTER_HITS: no games for {run_date}")
        return pd.DataFrame()

    # 2. Latest batter feature snapshot
    feat_key = cfg.get("gcs_model_features", cfg.get("model_features"))
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"BATTER_HITS: feature load failed: {e}")
        return pd.DataFrame()

    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])
    batter_latest = (
        feat_df.sort_values("game_date")
               .groupby("batter", as_index=False)
               .last()
    )
    logger.info(f"BATTER_HITS: {len(batter_latest):,} batters in snapshot")

    # 3. Latest pitcher feature snapshot (used for joining opp_pitcher features)
    pf_key = cfg.get("gcs_pitcher_hits_features", cfg.get("pitcher_hits_features", ""))
    pitcher_latest = pd.DataFrame()
    if pf_key:
        try:
            pf_raw = read_csv(pf_key, low_memory=False) if GCS_BUCKET \
                     else pd.read_csv(cfg.get("pitcher_hits_features", ""), low_memory=False)
            if not pf_raw.empty:
                pf_raw["game_date"] = pd.to_datetime(pf_raw["game_date"])
                pitcher_latest = pf_raw.sort_values("game_date").groupby("pitcher", as_index=False).last()
        except Exception as e:
            logger.warning(f"BATTER_HITS: pitcher features load failed: {e}")

    # 4. Confirmed lineups
    try:
        lineups = _pull_lineup_date(run_date, verbose=False)
    except Exception as e:
        logger.warning(f"BATTER_HITS: lineup fetch failed: {e}")
        lineups = pd.DataFrame()

    # 5. Weather
    weather_today = _fetch_today_weather(sched)

    # 6. Build candidate rows
    candidates = _candidates_from_lineups(sched, lineups, batter_latest)
    logger.info(f"BATTER_HITS: {len(candidates):,} candidate batter-game rows")
    if candidates.empty:
        return pd.DataFrame()

    # 7. Attach opposing pitcher features
    out = _join_pitcher_features(candidates, sched, pitcher_latest)

    # 8. Overlay weather
    out = _join_weather(out, weather_today)

    # 9. Park hits factor + context features
    from runners.build_batter_hits_features import HITS_PARK_FACTORS, STADIUMS_ROOF, TEAM_NAME_TO_ABBR
    home_abbr = out["home_team"].map(TEAM_NAME_TO_ABBR)
    out["hits_park_factor"] = home_abbr.map(HITS_PARK_FACTORS).fillna(1.0)
    out["is_dome"]          = home_abbr.map(lambda t: 1 if STADIUMS_ROOF.get(t) else 0).fillna(0).astype(int)
    out["temperature_f"]    = out.get("temperature_f", pd.Series(70, index=out.index)).fillna(70)
    out["post_pitch_clock"] = 1  # all 2026 games post pitch clock

    return out


def _candidates_from_lineups(sched, lineups, batter_latest):
    from runners.build_batter_hits_features import TEAM_NAME_TO_ABBR
    rows = []

    if not lineups.empty:
        for _, r in lineups.iterrows():
            game_pk   = r["game_pk"]
            batter_id = r["player_id"]
            bat_row   = batter_latest[batter_latest["batter"] == batter_id]
            if bat_row.empty:
                continue
            bat = bat_row.iloc[0].to_dict()
            bat.update({
                "game_pk":          game_pk,
                "home_team":        r.get("home_team"),
                "away_team":        r.get("away_team"),
                "batter_team_side": r["team_side"],
                "batting_order":    int(r["batting_order"]),
                "player_name":      r["player_name"],
            })
            rows.append(bat)

    if not rows:
        logger.warning("BATTER_HITS: no confirmed lineup candidates; skipping unsafe historical-team fallback")
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out = out.drop_duplicates(subset=["batter", "game_pk"], keep="first")
    out["is_home"] = (out["batter_team_side"] == "home").astype(int)
    return out


def _join_pitcher_features(candidates, sched, pitcher_latest):
    games_idx = sched.set_index("game_pk")
    out_rows = []
    for _, r in candidates.iterrows():
        game_pk = r["game_pk"]
        if game_pk not in games_idx.index:
            continue
        game = games_idx.loc[game_pk]
        opp_pitcher_id = (
            game.get("away_pitcher_id") if r["batter_team_side"] == "home"
            else game.get("home_pitcher_id")
        )
        merged = dict(r)
        merged["opp_pitcher_id"] = opp_pitcher_id
        if not pitcher_latest.empty and pd.notna(opp_pitcher_id):
            pit = pitcher_latest[pitcher_latest["pitcher"] == opp_pitcher_id]
            if not pit.empty:
                for col in pit.iloc[0].index:
                    if col.startswith("pitcher_") and col.endswith("_L20"):
                        merged[col] = pit.iloc[0][col]
        out_rows.append(merged)
    return pd.DataFrame(out_rows)


def _join_weather(df, weather_today):
    if df.empty or not weather_today:
        return df
    wx_rows = [{"game_pk": gp, **wx} for gp, wx in weather_today.items()]
    wx_df   = pd.DataFrame(wx_rows)
    overlap = set(df.columns) & set(wx_df.columns) - {"game_pk"}
    if overlap:
        wx_df = wx_df.drop(columns=list(overlap))
    return df.merge(wx_df, on="game_pk", how="left")


def _fetch_today_weather(sched):
    try:
        from mlb_core.data.weather import fetch_live_weather_for_slate
        out = fetch_live_weather_for_slate(sched)
        logger.info(f"BATTER_HITS weather: {len(out)}/{len(sched)} games")
        return out
    except Exception as e:
        logger.warning(f"BATTER_HITS: weather fetch failed: {e}")
        return {}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.utils import remove_vig
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_batter_hits_odds

    # Snapshot freshness
    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = _sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"BATTER_HITS: aborting — {_reason}")
        return pd.DataFrame()

    # Feature build sentinel
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "BATTER_HITS_System")
    if not _sok:
        msg = f"BATTER_HITS: aborting — stale/failed feature build: {_sreason}"
        logger.error(msg)
        from mlb_core.notify.discord import post_error
        post_error("BATTER_HITS", msg)
        return pd.DataFrame()

    booster, features, feature_means, nb_alpha = _load_model(cfg)
    calibrator = _load_calibrator(cfg)

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        logger.warning("BATTER_HITS: no candidate rows — skipping")
        return pd.DataFrame()

    events    = _sgo.load_snapshot(_SGO_KEY)
    hits_odds = extract_batter_hits_odds(events) if events else {}
    if not hits_odds:
        logger.warning("BATTER_HITS: no odds in SGO snapshot")
        return pd.DataFrame()
    logger.info(f"BATTER_HITS: {len(hits_odds)} players with odds")

    # Score all candidates
    X = feat_df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            mean = feature_means.get(col)
            if mean is not None:
                X[col] = X[col].fillna(float(mean))
    X = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    preds = booster.predict(dm, iteration_range=(0, ntree)) if ntree else booster.predict(dm)

    feat_df = feat_df.copy()
    feat_df["lambda_hits"] = preds.clip(0.01, cfg.get("mc_cap", 10))

    # Apply isotonic calibrator to lambda only inside its fitted input range.
    # sklearn's out_of_bounds="clip" is useful for offline evaluation but too
    # aggressive for live betting, where distribution drift would otherwise
    # snap lambdas to a boundary and inflate O/U probabilities.
    feat_df["raw_lambda_hits"] = feat_df["lambda_hits"]
    if calibrator is not None:
        try:
            raw = feat_df["lambda_hits"].values.copy()
            x_min = getattr(calibrator, "X_min_", None)
            x_max = getattr(calibrator, "X_max_", None)
            in_range = np.ones(len(raw), dtype=bool)
            if x_min is not None and x_max is not None:
                in_range = (raw >= x_min) & (raw <= x_max)
            cal = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            feat_df["lambda_hits"] = np.clip(cal, 0.01, cfg.get("mc_cap", 10))
            feat_df["calibrator_in_range"] = in_range
            logger.info(
                "BATTER_HITS: calibrator applied to %d/%d batters",
                int(in_range.sum()), len(raw),
            )
        except Exception as e:
            logger.warning(f"BATTER_HITS calibrator predict failed: {e}")
            feat_df["calibrator_in_range"] = False
    else:
        feat_df["calibrator_in_range"] = False

    # Name index for odds matching
    if "player_name" not in feat_df.columns:
        logger.warning("BATTER_HITS: player_name column missing from feature rows")
        return pd.DataFrame()

    feat_df["_name_key"] = feat_df["player_name"].apply(_normalize_name)
    name_to_idx = {n: i for i, n in enumerate(feat_df["_name_key"]) if n}
    _all_keys   = list(name_to_idx)

    def _resolve(raw_name):
        key = _normalize_name(raw_name)
        if not key:
            return None
        idx = name_to_idx.get(key)
        if idx is not None:
            return idx
        import difflib
        matches = difflib.get_close_matches(key, _all_keys, n=1, cutoff=0.85)
        if matches:
            return name_to_idx[matches[0]]
        return None

    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _engine     = _make_engine("unused")
    _game_pks   = list(feat_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched = prefetch_exposure(_engine, _game_pks, run_date, system="BATTER_HITS")
    _pending: dict[int, float] = {}
    from mlb_core.risk.gates import is_suppressed as _is_suppressed
    _gate_suppressed = _is_suppressed("BATTER_HITS")
    if _gate_suppressed:
        logger.warning("BATTER_HITS gate active -- logging only, no staked bets this run")

    PROP_VIG = 0.07
    results  = []

    for player_name, odds_info in hits_odds.items():
        idx = _resolve(player_name)
        if idx is None:
            continue
        row  = feat_df.iloc[idx]
        try:
            event_id = int(odds_info.get("event_id"))
            row_game_pk = int(row.get("game_pk"))
            if event_id != row_game_pk:
                logger.warning(
                    "BATTER_HITS: skipping %s due event/game mismatch odds_event=%s feature_game=%s",
                    player_name, event_id, row_game_pk,
                )
                continue
        except (TypeError, ValueError):
            pass
        mu   = float(row["lambda_hits"])
        line = odds_info.get("line")
        if line is None:
            continue

        p_over  = _negbin_p_over(line, mu, nb_alpha)
        p_under = _negbin_p_under(line, mu, nb_alpha)

        # Devig both sides of the O/U
        mkt_over  = american_to_implied_prob(odds_info["over_odds"])
        mkt_under = american_to_implied_prob(odds_info["under_odds"])
        total = mkt_over + mkt_under
        if not total:
            continue
        fair_over  = mkt_over  / total
        fair_under = mkt_under / total

        edge_over  = p_over  - fair_over
        edge_under = p_under - fair_under

        if edge_over >= edge_under:
            side, edge, fair, odds, model_prob = "OVER",  edge_over,  fair_over,  odds_info["over_odds"],  p_over
        else:
            side, edge, fair, odds, model_prob = "UNDER", edge_under, fair_under, odds_info["under_odds"], p_under

        logger.info(
            "BATTER_HITS pred | %s | raw_lam=%.3f lam=%.3f in_range=%s "
            "line=%.1f %s | model=%.3f fair=%.3f edge=%+.3f",
            player_name,
            float(row.get("raw_lambda_hits", mu)),
            mu,
            bool(row.get("calibrator_in_range", False)),
            float(line),
            side,
            model_prob, fair, edge,
        )

        k_pct_val = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(
            _bankroll, int(row["game_pk"]),
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

        kelly_triggered = (edge >= cfg["min_edge"]) and (stake > 0) and (not LOG_ONLY) and (not _gate_suppressed)
        if kelly_triggered and stake > 0:
            gp = int(row.get("game_pk", 0))
            _pending[gp] = _pending.get(gp, 0.0) + stake

        results.append({
            "player":          player_name,
            "game_pk":         int(row.get("game_pk", 0)),
            "away_team":       odds_info["away_team"],
            "home_team":       odds_info["home_team"],
            "line":            float(line),
            "side":            side,
            "bet_type":        f"BATTER_HITS_{side}_{line}",
            "raw_lambda_hits": round(float(row.get("raw_lambda_hits", mu)), 4),
            "lambda_hits":     round(mu, 4),
            "model_prob":      round(model_prob, 4),
            "market_prob":     round(fair, 4),
            "edge":            round(edge, 4),
            "kelly_pct":       round(k_pct_val, 4),
            "odds":            odds,
            "stake":           stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
            "bookmaker":       odds_info.get("bookmaker"),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("edge", ascending=False)


# ── Entry point ───────────────────────────────────────────────────────────────

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"BATTER_HITS run | type={run_type} | date={run_date} | log_only={LOG_ONLY}")

    from BATTER_HITS_System.config_batter_hits import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("BATTER_HITS: no qualifying bets today")
        post_bets([], system="BATTER_HITS", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="BATTER_HITS")
    bets_logged = 0
    bet_rows    = []

    for _, row in today_df.iterrows():
        triggered = bool(row.get("kelly_triggered", False))
        bet_id = tracker.log_bet(
            game_date       = run_date,
            game_pk         = row.get("game_pk"),
            player          = row.get("player"),
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

    log_suffix = " (log-only — 200-bet gate not yet cleared)" if LOG_ONLY else ""
    logger.info(f"BATTER_HITS: {bets_logged} bets logged{log_suffix}")
    post_bets(bet_rows, system="BATTER_HITS", run_date=run_date)

    return {"bets_logged": bets_logged, "log_only": LOG_ONLY, "bet_rows": bet_rows}
