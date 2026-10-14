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
# MLB_DB_URL not set here -- existing tests use sqlite via tmp_path
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

    with patch("main._get_engine", return_value=mock_engine):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = VALID_KEY

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

    def test_cors_origin_is_beezy_fyi(self, client):
        with patch("main.get_summary_stats", return_value=SAMPLE_STATS):
            r = client.get("/api/public/stats/summary",
                           headers={"X-API-Key": VALID_KEY})
        assert "beezy.fyi" in r.headers.get("Access-Control-Allow-Origin", "")

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
        from mlb.runners.public_api import _require_api_key
        headers = {"X-API-Key": "my-secret"}
        assert _require_api_key(headers, "my-secret") is True

    def test_wrong_key_returns_false(self):
        from mlb.runners.public_api import _require_api_key
        headers = {"X-API-Key": "wrong"}
        assert _require_api_key(headers, "my-secret") is False

    def test_missing_key_returns_false(self):
        from mlb.runners.public_api import _require_api_key
        assert _require_api_key({}, "my-secret") is False

    def test_lowercase_header_returns_true(self):
        """HTTP/2 sends headers lowercase -- must still match."""
        from mlb.runners.public_api import _require_api_key
        headers = {"x-api-key": "my-secret"}
        assert _require_api_key(headers, "my-secret") is True

    def test_key_with_whitespace_stripped(self):
        from mlb.runners.public_api import _require_api_key
        headers = {"X-API-Key": "  my-secret  "}
        assert _require_api_key(headers, "my-secret") is True

    def test_empty_secret_is_handled_by_auth_required(self):
        # _require_api_key("", "") returns True (empty matches empty)
        # but _auth_required() in main.py catches SITE_API_KEY == "" before
        # calling _require_api_key, returning 500 instead.
        # This test confirms the pure function behavior is consistent.
        from mlb.runners.public_api import _require_api_key
        assert _require_api_key({"X-API-Key": "real-key"}, "") is False
        assert _require_api_key({"X-API-Key": ""}, "real-key") is False


# ---------------------------------------------------------------------------
# +EV feed + analytics deep dive
# ---------------------------------------------------------------------------

def _ev_engine():
    from sqlalchemy import create_engine, text
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text(
            "CREATE TABLE bets (id INTEGER PRIMARY KEY, system TEXT, game_date TEXT, game_pk INTEGER, "
            "bet_type TEXT, player TEXT, away_team TEXT, home_team TEXT, odds REAL, stake REAL, "
            "model_prob REAL, market_prob REAL, edge REAL, kelly_pct REAL, kelly_triggered BOOLEAN, "
            "result TEXT, profit REAL, paper BOOLEAN, book TEXT, notes TEXT, created_at TEXT, "
            "clv_pct REAL)"))
        rows = [
            # EV: flat $100 stake, +110 win, -120 loss, void, pending
            (1, "EV", "2026-10-01", 11, "K_OVER_6.5_draftkings", "Ace One", "A", "B", 110, 100, .52, .476, .045, .02, 1, "win", 110.0, 1, "draftkings", "soft-book +EV alert vs Pinnacle (5 books)", "2026-10-01T10:00", None),
            (2, "EV", "2026-10-01", 12, "HR_NO_novig", "Slug Two", "A", "B", -120, 100, .60, .545, .095, .04, 1, "loss", -100.0, 1, "novig", "soft-book +EV alert vs consensus (4 books)", "2026-10-01T11:00", None),
            (3, "EV", "2026-10-02", 13, "NRFI_caesars", None, "A", "B", -110, 100, .56, .524, .130, .03, 1, "void", 0.0, 1, "caesars", "", "2026-10-02T11:00", None),
            (4, "EV", "2026-10-03", 14, "HR_fanduel", "Slug Three", "A", "B", 300, 100, .27, .25, .06, .01, 1, None, None, 1, "fanduel", "", "2026-10-03T11:00", None),
            # model system rows
            (5, "NRFI", "2026-10-01", 15, "NRFI", None, "A", "B", -110, 10, .58, .524, 8.0, .03, 1, "win", 9.09, 1, "draftkings", "", "2026-10-01T12:00", 1.5),
            (6, "NRFI", "2026-10-02", 16, "NRFI", None, "A", "B", -110, 10, .58, .524, .02, .03, 1, "loss", -10.0, 1, "draftkings", "", "2026-10-02T12:00", None),
        ]
        for r in rows:
            c.execute(text(
                "INSERT INTO bets (id, system, game_date, game_pk, bet_type, player, away_team, home_team, odds, stake, "
                "model_prob, market_prob, edge, kelly_pct, kelly_triggered, result, profit, paper, book, notes, "
                "created_at, clv_pct) VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j,:k,:l,:m,:n,:o,:p,:q,:r,:s,:t,:u,:v)"),
                dict(zip("abcdefghijklmnopqrstuv", r)))
    return eng


def test_deep_dive_ev_headline_excludes_void_and_pending():
    from mlb.runners.public_api import get_analytics_deep_dive
    out = get_analytics_deep_dive(_ev_engine())
    h = out["ev_headline"]
    assert (h["wins"], h["losses"], h["voids"], h["pending"], h["logged"]) == (1, 1, 1, 1, 4)
    assert h["graded"] == 2
    assert h["flat_pnl_dollars"] == 10.0          # +110 - 100
    assert h["flat_roi_pct"] == 5.0               # 10 / 200, void stake excluded
    assert h["voids_excluded_from_roi"] is True


