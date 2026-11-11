"""Arm S (structured) minutes-restriction backtest -- GUARDED CLI.

    python scripts/minutes_arm_s_backtest.py --box-path <file|dir> [--reports-path <file|dir>] \
        [--out-json report.json] [--out-md report.md] [--execute]

DISABLED BY DEFAULT (Luc, 2026-11-11: "do the build but don't trigger the run yet"). Without
--execute this prints the run plan and exits WITHOUT reading any data file: the loaders below are
only called on the --execute path. With --execute it loads the hoopR player box scores and (when
given) the banked official injury reports from LOCAL paths, builds cases
(nba.minutes_restriction.cases), fits Arm S on dev seasons 2021-22..2023-24 only, grades test
seasons 2024-25 / 2025-26 against the pre-registered bars, and writes the JSON + markdown reports.

Inputs are local files/dirs of .parquet or .csv (a dir is globbed recursively). GCS paths are NOT
read implicitly: export the frames locally first, so a run is always an explicit, inspectable act.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from nba.minutes_restriction import arm_s, cases, grade  # noqa: E402


def _read_frame(path: str) -> pd.DataFrame:
    p = Path(path)
    files = sorted(p.rglob("*")) if p.is_dir() else [p]
    frames = []
    for f in files:
        if f.suffix == ".parquet":
            frames.append(pd.read_parquet(f))
        elif f.suffix == ".csv":
            frames.append(pd.read_csv(f))
    if not frames:
        raise ValueError(f"no .parquet/.csv data under {path}")
    return pd.concat(frames, ignore_index=True)


def load_box_scores(path: str) -> pd.DataFrame:
    """hoopR player box scores from a local file or directory of parquet/csv."""
    return _read_frame(path)


def load_designations(path: str) -> pd.DataFrame:
    """Banked official injury reports (nba_injury_report COLUMNS) from a local file or directory."""
    return _read_frame(path)


def run_plan(args) -> str:
    return "\n".join([
        "Arm S minutes-restriction backtest -- EXECUTION DISABLED (no --execute flag).",
        "Nothing was read, fit, or written. This is the plan only:",
        f"  1. Load hoopR player box scores from --box-path={args.box_path!r}",
        f"  2. Load official injury reports from --reports-path={args.reports_path!r} "
        "(optional; without them only box-score return cases are built)",
        "  3. Build cases (nba.minutes_restriction.cases): absence spells bounded by played games,",
        "     baseline = mean of last 8 played minutes (>= 5 required, rotation >= 15 mpg),",
        "     COACH_DECISION spells excluded, designation frozen at the last report >= 60 min pre-tip.",
        f"  4. Fit Arm S (Ridge, alpha={arm_s.RIDGE_ALPHA}) on dev seasons "
        f"{list(arm_s.DEV_SEASONS)} ONLY; no test-season case enters any fit set.",
        f"  5. Grade test seasons {list(arm_s.TEST_SEASONS)} separately and pooled against the bars:",
        "     missed >= 4 injury MAE cut >= 15%; full population MAE cut >= 10%;",
        "     calibration |mean predicted - mean realized ratio| <= 0.05 per missed band.",
        f"  6. Write JSON to {args.out_json!r} and markdown to {args.out_md!r} (when given).",
        "Re-run with --execute to perform the run.",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Arm S minutes-restriction backtest (guarded).")
    ap.add_argument("--box-path", help="local file/dir of hoopR player box scores (.parquet/.csv)")
    ap.add_argument("--reports-path", help="local file/dir of banked official injury reports")
    ap.add_argument("--out-json", help="write the grading report dict as JSON here")
    ap.add_argument("--out-md", help="write the grading report as markdown here")
    ap.add_argument("--execute", action="store_true",
                    help="actually run the backtest (without this flag: print the plan, read nothing)")
    args = ap.parse_args(argv)

    if not args.execute:
        print(run_plan(args))
        return 0

    if not args.box_path:
        ap.error("--execute requires --box-path")
    box = load_box_scores(args.box_path)
    reports = load_designations(args.reports_path) if args.reports_path else None
    built = cases.build_cases(box, reports)
    predictions = arm_s.walk_forward(built)
    report = grade.grade(predictions)
    report["n_cases"] = int(len(built))
    report["n_predictions"] = int(len(predictions))
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(report, indent=1, default=str))
    md = grade.render_markdown(report)
    if args.out_md:
        Path(args.out_md).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
