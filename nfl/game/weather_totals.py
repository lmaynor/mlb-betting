"""N6 weather variant of the NFL 2D Kalman (Off/Def) totals model -- the Nov 17 with-vs-without judgment harness.

Argon verdict N6 (2026-11-11 game-state consult): run the Nov 17 holdout with a numeric weather vector; retain
weather for the Nov 24 prop residual pass ONLY if it improves out-of-sample log-loss on the 2D Kalman totals
holdout. Auto-kill: zero or negative log-loss improvement kills weather for BOTH the totals model and the
Nov 24 prop residual pass. This module makes that comparison runnable; it does not run the judgment.

THE MODEL AND THE HOLDOUT (unchanged from the game-lines pack):
  * Totals model: the Kalman Off/Def split in `nfl.game.kalman_ratings` (theta[0] OffPts / theta[1] DefPts)
    produces `kal_total`; the walk-forward replay in `nfl.game.market_eval` turns it into the engine total
    `mu_total` and the over/under probabilities. Weather enters HERE, as one additive game-level term on
    `mu_total` -- the Kalman state, the margin, the sigmas and the score matrix are identical in both variants,
    so the two runs differ SOLELY by the weather vector.
  * Holdout: the same records file the confidence-gated eval scored (`nfl.game.market_eval --records-csv`),
    dev seasons 2021-2023 / held-out 2024-2025 from `nfl.game.gated_eval`. The totals scoring frame is built
    exactly like `market_eval.to_events` builds "total (over)" (pushes dropped): n = 541 held-out games.
  * Fit protocol: the weather coefficients are fit by OLS on the DEV seasons only (residual = realised total
    minus base `mu_total`), then frozen and applied to the held-out seasons. Held-out outcomes fit nothing.

THE WEATHER VECTOR (one row per game, from the nflverse `schedules` mirror the replay already loads):
    [temp_f, wind_mph, precip_mm, is_dome]
  * `roof` -> `is_dome`: "dome" / "closed" (and a bare "retractable", whose game-day state nflverse does not
    record separately) are indoor: temp / wind take the neutral indoor values of `nfl.data.weather.NEUTRAL`
    (72 F / 0 mph) and precip is 0.0 BY CONSTRUCTION (it does not rain indoors). The vector is complete.
  * Outdoor games take the schedule's `temp` / `wind`. PROVENANCE, stated plainly: those are GAME-TIME OBSERVED
    conditions as recorded by nflverse / Pro Football Reference, not pregame forecasts -- they are only filled
    in after the game is played (the same caveat `ev/daily_projections.py` records for NFL_HIER_WEATHER). The
    repo has no archived historical forecast for these games, so the Nov 17 backtest measures the weather
    signal under observed conditions; that is an upper bound on what a forecast-based vector would deliver.
    The PREGAME form of the identical vector already exists for live games: `nfl.data.weather.kickoff_weather`
    returns forecast temp / wind / precip plus the dome flag from the Open-Meteo forecast, and it is that
    forecast form (knowable before kickoff) that the Nov 24 prop pass would inherit as a control if N6 clears.
  * PRECIPITATION GAP: `schedules` carries no precipitation column at all, and no other repo source has
    historical NFL precipitation (the GCS `Weather/` prefix is an MLB table keyed by baseball `game_pk`).
    So on the held-out 541: temp / wind / dome coverage is 532 full + 9 missing (below); precipitation
    coverage is 0 / 541. The precip feature is therefore INACTIVE in the Nov 17 fit (a feature column is only
    fitted when its raw field is observed for >= 50% of outdoor games in the fit frame); the column stays in
    the vector schema so the forecast form can activate it later without a schema change. No new data source
    was bought or scraped for this variant.
  * Coverage on the held-out 541 (2024-2025 REG, pushes dropped; verified against the schedules mirror):
    185 indoor (complete by construction) + 347 outdoor with observed temp+wind = 532 complete (98.3%);
    9 outdoor games have temp AND wind missing and get NO adjustment (fallback, below):
    2024_01_GB_PHI, 2024_10_NYG_CAR, 2024_13_HOU_JAX, 2024_15_NYJ_JAX, 2024_17_DET_SF,
    2025_11_WAS_MIA, 2025_12_IND_KC, 2025_15_LAC_KC, 2025_17_DEN_KC (several are international-site games).

MISSING-WEATHER FALLBACK (a hard rule, unit-tested): a game whose vector is incomplete gets the BASE model,
bit for bit -- its `mu_total` and `p_over` are the without-variant values. Missing outdoor weather is never
zero-filled or neutral-filled: filling wind = 0 / temp = 72 for an outdoor game with unknown weather would
silently score it as a calm dome-like game and leak a false signal into both the fit and the judgment.

FEATURE FORM (fixed a priori, not tuned): the additive term is linear in
    wind_mph            (outdoor observed wind; 0 indoors)
    cold_deg            max(0, 50 - temp_f)
    heat_deg            max(0, temp_f - 80)
    precip_mm           (inactive until a source exists; see the gap above)
    is_dome             1.0 indoor, else 0.0
plus an intercept. The Nov 17 judgment commands (identical holdout, identical records file; the ONLY
difference is --variant):

    GCS_BUCKET=<bucket> python -m nfl.game.market_eval --seasons 2021-2025 --records-csv nfl_rec.csv
    python -m nfl.game.weather_totals --records nfl_rec.csv --variant without
    python -m nfl.game.weather_totals --records nfl_rec.csv --variant with

Each prints n, totals log-loss, totals MAE and the weather coverage of the scored frame; the with-variant
also prints the frozen dev-fit coefficients. UNVALIDATED research tool; nothing in serving reads this.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from nfl.game.gated_eval import NFL_DEV_SEASONS, NFL_HELD_SEASONS
from nfl.game.model import BASE_SIGMA_MARGIN, BASE_SIGMA_TOTAL
from nfl.game.score_matrix import build_nfl_score_matrix

log = logging.getLogger("nfl.game.weather_totals")

INDOOR_ROOFS = {"dome", "closed", "retractable"}
NEUTRAL_TEMP_F = 72.0          # nfl.data.weather.NEUTRAL
NEUTRAL_WIND_MPH = 0.0
PRECIP_MIN_OUTDOOR_COVERAGE = 0.50   # a raw field must be observed for >= half of outdoor games to be fitted

BASE_FEATURES = ["wind_mph", "cold_deg", "heat_deg", "is_dome"]
ALL_FEATURES = ["wind_mph", "cold_deg", "heat_deg", "precip_mm", "is_dome"]


# --------------------------------------------------------------------------- the vector
@dataclass(frozen=True)
class WeatherVector:
    """One game's numeric weather vector. Numeric fields are None when NOT observed -- never filled."""

    temp_f: Optional[float]
    wind_mph: Optional[float]
    precip_mm: Optional[float]
    is_dome: bool
    status: str                # "indoor" (complete by construction) | "full" (observed) | "missing"
    source: str                # "schedule-roof" | "schedule" | "none"

    @property
    def complete(self) -> bool:
        return self.status in ("indoor", "full")


