"""nba.minutes_restriction.graded_run -- Arm T graded-run wiring.

Implements Argon's 2026-11-13 ruling:
  - Canonical frame hash-lock assertion (refuses unless SHA-256 equals the lock).
  - Arm S cohort reproduction check against predictions_export.csv (< 1e-9 max abs diff).
  - Two-day official reports resolution and Q4/Q5 prose slicing.
  - Cohort preflight assertions (decision time <= tip - 60m, all admitted items <= freeze, no strata items).
  - ThreadPoolExecutor parallel classification.
  - Test grading under the tightened Q1 log-loss prong (gain >= 0.010 nats, clustered paired p < 0.05).
  - Sensitivity exhibit (invariant vs moved cohort cases).
  - Artifact generation (grade_artifact.json, t_predictions.csv, dev_t_predictions.csv, arm_s_predictions_rebuilt.csv).
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from . import arm_s, arm_t, arm_t_config, cases, grade, grade_t, prose_loader
from .arm_t import Calibration, band_mapped_minutes
from .arm_t_config import (
    CANONICAL_FRAME_COHORT_N,
    CANONICAL_FRAME_ROWS,
    CANONICAL_FRAME_SHA256,
    DEV_SEASON_STARTS,
    TEST_SEASON_STARTS,
)
from .decisions_client import BANDS, BandPrediction, DecisionsBandClassifier, DecisionsConfigError
from .prose_loader import CaseProseInput, DecisionTime, ProseCorpus

log = logging.getLogger("graded_run")


class GradedGateError(RuntimeError):
    """Raised when any precondition or honesty gate of the graded run fails."""


def sha256_of(path: str | Path) -> str:
    """Compute SHA-256 hex digest of a file via streamed reads."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def test_missed_ge4_mask(df: pd.DataFrame) -> pd.Series:
    """Test seasons (2024-25, 2025-26), ~is_game_two, games_missed >= 4."""
    season_ok = df["season_start"].astype(int).isin(arm_t_config.TEST_SEASON_STARTS)
    not_game_two = ~df["is_game_two"].astype(bool)
    missed_ge4 = df["games_missed"].astype(int) >= 4
    return season_ok & not_game_two & missed_ge4


test_missed_ge4_mask.__test__ = False  # Prevent pytest from treating as a test fixture


def cohort_mask(df: pd.DataFrame) -> pd.Series:
    """The missed >= 4 injury return cohort on test seasons (n = 227)."""
    return test_missed_ge4_mask(df) & df["is_injury_return"].astype(bool)


def assert_frame_lock(frame_path: str | Path) -> dict:
    """Assert the case frame matches the canonical frame hash and row/cohort counts.

    Argon 2026-11-13 ruling (Q3 frame provenance):
    The graded run uses EXCLUSIVELY the rebuilt canonical frame (case_frame.parquet).
    Raises GradedGateError on any hash, row-count, or cohort-count mismatch.
    """
    actual_sha = sha256_of(frame_path)
    if actual_sha != arm_t_config.CANONICAL_FRAME_SHA256:
        raise GradedGateError(
            f"Canonical frame SHA-256 mismatch: expected {arm_t_config.CANONICAL_FRAME_SHA256}, "
            f"got {actual_sha} for {frame_path}"
        )
    df = pd.read_parquet(frame_path)
    n_rows = len(df)
    if n_rows != arm_t_config.CANONICAL_FRAME_ROWS:
        raise GradedGateError(
            f"Canonical frame row count mismatch: expected {arm_t_config.CANONICAL_FRAME_ROWS}, "
            f"got {n_rows}"
        )
    c_mask = cohort_mask(df)
    cohort_n = int(c_mask.sum())
    if cohort_n != arm_t_config.CANONICAL_FRAME_COHORT_N:
        raise GradedGateError(
            f"Canonical frame cohort N mismatch: expected {arm_t_config.CANONICAL_FRAME_COHORT_N}, "
            f"got {cohort_n}"
        )
    return {"sha256": actual_sha, "rows": n_rows, "cohort_n": cohort_n}


