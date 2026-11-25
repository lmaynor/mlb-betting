#!/usr/bin/env python3
"""MOD-04 Cluster (g) weekly Sigma_role fit + Student-t ablation harness.

SHADOW-ONLY: combo weight c=0.00.
Per RUL-20261124, market-anchored marginals (recovered from served board quantiles)
are a serving-input fallback ONLY -- NOT training features. Prop lines and odds are
banned as training features and must never enter any fit.

U1 LEAK-PROOF GUARD: ONLY board archives with date < as_of and box scores with
game_date < as_of are used in fitting Sigma_role, cv_min, G1 ECE, and ablation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd

from mlb_core.data.player_names import norm_name
from nba.props.copula_combo import (
    ARCHETYPES,
    STATS_PRA,
    archetype_for_position,
    fit_sigma_role,
    gaussianize,
    pit_residual,
    recover_marginal_from_board_row,
    student_t_ablation,
)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_minutes(val) -> float:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return 0.0
    if ":" in s:
        mm, ss = s.split(":", 1)
        try:
            return float(mm) + float(ss) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _pick_col(cols: list[str], candidates: tuple[str, ...], required: bool = True) -> str | None:
    lower_map = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    if required:
        raise ValueError(f"Missing required column from {candidates}; found {cols}")
    return None


def _extract_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "predictions"):
            if key in payload:
                block = payload[key]
                if isinstance(block, list):
                    return [r for r in block if isinstance(r, dict)]
                if isinstance(block, dict):
                    return [r for r in block.values() if isinstance(r, dict)]
    return []


def _load_archives(archives_dir: Path, as_of: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for path in sorted(archives_dir.glob("*.json")):
        m = _DATE_RE.search(path.stem)
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        day = None
        if isinstance(payload, dict):
            day = payload.get("day") or payload.get("date")
        if not day and m:
            day = m.group(1)
        if not day or str(day)[:10] >= as_of:
            continue
        day_str = str(day)[:10]
        for row in _extract_rows(payload):
            sport = row.get("sport")
            if sport is not None and str(sport).lower() != "nba":
                continue
            out.append((day_str, row))
    return out


def _load_boxscores(box_path: Path, as_of: str) -> pd.DataFrame:
    df = pd.read_csv(box_path)
    cols = list(df.columns)
    c_name = _pick_col(cols, ("player_name", "name", "player"))
    c_pts = _pick_col(cols, ("pts", "points"))
    c_reb = _pick_col(cols, ("reb", "rebounds"))
    c_ast = _pick_col(cols, ("ast", "assists"))
    c_min = _pick_col(cols, ("min", "minutes"))
    c_pos = _pick_col(cols, ("pos", "position"), required=False)
    c_date = _pick_col(cols, ("game_date", "date"))
    c_team = _pick_col(cols, ("team_abbr", "team"), required=False)
    c_played = _pick_col(cols, ("played",), required=False)

    work = pd.DataFrame({
        "game_date": df[c_date].astype(str).str[:10],
        "pnorm": df[c_name].astype(str).map(norm_name),
        "points": pd.to_numeric(df[c_pts], errors="coerce"),
        "rebounds": pd.to_numeric(df[c_reb], errors="coerce"),
        "assists": pd.to_numeric(df[c_ast], errors="coerce"),
        "minutes_num": df[c_min].map(_parse_minutes),
        "position": df[c_pos].astype(str) if c_pos else "",
        "team": df[c_team].astype(str).str.upper() if c_team else "",
        "played_flag": df[c_played] if c_played else True,
    })
    # U1 strict cutoff: only game_date < as_of
    work = work[work["game_date"] < as_of].copy()
    work = work[work["pnorm"] != ""]
    played_ok = work["minutes_num"] > 0.0
    if c_played:
        played_ok = played_ok & (work["played_flag"].astype(str).str.lower().isin(("true", "1", "yes")))
    work = work[played_ok].dropna(subset=["points", "rebounds", "assists"]).copy()
    work["archetype"] = work["position"].map(archetype_for_position)
    return work


def _ece_equal_count(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    n = len(p)
    if n == 0:
        return 0.0
    order = np.argsort(p, kind="mergesort")
    bins = np.array_split(order, min(n_bins, n))
    ece = 0.0
    for idx in bins:
        if len(idx) == 0:
            continue
        ece += (len(idx) / n) * abs(float(np.mean(y[idx])) - float(np.mean(p[idx])))
    return float(ece)


def _deterministic_rng(date_str: str, player_norm: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{date_str}|{player_norm}".encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "little")
    return np.random.default_rng(seed)


def fit_mod04g(archives_dir: Path, box_path: Path, as_of: str) -> dict:
    archive_rows = _load_archives(archives_dir, as_of)
    box = _load_boxscores(box_path, as_of)

    # Index boxscore rows by (pnorm, game_date)
    box_idx: dict[tuple[str, str], list[dict]] = {}
    for rec in box.to_dict(orient="records"):
        box_idx.setdefault((rec["pnorm"], rec["game_date"]), []).append(rec)

    def _match_box(pn: str, day_str: str, team: str | None) -> dict | None:
        base = pd.Timestamp(day_str)
        for off in (0, 1, -1):
            dt = (base + pd.Timedelta(days=off)).strftime("%Y-%m-%d")
            if dt >= as_of:
                continue
            cands = box_idx.get((pn, dt), [])
            if not cands:
                continue
            if team:
                for c in cands:
                    if c["team"] == team.upper():
                        return c
            return cands[0]
        return None

    # Group archive rows per (date, player_norm) for PRA joint residuals and collect ECE rows
    grouped: dict[tuple[str, str], dict] = {}
    ece_rows: list[tuple[str, str, float, float]] = []

    for day_str, row in archive_rows:
        stat = str(row.get("stat", "")).strip().lower()
        if stat not in STATS_PRA:
            continue
        pn = row.get("player_norm") or norm_name(str(row.get("player", "")))
        if not pn:
            continue
        team = str(row.get("team") or "").upper() or None
        matched = _match_box(pn, day_str, team)
        if matched is None:
            continue

        arch = matched["archetype"]
        if (not matched["position"] or matched["position"] == "nan") and row.get("position"):
            arch = archetype_for_position(str(row["position"]))

        # G1 ECE validation row (main-line probability where a line exists)
        line = row.get("line")
        has_line = row.get("has_line", True)
        prob = row.get("p_over_blend")
        if prob is None:
            prob = row.get("p_over_model")
        if has_line and line is not None and prob is not None:
            actual_val = float(matched[stat])
            line_f = float(line)
            if actual_val != line_f:
                y_ind = 1.0 if actual_val > line_f else 0.0
                ece_rows.append((stat, arch, float(prob), y_ind))

        if row.get("proj_mean") is None:
            continue
        try:
            marg = recover_marginal_from_board_row(row)
        except (TypeError, ValueError):
            continue

        key = (day_str, pn)
        entry = grouped.setdefault(
            key,
            {
                "date": day_str,
                "player_norm": pn,
                "archetype": arch,
                "marginals": {},
                "actual": (
                    int(round(float(matched["points"]))),
                    int(round(float(matched["rebounds"]))),
                    int(round(float(matched["assists"]))),
                ),
            },
        )
        if stat not in entry["marginals"]:
            entry["marginals"][stat] = marg

    records: list[dict] = []
    for (day_str, pn), entry in sorted(grouped.items()):
        margs = entry["marginals"]
        if not all(s in margs for s in STATS_PRA):
            continue
        rng = _deterministic_rng(day_str, pn)
        z_vals = []
        for y_val, stat in zip(entry["actual"], STATS_PRA):
            u = pit_residual(y_val, margs[stat], rng=rng)
            z_vals.append(float(gaussianize(u)))
        records.append({
            "date": day_str,
            "player_norm": pn,
            "archetype": entry["archetype"],
            "z": tuple(z_vals),
            "marginals": margs,
            "actual": entry["actual"],
        })

    sigma_by_arch, sigma_counts = fit_sigma_role(records)

    # Estimate cv_min over rotation players (minutes >= 10) before as_of, shrunk toward global CV (weight 200)
    rot = box[box["minutes_num"] >= 10.0]
    if len(rot) > 1 and rot["minutes_num"].mean() > 0:
        cv_global = float(rot["minutes_num"].std(ddof=1) / rot["minutes_num"].mean())
    else:
        cv_global = 0.18

    cv_min: dict[str, float] = {}
    for arch in ARCHETYPES:
        sub = rot[rot["archetype"] == arch]
        n_a = len(sub)
        if n_a > 1 and sub["minutes_num"].mean() > 0:
            cv_raw = float(sub["minutes_num"].std(ddof=1) / sub["minutes_num"].mean())
            cv_min[arch] = float((n_a * cv_raw + 200.0 * cv_global) / (n_a + 200.0))
        else:
            cv_min[arch] = float(cv_global)
    cv_min["*"] = float(cv_global)

    # G1 ECE per (stat, archetype), 10 equal-count bins; n < 100 falls back to (stat, '*')
    ece_by_cell: dict[str, float] = {}
    for stat in STATS_PRA:
        s_rows = [(p, y) for st, _, p, y in ece_rows if st == stat]
        if s_rows:
            p_arr = np.array([r[0] for r in s_rows], dtype=float)
            y_arr = np.array([r[1] for r in s_rows], dtype=float)
            ece_by_cell[f"{stat}|*"] = _ece_equal_count(p_arr, y_arr, n_bins=10)
        for arch in ARCHETYPES:
            c_rows = [(p, y) for st, ar, p, y in ece_rows if st == stat and ar == arch]
            if len(c_rows) >= 100:
                p_arr = np.array([r[0] for r in c_rows], dtype=float)
                y_arr = np.array([r[1] for r in c_rows], dtype=float)
                ece_by_cell[f"{stat}|{arch}"] = _ece_equal_count(p_arr, y_arr, n_bins=10)

    # Walk-forward tail: most recent 7 days of fitted records
    if records:
        max_date = max(r["date"] for r in records)
        cutoff_7d = (pd.Timestamp(max_date) - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
        tail_records = [r for r in records if r["date"] >= cutoff_7d]
    else:
        tail_records = []

    ablation = student_t_ablation(tail_records, sigma_by_arch, cv_min)

    return {
        "as_of": as_of,
        "sigma_role": {k: v.tolist() for k, v in sigma_by_arch.items()},
        "sigma_counts": {k: int(v) for k, v in sigma_counts.items()},
        "cv_min": cv_min,
        "ece_by_cell": ece_by_cell,
        "ece_record_source": (
            "MOD-02/MOD-03 banked all-rows archive record (served blend marginals), "
            "archives < as_of joined to box-score masters"
        ),
        "ablation": ablation,
        "n_residual_records": len(records),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MOD-04 Cluster (g) Sigma_role fit + Student-t ablation")
    ap.add_argument("--archives-dir", required=True, type=Path)
    ap.add_argument("--boxscores", required=True, type=Path)
    ap.add_argument("--as-of", required=True, help="Exclusive cutoff YYYY-MM-DD (U1 leak-proof)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    artifact = fit_mod04g(args.archives_dir, args.boxscores, args.as_of)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2)
    print(json.dumps({
        "as_of": artifact["as_of"],
        "n_residual_records": artifact["n_residual_records"],
        "sigma_counts": artifact["sigma_counts"],
        "adopt_student_t": artifact["ablation"].get("adopt_student_t"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
