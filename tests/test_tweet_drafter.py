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
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SITE_API_KEY", "test-site-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

import tweet_drafter as td


@pytest.fixture(autouse=True)
def _no_discord_pacing(monkeypatch):
    """post_messages sleeps between webhook posts; never in tests."""
    monkeypatch.setattr(td.time, "sleep", lambda s: None)

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
        "stake": 1.0,
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

    assert "NYY @ BOS" in prompt          # a leg with no player is described by its matchup


def test_build_recap_prompt_no_win_loss_push_rows_returns_none():
    """Rows present but none carry a settled result (e.g. all still void or
    None) -- nothing to recap."""
    settled = [_bet(YESTERDAY, "void", 0.0)]
    assert td.build_recap_prompt(settled, {"overall": {}}) is None


def test_build_picks_prompt_empty_returns_none():
    assert td.build_picks_prompt([]) is None


def test_build_picks_prompt_asks_for_watermark_not_a_link():
    picks = [{
        "system": "HR", "game": "NYY @ BOS", "bet_type": "HR",
        "odds": "+450", "edge": "8.2", "stake": "0.4", "notes": "",
    }]
    prompt = td.build_picks_prompt(picks)
    assert prompt is not None
    # the prompt may SAY "no https://", but it must not hand the model a URL to copy
    assert "beezy.fyi/" not in prompt and "/cheat-sheet" not in prompt and "NO https://" in prompt
    assert td.WATERMARK in prompt


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


def test_post_settlement_without_webhook_skips(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_OPS", raising=False)
    assert "SKIPPED" in td.post_settlement_to_discord({"date": "d", "text": "t", "card_url": "u"}, {"gif_url": "", "query": "q"})


# -- Link policy, Discord mirror, reply_ammo (2026-10-19) ---------------------------------------------------------------

from urllib.parse import parse_qs, unquote, urlparse  # noqa: E402


def test_filter_voice_keeps_clean_drops_banned_and_non_strings():
    assert td.filter_voice(["DK +115 vs -108 fair. beezy.fyi", "Smash it", 7, None]) == ["DK +115 vs -108 fair. beezy.fyi"]


def test_filter_voice_raises_when_nothing_survives():
    with pytest.raises(RuntimeError, match="voice rules"):
        td.filter_voice(["Lock it in", "guaranteed"])


@pytest.mark.parametrize("text,expected", [
    ("Edge today -> https://beezy.fyi/picks", True),
    ("Edge today -> http://example.com/x", True),
    ("see www.example.com", True),
    ("full board beezy.fyi/ev", True),
    ("Edge today. beezy.fyi", False),
    ("no links at all", False),
])
def test_has_url(text, expected):
    assert td.has_url(text) is expected


def test_strip_urls_turns_site_urls_into_the_plain_watermark_and_drops_others():
    assert td.strip_urls("DK +115 vs -108 fair.\nhttps://beezy.fyi/cheat-sheet") == "DK +115 vs -108 fair.\nbeezy.fyi"
    assert td.strip_urls("Full board -> beezy.fyi/ev") == "Full board -> beezy.fyi"
    assert td.strip_urls("see https://other.example/a?b=1 now. beezy.fyi") == "see now. beezy.fyi"
    assert td.strip_urls("beezy.fyi https://www.beezy.fyi/results") == "beezy.fyi"   # doubled watermark collapses
    assert not td.has_url(td.strip_urls("x https://beezy.fyi/a/b?c=d y www.foo.com"))


def test_generate_tweets_output_never_contains_a_url():
    texts = ["DK +115 vs -108 fair.\nhttps://beezy.fyi/cheat-sheet", "Season +8% ROI. www.beezy.fyi/results"]
    resp = MagicMock()
    resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(texts)}]}}]}
    with patch.object(td.requests, "post", return_value=resp):
        out = td.generate_tweets("p")
    assert out == ["DK +115 vs -108 fair.\nbeezy.fyi", "Season +8% ROI. beezy.fyi"]
    assert not any(td.has_url(t) for t in out)


def test_recap_prompt_asks_for_watermark_not_a_link():
    prompt = td.build_recap_prompt([_bet(YESTERDAY, "win", 1.0)], {"overall": {"roi": 1.0, "total_bets": 10}})
    assert "beezy.fyi/" not in prompt and "/results" not in prompt and "NO https://" in prompt
    assert td.WATERMARK in prompt


def test_system_prompt_example_has_no_url():
    assert "beezy.fyi/" not in td.SYSTEM_PROMPT and "https://beezy" not in td.SYSTEM_PROMPT


