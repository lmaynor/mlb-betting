"""U3/U6 shadow predictions serialization contract."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Iterable, Optional

import pandas as pd

from mlb_core import storage
from mod04c.dist_negbin import blend_p


def _clean_scalar(val):
    if val is None:
        return None
    if isinstance(val, float) and not math.isfinite(val):
        return None
    if pd.isna(val):
        return None
    if hasattr(val, "item"):
        return val.item()
    return val


def _json_default(obj):
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def make_shadow_rows(
    family: str,
    engine_label: str,
    rows,
    p_model_list: Iterable,
    market_p: Optional[Iterable] = None,
    lines: Optional[Iterable] = None,
    c_serving: float = 0.0,
) -> list[dict]:
    """Build truthful shadow prediction rows.

    - `p_model` is always the market-free raw model probability.
    - Rows stamped `guard_status == "excluded"` have `p_model` and `p_served`
      cleared to `None` so excluded rows are never silently scored.
    - When `market_p` is absent or NaN, `p_served == p_model` with `blend_status="no_market_p"`.
    """
    records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else [dict(r) for r in rows]
    p_models = list(p_model_list)
    if len(p_models) != len(records):
        raise ValueError(f"p_model_list length ({len(p_models)}) != rows length ({len(records)})")

    mkt_list = list(market_p) if market_p is not None else [None] * len(records)
    line_list = list(lines) if lines is not None else [r.get("line") for r in records]

    out: list[dict] = []
    for i, rec in enumerate(records):
        guard_status = _clean_scalar(rec.get("guard_status"))
        guard_reason = _clean_scalar(rec.get("guard_reason"))

        if guard_status == "excluded":
            p_m = None
            p_s = None
            blend_status = "excluded_by_guard"
        else:
            raw_pm = _clean_scalar(p_models[i])
            p_m = float(raw_pm) if raw_pm is not None else None
            raw_mk = _clean_scalar(mkt_list[i]) if i < len(mkt_list) else None
            if p_m is not None and raw_mk is not None:
                p_s = float(blend_p(p_m, float(raw_mk), float(c_serving)))
                blend_status = "blended"
            else:
                p_s = p_m
                blend_status = "no_market_p"

        team_val = _clean_scalar(rec.get("team") or rec.get("team_abbr"))
        opp_val = _clean_scalar(rec.get("opponent") or rec.get("opp") or rec.get("opp_abbr"))
        pname = _clean_scalar(rec.get("player_name") or rec.get("name"))

        item = {
            "family": family,
            "engine": engine_label,
            "date": _clean_scalar(rec.get("date")),
            "game_id": _clean_scalar(rec.get("game_id")),
            "player_id": _clean_scalar(rec.get("player_id")),
            "player_name": pname,
            "team": team_val,
            "opponent": opp_val,
            "line": _clean_scalar(line_list[i]) if i < len(line_list) else None,
            "p_model": p_m,
            "p_served": p_s,
            "blend_status": blend_status,
            "c_serving": float(c_serving),
        }
        if "line_source" in rec:
            item["line_source"] = _clean_scalar(rec.get("line_source"))
        if guard_status is not None:
            item["guard_status"] = guard_status
        if guard_reason is not None:
            item["guard_reason"] = guard_reason
        out.append(item)
    return out


def write_predictions_json(rows: list[dict], family: str, version: str, key: str) -> tuple[str, int]:
    """Serialize shadow prediction rows to JSON via `mlb_core.storage.write_bytes`."""
    payload = {
        "version": version,
        "family": family,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
    }
    data = json.dumps(payload, indent=2, default=_json_default).encode("utf-8")
    storage.write_bytes(data, key)
    return key, len(rows)