def _finite(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def vector_from_schedule(roof, temp, wind, precip=None) -> WeatherVector:
    """Build the vector from one nflverse `schedules` row's roof / temp / wind (+ precip if a source ever has it).

    Indoor (roof dome / closed / retractable) -> neutral temp / wind, precip 0.0, complete by construction.
    Outdoor -> the observed values; if temp or wind is missing the vector is "missing" and ALL numeric fields
    stay None, so no downstream code can mistake an unknown-weather game for a calm one."""
    r = str(roof).strip().lower() if roof is not None and str(roof).lower() != "nan" else ""
    if r in INDOOR_ROOFS:
        return WeatherVector(temp_f=NEUTRAL_TEMP_F, wind_mph=NEUTRAL_WIND_MPH, precip_mm=0.0,
                             is_dome=True, status="indoor", source="schedule-roof")
    t, w = _finite(temp), _finite(wind)
    if t is None or w is None:
        return WeatherVector(temp_f=None, wind_mph=None, precip_mm=None,
                             is_dome=False, status="missing", source="none")
    return WeatherVector(temp_f=t, wind_mph=w, precip_mm=_finite(precip),
                         is_dome=False, status="full", source="schedule")


def weather_frame(sched: pd.DataFrame) -> pd.DataFrame:
    """schedules -> one weather-vector row per game_id (temp_f, wind_mph, precip_mm, is_dome, wx_status, wx_source)."""
    cols = {"game_id", "roof", "temp", "wind"}
    missing = cols - set(sched.columns)
    if missing:
        raise ValueError(f"schedules frame lacks columns {sorted(missing)}; cannot build weather vectors")
    precip = sched["precip"] if "precip" in sched.columns else pd.Series([None] * len(sched), index=sched.index)
    rows = []
    for gid, roof, temp, wind, pr in zip(sched["game_id"], sched["roof"], sched["temp"], sched["wind"], precip):
        v = vector_from_schedule(roof, temp, wind, pr)
        rows.append({"game_id": gid, "temp_f": v.temp_f, "wind_mph": v.wind_mph, "precip_mm": v.precip_mm,
                     "is_dome": v.is_dome, "wx_status": v.status, "wx_source": v.source})
    return pd.DataFrame(rows).drop_duplicates(subset=["game_id"], keep="first")


def design_row(frame_row) -> Optional[Dict[str, float]]:
    """Fixed feature transforms for one joined record row; None when the vector is incomplete (no adjustment)."""
    if frame_row["wx_status"] not in ("indoor", "full"):
        return None
    temp, wind = float(frame_row["temp_f"]), float(frame_row["wind_mph"])
    pr = frame_row["precip_mm"]
    return {"wind_mph": wind, "cold_deg": max(0.0, 50.0 - temp), "heat_deg": max(0.0, temp - 80.0),
            "precip_mm": (float(pr) if pr is not None and np.isfinite(pr) else np.nan),
            "is_dome": 1.0 if bool(frame_row["is_dome"]) else 0.0}


# --------------------------------------------------------------------------- the adjustment
@dataclass
class WeatherAdjustment:
    coef: Dict[str, float]           # includes "intercept"
    active_features: List[str]
    n_fit: int
    precip_outdoor_coverage: float   # fraction of outdoor complete dev rows whose precip was actually observed

    def apply(self, frame_row, mu_total: float) -> float:
        """Adjusted total mean for one game. Incomplete vector -> the base mu_total, unchanged."""
        d = design_row(frame_row)
        if d is None:
            return float(mu_total)
        adj = self.coef["intercept"]
        for f in self.active_features:
            v = d[f]
            if v is None or not np.isfinite(v):      # e.g. an active precip column on a row that lacks it
                return float(mu_total)
            adj += self.coef[f] * v
        return float(mu_total) + adj


def fit_adjustment(dev: pd.DataFrame) -> WeatherAdjustment:
    """OLS of (realised total - base mu_total) on the weather design, DEV rows with complete vectors only."""
    rows = [design_row(r) for _, r in dev.iterrows()]
    keep = [i for i, d in enumerate(rows) if d is not None]
    if len(keep) < 30:
        raise ValueError(f"only {len(keep)} dev games have complete weather vectors; refusing to fit")
    kept = dev.iloc[keep]
    design = [rows[i] for i in keep]
    outdoor = [d for d in design if d["is_dome"] == 0.0]
    precip_cov = (float(np.mean([np.isfinite(d["precip_mm"]) for d in outdoor])) if outdoor else 0.0)
    active = list(BASE_FEATURES)
    if precip_cov >= PRECIP_MIN_OUTDOOR_COVERAGE:
        active = list(ALL_FEATURES)
    X = np.array([[1.0] + [d[f] for f in active] for d in design], dtype=float)
    y = (kept["total"].to_numpy(dtype=float) - kept["mu_total"].to_numpy(dtype=float))
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    coef = {"intercept": float(beta[0])}
    coef.update({f: float(b) for f, b in zip(active, beta[1:])})
    return WeatherAdjustment(coef=coef, active_features=active, n_fit=len(keep),
                             precip_outdoor_coverage=precip_cov)


# --------------------------------------------------------------------------- scoring
def _join(records: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    wx = weather_frame(sched)
    out = records.merge(wx, on="game_id", how="left")
    out["wx_status"] = out["wx_status"].fillna("missing")     # game absent from schedules: no vector, fallback
    return out


def _totals_frame(rec: pd.DataFrame) -> pd.DataFrame:
    """The gated-eval totals frame: rows with pushes dropped, exactly as market_eval.to_events does."""
    return rec[rec["total"] != rec["mkt_total"]].copy()


def _p_over_from_mu(mu_margin: float, mu_total: float, line: float) -> float:
    m = build_nfl_score_matrix(float(mu_margin), float(mu_total), BASE_SIGMA_MARGIN, BASE_SIGMA_TOTAL)
    po, pu, _push = m.total(float(line))
    return float(po / (po + pu)) if (po + pu) > 0 else 0.5


def score_variant(records: pd.DataFrame, sched: pd.DataFrame, variant: str,
                  dev_seasons: List[int] = NFL_DEV_SEASONS, held_seasons: List[int] = NFL_HELD_SEASONS) -> dict:
    """Score ONE variant on the held-out totals frame. `variant` is "without" (the base model, untouched) or
    "with" (dev-frozen weather term added to mu_total). Both variants score the identical rows."""
    if variant not in ("without", "with"):
        raise ValueError(f"variant must be 'without' or 'with', got {variant!r}")
    joined = _join(records, sched)
    held = _totals_frame(joined[joined["season"].isin(held_seasons)])
    adj: Optional[WeatherAdjustment] = None
    if variant == "with":
        dev = _totals_frame(joined[joined["season"].isin(dev_seasons)])
        adj = fit_adjustment(dev)
    p_model, mu_used = [], []
    n_adjusted = 0
    for r in held.itertuples(index=False):
        if adj is not None:
            row = {"wx_status": r.wx_status, "temp_f": r.temp_f, "wind_mph": r.wind_mph,
                   "precip_mm": r.precip_mm, "is_dome": r.is_dome}
            mu_adj = adj.apply(row, float(r.mu_total))
            if mu_adj != float(r.mu_total):
                n_adjusted += 1
                p_model.append(_p_over_from_mu(r.mu_margin, mu_adj, r.mkt_total))
                mu_used.append(mu_adj)
                continue
        p_model.append(float(r.p_over) / (float(r.p_over) + float(r.p_under)))
        mu_used.append(float(r.mu_total))
    p = np.clip(np.array(p_model, dtype=float), 1e-9, 1 - 1e-9)
    y = (held["total"].to_numpy(dtype=float) > held["mkt_total"].to_numpy(dtype=float)).astype(float)
    ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    mae = float(np.mean(np.abs(np.array(mu_used) - held["total"].to_numpy(dtype=float))))
    coverage = {s: int((held["wx_status"] == s).sum()) for s in ("indoor", "full", "missing")}
    out = {"variant": variant, "n": int(len(held)), "log_loss": ll, "mae_total": mae,
           "coverage": coverage, "n_adjusted": int(n_adjusted)}
    if adj is not None:
        out["fit"] = {"n_fit_dev": adj.n_fit, "active_features": adj.active_features,
                      "coef": adj.coef, "precip_outdoor_coverage_dev": adj.precip_outdoor_coverage}
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", required=True, help="records CSV from nfl.game.market_eval --records-csv")
    ap.add_argument("--variant", required=True, choices=["without", "with"])
    ap.add_argument("--out", default=None, help="optional JSON output path")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from nfl.data.read import load as nfl_load
    records = pd.read_csv(a.records)
    seasons = list(range(min(NFL_DEV_SEASONS), max(NFL_HELD_SEASONS) + 1))
    sched = nfl_load("schedules", seasons=seasons, columns=["game_id", "season", "roof", "temp", "wind"])
    res = score_variant(records, sched, a.variant)
    print(json.dumps(res, indent=2))
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(res, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
