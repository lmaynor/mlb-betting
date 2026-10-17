"""Offline tests for nfl.data.pipeline (stages injected; no network, no GCS)."""
import pytest

from nfl.data import pipeline as PL

OK_REPORT = {"ok": True, "status": "pass", "n_fail": 0, "n_warn": 0, "checks": []}


def _fns(calls, ingest=None, derive=None, contracts=None):
    def ing(mode, seasons=None, force=False):
        calls.append(("ingest", mode, seasons, force))
        return ingest() if callable(ingest) else (ingest or {"status": "ok"})

    def der(seasons, force):
        calls.append(("derive", seasons, force))
        return derive() if callable(derive) else (derive or {"status": "ok"})

    def con(write=False):
        calls.append(("contracts", write))
        return contracts() if callable(contracts) else (contracts or OK_REPORT)
    return {"ingest_fn": ing, "derive_fn": der, "contracts_fn": con}


def test_stages_run_in_order_and_contracts_write_their_report():
    calls = []
    out = PL.run_pipeline(**_fns(calls))
    assert [c[0] for c in calls] == ["ingest", "derive", "contracts"]
    assert calls[2] == ("contracts", True) and out["status"] == "ok"
    assert calls[1] == ("derive", None, False)              # refresh -> derive the current season only


def test_backfill_derives_the_same_season_window():
    calls = []
    PL.run_pipeline("backfill", [2024, 2025], **_fns(calls))
    assert calls[0][:3] == ("ingest", "backfill", [2024, 2025]) and calls[1] == ("derive", [2024, 2025], False)


def test_failed_ingest_still_runs_contracts_and_fails_the_job():
    calls = []
    out = PL.run_pipeline(**_fns(calls, ingest={"status": "error", "failures": []}))
    assert [c[0] for c in calls] == ["ingest", "derive", "contracts"]
    assert out["status"] == "error"


def test_crashing_stage_is_captured_not_raised():
    def boom():
        raise RuntimeError("gcs down")
    calls = []
    out = PL.run_pipeline(**_fns(calls, ingest=boom))
    assert out["ingest"]["status"] == "error" and "gcs down" in out["ingest"]["failures"][0]["error"]
    assert [c[0] for c in calls] == ["ingest", "derive", "contracts"] and out["status"] == "error"


def test_failed_contract_does_not_fail_the_job():
    bad = {"ok": False, "status": "fail", "n_fail": 2, "n_warn": 0,
           "checks": [{"name": "coverage_pbp", "status": "fail", "detail": {}}]}
    out = PL.run_pipeline(**_fns([], contracts=bad))
    assert out["status"] == "ok" and out["contracts"]["ok"] is False and out["contracts"]["n_fail"] == 2


def test_derive_error_fails_the_job():
    out = PL.run_pipeline(**_fns([], derive={"status": "error"}))
    assert out["status"] == "error"


def test_skip_flags():
    calls = []
    out = PL.run_pipeline(derive=False, contracts=False, **_fns(calls))
    assert [c[0] for c in calls] == ["ingest"] and "derived" not in out and "contracts" not in out