def test_build_link_reply_copy():
    assert td.build_link_reply("picks", "https://beezy.fyi/picks") == "Full live board + model projections -> https://beezy.fyi/picks"
    assert td.build_link_reply("recap", "https://beezy.fyi/results").endswith("https://beezy.fyi/results")
    assert td.build_link_reply("nope", "https://x.y") == "More -> https://x.y"


def test_build_x_search_url_is_live_search_with_min_faves():
    url = td.build_x_search_url("Luka Doncic min_faves:20")
    parts = urlparse(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "x.com", "/search")
    q = parse_qs(parts.query)
    assert q["q"] == ["Luka Doncic min_faves:20"] and q["f"] == ["live"]


def _embed_total(batch):
    return sum(td._embed_len(e) for e in batch)


def test_post_drafts_to_discord_without_webhook_skips(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_OPS", raising=False)
    assert "SKIPPED" in td.post_drafts_to_discord("picks", ["t"])


def test_nfl_is_not_a_settlement_sport_because_nfl_bets_are_not_tracked():
    """bets.sport is mlb|nhl|nba, so /api/public/picks/recent?sport=nfl answers 400 and there is nothing to settle. If NFL
    bets are ever tracked, add it here AND to the settlement-card route's SPORTS, and flip this test."""
    assert td.SETTLEMENT_SPORTS == ("mlb", "nhl", "nba")
    from mlb_core.tracking.bet_tracker import VALID_SPORTS
    assert set(td.SETTLEMENT_SPORTS) == set(VALID_SPORTS)


# reply_ammo ---------------------------------------------------------------------------------------------------------------

def _outlier(**kw):
    row = {"sport": "nba", "venue": "book", "book": "draftkings", "player": "Luka Doncic", "player_norm": "luka doncic",
           "stat": "points", "point": 32.5, "side": "OVER", "american": 115, "p_fair": 0.5192, "ev": 0.116, "n_books": 8,
           "flag": True, "mins_to_kick": 120.0}
    row.update(kw)
    return row


def _feed(nba=(), nhl=(), nfl=(), mlb=(), stale=False):
    return {"mlb": {"rows": list(mlb)},
            "nfl": {"rows": [], "book_outliers": list(nfl), "stale": stale},
            "nhl": {"rows": list(nhl), "stale": stale},
            "nba": {"rows": list(nba), "stale": stale}}


def test_prob_to_american():
    assert td.prob_to_american(0.5192) == -108
    assert td.prob_to_american(0.4) == 150
    assert td.prob_to_american(0.5) == -100
    assert td.prob_to_american(0) is None and td.prob_to_american(1) is None and td.prob_to_american("x") is None


def test_stat_labels_mirror_the_projection_engine():
    from ev.daily_projections import STAT_LABEL
    assert td.STAT_LABELS == STAT_LABEL


def test_reply_snippet_is_link_free_short_and_factual():
    c = td.reply_candidates(_feed(nba=[_outlier()]))[0]
    text = td.build_reply_snippet(c, proj_mean=34.1)
    assert text == ("Luka Doncic Over 32.5 Points: DraftKings is hanging +115 while the other 8 books are around -108. "
                    "We have him closer to 34.1.")
    assert len(text) <= td.REPLY_SNIPPET_MAX
    assert td.is_link_free(text) and not td.violates_voice(text)


def test_reply_snippet_drops_optional_parts_to_fit_and_never_truncates():
    long_name = "Maximilian Bartholomew Featherstonehaugh-Cholmondeley the Third of Westmorland"
    c = td.reply_candidates(_feed(nba=[_outlier(player=long_name)]))[0]
    assert len(td.build_reply_snippet(c, proj_mean=None)) <= td.REPLY_SNIPPET_MAX   # bare sentence fits...
    text = td.build_reply_snippet(c, proj_mean=34.1)                                # ...but not with the projection added
    assert text is not None and len(text) <= td.REPLY_SNIPPET_MAX and "closer to" not in text
    c2 = td.reply_candidates(_feed(nba=[_outlier(player=long_name * 3)]))[0]
    assert td.build_reply_snippet(c2) is None                                  # does not fit even bare: skipped, not cut


@pytest.mark.parametrize("bad", ["see https://x.co", "ping @someone", "beezy", "www.foo.com"])
def test_is_link_free_rejects_links_handles_and_brand(bad):
    assert not td.is_link_free(bad)


def test_reply_ammo_snippets_never_contain_links_or_the_brand_and_search_urls_are_valid():
    feed = _feed(
        nba=[_outlier(), _outlier(player="Jayson Tatum", player_norm="jayson tatum", ev=0.09)],
        nhl=[_outlier(sport="nhl", player="Auston Matthews", player_norm="auston matthews", stat="sog", point=3.5, ev=0.08)],
        nfl=[_outlier(sport="nfl", player="Derrick Henry", player_norm="derrick henry", stat="rush_yds", point=78.5,
                      american=-105, p_fair=0.55, ev=0.07)],
    )
    plays = td.build_reply_ammo(feed)
    assert 1 <= len(plays) <= td.REPLY_MAX_PLAYS
    for p in plays:
        assert not td.has_url(p["snippet"]) and "http" not in p["snippet"] and "beezy" not in p["snippet"].lower()
        parts = urlparse(p["search_url"])
        assert (parts.scheme, parts.netloc, parts.path) == ("https", "x.com", "/search")
        q = parse_qs(parts.query)
        assert q["q"] == [f"{p['player']} min_faves:{td.REPLY_MIN_FAVES}"] and q["f"] == ["live"]


def test_select_reply_plays_ranks_by_ev_caps_per_sport_and_dedups():
    nba = [_outlier(player=f"P{i}", player_norm=f"p{i}", ev=0.05 + i / 100) for i in range(5)]
    nba.append(_outlier(player="P4", player_norm="p4", ev=0.04, book="fanduel"))        # same player+stat, worse price
    nhl = [_outlier(sport="nhl", player="H", player_norm="h", stat="sog", point=2.5, ev=0.03)]
    plays = td.select_reply_plays(td.reply_candidates(_feed(nba=nba, nhl=nhl)))
    sports = [c["sport"] for c in plays]
    assert sports.count("nba") == td.REPLY_MAX_PER_SPORT and "nhl" in sports
    assert [c["ev"] for c in plays if c["sport"] == "nba"] == sorted([c["ev"] for c in plays if c["sport"] == "nba"], reverse=True)
    assert len({(c["sport"], c["player"], c["stat"]) for c in plays}) == len(plays)


def test_select_reply_plays_fails_closed():
    rows = [_outlier(n_books=3), _outlier(player="B", ev=-0.01), _outlier(player="C", mins_to_kick=10.0),
            _outlier(player="D", flag=False), _outlier(player="E", venue="kalshi"), _outlier(player="F", american=None),
            _outlier(player="G", side="")]
    assert td.select_reply_plays(td.reply_candidates(_feed(nba=rows))) == []
    assert td.reply_candidates(_feed(nba=[_outlier()], stale=True)) == []        # a stale sport block contributes nothing


def test_reply_candidates_mlb_only_pending_rows_from_todays_slate():
    today = datetime.now(td._CT).date().isoformat()
    base = {"game_date": today, "player": "Gerrit Cole", "bet_type": "K_OVER_7.5", "book": "draftkings", "odds": 105,
            "model_prob": 0.52, "edge": 0.06, "n_books": 6, "result": None}
    cands = td.reply_candidates(_feed(mlb=[base, {**base, "result": "win"}, {**base, "game_date": "2020-01-01"},
                                           {**base, "bet_type": "HR"}]))
    assert len(cands) == 1 and cands[0]["stat_label"] == "Ks" and cands[0]["point"] == 7.5 and cands[0]["side"] == "OVER"
    snippet = td.build_reply_snippet(cands[0])
    assert snippet.startswith("Gerrit Cole Over 7.5 Ks: DraftKings is hanging +105")


def test_build_reply_ammo_adds_projection_mean_when_matched():
    feed = _feed(nba=[_outlier()])
    plays = td.build_reply_ammo(feed, {"nba": {"rows": [{"player_norm": "luka doncic", "stat": "points", "proj_mean": 34.12}]}})
    assert plays[0]["snippet"].endswith("We have him closer to 34.12.")
    assert plays[0]["facts"]["proj_mean"] == pytest.approx(34.12)
    assert "closer to" not in td.build_reply_ammo(feed, {})[0]["snippet"]


def test_run_reply_ammo_with_no_plays_posts_nothing(monkeypatch, capsys):
    monkeypatch.setattr(td, "get_ev_feed", lambda *a, **k: _feed())
    monkeypatch.setattr(td, "get_projections", lambda sp: {"rows": []})
    with patch.object(td.requests, "post") as post:
        td.run_reply_ammo()
    post.assert_not_called()
    assert "no reply ammo" in capsys.readouterr().out.lower()


def test_resolve_mode_accepts_reply_ammo():
    assert td.resolve_mode(["--mode", "reply_ammo"]) == "reply_ammo"


def test_main_picks_posts_to_discord_with_card_and_link_reply(monkeypatch):
    monkeypatch.setattr(td.sys, "argv", ["tweet_drafter.py", "--mode", "picks"])
    monkeypatch.setattr(td, "get_today_picks", lambda: [{"system": "HR", "game": "NYY @ BOS", "bet_type": "HR", "odds": "+450", "edge": "8.2", "stake": "0.4"}])
    monkeypatch.setattr(td, "generate_tweets", lambda prompt: ["hook. beezy.fyi"])
    calls = {}
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda label, tweets, card_url=None, reply_url=None: calls.update(dc=(label, card_url, reply_url)) or "  Discord post ok")
    td.main()
    assert calls["dc"] == ("picks", f"{td.BEEZY_SITE_URL}/api/og/picks-card", f"{td.BEEZY_SITE_URL}/picks")


