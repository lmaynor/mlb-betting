#!/usr/bin/env python3
"""
scripts/minutes_arm_t_run.py -- Arm T (text) runner for the NBA
minutes-restriction program, built on the Q4/Q5 prose loader
(nba.minutes_restriction.prose_loader; Argon's 2026-11-11/12 rulings).

GUARDED, like every runner in this program:

- No mode flag: prints the plan and exits. Nothing is read, no client is
  constructed, no HTTP call is made.
- --smoke: runs the LOADER ONLY over the cases whose player is banked in
  the chosen corpus (decision-time resolution + Q4 slicing + cleansing
  audit) and writes case_prose_audit.csv, prose_inputs.jsonl and
  smoke_summary.json to --out. No classifier calls unless --classify N is
  also given.
- --classify N (with --smoke): additionally sends the first N
  non-abstaining cases (N <= 10, a hard cap: smoke volume only) through
  the Decisions classifier. Requires GEMINI_API_KEY in the environment
  (in production: Secret Manager secret 'gemini-api-key'). Writes
  classify_results.json.
- --graded: the registered graded run (fit 2021-22 -> 2023-24, judge
  pooled 2024-25 + 2025-26 against the Q1 bars in arm_t_config). It
  REFUSES (exit 2) unless the corpus is the graded corpus (nbc) AND the
  corpus-completeness check passes: every resolved player in the player
  map banked. Without --execute, it reports completeness and exits.
  With --execute, it asserts the canonical frame lock (SHA-256 + rows/cohort),
  verifies the amended Q4.2 Arm S cohort gate against --arm-s-export
  (membership/outcome/designation invariance + canonical re-grade pins),
  slices prose, preflights cohort honesty, runs parallel classification via
  --workers, fits dev calibration and shrinkage, grades test against the
  tightened Q1 bars, writes artifacts (grade_artifact.json, etc.), and prints
  the verdict line.

The corpus is a config switch: --corpus nbc reads the NBC Sports archive
(parsed/ + mentions/ + the NBC player map); --corpus rotowire reads the
RotoWire archive (parsed/ + its player map) as the dev harness. Case set
and decision times are identical across corpora.

Inputs are LOCAL paths (export frames from GCS first, as with the Arm S
CLI): the case frame is the build_cases frame (parquet/CSV, with tip_ts
and decision_report_ts); the official reports are the banked
{date}/report_*.csv tree.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("minutes_arm_t_run")

MAX_CLASSIFY = 10


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--corpus", choices=["nbc", "rotowire"], required=True)
    p.add_argument("--parsed-dir", required=True, help="local parsed JSONL dir of the corpus")
    p.add_argument("--mentions-dir", help="NBC mentions JSONL dir (nbc corpus only)")
    p.add_argument("--player-map", required=True, help="player map CSV for the corpus")
    p.add_argument("--cases", help="case frame (parquet/CSV) with tip_ts + decision_report_ts")
    p.add_argument("--reports-dir", help="banked official reports root ({date}/report_*.csv)")
    p.add_argument("--spell-file", help="optional team-calendar case JSON with spell_start per return case")
    p.add_argument("--arm-s-export", help="path to Arm S predictions_export.csv for cohort verification")
    p.add_argument("--workers", type=int, default=12, help="worker threads for classification (default 12)")
    p.add_argument("--out", help="output directory (smoke/graded artifacts)")
    p.add_argument("--smoke", action="store_true", help="run the loader over corpus-covered cases")
    p.add_argument("--classify", type=int, default=0,
                   help="with --smoke: classify the first N non-abstaining cases (<= 10)")
    p.add_argument("--graded", action="store_true",
                   help="the registered graded run; refuses unless the corpus is complete")
    p.add_argument("--execute", action="store_true",
                   help="with --graded and the gate open: actually run the graded backtest")
    return p


def print_plan(args: argparse.Namespace) -> None:
    print("Arm T minutes-restriction runner -- PLAN ONLY (no --smoke / --graded).")
    print("No corpus or case file was read; no Decisions client constructed.")
    print(f"  corpus:        {args.corpus} (parsed: {args.parsed_dir})")
    print(f"  player map:    {args.player_map}")
    print(f"  cases:         {args.cases}")
    print(f"  reports:       {args.reports_dir}")
    print("Modes: --smoke runs the Q4/Q5 loader over corpus-covered cases; "
          "--graded refuses unless the NBC corpus is complete over the "
          "resolved player map.")


# ---------------------------------------------------------------------------
# Shared loading helpers
# ---------------------------------------------------------------------------

def _load_reports_for_dates(reports_dir: str, game_dates: set) -> "pd.DataFrame":
    """Read the banked report CSVs that can cover the given game dates.

    A game's reports are published on its own date and the evening before,
    so only those two directory days are read per game date.
    """
    import pandas as pd

    root = Path(reports_dir)
    frames = []
    wanted_dirs = set()
    for d in game_dates:
        wanted_dirs.add(d.isoformat())
        from datetime import timedelta

        wanted_dirs.add((d - timedelta(days=1)).isoformat())
    for dirname in sorted(wanted_dirs):
        day_dir = root / dirname
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("report_*.csv")):
            frames.append(pd.read_csv(f))
    if not frames:
        raise ValueError(f"no report CSVs found under {reports_dir} for the case dates")
    df = pd.concat(frames, ignore_index=True)
    df["_game_date"] = pd.to_datetime(df["game_date"]).dt.date
    return df[df["_game_date"].isin(game_dates)].drop(columns=["_game_date"]).reset_index(drop=True)


def _load_spell_starts(spell_file: str | None) -> dict:
    """(name_key, return_date) -> spell_start Timestamp (UTC), when given."""
    if not spell_file:
        return {}
    import pandas as pd

    from nba.minutes_restriction.cases import _norm_name

    records = json.loads(Path(spell_file).read_text(encoding="utf-8"))
    out = {}
    for rec in records:
        out[(_norm_name(rec["player"]), rec["return_date"])] = pd.Timestamp(
            rec["spell_start"], tz="UTC"
        )
    return out


# ---------------------------------------------------------------------------
# Smoke: loader machinery over corpus-covered real cases
# ---------------------------------------------------------------------------

def run_smoke(args: argparse.Namespace) -> int:
    import pandas as pd

    from nba.minutes_restriction import cases as cases_mod
    from nba.minutes_restriction.arm_t import load_cases
    from nba.minutes_restriction.prose_loader import (
        build_stamp_calendar,
        load_corpus,
        predict_case,
        resolve_decision_time,
        slice_case_prose,
        strata_cluster_keys,
    )

    if args.classify > MAX_CLASSIFY:
        print(f"REFUSED: --classify is capped at {MAX_CLASSIFY} (smoke volume only).")
        return 2
    if not args.cases or not args.reports_dir or not args.out:
        print("REFUSED: --smoke requires --cases, --reports-dir and --out.")
        return 2

    corpus = load_corpus(args.corpus, args.parsed_dir, args.mentions_dir, args.player_map)
    clusters = strata_cluster_keys(corpus)
    log.info("corpus %s: %d files, %d rows, %d banked players, %d strata-cluster keys",
             corpus.name, corpus.n_files, corpus.n_rows,
             len(corpus.banked_players()), len(clusters))

    frame = load_cases(args.cases)
    frame["game_date"] = pd.to_datetime(frame["game_date"]).dt.date
    covered = frame[
        frame["player"].map(lambda p: cases_mod._norm_name(p)).isin(corpus.banked_players())
    ].copy()
    log.info("cases: %d total, %d covered by the %s corpus",
             len(frame), len(covered), corpus.name)

    reports = _load_reports_for_dates(args.reports_dir, set(covered["game_date"]))
    calendar = build_stamp_calendar(reports)
    reports_norm = cases_mod._reports_normalized(reports)
    index = cases_mod.build_designation_index(reports_norm)
    spell_starts = _load_spell_starts(args.spell_file)

    audit_rows = []
    prose_inputs = []
    stamp_mismatches = 0
    stamp_checked = 0
    for case in covered.to_dict("records"):
        found = cases_mod.select_designation(
            reports_norm,
            player=case["player"], team=case["team"],
            game_date=case["game_date"], tip_ts=case["tip_ts"], index=index,
        )
        designation_ts = None if found is None else found["decision_report_ts"]
        stored = case.get("decision_report_ts")
        if designation_ts is not None and stored is not None and not pd.isna(stored):
            stamp_checked += 1
            if pd.Timestamp(designation_ts) != pd.Timestamp(stored):
                stamp_mismatches += 1
        decision = resolve_decision_time(
            game_date=case["game_date"], tip_ts=case["tip_ts"],
            banked_stamps=calendar.get(case["game_date"], []),
            designation_ts=designation_ts,
        )
        absence_start = spell_starts.get(
            (cases_mod._norm_name(case["player"]), case["game_date"].isoformat())
        )
        prose = slice_case_prose(
            case, corpus, decision, cluster_keys=clusters,
            absence_start=absence_start,
        )
        row = {
            "case_id": prose.case_id,
            "player": case["player"],
            "team": case["team"],
            "game_date": case["game_date"].isoformat(),
            "season_label": case.get("season_label"),
            "case_class": case.get("case_class"),
            "games_missed": case.get("games_missed"),
            "is_injury_return": case.get("is_injury_return"),
            "abstention": prose.abstention,
            "abstention_reason": prose.abstention_reason,
            **{f"decision_{k}": v for k, v in decision.to_dict().items()},
            **prose.audit.to_dict(),
        }
        audit_rows.append(row)
        prose_inputs.append(prose.to_dict())

    audit = pd.DataFrame(audit_rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    audit.to_csv(out / "case_prose_audit.csv", index=False)
    with open(out / "prose_inputs.jsonl", "w", encoding="utf-8") as fh:
        for rec in prose_inputs:
            fh.write(json.dumps(rec) + "\n")

    abstentions = audit[audit["abstention"]]
    summary = {
        "corpus": corpus.name,
        "corpus_files": corpus.n_files,
        "corpus_rows": corpus.n_rows,
        "corpus_banked_players": len(corpus.banked_players()),
        "subject_flag_name_disagreements": corpus.n_subject_flag_disagreements,
        "cases_total_in_frame": int(len(frame)),
        "cases_covered": int(len(audit)),
        "cases_loaded": int((~audit["abstention"]).sum()),
        "abstentions": int(len(abstentions)),
        "abstention_reasons": (
            abstentions["abstention_reason"].value_counts().to_dict() if len(abstentions) else {}
        ),
        "fallback_cases": int(audit["decision_was_fallback"].sum()),
        "fallback_exclusions": int(
            (audit["abstention_reason"] == "fallback_stamp_outside_window").sum()
        ),
        "strata_items_dropped": int(audit["n_strata_dropped"].sum()),
        "pre_spell_items_dropped": int(audit["n_pre_spell_dropped"].sum()),
        "retro_items_stripped": int(audit["n_retro_stripped"].sum()),
        "unresolved_mention_refs": int(audit["n_unresolved_mention_refs"].sum()),
        "mention_items_considered": int(audit["n_mention_items_considered"].sum()),
        "mention_items_admissible": int(audit["n_mention_items_admissible"].sum()),
        "raw_token_count_total": int(audit["raw_token_count"].sum()),
        "cleansed_token_count_total": int(audit["cleansed_token_count"].sum()),
        "designation_stamp_crosscheck": {
            "checked": stamp_checked,
            "mismatches_vs_case_frame": stamp_mismatches,
        },
    }

    if args.classify:
        from nba.minutes_restriction.decisions_client import (
            DecisionsBandClassifier,
            DecisionsConfigError,
        )

        try:
            classifier = DecisionsBandClassifier()
        except DecisionsConfigError as exc:
            print(f"REFUSED: {exc}")
            return 2
        from nba.minutes_restriction.prose_loader import CaseProseInput

        results = []
        covered_by_id = {c["case_id"]: c for c in covered.to_dict("records")}
        for rec in prose_inputs:
            if len(results) >= args.classify:
                break
            if rec["abstention"]:
                continue
            prose_obj = CaseProseInput(
                case_id=rec["case_id"], abstention=False, abstention_reason=None,
                subject_blurbs=rec["subject_blurbs"],
                mention_excerpts=rec["mention_excerpts"],
            )
            pred = predict_case(classifier, covered_by_id[rec["case_id"]], prose_obj)
            results.append({
                "case_id": pred.case_id,
                "band": pred.band,
                "probabilities": pred.probabilities,
                "confidence": pred.confidence,
                "abstention": pred.abstention,
                "abstention_reason": pred.abstention_reason,
            })
        (out / "classify_results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        summary["classified"] = len(results)
        summary["classify_results"] = results

    (out / "smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Graded: gated on corpus completeness
# ---------------------------------------------------------------------------

def run_graded(args: argparse.Namespace) -> int:
    from nba.minutes_restriction.arm_t_config import GRADED_BARS, GRADED_CORPUS
    from nba.minutes_restriction.prose_loader import (
        corpus_completeness,
        load_corpus,
        load_player_map,
    )

    if args.corpus != GRADED_CORPUS:
        print(f"REFUSED: the graded Arm T run is registered on the {GRADED_CORPUS!r} "
              f"corpus only (got {args.corpus!r}). The RotoWire corpus is the dev harness.")
        return 2
    corpus = load_corpus(args.corpus, args.parsed_dir, args.mentions_dir, args.player_map)
    resolved_ids = set(load_player_map(args.player_map).values())
    report = corpus_completeness(corpus, resolved_ids)
    print(json.dumps({"completeness": report.to_dict(), "graded_bars": GRADED_BARS}, indent=2))
    if not report.complete:
        print(
            f"REFUSED: corpus incomplete -- {report.n_banked} of {report.n_resolved} "
            "resolved players banked. The graded run fires only on the complete "
            "corpus (Argon Q1 scope lock)."
        )
        return 2
    if not args.execute:
        print("Corpus complete: the graded gate is OPEN. Nothing run without --execute.")
        return 0

    if not args.cases or not args.reports_dir or not args.out or not args.arm_s_export:
        print("REFUSED: --graded --execute requires --cases, --reports-dir, --out and --arm-s-export.")
        return 2

    from nba.minutes_restriction import graded_run

    try:
        graded_run.assert_frame_lock(args.cases)
    except graded_run.GradedGateError as exc:
        print(f"REFUSED: {exc}")
        return 2

    try:
        artifact = graded_run.run_graded_execution(
            frame_path=args.cases,
            parsed_dir=args.parsed_dir,
            mentions_dir=args.mentions_dir,
            player_map=args.player_map,
            reports_dir=args.reports_dir,
            export_csv=args.arm_s_export,
            out_dir=args.out,
            workers=args.workers,
        )
    except graded_run.GradedGateError as exc:
        print(f"REFUSED: {exc}")
        return 2

    verdict = artifact.get("verdict") or artifact.get("grade_report", {}).get("verdict")
    print(f"Arm T graded run verdict: {verdict}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.graded:
        return run_graded(args)
    if args.smoke:
        return run_smoke(args)
    print_plan(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
