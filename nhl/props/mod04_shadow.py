"""B6 MOD-04 shadow prediction banking and day-level grading (`nhl.props.mod04_shadow`).

SHADOW-ONLY: writes shadow predictions to local JSON or `NHL/mod04_shadow/{family}_latest.json` /
`NBA/mod04_shadow/{family}_latest.json` via `mlb_core.storage`, and scores shadow predictions
against Label Store actuals (`game_id`, `player_id`, `actual`). Never writes to live boards,
`paper_track`, or serving bundles.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from nhl.props.mod04_features import join_labels

_PROB_EPS = 1e-6


def _clip_prob(p: float) -> float:
    return min(max(float(p), _PROB_EPS), 1.0 - _PROB_EPS)


def logit(p: float) -> float:
    """Log-odds `log(p / (1 - p))` with `[1e-6, 1 - 1e-6]` clamping."""
    pc = _clip_prob(p)
    return math.log(pc / (1.0 - pc))


def sigmoid(z: float) -> float:
    """Logistic function `1 / (1 + exp(-z))`."""
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def blend_p(p_model: float, p_market: float, c: float) -> float:
    """U4 logit blend: `logit(p_served) = logit(p_market) + c * (logit(p_model) - logit(p_market))` (`a=0, b=1`).

    - `c == 0` returns `p_market`.
    - `c == 1` returns `p_model`.
    - Missing/non-finite `p_model` falls back to `p_market`; missing `p_market` falls back to `p_model`.
    """
    mkt_ok = p_market is not None and math.isfinite(float(p_market))
    mod_ok = p_model is not None and math.isfinite(float(p_model))
    if not mkt_ok and not mod_ok:
        return float("nan")
    if not mkt_ok:
        return float(p_model)
    if not mod_ok:
        return float(p_model) if not mkt_ok else float(p_market)

    c_val = float(c)
    if c_val == 0.0:
        return float(p_market)
    if c_val == 1.0:
        return float(p_model)

    lm = logit(float(p_market))
    ld = logit(float(p_model)) - lm
    return sigmoid(lm + c_val * ld)


def p_over_from_pmf(pmf: Iterable[float], line: float) -> float:
    """Compute `P(Y > line)` from a discrete PMF over `0..kmax`."""
    arr = np.asarray(list(pmf), dtype=float)
    if arr.size == 0:
        return 0.5
    k_floor = int(math.floor(float(line)))
    if k_floor < 0:
        return 1.0
    if k_floor + 1 >= len(arr):
        return 0.0
    return float(np.sum(arr[k_floor + 1:]))


def _lookup_line_prob(mapping: Mapping[Any, Any], line: float) -> float | None:
    if not isinstance(mapping, Mapping):
        return None
    for key in (str(line), str(float(line)), line, float(line)):
        if key in mapping and mapping[key] is not None:
            val = float(mapping[key])
            if math.isfinite(val):
                return val
    for k, v in mapping.items():
        try:
            if math.isclose(float(k), float(line), abs_tol=1e-9) and v is not None:
                val = float(v)
                if math.isfinite(val):
                    return val
        except (TypeError, ValueError):
            continue
    return None


def _normalize_shadow_row(row: Mapping[str, Any], default_ts: str) -> dict[str, Any]:
    pmf_list = [float(x) for x in (row.get("pmf") or [])]
    main_line = row.get("main_line")

    if main_line is not None and math.isfinite(float(main_line)):
        line_val = float(main_line)
        line_key = str(line_val)
        p_model_dict: dict[str, float] = {line_key: p_over_from_pmf(pmf_list, line_val)}
    elif isinstance(row.get("p_model"), Mapping) and row.get("p_model"):
        p_model_dict = {str(k): float(v) for k, v in row["p_model"].items()}
    else:
        p_model_dict = {}

    p_market_raw = row.get("p_market", row.get("p_mkt"))
    c_val = float(row.get("c", row.get("blend_c", 0.0)))
    has_market = False

    if p_market_raw is not None:
        if isinstance(p_market_raw, Mapping):
            p_served_dict: dict[str, float] = {}
            for lk, pm in p_model_dict.items():
                pmkt = _lookup_line_prob(p_market_raw, float(lk))
                if pmkt is not None:
                    has_market = True
                    p_served_dict[lk] = blend_p(pm, pmkt, c_val)
                else:
                    p_served_dict[lk] = pm
        elif math.isfinite(float(p_market_raw)):
            has_market = True
            pmkt_scalar = float(p_market_raw)
            p_served_dict = {lk: blend_p(pm, pmkt_scalar, c_val) for lk, pm in p_model_dict.items()}
        else:
            p_served_dict = dict(p_model_dict)
    elif isinstance(row.get("p_served"), Mapping) and row.get("p_served") and row.get("served_note") != "shadow_no_market":
        has_market = True
        p_served_dict = {str(k): float(v) for k, v in row["p_served"].items()}
    else:
        p_served_dict = dict(p_model_dict)

    out: dict[str, Any] = {
        "date": str(row.get("date", ""))[:10],
        "sport": str(row.get("sport", "")),
        "family": str(row.get("family", "")),
        "player_id": row.get("player_id"),
        "player_name": row.get("player_name", row.get("name", "")),
        "game_id": row.get("game_id"),
        "engine": str(row.get("engine", "")),
        "mu": float(row["mu"]) if row.get("mu") is not None else None,
        "alpha": float(row["alpha"]) if row.get("alpha") is not None else None,
        "pmf": pmf_list,
        "p_model": p_model_dict,
        "p_served": p_served_dict,
        "generated_at": str(row.get("generated_at") or default_ts),
    }
    if main_line is not None and math.isfinite(float(main_line)):
        out["main_line"] = float(main_line)
    if not has_market:
        out["served_note"] = "shadow_no_market"
    elif row.get("served_note"):
        out["served_note"] = str(row["served_note"])
    return out


def build_shadow_payload(
    rows: Iterable[Mapping[str, Any]],
    version: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the `trained_latest.json`-style shadow payload from prediction dicts."""
    now = datetime.now(timezone.utc)
    gen_ts = generated_at or now.isoformat()
    ver = version or now.strftime("%Y%m%dT%H%M%SZ")
    preds = [_normalize_shadow_row(r, gen_ts) for r in rows]
    return {
        "version": ver,
        "generated_at": gen_ts,
        "predictions": preds,
    }


