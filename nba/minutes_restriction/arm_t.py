"""Arm T (text) harness for the minutes-restriction program.

Owns: loaders for the Lane 2 case frame / Arm S predictions / designation
frame / Lane 3 prose corpus, decision-time blurb slicing, dev-season
calibration, band-mapped minute predictions, and the S+T combiner.

Integration contract with Lane 2 (parallel lane; its code is NOT imported):

- Cases arrive as a DataFrame with columns [case_id, player, team,
  game_date, tip_ts, decision_report_ts, designation, games_missed,
  missed_band, baseline_minutes, realized_minutes, realized_ratio,
  case_class, is_game_two]. An optional ``reason`` column (from the official
  report frame) is used when present.
- Arm S predictions arrive as [case_id, pred_ratio] OR [case_id,
  pred_minutes]; either is accepted (see ``normalize_arm_s``).
- The prose corpus is JSONL in Lane 3's record shape: player, published_ts,
  headline, body.

Joins are on case_id (predictions) and player + game_date / decision time
(prose). All loaders take explicit paths.

Honesty properties:

- Blurb cutoff: only blurbs with ``published_ts <= decision_report_ts`` are
  inputs to the classifier (a blurb exactly at decision time is included).
  The corpus is never cleaned: superseded/contradictory updates pass through
  raw, in timestamp order.
- Bands on realized minutes ratio (realized / baseline): out (did not play),
  heavy_restriction (< 0.7), light_restriction (0.7-0.9), full_workload
  (>= 0.9).
- Calibration is fit on dev seasons only (2021-22 -> 2023-24 in the
  pre-registered design) and applied frozen to test seasons.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

from .decisions_client import BANDS, BandPrediction, DecisionsBandClassifier

# Documented priors for injury returns after >= 4 missed games (spike
# section 4, Arm S dev-fit band base rates). These initialize the calibration
# ONLY until a fitted calibration exists; ``fit_calibration`` replaces them.
PRIOR_BASE_RATES: dict[str, float] = {
    "out": 0.0,  # the spike's three-band split conditions on dressing
    "heavy_restriction": 0.27,
    "light_restriction": 0.38,
    "full_workload": 0.36,
}
# Prior per-band mean realized ratios (band-representative values; replaced
# by the fitted calibration when data exists).
PRIOR_MEAN_RATIOS: dict[str, float] = {
    "out": 0.0,
    "heavy_restriction": 0.50,
    "light_restriction": 0.80,
    "full_workload": 1.00,
}

CASE_COLUMNS = [
    "case_id", "player", "team", "game_date", "tip_ts", "decision_report_ts",
    "designation", "games_missed", "missed_band", "baseline_minutes",
    "realized_minutes", "realized_ratio", "case_class", "is_game_two",
]


def realized_band(realized_ratio: float, realized_minutes: float) -> str:
    """Band a realized outcome. Did-not-play (0 minutes) is ``out``."""
    if realized_minutes is None or float(realized_minutes) <= 0:
        return "out"
    ratio = float(realized_ratio)
    if ratio < 0.7:
        return "heavy_restriction"
    if ratio < 0.9:
        return "light_restriction"
    return "full_workload"


# ---------------------------------------------------------------------------
# Loaders (explicit paths; tests feed synthetic files)
# ---------------------------------------------------------------------------

def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    if p.suffix in (".csv", ".tsv"):
        return pd.read_csv(p, sep="\t" if p.suffix == ".tsv" else ",")
    raise ValueError(f"unsupported table format: {p.suffix} ({p})")


def load_cases(path: str | Path) -> pd.DataFrame:
    """Load the Lane 2 case frame (parquet or CSV)."""
    df = _read_table(path)
    missing = [c for c in CASE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"cases file {path} missing columns: {missing}")
    df = df.copy()
    df["decision_report_ts"] = pd.to_datetime(df["decision_report_ts"], utc=True)
    df["tip_ts"] = pd.to_datetime(df["tip_ts"], utc=True)
    return df


def load_arm_s_predictions(path: str | Path) -> pd.DataFrame:
    """Load Arm S predictions: [case_id, pred_ratio] or [case_id, pred_minutes]."""
    df = _read_table(path)
    if "case_id" not in df.columns:
        raise ValueError(f"Arm S predictions {path} missing case_id")
    if "pred_ratio" not in df.columns and "pred_minutes" not in df.columns:
        raise ValueError(
            f"Arm S predictions {path} must carry pred_ratio or pred_minutes"
        )
    return df


def load_designations(path: str | Path) -> pd.DataFrame:
    """Load the official-report designation frame (parquet or CSV).

    Expected at minimum: player, game_date, report_ts, designation; a
    ``reason`` column is carried through when present.
    """
    df = _read_table(path)
    for col in ("player", "game_date", "designation"):
        if col not in df.columns:
            raise ValueError(f"designation frame {path} missing column {col}")
    return df


def load_prose_corpus(path: str | Path) -> pd.DataFrame:
    """Load the Lane 3 prose corpus JSONL: player, published_ts, headline, body."""
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    df = pd.DataFrame.from_records(records)
    for col in ("player", "published_ts", "headline", "body"):
        if col not in df.columns:
            raise ValueError(f"prose corpus {path} missing field {col}")
    df = df.copy()
    df["published_ts"] = pd.to_datetime(df["published_ts"], utc=True)
    return df.sort_values("published_ts").reset_index(drop=True)


def normalize_arm_s(arm_s: pd.DataFrame, cases: pd.DataFrame) -> pd.DataFrame:
    """Return [case_id, s_pred_minutes], converting pred_ratio via baseline."""
    out = arm_s[["case_id"]].copy()
    if "pred_minutes" in arm_s.columns and arm_s["pred_minutes"].notna().any():
        out["s_pred_minutes"] = arm_s["pred_minutes"].astype(float)
    else:
        base = cases.set_index("case_id")["baseline_minutes"]
        out["s_pred_minutes"] = (
            arm_s["pred_ratio"].astype(float).values
            * out["case_id"].map(base).astype(float).values
        )
    return out


# ---------------------------------------------------------------------------
# Decision-time blurb slicing (the cutoff is a core honesty property)
# ---------------------------------------------------------------------------

def blurbs_for_case(
    case: Mapping[str, Any],
    corpus: pd.DataFrame,
    max_blurbs: int = 8,
) -> list[dict[str, Any]]:
    """Latest pre-decision blurbs for the case's player, oldest first.

    Includes blurbs published exactly at decision time; excludes anything
    after. Raw and unedited: superseded/contradictory updates are inputs,
    never cleaned. If more than ``max_blurbs`` qualify, the LATEST
    ``max_blurbs`` are kept (still oldest-first in the returned list).
    """
    decision_ts = pd.Timestamp(case["decision_report_ts"])
    if decision_ts.tzinfo is None:
        decision_ts = decision_ts.tz_localize("UTC")
    mine = corpus[
        (corpus["player"] == case["player"])
        & (corpus["published_ts"] <= decision_ts)
    ].sort_values("published_ts")
    if len(mine) > max_blurbs:
        mine = mine.tail(max_blurbs)
    return [
        {
            "published_ts": str(row.published_ts),
            "headline": row.headline,
            "body": row.body,
        }
        for row in mine.itertuples(index=False)
    ]


# ---------------------------------------------------------------------------
# Running the classifier over cases (fail closed at the harness level too)
# ---------------------------------------------------------------------------

def classify_cases(
    cases: pd.DataFrame,
    corpus: pd.DataFrame,
    classifier: Optional[DecisionsBandClassifier] = None,
    max_blurbs: int = 8,
) -> pd.DataFrame:
    """Classify every case. Returns one row per case:

    [case_id, band, p_out, p_heavy_restriction, p_light_restriction,
     p_full_workload, confidence, abstention, abstention_reason]

    With ``classifier=None`` (e.g. the API key is unconfigured) every case is
    recorded as an abstention instead of crashing the run.
    """
    rows = []
    for case in cases.to_dict("records"):
        if classifier is None:
            pred = BandPrediction.abstain(str(case["case_id"]), "not_configured")
        else:
            blurbs = blurbs_for_case(case, corpus, max_blurbs=max_blurbs)
            pred = classifier.classify_case(case, blurbs)
        row: dict[str, Any] = {
            "case_id": pred.case_id,
            "band": pred.band,
            "confidence": pred.confidence,
            "abstention": pred.abstention,
            "abstention_reason": pred.abstention_reason,
        }
        for band in BANDS:
            row[f"p_{band}"] = (
                pred.probabilities.get(band) if pred.probabilities else None
            )
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Calibration (fit on dev seasons only)
# ---------------------------------------------------------------------------

@dataclass
class Calibration:
    """Per-band mean realized ratio + empirical band base rates."""

    mean_ratio_by_band: dict[str, float]
    base_rates: dict[str, float]
    n_by_band: dict[str, int] = field(default_factory=dict)
    source: str = "fitted"

    @classmethod
    def priors(cls) -> "Calibration":
        total = sum(PRIOR_BASE_RATES.values())
        return cls(
            mean_ratio_by_band=dict(PRIOR_MEAN_RATIOS),
            base_rates={b: PRIOR_BASE_RATES[b] / total for b in BANDS},
            source="prior",
        )


def fit_calibration(dev_cases: pd.DataFrame, dev_predictions: pd.DataFrame) -> Calibration:
    """Fit on dev cases joined with their (non-abstention) Arm T predictions.

    - Per PREDICTED (argmax) band: empirical mean realized ratio.
    - Base rates: empirical distribution of REALIZED bands on dev.
    Bands with no dev cases fall back to the documented priors.
    """
    merged = dev_cases.merge(dev_predictions, on="case_id", how="inner")
    graded = merged[~merged["abstention"].fillna(True)].copy()
    priors = Calibration.priors()

    mean_ratio: dict[str, float] = {}
    n_by_band: dict[str, int] = {}
    for band in BANDS:
        sub = graded[graded["band"] == band]
        n_by_band[band] = int(len(sub))
        mean_ratio[band] = (
            float(sub["realized_ratio"].mean()) if len(sub) else priors.mean_ratio_by_band[band]
        )

    realized = [
        realized_band(r, m)
        for r, m in zip(dev_cases["realized_ratio"], dev_cases["realized_minutes"])
    ]
    n = len(realized)
    base_rates = {
        band: (realized.count(band) / n if n else priors.base_rates[band]) for band in BANDS
    }
    return Calibration(
        mean_ratio_by_band=mean_ratio, base_rates=base_rates, n_by_band=n_by_band
    )


# ---------------------------------------------------------------------------
# Band-mapped predictions and the S+T combiner
# ---------------------------------------------------------------------------

def band_mapped_ratio(probabilities: Mapping[str, float], calibration: Calibration) -> float:
    """Expectation form (primary): sum over bands of p(band) x calibrated
    mean ratio(band)."""
    return sum(
        float(probabilities[b]) * calibration.mean_ratio_by_band[b] for b in BANDS
    )


def band_mapped_minutes(
    baseline_minutes: float,
    probabilities: Mapping[str, float],
    calibration: Calibration,
    point: bool = False,
) -> float:
    """Band-mapped minutes prediction.

    Primary (``point=False``): baseline x E[ratio] under the band posterior.
    Point variant (``point=True``): baseline x calibrated mean ratio of the
    argmax band. Both are implemented; the expectation form is the graded one.
    """
    if point:
        argmax_band = max(BANDS, key=lambda b: float(probabilities[b]))
        return float(baseline_minutes) * calibration.mean_ratio_by_band[argmax_band]
    return float(baseline_minutes) * band_mapped_ratio(probabilities, calibration)


def combine(s_pred_minutes: float, t_pred_minutes: float, weight: float) -> float:
    """S+T combiner: S's point prediction updated by T's band-mapped value,
    shrunk toward S. ``weight`` is T's weight: 0 returns S unchanged, 1
    returns the T-mapped value."""
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"combiner weight must be in [0, 1], got {weight}")
    return (1.0 - weight) * float(s_pred_minutes) + weight * float(t_pred_minutes)


def fit_shrinkage_weight(
    dev: pd.DataFrame, step: float = 0.01
) -> float:
    """Fit the combiner weight on dev by grid search on MAE.

    ``dev`` needs columns [s_pred_minutes, t_pred_minutes, realized_minutes].
    The weight is shrunk toward S by T's calibration error in effect: the
    better T maps to minutes on dev, the more weight it earns; a T arm that
    adds nothing on dev fits weight 0.
    """
    best_w, best_mae = 0.0, float("inf")
    steps = int(round(1.0 / step))
    for i in range(steps + 1):
        w = i * step
        pred = (1.0 - w) * dev["s_pred_minutes"] + w * dev["t_pred_minutes"]
        mae = float((pred - dev["realized_minutes"]).abs().mean())
        if mae < best_mae:
            best_w, best_mae = w, mae
    return best_w