def arm_s_cohort_check(frame: pd.DataFrame, export_csv_path: str | Path) -> dict:
    """Assert rebuilt Arm S predictions exactly reproduce export on the cohort."""
    rebuilt_walk = arm_s.walk_forward(frame)
    rebuilt_cohort = rebuilt_walk[cohort_mask(rebuilt_walk)].copy()

    export_df = pd.read_csv(export_csv_path)
    joined = rebuilt_cohort.merge(export_df, on="case_id", suffixes=("", "_export"))
    if len(joined) != len(rebuilt_cohort):
        raise GradedGateError(
            f"Arm S export CSV missing cohort cases: joined {len(joined)} "
            f"of {len(rebuilt_cohort)} rebuilt cohort cases"
        )

    pred_col_export = (
        "pred_minutes_export" if "pred_minutes_export" in joined.columns else "pred_minutes"
    )
    if pred_col_export not in joined.columns:
        if "pred_ratio_export" in joined.columns:
            joined["pred_minutes_export"] = (
                joined["pred_ratio_export"].astype(float)
                * joined["baseline_minutes"].astype(float)
            )
            pred_col_export = "pred_minutes_export"
        else:
            raise GradedGateError(
                f"Export CSV {export_csv_path} lacks pred_minutes or pred_ratio"
            )

    diffs = (joined["pred_minutes"].astype(float) - joined[pred_col_export].astype(float)).abs()
    max_abs_diff = float(diffs.max())
    if max_abs_diff >= 1e-9:
        raise GradedGateError(
            f"Arm S cohort reproduction failure: max abs diff {max_abs_diff:.4e} >= 1e-9"
        )

    rebuilt_mae = float(
        (joined["realized_minutes"].astype(float) - joined["pred_minutes"].astype(float)).abs().mean()
    )
    export_mae = float(
        (joined["realized_minutes"].astype(float) - joined[pred_col_export].astype(float)).abs().mean()
    )
    if abs(rebuilt_mae - export_mae) >= 1e-6:
        raise GradedGateError(
            f"Arm S cohort MAE mismatch: rebuilt {rebuilt_mae:.6f} vs export {export_mae:.6f}"
        )

    # Calibration-by-band, two populations (the recorded 0.045 is the
    # PRIMARY-population max band |diff| from grade.calibration over all
    # non-game-2 test cases; the cohort-band table is published alongside):
    calib = grade.calibration(joined)
    calib_max_abs_diff = max((abs(b["diff"]) for b in calib.values()), default=0.0)

    rebuilt_primary = rebuilt_walk[~rebuilt_walk["is_game_two"].astype(bool)]
    export_primary = export_df[~export_df["is_game_two"].astype(bool)]
    calib_primary_rebuilt = grade.calibration(rebuilt_primary)
    calib_primary_export = grade.calibration(export_primary)
    calib_primary_max_rebuilt = max(
        (abs(b["diff"]) for b in calib_primary_rebuilt.values()), default=0.0)
    calib_primary_max_export = max(
        (abs(b["diff"]) for b in calib_primary_export.values()), default=0.0)
    if abs(calib_primary_max_rebuilt - calib_primary_max_export) >= 1e-6:
        raise GradedGateError(
            "Arm S primary-population calibration mismatch: rebuilt "
            f"{calib_primary_max_rebuilt:.6f} vs export {calib_primary_max_export:.6f}"
        )

    season_splits = {}
    for (start, label), sub in joined.groupby(["season_start", "season_label"], sort=True):
        season_splits[str(label)] = grade.cohort_stats(sub)

    overall_stats = grade.cohort_stats(joined)

    return {
        "max_abs_diff": max_abs_diff,
        "rebuilt_cohort_mae": rebuilt_mae,
        "export_cohort_mae": export_mae,
        "calibration_cohort_bands": calib,
        "calibration_cohort_max_abs_diff": calib_max_abs_diff,
        "calibration_primary_bands_rebuilt": calib_primary_rebuilt,
        "calibration_primary_bands_export": calib_primary_export,
        "calibration_primary_max_abs_diff_rebuilt": calib_primary_max_rebuilt,
        "calibration_primary_max_abs_diff_export": calib_primary_max_export,
        "seasons": season_splits,
        "overall": overall_stats,
        "n": len(joined),
    }


def _load_reports_for_dates(reports_dir: str | Path, game_dates: set[date]) -> pd.DataFrame:
    """Read the banked report CSVs covering game_dates (and previous day)."""
    root = Path(reports_dir)
    frames = []
    wanted_dirs = set()
    for d in game_dates:
        wanted_dirs.add(d.isoformat())
        wanted_dirs.add((d - timedelta(days=1)).isoformat())
    for dirname in sorted(wanted_dirs):
        day_dir = root / dirname
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("report_*.csv")):
            frames.append(pd.read_csv(f))
    if not frames:
        raise GradedGateError(f"no report CSVs found under {reports_dir} for the case dates")
    df = pd.concat(frames, ignore_index=True)
    df["_game_date"] = pd.to_datetime(df["game_date"]).dt.date
    return df[df["_game_date"].isin(game_dates)].drop(columns=["_game_date"]).reset_index(drop=True)


