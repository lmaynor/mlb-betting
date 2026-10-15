"""
tests/test_tweet_drafter.py -- Regression coverage for the 2026-09-02 fix to
tweet_drafter.build_recap_prompt(): it used to filter settled bets on
`game_date == str(date.today())`, which can never match because /settle
always settles YESTERDAY's slate and this job runs an hour later, after
date.today() has already rolled forward. The recap job silently produced
zero output every single day as a result.

See docs/solutions/logic-errors/tweet-recap-date-filter-never-matches.md and
docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md
(the companion TWEET_MODE env var bug found in the same investigation).

Run with: pytest tests/test_tweet_drafter.py -v
"""
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SITE_API_KEY", "test-site-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("TYPEFULLY_API_KEY", "test-typefully-key")

import tweet_drafter as td

YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
TWO_DAYS_AGO = (date.today() - timedelta(days=2)).isoformat()


def _bet(game_date, result, profit, **kw):
    row = {
        "game_date": game_date,
        "result": result,
        "profit": profit,
        "system": "HR",
        "game": "NYY @ BOS",
        "bet_type": "HR",
        "odds": -110,
    }
    row.update(kw)
    return row


def test_build_recap_prompt_empty_settled_returns_none():
    assert td.build_recap_prompt([], {"overall": {}}) is None


def test_build_recap_prompt_uses_most_recent_settled_date_not_today():
    """The core regression: real settled bets never carry game_date ==
    str(date.today()) at the time this job actually runs (see module
    docstring). A fixture dated "yesterday" -- exactly what /settle produces
    -- must still produce a real prompt, not None."""
    settled = [
        _bet(YESTERDAY, "win", 1.5),
        _bet(YESTERDAY, "loss", -1.0),
    ]
    prompt = td.build_recap_prompt(settled, {"overall": {"roi": 5.0, "total_bets": 500}})
    assert prompt is not None
    assert "Record: 1-1" in prompt
    assert "500 bets" in prompt


def test_build_recap_prompt_ignores_stale_older_dates():
    """A straggler from an earlier retry (older game_date) must not be
    counted alongside the actual most-recent settled slate, and must not
    corrupt which date the recap is labeled with."""
    settled = [
        _bet(YESTERDAY, "win", 2.0),
        _bet(YESTERDAY, "win", 1.0),
        _bet(TWO_DAYS_AGO, "loss", -3.0),  # stale, should be excluded
    ]
    prompt = td.build_recap_prompt(settled, {"overall": {"roi": 1.0, "total_bets": 10}})
    assert prompt is not None
    assert "Record: 2-0" in prompt
    assert "+3.00u" in prompt  # only the two YESTERDAY wins counted


def test_build_recap_prompt_profit_and_record_math():
    settled = [
        _bet(YESTERDAY, "win", 1.91),
        _bet(YESTERDAY, "loss", -1.0),
        _bet(YESTERDAY, "push", 0.0),
    ]
    prompt = td.build_recap_prompt(settled, {"overall": {"roi": 2.0, "total_bets": 42}})
    assert "Record: 1-1" in prompt  # push counted in neither wins nor losses
    assert "+0.91u" in prompt

    for expected_line in ("HR", "NYY @ BOS"):
        assert expected_line in prompt


def test_build_recap_prompt_no_win_loss_push_rows_returns_none():
    """Rows present but none carry a settled result (e.g. all still void or
    None) -- nothing to recap."""
    settled = [_bet(YESTERDAY, "void", 0.0)]
    assert td.build_recap_prompt(settled, {"overall": {}}) is None


def test_build_picks_prompt_empty_returns_none():
    assert td.build_picks_prompt([]) is None


def test_build_picks_prompt_includes_edge_and_link():
    picks = [{
        "system": "HR", "game": "NYY @ BOS", "bet_type": "HR",
        "odds": "+450", "edge": "8.2", "stake": "0.4", "notes": "",
    }]
    prompt = td.build_picks_prompt(picks)
    assert prompt is not None
    assert td.BEEZY_SITE_URL + "/cheat-sheet" in prompt


def _gemini_response(text, finish_reason="STOP", usage=None):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "candidates": [{
            "content": {"parts": [{"text": text}]} if text is not None else {},
            "finishReason": finish_reason,
        }],
        "usageMetadata": usage or {"totalTokenCount": 512},
    }
    return resp


def test_generate_tweets_happy_path():
    with patch("tweet_drafter.requests.post", return_value=_gemini_response(
        '["Tweet one", "Tweet two", "Tweet three"]'
    )):
        tweets = td.generate_tweets("some prompt")
    assert tweets == ["Tweet one", "Tweet two", "Tweet three"]