def test_main_recap_card_is_the_settlement_card_for_the_recap_day(monkeypatch):
    monkeypatch.setattr(td.sys, "argv", ["tweet_drafter.py", "--mode", "recap"])
    monkeypatch.setattr(td, "get_recent_settled", lambda limit=10, sport=None: [_bet(YESTERDAY, "win", 1.0)])
    monkeypatch.setattr(td, "get_stats", lambda: {"overall": {"roi": 1.0, "total_bets": 10}})
    monkeypatch.setattr(td, "generate_tweets", lambda prompt: ["recap. beezy.fyi"])
    calls = {}
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda label, tweets, card_url=None, reply_url=None: calls.update(card=card_url, reply=reply_url) or "  Discord post ok")
    td.main()
    assert calls["card"] == f"{td.BEEZY_SITE_URL}/api/og/settlement-card?date={YESTERDAY}&sport=all"
    assert calls["reply"] == f"{td.BEEZY_SITE_URL}/results"


def test_typefully_is_gone_from_the_module():
    assert not hasattr(td, "push_to_typefully") and not hasattr(td, "TYPEFULLY_API_KEY")


@pytest.mark.parametrize("result", ["  Discord post FAILED (400)", "  Discord post SKIPPED: no webhook"])
def test_require_posted_raises_when_nothing_went_out(result):
    with pytest.raises(RuntimeError, match="did not go out"):
        td.require_posted(result)


