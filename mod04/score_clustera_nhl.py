"""MOD-04 Cluster (a) Shadow Scorer CLI for NHL blocked shots.

Reads a board JSON (``EVSweep/nhl_props/projections_latest.json`` shape),
scores every ``blocked_shots`` row strictly using pre-cutoff features
(``cutoff_utc = game commence``, or ``now_utc`` in ``--rescore`` mode for
games locking within 75 minutes, never after commence), optionally refreshes
Rotowire daily lineup files from GCS via ``gcloud storage cp``, computes the
U4 market-anchored shadow probability via plain-NB (``pi = 0.0``) 5-point
Gauss-Hermite quadrature, and emits JSONL rows locally and optionally to GCS:
    ``gs://concrete-crow-445205-m4-mlb-data/MOD04/cluster_a/shadow/dt=<date>/nhl.jsonl``.

Never touches ``ev/daily_projections.py`` or any serving path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core.data.player_names import norm_name
from mod04.feature_store_nhl import _norm_team, build_pregame_features_nhl
from mod04.hurdle_nb import p_over as hurdle_p_over
from mod04.score_clustera import (
    _extract_board_served_over,
    _extract_line,
    _extract_market_prob,
    _load_existing_timestamps,
    _to_utc,
    _u4_blend,
    _write_jsonl,
)
from mod04.train_clustera_nhl import load_bundle, predict_params

log = logging.getLogger("mod04.score_clustera_nhl")

GCS_BUCKET = "concrete-crow-445205-m4-mlb-data"
GCS_SHADOW_PREFIX = "MOD04/cluster_a/shadow"
GCS_ROTOWIRE_PREFIX = "NHL/lineups/rotowire"
GCLOUD_BIN = Path.home() / "google-cloud-sdk/bin/gcloud"
RESCORE_WINDOW_MIN = 75.0
ENGINE_NAME = "mod04_nhl_blocked_shots_shadow_v1"
_ET = ZoneInfo("America/New_York")


def _fetch_rotowire_gcloud(data_dir: str | Path, et_dates: set[str]) -> None:
    """Download Rotowire daily lineup JSONs for ``et_dates`` via ``gcloud storage cp`` (fail-open)."""
    root = Path(data_dir) / "NHL" / "lineups" / "rotowire"
    root.mkdir(parents=True, exist_ok=True)
    for dt_str in sorted(et_dates):
        src = f"gs://{GCS_BUCKET}/{GCS_ROTOWIRE_PREFIX}/{dt_str}.json"
        dst = root / f"{dt_str}.json"
        try:
            subprocess.run(
                [str(GCLOUD_BIN), "storage", "cp", src, str(dst)],
                check=True,
                capture_output=True,
                text=True,
            )
            log.info("Fetched Rotowire lineup snapshot %s", src)
        except Exception as exc:  # noqa: BLE001 -- fail-open if daily file not yet posted
            log.info("Rotowire snapshot %s unavailable (%s)", src, exc)


def score_board(
    board_payload: dict[str, Any] | list[dict[str, Any]],
    bundle_dir: str | Path,
    data_dir: str | Path,
    *,
    rescore: bool = False,
    now_utc: str | pd.Timestamp | None = None,
    existing_jsonl: Path | None = None,
    fetch_rotowire: bool = False,
) -> list[dict[str, Any]]:
    """Score NHL ``blocked_shots`` rows on ``board_payload`` and return shadow JSONL dicts."""
    raw_rows = board_payload.get("rows", []) if isinstance(board_payload, dict) else list(board_payload)
    blk_rows = [r for r in raw_rows if str(r.get("stat", "")).lower() == "blocked_shots"]
    if not blk_rows:
        return []

    bundle = load_bundle(bundle_dir)
    man = bundle["manifest"]
    bundle_ver = str(man["version"])
    now_ts = _to_utc(now_utc) if now_utc is not None else pd.Timestamp(datetime.now(timezone.utc))
    scored_at_str = now_ts.isoformat()

    prior_ts_map = _load_existing_timestamps(existing_jsonl) if (rescore and existing_jsonl is not None) else {}

    selected: list[dict[str, Any]] = []
    for r in blk_rows:
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
        gk_str = str(r.get("game_key", ""))
        gk_parts = gk_str.split(":")
        et_date = gk_parts[1] if len(gk_parts) >= 2 and len(gk_parts[1]) == 10 else comm_ts.tz_convert(_ET).strftime("%Y-%m-%d")
        selected.append({
            **r,
            "_comm_ts": comm_ts,
            "_et_date": et_date,
            "_line": line_val,
            "_team": _norm_team(r.get("team")),
            "_opp": _norm_team(r.get("opp")),
            "_pnorm": norm_name(str(r.get("player_norm") or r.get("player") or "")),
        })

    if not selected:
        return []

    if fetch_rotowire:
        _fetch_rotowire_gcloud(data_dir, {it["_et_date"] for it in selected})

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in selected:
        cutoff_ts = min(now_ts, item["_comm_ts"]) if rescore else item["_comm_ts"]
        groups.setdefault((item["_et_date"], cutoff_ts.isoformat()), []).append(item)

    shadow_rows: list[dict[str, Any]] = []
    art = bundle["models"]["blocked_shots"]
    blend_cfg = man.get("blend", {}).get("blocked_shots", {"a": 0.0, "b": 1.0, "c": 0.0})

    for (t_date, cut_iso), items in groups.items():
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
        feat_df = build_pregame_features_nhl(t_date, cut_iso, data_dir, slate=slate_df)
        if feat_df.empty:
            continue

        feat_lookup: dict[tuple[str, str], int] = {}
        for idx, fr in enumerate(feat_df.itertuples(index=False)):
            feat_lookup[(str(fr.team), str(fr.player_norm))] = idx

        preds = predict_params(art, feat_df)

        for it in items:
            idx = feat_lookup.get((it["_team"] or "", it["_pnorm"]))
            if idx is None:
                continue
            frow = feat_df.iloc[idx]
            prow = preds.iloc[idx]

            mu_ev = float(frow["mu_ev_toi"])
            mu_pk = float(frow["mu_pk_toi"])
            mu_exp = float(frow["mu_exp_toi"])
            sd_exp = float(frow["sigma_exp_toi"])
            lam_pm = float(prow["lambda_per_min"])
            lam_60 = float(prow["lambda_per60"])
            alp_val = float(prow["alpha"])
            line_val = float(it["_line"])

            # Plain-NB (pi=0.0) over 5-point Gauss-Hermite TOI quadrature
            p_mod = float(hurdle_p_over(line_val, 0.0, lam_pm, alp_val, mu_exp, sd_exp))
            p_mkt = _extract_market_prob(it)
            p_board = _extract_board_served_over(it)
            p_shadow = _u4_blend(p_mkt, p_mod, blend_cfg)

            orig_key = (str(it.get("game_key", "")), it["_pnorm"], "blocked_shots", line_val)
            rescore_of = prior_ts_map.get(orig_key, scored_at_str) if rescore else None

            shadow_rows.append({
                "date": t_date,
                "game_key": str(it.get("game_key", "")),
                "player": str(it["player"]),
                "team": it["_team"],
                "opp": it["_opp"],
                "stat": "blocked_shots",
                "line": round(line_val, 2),
                "p_market": None if p_mkt is None else round(p_mkt, 6),
                "p_board_served": None if p_board is None else round(p_board, 6),
                "p_model": round(p_mod, 6),
                "p_served_shadow": round(p_shadow, 6),
                "lambda_per60": round(lam_60, 6),
                "alpha": round(alp_val, 6),
                "mu_ev_toi": round(mu_ev, 3),
                "mu_pk_toi": round(mu_pk, 3),
                "engine": ENGINE_NAME,
                "bundle_version": bundle_ver,
                "scored_at_utc": scored_at_str,
                "rescore_of": rescore_of,
            })

    return shadow_rows


def _upload_shadow_gcs(local_path: Path, dt_str: str) -> str:
    key = f"{GCS_SHADOW_PREFIX}/dt={dt_str}/nhl.jsonl"
    if os.environ.get("MLB_GCS_BUCKET") or os.environ.get("GCS_BUCKET"):
        from mlb_core import storage

        storage.write_bytes(local_path.read_bytes(), key)
    else:
        subprocess.run([str(GCLOUD_BIN), "storage", "cp", str(local_path), f"gs://{GCS_BUCKET}/{key}"], check=True)
    return f"gs://{GCS_BUCKET}/{key}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score NHL blocked shots board in shadow mode.")
    ap.add_argument("--board", required=True, help="Path to board JSON (EVSweep/nhl_props/projections_latest.json shape)")
    ap.add_argument("--bundle-dir", required=True, help="Bundle directory or parent directory with latest.json")
    ap.add_argument("--data-dir", default="data/mod04", help="Feature data directory")
    ap.add_argument("--out", default=None, help="Output JSONL path")
    ap.add_argument("--rescore", action="store_true", help="Pre-lock rescore mode (games locking <= 75m)")
    ap.add_argument("--now-utc", default=None, help="Override UTC timestamp (ISO) for deterministic runs")
    ap.add_argument("--no-fetch-rotowire", action="store_true", help="Skip gcloud download of Rotowire JSON")
    ap.add_argument("--write-gcs", action="store_true", help="Upload shadow JSONL to GCS")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    board = json.loads(Path(args.board).read_text())
    dt_raw = board.get("date")
    if not dt_raw and board.get("ts"):
        dt_raw = _to_utc(board["ts"]).tz_convert(_ET).strftime("%Y-%m-%d")
    dt_str = str(dt_raw or datetime.now(_ET).strftime("%Y-%m-%d"))[:10]
    out_path = Path(args.out) if args.out else Path(args.data_dir) / f"shadow/dt={dt_str}/nhl.jsonl"

    rows = score_board(
        board,
        args.bundle_dir,
        args.data_dir,
        rescore=args.rescore,
        now_utc=args.now_utc,
        existing_jsonl=out_path,
        fetch_rotowire=not args.no_fetch_rotowire,
    )
    _write_jsonl(rows, out_path, append=args.rescore)
    if args.write_gcs:
        _upload_shadow_gcs(out_path, dt_str)
    print(f"wrote {len(rows)} shadow rows to {out_path} (rescore={args.rescore})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
