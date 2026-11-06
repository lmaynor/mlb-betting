"""
tests/test_waitlist.py -- POST /api/public/waitlist + mlb.runners.waitlist.

Run with: pytest tests/test_waitlist.py -v
"""
import os

import pytest
from unittest.mock import patch, MagicMock

# Set env vars before importing main (same pattern as test_public_api.py)
os.environ.setdefault("SITE_API_KEY", "test-secret-key-abc123")
os.environ.setdefault("MLB_GCS_BUCKET", "fake-bucket")

from sqlalchemy import create_engine, text  # noqa: E402

from mlb.runners.waitlist import (  # noqa: E402
    add_waitlist_signup, ensure_waitlist_table, normalize_email,
)

VALID_KEY = "test-secret-key-abc123"


def _engine():
    return create_engine("sqlite://")


def _rows(engine):
    ensure_waitlist_table(engine)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            text("SELECT email, source, consent, created_at, updated_at FROM waitlist_signups")
        ).mappings().all()]


# ---------------------------------------------------------------------------
# normalize_email
# ---------------------------------------------------------------------------

class TestNormalizeEmail:
    def test_strips_and_lowercases(self):
        assert normalize_email("  Fan@Example.COM ") == "fan@example.com"

    @pytest.mark.parametrize("bad", [
        "", "   ", "not-an-email", "missing@tld", "two@@example.com",
        "space in@example.com", "@example.com", "a@.com", None, 42, ["a@b.co"],
    ])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            normalize_email(bad)

    def test_error_does_not_echo_the_address(self):
        with pytest.raises(ValueError) as ei:
            normalize_email("definitely not an email")
        assert "definitely" not in str(ei.value)


# ---------------------------------------------------------------------------
# add_waitlist_signup (sqlite engine, same approach as test_public_api's
# _ev_engine function-level tests)
# ---------------------------------------------------------------------------

class TestAddWaitlistSignup:
    def test_valid_signup_is_created(self):
        eng = _engine()
        out = add_waitlist_signup(eng, "fan@example.com", source="pricing")
        assert out == {"status": "created"}
        rows = _rows(eng)
        assert len(rows) == 1
        assert rows[0]["email"] == "fan@example.com"
        assert rows[0]["source"] == "pricing"
        assert rows[0]["consent"] in (True, 1)
        assert rows[0]["created_at"] and rows[0]["updated_at"]

    def test_case_is_normalized_before_storage(self):
        eng = _engine()
        add_waitlist_signup(eng, "  Fan@Example.COM ")
        rows = _rows(eng)
        assert [r["email"] for r in rows] == ["fan@example.com"]

    def test_duplicate_signup_is_idempotent_and_refreshes_source(self):
        eng = _engine()
        first = add_waitlist_signup(eng, "fan@example.com", source="pricing")
        dupe = add_waitlist_signup(eng, "FAN@example.com", source="footer")
        assert first["status"] == "created"
        assert dupe["status"] == "existing"
        rows = _rows(eng)
        assert len(rows) == 1
        assert rows[0]["source"] == "footer"

    def test_invalid_email_stores_nothing(self):
        eng = _engine()
        with pytest.raises(ValueError):
            add_waitlist_signup(eng, "nope")
        assert _rows(eng) == []

    def test_source_is_sanitized_never_stored_raw(self):
        eng = _engine()
        add_waitlist_signup(eng, "fan@example.com", source="javascript:alert(1)")
        # only [a-z0-9_-] survive _clean_source -- punctuation is stripped
        assert _rows(eng)[0]["source"] == "javascriptalert1"


# ---------------------------------------------------------------------------
# The Flask route (test client pattern from test_public_api.py)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    mock_engine = MagicMock()
    with patch("main._get_engine", return_value=mock_engine):
        import main as m
        m.app.config["TESTING"] = True
        with m.app.test_client() as c:
            yield c


class TestWaitlistRoute:
    def test_no_key_returns_401(self, client):
        with patch("main.add_waitlist_signup") as fn:
            r = client.post("/api/public/waitlist", json={"email": "fan@example.com"})
        assert r.status_code == 401
        fn.assert_not_called()

    def test_valid_signup_returns_ok(self, client):
        with patch("main.add_waitlist_signup", return_value={"status": "created"}) as fn:
            r = client.post("/api/public/waitlist",
                            json={"email": "fan@example.com", "source": "pricing"},
                            headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "status": "created"}
        args = fn.call_args.args
        assert args[1] == "fan@example.com" and args[2] == "pricing"

    def test_repeat_signup_reports_existing(self, client):
        with patch("main.add_waitlist_signup", return_value={"status": "existing"}):
            r = client.post("/api/public/waitlist",
                            json={"email": "fan@example.com"},
                            headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        assert r.get_json()["status"] == "existing"

    def test_invalid_email_returns_400(self, client):
        with patch("main.add_waitlist_signup", side_effect=ValueError("invalid email address")):
            r = client.post("/api/public/waitlist",
                            json={"email": "nope"},
                            headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 400
        assert r.get_json() == {"error": "invalid email address"}

    def test_options_preflight_allows_post(self, client):
        r = client.options("/api/public/waitlist")
        assert r.status_code == 204
        assert "POST" in r.headers.get("Access-Control-Allow-Methods", "")
