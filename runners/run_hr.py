"""
runners/run_hr.py — HR Pro daily runner for Cloud Run.

Odds source: SGO snapshot at Odds/sgo/latest.json (written by snapshot_odds.py)
Feature source: GCS model_features.csv (pre-built, updated nightly)
Model: xgb_hr_v6.json loaded from GCS

run() is called by main.py.
"""
import json
import pickle
import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xgboost as xgb
from mlb_core.odds.dk_scraper import resolve_team

logger = logging.getLogger(__name__)

# Feature list is loaded from model_meta_hr_v6.json at predict time — see
# _load_model below. The previous hardcoded HR_FEATURES drifted from the
# trained model (34 listed vs 22 in the model) and caused
# `feature_names mismatch` errors at predict time once SGO odds started
# returning data. Authoritative list lives with the model artifact.


def _fetch_hr_odds(run_date: str) -> dict:
    """
    Fetch anytime HR odds from the latest SGO snapshot in GCS.

    Returns the same dict shape the old Odds-API-based function did:
        {player_name: {"odds": int,
                       "away_team": str,
                       "home_team": str,
                       "event_id": str}}

    Reads Odds/sgo/latest.json which is written twice daily by
    runners.snapshot_odds. If the snapshot is missing or stale, returns
    an empty dict and the caller will produce no bets.

    Cost: 0 SGO objects (this reads the cached snapshot, not the live API).
    """
    from mlb_core.odds import sgo

    # Abort if snapshot is stale — stale lines produce fictitious edge.
    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"aborting run — {_reason}")
        return pd.DataFrame()
    # Sentinel check -- abort if feature build is stale or failed
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "HR_Pro")
    if not _sok:
        msg = f"HR: aborting run -- stale/failed feature build: {_sreason}"
        logger.error(msg)
        from mlb_core.notify.discord import post_error
        post_error("HR", msg)
        return pd.DataFrame()
    logger.info("HR: sentinel ok -- %s", _sreason)
    # E10: load morning snapshot for line movement signal
    from mlb_core.odds.line_movement import load_morning_odds
    _morning_odds = load_morning_odds(run_date)

    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("HR odds: SGO snapshot empty or missing")
        return {}

    props = sgo.extract_hr_props(events)
    logger.info(f"HR odds: {len(props)} players from SGO snapshot")
    return props


def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict]:
    """Load HR XGBoost model + its trained feature list + training means.

    Returns (booster, features, feature_means).
      - `features` comes from the model_meta's `features` key. If the meta
        is missing in GCS, falls back to `booster.feature_names` (set by
        XGBoost when the model was trained with named features). Raises
        only if both are unavailable.
      - `feature_means` is used to fill NaN at predict time. Empty dict if
        meta has no such key — XGBoost handles NaN natively.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes, exists

    booster = xgb.Booster()
    meta = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(
                cfg.get("gcs_model_xgb", cfg["model_xgb"]),
                Path(tmpdir) / "xgb_hr.json",
            )
            booster.load_model(str(local))
            meta_key = cfg.get("gcs_model_meta")
            if meta_key and exists(meta_key):
                try:
                    meta = json.loads(read_bytes(meta_key))
                except Exception as e:
                    logger.warning(f"HR meta load failed ({meta_key}): {e}")
        else:
            booster.load_model(cfg["model_xgb"])
            meta_path = Path(cfg["model_meta"])
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_bytes())
                except Exception as e:
                    logger.warning(f"HR meta load failed ({meta_path}): {e}")

    features = meta.get("features")
    if not features:
        # Fall back to feature names embedded in the booster JSON itself
        features = list(getattr(booster, "feature_names", []) or [])
        if features:
            logger.warning(f"HR meta missing or has no 'features' key — "
                           f"falling back to booster.feature_names "
                           f"({len(features)} features)")
        else:
            raise RuntimeError("HR features unavailable: meta missing and "
                               "booster.feature_names is empty")

    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(f"HR model loaded | features={len(features)} | "
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
            logger.warning(f"HR calibrator load failed: {e}")
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"HR calibrator load failed: {e}")
    return None

def _normalize_name(name: str) -> str:
    """Match the notebook's name normalization: NFD + ASCII-fold + lower + strip."""
    import unicodedata
    if pd.isna(name):
        return ""
    return (
        unicodedata.normalize("NFD", str(name))
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )


def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Build a feature-ready DataFrame for today's slate using live data.

    Replaces the old "filter model_features by today's game_date" approach
    (which never returned rows because today's games haven't been played).
    Instead:
      1. Pull each batter's most-recent rolling-stats snapshot from
         model_features.csv (groupby(batter).last())
      2. Pull each pitcher's most-recent snapshot from pitcher_hr_features.csv
      3. Fetch today's slate + probable pitchers from MLB Stats API
      4. Fetch today's confirmed lineups (may be partial; fall back to
         each batter's ewma_batting_order)
      5. Fetch today's weather forecast per game's venue
      6. For each (batter on today's schedule, opposing probable pitcher),
         assemble a row with: batter stats + pitcher stats + weather +
         park factor + batting order

    Returns DataFrame keyed on player_name (Odds-API style "First Last") so
    the caller can join odds by name.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv, exists
    from mlb_core.data.lineups import get_today_schedule, _pull_lineup_date
    from mlb_core.data.weather import STADIUMS

    # 1. Today's schedule + probable pitchers
    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning(f"HR: no games scheduled for {run_date}")
        return pd.DataFrame()
    logger.info(f"HR: {len(sched)} games on the {run_date} slate")

    # 2. Latest per-batter feature snapshot from model_features
    feat_key = cfg.get("gcs_model_features", cfg.get("model_features"))
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"HR features load failed: {e}")
        return pd.DataFrame()
    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])
    batter_latest = (
        feat_df.sort_values("game_date")
               .groupby("batter", as_index=False)
               .last()
    )
    logger.info(f"HR: latest snapshot for {len(batter_latest):,} batters "
                f"(through {feat_df['game_date'].max().date()})")

    # 3. Latest per-pitcher feature snapshot
    pitcher_key = cfg.get("gcs_pitcher_features", cfg.get("pitcher_features"))
    pitcher_latest = pd.DataFrame()
    if pitcher_key:
        try:
            if GCS_BUCKET:
                if exists(pitcher_key):
                    pdf = read_csv(pitcher_key, low_memory=False)
                else:
                    pdf = pd.DataFrame()
            else:
                pdf = pd.read_csv(cfg["pitcher_features"], low_memory=False)
            if not pdf.empty:
                pdf["game_date"] = pd.to_datetime(pdf["game_date"])
                pitcher_latest = (
                    pdf.sort_values("game_date")
                       .groupby("pitcher", as_index=False)
                       .last()
                )
                logger.info(f"HR: latest snapshot for {len(pitcher_latest):,} pitchers")
        except Exception as e:
            logger.warning(f"HR: pitcher_features load failed: {e}")

    # 4. Confirmed lineups (may be partial)
    try:
        lineups = _pull_lineup_date(run_date, verbose=False)
    except Exception as e:
        logger.warning(f"HR: lineup fetch failed: {e}")
        lineups = pd.DataFrame()
    n_lineup_games = lineups["game_pk"].nunique() if not lineups.empty else 0
    logger.info(f"HR: lineups posted for {n_lineup_games}/{len(sched)} games")

    # 5. Live weather per game (call weather_live ourselves for these games)
    weather_today = _fetch_today_weather(sched)

    # 6. Build a candidate list: batters in confirmed lineups + ewma fallback
    #    For each candidate, identify the opposing probable pitcher and assemble.
    candidates = _candidates_from_lineups_and_fallback(
        sched, lineups, batter_latest,
    )
    logger.info(f"HR: {len(candidates):,} candidate batter-game rows assembled")

    if candidates.empty:
        return pd.DataFrame()

    # 7. Attach the opposing pitcher's features to each candidate
    out = _join_pitcher_features(candidates, sched, pitcher_latest)

    # 8. Overlay live weather
    out = _join_weather(out, weather_today)

    # 9. Add park_factor from home_team (uses HR feature builder's lookup if
    #    available, otherwise neutral 1.0)
    if "hr_park_factor" not in out.columns:
        out["hr_park_factor"] = 1.0

    return out