def resolve_and_slice(
    cases_df: pd.DataFrame,
    corpus: ProseCorpus,
    reports_dir: str | Path,
    spell_starts: dict | None = None,
) -> tuple[dict[str, CaseProseInput], dict[str, DecisionTime], pd.DataFrame]:
    """Resolve decision times and slice prose inputs for the given cases."""
    if not len(cases_df):
        return {}, {}, pd.DataFrame()

    game_dates = set(pd.to_datetime(cases_df["game_date"]).dt.date)
    reports = _load_reports_for_dates(reports_dir, game_dates)
    calendar = prose_loader.build_stamp_calendar(reports)
    reports_norm = cases._reports_normalized(reports)
    index = cases.build_designation_index(reports_norm)
    clusters = prose_loader.strata_cluster_keys(corpus)
    spell_starts = spell_starts or {}

    prose_inputs: dict[str, CaseProseInput] = {}
    decisions: dict[str, DecisionTime] = {}
    audit_rows = []

    for case in cases_df.to_dict("records"):
        cid = str(case["case_id"])
        gdate = pd.to_datetime(case["game_date"]).date()
        tip_ts = pd.Timestamp(case["tip_ts"])

        found = cases.select_designation(
            reports_norm,
            player=case["player"],
            team=case["team"],
            game_date=gdate,
            tip_ts=tip_ts,
            index=index,
        )
        designation_ts = None if found is None else found["decision_report_ts"]

        decision = prose_loader.resolve_decision_time(
            game_date=gdate,
            tip_ts=tip_ts,
            banked_stamps=calendar.get(gdate, []),
            designation_ts=designation_ts,
        )

        absence_start = spell_starts.get(
            (cases._norm_name(case["player"]), gdate.isoformat())
        )
        prose = prose_loader.slice_case_prose(
            case,
            corpus,
            decision,
            cluster_keys=clusters,
            absence_start=absence_start,
        )

        prose_inputs[cid] = prose
        decisions[cid] = decision

        row = {
            "case_id": prose.case_id,
            "player": case["player"],
            "team": case["team"],
            "game_date": gdate.isoformat(),
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

    return prose_inputs, decisions, pd.DataFrame(audit_rows)


def preflight_cohort(
    frame: pd.DataFrame,
    corpus: ProseCorpus,
    cohort_df: pd.DataFrame,
    prose_inputs: dict[str, CaseProseInput],
    decisions: dict[str, DecisionTime],
) -> dict:
    """Run mandatory preflight honesty assertions on the cohort."""
    clusters = prose_loader.strata_cluster_keys(corpus)

    strata_signatures_by_player: dict[str, set[tuple[pd.Timestamp, str]]] = {}
    for p_key, items in corpus.items_by_player.items():
        sig_set = set()
        for item in items:
            if prose_loader.is_stratum_by_date(item) or item.corpus_key in clusters:
                ts_utc = pd.Timestamp(item.published_ts).tz_convert("UTC").floor("s")
                sig_set.add((ts_utc, prose_loader.cleanse_text(item.headline)))
        strata_signatures_by_player[p_key] = sig_set

    for p_key, refs in corpus.mention_refs_by_player.items():
        sig_set = strata_signatures_by_player.setdefault(p_key, set())
        for ref in refs:
            resolved = corpus.pool_by_key.get(ref.corpus_key)
            if resolved is not None and (
                prose_loader.is_stratum_by_date(resolved) or resolved.corpus_key in clusters
            ):
                ts_utc = pd.Timestamp(resolved.published_ts).tz_convert("UTC").floor("s")
                sig_set.add((ts_utc, prose_loader.cleanse_text(resolved.headline)))

    n_cases_checked = 0
    n_blurbs_checked = 0
    strata_dropped_total = 0

    for case in cohort_df.to_dict("records"):
        cid = str(case["case_id"])
        n_cases_checked += 1

        # (a) non-excluded DecisionTime (freeze present)
        dec = decisions.get(cid)
        if dec is None or dec.excluded or dec.freeze_ts is None:
            raise GradedGateError(
                f"Cohort preflight failure (a): case {cid} has excluded or missing decision time ({dec})"
            )

        # (b) freeze <= tip_ts - 60 minutes
        freeze = dec.freeze_ts
        tip = pd.Timestamp(case["tip_ts"])
        tip_aligned = tip.tz_convert(freeze.tz) if tip.tzinfo is not None else tip.tz_localize(freeze.tz)
        cutoff = tip_aligned - pd.Timedelta(minutes=60)
        if freeze > cutoff:
            raise GradedGateError(
                f"Cohort preflight failure (b): case {cid} freeze {freeze} > tip - 60m ({cutoff})"
            )

        inp = prose_inputs.get(cid)
        if inp is None:
            raise GradedGateError(f"Cohort preflight failure: case {cid} missing prose input")

        strata_dropped_total += inp.audit.n_strata_dropped
        freeze_utc = freeze.tz_convert("UTC")
        p_key = cases._norm_name(case["player"])
        player_strata = strata_signatures_by_player.get(p_key, set())

        # (c) and (d) for subject blurbs
        for b in inp.subject_blurbs:
            n_blurbs_checked += 1
            b_ts = pd.Timestamp(b["published_ts"])
            b_ts_utc = b_ts.tz_convert("UTC") if b_ts.tzinfo is not None else b_ts.tz_localize("UTC")
            if b_ts_utc > freeze_utc:
                raise GradedGateError(
                    f"Cohort preflight failure (c): case {cid} subject blurb {b['headline']!r} "
                    f"published {b_ts_utc} > freeze {freeze_utc}"
                )
            sig = (b_ts_utc.floor("s"), prose_loader.cleanse_text(b["headline"]))
            if sig in player_strata:
                raise GradedGateError(
                    f"Cohort preflight failure (d): case {cid} admitted stratum subject blurb: {sig}"
                )

        # (c) and (d) for mention items
        for m in inp.mention_items:
            n_blurbs_checked += 1
            m_ts = pd.Timestamp(m["published_ts"])
            m_ts_utc = m_ts.tz_convert("UTC") if m_ts.tzinfo is not None else m_ts.tz_localize("UTC")
            if m_ts_utc > freeze_utc:
                raise GradedGateError(
                    f"Cohort preflight failure (c): case {cid} mention item {m['headline']!r} "
                    f"published {m_ts_utc} > freeze {freeze_utc}"
                )
            sig = (m_ts_utc.floor("s"), prose_loader.cleanse_text(m["headline"]))
            if sig in player_strata:
                raise GradedGateError(
                    f"Cohort preflight failure (d): case {cid} admitted stratum mention item: {sig}"
                )

    return {
        "n_cases_checked": n_cases_checked,
        "n_blurbs_checked": n_blurbs_checked,
        "n_strata_admitted": 0,
        "strata_dropped_total": strata_dropped_total,
    }


def classify_many(
    classifier: Any,
    cases_df: pd.DataFrame,
    prose_inputs: dict[str, CaseProseInput],
    workers: int = 12,
) -> pd.DataFrame:
    """Classify cases using prose_loader.predict_case via ThreadPoolExecutor.

    Preserves input order in the output DataFrame:
    [case_id, band, p_out, p_heavy_restriction, p_light_restriction,
     p_full_workload, confidence, abstention, abstention_reason]
    """
    records = cases_df.to_dict("records")

    def _classify_one(case: dict) -> dict[str, Any]:
        cid = str(case["case_id"])
        prose = prose_inputs.get(cid)
        if prose is None:
            pred = BandPrediction.abstain(cid, "missing_prose_input")
        elif prose.abstention:
            pred = BandPrediction.abstain(cid, prose.abstention_reason or "abstention")
        elif classifier is None:
            pred = BandPrediction.abstain(cid, "not_configured")
        else:
            pred = prose_loader.predict_case(classifier, case, prose)

        row: dict[str, Any] = {
            "case_id": pred.case_id,
            "band": pred.band,
        }
        for b in BANDS:
            row[f"p_{b}"] = (
                pred.probabilities.get(b) if pred.probabilities else None
            )
        row["confidence"] = pred.confidence
        row["abstention"] = pred.abstention
        row["abstention_reason"] = pred.abstention_reason
        return row

    if workers > 1 and len(records) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_classify_one, records))
    else:
        rows = [_classify_one(case) for case in records]

    cols = ["case_id", "band"] + [f"p_{b}" for b in BANDS] + ["confidence", "abstention", "abstention_reason"]
    return pd.DataFrame(rows, columns=cols)


