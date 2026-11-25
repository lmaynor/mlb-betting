#!/usr/bin/env python3
"""MOD-03 item (b-i) acceptance proof: NFL board outcome matching BEFORE vs AFTER.

Rebuilds the NFL grading population from banked board archives in GCS
(``EVSweep/nfl_props/archive/{day}.json`` for ``2026-11-02..2026-11-24``, read
via ``mlb_core.storage``), runs ``nfl.data.outcome_matching.resolve_board_outcomes``
for season 2026, compares gradeable rows per stat family BEFORE (the audit's
``player_stats``-only join with hardcoded week lookup) vs AFTER (crosswalk +
schedules + snap_counts + rosters_weekly resolver), and writes the JSON report
to ``~/workspace/beezy/mod03-nfl-match-results.json``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mlb_core import storage  # noqa: E402
from nfl.data import read as nfl_read  # noqa: E402
from nfl.data.outcome_matching import (  # noqa: E402
    normalize_name,
    resolve_board_outcomes,
    summarize_resolution,
)

DAY_MIN = "2026-11-02"
DAY_MAX = "2026-11-24"
DEFAULT_OUT = os.path.expanduser("~/workspace/beezy/mod03-nfl-match-results.json")

# Audit's hardcoded stat column map and week lookup (the BEFORE baseline).
_AUDIT_STAT_MAP = {
    "pass_yds": "passing_yards",
    "pass_attempts": "attempts",
    "pass_completions": "completions",
    "pass_tds": "passing_tds",
    "interceptions": "passing_interceptions",
    "rush_yds": "rushing_yards",
    "rush_attempts": "carries",
    "rec_yds": "receiving_yards",
    "receptions": "receptions",
    "anytime_td": "anytd",
}


def _audit_nfl_week(day: str) -> int | None:
    """Exact week lookup used by audit.py / mod02_refit_grade.py (BEFORE)."""
    for lo, hi, wk in (
        ("2026-10-29", "2026-11-02", 9),
        ("2026-11-05", "2026-11-09", 10),
        ("2026-11-12", "2026-11-16", 11),
        ("2026-11-19", "2026-11-23", 12),
        ("2026-11-26", "2026-11-30", 13),
    ):
        if lo <= day <= hi:
            return wk
    return None


def load_archived_nfl_board(day_min: str = DAY_MIN, day_max: str = DAY_MAX) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load banked NFL board archives from GCS and dedupe (day, pnorm, stat, line)."""
    prefix = "EVSweep/nfl_props/archive/"
    keys = sorted(
        k
        for k in storage.list_keys(prefix)
        if k.endswith(".json") and day_min <= k.rsplit("/", 1)[-1].replace(".json", "") <= day_max
    )
    records: dict[tuple[str, str], dict[str, Any]] = {}
    archive_days: list[str] = []
    for key in keys:
        payload = json.loads(storage.read_bytes(key))
        day = str(payload.get("day") or key.rsplit("/", 1)[-1].replace(".json", ""))
        if not (day_min <= day <= day_max):
            continue
        archive_days.append(day)
        rows = payload.get("rows") or {}
        rows_iter = rows.items() if isinstance(rows, dict) else enumerate(rows)
        for row_key, r in rows_iter:
            if not r.get("has_line") or r.get("line") is None:
                continue
            records[(day, str(row_key))] = {
                "day": day,
                "game_date": day,
                "row_key": str(row_key),
                "player": r.get("player"),
                "pnorm": normalize_name(r.get("player")),
                "team": r.get("team"),
                "opp": r.get("opp"),
                "stat": r.get("stat"),
                "line": float(r["line"]),
                "side": r.get("side"),
                "game_key": r.get("game_key"),
                "p_over_model": r.get("p_over_model"),
                "p_over_market": r.get("p_over_market"),
            }

    raw_df = pd.DataFrame(records.values())
    if raw_df.empty:
        return raw_df, {"archive_keys": len(keys), "days": archive_days, "raw_has_line_rows": 0, "deduped_rows": 0}

    dedup_df = (
        raw_df.sort_values("side", kind="mergesort")
        .drop_duplicates(subset=["day", "pnorm", "stat", "line"], keep="first")
        .reset_index(drop=True)
    )
    meta = {
        "archive_keys": len(keys),
        "days": sorted(set(archive_days)),
        "raw_has_line_rows": int(len(raw_df)),
        "deduped_rows": int(len(dedup_df)),
    }
    return dedup_df, meta


