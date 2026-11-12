"""Arm S + B1 (blowout minutes) experiment -- GUARDED CLI.

    python scripts/minutes_arm_s_b1_backtest.py --box-path <file|dir> --spread-dir <dir> \
        [--out-json report.json] [--out-md report.md] [--execute]

DISABLED BY DEFAULT, same guard as scripts/minutes_arm_s_backtest.py: without --execute
this prints the run plan and exits WITHOUT reading any data file. With --execute it:

  1. loads hoopR player box scores and the BettingPros game-line SPREAD partitions
     (Odds/bettingpros_nba_games/spread/{day}.parquet, per-book rows) from LOCAL paths;
  2. builds Arm S cases (nba.minutes_restriction.cases; no reports path -- the official-
     report designation backfill is a separate lane, and B1's features do not use
     designations, so the population is box-score return cases + game-2);
  3. computes the consensus CLOSING spread per game (blowout.consensus_closing_spreads:
     fresh near-tip quotes, modal line, >= 3 books) and attaches it to every case --
     PREGAME spread only, never realized margin (Argon's B1 nitpick);
  4. runs the UNMODIFIED Arm S harness (arm_s.walk_forward) on the spread-matched cases
     (the "before") and the B1 arm (blowout.walk_forward_b1: + closing_spread,
     abs_closing_spread, abs_spread_x_starter, shrunk team pull-speed fit on dev
     seasons only) on the identical cases (the "after");
  5. grades before vs after (blowout.grade_b1) against Argon's auto-kill bars:
     band-mapped MAE improvement >= 0.15 min overall AND >= 0.35 min at |spread| >= 9.5
     on the pooled test seasons 2024-25 / 2025-26, primary population = non-game-2 cases.

Inputs are local files/dirs only; GCS paths are NOT read implicitly (export first with
`gcloud storage cp`), so a run is always an explicit, inspectable act.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from nba.minutes_restriction import arm_s, blowout, cases  # noqa: E402


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


def load_spread_quotes(path: str) -> pd.DataFrame:
    """BettingPros game-line spread quotes (per-book rows) from a local file or dir."""
    return _read_frame(path)


def run_plan(args) -> str:
    return "\n".join([
        "Arm S + B1 blowout-minutes experiment -- EXECUTION DISABLED (no --execute flag).",
        "Nothing was read, fit, or written. This is the plan only:",
        f"  1. Load hoopR player box scores from --box-path={args.box_path!r}",
        f"  2. Load BettingPros game-line spread quotes from --spread-dir={args.spread_dir!r}",
        "  3. Build Arm S cases (box-score return cases + game-2; no designation reports).",
        "  4. Consensus closing spread per game (fresh near-tip book quotes, modal line,",
        "     >= 3 books); attach to cases from the case team's perspective. Pregame only.",
        f"  5. BEFORE: unmodified Arm S (Ridge, alpha={arm_s.RIDGE_ALPHA}) fit on dev seasons",
        f"     {list(arm_s.DEV_SEASONS)} only, graded on test seasons {list(arm_s.TEST_SEASONS)}.",
        "  6. AFTER: same protocol + B1 features (closing_spread, abs_closing_spread,",
        "     abs_spread_x_starter, shrunk team pull-speed from dev seasons only).",
        "  7. Grade (blowout.grade_b1): PASS only if band-mapped MAE improves >= 0.15 min",
        "     overall AND >= 0.35 min in the |spread| >= 9.5 cut; otherwise AUTO-KILLED.",
        f"  8. Write JSON to {args.out_json!r} and markdown to {args.out_md!r} (when given).",
        "Re-run with --execute to perform the run.",
    ])


def run_experiment(box: pd.DataFrame, quotes: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Box + spread quotes -> (B1 grading report dict, pull-speed diagnostics frame)."""
    spreads = blowout.consensus_closing_spreads(quotes)
    built = cases.build_cases(box, None)
    attached = blowout.attach_closing_spread(built, spreads)
    matched = attached[pd.to_numeric(attached["closing_spread"], errors="coerce").notna()].copy()
    before = arm_s.walk_forward(matched)
    after = blowout.walk_forward_b1(matched, box, spreads)
    report = blowout.grade_b1(before, after)
    report["n_cases"] = int(len(built))
    report["n_cases_spread_matched"] = int(len(matched))
    report["n_games_with_spread"] = int(len(spreads))
    report["n_predictions"] = int(len(after))
    report["before_fit_seasons"] = before.attrs.get("fit_seasons")
    diag = after.attrs.get("pull_speed_diag")
    return report, (diag if diag is not None else pd.DataFrame())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Arm S + B1 blowout-minutes experiment (guarded).")
    ap.add_argument("--box-path", help="local file/dir of hoopR player box scores (.parquet/.csv)")
    ap.add_argument("--spread-dir", help="local file/dir of BettingPros spread quotes (.parquet/.csv)")
    ap.add_argument("--out-json", help="write the B1 grading report dict as JSON here")
    ap.add_argument("--out-md", help="write the B1 grading report as markdown here")
    ap.add_argument("--execute", action="store_true",
                    help="actually run the experiment (without this flag: print the plan, read nothing)")
    args = ap.parse_args(argv)

    if not args.execute:
        print(run_plan(args))
        return 0

    if not args.box_path or not args.spread_dir:
        ap.error("--execute requires --box-path and --spread-dir")
    box = load_box_scores(args.box_path)
    quotes = load_spread_quotes(args.spread_dir)
    report, diag = run_experiment(box, quotes)
    if args.out_json:
        payload = dict(report)
        payload["pull_speed_diag"] = diag.to_dict("records") if len(diag) else []
        Path(args.out_json).write_text(json.dumps(payload, indent=1, default=str))
    md = blowout.render_markdown_b1(report)
    if args.out_md:
        Path(args.out_md).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
