"""
runners/run_k.py — K Pro v1 daily runner for Cloud Run.

Odds source: SGO snapshot at Odds/sgo/latest.json (extract_k_odds)
Feature source: K_Pro_System/data/model_features.csv (build_k_features)
Model: xgb_k_v1.json + model_meta_v1.json (retrain_k_v1)

Scoring path mirrors NRFI's runner but with two twists:
  1. The model outputs a Poisson λ (expected K count), not a probability.
     We run a Monte Carlo to convert λ → distribution and read off
     P(Ks > line) for the over and P(Ks < line) for the under. Notebook
     Section 8.
  2. SGO exposes O/U K props only (CONTEXT §5). Ladder markets from the
     notebook (k_3plus, k_4plus, ...) are NOT supported here; the runner
     only emits ou_over / ou_under bets.

run() is called by main.py.
"""
from __future__ import annotations

import json
import logging
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """NFD + ASCII fold + lower + strip. Matches sgo.normalize_name."""
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


# ── Model load (parallels run_nrfi._load_halfinn_model) ──────────────────────

def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict]:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(cfg["gcs_model_k"], Path(tmpdir) / "xgb_k.json")
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_k"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("model_meta_v1.json missing 'features' key")
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(f"K model loaded | features={len(features)} | "
                f"feature_means={len(feature_means)} | "
                f"MAE={meta.get('mae_oos', '?')}")
    return booster, features, feature_means


# ── Build today's feature rows (parallels NRFI's _build_today_feature_rows) ──

