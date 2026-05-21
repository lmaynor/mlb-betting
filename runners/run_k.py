"""
runners/run_k.py — K Pro v1 daily runner for Cloud Run.

Odds source: SGO snapshot at Odds/sgo/latest.json (extract_k_odds,
             extract_outs_odds)
Feature source: K_Pro_System/data/model_features.csv (build_k_features)
Model: xgb_k_v1.json + model_meta_v1.json (retrain_k_v1)

Scoring path mirrors NRFI's runner but with two twists:
  1. The model outputs a Poisson λ (expected K count), not a probability.
     We run a Monte Carlo to convert λ → distribution and read off
     P(Ks > line) for the over and P(Ks < line) for the under.
  2. SGO exposes O/U K props only. Ladder markets are NOT supported.

Also bets pitcher outs recorded O/U using avg_ip_L5 as a proxy for
expected IP (outs = IP × 3). IP is modelled as Normal(avg_ip_L5, 1.5).

run() is called by main.py.
"""
from __future__ import annotations

import json
import pickle
import logging
import math
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)

_IP_STD      = 1.5
_OUTS_MC_SIMS = 10_000


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


# ── Model load ────────────────────────────────────────────────────────────────

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
    # C07: attach NB dispersion parameter to _simulate_k for use in Monte Carlo.
    _simulate_k._nb_alpha = float(meta.get("nb_alpha", 0.0))
    logger.info(f"K: nb_alpha={_simulate_k._nb_alpha:.4f} loaded from meta")
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(f"K model loaded | features={len(features)} | "
                f"feature_means={len(feature_means)} | "
                f"MAE={meta.get('mae_oos', '?')}")
    return booster, features, feature_means


# ── Feature rows ──────────────────────────────────────────────────────────────