def write_shadow_predictions(
    rows: Iterable[Mapping[str, Any]],
    out_path: str | Path,
    version: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Write shadow predictions JSON in the `trained_latest.json` pattern to `out_path`."""
    payload = build_shadow_payload(rows, version=version, generated_at=generated_at)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def shadow_gcs_key(sport: str, family: str) -> str:
    """Return canonical GCS key `NHL/mod04_shadow/{family}_latest.json` or `NBA/mod04_shadow/{family}_latest.json`."""
    sp = str(sport).strip().upper()
    if sp not in ("NHL", "NBA"):
        raise ValueError(f"Unsupported sport for mod04_shadow: {sport!r} (expected 'NHL' or 'NBA')")
    fam = str(family).strip()
    if not fam:
        raise ValueError("family must be non-empty")
    return f"{sp}/mod04_shadow/{fam}_latest.json"


def write_shadow_predictions_gcs(
    rows: Iterable[Mapping[str, Any]],
    sport: str,
    family: str,
    version: str | None = None,
    generated_at: str | None = None,
) -> str:
    """Write shadow predictions JSON to GCS via `mlb_core.storage` (matching `paper_track` pattern)."""
    from mlb_core import storage

    key = shadow_gcs_key(sport, family)
    payload = build_shadow_payload(rows, version=version, generated_at=generated_at)
    storage.write_bytes(json.dumps(payload, indent=2).encode("utf-8"), key)
    return key


def score_shadow_day(
    predictions_path: str | Path,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    """Grade a shadow predictions JSON file against a Label Store DataFrame `(game_id, player_id, actual)`.

    Uses `mod04_features.join_labels` on `(game_id, player_id)` and computes per-row
    log-loss and Brier score contributions for each row's main line when present.
    """
    req_labels = {"game_id", "player_id", "actual"}
    missing = req_labels - set(labels.columns)
    if missing:
        raise ValueError(f"labels missing required column(s): {sorted(missing)}")

    payload = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    preds = payload.get("predictions", [])
    if not preds:
        return pd.DataFrame(columns=[
            "date", "sport", "family", "game_id", "player_id", "player_name", "engine",
            "main_line", "actual", "y_over", "p_model_main", "p_served_main",
            "log_loss", "brier", "log_loss_served", "brier_served",
        ])

    pred_df = pd.DataFrame(preds)
    joined = join_labels(pred_df, labels[["game_id", "player_id", "actual"]], how="inner")
    if joined.empty:
        return pd.DataFrame(columns=[
            "date", "sport", "family", "game_id", "player_id", "player_name", "engine",
            "main_line", "actual", "y_over", "p_model_main", "p_served_main",
            "log_loss", "brier", "log_loss_served", "brier_served",
        ])

    out_rows: list[dict[str, Any]] = []
    for row in joined.to_dict(orient="records"):
        p_mod_map = row.get("p_model") if isinstance(row.get("p_model"), Mapping) else {}
        p_srv_map = row.get("p_served") if isinstance(row.get("p_served"), Mapping) else {}

        line_val = row.get("main_line")
        if line_val is None or not math.isfinite(float(line_val)):
            if len(p_mod_map) == 1:
                try:
                    line_val = float(next(iter(p_mod_map)))
                except (TypeError, ValueError):
                    line_val = np.nan
            else:
                line_val = np.nan
        else:
            line_val = float(line_val)

        actual = float(row["actual"]) if row.get("actual") is not None and math.isfinite(float(row["actual"])) else np.nan
        p_m = _lookup_line_prob(p_mod_map, line_val) if math.isfinite(line_val) else None
        p_s = _lookup_line_prob(p_srv_map, line_val) if math.isfinite(line_val) else None
        if p_s is None:
            p_s = p_m

        y_over = np.nan
        ll_m = np.nan
        br_m = np.nan
        ll_s = np.nan
        br_s = np.nan

        if math.isfinite(line_val) and math.isfinite(actual) and actual != line_val:
            y_over = 1.0 if actual > line_val else 0.0
            if p_m is not None:
                pm_c = _clip_prob(p_m)
                ll_m = -(y_over * math.log(pm_c) + (1.0 - y_over) * math.log(1.0 - pm_c))
                br_m = (p_m - y_over) ** 2
            if p_s is not None:
                ps_c = _clip_prob(p_s)
                ll_s = -(y_over * math.log(ps_c) + (1.0 - y_over) * math.log(1.0 - ps_c))
                br_s = (p_s - y_over) ** 2

        out_rows.append({
            "date": row.get("date"),
            "sport": row.get("sport"),
            "family": row.get("family"),
            "game_id": row.get("game_id"),
            "player_id": row.get("player_id"),
            "player_name": row.get("player_name"),
            "engine": row.get("engine"),
            "main_line": line_val,
            "actual": actual,
            "y_over": y_over,
            "p_model_main": p_m if p_m is not None else np.nan,
            "p_served_main": p_s if p_s is not None else np.nan,
            "log_loss": ll_m,
            "brier": br_m,
            "log_loss_served": ll_s,
            "brier_served": br_s,
        })

    return pd.DataFrame(out_rows)