def sensitivity_exhibit(detail: pd.DataFrame, moved_case_ids: Iterable[Any]) -> dict:
    """Sensitivity exhibit over moved vs invariant decision-time cases.

    Diagnostic / non-gating exhibit per Argon 2026-11-13 ruling.
    """
    if isinstance(moved_case_ids, dict):
        moved_set = {str(k) for k in moved_case_ids.keys()}
    elif isinstance(moved_case_ids, (list, set, tuple)):
        moved_set = {
            x["case_id"] if isinstance(x, dict) else str(x)
            for x in moved_case_ids
        }
    else:
        moved_set = set()

    def _calc(sub: pd.DataFrame) -> dict:
        n = int(len(sub))
        if n == 0:
            return {
                "n": 0,
                "s_mae": None,
                "t_mae": None,
                "rel_improvement": None,
                "abs_improvement": None,
                "logloss_gain": None,
            }
        s_mae = float((sub["s_pred_minutes"] - sub["realized_minutes"]).abs().mean())
        t_mae = float((sub["t_pred_minutes"] - sub["realized_minutes"]).abs().mean())
        abs_imp = s_mae - t_mae
        rel_imp = abs_imp / s_mae if s_mae > 0 else 0.0
        ll_gain = float((sub["base_log_loss_i"] - sub["t_log_loss_i"]).mean())
        return {
            "n": n,
            "s_mae": s_mae,
            "t_mae": t_mae,
            "rel_improvement": rel_imp,
            "abs_improvement": abs_imp,
            "logloss_gain": ll_gain,
        }

    sub_all = detail
    sub_moved = detail[detail["case_id"].astype(str).isin(moved_set)]
    sub_invariant = detail[~detail["case_id"].astype(str).isin(moved_set)]

    return {
        "status": "diagnostic/non-gating",
        "is_gating": False,
        "all": _calc(sub_all),
        "invariant_219": _calc(sub_invariant),
        "moved_8": _calc(sub_moved),
    }