def test_require_posted_passes_a_good_result_through():
    assert td.require_posted("  Discord post ok") == "  Discord post ok"


def test_main_fails_the_job_when_the_discord_post_fails(monkeypatch):
    """Discord is the only output now: a Job that exits 0 having posted nothing would be a silent failure."""
    monkeypatch.setattr(td.sys, "argv", ["tweet_drafter.py", "--mode", "picks"])
    monkeypatch.setattr(td, "get_today_picks", lambda: [{"system": "HR", "game": "g", "bet_type": "HR", "odds": "+1", "edge": "1", "stake": "1"}])
    monkeypatch.setattr(td, "generate_tweets", lambda prompt: ["hook. beezy.fyi"])
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda *a, **k: "  Discord post FAILED (500)")
    with pytest.raises(RuntimeError, match="did not go out"):
        td.main()


def test_main_runs_without_any_typefully_env(monkeypatch):
    monkeypatch.delenv("TYPEFULLY_API_KEY", raising=False)
    import importlib
    importlib.reload(td)   # import must not require TYPEFULLY_API_KEY any more


# -- Conversational voice (2026-10-19) ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("robotic", [
    "the 12-book de-vigged fair is -108", "vs the consensus fair price", "proj_mean says 7.3",
])
def test_robotic_quant_terms_are_banned(robotic):
    assert td.violates_voice(robotic)
    for term in ("de-vigged", "consensus fair", "proj_mean"):
        assert term in td.BANNED_PHRASES


def _play(**kw):
    c = td.reply_candidates(_feed(nba=[_outlier(**kw)]))[0]
    return {"sport": "nba", "player": c["player"], "snippet": td.build_reply_snippet(c, 34.1), "search_url": "https://x.com/search?q=a",
            "facts": {**c, "proj_mean": 34.1}}


GOOD = {
    "slow_book": "DraftKings has Luka Over 32.5 points at +115 while the rest of the market is around -108.",
    "model_first": "We have Luka closer to 34.1 tonight, and DraftKings is still hanging +115 on the Over 32.5.",
    "deadpan": "Luka Over 32.5 points: DraftKings +115, the other 8 books around -108.",
}