def test_generate_tweets_strips_markdown_fences():
    fenced = '```json\n["a", "b"]\n```'
    with patch("tweet_drafter.requests.post", return_value=_gemini_response(fenced)):
        tweets = td.generate_tweets("some prompt")
    assert tweets == ["a", "b"]


def test_generate_tweets_empty_text_raises_clear_error_not_json_decode_error():
    """Regression for the 2026-09-02 live crash: newer Gemini generations
    can spend the entire maxOutputTokens budget on internal "thinking"
    tokens, returning empty visible text. This used to surface as a bare,
    confusing json.decoder.JSONDecodeError three frames removed from the
    real cause -- it must now raise a clear, diagnosable RuntimeError
    instead, with finishReason/usageMetadata included."""
    with patch("tweet_drafter.requests.post", return_value=_gemini_response(
        "", finish_reason="MAX_TOKENS", usage={"totalTokenCount": 1536}
    )):
        with pytest.raises(RuntimeError, match="MAX_TOKENS"):
            td.generate_tweets("some prompt")


def test_generate_tweets_missing_parts_raises_clear_error():
    """Defends the same failure mode when Gemini omits `parts` entirely
    (not just an empty string within it)."""
    with patch("tweet_drafter.requests.post", return_value=_gemini_response(
        None, finish_reason="MAX_TOKENS"
    )):
        with pytest.raises(RuntimeError, match="no visible text"):
            td.generate_tweets("some prompt")


def _social_sets_response(results):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"results": results}
    return resp


def _draft_response(status_code, body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = text
    return resp


def test_get_social_set_id_matches_beezy_fyi_username():
    sets = _social_sets_response([
        {"id": 111, "username": "someone_else"},
        {"id": 222, "username": "beezy_fyi"},
    ])
    with patch("tweet_drafter.requests.get", return_value=sets):
        assert td._get_social_set_id() == 222


def test_get_social_set_id_falls_back_to_first_result():
    sets = _social_sets_response([{"id": 333, "username": "someone_else"}])
    with patch("tweet_drafter.requests.get", return_value=sets):
        assert td._get_social_set_id() == 333


def test_get_social_set_id_no_results_raises():
    with patch("tweet_drafter.requests.get", return_value=_social_sets_response([])):
        with pytest.raises(RuntimeError, match="no results"):
            td._get_social_set_id()


def test_push_to_typefully_happy_path_v2():
    sets = _social_sets_response([{"id": 42, "username": "beezy_fyi"}])
    created = _draft_response(201, {"id": 1, "private_url": "https://typefully.com/?d=1"})
    with patch("tweet_drafter.requests.get", return_value=sets), \
         patch("tweet_drafter.requests.post", return_value=created) as mock_post:
        result = td.push_to_typefully(["tweet a", "tweet b"], "picks")

    assert len(result) == 2
    assert all("pushed" in line for line in result)
    assert "https://typefully.com/?d=1" in result[0]
    # posted to the v2 social-set-scoped drafts URL, not the old v1 one,
    # once per tweet
    assert mock_post.call_count == 2
    first_call = mock_post.call_args_list[0]
    assert first_call.args[0] == "https://api.typefully.com/v2/social-sets/42/drafts"
    assert first_call.kwargs["json"] == {
        "platforms": {"x": {"enabled": True, "posts": [{"text": "tweet a"}]}}
    }


def test_push_to_typefully_reports_failure_without_crashing():
    sets = _social_sets_response([{"id": 42, "username": "beezy_fyi"}])
    forbidden = _draft_response(403, text='{"detail":"API v1 access via API keys is disabled"}')
    with patch("tweet_drafter.requests.get", return_value=sets), \
         patch("tweet_drafter.requests.post", return_value=forbidden):
        result = td.push_to_typefully(["tweet a"], "picks")

    assert len(result) == 1
    assert "FAILED (403)" in result[0]


def test_push_to_typefully_social_set_lookup_failure_does_not_crash():
    with patch("tweet_drafter.requests.get", side_effect=RuntimeError("boom")):
        result = td.push_to_typefully(["tweet a"], "picks")
    assert len(result) == 1
    assert "social-set lookup FAILED" in result[0]


# ── #GamblingX voice, modes, settlement (2026-10-14) ─────────────────────────

@pytest.mark.parametrize("text", [
    "Let's dive in to today's card", "Lock it in: Judge HR", "Smash the over", "Our AI found an edge",
    "proprietary algorithm says over", "guaranteed winner", "Parlay of the day", "two \U0001F525\U0001F525 emojis",
])
def test_voice_filter_rejects_banned_copy(text):
    assert td.violates_voice(text)


def test_voice_filter_allows_deadpan_numbers_copy():
    assert not td.violates_voice("DraftKings +115 vs 12-book fair -108 (+7.1% EV). Full board -> beezy.fyi/ev")
    assert not td.violates_voice("Said it was a main slate")  # 'ai' inside words must not trip the ' ai ' rule


def test_generate_tweets_drops_banned_variants_and_raises_when_none_left():
    def fake(texts):
        resp = MagicMock()
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(texts)}]}}]}
        return resp

    with patch.object(td.requests, "post", return_value=fake(["Smash it", "DK +115 vs -108 fair. beezy.fyi"])):
        assert td.generate_tweets("p") == ["DK +115 vs -108 fair. beezy.fyi"]
    with patch.object(td.requests, "post", return_value=fake(["Smash it", "Lock it in"])):
        with pytest.raises(RuntimeError, match="voice rules"):
            td.generate_tweets("p")