def run_graded_execution(
    *,
    frame_path: str | Path,
    parsed_dir: str | Path,
    mentions_dir: str | Path | None,
    player_map: str | Path,
    reports_dir: str | Path,
    export_csv: str | Path,
    out_dir: str | Path,
    workers: int = 12,
    classifier: Any = None,
) -> dict:
    """Run the registered Arm T graded execution in exact 12-step sequence."""
    # 1. Assert frame lock
    lock = assert_frame_lock(frame_path)

    # 2. Load frame
    frame = arm_t.load_cases(frame_path)

    # 3. Load corpus and verify completeness
    corpus = prose_loader.load_corpus("nbc", parsed_dir, mentions_dir, player_map)
    resolved_ids = set(prose_loader.load_player_map(player_map).values())
    comp = prose_loader.corpus_completeness(corpus, resolved_ids)
    if not comp.complete:
        raise GradedGateError(
            f"Corpus incomplete: {comp.n_banked} of {comp.n_resolved} players banked "
            f"(missing: {comp.missing_ids})"
        )

    # 4. Arm S cohort check (hard gate inside) and walk_forward predictions
    cohort_check = arm_s_cohort_check(frame, export_csv)
    arm_s_predictions = arm_s.walk_forward(frame)

    # 5. Populations
    dev_fit_mask = (
        frame["season_start"].astype(int).isin(arm_t_config.DEV_SEASON_STARTS)
        & (~frame["is_game_two"].astype(bool))
        & (frame["games_missed"].astype(int) >= 4)
        & frame["is_injury_return"].astype(bool)
    )
    dev_fit = frame[dev_fit_mask].copy()
    test_eval = frame[test_missed_ge4_mask(frame)].copy()
    cohort = frame[cohort_mask(frame)].copy()

    # 6. Resolve and slice over dev_fit + test_eval cases
    eval_cases = pd.concat([dev_fit, test_eval], ignore_index=True).drop_duplicates(subset=["case_id"])
    prose_inputs, decisions, audit_df = resolve_and_slice(eval_cases, corpus, reports_dir)

    # 7. Preflight cohort (hard gate)
    preflight = preflight_cohort(frame, corpus, cohort, prose_inputs, decisions)

    # 8. Classifier construction / check
    if classifier is None:
        try:
            classifier = DecisionsBandClassifier()
        except DecisionsConfigError as exc:
            raise GradedGateError(f"Decisions classifier config error: {exc}") from exc

    # 9. Dev classification & calibration & shrinkage weight.
    # The combiner is INFORMATIONAL ONLY (no bar reads it). Its dev Arm S
    # predictions are the in-sample dev fit (ArmSModel fit on the dev
    # seasons of the same frame, exactly the fit walk_forward applies to
    # test), because walk_forward itself emits test-season predictions
    # only. Provenance is recorded in the artifact.
    dev_t = classify_many(classifier, dev_fit, prose_inputs, workers=workers)
    calibration = arm_t.fit_calibration(dev_fit, dev_t)
    weight = None
    dev_shared = dev_t[~dev_t["abstention"].fillna(True)]
    if len(dev_shared):
        dev_model = arm_s.ArmSModel().fit(arm_s.dev_fit_frame(frame))
        dev_s = dev_fit[["case_id", "baseline_minutes"]].copy()
        dev_s["s_pred_minutes"] = (
            dev_model.predict_ratio(dev_fit)
            * dev_fit["baseline_minutes"].astype(float).to_numpy()
        )
        dev_merged = dev_fit.merge(dev_shared, on="case_id").merge(dev_s, on="case_id")
        if len(dev_merged):
            dev_merged["t_pred_minutes"] = [
                arm_t.band_mapped_minutes(
                    row.baseline_minutes,
                    {b: getattr(row, f"p_{b}") for b in BANDS},
                    calibration,
                )
                for row in dev_merged.itertuples(index=False)
            ]
            weight = arm_t.fit_shrinkage_weight(dev_merged)

    # 10. Test classification and grading
    test_t = classify_many(classifier, test_eval, prose_inputs, workers=workers)
    cohort_cids = set(cohort["case_id"])
    cohort_t = test_t[test_t["case_id"].isin(cohort_cids)].copy()
    report = grade_t.grade(
        cohort,
        arm_s_predictions,
        cohort_t,
        calibration,
        combiner_weight=weight,
        graded=True,
    )

    # 11. Grouped abstention and moved cases sensitivity exhibit
    n_grouped = int(len(test_t))
    n_abstentions_grouped = int(test_t["abstention"].fillna(True).sum())
    rate_grouped = float(n_abstentions_grouped / n_grouped) if n_grouped else 0.0
    grouped_dict = {"n": n_grouped, "abstentions": n_abstentions_grouped, "rate": rate_grouped}

    export_df = pd.read_csv(export_csv)
    cohort_merged = cohort.merge(
        export_df[["case_id", "decision_report_ts"]],
        on="case_id",
        suffixes=("_frame", "_export"),
    )
    moved_info = []
    for row in cohort_merged.itertuples():
        f_ts = getattr(row, "decision_report_ts_frame", None)
        e_ts = getattr(row, "decision_report_ts_export", None)
        if pd.notna(f_ts) and pd.notna(e_ts):
            t_f = pd.to_datetime(f_ts, utc=True)
            t_e = pd.to_datetime(e_ts, utc=True)
            if t_f != t_e:
                delta_m = (t_f - t_e).total_seconds() / 60.0
                moved_info.append({
                    "case_id": str(row.case_id),
                    "frame_ts": str(t_f),
                    "export_ts": str(t_e),
                    "delta_minutes": delta_m,
                })
    moved_case_ids = [m["case_id"] for m in moved_info]
    exhibit = sensitivity_exhibit(report.detail, moved_case_ids)

    # 12. Write artifacts
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    test_t.to_csv(out / "t_predictions.csv", index=False)
    dev_t.to_csv(out / "dev_t_predictions.csv", index=False)
    arm_s_predictions.to_csv(out / "arm_s_predictions_rebuilt.csv", index=False)
    audit_df.to_csv(out / "prose_audit.csv", index=False)

    artifact = {
        "lock": lock,
        "completeness": comp.to_dict(),
        "cohort_check": cohort_check,
        "preflight": preflight,
        "calibration": {
            "mean_ratio_by_band": calibration.mean_ratio_by_band,
            "base_rates": calibration.base_rates,
            "n_by_band": calibration.n_by_band,
            "source": calibration.source,
        },
        "combiner_weight_source": (
            "in-sample dev Arm S fit (ArmSModel on dev seasons of the canonical "
            "frame); combiner is informational only, no bar reads it"
        ),
        "grade_report": report.to_dict(),
        "report": report.to_dict(),
        "grouped": grouped_dict,
        "sensitivity_exhibit": exhibit,
        "moved_case_ids": moved_info,
        "moved_case_id_list": moved_case_ids,
        "verdict": report.verdict,
    }
    (out / "grade_artifact.json").write_text(
        json.dumps(artifact, indent=2, default=str), encoding="utf-8"
    )
    return artifact