@pytest.mark.parametrize("bad", [
    "Did DraftKings fall asleep? They are at +125 while everyone else is near -108.",               # invented price
    "Luka Over 32.5: DraftKings is on an island.",                                                  # never quotes the real price
    "DraftKings +115 vs a de-vigged fair of -108 on Luka Over 32.5.",                                # robotic term
    "DraftKings +115 on Luka, full board at beezy.fyi",                                              # brand/link
    "DraftKings +115 on Luka, see https://x.co/a",                                                    # url
    "DraftKings +115 on Luka, the other 40 books are around -108",                                   # invented count
    "DraftKings +115 on Luka. " + "x" * 200,                                                         # too long
    "",
    None,
    7,
])
def test_reply_variant_ok_rejects_unsafe_or_invented_replies(bad):
    assert not td.reply_variant_ok(bad, _play()["facts"])


def _gemini_reply(payload):
    return lambda *a, **k: json.dumps(payload)


def test_generate_reply_fodder_skips_model_first_without_a_projection(monkeypatch):
    play = _play()
    play["facts"]["proj_mean"] = None
    play["snippet"] = td.build_reply_snippet(play["facts"])
    monkeypatch.setattr(td, "_gemini_json_text", _gemini_reply({"0": GOOD}))
    assert "model_first" not in td.generate_reply_fodder([play])[0]["variants"]


def test_generate_reply_fodder_is_one_batched_call_and_prompt_carries_only_given_facts(monkeypatch):
    seen = []
    monkeypatch.setattr(td, "_gemini_json_text", lambda system, user, max_tokens=0: seen.append((system, user, max_tokens)) or "{}")
    td.generate_reply_fodder([_play(), _play(player="Jayson Tatum", player_norm="jayson tatum")])
    assert len(seen) == 1
    system, user, _ = seen[0]
    assert system == td.REPLY_SYSTEM_PROMPT
    assert "[0] NBA | Luka Doncic Over 32.5 Points | DraftKings is at +115" in user and "[1] NBA | Jayson Tatum" in user
    assert "our projection: 34.1" in user and "http" not in user and "beezy" not in user.lower()




# -- Units, plain copy, plain Discord messages, Gemini hardening (2026-10-20) -----------------------------------------------

def _ev_row(result, profit, **kw):
    """A flat-$100 EV alert row: profit and stake are DOLLARS (the real shape of /api/public/picks/recent)."""
    row = {"game_date": YESTERDAY, "result": result, "profit": profit, "stake": 100.0, "system": "EV", "odds": 105,
           "player": "Some Batter", "bet_type": "BATTER_HITS_UNDER_0.5_fanduel", "sport": "mlb"}
    row.update(kw)
    return row


def test_a_190_dollar_loss_on_flat_100_stakes_is_minus_1_90_units_not_190u():
    """The 2026-10-19 recap said '2-4, -190u'. profit/stake are dollars; two +105 wins and four losses at a flat $100 stake is
    -$190, i.e. -1.90u."""
    slate = [_ev_row("win", 105.0), _ev_row("win", 105.0)] + [_ev_row("loss", -100.0) for _ in range(4)]
    ds = td.day_stats(slate)
    assert (ds["w"], ds["l"], ds["n"]) == (2, 4, 6) and ds["units"] == pytest.approx(-1.90)
    s = td.build_settlement(slate, {"overall": {"roi": 6.74, "total_bets": 37141}})
    assert "-1.90u" in s["text"] and "190" not in s["text"] and s["units"] == pytest.approx(-1.90)
    prompt = td.build_recap_prompt(slate, {"overall": {"roi": 6.74, "total_bets": 37141}})
    assert "Result: -1.90u" in prompt and "190" not in prompt and "$" not in prompt


def test_bet_units_is_profit_over_stake_and_safe_on_bad_rows():
    assert td.bet_units({"profit": 105.0, "stake": 100.0}) == pytest.approx(1.05)
    assert td.bet_units({"profit": -57.5, "stake": 57.5}) == pytest.approx(-1.0)       # a Kelly-sized row normalises the same way
    assert td.bet_units({"profit": 10, "stake": 0}) == 0.0 and td.bet_units({"profit": None, "stake": None}) == 0.0
    assert td.bet_units({"profit": "x", "stake": "y"}) == 0.0


@pytest.mark.parametrize("row,expected", [
    ({"player": "Gerrit Cole", "bet_type": "K_OVER_7.5"}, "Gerrit Cole over 7.5 Ks"),
    ({"player": "Freddy Peralta", "bet_type": "OUTS_UNDER_14.5_novig"}, "Freddy Peralta under 14.5 outs"),
    ({"player": "Aaron Judge", "bet_type": "BATTER_TB_2PLUS_2.0"}, "Aaron Judge 2+ total bases"),
    ({"player": "Aaron Judge", "bet_type": "HR"}, "Aaron Judge to homer"),
    ({"player": "Aaron Judge", "bet_type": "HR_NO_hardrock"}, "Aaron Judge no HR"),
    ({"bet_type": "GAME_HOME", "away_team": "NYY", "home_team": "BOS"}, "BOS moneyline"),
    ({"bet_type": "NRFI", "away_team": "NYY", "home_team": "BOS"}, "NYY @ BOS NRFI"),
])
def test_bet_label_is_plain_words(row, expected):
    assert td.bet_label(row) == expected


