"""Shadow predictions-file builder, U3/U4 dual logging, and pre-lock rescore design (MOD-04 Cluster d).

SHADOW-ONLY: Writes shadow JSON payloads matching the `trained_latest.json` pattern.
Never alters production serving weights or live projection tables.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from mod04d import nhl_scoring
from mod04d.distributions import blend_probs, pmf_mean, pmf_p_over
from mod04d.features import assert_no_leak_columns

FAMILY_ENGINE_MAP: dict[str, str] = {
    "nba_points": "mod04_nba_points_shadow_v1",
    "nba_threes": "mod04_nba_threes_shadow_v1",
    "nhl_goals": "mod04_nhl_goals_shadow_v1",
    "nhl_points": "mod04_nhl_points_shadow_v1",
}

DEFAULT_LINES: dict[str, float] = {
    "nba_points": 19.5,
    "nba_threes": 2.5,
    "nhl_goals": 0.5,
    "nhl_points": 0.5,
}

DEFAULT_K_MAX: dict[str, int] = {
    "nba_points": 80,
    "nba_threes": 16,
    "nhl_goals": 10,
    "nhl_points": 12,
}


def _resolve_engine(family: str, model) -> str:
    if family not in FAMILY_ENGINE_MAP:
        raise ValueError(f"Unknown family {family!r}; expected one of {tuple(FAMILY_ENGINE_MAP)}")
    if isinstance(model, dict) and "manifest" in model:
        stat = family.split("_", 1)[1]
        return str(model["manifest"].get("engines", {}).get(stat, FAMILY_ENGINE_MAP[family]))
    if isinstance(model, dict) and "meta" in model:
        return str(model["meta"].get("engine", FAMILY_ENGINE_MAP[family]))
    meta = getattr(model, "meta", None)
    if isinstance(meta, dict) and meta.get("engine"):
        return str(meta["engine"])
    eng = getattr(model, "engine", None)
    return str(eng) if eng else FAMILY_ENGINE_MAP[family]


def _score_model(
    family: str, slate_features: pd.DataFrame, model, k_max: int | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Return `(pmf, mu, alpha)` arrays for `slate_features`."""
    km = int(k_max or DEFAULT_K_MAX[family])
    if family.startswith("nhl_"):
        stat = family.split("_", 1)[1]
        mu, alpha = nhl_scoring.predict(model, slate_features, stat=stat)
        pmf = nhl_scoring.predict_pmf(model, slate_features, stat=stat, k_max=km)
        return pmf, mu, alpha

    pmf = model.pmf(slate_features, k_max=km)
    if hasattr(model, "predict_mu_alpha"):
        mu, alpha = model.predict_mu_alpha(slate_features)
    elif hasattr(model, "predict_attempts_mu_alpha"):
        mu = pmf_mean(pmf)
        alpha = np.full(len(slate_features), np.nan, dtype=float)
    else:
        mu = pmf_mean(pmf)
        alpha = None
    return pmf, mu, alpha


