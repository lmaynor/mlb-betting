"""
mlb.analysis.gen_preds -- generalize NRFI's gen_preds to every system.

Scores a system's FULL historical feature table (model_features.csv) with the
SAME production artifacts the live runner uses -- booster + model_meta (feature
list, feature_means, best_iteration, nb_alpha) + calibrator -- and emits one
tidy row per scored entity:

    system  market  game_pk  game_date  player_id  kind  p_model  mu  nb_alpha  realized

- kind="binary" (HR, GAME): p_model = calibrated P(positive event); mu/nb_alpha NaN.
- kind="count"  (K, OUTS, BATTER_HITS, BATTER_TB): mu = calibrated expected count,
  nb_alpha = NegBin dispersion; p_model NaN (it's line-dependent -- the market
  adapter turns (mu, nb_alpha, line) into P(over) via p_over() below).

`realized` is the settled label already present in the feature table (hr_flag,
starter_ks, starter_outs, batter_hits, batter_total_bases, home_win) -- so a
backtest never re-derives outcomes; it joins preds -> odds_history and scores.

This is the model half of the join contract audited in verify_odds_history:
    odds_history.(game_pk, player_id, market, selection, line)  <->  gen_preds

WHY score model_features.csv (not re-fetch features per historical date): the
feature table IS the training table -- it already holds every historical game's
features AND its realized label, keyed by (game_pk, batter|pitcher). Scoring it
once reproduces exactly what the model would have output, with no leakage as long
as the backtest time-splits on game_date.

Run (Cloud Shell / Cloud Run; needs GCS + xgboost + scipy):
    export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
    PYTHONPATH=. python3 -m mlb.analysis.gen_preds --system HR --since 2026-04-01
    PYTHONPATH=. python3 -m mlb.analysis.gen_preds --system K  --inspect   # print cols only
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gen_preds")


# ── per-system spec (from mlb/systems/*/config_*.py + registry) ───────────────

@dataclass(frozen=True)
class Spec:
    market: str          # canonical odds_history market
    kind: str            # "binary" | "count"
    feature_csv: str     # GCS key
    model_key: str       # GCS key (xgb .json)
    meta_key: str        # GCS key (model_meta .json)
    calibrator_key: str  # GCS key (.pkl) -- isotonic on prob (binary) or lambda (count)
    id_col: str | None   # "batter" | "pitcher" | None (game-level)
    label_col: str       # realized outcome column in the feature table


SPECS: dict[str, Spec] = {
    "HR": Spec(
        market="hr_yn", kind="binary",
        feature_csv="HR_Pro/data/model_features.csv",
        model_key="HR_Pro/models/xgb_hr_v6.json",
        meta_key="HR_Pro/models/model_meta_hr_v6.json",
        calibrator_key="HR_Pro/models/isotonic_calibrator_hr_v6.pkl",
        id_col="batter", label_col="hr",
    ),
    "K": Spec(
        market="k_ou", kind="count",
        feature_csv="K_Pro_System/data/model_features.csv",
        model_key="K_Pro_System/models/xgb_k_v1.json",
        meta_key="K_Pro_System/models/model_meta_v1.json",
        calibrator_key="K_Pro_System/models/lambda_calibrator_k_v1.pkl",
        id_col="pitcher", label_col="starter_ks",
    ),
    "OUTS": Spec(
        market="outs_ou", kind="count",
        feature_csv="K_Pro_System/data/model_features.csv",   # shares K feature build
        model_key="OUTS_Pro_System/models/xgb_outs_v1.json",
        meta_key="OUTS_Pro_System/models/model_meta_outs_v1.json",
        calibrator_key="OUTS_Pro_System/models/isotonic_calibrator_outs_v1.pkl",
        id_col="pitcher", label_col="starter_outs",
    ),
    "BATTER_HITS": Spec(
        market="bhits_ou", kind="count",
        feature_csv="BATTER_HITS_System/data/model_features.csv",
        model_key="BATTER_HITS_System/models/xgb_batter_hits_v1.json",
        meta_key="BATTER_HITS_System/models/model_meta_batter_hits_v1.json",
        calibrator_key="BATTER_HITS_System/models/lambda_calibrator_batter_hits_v1.pkl",
        id_col="batter", label_col="batter_hits",
    ),
    "BATTER_TB": Spec(
        market="btb_ou", kind="count",
        feature_csv="BATTER_TB_System/data/model_features.csv",
        model_key="BATTER_TB_System/models/xgb_batter_tb_v1.json",
        meta_key="BATTER_TB_System/models/model_meta_batter_tb_v1.json",
        calibrator_key="BATTER_TB_System/models/lambda_calibrator_batter_tb_v1.pkl",
        id_col="batter", label_col="batter_total_bases",
    ),
    "GAME": Spec(
        market="game_ml", kind="binary",
        feature_csv="GAME_Pro_System/data/model_features.csv",
        model_key="GAME_Pro_System/models/xgb_game_v1.json",
        meta_key="GAME_Pro_System/models/model_meta_game_v1.json",
        calibrator_key="GAME_Pro_System/models/isotonic_calibrator_game_v1.pkl",
        id_col=None, label_col="home_win",
    ),
    # NOTE: NRFI (1IOU/1I) + F5 use the v18 half-inning ENSEMBLE (multi sub-model),
    # not a single booster -- they need run_nrfi._load_v18_ensemble, handled in a
    # follow-up. Everything above is a single-booster load, covered here.
}

DEFAULT_NB_ALPHA = {"K": 0.0, "OUTS": 0.10, "BATTER_HITS": 0.10, "BATTER_TB": 0.15}


# ── NegBin count -> P(over line) (canonical, matches run_batter_hits) ─────────

def p_over(line: float, mu: float, nb_alpha: float) -> float:
    """P(X > line) for NegBin(mu, alpha); degrades to Poisson if alpha<=0.
    Half-point lines (the norm for props) => P(X >= ceil(line)). Mirrors
    run_batter_hits._negbin_p_over exactly so backtest probs == live probs."""
    if mu is None or (isinstance(mu, float) and math.isnan(mu)) or mu <= 0:
        return 0.5
    k = int(math.floor(line))   # P(X > line) = P(X >= k+1) = 1 - P(X <= k)
    if not nb_alpha or nb_alpha <= 0:
        from scipy.stats import poisson
        return float(1.0 - poisson.cdf(k, mu))
    from scipy.stats import nbinom
    n = 1.0 / nb_alpha
    p = n / (n + mu)
    return float(1.0 - nbinom.cdf(k, n, p))


# ── model / calibrator loading (mirrors the runners) ──────────────────────────

def _read_csv(key: str, **kw) -> pd.DataFrame:
    from mlb_core.storage import read_csv
    return read_csv(key, **kw)


def _load_booster_meta(spec: Spec):
    import xgboost as xgb
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmp:
        if GCS_BUCKET:
            local = download_model(spec.model_key, Path(tmp) / "m.json")
            booster.load_model(str(local))
            meta = json.loads(read_bytes(spec.meta_key))
        else:  # local-mode fallback (BASE_DATA mirror of the GCS layout)
            from mlb_core.storage import _get_base_data
            base = _get_base_data()
            booster.load_model(str(base / spec.model_key))
            meta = json.loads((base / spec.meta_key).read_text())
    features = meta.get("features")
    if not features:
        raise RuntimeError(f"{spec.meta_key} missing 'features'")
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    return booster, features, (meta.get("feature_means", {}) or {}), meta


def _load_calibrator(spec: Spec):
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists
    try:
        if GCS_BUCKET and exists(spec.calibrator_key):
            return pickle.loads(read_bytes(spec.calibrator_key))
        from mlb_core.storage import _get_base_data
        p = _get_base_data() / spec.calibrator_key
        if p.exists():
            return pickle.loads(p.read_bytes())
    except Exception as e:  # noqa: BLE001
        log.warning("%s calibrator load failed: %s -- using raw", spec.market, e)
    return None


def _score_raw(booster, features, feature_means, df: pd.DataFrame) -> np.ndarray:
    """Reindex to the model's feature list, fill NaNs with training means, predict.
    Identical to run_k._score_lambda / run_hr scoring."""
    import xgboost as xgb
    X = df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    for col in features:
        m = feature_means.get(col)
        if m is not None:
            X[col] = X[col].fillna(float(m))
    X = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    return booster.predict(dm, iteration_range=(0, ntree)) if ntree else booster.predict(dm)


def _apply_calibrator(cal, raw: np.ndarray) -> np.ndarray:
    """In-range isotonic map (prob->prob or lambda->lambda). Out-of-range values
    pass through untouched -- exactly how the runners guard X_min_/X_max_."""
    if cal is None:
        return raw
    out = raw.copy()
    try:
        in_range = (raw >= cal.X_min_) & (raw <= cal.X_max_)
        if in_range.any():
            out[in_range] = cal.predict(raw[in_range])
        log.info("calibrator applied to %d/%d rows", int(in_range.sum()), len(raw))
    except Exception as e:  # noqa: BLE001
        log.warning("calibrator predict failed: %s -- using raw", e)
        return raw
    return out


# ── main entry ────────────────────────────────────────────────────────────────

def gen_preds(system: str, since: str | None = None, until: str | None = None,
              inspect: bool = False) -> pd.DataFrame:
    if system not in SPECS:
        raise ValueError(f"gen_preds has no spec for {system!r}. "
                         f"Known: {', '.join(SPECS)}")
    spec = SPECS[system]
    df = _read_csv(spec.feature_csv)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    if since:
        df = df[df["game_date"] >= since]
    if until:
        df = df[df["game_date"] <= until]
    df = df.reset_index(drop=True)

    if inspect:
        cols = list(df.columns)
        need = ["game_pk", "game_date", spec.id_col, spec.label_col]
        print(f"[{system}] feature_csv={spec.feature_csv}  rows={len(df)}")
        print(f"  required cols present:")
        for c in need:
            print(f"    {str(c):22} {'OK' if c is None or c in cols else 'MISSING !!'}")
        print(f"  total columns: {len(cols)}")
        return df

    booster, features, feature_means, meta = _load_booster_meta(spec)
    raw = _score_raw(booster, features, feature_means, df)
    cal = _apply_calibrator(_load_calibrator(spec), raw)

    out = pd.DataFrame({
        "system": system,
        "market": spec.market,
        "kind": spec.kind,
        "game_pk": pd.to_numeric(df["game_pk"], errors="coerce").astype("Int64"),
        "game_date": df["game_date"],
        "player_id": (pd.to_numeric(df[spec.id_col], errors="coerce").astype("Int64")
                      if spec.id_col else pd.Series([pd.NA] * len(df), dtype="Int64")),
        "realized": pd.to_numeric(df.get(spec.label_col), errors="coerce"),
    })
    if spec.kind == "binary":
        out["p_model"] = cal
        out["mu"] = np.nan
        out["nb_alpha"] = np.nan
    else:
        nb_alpha = float(meta.get("nb_alpha", DEFAULT_NB_ALPHA.get(system, 0.0)))
        out["p_model"] = np.nan
        out["mu"] = cal
        out["nb_alpha"] = nb_alpha
    log.info("[%s] scored %d rows (%s) -- realized non-null %d",
             system, len(out), spec.kind, int(out["realized"].notna().sum()))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Score a system's historical feature table")
    p.add_argument("--system", required=True, help=", ".join(SPECS))
    p.add_argument("--since", default=None, help="YYYY-MM-DD lower bound on game_date")
    p.add_argument("--until", default=None)
    p.add_argument("--inspect", action="store_true",
                   help="print feature-table columns + label presence, then exit")
    p.add_argument("--out", default=None, help="optional GCS/local key to write preds parquet")
    args = p.parse_args(argv)

    df = gen_preds(args.system, since=args.since, until=args.until, inspect=args.inspect)
    if args.inspect:
        return 0

    r = df[df["realized"].notna()]
    print(f"\ngen_preds[{args.system}] -> {len(df)} rows, {df['game_date'].nunique()} dates")
    if df["kind"].iloc[0] == "binary":
        print(f"  p_model: mean={df['p_model'].mean():.4f}  "
              f"realized base rate={r['realized'].mean():.4f}  (n_settled={len(r)})")
    else:
        print(f"  mu: mean={df['mu'].mean():.3f}  nb_alpha={df['nb_alpha'].iloc[0]:.4f}  "
              f"realized mean={r['realized'].mean():.3f}  (n_settled={len(r)})")
    if args.out:
        from mlb_core import storage
        storage.write_parquet(df, args.out) if hasattr(storage, "write_parquet") \
            else storage.write_csv(df, args.out)
        print(f"  wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
