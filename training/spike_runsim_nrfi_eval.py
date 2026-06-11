"""
training/spike_runsim_nrfi_eval.py -- First-inning run-distribution spike (EVAL).

Evaluates the count:poisson half-inning model (spike_runsim_nrfi_v1.py) against
the production v18 binary ensemble on the SAME out-of-sample test slice, and
prints a PASS/FAIL verdict against the scope's statistical gate.

Decision gate (scope s1 -- ALL must hold on the OOS test slice for the NEW model):
  1. OOS AUC for P(YRFI) >= 0.55
  2. |hit_rate - mean_pred| < 0.05 in each of 5 probability bins
  3. Brier skill score > 0 vs the YRFI base-rate naive model
  4. Reliability curve monotone and near-diagonal (data emitted for plotting)

Composition (both models): two half-innings per game.
  P(NRFI) = P(top scores 0) * P(bot scores 0)   [independence assumption]
  P(YRFI) = 1 - P(NRFI)
  New model:  P(half=0) = NegBin P(0) = (1/(1+alpha*lambda))^(1/alpha)
  v18:        P(half=0) = 1 - p_half_yrfi  (binary ensemble per-half prob)

Run (needs GCS):
  PYTHONPATH=. python3 -m training.spike_runsim_nrfi_eval
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import xgboost as xgb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EXP_PREFIX      = "NRFI_Pro_System/experimental/runsim_v1"
EXP_TRAIN_FRAME = f"{EXP_PREFIX}/train_frame.csv"
EXP_BOOSTER     = f"{EXP_PREFIX}/xgb_runsim_nrfi_v1.json"
EXP_META        = f"{EXP_PREFIX}/model_meta_runsim_nrfi_v1.json"
EXP_REPORT      = f"{EXP_PREFIX}/eval_report.json"

TARGET = "runs_against_i1"

# Gate thresholds (scope s1)
GATE_AUC_MIN       = 0.55
GATE_CAL_BIN_TOL   = 0.05
GATE_BRIER_SKILL   = 0.0


# --- metrics (no sklearn) -----------------------------------------------------

def _auc(probs, outcomes):
    pos = [p for p, o in zip(probs, outcomes) if o == 1]
    neg = [p for p, o in zip(probs, outcomes) if o == 0]
    if not pos or not neg:
        return None
    conc = sum(1 for p in pos for n in neg if p > n)
    tied = sum(1 for p in pos for n in neg if p == n)
    return (conc + 0.5 * tied) / (len(pos) * len(neg))


def _brier(probs, outcomes):
    return float(np.mean((np.array(probs) - np.array(outcomes)) ** 2)) if len(probs) else None


def _brier_skill(probs, outcomes):
    if not len(probs):
        return None
    base = float(np.mean(outcomes))
    br_naive = float(np.mean((base - np.array(outcomes)) ** 2))
    if br_naive <= 0:
        return None
    return 1.0 - _brier(probs, outcomes) / br_naive


def _reliability(probs, outcomes, n_bins=5):
    """Equal-width bins over [0,1]. Returns list of (bin_lo, bin_hi, n, mean_pred, hit_rate)."""
    probs = np.array(probs)
    outcomes = np.array(outcomes)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs < hi) if i < n_bins - 1 else (probs >= lo) & (probs <= hi)
        k = int(mask.sum())
        if k == 0:
            rows.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": 0,
                         "mean_pred": None, "hit_rate": None, "cal_err": None})
            continue
        mp = float(probs[mask].mean())
        hr = float(outcomes[mask].mean())
        rows.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": k,
                     "mean_pred": round(mp, 4), "hit_rate": round(hr, 4),
                     "cal_err": round(hr - mp, 4)})
    return rows


def _p_zero_negbin(mu, alpha):
    """P(X = 0) for NegBin(mu, alpha). Poisson limit exp(-mu) as alpha->0."""
    mu = max(float(mu), 0.0)
    if alpha <= 0:
        return float(np.exp(-mu))
    return float((1.0 / (1.0 + alpha * mu)) ** (1.0 / alpha))


# --- per-game composition -----------------------------------------------------

def _compose_game_level(test_df: pd.DataFrame, p_half_zero: np.ndarray) -> pd.DataFrame:
    """Group half-inning rows by game_pk, compose game-level P(YRFI) and target.

    p_half_zero[i] = P(half i scores 0). Requires exactly 2 rows per game.
    Returns DataFrame: game_pk, p_yrfi, yrfi_game.
    """
    tmp = test_df[["game_pk", TARGET]].copy()
    tmp["p0"] = p_half_zero
    out = []
    for gp, g in tmp.groupby("game_pk"):
        if len(g) != 2:
            continue  # need both halves for game-level composition
        p_nrfi = float(g["p0"].iloc[0] * g["p0"].iloc[1])
        p_yrfi = 1.0 - p_nrfi
        yrfi_game = 1 if float(g[TARGET].sum()) > 0 else 0
        out.append({"game_pk": gp, "p_yrfi": p_yrfi, "yrfi_game": yrfi_game})
    return pd.DataFrame(out)


def _evaluate(label: str, comp: pd.DataFrame) -> dict:
    probs = comp["p_yrfi"].tolist()
    outs  = comp["yrfi_game"].tolist()
    auc   = _auc(probs, outs)
    rel   = _reliability(probs, outs, n_bins=5)
    max_bin_cal_err = max((abs(r["cal_err"]) for r in rel if r["cal_err"] is not None),
                          default=None)
    # monotonicity of hit_rate across non-empty bins
    hrs = [r["hit_rate"] for r in rel if r["hit_rate"] is not None]
    monotone = all(hrs[i] <= hrs[i + 1] + 1e-9 for i in range(len(hrs) - 1)) if len(hrs) > 1 else None
    return {
        "label":        label,
        "n_games":      len(comp),
        "base_rate":    round(float(np.mean(outs)), 4) if outs else None,
        "auc":          round(auc, 4) if auc is not None else None,
        "brier":        round(_brier(probs, outs), 4) if probs else None,
        "brier_skill":  round(_brier_skill(probs, outs), 4) if probs else None,
        "max_bin_cal_err": round(max_bin_cal_err, 4) if max_bin_cal_err is not None else None,
        "reliability_monotone": monotone,
        "reliability":  rel,
    }


def run() -> dict:
    from mlb_core.storage import read_csv, read_bytes, write_bytes, exists

    if not exists(EXP_TRAIN_FRAME) or not exists(EXP_BOOSTER):
        return {"status": "error",
                "error": "spike artifacts missing -- run spike_runsim_nrfi_v1 first"}

    meta = json.loads(read_bytes(EXP_META))
    features = meta["features"]
    nb_alpha = float(meta["nb_alpha"])
    best_iter = int(meta["best_iteration"])
    val_idx  = int(meta["split"]["val_idx"])
    test_idx = int(meta["split"]["test_idx"])

    df = read_csv(EXP_TRAIN_FRAME, low_memory=False)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values(["game_date", "game_pk", "pitcher_is_home"]).reset_index(drop=True)
    test_df = df.iloc[test_idx:].copy()
    logger.info("test slice: %s rows (from %s)", f"{len(test_df):,}",
                test_df["game_date"].min().date())

    # ---- NEW count model: lambda per half -> P(half=0) ----
    import tempfile, os
    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as td:
        local = os.path.join(td, "b.json")
        with open(local, "wb") as fh:
            fh.write(read_bytes(EXP_BOOSTER))
        booster.load_model(local)
    X_te = test_df[features].apply(pd.to_numeric, errors="coerce")
    dm_te = xgb.DMatrix(X_te, feature_names=features)
    lam = booster.predict(dm_te, iteration_range=(0, best_iter))
    new_p0 = np.array([_p_zero_negbin(m, nb_alpha) for m in lam])
    comp_new = _compose_game_level(test_df, new_p0)
    res_new = _evaluate("runsim_count_v1", comp_new)

    # ---- v18 baseline: per-half binary ensemble -> P(half=0)=1-p_yrfi ----
    res_v18 = {"label": "v18_binary_baseline", "error": None}
    try:
        from runners.run_nrfi import (
            _load_v18_ensemble, _score_v18, _load_calibrator_by_key, _V18_CALIBRATOR_KEY,
        )
        sub_boosters, v18_meta = _load_v18_ensemble()
        if sub_boosters is None:
            raise RuntimeError("v18 ensemble failed to load")
        p_half_yrfi = _score_v18(sub_boosters, v18_meta, test_df)
        v18_p0 = 1.0 - np.asarray(p_half_yrfi)
        comp_v18 = _compose_game_level(test_df, v18_p0)
        # Apply production isotonic calibrator at game level (in-range), clip 0.05-0.95.
        calib = _load_calibrator_by_key(_V18_CALIBRATOR_KEY)
        if calib is not None:
            raw = comp_v18["p_yrfi"].values.copy()
            in_rng = (raw >= calib.X_min_) & (raw <= calib.X_max_)
            cal = raw.copy()
            if in_rng.any():
                cal[in_rng] = calib.predict(raw[in_rng])
            comp_v18["p_yrfi"] = np.clip(cal, 0.05, 0.95)
            logger.info("v18 calibrator applied to %d/%d games", int(in_rng.sum()), len(raw))
        res_v18 = _evaluate("v18_binary_baseline", comp_v18)
    except Exception as e:
        logger.warning("v18 baseline eval failed: %s", e)
        res_v18 = {"label": "v18_binary_baseline", "error": str(e)}

    # ---- Gate verdict (NEW model only) ----
    gate = {
        "auc_pass":   res_new["auc"] is not None and res_new["auc"] >= GATE_AUC_MIN,
        "cal_pass":   res_new["max_bin_cal_err"] is not None
                      and res_new["max_bin_cal_err"] < GATE_CAL_BIN_TOL,
        "brier_pass": res_new["brier_skill"] is not None
                      and res_new["brier_skill"] > GATE_BRIER_SKILL,
        "monotone_pass": res_new["reliability_monotone"] is True,
    }
    gate["PASS"] = all(gate[k] for k in ("auc_pass", "cal_pass", "brier_pass", "monotone_pass"))

    report = {"status": "ok", "gate": gate, "new_model": res_new, "v18_baseline": res_v18}
    write_bytes(json.dumps(report, indent=2).encode(), EXP_REPORT)

    # ---- Human-readable summary ----
    logger.info("=" * 70)
    logger.info("SPIKE EVAL | test games (new)=%s  (v18)=%s",
                res_new["n_games"], res_v18.get("n_games", "n/a"))
    logger.info("%-22s %8s %8s %12s %14s", "model", "AUC", "Brier", "BrierSkill", "maxBinCalErr")
    for r in (res_new, res_v18):
        if r.get("error"):
            logger.info("%-22s  ERROR: %s", r["label"], r["error"])
            continue
        logger.info("%-22s %8s %8s %12s %14s", r["label"],
                    r["auc"], r["brier"], r["brier_skill"], r["max_bin_cal_err"])
    logger.info("-" * 70)
    logger.info("GATE: AUC>=%.2f:%s  cal<%.2f:%s  brier_skill>0:%s  monotone:%s  => %s",
                GATE_AUC_MIN, gate["auc_pass"], GATE_CAL_BIN_TOL, gate["cal_pass"],
                gate["brier_pass"], gate["monotone_pass"],
                "PASS" if gate["PASS"] else "FAIL")
    logger.info("reliability (new model):")
    for b in res_new["reliability"]:
        logger.info("  [%.1f-%.1f) n=%s mean_pred=%s hit_rate=%s cal_err=%s",
                    b["lo"], b["hi"], b["n"], b["mean_pred"], b["hit_rate"], b["cal_err"])
    logger.info("report written -> %s", EXP_REPORT)
    logger.info("=" * 70)

    return report


def main():
    import sys
    result = run()
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