def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    """One row per probable starter. Inherits rolling features from the
    pitcher's most-recent snapshot in model_features.csv; overrides
    is_home and weather from today's slate.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv
    from mlb_core.data.lineups import get_today_schedule

    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning(f"K: no games scheduled for {run_date}")
        return pd.DataFrame()
    logger.info(f"K: {len(sched)} games on the {run_date} slate")

    feat_key = cfg["gcs_model_features"] if GCS_BUCKET else cfg["model_features"]
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"K features load failed: {e}")
        return pd.DataFrame()
    if feat_df.empty:
        logger.error("K features CSV is empty")
        return pd.DataFrame()

    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])

    # Latest historical snapshot per pitcher (drop slate rows where starter_ks is NaN
    # since those carry no rolling history beyond what the previous start already had)
    historical = feat_df[feat_df["starter_ks"].notna()]
    pitcher_latest = (
        historical.sort_values("game_date")
                  .groupby("pitcher", as_index=False)
                  .last()
    )
    logger.info(f"K: latest snapshot for {len(pitcher_latest):,} pitchers "
                f"(through {historical['game_date'].max().date()})")

    from mlb_core.data.weather import STADIUMS
    dome_set = {abbr for abbr, info in STADIUMS.items() if info[2] == "dome"}

    rows = []
    for _, g in sched.iterrows():
        for side in ("home", "away"):
            pid = g.get(f"{side}_pitcher_id")
            pname = g.get(f"{side}_pitcher_name")
            if pd.isna(pid):
                continue
            pid = int(pid)
            match = pitcher_latest[pitcher_latest["pitcher"] == pid]
            if match.empty:
                logger.info(f"K: no feature snapshot for {pname} (id={pid})")
                continue
            row = match.iloc[0].to_dict()
            row["game_pk"]       = g["game_pk"]
            row["game_date"]     = pd.Timestamp(run_date)
            row["home_team"]     = g["home_team"]
            row["away_team"]     = g["away_team"]
            row["is_home"]       = 1 if side == "home" else 0
            row["implied_win_pct"] = 0.5
            row["is_dome"]       = int(g["home_team"] in dome_set)
            if row["is_dome"]:
                row["temperature_f"] = 70.0
            row["_pitcher_id"]   = pid
            row["_pitcher_name"] = pname
            row["_pitcher_name_norm"] = _normalize_name(pname)
            rows.append(row)

    if not rows:
        logger.warning("K: 0 probable starters matched feature snapshots")
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    logger.info(f"K: {len(out)} starter-row predictions ready "
                f"({(out['is_home']==1).sum()} home, {(out['is_home']==0).sum()} away)")
    return out


# ── Scoring (parallels run_nrfi._score) ──────────────────────────────────────

def _score_lambda(booster: xgb.Booster, features: list, feature_means: dict,
                  df: pd.DataFrame) -> np.ndarray:
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            m = feature_means.get(col)
            if m is None:
                continue
            X[col] = X[col].fillna(float(m))
    X = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    return booster.predict(dm, iteration_range=(0, ntree) if ntree else None)


# ── Monte Carlo K distribution (notebook Section 8) ──────────────────────────

def _simulate_k(lambda_k: float, avg_ip_L5: float | None,
                n_sims: int, cap: int, seed: int = 42) -> dict:
    """Notebook Section 8 simulate_k_distribution, Poisson branch only.

    Negative Binomial branch is deferred — it depended on a runtime
    overdispersion fit using training-set lambdas that isn't surfaced
    in the meta. If we want NB later, add nb_r/nb_p to the retrain meta
    and re-enable here.

    Early exit IP penalty: if avg_ip_L5 < 5.0, scale lambda down by
    (avg_ip_L5 / 5.0). Lambda floored at 0.5; samples capped at `cap`.
    """
    if avg_ip_L5 is not None and not pd.isna(avg_ip_L5) and avg_ip_L5 < 5.0:
        lambda_k = max(lambda_k * (avg_ip_L5 / 5.0), 0.5)
    lambda_k = max(lambda_k, 0.1)

    rng = np.random.default_rng(seed)
    samples = rng.poisson(lambda_k, size=n_sims)
    samples = np.clip(samples, 0, cap)

    out = {
        "lambda_k": float(lambda_k),
        "mean":     float(np.mean(samples)),
        "median":   float(np.median(samples)),
    }
    for n in range(1, cap + 1):
        out[f"p_{n}plus"] = float(np.mean(samples >= n))
    return out


def _ou_probs(probs: dict, line: float) -> tuple[float, float]:
    """Return (p_over, p_under) for a K line. line=5.5 → over rung = 6+ Ks."""
    import math
    rung = max(1, min(int(math.ceil(line)), 14))
    p_over = probs.get(f"p_{rung}plus", 0.0)
    return p_over, 1.0 - p_over


# ── Match SGO odds to scored pitchers ────────────────────────────────────────

def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.odds import sgo
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct

    booster, features, feature_means = _load_model(cfg)

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        return pd.DataFrame()

    lambdas = _score_lambda(booster, features, feature_means, feat_df)
    feat_df = feat_df.copy()
    feat_df["lambda_k"] = lambdas

    # Pull SGO K odds — keyed by pitcher display name
    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("K: SGO snapshot empty or missing — skipping")
        return pd.DataFrame()
    k_odds_by_name = sgo.extract_k_odds(events)
    if not k_odds_by_name:
        logger.warning("K: 0 pitchers have DK K prices — skipping")
        return pd.DataFrame()

    # Build lookup by normalized name
    odds_by_norm = {_normalize_name(name): {"name": name, **info}
                    for name, info in k_odds_by_name.items()}

    results = []
    for _, row in feat_df.iterrows():
        norm = row["_pitcher_name_norm"]
        odds_info = odds_by_norm.get(norm)
        if odds_info is None:
            continue
        line = odds_info.get("line")
        over_odds = odds_info.get("over_odds")
        under_odds = odds_info.get("under_odds")
        if line is None or over_odds is None or under_odds is None:
            continue

        avg_ip = row.get("avg_ip_L5")
        probs = _simulate_k(
            float(row["lambda_k"]), avg_ip,
            n_sims=cfg["mc_sims"], cap=cfg["mc_cap"],
        )
        p_over, p_under = _ou_probs(probs, float(line))

        mkt_over  = american_to_implied_prob(over_odds)
        mkt_under = american_to_implied_prob(under_odds)
        total = mkt_over + mkt_under
        if not total or total <= 0:
            continue
        fair_over  = mkt_over  / total
        fair_under = mkt_under / total

        edge_over  = p_over  - fair_over
        edge_under = p_under - fair_under

        if edge_over >= edge_under:
            side, edge, fair, odds, model_prob = (
                "OVER", edge_over, fair_over, over_odds, p_over
            )
        else:
            side, edge, fair, odds, model_prob = (
                "UNDER", edge_under, fair_under, under_odds, p_under
            )

        if edge < cfg["min_edge"]:
            continue

        k_pct = kpct(edge, odds, cfg["kelly_fraction"])
        stake = kelly_stake(
            edge, odds,
            bankroll=cfg["BANKROLL"],
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        )

        results.append({
            "player":       row["_pitcher_name"],
            "game_pk":      int(row["game_pk"]),
            "away_team":    row["away_team"],
            "home_team":    row["home_team"],
            "side":         side,
            "line":         float(line),
            "lambda_k":     round(float(row["lambda_k"]), 3),
            "proj_k":       round(probs["mean"], 3),
            "model_prob":   round(model_prob, 4),
            "market_prob":  round(fair, 4),
            "edge":         round(edge, 4),
            "kelly_pct":    round(k_pct, 4),
            "odds":         odds,
            "stake":        stake,
        })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"K: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"K run | type={run_type} | date={run_date}")

    from K_Pro_System.config_k import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets, post_summary

    today_df = _build_predictions(cfg, run_date)
    if today_df.empty:
        logger.info("K: no qualifying bets today")
        post_bets([], system="K", run_date=run_date)
        return {"bets_logged": 0}

    tracker = BetTracker(cfg["bet_db"], system="K")
    bets_logged = 0
    bet_rows = []

    for _, row in today_df.iterrows():
        # bet_type carries side + line so the same pitcher with two different
        # lines (rare; happens around prop reshuffles) doesn't dedup collide.
        bet_type = f"K_{row['side']}_{row['line']}"
        tracker.log_bet(
            game_date   = run_date,
            game_pk     = int(row["game_pk"]),
            player      = row["player"],
            away_team   = row["away_team"],
            home_team   = row["home_team"],
            bet_type    = bet_type,
            model_prob  = row["model_prob"],
            market_prob = row["market_prob"],
            edge        = row["edge"],
            kelly_pct   = row["kelly_pct"],
            odds        = row["odds"],
            stake       = row["stake"],
            paper       = cfg["PAPER"],
        )
        bets_logged += 1
        bet_rows.append(row.to_dict())

    post_bets(bet_rows, system="K", run_date=run_date)
    stats = tracker.summary(season=run_date[:4])
    post_summary(stats, system="K", run_date=run_date)

    logger.info(f"K: {bets_logged} bets logged")
    return {"bets_logged": bets_logged}