def _candidates_from_lineups_and_fallback(sched: pd.DataFrame,
                                          lineups: pd.DataFrame,
                                          batter_latest: pd.DataFrame) -> pd.DataFrame:
    """
    Build one row per candidate (batter, game) for today.

    Priority 1: batters in posted lineups — use their actual batting_order.
    Priority 2: fallback for games with no posted lineup — take batters
                whose last game's home_team or away_team matches one of
                today's teams, using their ewma_batting_order as slot.

    The Priority 2 heuristic is imperfect: it picks up batters who played
    AGAINST a team as well as batters on the team. The downstream odds-
    matching step naturally filters these out (no odds posted → drop) but
    if odds are posted for a player on the wrong team it can produce a
    nonsense prediction. To minimize this, take top 9 by ewma_batting_order
    per team-side.

    Returns DataFrame with: batter, player_name, game_pk, home_team,
    away_team, batter_team_side, batting_order, plus all batter_latest cols.
    """
    rows = []

    # --- Priority 1: confirmed lineups ---
    posted_game_pks = set()
    if not lineups.empty:
        for _, r in lineups.iterrows():
            game_pk = r["game_pk"]
            posted_game_pks.add(game_pk)
            batter_id = r["player_id"]
            bat_row = batter_latest[batter_latest["batter"] == batter_id]
            if bat_row.empty:
                continue
            bat = bat_row.iloc[0].to_dict()
            bat["game_pk"]          = game_pk
            bat["home_team"]        = r.get("home_team")
            bat["away_team"]        = r.get("away_team")
            bat["batter_team_side"] = r["team_side"]
            bat["batting_order"]    = int(r["batting_order"])
            bat["player_name"]      = r["player_name"]
            rows.append(bat)

    # --- Priority 2: ewma_batting_order fallback for unposted games ---
    if "ewma_batting_order" in batter_latest.columns and \
       "home_team" in batter_latest.columns and "away_team" in batter_latest.columns:
        for _, game in sched.iterrows():
            if game["game_pk"] in posted_game_pks:
                continue
            for side, team in [("home", game["home_team"]),
                                ("away", game["away_team"])]:
                plausible = batter_latest[
                    (batter_latest["home_team"] == team)
                    | (batter_latest["away_team"] == team)
                ]
                top9 = plausible.sort_values("ewma_batting_order").head(9)
                for _, bat_r in top9.iterrows():
                    bat = bat_r.to_dict()
                    bat["game_pk"]          = game["game_pk"]
                    bat["home_team"]        = game["home_team"]
                    bat["away_team"]        = game["away_team"]
                    bat["batter_team_side"] = side
                    bat["batting_order"]    = int(round(bat.get("ewma_batting_order", 5)))
                    rows.append(bat)
    else:
        logger.info("HR: ewma fallback disabled (missing required columns)")

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out = out.drop_duplicates(subset=["batter", "game_pk"], keep="first")
    return out


def _join_pitcher_features(candidates: pd.DataFrame, sched: pd.DataFrame,
                           pitcher_latest: pd.DataFrame) -> pd.DataFrame:
    """
    For each candidate (batter, game), look up the opposing probable
    pitcher from the schedule and attach that pitcher's latest features.
    """
    games_idx = sched.set_index("game_pk")
    out_rows = []
    for _, r in candidates.iterrows():
        game_pk = r["game_pk"]
        if game_pk not in games_idx.index:
            continue
        game = games_idx.loc[game_pk]
        # Opposing pitcher: if batter is on home team, the opponent is away pitcher
        if r["batter_team_side"] == "home":
            opp_pitcher_id = game.get("away_pitcher_id")
            opp_pitcher_throws = game.get("away_pitcher_throws")
        else:
            opp_pitcher_id = game.get("home_pitcher_id")
            opp_pitcher_throws = game.get("home_pitcher_throws")

        merged = dict(r)
        merged["opp_pitcher_id"]     = opp_pitcher_id
        merged["opp_pitcher_throws"] = opp_pitcher_throws

        if not pitcher_latest.empty and pd.notna(opp_pitcher_id):
            pit = pitcher_latest[pitcher_latest["pitcher"] == opp_pitcher_id]
            if not pit.empty:
                pit_row = pit.iloc[0]
                # Copy only the pitcher_* columns to avoid overwriting batter
                # game-level columns like game_pk, home_team, etc.
                for col in pit_row.index:
                    if col.startswith("pitcher_"):
                        merged[col] = pit_row[col]
        out_rows.append(merged)

    return pd.DataFrame(out_rows)


