"""Tests for the Arm T graded-run wiring (Argon 2026-11-13 ruling).

Covers:
  - verdict_for_graded boundary tests (pass case; margin-fail; p-fail; n-fail; mae-fails).
  - End-to-end grade(graded=True) synthetic tests isolating the tightened log-loss prong:
      (i) gain 0.005 nats (t_ll < base_ll holds, tightened prong bites) -> KILL.
      (ii) gain 0.05 nats concentrated on one date (clustered p >= 0.05) -> KILL.
      (iii) gain 0.05 nats spread evenly across >= 10 dates -> PASS.
  - paired_logloss_test unit checks (identical losses, single cluster, zero cases).
  - assert_frame_lock: hash mismatch raises GradedGateError; positive control
    with monkeypatched hash raises row / cohort count mismatch.
  - arm_s_cohort_check and sensitivity_exhibit unit verification.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nba.minutes_restriction import arm_t_config, graded_run
from nba.minutes_restriction.arm_t import Calibration
from nba.minutes_restriction.grade_t import grade, paired_logloss_test, verdict_for_graded
from nba.minutes_restriction.graded_run import (
    GradedGateError,
    arm_s_cohort_check,
    assert_frame_lock,
    cohort_mask,
    sensitivity_exhibit,
    sha256_of,
)

# ---------------------------------------------------------------------------
# 1. verdict_for_graded boundary tests
# ---------------------------------------------------------------------------

def test_verdict_for_graded_boundaries():
    # Pass case: n=150, rel=0.10, abs=0.40, gain=0.010, p=0.049 -> PASS
    assert verdict_for_graded(150, 0.10, 0.40, 0.010, 0.049) == "PASS"

    # Margin-fail: gain=0.0099 < 0.010 -> KILL
    assert verdict_for_graded(150, 0.10, 0.40, 0.0099, 0.01) == "KILL"

    # p-fail: p=0.05 is not < 0.05 -> KILL
    assert verdict_for_graded(150, 0.10, 0.40, 0.05, 0.05) == "KILL"
    assert verdict_for_graded(150, 0.10, 0.40, 0.05, 0.051) == "KILL"

    # n-fail: n=149 < 150 -> INSUFFICIENT_N
    assert verdict_for_graded(149, 0.20, 1.0, 0.05, 0.01) == "INSUFFICIENT_N"

    # MAE relative improvement fail: rel=0.099 < 0.10 -> KILL
    assert verdict_for_graded(150, 0.099, 0.50, 0.05, 0.01) == "KILL"

    # MAE absolute improvement fail: abs=0.399 < 0.40 -> KILL
    assert verdict_for_graded(150, 0.20, 0.399, 0.05, 0.01) == "KILL"


# ---------------------------------------------------------------------------
# 2. paired_logloss_test unit checks
# ---------------------------------------------------------------------------

def test_paired_logloss_test_identical_losses():
    t_ll = [1.2] * 20
    base_ll = [1.2] * 20
    dates = [f"2025-01-{(i % 10) + 1:02d}" for i in range(20)]
    gain, p, G = paired_logloss_test(t_ll, base_ll, dates)
    assert gain == pytest.approx(0.0)
    assert p == 1.0
    assert G == 10


def test_paired_logloss_test_single_cluster():
    t_ll = [0.5, 0.4]
    base_ll = [1.0, 1.0]
    dates = ["2025-01-01", "2025-01-01"]
    gain, p, G = paired_logloss_test(t_ll, base_ll, dates)
    assert gain == pytest.approx(0.55)
    assert p == 1.0  # fail closed when G < 2
    assert G == 1


def test_paired_logloss_test_zero_cases():
    gain, p, G = paired_logloss_test([], [], [])
    assert gain == 0.0
    assert p == 1.0
    assert G == 0


# ---------------------------------------------------------------------------
# 3. End-to-end grade(graded=True) synthetic tests isolating the log-loss prong
# ---------------------------------------------------------------------------

def _build_synthetic_cohort(n_cases=150, n_dates=10):
    cases_rows = []
    arm_s_rows = []
    cases_per_date = n_cases // n_dates

    for i in range(n_cases):
        cid = f"c{i}"
        d_idx = min(i // cases_per_date, n_dates - 1)
        gdate = f"2025-01-{d_idx + 1:02d}"
        cases_rows.append({
            "case_id": cid,
            "player": f"Player {i}",
            "team": "ORL",
            "game_date": gdate,
            "baseline_minutes": 30.0,
            "realized_minutes": 27.0,
            "realized_ratio": 0.9,
            "is_game_two": False,
            "games_missed": 10,
            "is_injury_return": True,
            "season_start": 2024,
            "season_label": "2024-25",
        })
        arm_s_rows.append({
            "case_id": cid,
            "pred_minutes": 30.0,
        })

    cases_df = pd.DataFrame(cases_rows)
    arm_s_df = pd.DataFrame(arm_s_rows)

    # Hand-built calibration: mean ratio 0.9 for all bands guarantees:
    # E[ratio] = 0.9 -> t_pred_minutes = 30 * 0.9 = 27.0 -> t MAE = 0.0
    # s MAE = |30 - 27| = 3.0 -> abs imp = 3.0 (>= 0.40), rel imp = 1.0 (>= 0.10).
    # Realized band for all cases is 'full_workload'. Base rate = 0.50.
    cal = Calibration(
        mean_ratio_by_band={
            "out": 0.9,
            "heavy_restriction": 0.9,
            "light_restriction": 0.9,
            "full_workload": 0.9,
        },
        base_rates={
            "out": 0.1,
            "heavy_restriction": 0.2,
            "light_restriction": 0.2,
            "full_workload": 0.5,
        },
    )
    return cases_df, arm_s_df, cal


def test_grade_graded_true_gain_too_small_bites():
    """(i) gain of 0.005 nats: t_ll < base_ll holds (passes legacy), but tightened prong bites -> KILL."""
    cases_df, arm_s_df, cal = _build_synthetic_cohort(150, 10)
    # Target gain per date ~0.005 with tiny variation
    n_cases = len(cases_df)
    t_preds_rows = []
    for i in range(n_cases):
        d_idx = i // 15
        target_gain = 0.005 + 1e-6 * (d_idx - 4.5)
        p_full = 0.5 * math.exp(target_gain)
        rem = (1.0 - p_full) / 3.0
        t_preds_rows.append({
            "case_id": f"c{i}",
            "band": "full_workload",
            "abstention": False,
            "confidence": 0.9,
            "p_out": rem,
            "p_heavy_restriction": rem,
            "p_light_restriction": rem,
            "p_full_workload": p_full,
        })
    t_preds = pd.DataFrame(t_preds_rows)

    # Legacy grade: t_ll < base_ll holds, MAE passes -> PASS
    legacy_report = grade(cases_df, arm_s_df, t_preds, cal, graded=False)
    assert legacy_report.t_log_loss < legacy_report.base_rate_log_loss
    assert legacy_report.verdict == "PASS"

    # Graded run: gain < 0.010 nats -> tightened prong bites -> KILL
    graded_report = grade(cases_df, arm_s_df, t_preds, cal, graded=True)
    assert graded_report.logloss_gain_nats == pytest.approx(0.005, abs=1e-5)
    assert graded_report.verdict == "KILL"


def test_grade_graded_true_gain_concentrated_high_p_fails():
    """(ii) gain of 0.05 nats but concentrated on one date -> clustered p >= 0.05 -> KILL."""
    cases_df, arm_s_df, cal = _build_synthetic_cohort(150, 10)
    # Date 0: large improvement (target gain 0.50 nats); Dates 1..9: 0.0 nats
    # Average gain: (15 * 0.50 + 135 * 0.0) / 150 = 0.05 nats
    t_preds_rows = []
    for i in range(len(cases_df)):
        d_idx = i // 15
        target_gain = 0.50 if d_idx == 0 else 0.0
        p_full = 0.5 * math.exp(target_gain)
        rem = (1.0 - p_full) / 3.0
        t_preds_rows.append({
            "case_id": f"c{i}",
            "band": "full_workload",
            "abstention": False,
            "confidence": 0.9,
            "p_out": rem,
            "p_heavy_restriction": rem,
            "p_light_restriction": rem,
            "p_full_workload": p_full,
        })
    t_preds = pd.DataFrame(t_preds_rows)

    report = grade(cases_df, arm_s_df, t_preds, cal, graded=True)
    assert report.logloss_gain_nats == pytest.approx(0.05, abs=1e-4)
    # Clustered p-value with t = 1.0 on 9 df is ~0.17 >= 0.05
    assert report.logloss_p_value >= 0.05
    assert report.verdict == "KILL"


def test_grade_graded_true_gain_spread_evenly_passes():
    """(iii) gain of 0.05 nats spread across >= 10 dates with MAE satisfied -> PASS."""
    cases_df, arm_s_df, cal = _build_synthetic_cohort(150, 10)
    # Spread gain 0.05 evenly across dates with tiny jitter
    t_preds_rows = []
    for i in range(len(cases_df)):
        d_idx = i // 15
        target_gain = 0.05 + 1e-4 * (d_idx - 4.5)
        p_full = 0.5 * math.exp(target_gain)
        rem = (1.0 - p_full) / 3.0
        t_preds_rows.append({
            "case_id": f"c{i}",
            "band": "full_workload",
            "abstention": False,
            "confidence": 0.9,
            "p_out": rem,
            "p_heavy_restriction": rem,
            "p_light_restriction": rem,
            "p_full_workload": p_full,
        })
    t_preds = pd.DataFrame(t_preds_rows)

    report = grade(cases_df, arm_s_df, t_preds, cal, graded=True)
    assert report.logloss_gain_nats == pytest.approx(0.05, abs=1e-4)
    assert report.logloss_p_value < 0.05
    assert report.s_mae == pytest.approx(3.0)
    assert report.t_mae == pytest.approx(0.0)
    assert report.verdict == "PASS"


# ---------------------------------------------------------------------------
# 4. assert_frame_lock checks
# ---------------------------------------------------------------------------

def test_assert_frame_lock_hash_mismatch_raises(tmp_path):
    temp_file = tmp_path / "temp_cases.parquet"
    pd.DataFrame({"x": [1, 2]}).to_parquet(temp_file)

    with pytest.raises(GradedGateError, match="Canonical frame SHA-256 mismatch"):
        assert_frame_lock(temp_file)


def test_assert_frame_lock_row_and_cohort_count_mismatch(tmp_path, monkeypatch):
    temp_file = tmp_path / "temp_cases.parquet"
    pd.DataFrame({"x": [1, 2]}).to_parquet(temp_file)

    # Monkeypatch the config constant to the temp file's real hash for positive control
    real_sha = sha256_of(temp_file)
    monkeypatch.setattr(arm_t_config, "CANONICAL_FRAME_SHA256", real_sha)

    # Now hash check passes, but row count (2 != 5365) fails
    with pytest.raises(GradedGateError, match="row count mismatch"):
        assert_frame_lock(temp_file)

    # Create dummy dataframe with 5365 rows but 0 cohort rows
    df_rows = pd.DataFrame({
        "season_start": [2021] * arm_t_config.CANONICAL_FRAME_ROWS,
        "is_game_two": [False] * arm_t_config.CANONICAL_FRAME_ROWS,
        "games_missed": [1] * arm_t_config.CANONICAL_FRAME_ROWS,
        "is_injury_return": [False] * arm_t_config.CANONICAL_FRAME_ROWS,
    })
    df_rows.to_parquet(temp_file)
    monkeypatch.setattr(arm_t_config, "CANONICAL_FRAME_SHA256", sha256_of(temp_file))

    # Now row count passes, but cohort count (0 != 227) fails
    with pytest.raises(GradedGateError, match="cohort N mismatch"):
        assert_frame_lock(temp_file)


# ---------------------------------------------------------------------------
# 5. Cohort check & sensitivity exhibit unit tests
# ---------------------------------------------------------------------------

def test_sensitivity_exhibit():
    detail = pd.DataFrame([
        {"case_id": "c1", "s_pred_minutes": 30.0, "t_pred_minutes": 25.0, "realized_minutes": 25.0,
         "base_log_loss_i": 1.0, "t_log_loss_i": 0.8},
        {"case_id": "c2", "s_pred_minutes": 30.0, "t_pred_minutes": 28.0, "realized_minutes": 25.0,
         "base_log_loss_i": 1.0, "t_log_loss_i": 0.9},
        {"case_id": "c3", "s_pred_minutes": 25.0, "t_pred_minutes": 25.0, "realized_minutes": 25.0,
         "base_log_loss_i": 1.0, "t_log_loss_i": 1.0},
    ])
    moved = ["c1"]
    ex = sensitivity_exhibit(detail, moved)
    assert ex["status"] == "diagnostic/non-gating"
    assert not ex["is_gating"]
    assert ex["all"]["n"] == 3
    assert ex["moved_8"]["n"] == 1
    assert ex["invariant_219"]["n"] == 2
    assert ex["moved_8"]["t_mae"] == pytest.approx(0.0)


def _synthetic_frame_and_export(tmp_path):
    """A small frame plus an export built from its own walk-forward.

    The export carries the designations Arm S consumed (the real
    predictions_export.csv does), attached from the frame by case_id.
    """
    from nba.minutes_restriction import arm_s

    rows = []
    for season in (2021, 2022, 2023):
        for i, missed in enumerate((0, 2, 4, 6, 8, 10)):
            rows.append({
                "case_id": f"dev-{season}-{i}", "player": "P", "team": "LAL",
                "game_date": f"{season}-11-01", "season_start": season,
                "season_label": f"{season}-{str(season+1)[-2:]}",
                "case_class": "return", "games_missed": missed,
                "missed_band": "4-9" if missed in (4, 6, 8) else ("10+" if missed == 10 else "0"),
                "is_injury_return": True, "is_game_two": False,
                "baseline_minutes": 30.0, "realized_minutes": 27.0, "realized_ratio": 0.9,
                "designation": "Questionable", "injury_class": "injury_illness",
                "is_back_to_back": False,
            })
    for season in (2024, 2025):
        for i in range(4):
            rows.append({
                "case_id": f"test-{season}-{i}", "player": "P", "team": "LAL",
                "game_date": f"{season}-11-01", "season_start": season,
                "season_label": f"{season}-{str(season+1)[-2:]}",
                "case_class": "return", "games_missed": 5,
                "missed_band": "4-9", "is_injury_return": True, "is_game_two": False,
                "baseline_minutes": 30.0, "realized_minutes": 27.0, "realized_ratio": 0.9,
                "designation": "Questionable", "injury_class": "injury_illness",
                "is_back_to_back": False,
            })
    frame = pd.DataFrame(rows)
    walked = arm_s.walk_forward(frame)
    export_df = walked.copy()
    export_df["designation"] = export_df["case_id"].map(
        frame.set_index("case_id")["designation"])
    export_path = tmp_path / "export.csv"
    export_df.to_csv(export_path, index=False)
    return frame, walked, export_df, export_path


def _pin_synthetic_canonicals(monkeypatch, walked):
    """Point the canonical pins at the synthetic frame's own re-grade.

    Mirrors the real-data situation (pins recorded from the canonical
    re-grade) so the amended check can pass on synthetic data; tamper
    tests then break one assertion at a time.
    """
    from nba.minutes_restriction import grade as grade_mod

    rebuilt_cohort = walked[cohort_mask(walked)]
    mae = float((rebuilt_cohort["realized_minutes"]
                 - rebuilt_cohort["pred_minutes"]).abs().mean())
    cmax = max((abs(b["diff"]) for b in grade_mod.calibration(rebuilt_cohort).values()),
               default=0.0)
    pmax = max((abs(b["diff"]) for b in grade_mod.calibration(
        walked[~walked["is_game_two"].astype(bool)]).values()), default=0.0)
    monkeypatch.setattr(arm_t_config, "CANONICAL_ARM_S_COHORT_MAE", mae)
    monkeypatch.setattr(arm_t_config, "CANONICAL_ARM_S_COHORT_CALIB_MAX", cmax)
    monkeypatch.setattr(arm_t_config, "CANONICAL_ARM_S_PRIMARY_CALIB_MAX", pmax)
    monkeypatch.setattr(arm_t_config, "CANONICAL_DEV_FIT_ROWS", 18)
    return mae


def test_arm_s_cohort_check_amended_passes(tmp_path, monkeypatch):
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    mae = _pin_synthetic_canonicals(monkeypatch, walked)

    res = arm_s_cohort_check(frame, export_path)
    assert res["n"] == 8
    assert res["invariance"]["case_id_exact_match"] is True
    assert res["invariance"]["designation_mismatches"] == 0
    assert res["invariance"]["max_outcome_abs_diff"] < 1e-9
    assert res["fit_provenance"]["dev_fit_rows"] == 18
    assert res["comparators"]["canonical_rebuilt"]["cohort_mae"] == pytest.approx(mae)
    # The synthetic export IS the same predictions, so legacy == canonical here.
    assert res["comparators"]["legacy_export"]["cohort_mae"] == pytest.approx(mae)


def test_arm_s_cohort_check_prediction_drift_is_not_gated(tmp_path, monkeypatch):
    """Amended Q4.2: export predictions are NOT asserted (fit-dependent)."""
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    _pin_synthetic_canonicals(monkeypatch, walked)

    drifted = export_df.copy()
    drifted.loc[drifted.index[0], "pred_minutes"] += 0.25
    drifted_path = tmp_path / "drifted_export.csv"
    drifted.to_csv(drifted_path, index=False)
    res = arm_s_cohort_check(frame, drifted_path)  # must NOT raise
    assert res["comparators"]["legacy_export"]["cohort_mae"] != pytest.approx(
        res["comparators"]["canonical_rebuilt"]["cohort_mae"])


def test_arm_s_cohort_check_outcome_tamper_fails(tmp_path, monkeypatch):
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    _pin_synthetic_canonicals(monkeypatch, walked)

    bad = export_df.copy()
    bad.loc[bad.index[0], "realized_minutes"] += 0.01
    bad_path = tmp_path / "bad_export.csv"
    bad.to_csv(bad_path, index=False)
    with pytest.raises(GradedGateError, match="outcome mismatch"):
        arm_s_cohort_check(frame, bad_path)


def test_arm_s_cohort_check_designation_tamper_fails(tmp_path, monkeypatch):
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    _pin_synthetic_canonicals(monkeypatch, walked)

    bad = export_df.copy()
    bad.loc[bad.index[0], "designation"] = "Probable"
    bad_path = tmp_path / "bad_export.csv"
    bad.to_csv(bad_path, index=False)
    with pytest.raises(GradedGateError, match="designation mismatch"):
        arm_s_cohort_check(frame, bad_path)


def test_arm_s_cohort_check_membership_tamper_fails(tmp_path, monkeypatch):
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    _pin_synthetic_canonicals(monkeypatch, walked)

    bad = export_df.drop(index=export_df.index[0])
    bad_path = tmp_path / "bad_export.csv"
    bad.to_csv(bad_path, index=False)
    with pytest.raises(GradedGateError, match="membership mismatch"):
        arm_s_cohort_check(frame, bad_path)


def test_arm_s_cohort_check_pin_tamper_fails(tmp_path, monkeypatch):
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    mae = _pin_synthetic_canonicals(monkeypatch, walked)
    monkeypatch.setattr(arm_t_config, "CANONICAL_ARM_S_COHORT_MAE", mae + 0.001)
    with pytest.raises(GradedGateError, match="pin failure \\(cohort MAE\\)"):
        arm_s_cohort_check(frame, export_path)


def test_arm_s_cohort_check_regrade_drift_fails(tmp_path, monkeypatch):
    """Shifting the dev fit set moves the re-grade off its pin -> gate fails."""
    frame, walked, export_df, export_path = _synthetic_frame_and_export(tmp_path)
    _pin_synthetic_canonicals(monkeypatch, walked)

    tampered = frame.copy()
    dev_rows = tampered["season_start"].isin((2021, 2022, 2023))
    tampered.loc[dev_rows, "realized_ratio"] = 0.5
    tampered.loc[dev_rows, "realized_minutes"] = 15.0
    with pytest.raises(GradedGateError, match="pin failure"):
        arm_s_cohort_check(tampered, export_path)


def test_preflight_cohort_gate_assertions():
    from nba.minutes_restriction.prose_loader import CaseProseInput, DecisionTime, ProseCorpus

    cohort_df = pd.DataFrame([{
        "case_id": "c1", "player": "Player 1", "tip_ts": "2025-01-10T23:00:00Z"
    }])
    corpus = ProseCorpus(name="nbc")
    prose_inp = CaseProseInput(case_id="c1", abstention=False, abstention_reason=None)

    # (a) excluded decision
    dec_excluded = DecisionTime(
        freeze_ts=None, ideal_stamp=None, last_banked_stamp=None,
        was_fallback=False, excluded=True, exclusion_reason="no_stamp"
    )
    with pytest.raises(GradedGateError, match=r"Cohort preflight failure \(a\)"):
        graded_run.preflight_cohort(pd.DataFrame(), corpus, cohort_df, {"c1": prose_inp}, {"c1": dec_excluded})

    # (b) freeze > tip - 60 min
    dec_late = DecisionTime(
        freeze_ts=pd.Timestamp("2025-01-10T22:30:00Z").tz_convert("America/New_York"),
        ideal_stamp=None, last_banked_stamp=None, was_fallback=False,
        excluded=False, exclusion_reason=None
    )
    with pytest.raises(GradedGateError, match=r"Cohort preflight failure \(b\)"):
        graded_run.preflight_cohort(pd.DataFrame(), corpus, cohort_df, {"c1": prose_inp}, {"c1": dec_late})

    # (c) blurb > freeze
    dec_ok = DecisionTime(
        freeze_ts=pd.Timestamp("2025-01-10T21:00:00Z").tz_convert("America/New_York"),
        ideal_stamp=None, last_banked_stamp=None, was_fallback=False,
        excluded=False, exclusion_reason=None
    )
    prose_late_blurb = CaseProseInput(
        case_id="c1", abstention=False, abstention_reason=None,
        subject_blurbs=[{"published_ts": "2025-01-10T21:30:00Z", "headline": "Late News", "body": "text"}]
    )
    with pytest.raises(GradedGateError, match=r"Cohort preflight failure \(c\)"):
        graded_run.preflight_cohort(pd.DataFrame(), corpus, cohort_df, {"c1": prose_late_blurb}, {"c1": dec_ok})


def test_classify_many_order_and_abstention():
    cases_df = pd.DataFrame([
        {"case_id": "c1"}, {"case_id": "c2"}, {"case_id": "c3"}
    ])
    from nba.minutes_restriction.prose_loader import CaseProseInput

    prose_inputs = {
        "c1": CaseProseInput(case_id="c1", abstention=True, abstention_reason="zero_subject_items"),
        "c2": CaseProseInput(case_id="c2", abstention=False, abstention_reason=None),
        "c3": CaseProseInput(case_id="c3", abstention=True, abstention_reason="decision_excluded"),
    }
    # With classifier=None, un-abstained case c2 abstains with not_configured
    out = graded_run.classify_many(None, cases_df, prose_inputs, workers=2)
    assert list(out["case_id"]) == ["c1", "c2", "c3"]
    assert out.iloc[0]["abstention_reason"] == "zero_subject_items"
    assert out.iloc[1]["abstention_reason"] == "not_configured"
    assert out.iloc[2]["abstention_reason"] == "decision_excluded"


def test_cli_graded_runner_guards(capsys, monkeypatch, tmp_path):
    import scripts.minutes_arm_t_run as runner
    from nba.minutes_restriction import prose_loader

    # Mock completeness to pass
    monkeypatch.setattr(prose_loader, "load_corpus", lambda *a, **k: prose_loader.ProseCorpus(name="nbc"))
    monkeypatch.setattr(prose_loader, "corpus_completeness", lambda c, ids: type("CR", (), {"complete": True, "n_banked": 1, "n_resolved": 1, "to_dict": lambda s: {}})())
    monkeypatch.setattr(prose_loader, "load_player_map", lambda p: {"p": "1"})

    # 1. Missing required arguments under --graded --execute -> exit 2
    rc = runner.main(["--corpus", "nbc", "--parsed-dir", str(tmp_path), "--player-map", "map.csv", "--graded", "--execute"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "REFUSED: --graded --execute requires" in out

    # 2. Hash lock mismatch -> exit 2
    fake_cases = tmp_path / "cases.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(fake_cases)
    rc = runner.main([
        "--corpus", "nbc", "--parsed-dir", str(tmp_path), "--player-map", "map.csv",
        "--cases", str(fake_cases), "--reports-dir", str(tmp_path),
        "--out", str(tmp_path), "--arm-s-export", str(tmp_path / "exp.csv"),
        "--graded", "--execute",
    ])
    assert rc == 2
    out = capsys.readouterr().out
    assert "REFUSED: Canonical frame SHA-256 mismatch" in out

    # 3. Successful run prints verdict line
    monkeypatch.setattr(graded_run, "assert_frame_lock", lambda p: {"sha256": "ok"})
    monkeypatch.setattr(graded_run, "run_graded_execution", lambda **k: {"verdict": "PASS"})
    rc = runner.main([
        "--corpus", "nbc", "--parsed-dir", str(tmp_path), "--player-map", "map.csv",
        "--cases", str(fake_cases), "--reports-dir", str(tmp_path),
        "--out", str(tmp_path), "--arm-s-export", str(tmp_path / "exp.csv"),
        "--graded", "--execute",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Arm T graded run verdict: PASS" in out

