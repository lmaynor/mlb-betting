"""
tests/test_admin_auth.py -- Regression coverage for the 2026-08-16 audit's
main.py auth-hardening fix (finding A5): admin/backfill routes that had zero
authentication now require X-API-Key, and the 9 Cloud-Scheduler-triggered
routes get a feature-flagged (off by default) OIDC verification gate that
must never fire unless ENFORCE_SCHEDULER_AUTH is explicitly set -- a false
positive there would silently stop every automated betting/settlement run.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.

Run with: pytest tests/test_admin_auth.py -v
"""
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SITE_API_KEY", "test-secret-key-abc123")
os.environ.setdefault("MLB_GCS_BUCKET", "fake-bucket")

VALID_KEY = "test-secret-key-abc123"


@pytest.fixture(scope="module")
def client():
    mock_engine = MagicMock()
    with patch("main._get_engine", return_value=mock_engine):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = VALID_KEY
        with m.app.test_client() as c:
            yield c


@pytest.fixture(scope="module")
def m():
    import main as _m
    return _m


# ---------------------------------------------------------------------------
# Previously-unauthenticated admin/backfill routes now require X-API-Key
# ---------------------------------------------------------------------------

class TestAdminRoutesNowRequireApiKey:
    def test_dashboard_no_key_returns_401(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 401

    def test_dashboard_wrong_key_returns_401(self, client):
        r = client.get("/dashboard", headers={"X-API-Key": "wrong"})
        assert r.status_code == 401

    def test_diagnose_nrfi_drift_no_key_returns_401(self, client):
        r = client.post("/diagnose-nrfi-drift", json={})
        assert r.status_code == 401

    def test_backfill_data_no_key_returns_401(self, client):
        r = client.post("/backfill-data", json={"start_date": "2026-04-01"})
        assert r.status_code == 401

    def test_backfill_savant_no_key_returns_401(self, client):
        r = client.post("/backfill-savant", json={})
        assert r.status_code == 401

    def test_backfill_statcast_no_key_returns_401(self, client):
        r = client.post("/backfill-statcast", json={"dates": ["2026-05-01"]})
        assert r.status_code == 401

    def test_build_features_no_key_returns_401(self, client):
        r = client.post("/build-features", json={})
        assert r.status_code == 401

    def test_build_all_features_no_key_returns_401(self, client):
        r = client.post("/build-all-features", json={})
        assert r.status_code == 401

    def test_admin_backfill_notes_no_key_returns_401(self, client):
        r = client.post("/admin/backfill-notes", json={})
        assert r.status_code == 401

    def test_retrain_outs_no_key_returns_401(self, client):
        r = client.post("/retrain-outs", json={})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# /dashboard system whitelist: "1I" is a currently-active registry system
# and must not 400 (regression for the drift found in the same audit pass)
# ---------------------------------------------------------------------------

class TestDashboardSystemFilter:
    def test_system_1i_is_accepted_not_400(self, client, monkeypatch):
        # No real Postgres in this test env (the dashboard query uses
        # Postgres-only FILTER clause syntax, and Flask TESTING mode
        # re-raises unhandled exceptions rather than returning a clean 500),
        # so any failure past the whitelist check is an environment
        # limitation and is tolerated here -- this test only cares about
        # the whitelist check itself specifically rejecting '1I'.
        monkeypatch.setenv("MLB_DB_URL", "postgresql+pg8000://fake:fake@localhost/fake")
        try:
            r = client.get("/dashboard?system=1I", headers={"X-API-Key": VALID_KEY})
        except Exception as e:
            assert "Invalid system" not in str(e), (
                "the dashboard system whitelist rejected '1I' -- it is a "
                "currently-active registry system, this is a regression"
            )
            return
        if r.status_code == 400:
            assert b"Invalid system" not in r.data, (
                "the dashboard system whitelist rejected '1I' -- it is a "
                "currently-active registry system, this is a regression"
            )


# ---------------------------------------------------------------------------
# Scheduler OIDC gate: must be a true no-op unless explicitly enabled
# ---------------------------------------------------------------------------

class TestSchedulerAuthDefaultOff:
    def test_returns_none_when_flag_unset(self, m, monkeypatch):
        monkeypatch.delenv("ENFORCE_SCHEDULER_AUTH", raising=False)
        with m.app.test_request_context("/run", headers={}):
            assert m._scheduler_auth_required(m.request) is None

    def test_returns_none_when_flag_false(self, m, monkeypatch):
        monkeypatch.setenv("ENFORCE_SCHEDULER_AUTH", "false")
        with m.app.test_request_context("/run", headers={}):
            assert m._scheduler_auth_required(m.request) is None

    def test_returns_none_even_with_no_auth_header_at_all(self, m, monkeypatch):
        """The whole point of default-off: a real scheduler-triggered request
        with no Authorization header handling issues must not be rejected
        unless an operator has explicitly opted in."""
        monkeypatch.delenv("ENFORCE_SCHEDULER_AUTH", raising=False)
        with m.app.test_request_context("/settle", headers={"Authorization": "garbage"}):
            assert m._scheduler_auth_required(m.request) is None


class TestSchedulerAuthWhenEnabled:
    def test_missing_authorization_header_rejected(self, m, monkeypatch):
        monkeypatch.setenv("ENFORCE_SCHEDULER_AUTH", "1")
        with m.app.test_request_context("/run", headers={}):
            err = m._scheduler_auth_required(m.request)
            assert err is not None
            assert err[1] == 401

    def test_malformed_bearer_token_rejected_not_crashed(self, m, monkeypatch):
        monkeypatch.setenv("ENFORCE_SCHEDULER_AUTH", "true")
        with m.app.test_request_context("/run", headers={"Authorization": "Bearer not-a-real-jwt"}):
            err = m._scheduler_auth_required(m.request)
            assert err is not None
            assert err[1] == 401

    def test_verify_scheduler_oidc_false_on_non_bearer_header(self, m, monkeypatch):
        with m.app.test_request_context("/run", headers={"Authorization": "Basic xyz"}):
            assert m._verify_scheduler_oidc(m.request) is False

    def test_verify_scheduler_oidc_false_on_missing_header(self, m, monkeypatch):
        with m.app.test_request_context("/run", headers={}):
            assert m._verify_scheduler_oidc(m.request) is False
