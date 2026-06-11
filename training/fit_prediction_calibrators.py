"""
training/fit_prediction_calibrators.py -- fit per-system prediction calibrators.

Fits an isotonic regression mapping model_prob -> realized win outcome for each
system, using ALL settled predictions in the bets table (placed AND unplaced --
settle_bets settles every row regardless of kelly_triggered, so the calibration
sample spans the full predicted-probability range, not just placed bets).

Writes one calibrator per system to GCS:
  Calibration/{system}_prediction_calibrator.pkl   (sklearn IsotonicRegression)
  Calibration/{system}_prediction_calibrator_meta.json

Runners load these via mlb_core.risk.calibration.apply() and calibrate model_prob
BEFORE computing edge. This corrects systematic overconfidence (the /edge-analysis
finding: model says ~0.77 in the >=20% gap bucket, wins ~0.46) against realized
outcomes -- NOT against the market, so genuine gaps survive.

Run (needs GCS + Cloud SQL):
  gcloud run jobs execute mlb-fit-calibrators --region=us-central1 --wait
"""
from __future__ import annotations

import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CALIBRATION_PREFIX = "Calibration"
# Minimum settled predictions (with both outcomes present) before we fit. Below
# this, leave the system uncalibrated (runner passthrough) rather than overfit.
MIN_FIT_N = int(__import__("os").getenv("CALIB_MIN_FIT_N", "75"))


def _fit_one(system: str, probs, outcomes):
    """Fit IsotonicRegression(prob -> outcome). Returns (calibrator, meta) or (None, reason)."""
    import numpy as np
    from sklearn.isotonic import IsotonicRegression

    n = len(probs)
    if n < MIN_FIT_N:
        return None, {"system": system, "skipped": f"n={n} < {MIN_FIT_N}"}
    if len(set(outcomes)) < 2:
        return None, {"system": system, "skipped": "only one outcome class"}

    x = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(x, y)

    # Reliability summary (pre vs post) for the meta file.
    bins = np.linspace(0.0, 1.0, 6)
    rel = []
    pred = iso.predict(x)
    for i in range(5):
        lo, hi = bins[i], bins[i + 1]
        m = (x >= lo) & (x < hi) if i < 4 else (x >= lo) & (x <= hi)
        if m.sum() == 0:
            continue
        rel.append({
            "lo": round(float(lo), 2), "hi": round(float(hi), 2),
            "n": int(m.sum()),
            "raw_mean": round(float(x[m].mean()), 4),
            "hit_rate": round(float(y[m].mean()), 4),
            "calibrated_mean": round(float(pred[m].mean()), 4),
        })
    meta = {"system": system, "n": n, "base_rate": round(float(y.mean()), 4),
            "min_fit_n": MIN_FIT_N, "reliability": rel}
    return iso, meta


def run() -> dict:
    import pickle
    import pandas as pd
    from sqlalchemy import text
    from mlb_core.tracking.bet_tracker import _make_engine
    from mlb_core.storage import write_bytes

    engine = _make_engine("unused")
    with engine.connect() as conn:
        df = pd.read_sql(text(
            "SELECT system, model_prob, result FROM bets "
            "WHERE result IN ('win','loss') AND model_prob IS NOT NULL"
        ), conn)
    logger.info("loaded %s settled predictions across %s systems",
                f"{len(df):,}", df["system"].nunique())

    summary = {}
    for system, g in df.groupby("system"):
        probs    = g["model_prob"].astype(float).tolist()
        outcomes = [1 if r == "win" else 0 for r in g["result"]]
        iso, meta = _fit_one(system, probs, outcomes)
        if iso is None:
            logger.info("skip %s: %s", system, meta.get("skipped"))
            summary[system] = meta
            continue
        write_bytes(pickle.dumps(iso), f"{CALIBRATION_PREFIX}/{system}_prediction_calibrator.pkl")
        write_bytes(json.dumps(meta, indent=2).encode(),
                    f"{CALIBRATION_PREFIX}/{system}_prediction_calibrator_meta.json")
        logger.info("fit %s | n=%d base_rate=%.3f", system, meta["n"], meta["base_rate"])
        summary[system] = {"fit": True, "n": meta["n"]}

    return {"status": "ok", "systems": summary}


def main():
    import sys
    result = run()
    logger.info("RESULT: %s", json.dumps(result))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
