"""MOD-04 Cluster (a) Shadow Scorer CLI for NBA steals and blocks.

Reads a board JSON (``projections_latest.json`` shape), scores every ``steals`` / ``blocks``
row strictly using pre-cutoff features (``cutoff_utc = game commence``, or ``now_utc`` in
``--rescore`` mode for games locking within 75 minutes), computes the U4 market-anchored
shadow probability, and emits JSONL rows locally and optionally to GCS:
    ``gs://concrete-crow-445205-m4-mlb-data/MOD04/cluster_a/shadow/dt=<date>/nba.jsonl``.

Never touches ``ev/daily_projections.py`` or any serving path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mlb_core.data.player_names import norm_name
from mod04.feature_store import _norm_team, build_pregame_features
from mod04.hurdle_nb import p_over as hurdle_p_over
from mod04.train_clustera_nba import load_bundle, predict_params

GCS_BUCKET = "concrete-crow-445205-m4-mlb-data"
GCS_SHADOW_PREFIX = "MOD04/cluster_a/shadow"
GCLOUD_BIN = Path.home() / "google-cloud-sdk/bin/gcloud"
RESCORE_WINDOW_MIN = 75.0
ENGINES = {
    "steals": "mod04_steals_shadow_v1",
    "blocks": "mod04_blocks_shadow_v1",
}


def _to_utc(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def _logit(p: float) -> float:
    pc = min(max(float(p), 0.01), 0.99)
    return math.log(pc / (1.0 - pc))


def _sigmoid(z: float) -> float:
    zc = min(max(float(z), -20.0), 20.0)
    return 1.0 / (1.0 + math.exp(-zc))


def _u4_blend(p_market: float | None, p_model: float, blend_cfg: dict[str, Any]) -> float:
    """U4 market-anchored blend: ``sigmoid(a + b*logit(p_market) + c*(logit(p_model) - logit(p_market)))``."""
    if p_market is None or not math.isfinite(float(p_market)):
        return float(p_model)
    a = float(blend_cfg.get("a", 0.0))
    b = float(blend_cfg.get("b", 1.0))
    c = float(blend_cfg.get("c", 0.0))
    if not blend_cfg.get("fitted_on_market", False):
        a, b = 0.0, 1.0
    lm = _logit(float(p_market))
    ld = _logit(float(p_model)) - lm
    return float(_sigmoid(a + b * lm + c * ld))


def _extract_market_prob(row: dict[str, Any]) -> float | None:
    for k in ("p_cons_w_over", "p_over_market"):
        val = row.get(k)
        if val is not None and math.isfinite(float(val)):
            return float(val)
    return None


def _extract_board_served_over(row: dict[str, Any]) -> float | None:
    """Extract over-side served probability from a board row."""
    p_ov = row.get("p_over_blend")
    if p_ov is not None and math.isfinite(float(p_ov)):
        return float(p_ov)
    pbs = row.get("p_blend_side")
    if pbs is not None and math.isfinite(float(pbs)):
        side = str(row.get("side") or "over").strip().lower()
        return float(pbs) if side != "under" else float(1.0 - float(pbs))
    return None


def _extract_line(row: dict[str, Any]) -> float | None:
    for k in ("line", "consensus_line"):
        val = row.get(k)
        if val is not None and math.isfinite(float(val)):
            return float(val)
    return None


def _load_existing_timestamps(path: Path) -> dict[tuple[str, str, str, float], str]:
    """Map ``(game_key, player_norm, stat, line) -> earliest scored_at_utc`` for ``--rescore``."""
    out: dict[tuple[str, str, str, float], str] = {}
    if not path.exists():
        return out
    for line_str in path.read_text().splitlines():
        if not line_str.strip():
            continue
        try:
            rec = json.loads(line_str)
        except json.JSONDecodeError:
            continue
        key = (
            str(rec.get("game_key", "")),
            norm_name(str(rec.get("player", ""))),
            str(rec.get("stat", "")),
            float(rec.get("line", 0.0)),
        )
        if key not in out and rec.get("scored_at_utc"):
            out[key] = str(rec["scored_at_utc"])
    return out


def score_board(
    board_payload: dict[str, Any] | list[dict[str, Any]],
    bundle_dir: str | Path,
    data_dir: str | Path,
    *,
    rescore: bool = False,
    now_utc: str | pd.Timestamp | None = None,
    existing_jsonl: Path | None = None,
) -> list[dict[str, Any]]:
    """Score steals & blocks rows on ``board_payload`` and return shadow JSONL dicts."""
    raw_rows = board_payload.get("rows", []) if isinstance(board_payload, dict) else list(board_payload)
    sb_rows = [r for r in raw_rows if str(r.get("stat", "")).lower() in ENGINES]
    if not sb_rows:
        return []

    bundle = load_bundle(bundle_dir)
    man = bundle["manifest"]
    bundle_ver = str(man["version"])
    now_ts = _to_utc(now_utc) if now_utc is not None else pd.Timestamp(datetime.now(timezone.utc))
    scored_at_str = now_ts.isoformat()

    prior_ts_map = _load_existing_timestamps(existing_jsonl) if (rescore and existing_jsonl is not None) else {}

    # Filter rows (especially --rescore: strictly 0 < mins_to_commence <= 75)
    selected: list[dict[str, Any]] = []
    for r in sb_rows:
        comm_raw = r.get("commence")
        if not comm_raw:
            continue
        comm_ts = _to_utc(comm_raw)
        mins_to_lock = (comm_ts - now_ts).total_seconds() / 60.0
        if rescore:
            if not (0.0 < mins_to_lock <= RESCORE_WINDOW_MIN):
                continue
        line_val = _extract_line(r)
        if line_val is None:
            continue
        selected.append({
            **r,
            "_comm_ts": comm_ts,
            "_utc_date": comm_ts.strftime("%Y-%m-%d"),
            "_line": line_val,
            "_team": _norm_team(r.get("team")),
            "_opp": _norm_team(r.get("opp")),
            "_pnorm": norm_name(str(r.get("player", ""))),
        })

    if not selected:
        return []

    # Group by (utc_date, cutoff_ts) to build features once per distinct game tip / rescore cutoff
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in selected:
        cutoff_ts = min(now_ts, item["_comm_ts"]) if rescore else item["_comm_ts"]
        gk_group = (item["_utc_date"], cutoff_ts.isoformat())
        groups.setdefault(gk_group, []).append(item)

    shadow_rows: list[dict[str, Any]] = []
    for (t_date, cut_iso), items in groups.items():
        # Unique player-team-opp rows in this group
        slate_records = []
        seen_keys: set[tuple[str, str]] = set()
        for it in items:
            pk = (it["_team"] or "", it["_pnorm"])
            if pk in seen_keys:
                continue
            seen_keys.add(pk)
            gk = str(it.get("game_key", ""))
            is_home = 1 if (f"@{it['_team']}" in gk) else 0
            slate_records.append({
                "game_id": gk,
                "player": it["player"],
                "player_norm": it["_pnorm"],
                "team": it["_team"],
                "opp": it["_opp"],
                "is_home": is_home,
            })
        slate_df = pd.DataFrame(slate_records)
        feat_df = build_pregame_features(t_date, cut_iso, data_dir, slate=slate_df)

        # Index features by (team, player_norm)
        feat_lookup: dict[tuple[str, str], int] = {}
        for idx, fr in enumerate(feat_df.itertuples(index=False)):
            feat_lookup[(str(fr.team), str(fr.player_norm))] = idx

        # Batch-predict per stat on feat_df
        preds_by_stat: dict[str, pd.DataFrame] = {}
        for st_name in {str(it["stat"]).lower() for it in items}:
            if st_name in bundle["models"] and not feat_df.empty:
                preds_by_stat[st_name] = predict_params(bundle["models"][st_name], feat_df)

        for it in items:
            st_name = str(it["stat"]).lower()
            idx = feat_lookup.get((it["_team"] or "", it["_pnorm"]))
            if idx is None or st_name not in preds_by_stat:
                continue
            frow = feat_df.iloc[idx]
            prow = preds_by_stat[st_name].iloc[idx]

            mu_min = float(frow["mu_min"])
            sigma_min = float(frow["sigma_min"])
            pi_val = float(prow["pi"])
            lam_val = float(prow["lambda_per_min"])
            alp_val = float(prow["alpha"])
            line_val = float(it["_line"])

            p_mod = float(hurdle_p_over(line_val, pi_val, lam_val, alp_val, mu_min, sigma_min))
            p_mkt = _extract_market_prob(it)
            p_board = _extract_board_served_over(it)
            blend_cfg = man.get("blend", {}).get(st_name, {"a": 0.0, "b": 1.0, "c": 0.0})
            p_shadow = _u4_blend(p_mkt, p_mod, blend_cfg)

            orig_key = (str(it.get("game_key", "")), it["_pnorm"], st_name, line_val)
            rescore_of = prior_ts_map.get(orig_key, scored_at_str) if rescore else None

            # Use ET date from game_key if present, else UTC tip date
            gk_str = str(it.get("game_key", ""))
            gk_parts = gk_str.split(":")
            row_date = gk_parts[1] if len(gk_parts) >= 2 and len(gk_parts[1]) == 10 else t_date

            shadow_rows.append({
                "date": row_date,
                "game_key": gk_str,
                "player": str(it["player"]),
                "team": it["_team"],
                "opp": it["_opp"],
                "stat": st_name,
                "line": round(line_val, 2),
                "p_market": None if p_mkt is None else round(p_mkt, 6),
                "p_board_served": None if p_board is None else round(p_board, 6),
                "p_model": round(p_mod, 6),
                "p_served_shadow": round(p_shadow, 6),
                "pi": round(pi_val, 6),
                "lambda_per_min": round(lam_val, 6),
                "alpha": round(alp_val, 6),
                "mu_min": round(mu_min, 3),
                "sigma_min": round(sigma_min, 3),
                "engine": ENGINES[st_name],
                "bundle_version": bundle_ver,
                "scored_at_utc": scored_at_str,
                "rescore_of": rescore_of,
            })

    return shadow_rows


def _write_jsonl(rows: list[dict[str, Any]], out_path: Path, append: bool = False) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and out_path.exists() else "w"
    with out_path.open(mode) as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _upload_shadow_gcs(local_path: Path, dt_str: str) -> str:
    key = f"{GCS_SHADOW_PREFIX}/dt={dt_str}/nba.jsonl"
    if os.environ.get("MLB_GCS_BUCKET") or os.environ.get("GCS_BUCKET"):
        from mlb_core import storage

        storage.write_bytes(local_path.read_bytes(), key)
    else:
        subprocess.run([str(GCLOUD_BIN), "storage", "cp", str(local_path), f"gs://{GCS_BUCKET}/{key}"], check=True)
    return f"gs://{GCS_BUCKET}/{key}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score NBA steals & blocks board in shadow mode.")
    ap.add_argument("--board", required=True, help="Path to board JSON (projections_latest.json shape)")
    ap.add_argument("--bundle-dir", required=True, help="Bundle directory or parent directory with latest.json")
    ap.add_argument("--data-dir", default="data/mod04", help="Feature data directory")
    ap.add_argument("--out", default=None, help="Output JSONL path")
    ap.add_argument("--rescore", action="store_true", help="Pre-lock rescore mode (games locking <= 75m)")
    ap.add_argument("--now-utc", default=None, help="Override UTC timestamp (ISO) for deterministic runs")
    ap.add_argument("--write-gcs", action="store_true", help="Upload shadow JSONL to GCS")
    args = ap.parse_args(argv)

    board = json.loads(Path(args.board).read_text())
    dt_str = str(board.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10]
    out_path = Path(args.out) if args.out else Path(args.data_dir) / f"shadow/dt={dt_str}/nba.jsonl"

    rows = score_board(
        board,
        args.bundle_dir,
        args.data_dir,
        rescore=args.rescore,
        now_utc=args.now_utc,
        existing_jsonl=out_path,
    )
    _write_jsonl(rows, out_path, append=args.rescore)
    if args.write_gcs:
        _upload_shadow_gcs(out_path, dt_str)
    print(f"wrote {len(rows)} shadow rows to {out_path} (rescore={args.rescore})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
