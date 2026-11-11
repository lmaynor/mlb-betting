#!/usr/bin/env python3
"""
scripts/minutes_arm_t_backtest.py -- Arm T (text) historical backtest harness
for the NBA minutes-restriction program. CODE ONLY as shipped: the CLI is
DISABLED BY DEFAULT. Without --execute it prints the plan and exits without
constructing the Decisions client and without any HTTP call.

What --execute does (when Luc triggers it later):
  1. Classify the DEV-season cases (2021-22 -> 2023-24) with the Decisions
     API (one batched choice call per case, 20s hard timeout, fail closed:
     timeouts / HTTP errors / malformed responses are abstentions).
  2. Fit the band calibration + combiner shrinkage weight on dev.
  3. Classify the TEST-season cases (2024-25, 2025-26), grade Arm T vs
     Arm S on the shared cases against the pre-registered kill bar
     (>=10% relative AND >=0.4 min absolute MAE improvement at n>=150, and
     band log-loss better than the dev base-rate distribution).
  4. Write t_predictions.csv, dev_t_predictions.csv and grade_report.json
     to --out (local paths only; this runner does no GCS banking and no
     provisioning).

API key: read from env GEMINI_API_KEY at runtime only. In production fetch
it from GCP Secret Manager secret `gemini-api-key`, e.g.:
    GEMINI_API_KEY=$(gcloud secrets versions access latest \
        --secret=gemini-api-key) python3 scripts/minutes_arm_t_backtest.py ...

Exact trigger command (for later, do not run as part of the build):
    GEMINI_API_KEY=$(gcloud secrets versions access latest --secret=gemini-api-key) \
      python3 scripts/minutes_arm_t_backtest.py --execute \
        --dev-cases <dev_cases.parquet> --cases <test_cases.parquet> \
        --arm-s-dev <arm_s_dev.csv> --arm-s <arm_s_test.csv> \
        --prose <prose_corpus.jsonl> --out <out_dir>

Usage (plan mode, safe):
    python3 scripts/minutes_arm_t_backtest.py \
        --dev-cases <...> --cases <...> --arm-s-dev <...> --arm-s <...> \
        --prose <...> --out <...>
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("minutes_arm_t_backtest")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--dev-cases", required=True, help="dev-season case frame (parquet/CSV)")
    p.add_argument("--cases", required=True, help="test-season case frame (parquet/CSV)")
    p.add_argument("--arm-s-dev", required=True, help="Arm S predictions for dev cases")
    p.add_argument("--arm-s", required=True, help="Arm S predictions for test cases")
    p.add_argument("--prose", required=True, help="Lane 3 prose corpus JSONL")
    p.add_argument("--out", required=True, help="local output directory")
    p.add_argument("--max-blurbs", type=int, default=8)
    p.add_argument(
        "--execute",
        action="store_true",
        help="actually run the backtest (Decisions API calls). Default: print plan only.",
    )
    return p


def print_plan(args: argparse.Namespace) -> None:
    print("Arm T minutes-restriction backtest -- PLAN ONLY (no --execute).")
    print("No Decisions client constructed; no HTTP calls will be made.")
    print(f"  dev cases:        {args.dev_cases}")
    print(f"  test cases:       {args.cases}")
    print(f"  Arm S (dev):      {args.arm_s_dev}")
    print(f"  Arm S (test):     {args.arm_s}")
    print(f"  prose corpus:     {args.prose}")
    print(f"  output dir:       {args.out}")
    print(f"  max blurbs/case:  {args.max_blurbs}")
    print("Steps if executed: classify dev -> fit calibration + combiner "
          "weight on dev -> classify test -> grade vs the pre-registered "
          "kill bar -> write predictions + grade_report.json locally.")
    print("Re-run with --execute (and GEMINI_API_KEY set from Secret Manager "
          "secret 'gemini-api-key') to run it.")


def run(args: argparse.Namespace) -> int:
    from nba.minutes_restriction.arm_t import (
        classify_cases,
        fit_calibration,
        fit_shrinkage_weight,
        load_arm_s_predictions,
        load_cases,
        load_prose_corpus,
        normalize_arm_s,
    )
    from nba.minutes_restriction.decisions_client import (
        DecisionsBandClassifier,
        DecisionsConfigError,
    )
    from nba.minutes_restriction.grade_t import grade

    dev_cases = load_cases(args.dev_cases)
    test_cases = load_cases(args.cases)
    corpus = load_prose_corpus(args.prose)
    arm_s_dev = load_arm_s_predictions(args.arm_s_dev)
    arm_s_test = load_arm_s_predictions(args.arm_s)

    try:
        classifier = DecisionsBandClassifier()
    except DecisionsConfigError as exc:
        # Fail closed at the harness level: every case is an abstention,
        # the run still produces a grade report (INSUFFICIENT_N).
        log.error("%s", exc)
        classifier = None

    log.info("classifying %d dev cases", len(dev_cases))
    dev_t = classify_cases(dev_cases, corpus, classifier, max_blurbs=args.max_blurbs)
    calibration = fit_calibration(dev_cases, dev_t)

    weight = None
    dev_shared = dev_t[~dev_t["abstention"].fillna(True)]
    if len(dev_shared):
        from nba.minutes_restriction.arm_t import band_mapped_minutes
        from nba.minutes_restriction.decisions_client import BANDS

        frame = dev_cases.merge(dev_shared, on="case_id").merge(
            normalize_arm_s(arm_s_dev, dev_cases), on="case_id"
        )
        if len(frame):
            frame["t_pred_minutes"] = [
                band_mapped_minutes(
                    row.baseline_minutes,
                    {b: getattr(row, f"p_{b}") for b in BANDS},
                    calibration,
                )
                for row in frame.itertuples(index=False)
            ]
            weight = fit_shrinkage_weight(frame)

    log.info("classifying %d test cases", len(test_cases))
    test_t = classify_cases(test_cases, corpus, classifier, max_blurbs=args.max_blurbs)
    report = grade(test_cases, arm_s_test, test_t, calibration, combiner_weight=weight)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dev_t.to_csv(out / "dev_t_predictions.csv", index=False)
    test_t.to_csv(out / "t_predictions.csv", index=False)
    payload = report.to_dict()
    payload["calibration"] = {
        "mean_ratio_by_band": calibration.mean_ratio_by_band,
        "base_rates": calibration.base_rates,
        "n_by_band": calibration.n_by_band,
        "source": calibration.source,
    }
    (out / "grade_report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print_plan(args)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