def test_prompts_never_hand_gemini_raw_codes_dollars_or_a_dollar_u():
    picks = [{"player": "Gerrit Cole", "bet_type": "K_OVER_7.5", "odds": 115, "model_prob": 0.58, "market_prob": 0.52,
              "edge": 0.061, "stake": 48.0, "book": "draftkings"}]
    prompt = td.build_picks_prompt(picks)
    assert "Gerrit Cole over 7.5 Ks at +115 (draftkings)" in prompt and "our model has it at 58% vs 52% implied by this price" in prompt and "edge 6.1%" in prompt
    assert "48" not in prompt and "Kelly" not in prompt and "K_OVER" not in prompt


def test_recap_prompt_names_every_leg_with_its_units_and_has_no_stock_lines():
    slate = [_ev_row("win", 105.0, player="Gerrit Cole", bet_type="K_OVER_7.5"), _ev_row("loss", -100.0, player="Ohtani", bet_type="HR")]
    prompt = td.build_recap_prompt(slate, {"overall": {"roi": 6.74, "total_bets": 37141}})
    assert "WON  Gerrit Cole over 7.5 Ks at +105 (+1.05u each)" in prompt and "LOST  Ohtani to homer at +105 (-1.00u each)" in prompt
    for stock in ("transparency", "season sample size", "units honestly"):
        assert stock not in prompt.lower()
    assert "Write results in units, never dollars" in prompt


def test_corny_stock_phrases_are_banned_and_not_in_the_persona():
    for phrase in ("took it on the chin", "couldn't buy a bounce", "days like that happen", "on to the next board",
                   "the transparency is the brand", "fun sweat", "did draftkings fall asleep", "on an island"):
        assert td.violates_voice(phrase), phrase
        assert phrase not in td.PERSONA.lower() and phrase not in td.REPLY_SYSTEM_PROMPT.lower()
    assert "units" in td.PERSONA and "-1.9u" in td.PERSONA


@pytest.mark.parametrize("sport", ["mlb", "nhl", "nba"])
def test_settlement_text_is_plain_factual_and_link_free(sport):
    slate = [_ev_row("win", 150.0, player="Gerrit Cole", bet_type="K_OVER_7.5", odds=150, sport=sport), _ev_row("loss", -100.0, sport=sport)]
    s = td.build_settlement(slate, {"overall": {"roi": 8.2, "total_bets": 7550}})
    assert s["text"] == (f"{td._day_label(YESTERDAY)}: 1-1, +0.50u on 2 bets. Best: Gerrit Cole over 7.5 Ks +150. "
                         f"Season: +8.2% ROI over 7,550 bets. beezy.fyi")
    assert len(s["text"]) <= 280 and not td.violates_voice(s["text"]) and not td.has_url(s["text"])
    assert s["reply_url"] == f"{td.BEEZY_SITE_URL}/results"


def test_settlement_losing_day_names_the_costliest_miss():
    slate = [_ev_row("loss", -100.0, player="Ohtani", bet_type="HR", odds=300), _ev_row("loss", -40.0, stake=40.0)]
    s = td.build_settlement(slate, {})
    assert s["text"].startswith(f"{td._day_label(YESTERDAY)}: 0-2, -2.00u on 2 bets. Costliest miss:")
    assert td._day_label("not-a-date") == "not-a-date" and td._day_label("2026-10-18") == "Sunday"


# plain messages --------------------------------------------------------------------------------------------------------

def _posted(post):
    return [c.kwargs["json"] for c in post.call_args_list]