def build_shadow_rows(
    family: str,
    slate_features: pd.DataFrame,
    model,
    market_probs: Sequence[float | None] | np.ndarray | pd.Series | None = None,
    lines: Sequence[float] | np.ndarray | pd.Series | float | None = None,
    c: float = 0.0,
    date: str | None = None,
    k_max: int | None = None,
) -> list[dict]:
    """Score a pregame feature slate and emit U3/U4 dual-logged shadow row dicts.

    Each row carries:
      `{date, player_id, family, engine, line, p_model, p_served, mu, alpha, pmf_over}`
    where `p_model` is the raw probability from the discrete PMF and `p_served` is the
    U4 logit-blend with `p_market` when supplied (else `None`).
    """
    if family not in FAMILY_ENGINE_MAP:
        raise ValueError(f"Unknown family {family!r}; expected one of {tuple(FAMILY_ENGINE_MAP)}")
    assert_no_leak_columns(slate_features)
    n = len(slate_features)
    if n == 0:
        return []

    engine = _resolve_engine(family, model)
    pmf, mu_arr, alpha_arr = _score_model(family, slate_features, model, k_max=k_max)

    if lines is None:
        line_arr = np.full(n, DEFAULT_LINES[family], dtype=float)
    elif np.ndim(lines) == 0:
        line_arr = np.full(n, float(lines), dtype=float)
    else:
        line_arr = np.asarray(lines, dtype=float)
        if len(line_arr) != n:
            raise ValueError(f"lines length {len(line_arr)} != slate rows {n}")

    p_model_arr = pmf_p_over(pmf, line_arr)

    if market_probs is not None:
        mkt_arr = np.asarray(
            [np.nan if v is None else float(v) for v in market_probs], dtype=float
        )
        if len(mkt_arr) != n:
            raise ValueError(f"market_probs length {len(mkt_arr)} != slate rows {n}")
    else:
        mkt_arr = None

    dates_col = (
        slate_features["date"].astype(str).tolist()
        if "date" in slate_features.columns
        else [str(date or "")] * n
    )
    pids_col = (
        slate_features["player_id"].tolist()
        if "player_id" in slate_features.columns
        else list(range(n))
    )

    rows: list[dict] = []
    for i in range(n):
        p_mod = float(p_model_arr[i])
        p_srv: float | None = None
        if mkt_arr is not None and np.isfinite(mkt_arr[i]):
            p_srv = float(blend_probs(p_mod, float(mkt_arr[i]), c=float(c)))

        al_val: float | None = None
        if alpha_arr is not None and np.isfinite(alpha_arr[i]):
            al_val = round(float(alpha_arr[i]), 6)

        pid = pids_col[i]
        if isinstance(pid, (np.integer,)):
            pid = int(pid)

        rows.append(
            {
                "date": dates_col[i][:10],
                "player_id": pid,
                "family": family,
                "engine": engine,
                "line": float(line_arr[i]),
                "p_model": round(p_mod, 6),
                "p_served": round(p_srv, 6) if p_srv is not None else None,
                "mu": round(float(mu_arr[i]), 6),
                "alpha": al_val,
                "pmf_over": round(p_mod, 6),
            }
        )
    return rows


def write_shadow_file(
    rows: list[dict], path: str | Path, meta: dict | None = None
) -> Path:
    """Write shadow rows in the `trained_latest.json` payload format."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engines = sorted({r["engine"] for r in rows}) if rows else []
    families = sorted({r["family"] for r in rows}) if rows else []
    payload = {
        "shadow_only": True,
        "schema": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "families": families,
        "engines": engines,
        "count": len(rows),
        "rows": rows,
    }
    if meta:
        payload["meta"] = meta
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def read_shadow_file(path: str | Path) -> dict:
    """Read a shadow JSON file written by `write_shadow_file`."""
    return json.loads(Path(path).read_text())


def rescore_prelock(
    rows: list[dict],
    updated_features: pd.DataFrame,
    model,
    lock_time: str | datetime | pd.Timestamp,
    now_utc: str | datetime | pd.Timestamp | None = None,
    market_probs: Sequence[float | None] | np.ndarray | None = None,
    c: float = 0.0,
) -> list[dict]:
    """Pre-lock rescore hook (T - 60 min design).

    Pre-lock rescore design:
    - Triggered at `lock_time - 60 min` re-reading ONLY pre-lock lineup/injury snapshots
      with `ts_utc <= now_utc` (strictly `< lock_time`).
    - Recomputes `mu`, `alpha`, `p_model`, `pmf_over`, and `p_served` from `updated_features`
      while preserving each row's `family`, `line`, `date`, and `player_id`.
    - Hard-refuses to execute at or after `lock_time` (`now_utc >= lock_time`).
    """
    lock_ts = pd.Timestamp(lock_time)
    if lock_ts.tzinfo is None:
        lock_ts = lock_ts.tz_localize("UTC")
    now_ts = (
        pd.Timestamp.now(tz="UTC")
        if now_utc is None
        else pd.Timestamp(now_utc)
    )
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    if now_ts >= lock_ts:
        raise ValueError(
            f"Pre-lock rescore refused at/after lock_time ({now_ts.isoformat()} >= {lock_ts.isoformat()})"
        )
    if not rows:
        return []
    family = rows[0]["family"]
    lines = [float(r["line"]) for r in rows]
    date = rows[0].get("date")
    return build_shadow_rows(
        family=family,
        slate_features=updated_features,
        model=model,
        market_probs=market_probs,
        lines=lines,
        c=c,
        date=date,
    )
