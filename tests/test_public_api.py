"""
tests/test_public_api.py -- Tests for the beezy-vip public API routes.

Run with: pytest tests/test_public_api.py -v
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

# Set env vars before importing main
os.environ.setdefault("SITE_API_KEY", "test-secret-key-abc123")
os.environ.setdefault("MLB_DB_URL", "postgresql://fake/fake")
os.environ.setdefault("MLB_GCS_BUCKET", "fake-bucket")

VALID_KEY = "test-secret-key-abc123"
WRONG_KEY = "wrong-key"

SAMPLE_PICKS = [
    {
        "id": 1, "system": "NRFI", "game_date": "2026-05-15",
        "game_pk": 12345, "bet_type": "NRFI", "player": None,
        "away_team": "NYY", "home_team": "BOS",
        "odds": -115, "stake": 10.0, "model_prob": 0.62,
        "market_prob": 0.55, "edge": 0.07, "kelly_pct": 0.05,
        "kelly_triggered": True, "result": None, "profit": None,
        "paper": True, "notes": None, "created_at": "2026-05-15T12:00:00",
    }
]

SAMPLE_STATS = {
    "overall":  {"total_bets": "87", "win_rate": "58.2", "roi": "11.4", "avg_edge": "4.8"},
    "bySystem": [
        {"system": "NRFI", "total_bets": 28, "wins": 17, "losses": 11,
         "pushes": 0, "win_rate": 61.5, "roi": 14.2, "total_pnl": 39.76, "avg_edge": 6.1}
    ],
}


@pytest.fixture(scope="module")
def client():
    """
    Flask test client. Patches engine and DB init so main.py loads
    without a real Cloud SQL connection.
    """
    mock_engine = MagicMock()

    with patch("sqlalchemy.create_engine", return_value=mock_engine), \
         patch("main.engine", mock_engine, create=True):

        import main as m
        m.app.config["TESTING"] = True
        # Inject the test key and mock engine into the module
        m.SITE_API_KEY = VALID_KEY
        m.engine = mock_engine

        with m.app.test_client() as c:
            yield c


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_key_returns_401(self, client):
        r = client.get("/api/public/stats/summary")
        assert r.status_code == 401
        assert b"Unauthorized" in r.data

    def test_wrong_key_returns_401(self, client):
        r = client.get("/api/public/stats/summary",
                       headers={"X-API-Key": WRONG_KEY})
        assert r.status_code == 401

    def test_correct_key_uppercase_header(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200

    def test_correct_key_lowercase_header(self, client):
        """HTTP/2 lowercases all headers -- must work with x-api-key too."""
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"x-api-key": VALID_KEY})
        assert r.status_code == 200

    def test_options_preflight_no_auth_required(self, client):
        """CORS preflight must succeed without API key."""
        r = client.options("/api/public/stats/summary")
        assert r.status_code == 204

    def test_empty_site_api_key_returns_500(self, client):
        import main as m
        original = m.SITE_API_KEY
        m.SITE_API_KEY = ""
        try:
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
            assert r.status_code == 500
            assert b"SITE_API_KEY" in r.data
        finally:
            m.SITE_API_KEY = original


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

class TestCORS:
    def test_cors_header_present(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        assert "Access-Control-Allow-Origin" in r.headers

    def test_cors_origin_is_beezy_vip(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        assert "beezy.vip" in r.headers.get("Access-Control-Allow-Origin", "")

    def test_preflight_cors_headers(self, client):
        r = client.options("/api/public/picks/today")
        assert r.status_code == 204
        assert "Access-Control-Allow-Origin" in r.headers


# ---------------------------------------------------------------------------
# /api/public/picks/today
# ---------------------------------------------------------------------------

class TestPicksToday:
    def test_returns_picks_and_count(self, client):
        with patch("main.get_today_picks", return_value=SAMPLE_PICKS):
            r = client.get("/api/public/picks/today",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "picks" in data
        assert "count" in data
        assert data["count"] == len(data["picks"])

    def test_empty_slate_returns_empty_list(self, client):
        with patch("main.get_today_picks", return_value=[]):
            r = client.get("/api/public/picks/today",
                           headers={"X-API-Key": VALID_KEY})
        data = json.loads(r.data)
        assert data["picks"] == []
        assert data["count"] == 0

    def test_pick_schema_uses_odds_not_line(self, client):
        with patch("main.get_today_picks", return_value=SAMPLE_PICKS):
            r = client.get("/api/public/picks/today",
                           headers={"X-API-Key": VALID_KEY})
        pick = json.loads(r.data)["picks"][0]
        assert "odds" in pick
        assert "line" not in pick

    def test_pick_schema_uses_profit_not_pnl(self, client):
        with patch("main.get_today_picks", return_value=SAMPLE_PICKS):
            r = client.get("/api/public/picks/today",
                           headers={"X-API-Key": VALID_KEY})
        pick = json.loads(r.data)["picks"][0]
        assert "profit" in pick
        assert "pnl" not in pick

    def test_pick_schema_uses_market_prob_not_implied_prob(self, client):
        with patch("main.get_today_picks", return_value=SAMPLE_PICKS):
            r = client.get("/api/public/picks/today",
                           headers={"X-API-Key": VALID_KEY})
        pick = json.loads(r.data)["picks"][0]
        assert "market_prob" in pick
        assert "implied_prob" not in pick


# ---------------------------------------------------------------------------
# /api/public/picks
# ---------------------------------------------------------------------------

class TestPicks:
    def test_no_filters_returns_200(self, client):
        with patch("main.get_picks", return_value=SAMPLE_PICKS):
            r = client.get("/api/public/picks",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200

    def test_system_filter_passed_through(self, client):
        with patch("main.get_picks", return_value=[]) as mock:
            r = client.get("/api/public/picks?system=NRFI",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        # Verify mock was called (engine error means it wasn't)
        mock.assert_called_once()

    def test_status_won_filter(self, client):
        with patch("main.get_picks", return_value=[]) as mock:
            r = client.get("/api/public/picks?status=settled",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        mock.assert_called_once()

    def test_limit_offset_passed_through(self, client):
        with patch("main.get_picks", return_value=[]) as mock:
            r = client.get("/api/public/picks?limit=10&offset=20",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        mock.assert_called_once()


# ---------------------------------------------------------------------------
# /api/public/picks/recent
# ---------------------------------------------------------------------------

class TestPicksRecent:
    def test_returns_settled_picks(self, client):
        settled = [{**SAMPLE_PICKS[0], "result": "win", "profit": 8.7}]
        with patch("main.get_recent_settled", return_value=settled):
            r = client.get("/api/public/picks/recent",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["picks"][0]["result"] == "win"

    def test_custom_limit(self, client):
        with patch("main.get_recent_settled", return_value=[]) as mock:
            r = client.get("/api/public/picks/recent?limit=5",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        mock.assert_called_once()


# ---------------------------------------------------------------------------
# /api/public/stats/summary
# ---------------------------------------------------------------------------

class TestStats:
    def test_returns_overall_and_by_system(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "overall" in data
        assert "bySystem" in data

    def test_overall_has_required_fields(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        overall = json.loads(r.data)["overall"]
        for field in ("total_bets", "win_rate", "roi", "avg_edge"):
            assert field in overall

    def test_by_system_has_required_fields(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        entry = json.loads(r.data)["bySystem"][0]
        for field in ("system", "total_bets", "wins", "losses",
                      "win_rate", "roi", "total_pnl", "avg_edge"):
            assert field in entry

    def test_cache_control_header_present(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        assert "Cache-Control" in r.headers


# ---------------------------------------------------------------------------
# Result values contract
# ---------------------------------------------------------------------------

class TestResultValues:
    def test_result_values_are_lowercase(self, client):
        picks_with_results = [
            {**SAMPLE_PICKS[0], "result": "win",  "profit": 8.7,   "id": 1},
            {**SAMPLE_PICKS[0], "result": "loss", "profit": -10.0, "id": 2},
            {**SAMPLE_PICKS[0], "result": "push", "profit": 0.0,   "id": 3},
            {**SAMPLE_PICKS[0], "result": "void", "profit": 0.0,   "id": 4},
        ]
        with patch("main.get_recent_settled", return_value=picks_with_results):
            r = client.get("/api/public/picks/recent",
                           headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
        results = [p["result"] for p in json.loads(r.data)["picks"]]
        for result in results:
            assert result == result.lower(), f"result not lowercase: {result}"
        assert "W" not in results
        assert "L" not in results
        assert "P" not in results


# ---------------------------------------------------------------------------
# Existing routes unaffected
# ---------------------------------------------------------------------------

class TestExistingRoutesUnaffected:
    def test_healthz_still_200(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200

    def test_healthz_no_api_key_needed(self, client):
        r = client.get("/healthz")
        assert b"Unauthorized" not in r.data


# ---------------------------------------------------------------------------
# public_api.py unit tests (no Flask needed)
# ---------------------------------------------------------------------------

class TestRequireApiKey:
    def test_correct_key_returns_true(self):
        from runners.public_api import _require_api_key
        headers = {"X-API-Key": "my-secret"}
        assert _require_api_key(headers, "my-secret") is True

    def test_wrong_key_returns_false(self):
        from runners.public_api import _require_api_key
        headers = {"X-API-Key": "wrong"}
        assert _require_api_key(headers, "my-secret") is False

    def test_missing_key_returns_false(self):
        from runners.public_api import _require_api_key
        assert _require_api_key({}, "my-secret") is False

    def test_lowercase_header_returns_true(self):
        """HTTP/2 sends headers lowercase -- must still match."""
        from runners.public_api import _require_api_key
        headers = {"x-api-key": "my-secret"}
        assert _require_api_key(headers, "my-secret") is True

    def test_key_with_whitespace_stripped(self):
        from runners.public_api import _require_api_key
        headers = {"X-API-Key": "  my-secret  "}
        assert _require_api_key(headers, "my-secret") is True

    def test_empty_secret_is_handled_by_auth_required(self):
        # _require_api_key("", "") returns True (empty matches empty)
        # but _auth_required() in main.py catches SITE_API_KEY == "" before
        # calling _require_api_key, returning 500 instead.
        # This test confirms the pure function behavior is consistent.
        from runners.public_api import _require_api_key
        assert _require_api_key({"X-API-Key": "real-key"}, "") is False
        assert _require_api_key({"X-API-Key": ""}, "real-key") is False
