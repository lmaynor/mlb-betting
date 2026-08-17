"""
Regression test for the 2026-08-16 audit's bakeoff --resume fix (finding
B4.1): the deployed mlb-bakeoff Cloud Run Job never passed --resume on its
hr_model_bakeoff.py leg, so the job's own configured retry (--max-retries=1)
restarted HR's entire 7-candidate tuning exercise from scratch on every
automatic retry.

hr_model_bakeoff.py already fully supported --resume for a run that already
has SOMETHING persisted -- the gap was the deploy script's orchestration
(fixed separately in deploy/setup_bakeoff_job.sh) needing a way to say
"resume this deterministic id if it exists, otherwise start it fresh" in one
shot, since a genuine first attempt has nothing to resume yet. This test
exercises the new --create-if-missing flag end-to-end through the real
main(), with GCS I/O faked and the (heavy, unrelated) run() stubbed out.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import json

import pandas as pd
import pytest

import mlb.analysis.hr_model_bakeoff as hb


@pytest.fixture
def fake_gcs(monkeypatch):
    """In-memory stand-in for the handful of mlb_core.storage primitives
    bakeoff_persist.py funnels every GCS read/write through."""
    store: dict[str, bytes] = {}

    def _write_bytes(data, key):
        store[key] = data

    def _read_bytes(key):
        if key not in store:
            raise FileNotFoundError(key)
        return store[key]

    def _write_csv(df, key, index=False):
        store[key] = df.to_csv(index=index).encode()

    def _read_csv(key, **kwargs):
        if key not in store:
            raise FileNotFoundError(key)
        import io
        return pd.read_csv(io.BytesIO(store[key]))

    def _exists(key):
        return key in store

    monkeypatch.setattr("mlb_core.storage.write_bytes", _write_bytes)
    monkeypatch.setattr("mlb_core.storage.read_bytes", _read_bytes)
    monkeypatch.setattr("mlb_core.storage.write_csv", _write_csv)
    monkeypatch.setattr("mlb_core.storage.read_csv", _read_csv)
    monkeypatch.setattr("mlb_core.storage.exists", _exists)
    return store


@pytest.fixture
def stub_run(monkeypatch):
    """run() does real backtest+training work against real GCS features --
    irrelevant to this fix. Stub it to a cheap fake scorecard, and capture
    every call's persist_prefix so the test can assert on it."""
    calls = []

    def _fake_run(cutoff, until, models, min_books, max_spread, calibrate,
                  tune_trials=30, tune_folds=3, load_tuned_from=None, persist_prefix=None):
        calls.append(persist_prefix)
        return pd.DataFrame([{"model": "xgb", "verdict": "NO_EDGE", "hi_n": 0, "hi_clv%": None}]
                            ).set_index("model")

    monkeypatch.setattr(hb, "run", _fake_run)
    return calls


def test_create_if_missing_starts_fresh_on_true_first_attempt(fake_gcs, stub_run):
    run_id = "2026-06-01_deadbeef_120000"
    rc = hb.main(["--cutoff", "2026-06-01", "--resume", run_id, "--create-if-missing"])
    assert rc == 0

    prefix = hb.bakeoff_persist.run_prefix(run_id)
    meta = json.loads(fake_gcs[f"{prefix}/run_meta.json"])
    assert meta["run_id"] == run_id
    assert "HR" in (meta.get("systems_completed") or [])
    assert stub_run == [prefix]


def test_retry_with_same_id_resumes_instead_of_restarting(fake_gcs, stub_run):
    """The whole point: a second invocation with the SAME --resume id
    (simulating Cloud Run Jobs' automatic retry, which recomputes the same
    deterministic HR_RUN_ID) must pick up the prior attempt's persisted
    state, not silently start a brand-new run."""
    run_id = "2026-06-01_deadbeef_120000"
    args = ["--cutoff", "2026-06-01", "--resume", run_id, "--create-if-missing"]

    rc1 = hb.main(args)
    assert rc1 == 0
    prefix = hb.bakeoff_persist.run_prefix(run_id)
    meta_after_first = json.loads(fake_gcs[f"{prefix}/run_meta.json"])
    first_started_at = meta_after_first["started_at"]

    # Second call, identical args (the "retry") -- HR is already in
    # systems_completed from the first call, so main() should no-op (return
    # 0 immediately) rather than calling run() again.
    rc2 = hb.main(args)
    assert rc2 == 0
    assert len(stub_run) == 1, "the retry re-ran HR's tuning from scratch instead of resuming"

    meta_after_second = json.loads(fake_gcs[f"{prefix}/run_meta.json"])
    assert meta_after_second["started_at"] == first_started_at, (
        "the retry created a brand-new run_meta instead of reusing the same run"
    )


def test_resume_without_create_if_missing_still_errors_on_typo(fake_gcs, stub_run):
    """The strict, pre-existing behavior must survive for interactive use:
    a genuinely bad --resume id (no --create-if-missing) should still error
    loudly, not silently start an empty run."""
    with pytest.raises(SystemExit):
        hb.main(["--cutoff", "2026-06-01", "--resume", "typo-id-that-does-not-exist"])
    assert stub_run == []