def test_drafts_are_plain_messages_whose_entire_content_is_the_tweet(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    tweets = ["Peralta under 14.5 outs: DraftKings is +104. beezy.fyi", "Second variant, 5.2% edge & more. beezy.fyi"]
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        out = td.post_drafts_to_discord("picks", tweets, card_url="https://beezy.fyi/api/og/picks-card",
                                        reply_url="https://beezy.fyi/picks")
    msgs = _posted(post)
    assert "ok" in out and len(msgs) == 5
    assert msgs[1] == {"content": tweets[0]} and msgs[2] == {"content": tweets[1]}      # exactly the text: nothing to strip
    assert msgs[3] == {"embeds": [{"image": {"url": "https://beezy.fyi/api/og/picks-card"}}]}
    assert msgs[4]["content"] == "Full live board + model projections -> https://beezy.fyi/picks" and msgs[4]["flags"] == 4
    blob = json.dumps(msgs)
    assert "```" not in blob and "x.com/intent" not in blob and "%20" not in blob and "1-Tap" not in blob
    assert len(msgs[0]["content"]) < 90                      # the header is one short line


def test_intent_links_are_gone():
    assert not hasattr(td, "build_x_intent_url")


def test_post_messages_paces_retries_a_429_once_and_reports_failure(monkeypatch):
    sleeps = []
    monkeypatch.setattr(td.time, "sleep", lambda s: sleeps.append(s))
    limited = MagicMock(status_code=429)
    limited.json.return_value = {"retry_after": 2}
    with patch.object(td.requests, "post", side_effect=[MagicMock(status_code=204), limited, MagicMock(status_code=204)]) as post:
        out = td.post_messages("https://d.test/h", [{"content": "a"}, {"content": "b"}])
    assert "ok" in out and post.call_count == 3 and 2.0 in sleeps and td._PAUSE in sleeps
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=400)):
        assert "FAILED (400)" in td.post_messages("https://d.test/h", [{"content": "a"}])
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_messages("https://d.test/h", [{"content": "x" * 5000}])
    assert len(post.call_args.kwargs["json"]["content"]) == 2000