def _load_calibrator(cfg: dict):
    """Load the lambda calibrator if present in GCS. Returns None if absent."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists

    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            return pickle.loads(read_bytes(gcs_key))
        except Exception as e:
            logger.warning(f"K calibrator load failed: {e}")
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"K calibrator load failed: {e}")
    return None

def _load_outs_model(cfg: dict):
    """Load OUTS Pro v1 booster + meta + calibrator.
    Returns (booster, features, feature_means, nb_alpha, calibrator).
    Falls back gracefully to Normal proxy if model not found.
    """
    import json
    import tempfile
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes, exists

    gcs_booster = cfg.get("gcs_outs_model",      "OUTS_Pro_System/models/xgb_outs_v1.json")
    gcs_meta    = cfg.get("gcs_outs_meta",        "OUTS_Pro_System/models/model_meta_outs_v1.json")
    gcs_cal     = cfg.get("gcs_outs_calibrator",  "OUTS_Pro_System/models/isotonic_calibrator_outs_v1.pkl")

    if not GCS_BUCKET or not exists(gcs_booster):
        logger.info("OUTS: trained model not found -- using Normal proxy fallback")
        return None, None, {}, 0.10, None
    try:
        booster = xgb.Booster()
        with tempfile.TemporaryDirectory() as tmpdir:
            local = download_model(gcs_booster, Path(tmpdir) / "xgb_outs.json")
            booster.load_model(str(local))
        meta        = json.loads(read_bytes(gcs_meta))
        features    = meta.get("features", [])
        feat_means  = meta.get("feature_means", {})
        nb_alpha    = float(meta.get("nb_alpha", 0.10))
        booster.best_ntree_limit = meta.get("best_iteration", 0)
        logger.info(f"OUTS model loaded | features={len(features)} MAE={meta.get('mae_oos','?')} nb_alpha={nb_alpha:.4f}")
        cal = None
        if exists(gcs_cal):
            try:
                cal = pickle.loads(read_bytes(gcs_cal))
                logger.info(f"OUTS calibrator loaded | X_min={cal.X_min_:.2f} X_max={cal.X_max_:.2f}")
            except Exception as ce:
                logger.warning(f"OUTS calibrator load failed: {ce}")
        return booster, features, feat_means, nb_alpha, cal
    except Exception as e:
        logger.warning(f"OUTS model load failed: {e} -- using Normal proxy fallback")
        return None, None, {}, 0.10, None


def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv
    from mlb_core.data.lineups import get_today_schedule
    from mlb_core.data.lineups import fetch_il_pitcher_ids
    try:
        _il_ids = fetch_il_pitcher_ids()
    except Exception as _ile:
        logger.warning(f"K: fetch_il_pitcher_ids failed: {_ile} -- failing open")
        _il_ids = set()

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
            pid   = g.get(f"{side}_pitcher_id")
            pname = g.get(f"{side}_pitcher_name")
            if pd.isna(pid):
                continue
            pid = int(pid)
            match = pitcher_latest[pitcher_latest["pitcher"] == pid]
            if match.empty:
                logger.info(f"K: no feature snapshot for {pname} (id={pid})")
                continue
            row = match.iloc[0].to_dict()
            _last_app = match.iloc[0]["game_date"]
            _days_since = (pd.Timestamp(run_date) - _last_app).days
            if _days_since > 7:
                if int(pid) not in _il_ids:
                    logger.info(
                        f"K: allowing {pname} -- {_days_since}d gap but NOT on IL"
                    )
                else:
                    logger.info(
                        f"K: skipping {pname} -- {_days_since}d gap + confirmed on IL"
                    )
                    continue
            row["game_pk"]            = g["game_pk"]
            row["game_date"]          = pd.Timestamp(run_date)
            row["home_team"]          = g["home_team"]
            row["away_team"]          = g["away_team"]
            row["is_home"]            = 1 if side == "home" else 0
            # implied_win_pct removed 2026-05-19 (T02): market-derived feature.
            row["is_dome"]            = int(g["home_team"] in dome_set)
            if row["is_dome"]:
                row["temperature_f"]  = 70.0
            row["_pitcher_id"]        = pid
            row["_pitcher_name"]      = pname
            row["_pitcher_name_norm"] = _normalize_name(pname)
            rows.append(row)

    if not rows:
        logger.warning("K: 0 probable starters matched feature snapshots")
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    logger.info(f"K: {len(out)} starter-row predictions ready "
                f"({(out['is_home']==1).sum()} home, {(out['is_home']==0).sum()} away)")
    return out


# ── Scoring ───────────────────────────────────────────────────────────────────

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
    if ntree:
        return booster.predict(dm, iteration_range=(0, ntree))
    return booster.predict(dm)


# ── Monte Carlo: K distribution ───────────────────────────────────────────────

def _simulate_k(lambda_k: float, avg_ip_L5: float | None,
                n_sims: int, cap: int, seed: int = 42,
                k_per_9_L5: float | None = None) -> dict:
    # C08: use k_per_9_L5 * expected_ip for scaling instead of lambda * (ip/5).
    # Diagnostic showed slope -1.13 with naive linear scaling -- non-trivial bias.
    if avg_ip_L5 is not None and not pd.isna(avg_ip_L5):
        ip = float(avg_ip_L5)
        if k_per_9_L5 is not None and not pd.isna(k_per_9_L5) and k_per_9_L5 > 0:
            # Expected Ks = K/9 rate * expected IP
            lambda_k = max(float(k_per_9_L5) / 9.0 * ip, 0.5)
        elif ip < 5.0:
            # Fallback: only apply penalty for short outings
            lambda_k = max(lambda_k * (ip / 5.0), 0.5)
    lambda_k = max(lambda_k, 0.1)
    rng = np.random.default_rng(seed)
    # C07: use Negative Binomial to capture MLB K over-dispersion.
    # Falls back to Poisson if nb_alpha not available or invalid.
    nb_alpha = getattr(_simulate_k, "_nb_alpha", None)
    if nb_alpha and nb_alpha > 0:
        # NB parameterisation: n=1/alpha, p=1/(1+alpha*lambda_k)
        nb_n = max(1.0 / nb_alpha, 0.1)
        nb_p = 1.0 / (1.0 + nb_alpha * lambda_k)
        nb_p = float(np.clip(nb_p, 1e-6, 1 - 1e-6))
        samples = rng.negative_binomial(nb_n, nb_p, size=n_sims)
    else:
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
    rung = max(1, min(int(math.ceil(line)), 14))
    p_over = probs.get(f"p_{rung}plus", 0.0)
    return p_over, 1.0 - p_over


# ── Monte Carlo: outs distribution ───────────────────────────────────────────

def _simulate_outs(avg_ip: float, n_sims: int = _OUTS_MC_SIMS,
                   ip_std: float = _IP_STD, seed: int = 43) -> dict:
    """Model IP as Normal(avg_ip, ip_std), convert to outs (IP × 3).
    Returns distribution dict including raw samples for line evaluation.
    """
    avg_ip = max(avg_ip, 0.5)
    rng = np.random.default_rng(seed)
    ip_samples = rng.normal(avg_ip, ip_std, size=n_sims)
    ip_samples = np.clip(ip_samples, 0, 9)
    outs_samples = np.round(ip_samples * 3).astype(int)
    return {
        "mean_ip":   float(np.mean(ip_samples)),
        "mean_outs": float(np.mean(outs_samples)),
        "_samples":  outs_samples,
    }


def _simulate_outs_model(lambda_outs: float, nb_alpha: float,
                        n_sims: int = _OUTS_MC_SIMS, seed: int = 43) -> dict:
    """NegBin Monte Carlo from trained OUTS model lambda. Target range 0-27."""
    lambda_outs = max(lambda_outs, 0.5)
    rng = np.random.default_rng(seed)
    if nb_alpha and nb_alpha > 0:
        nb_n = max(1.0 / nb_alpha, 0.1)
        nb_p = float(np.clip(1.0 / (1.0 + nb_alpha * lambda_outs), 1e-6, 1 - 1e-6))
        samples = rng.negative_binomial(nb_n, nb_p, size=n_sims)
    else:
        samples = rng.poisson(lambda_outs, size=n_sims)
    samples = np.clip(samples, 0, 27)
    return {"mean_outs": float(np.mean(samples)), "_samples": samples}


def _outs_ou_probs(dist: dict, line: float) -> tuple[float, float]:
    """P(outs > line) and P(outs < line). Exact hits are a push."""
    samples = dist["_samples"]
    p_over  = float(np.mean(samples > line))
    p_under = float(np.mean(samples < line))
    return p_over, p_under


# ── Match SGO odds ────────────────────────────────────────────────────────────

def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.odds import sgo
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct

    booster, features, feature_means = _load_model(cfg)
    calibrator = _load_calibrator(cfg)

    # E04: load OUTS trained model (falls back to Normal proxy if not found)
    _outs_booster, _outs_features, _outs_feat_means, _outs_nb_alpha, _outs_cal = _load_outs_model(cfg)

    # E10: load morning snapshot for line movement signal
    from mlb_core.odds.line_movement import load_morning_odds
    _morning_odds = load_morning_odds(run_date) if run_date else {}

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        return pd.DataFrame()

    lambdas = _score_lambda(booster, features, feature_means, feat_df)
    feat_df = feat_df.copy()
    feat_df["lambda_k"] = lambdas
    if calibrator is not None:
        try:
            raw = feat_df["lambda_k"].values.copy()
            in_range = (raw >= calibrator.X_min_) & (raw <= calibrator.X_max_)
            cal = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            feat_df["lambda_k"] = cal
            logger.info(f"K: lambda calibrator applied to {int(in_range.sum())}/{len(raw)} pitchers")
        except Exception as e:
            logger.warning(f"K calibrator predict failed: {e} -- using raw lambda")

    # Abort if snapshot is stale — stale lines produce fictitious edge.
    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"aborting run — {_reason}")
        return pd.DataFrame()
    # Sentinel check -- abort if feature build is stale or failed
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "K_Pro_System")
    if not _sok:
        msg = f"K: aborting run -- stale/failed feature build: {_sreason}"
        logger.error(msg)
        from mlb_core.notify.discord import post_error
        post_error(msg, system="K")
        return pd.DataFrame()
    logger.info("K: sentinel ok -- %s", _sreason)
    events = sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("K: SGO snapshot empty or missing — skipping")
        return pd.DataFrame()

    k_odds_by_name    = sgo.extract_k_odds(events)
    outs_odds_by_name = sgo.extract_outs_odds(events)

    if not k_odds_by_name and not outs_odds_by_name:
        logger.warning("K: 0 pitchers have DK K or outs prices — skipping")
        return pd.DataFrame()

    k_by_norm    = {_normalize_name(n): {"name": n, **info}
                    for n, info in k_odds_by_name.items()}
    outs_by_norm = {_normalize_name(n): {"name": n, **info}
                    for n, info in outs_odds_by_name.items()}

    results = []
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _exposure_engine = _make_engine("unused")
    _exposure_game_pks = list(feat_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched_stakes = prefetch_exposure(_exposure_engine, _exposure_game_pks, run_date, system="K")
    _pending_stakes: dict[int, float] = {}
    
    for _, row in feat_df.iterrows():
        norm     = row["_pitcher_name_norm"]
        avg_ip   = row.get("avg_ip_L5")
        avg_ip_f = float(avg_ip) if avg_ip is not None and not pd.isna(avg_ip) else 5.0
        team     = row["home_team"] if row["is_home"] == 1 else row["away_team"]

        # ── Strikeout O/U ──────────────────────────────────────────────────
        k_info = k_by_norm.get(norm)
        if k_info:
            line       = k_info.get("line")
            over_odds  = k_info.get("over_odds")
            under_odds = k_info.get("under_odds")
            if line is not None and over_odds is not None and under_odds is not None:
                k_per_9 = row.get("k_per_9_L5")
                k_per_9_f = float(k_per_9) if k_per_9 is not None and not pd.isna(k_per_9) else None
                probs = _simulate_k(
                    float(row["lambda_k"]), avg_ip,
                    n_sims=cfg["mc_sims"], cap=cfg["mc_cap"],
                    k_per_9_L5=k_per_9_f,
                )
                p_over, p_under = _ou_probs(probs, float(line))
                p_over  = min(max(p_over,  0.001), 0.999)
                p_under = min(max(p_under, 0.001), 0.999)
                mkt_over  = american_to_implied_prob(over_odds)
                mkt_under = american_to_implied_prob(under_odds)
                total = mkt_over + mkt_under
                if total and total > 0:
                    fair_over, fair_under = mkt_over / total, mkt_under / total
                    edge_over  = p_over  - fair_over
                    edge_under = p_under - fair_under
                    if edge_over >= edge_under:
                        side, edge, fair, odds, model_prob = (
                            "OVER", edge_over, fair_over, over_odds, p_over)
                    else:
                        side, edge, fair, odds, model_prob = (
                            "UNDER", edge_under, fair_under, under_odds, p_under)
                    logger.info(
                        f"K pred | {row['_pitcher_name']} | lam={row['lambda_k']:.2f} "
                        f"proj={probs['mean']:.2f} line={line} {side} | "
                        f"model={model_prob:.3f} fair={fair:.3f} edge={edge:+.3f}"
                    )
                    _bankroll, _cap = apply_cap(_bankroll, int(row["game_pk"]), _prefetched_stakes, _pending_stakes, cap_units=cfg.get("cap_units", 2.0))
                    _stake = min(kelly_stake(
                        edge, odds, bankroll=_bankroll,
                        fraction=cfg["kelly_fraction"],
                        min_pct=cfg["min_kelly_pct"],
                        max_pct=cfg["max_kelly_pct"],
                    ), _cap)
                    kelly_triggered = edge >= cfg["min_edge"] and _stake > 0
                    if kelly_triggered and _stake > 0:
                        _pending_stakes[int(row["game_pk"])] = (
                            _pending_stakes.get(int(row["game_pk"]), 0.0) + _stake
                        )
                    results.append({
                        "player":          row["_pitcher_name"],
                        "team":            team,
                        "game_pk":         int(row["game_pk"]),
                        "away_team":       row["away_team"],
                        "home_team":       row["home_team"],
                        "side":            side,
                        "line":            float(line),
                        "lambda_k":        round(float(row["lambda_k"]), 3),
                        "proj_k":          round(probs["mean"], 3),
                        "model_prob":      round(model_prob, 4),
                        "market_prob":     round(fair, 4),
                        "edge":            round(edge, 4),
                        "kelly_pct":       round(kpct(edge, odds, cfg["kelly_fraction"]), 4),
                        "odds":            odds,
                        "stake":           round(_stake, 4) if kelly_triggered else 0.0,
                        "kelly_triggered": kelly_triggered,
                        "market":          "K",
                        "bookmaker":       k_info.get("bookmaker"),
                        "morning_odds":    _morning_odds.get(
                            f"pitching_strikeouts-{row['_pitcher_id']}-game-ou-{side.lower()}"
                        ),
                    })

        # ── Pitcher outs O/U ───────────────────────────────────────────────
        outs_info = outs_by_norm.get(norm)
        if outs_info:
            line       = outs_info.get("line")
            over_odds  = outs_info.get("over_odds")
            under_odds = outs_info.get("under_odds")
            if line is not None and over_odds is not None and under_odds is not None:
                # E04: use trained OUTS model if available, else Normal proxy
                if _outs_booster is not None and _outs_features:
                    _X_outs = pd.DataFrame([row.to_dict()]).reindex(columns=_outs_features)
                    _X_outs = _X_outs.apply(pd.to_numeric, errors="coerce")
                    for _fc in _outs_features:
                        _fmv = _outs_feat_means.get(_fc)
                        if _fmv is not None:
                            _X_outs[_fc] = _X_outs[_fc].fillna(float(_fmv))
                    _dm_outs = xgb.DMatrix(_X_outs, feature_names=_outs_features)
                    _ntree_outs = getattr(_outs_booster, "best_ntree_limit", 0)
                    _lam_outs = float(_outs_booster.predict(
                        _dm_outs,
                        iteration_range=(0, _ntree_outs) if _ntree_outs else None
                    )[0])
                    if _outs_cal is not None:
                        try:
                            _lam_outs = float(np.clip(
                                _outs_cal.predict([_lam_outs])[0],
                                _outs_cal.X_min_, _outs_cal.X_max_,
                            ))
                        except Exception:
                            pass
                    dist = _simulate_outs_model(_lam_outs, _outs_nb_alpha)
                else:
                    dist = _simulate_outs(avg_ip_f)
                p_over, p_under = _outs_ou_probs(dist, float(line))
                p_over  = min(max(p_over,  0.001), 0.999)
                p_under = min(max(p_under, 0.001), 0.999)
                # Skip if distribution has no coverage near the line
                if p_over + p_under < 0.05:
                    continue
                mkt_over  = american_to_implied_prob(over_odds)
                mkt_under = american_to_implied_prob(under_odds)
                total = mkt_over + mkt_under
                if total and total > 0:
                    fair_over, fair_under = mkt_over / total, mkt_under / total
                    edge_over  = p_over  - fair_over
                    edge_under = p_under - fair_under
                    if edge_over >= edge_under:
                        side, edge, fair, odds, model_prob = (
                            "OVER", edge_over, fair_over, over_odds, p_over)
                    else:
                        side, edge, fair, odds, model_prob = (
                            "UNDER", edge_under, fair_under, under_odds, p_under)
                    _bankroll, _cap = apply_cap(_bankroll, int(row["game_pk"]), _prefetched_stakes, _pending_stakes, cap_units=cfg.get("cap_units", 2.0))
                    _stake = min(kelly_stake(
                        edge, odds, bankroll=_bankroll,
                        fraction=cfg["kelly_fraction"],
                        min_pct=cfg["min_kelly_pct"],
                        max_pct=cfg["max_kelly_pct"],
                    ), _cap)
                    kelly_triggered = edge >= cfg["min_edge"] and _stake > 0
                    if kelly_triggered and _stake > 0:
                        _pending_stakes[int(row["game_pk"])] = (
                            _pending_stakes.get(int(row["game_pk"]), 0.0) + _stake
                        )
                    results.append({
                        "player":          row["_pitcher_name"],
                        "team":            team,
                        "game_pk":         int(row["game_pk"]),
                        "away_team":       row["away_team"],
                        "home_team":       row["home_team"],
                        "side":            side,
                        "line":            float(line),
                        "lambda_k":        round(float(row["lambda_k"]), 3),
                        "proj_k":          round(dist["mean_outs"] / 3, 3),
                        "model_prob":      round(model_prob, 4),
                        "market_prob":     round(fair, 4),
                        "edge":            round(edge, 4),
                        "kelly_pct":       round(kpct(edge, odds, cfg["kelly_fraction"]), 4),
                        "odds":            odds,
                        "stake":           round(_stake, 4) if kelly_triggered else 0.0,
                        "kelly_triggered": kelly_triggered,
                        "market":          "OUTS",
                        "bookmaker":       outs_info.get("bookmaker"),
                        "morning_odds":    _morning_odds.get(
                            f"pitching_outs-{row['_pitcher_id']}-game-ou-{side.lower()}"
                        ),
                    })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    k_c    = (out["market"] == "K").sum()
    outs_c = (out["market"] == "OUTS").sum()
    logger.info(f"K: {len(out)} qualifying bets — "
                f"{k_c} strikeout, {outs_c} outs (edge >= {cfg['min_edge']:.0%})")
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

# STAGE 5 NOTE: PITCHER_ER ships log-only (stake=0, kelly_triggered=False)
# for the first 2 weeks. The Gamma approximation via lambda_k proxy is a
# placeholder -- enable real sizing only after post-hoc analysis confirms
# edge predicts outcomes.

_LEAGUE_ER_PER_9 = 4.5   # 2026 season -- derived from median DK ER line (2.5) over median starter IP (~5.0)

def _score_pitcher_er(predictions_df, cfg: dict, run_date: str) -> list:
    """Score PITCHER_ER sub-market from K model predictions.

    Uses Gamma(shape=mu_er, scale=1) approximation where mu_er is derived
    from lambda_k (quality proxy) and avg_ip_L5 (durability proxy).
    Ships log-only until calibration is confirmed.
    """
    import math
    import unicodedata
    from scipy.stats import gamma as _gamma
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_pitcher_er_odds
    from mlb_core.odds.utils import american_to_implied_prob, remove_vig

    def _norm_name(s):
        if not isinstance(s, str): return ""
        import unicodedata as _ud
        n = _ud.normalize("NFD", s)
        n = "".join(c for c in n if _ud.category(c) != "Mn")
        return n.encode("ascii", "ignore").decode().lower().strip()

    events = _sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("PITCHER_ER: SGO snapshot empty")
        return []

    er_map  = extract_pitcher_er_odds(events)
    er_norm = {_norm_name(k): v for k, v in er_map.items()}

    results = []
    for _, row in predictions_df.iterrows():
        norm_name = _norm_name(str(row.get("player", "")))
        odds_info = er_norm.get(norm_name)
        if odds_info is None:
            continue
        line = odds_info.get("line")
        if line is None:
            continue

        avg_ip = row.get("avg_ip_L5")
        if avg_ip is None or (isinstance(avg_ip, float) and math.isnan(avg_ip)):
            avg_ip = 5.0
        avg_ip = float(max(1.0, min(9.0, avg_ip)))

        lambda_k = float(row.get("lambda_k", 5.5))
        lambda_k = max(0.5, lambda_k)
        quality_mult = (8.5 / 9.0) / (lambda_k / max(avg_ip, 1.0))
        quality_mult = max(0.4, min(2.5, quality_mult))
        mu_er = (avg_ip / 9.0) * _LEAGUE_ER_PER_9 * quality_mult
        mu_er = max(0.1, mu_er)
        shape = max(0.5, mu_er)

        p_over  = float(1.0 - _gamma.cdf(line, a=shape, scale=1.0))
        p_under = float(_gamma.cdf(line, a=shape, scale=1.0))

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

        results.append({
            "player":          row["player"],
            "game_pk":         int(row["game_pk"]),
            "away_team":       row["away_team"],
            "home_team":       row["home_team"],
            "line":            float(line),
            "side":            side,
            "bet_type":        f"PITCHER_ER_{side}_{line}",
            "model_prob":      round(model_prob, 4),
            "market_prob":     round(fair, 4),
            "edge":            round(edge, 4),
            "kelly_pct":       0.0,
            "odds":            odds,
            "stake":           0.0,
            "kelly_triggered": False,
            "bookmaker":       odds_info.get("bookmaker"),
            "lambda_k":        round(lambda_k, 3),
            "mu_er":           round(mu_er, 3),
        })

    logger.info(f"PITCHER_ER: {len(results)} qualifying bets (log-only, stake=0)")
    return results


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"K run | type={run_type} | date={run_date}")

    from K_Pro_System.config_k import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)
    if today_df.empty:
        logger.info("K: no qualifying bets today")
        post_bets([], system="K", run_date=run_date)
        return {"bets_logged": 0}

    tracker = BetTracker(cfg["bet_db"], system="K")
    outs_tracker = BetTracker(cfg["bet_db"], system="OUTS")
    bets_logged = 0
    bet_rows = []

    for _, row in today_df.iterrows():
        market   = row.get("market", "K")
        bet_type = (f"K_{row['side']}_{row['line']}"
                    if market == "K"
                    else f"OUTS_{row['side']}_{row['line']}")
        triggered = bool(row.get("kelly_triggered", True))
        active_tracker = outs_tracker if market == "OUTS" else tracker
        bet_id = active_tracker.log_bet(
            game_date        = run_date,
            game_pk          = int(row["game_pk"]),
            player           = row["player"],
            away_team        = row["away_team"],
            home_team        = row["home_team"],
            bet_type         = bet_type,
            model_prob       = row["model_prob"],
            market_prob      = row["market_prob"],
            edge             = row["edge"],
            kelly_pct        = row["kelly_pct"],
            odds             = row["odds"],
            stake            = row["stake"],
            kelly_triggered  = triggered,
            paper            = cfg["PAPER"],
            lambda_k         = row.get("lambda_k"),
            proj_k           = row.get("proj_k"),
            book             = row.get("bookmaker"),
            morning_odds     = row.get("morning_odds"),
        )
        if bet_id == -1:
            continue
        if triggered:
            bets_logged += 1
            bet_rows.append(row.to_dict())

    k_rows    = [b for b in bet_rows if b.get("market", "K") == "K"]
    outs_rows = [b for b in bet_rows if b.get("market") == "OUTS"]
    post_bets(k_rows,    system="K",    run_date=run_date)
    post_bets(outs_rows, system="OUTS", run_date=run_date)

    # PITCHER_ER sub-market (log-only, stake=0 until calibration confirmed)
    er_bets    = _score_pitcher_er(today_df, cfg, run_date)
    er_tracker = BetTracker(cfg["bet_db"], system="PITCHER_ER")
    er_logged  = 0
    er_rows    = []
    for bet in er_bets:
        ret = er_tracker.log_bet(
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
        )
        if ret != -1:
            er_logged += 1
            er_rows.append(bet)
    post_bets(er_rows, system="PITCHER_ER", run_date=run_date)

    logger.info(f"K: {bets_logged} bets logged | PITCHER_ER: {er_logged} (log-only)")
    return {"bets_logged": bets_logged, "er_logged": er_logged, "bet_rows": bet_rows}