import json  # noqa: E402


def test_resolve_mode_cli_beats_env_and_morning_aliases_picks():
    assert td.resolve_mode(["--mode", "settlement"]) == "settlement"
    assert td.resolve_mode(["--mode", "morning"]) == "picks"
    assert td.resolve_mode([]) == td.MODE


def test_build_settlement_none_when_nothing_graded():
    assert td.build_settlement([], {}) is None
    assert td.build_settlement([_bet(YESTERDAY, "void", 0)], {}) is None


def test_build_settlement_winning_day_text_and_card_url():
    settled = [
        _bet(YESTERDAY, "win", 1.15, player="Gerrit Cole", odds=115, sport="mlb"),
        _bet(YESTERDAY, "loss", -1.0),
        _bet(TWO_DAYS_AGO, "win", 9.0),
    ]
    s = td.build_settlement(settled, {"overall": {"roi": 8.2, "total_bets": 7550}})
    assert s["date"] == YESTERDAY and (s["w"], s["l"], s["p"]) == (1, 1, 0)
    assert s["net"] == pytest.approx(0.15)
    assert "Gerrit Cole +115 (+1.15u)" in s["text"]
    assert "Season ledger +8.2% ROI across 7,550 graded bets" in s["text"]
    assert s["card_url"] == f"{td.BEEZY_SITE_URL}/api/og/settlement-card?date={YESTERDAY}&sport=all"
    assert not td.violates_voice(s["text"])


def test_build_settlement_losing_day_owns_the_variance():
    settled = [_bet(YESTERDAY, "loss", -1.0), _bet(YESTERDAY, "loss", -0.8), _bet(YESTERDAY, "win", 0.0)]
    s = td.build_settlement(settled, {"overall": {"roi": 8.2, "total_bets": 7550}})
    assert s["text"].startswith("Dropped 1.80u on")
    assert "Every play logged publicly" in s["text"]
    assert s["hero"] is not None


def test_post_settlement_attaches_card_and_gif(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    summary = {"date": "2026-10-13", "text": "tweet body", "card_url": "https://beezy.fyi/api/og/settlement-card?date=2026-10-13"}
    gif = {"gif_url": "https://x/g.gif", "preview_url": "https://x/t.gif", "query": "Aaron Judge celebration"}
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        out = td.post_settlement_to_discord(summary, gif)
    body = post.call_args.kwargs["json"]
    assert "ok" in out and "tweet body" in body["content"] and "https://x/g.gif" in body["content"]
    assert [e["image"]["url"] for e in body["embeds"]] == [summary["card_url"], "https://x/t.gif"]


def test_post_settlement_without_webhook_skips(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_OPS", raising=False)
    assert "SKIPPED" in td.post_settlement_to_discord({"date": "d", "text": "t", "card_url": "u"}, {"gif_url": "", "query": "q"})


def test_run_settlement_end_to_end_attaches_gif_and_card(monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_OPS", "https://discord.test/ops")
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    rows = [_bet(YESTERDAY, "win", 2.0, player="Aaron Judge", odds=150)]
    monkeypatch.setattr(td, "get_recent_settled", lambda limit=10, sport=None: rows if sport == "mlb" else [])
    monkeypatch.setattr(td, "get_stats", lambda: {"overall": {"roi": 8.2, "total_bets": 100}})
    monkeypatch.setattr(td, "fetch_celebration_gif", lambda bet: {"gif_url": "https://x/g.gif", "preview_url": "", "query": f"{bet['player']} celebration"})
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.run_settlement()
    body = post.call_args.kwargs["json"]
    assert "Aaron Judge celebration" in body["content"] and len(body["embeds"]) == 2