def test_settlement_message_order_and_plain_tweet(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    summary = {"date": "2026-10-18", "text": "Sunday: 2-4, -1.90u on 6 bets. beezy.fyi", "card_url": "https://beezy.fyi/api/og/settlement-card?date=2026-10-18",
               "reply_url": "https://beezy.fyi/results"}
    gif = {"gif_url": "https://x/g.gif", "preview_url": "https://x/t.gif", "query": "q"}
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_settlement_to_discord(summary, gif)
    msgs = _posted(post)
    assert msgs[1] == {"content": summary["text"]}
    assert msgs[2]["embeds"][0]["image"]["url"] == summary["card_url"]
    assert "full ledger -> https://beezy.fyi/results" in msgs[3]["content"]
    assert msgs[4]["embeds"][0]["image"]["url"] == "https://x/t.gif"
    assert "```" not in json.dumps(msgs) and "intent" not in json.dumps(msgs)


def test_settlement_without_webhook_skips(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_OPS", raising=False)
    assert "SKIPPED" in td.post_settlement_to_discord({"date": "d", "text": "t", "card_url": "u"}, {"gif_url": "", "query": "q"})


def test_run_settlement_posts_units_not_dollars(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_OPS", "https://discord.test/ops")
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    rows = [_ev_row("win", 105.0, player="Aaron Judge", bet_type="K_OVER_7.5")] + [_ev_row("loss", -100.0) for _ in range(4)]
    monkeypatch.setattr(td, "get_recent_settled", lambda limit=10, sport=None: rows if sport == "mlb" else [])
    monkeypatch.setattr(td, "get_stats", lambda: {"overall": {"roi": 8.2, "total_bets": 100}})
    monkeypatch.setattr(td, "fetch_celebration_gif", lambda bet: {"gif_url": "", "query": "q"})
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.run_settlement()
    texts = [m.get("content", "") for m in _posted(post)]
    assert any("-2.95u" in t for t in texts) and not any("295" in t for t in texts)      # 1.05 - 4 = -2.95u


def test_reply_ammo_posts_a_header_then_plain_variants_per_play(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    play = dict(_play(), variants=dict(GOOD))
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_reply_ammo_to_discord([play])
    msgs = _posted(post)
    assert msgs[1]["content"].startswith("**NBA | Luka Doncic** -- [search X](https://x.com/search") and msgs[1]["flags"] == 4
    assert [m["content"] for m in msgs[2:]] == list(GOOD.values())             # each reply is exactly its text
    assert "```" not in json.dumps(msgs)


def test_reply_ammo_without_variants_falls_back_to_the_plain_snippet(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    play = _play()
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_reply_ammo_to_discord([play])
    assert _posted(post)[-1] == {"content": play["snippet"]}


def test_run_reply_ammo_posts_via_plain_messages(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    monkeypatch.setattr(td, "get_ev_feed", lambda *a, **k: _feed(nba=[_outlier(), _outlier(player="Jayson Tatum", player_norm="jayson tatum", ev=0.09)]))
    monkeypatch.setattr(td, "get_projections", lambda sp: {"rows": []})
    monkeypatch.setattr(td, "_gemini_json_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gemini down")))
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.run_reply_ammo()
    contents = [m.get("content", "") for m in _posted(post)]
    assert sum("search X" in c for c in contents) == 2 and any(c.startswith("Luka Doncic Over 32.5") for c in contents)


# Gemini hardening ------------------------------------------------------------------------------------------------------

def _gemini_ok(text, finish="STOP"):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}
    return resp


def test_gemini_call_asks_for_json_mode_with_a_real_token_budget():
    with patch.object(td.requests, "post", return_value=_gemini_ok('["a. beezy.fyi"]')) as post:
        td.generate_tweets("p")
    cfg = post.call_args.kwargs["json"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["maxOutputTokens"] >= 4096
    assert cfg["responseSchema"] == {"type": "ARRAY", "items": {"type": "STRING"}}


@pytest.mark.parametrize("truncated", ['[\n  "first tweet, cut off here",\n', '[\n  "first tweet that never ends'])
def test_generate_tweets_retries_a_truncated_reply(truncated):
    """2026-10-19: picks died on 'Expecting value', recap's first attempt on 'Unterminated string' -- both truncated JSON."""
    with patch.object(td.requests, "post", side_effect=[_gemini_ok(truncated, "MAX_TOKENS"), _gemini_ok('["Fine. beezy.fyi"]')]) as post:
        assert td.generate_tweets("p") == ["Fine. beezy.fyi"]
    assert post.call_count == 2


def test_generate_tweets_retries_when_every_draft_breaks_the_voice_then_gives_up():
    with patch.object(td.requests, "post", side_effect=[_gemini_ok('["Lock it in"]'), _gemini_ok('["Fine. beezy.fyi"]')]):
        assert td.generate_tweets("p") == ["Fine. beezy.fyi"]
    with patch.object(td.requests, "post", return_value=_gemini_ok('["Lock it in"]')) as post:
        with pytest.raises(RuntimeError, match="voice rules"):
            td.generate_tweets("p", attempts=3)
    assert post.call_count == 3
    with patch.object(td.requests, "post", return_value=_gemini_ok("not json at all")) as post:
        with pytest.raises(ValueError):
            td.generate_tweets("p", attempts=2)
    assert post.call_count == 2


def test_recap_prompt_collapses_the_same_leg_logged_at_two_books():
    """2026-10-18: Bailey under 0.5 hits and DeLuca over 0.5 hits were each logged twice (two books); the copy said 'cashed twice'."""
    slate = [_ev_row("win", 105.0, player="Patrick Bailey", bet_type="BATTER_HITS_UNDER_0.5_fanduel"),
             _ev_row("win", 105.0, player="Patrick Bailey", bet_type="BATTER_HITS_UNDER_0.5_novig"),
             _ev_row("loss", -100.0, player="Jonny DeLuca", bet_type="BATTER_HITS_OVER_0.5_betmgm", odds=-135)]
    prompt = td.build_recap_prompt(slate, {})
    assert prompt.count("Patrick Bailey under 0.5 hits") == 1 and "(+1.05u each) x2 tickets" in prompt
    assert "Jonny DeLuca over 0.5 hits at -135 (-1.00u each)\n" in prompt and "Record: 2-1" in prompt and "Result: +1.10u" in prompt
    assert "xN was logged N times" in prompt


def test_picks_prompt_does_not_call_another_books_number_our_model():
    ev = {"system": "EV", "player": "Freddy Peralta", "bet_type": "OUTS_OVER_14.5_novig", "odds": 120, "model_prob": 0.48,
          "market_prob": 0.45, "edge": 0.052, "book": "novig"}
    model = dict(ev, system="K", bet_type="K_OVER_7.5", player="Gerrit Cole")
    p_ev, p_model = td.build_picks_prompt([ev]), td.build_picks_prompt([model])
    assert "the other books price it at 48% vs 45% implied by this price" in p_ev and "our model" not in p_ev.split("\nWrite 3")[0]
    assert "our model has it at 48% vs 45%" in p_model
    assert "do not call another book's number 'our model'" in p_ev


def test_recap_mode_fetches_enough_rows_not_ten(monkeypatch):
    monkeypatch.setattr(td.sys, "argv", ["tweet_drafter.py", "--mode", "recap"])
    seen = {}
    monkeypatch.setattr(td, "get_recent_settled", lambda limit=10, sport=None: seen.update(limit=limit) or [_bet(YESTERDAY, "win", 1.0)])
    monkeypatch.setattr(td, "get_stats", lambda: {"overall": {"roi": 1.0, "total_bets": 10}})
    monkeypatch.setattr(td, "generate_tweets", lambda prompt: ["recap. beezy.fyi"])
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda *a, **k: "  Discord post ok")
    td.main()
    assert seen["limit"] >= 100
