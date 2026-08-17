"""
Regression tests for the 2026-08-16 audit's odds_alert.py Discord-posting
fix (finding C6.9): unlike its sibling pagers, odds_alert.py's per-market
lag-vs-informed scorecard -- described in its own docstring as "the
empirical proof of whether books lag is bankable," the decisive evidence
for the soft-line strategy -- only ever reached Alerts/{day}/*.parquet and
Cloud Logging. Nobody was paged on a freshness failure either.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pandas as pd
import pytest

import mlb.runners.odds_alert as oa


# ---------------------------------------------------------------------------
# _post_freshness_alert / _post_scorecard in isolation
# ---------------------------------------------------------------------------

def test_post_freshness_alert_posts_to_ops_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr("mlb_core.notify.discord._get_ops_webhook", lambda: "https://fake-ops-webhook")
    monkeypatch.setattr("mlb_core.notify.discord._post", lambda url, payload: calls.append((url, payload)))

    oa._post_freshness_alert({"ok": False, "reasons": ["stale: outs_ou 7h old"]}, "2026-08-17")

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "https://fake-ops-webhook"
    assert "outs_ou" in payload["embeds"][0]["description"]


def test_post_freshness_alert_noop_without_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr("mlb_core.notify.discord._get_ops_webhook", lambda: None)
    monkeypatch.setattr("mlb_core.notify.discord._post", lambda url, payload: calls.append((url, payload)))

    oa._post_freshness_alert({"ok": False, "reasons": ["x"]}, "2026-08-17")
    assert calls == []


def test_post_scorecard_posts_to_performance_webhook(monkeypatch):
    calls = []
    monkeypatch.setenv("DISCORD_WEBHOOK_PERFORMANCE", "https://fake-perf-webhook")
    monkeypatch.setattr("mlb_core.notify.discord._post", lambda url, payload: calls.append((url, payload)))

    scorecard = pd.DataFrame(
        {"alerts": [3], "entry_ev": [0.05], "ev_at_close": [0.02], "held_up_pct": [0.67]},
        index=pd.Index(["hr_yn"], name="market"),
    )
    oa._post_scorecard(scorecard, "2026-08-17", n_new=3)

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "https://fake-perf-webhook"
    field_names = [f["name"] for f in payload["embeds"][0]["fields"]]
    assert "hr_yn" in field_names


def test_post_scorecard_falls_back_to_ops_webhook(monkeypatch):
    calls = []
    monkeypatch.delenv("DISCORD_WEBHOOK_PERFORMANCE", raising=False)
    monkeypatch.setattr("mlb_core.notify.discord._get_ops_webhook", lambda: "https://fake-ops-webhook")
    monkeypatch.setattr("mlb_core.notify.discord._post", lambda url, payload: calls.append((url, payload)))

    scorecard = pd.DataFrame(
        {"alerts": [1], "entry_ev": [0.04], "ev_at_close": [-0.01], "held_up_pct": [0.0]},
        index=pd.Index(["k_ou"], name="market"),
    )
    oa._post_scorecard(scorecard, "2026-08-17", n_new=1)
    assert calls and calls[0][0] == "https://fake-ops-webhook"


# ---------------------------------------------------------------------------
# Wiring inside run()
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_run_io(monkeypatch, tmp_path):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)


def test_run_pages_on_freshness_failure(monkeypatch, stub_run_io):
    freshness_calls = []
    monkeypatch.setattr(oa.fresh, "check", lambda markets, today: {"ok": False, "reasons": ["stale"]})
    monkeypatch.setattr(oa.osc, "scan_markets", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr(oa, "_post_freshness_alert", lambda fr, day: freshness_calls.append(day))

    oa.run(run_date="2026-08-17")
    assert freshness_calls == ["2026-08-17"]


def test_run_does_not_page_freshness_when_ok(monkeypatch, stub_run_io):
    freshness_calls = []
    monkeypatch.setattr(oa.fresh, "check", lambda markets, today: {"ok": True, "today_snaps": 5, "parlay_today": 3})
    monkeypatch.setattr(oa.osc, "scan_markets", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr(oa, "_post_freshness_alert", lambda fr, day: freshness_calls.append(day))

    oa.run(run_date="2026-08-17")
    assert freshness_calls == []


def test_run_posts_scorecard_only_when_new_alerts_found(monkeypatch, stub_run_io):
    """The exact conditional this fix's wiring adds: no new alerts this run
    -> no repost of the (unchanged) cumulative scorecard, even if a
    scorecard exists from earlier today."""
    scorecard_calls = []
    monkeypatch.setattr(oa.fresh, "check", lambda markets, today: {"ok": True, "today_snaps": 1, "parlay_today": 1})
    monkeypatch.setattr(oa, "_post_scorecard", lambda sc, day, n_new: scorecard_calls.append(n_new))

    found_row = {
        "market": "hr_yn", "game_pk": 123, "player_id": 456, "line": 0.5,
        "selection": "OVER", "book": "draftkings", "ev": 0.05, "decimal": 2.5,
        "snapshot_ts": "2026-08-17 12:00:00",
    }

    # Run 1: one new alert -> scorecard resolves and should post.
    monkeypatch.setattr(oa.osc, "scan_markets", lambda *a, **kw: pd.DataFrame([found_row]))
    monkeypatch.setattr(oa, "_latest_consensus", lambda markets, day: {
        ("hr_yn", 123, 456, 0.5, "OVER"): 0.55
    })
    oa.run(run_date="2026-08-17")
    assert scorecard_calls == [1], f"expected exactly one post with n_new=1, got {scorecard_calls}"

    # Run 2: scan finds nothing NEW this time (same day, alert already
    # logged) -> must not repost the identical cumulative scorecard.
    monkeypatch.setattr(oa.osc, "scan_markets", lambda *a, **kw: pd.DataFrame())
    oa.run(run_date="2026-08-17")
    assert scorecard_calls == [1], (
        f"reposted the scorecard on a run with zero new alerts: {scorecard_calls}"
    )