def test_deep_dive_kelly_rescales_real_ratio_with_cap():
    from mlb.runners.public_api import get_analytics_deep_dive
    h = get_analytics_deep_dive(_ev_engine(), bankroll=1000, kelly_mult=1.0)["ev_headline"]
    # win: 1.10 * (0.02*1000)=22 ; loss: -1.0 * (0.04*1000)=-40
    assert h["kelly_pnl_dollars"] == -18.0
    capped = get_analytics_deep_dive(_ev_engine(), bankroll=1000, kelly_mult=10.0)["ev_headline"]
    # mult 10 -> 0.2 / 0.4 capped at 0.05 -> 50 and 50
    assert capped["kelly_pnl_dollars"] == pytest.approx(5.0)


def test_deep_dive_edge_buckets_normalize_percent_valued_edges():
    from mlb.runners.public_api import get_analytics_deep_dive
    out = get_analytics_deep_dive(_ev_engine(), system="NRFI")
    by = {b["bucket"]: b for b in out["edge_buckets"]}
    assert by["8-12%"]["n"] == 1      # edge stored as 8.0 (percent) is read as 0.08
    assert by["0-3%"]["n"] == 1
    assert by["8-12%"]["mean_clv"] == 1.5


def test_deep_dive_book_leaderboard_and_curve():
    from mlb.runners.public_api import get_analytics_deep_dive
    out = get_analytics_deep_dive(_ev_engine())
    books = {b["book"]: b for b in out["book_leaderboard"]}
    assert books["draftkings"]["n"] == 3 and "caesars" not in books   # void excluded
    last = out["bankroll_curve"][-1]
    assert last["ev_flat_cum"] == 10.0
    assert last["models_cum"] == pytest.approx(-0.91)


def test_ev_feed_mlb_strips_book_suffix_and_parses_books():
    from mlb.runners.public_api import _ev_mlb_rows
    with patch("mlb.runners.public_api._ct_today", return_value="2026-10-03"):
        rows = _ev_mlb_rows(_ev_engine(), 0.02, 50, days=5)
    by = {r["id"]: r for r in rows}
    assert by[2]["bet_type"] == "HR_NO" and by[2]["n_books"] == 4 and by[2]["anchored"] is False
    assert by[1]["bet_type"] == "K_OVER_6.5" and by[1]["anchored"] is True
    assert rows[0]["edge"] >= rows[-1]["edge"] or rows[0]["game_date"] >= rows[-1]["game_date"]


def test_ev_feed_nfl_shadow_payload_drops_started_and_labels_unvalidated():
    from datetime import datetime, timedelta, timezone
    from mlb.runners.public_api import _nfl_feed
    now = datetime.now(timezone.utc)
    snap = {"ts": now.isoformat(), "summary": {"events_pulled": 1}, "rows": [
        {"ticker": "A", "stat": "pass_yds", "player": "P One", "strike": 249.5, "p_over": 0.4,
         "ask_yes": 0.3, "ev_yes": 0.08, "ask_no": 0.7, "ev_no": -0.1, "n_books": 5, "flag": True,
         "commence": (now + timedelta(hours=2)).isoformat()},
        {"ticker": "B", "stat": "rec_yds", "player": "P Two", "strike": 50.5, "p_over": 0.4,
         "ask_yes": 0.3, "ev_yes": 0.5, "ask_no": 0.7, "ev_no": -0.1, "n_books": 1, "flag": False,
         "commence": (now - timedelta(hours=1)).isoformat()},
        {"ticker": "C", "stat": "rec_yds", "player": "P Three", "strike": 60.5, "p_over": 0.7,
         "ask_yes": 0.9, "ev_yes": -0.2, "ask_no": 0.2, "ev_no": 0.09, "n_books": 2, "flag": False,
         "commence": (now + timedelta(hours=3)).isoformat()},
    ]}
    with patch("mlb_core.storage.read_bytes", return_value=json.dumps(snap).encode()):
        out = _nfl_feed(0.02, 50)
    tickers = [r["ticker"] for r in out["rows"]]
    assert tickers == ["A", "C"]                 # started game dropped, flagged first
    assert out["rows"][1]["side"] == "UNDER" and out["rows"][1]["fair_prob"] == 0.3
    assert out["validated"] is False and out["status"] == "shadow_live"


def test_ev_feed_nfl_unavailable_and_stale():
    from mlb.runners.public_api import _nfl_feed
    with patch("mlb_core.storage.read_bytes", side_effect=FileNotFoundError):
        assert _nfl_feed(0.02, 10)["status"] == "unavailable"
    old = {"ts": "2020-01-01T00:00:00+00:00", "rows": []}
    with patch("mlb_core.storage.read_bytes", return_value=json.dumps(old).encode()):
        out = _nfl_feed(0.02, 10)
    assert out["stale"] is True and out["status"] == "idle"


def test_new_public_routes_require_key(client):
    for path in ("/api/public/ev/feed", "/api/public/analytics/deep-dive"):
        assert client.get(path).status_code == 401
        with patch("main.get_ev_scanner_feed", return_value={"mlb": {"rows": []}}), \
             patch("main.get_analytics_deep_dive", return_value={"edge_buckets": []}):
            assert client.get(path, headers={"X-API-Key": VALID_KEY}).status_code == 200