def compute_before_audit_counts(board_df: pd.DataFrame, season: int = 2026) -> dict[str, Any]:
    """Replicate the audit's player_stats-only join (BEFORE) per stat family."""
    ps = nfl_read.load("player_stats", seasons=[season])
    if "season_type" in ps.columns:
        ps = ps[ps["season_type"] == "REG"].copy()
    ps["pnorm"] = ps["player_display_name"].map(normalize_name)
    ps["anytd"] = (ps["rushing_tds"].fillna(0) + ps["receiving_tds"].fillna(0)).astype(float)

    nfl_idx: dict[tuple[str, int], Any] = {}
    for rec in ps.itertuples(index=False):
        nfl_idx[(rec.pnorm, int(rec.week))] = rec

    by_stat: dict[str, dict[str, int]] = {}
    total_gradeable = 0
    total_non_push = 0
    for stat, sub in board_df.groupby("stat", sort=True):
        col = _AUDIT_STAT_MAP.get(str(stat))
        matched = 0
        gradeable = 0
        non_push = 0
        nomatch = 0
        for r in sub.itertuples(index=False):
            wk = _audit_nfl_week(r.day)
            rec = nfl_idx.get((r.pnorm, wk)) if wk else None
            if rec is None or col is None:
                nomatch += 1
                continue
            matched += 1
            val = getattr(rec, col, None)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            gradeable += 1
            if float(val) != float(r.line):
                non_push += 1
        by_stat[str(stat)] = {
            "total": int(len(sub)),
            "matched": matched,
            "gradeable": gradeable,
            "non_push_graded": non_push,
            "nomatch": nomatch,
        }
        total_gradeable += gradeable
        total_non_push += non_push

    return {
        "total_board_rows": int(len(board_df)),
        "total_gradeable": total_gradeable,
        "total_non_push_graded": total_non_push,
        "by_stat": by_stat,
    }


def build_report(day_min: str = DAY_MIN, day_max: str = DAY_MAX, season: int = 2026) -> dict[str, Any]:
    """Run BEFORE vs AFTER NFL outcome matching and assemble the report dict."""
    board_df, load_meta = load_archived_nfl_board(day_min=day_min, day_max=day_max)
    before = compute_before_audit_counts(board_df, season=season)

    resolved_df = resolve_board_outcomes(board_df, season=season)
    after_summary = summarize_resolution(resolved_df)

    comparison_by_stat: dict[str, dict[str, Any]] = {}
    for stat in sorted(set(before["by_stat"]) | set(after_summary["by_stat"])):
        b = before["by_stat"].get(stat, {"total": 0, "gradeable": 0, "non_push_graded": 0, "nomatch": 0})
        a = after_summary["by_stat"].get(stat, {"total": 0, "gradeable": 0, "clean_resolution_rate": 0.0, "counts": {}})
        comparison_by_stat[stat] = {
            "total_board_rows": a.get("total", b.get("total", 0)),
            "before_gradeable": b["gradeable"],
            "before_non_push_graded": b["non_push_graded"],
            "after_gradeable": a["gradeable"],
            "delta_gradeable": a["gradeable"] - b["gradeable"],
            "after_counts": a["counts"],
            "after_clean_resolution_rate": round(a["clean_resolution_rate"], 6),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "window": {"day_min": day_min, "day_max": day_max, **load_meta},
        "total_board_rows": after_summary["total"],
        "per_status_counts": after_summary["counts"],
        "clean_resolved": after_summary["clean_resolved"],
        "clean_resolution_rate": round(after_summary["clean_resolution_rate"], 6),
        "target_clean_resolution_rate": 0.98,
        "meets_target": after_summary["clean_resolution_rate"] >= 0.98,
        "after_gradeable_total": after_summary["gradeable"],
        "before_gradeable_total": before["total_gradeable"],
        "before": before,
        "after": after_summary,
        "by_stat_comparison": comparison_by_stat,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day-min", default=DAY_MIN)
    ap.add_argument("--day-max", default=DAY_MAX)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    report = build_report(day_min=args.day_min, day_max=args.day_max, season=args.season)
    out_path = Path(os.path.expanduser(args.out))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2)
    out_path.write_text(text + "\n")
    print(text)
    print(
        f"\nSummary: total={report['total_board_rows']} "
        f"clean_rate={report['clean_resolution_rate']:.4f} "
        f"gradeable_before={report['before_gradeable_total']} -> "
        f"gradeable_after={report['after_gradeable_total']} "
        f"wrote={out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