def _join_weather(df: pd.DataFrame, weather_today: dict) -> pd.DataFrame:
    """
    Attach today's weather forecast to each row.
    weather_today is dict keyed by game_pk with weather columns.
    """
    if df.empty or not weather_today:
        return df
    wx_rows = []
    for game_pk, wx in weather_today.items():
        row = dict(wx)
        row["game_pk"] = game_pk
        wx_rows.append(row)
    wx_df = pd.DataFrame(wx_rows)
    overlap = set(df.columns) & set(wx_df.columns) - {"game_pk"}
    if overlap:
        wx_df = wx_df.drop(columns=list(overlap))
    return df.merge(wx_df, on="game_pk", how="left")


def _fetch_today_weather(sched: pd.DataFrame) -> dict:
    """Live weather per game keyed by game_pk.

    Delegates to mlb_core.data.weather.fetch_live_weather_for_slate which
    uses _fetch_weather's 4-attempt exponential backoff. Replacing the old
    inline implementation that had no retries.
    """
    from mlb_core.data.weather import fetch_live_weather_for_slate
    out = fetch_live_weather_for_slate(sched)
    logger.info(f"HR weather: forecast retrieved for {len(out)}/{len(sched)} games")
    return out


def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Build today's HR predictions.

    Returns DataFrame with columns:
        player, game_pk, away_team, home_team,
        model_prob, market_prob, edge, kelly_pct, odds, stake
    Filtered to edge >= cfg["min_edge"].
    """
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.utils import devig_unilateral

    # 1. Load model + its trained feature list
    booster, features, feature_means = _load_model(cfg)
    calibrator = _load_calibrator(cfg)

    # 2. Build today's live feature rows
    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        logger.warning("HR: no candidate batter-game rows — skipping predictions")
        return pd.DataFrame()

    # 3. Fetch odds (from SGO snapshot)
    player_odds = _fetch_hr_odds(run_date)
    if not player_odds:
        logger.warning("HR: no odds available — skipping predictions")
        return pd.DataFrame()

    # 4. Score all players. Reindex to the model's exact feature list (in
    #    its exact order) so XGBoost doesn't reject the DMatrix. Missing
    #    cols become NaN, then get filled with training means if the meta
    #    provided them.
    missing = [f for f in features if f not in feat_df.columns]
    if missing:
        logger.warning(f"HR: {len(missing)} features missing from input: "
                       f"{missing[:5]}")
    X = feat_df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
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
        preds = booster.predict(dm, iteration_range=(0, ntree))
    else:
        # No best_iteration in meta (e.g. bootstrapped meta) — use all trees.
        preds = booster.predict(dm)
    feat_df = feat_df.copy()
    feat_df["model_prob"] = preds.clip(0.001, 0.95)
    if calibrator is not None:
        try:
            raw = feat_df["model_prob"].values.copy()
            in_range = (raw >= calibrator.X_min_) & (raw <= calibrator.X_max_)
            cal = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            feat_df["model_prob"] = __import__("numpy").clip(cal, 0.001, 0.95)
            logger.info(f"HR: isotonic calibrator applied to {int(in_range.sum())}/{len(raw)} players")
        except Exception as e:
            logger.warning(f"HR calibrator predict failed: {e} -- using raw probs")

    # 5. Match predictions to odds by normalized player name
    # Build a normalized-name index over feat_df once, then look up each odds player.
    feat_df["_name_key"] = feat_df["player_name"].apply(_normalize_name)
    name_to_idx = {n: i for i, n in enumerate(feat_df["_name_key"]) if n}

    # Fuzzy fallback: if exact match fails, try difflib closest match
    # (handles "Yordan Alvarez" vs "Yordan Alvarez Jr.", accent variants, etc.)
    _all_known_keys = [k for k in name_to_idx if k]

    def _resolve_name_idx(raw_name: str) -> int | None:
        key = _normalize_name(raw_name)
        if not key:
            return None
        idx = name_to_idx.get(key)
        if idx is not None:
            return idx
        # Fuzzy fallback — difflib SequenceMatcher, cutoff 0.85
        import difflib
        matches = difflib.get_close_matches(key, _all_known_keys, n=1, cutoff=0.85)
        if matches:
            logger.debug(
                f"HR name fuzzy match: '{raw_name}' → '{matches[0]}' "
                f"(exact='{key}' not in feature table)"
            )
            return name_to_idx[matches[0]]
        return None

    results = []
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _exposure_engine = _make_engine("unused")
    _exposure_game_pks = list(feat_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched_stakes = prefetch_exposure(_exposure_engine, _exposure_game_pks, run_date, system="HR")
    _pending_stakes: dict[int, float] = {}
    for player_name, odds_info in player_odds.items():
        idx = _resolve_name_idx(player_name)
        if idx is None:
            continue
        row = feat_df.iloc[idx]
        model_prob  = float(row["model_prob"])
        odds        = odds_info["odds"]
        market_prob = american_to_implied_prob(odds)

        # Remove vig from the one-sided HR prop market.
        # DK HR props carry ~7% vig; devig_unilateral centralises this assumption.
        # See mlb_core/odds/utils.py for the arithmetic justification.
        # Calibrate HR_VIG_PCT periodically from closing-line analysis (T08/T15).
        HR_VIG_PCT  = 0.07
        fair_prob   = devig_unilateral(market_prob, vig_pct=HR_VIG_PCT)
        edge        = model_prob - fair_prob
        k_pct       = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(_bankroll, int(row["game_pk"]), _prefetched_stakes, _pending_stakes, cap_units=cfg.get("cap_units", 2.0))
        stake       = min(kelly_stake(
            edge, odds,
            bankroll=_bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        ), _cap)

        kelly_triggered = edge >= cfg["min_edge"] and stake > 0
        if kelly_triggered and stake > 0:
            _pending_stakes[int(row.get("game_pk", 0))] = (
                _pending_stakes.get(int(row.get("game_pk", 0)), 0.0) + stake
            )

        # Derive batter team abbrev from row's home/away side flag set upstream
        side = row.get("batter_team_side", "")
        batter_team = (
            odds_info["home_team"] if side == "home"
            else odds_info["away_team"] if side == "away"
            else ""
        )
        results.append({
            "player":          player_name,
            "game_pk":         int(row.get("game_pk", 0)),
            "away_team":       odds_info["away_team"],
            "home_team":       odds_info["home_team"],
            "team":            batter_team,
            "model_prob":      round(model_prob, 4),
            "market_prob":     round(fair_prob, 4),
            "edge":            round(edge, 4),
            "kelly_pct":       round(k_pct, 4),
            "odds":            odds,
            "stake":           stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
        })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"HR: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


# STAGE 5 NOTE: BATTER_TB and BATTER_HITS ship as log-only (stake=0,
# kelly_triggered=False) for the first 2 weeks. The Normal approximation
# anchored to HR model quality is a placeholder -- enable real sizing only
# after post-hoc analysis confirms the proxy edge predicts outcomes.
# BATTER_K dropped -- market has no paired onshore book coverage.

def _score_batter_ou_markets(predictions_df, cfg: dict, run_date: str) -> dict:
    """Score BATTER_TB and BATTER_HITS sub-markets from HR predictions.

    Uses Normal(mu, sigma) approximation where mu is scaled from league
    average by HR model quality proxy. Ships log-only (stake=0) until
    calibration is confirmed after ~100 settled bets per market.
    Returns {system_key: [bet_dict, ...]}
    """
    from scipy.stats import norm as _norm
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_batter_tb_odds, extract_batter_hits_odds
    from mlb_core.odds.utils import american_to_implied_prob, remove_vig

    events = _sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("batter sub-markets: SGO snapshot empty")
        return {"BATTER_TB": [], "BATTER_HITS": []}

    tb_map   = extract_batter_tb_odds(events)
    hits_map = extract_batter_hits_odds(events)

    MARKETS = [
        (tb_map,   1.6, "BATTER_TB"),
        (hits_map, 1.0, "BATTER_HITS"),
    ]

    league_hr_rate = 0.032
    results = {"BATTER_TB": [], "BATTER_HITS": []}

    for _, row in predictions_df.iterrows():
        norm_name = _normalize_name(row["player"])
        for odds_map, stat_scale, sys_key in MARKETS:
            odds_info = odds_map.get(norm_name)
            if odds_info is None:
                continue
            line = odds_info.get("line")
            if line is None:
                continue
            model_hr_rate = float(row["model_prob"])
            quality_mult  = 1.0 + 0.25 * (model_hr_rate - league_hr_rate) / league_hr_rate
            quality_mult  = max(0.6, min(1.6, quality_mult))
            mu    = stat_scale * quality_mult
            sigma = mu * 0.6
            p_over  = float(1.0 - _norm.cdf(line, loc=mu, scale=sigma))
            p_under = float(_norm.cdf(line, loc=mu, scale=sigma))
            mkt_over  = american_to_implied_prob(odds_info["over_odds"])
            mkt_under = american_to_implied_prob(odds_info["under_odds"])
            total = mkt_over + mkt_under
            if not total:
                continue
            fair_over, fair_under = mkt_over / total, mkt_under / total
            edge_over  = p_over  - fair_over
            edge_under = p_under - fair_under
            if edge_over >= edge_under:
                side, edge, fair, odds, model_prob = "OVER", edge_over, fair_over, odds_info["over_odds"], p_over
            else:
                side, edge, fair, odds, model_prob = "UNDER", edge_under, fair_under, odds_info["under_odds"], p_under
            if edge < cfg["min_edge"]:
                continue
            results[sys_key].append({
                "player":          row["player"],
                "game_pk":         int(row["game_pk"]),
                "away_team":       row["away_team"],
                "home_team":       row["home_team"],
                "line":            float(line),
                "side":            side,
                "bet_type":        f"{sys_key}_{side}_{line}",
                "model_prob":      round(model_prob, 4),
                "market_prob":     round(fair, 4),
                "edge":            round(edge, 4),
                "kelly_pct":       0.0,
                "odds":            odds,
                "stake":           0.0,
                "kelly_triggered": False,
                "bookmaker":       odds_info.get("bookmaker"),
            })

    for sys_key, bets in results.items():
        logger.info(f"{sys_key}: {len(bets)} qualifying bets (log-only, stake=0)")
    return results


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"HR run | type={run_type} | date={run_date}")

    from HR_Pro.config_hr import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.rationale import build_rationale
    from mlb_core.notify.discord import post_bets

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
        triggered = bool(row.get("kelly_triggered", True))
        bet_id = tracker.log_bet(
            game_date        = run_date,
            game_pk          = row.get("game_pk"),
            player           = row.get("player"),
            away_team        = resolve_team(row.get("away_team") or "") or row.get("away_team"),
            home_team        = resolve_team(row.get("home_team") or "") or row.get("home_team"),
            bet_type         = "HR",
            model_prob       = row.get("model_prob"),
            market_prob      = row.get("market_prob"),
            edge             = row.get("edge"),
            kelly_pct        = row.get("kelly_pct"),
            odds             = row.get("odds"),
            stake            = row.get("stake"),
            kelly_triggered  = triggered,
            paper            = cfg["PAPER"],
            book             = row.get("bookmaker"),
            morning_odds     = row.get("morning_odds"),
            notes            = build_rationale(row.to_dict() if hasattr(row, 'to_dict') else dict(row), "HR"),
        )
        if bet_id == -1:
            continue
        if triggered:
            bets_logged += 1
            bet_rows.append(row.to_dict())

    # 4. Discord
    post_bets(bet_rows, system="HR", run_date=run_date)

    # 5. Batter sub-markets (log-only, stake=0 until calibration confirmed)
    sub_results = _score_batter_ou_markets(today_df, cfg, run_date)
    from mlb_core.tracking import BetTracker as _BT
    sub_logged = {}
    for sys_key, bets in sub_results.items():
        sub_tracker = _BT(cfg["bet_db"], system=sys_key)
        logged = 0
        sub_rows = []
        for bet in bets:
            ret = sub_tracker.log_bet(
                game_date        = run_date,
                game_pk          = bet["game_pk"],
                player           = bet["player"],
                away_team        = resolve_team(bet.get("away_team") or "") or bet.get("away_team"),
                home_team        = resolve_team(bet.get("home_team") or "") or bet.get("away_team"),
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
            )
            if ret != -1:
                logged += 1
                sub_rows.append(bet)
        sub_logged[sys_key] = logged
        post_bets(sub_rows, system=sys_key, run_date=run_date)

    logger.info(f"HR: {bets_logged} bets logged | " +
                " | ".join(f"{k}: {v} (log-only)" for k, v in sub_logged.items()))
    return {"bets_logged": bets_logged, "sub_logged": sub_logged, "bet_rows": bet_rows}
