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
    monkeypatch.setattr(td, "build_window_summary", lambda: "")      # never call the live API from a test

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
    assert text == ("DraftKings is still hanging +115 on the Over 32.5 points if you shop around; "
                    "the other 8 books are near -108, and we have him closer to 34.1.")
    assert len(text) <= td.REPLY_SNIPPET_MAX
    assert td.is_link_free(text) and not td.violates_voice(text)
    assert td.reply_variant_ok(text, c | {"proj_mean": 34.1}, "price_check")      # the fallback passes the guards it is a fallback for
    assert ":" not in text and not td.opens_with_player(text, c)


def test_reply_snippet_drops_optional_parts_to_fit_and_never_truncates():
    def snippet(name_len, proj):      # the player is not in the reply any more; a long stat name is what makes it long
        c = td.reply_candidates(_feed(nba=[_outlier(stat="s" * name_len)]))[0]
        return td.build_reply_snippet(c, proj_mean=proj)
    k = next(n for n in range(10, 400) if len(snippet(n, None) or "") > 0 and (snippet(n, 34.1) is None or "closer to" not in snippet(n, 34.1)))
    # at that stat-name length the bare sentence fits but the projection sentence does not: the projection is dropped, never cut
    assert len(snippet(k, None)) <= td.REPLY_SNIPPET_MAX and "closer to" not in snippet(k, 34.1)
    assert snippet(400, None) is None                                          # does not fit even bare: skipped, not truncated


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
        tq = parse_qs(urlparse(p["thread_url"]).query)
        assert tq["q"] == [f'"{p["player"]}" ({p["facts"]["point"]:g} OR prop OR over OR under OR line) '
                           f'min_faves:{td.REPLY_THREAD_MIN_FAVES} -filter:retweets'] and tq["f"] == ["live"]


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
    assert snippet.startswith("DraftKings is still hanging +105 on the Over 7.5 ks if you shop around")


def test_build_reply_ammo_adds_projection_mean_when_matched():
    feed = _feed(nba=[_outlier()])
    plays = td.build_reply_ammo(feed, {"nba": {"rows": [{"player_norm": "luka doncic", "stat": "points", "proj_mean": 34.12}]}})
    assert plays[0]["snippet"].endswith("and we have him closer to 34.1.")      # one decimal, not 34.12
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
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda label, tweets, card_url=None, reply_url=None, **kw: calls.update(dc=(label, card_url, reply_url)) or "  Discord post ok")
    td.main()
    assert calls["dc"] == ("picks", f"{td.BEEZY_SITE_URL}/api/og/picks-card", f"{td.BEEZY_SITE_URL}/picks")


def test_main_recap_card_is_the_settlement_card_for_the_recap_day(monkeypatch):
    monkeypatch.setattr(td.sys, "argv", ["tweet_drafter.py", "--mode", "recap"])
    monkeypatch.setattr(td, "get_recent_settled", lambda limit=10, sport=None: [_bet(YESTERDAY, "win", 1.0)])
    monkeypatch.setattr(td, "get_stats", lambda: {"overall": {"roi": 1.0, "total_bets": 10}})
    monkeypatch.setattr(td, "generate_tweets", lambda prompt: ["recap. beezy.fyi"])
    calls = {}
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda label, tweets, card_url=None, reply_url=None, **kw: calls.update(card=card_url, reply=reply_url) or "  Discord post ok")
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
    "price_check": "DraftKings is still hanging +115 on the over if you shop around, while the other 8 books are near -108.",
    "pushback": "Hard to back the under when the other 8 books are sitting near -108 on the over and we project 34.1.",
    "driver_note": "We have him closer to 34.1 against a 32.5 line, so DraftKings at +115 looks better than the other 8 books near -108.",
}


@pytest.mark.parametrize("bad", [
    "Did DraftKings fall asleep? They are at +125 while everyone else is near -108.",               # invented price
    "Luka Over 32.5: DraftKings is on an island.",                                                  # never quotes the real price
    "DraftKings +115 vs a de-vigged fair of -108 on Luka Over 32.5.",                                # robotic term
    "DraftKings +115 on Luka, full board at beezy.fyi",                                              # brand/link
    "DraftKings +115 on Luka, see https://x.co/a",                                                    # url
    "DraftKings +115 on Luka, the other 40 books are around -108",                                   # invented count
    "DraftKings +115 on Luka while others sit near -108. " + "x" * 240,                              # too long
    "",
    None,
    7,
])
def test_reply_variant_ok_rejects_unsafe_or_invented_replies(bad):
    assert not td.reply_variant_ok(bad, _play()["facts"])


def _gemini_reply(payload):
    return lambda *a, **k: json.dumps(payload)


def test_generate_reply_fodder_skips_driver_note_without_a_projection(monkeypatch):
    play = _play()
    play["facts"]["proj_mean"] = None
    play["snippet"] = td.build_reply_snippet(play["facts"])
    monkeypatch.setattr(td, "_gemini_json_text", _gemini_reply({"0": GOOD}))
    assert "driver_note" not in td.generate_reply_fodder([play])[0]["variants"]


def test_generate_reply_fodder_is_one_batched_call_and_prompt_carries_only_given_facts(monkeypatch):
    seen = []
    monkeypatch.setattr(td, "_gemini_json_text", lambda system, user, max_tokens=0: seen.append((system, user, max_tokens)) or "{}")
    td.generate_reply_fodder([_play(), _play(player="Jayson Tatum", player_norm="jayson tatum")])
    assert len(seen) == 1
    system, user, _ = seen[0]
    assert system == td.REPLY_SYSTEM_PROMPT
    assert "[0] NBA\nPick: Luka Doncic Over 32.5 Points at DraftKings +115" in user and "[1] NBA\nPick: Jayson Tatum" in user
    assert "Our projection is 34.1 against a line of 32.5" in user and "http" not in user and "beezy" not in user.lower()




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
    play["thread_url"] = "https://x.com/search?q=b"
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_reply_ammo_to_discord([play])
    msgs = _posted(post)
    assert msgs[1]["content"] == ("**NBA | Luka Doncic** -- [big tweets](https://x.com/search?q=a) / "
                                  "[betting threads](https://x.com/search?q=b)") and msgs[1]["flags"] == 4
    assert [m["content"] for m in msgs[2:]] == list(GOOD.values())             # each reply is exactly its text
    assert [m["username"] for m in msgs[2:]] == ["price check", "pushback", "driver note"]   # the angle rides in the author name
    assert "```" not in json.dumps(msgs)


def test_reply_ammo_without_variants_falls_back_to_the_plain_snippet(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    play = _play()
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_reply_ammo_to_discord([play])
    assert _posted(post)[-1] == {"content": play["snippet"], "username": "price check"}
    assert "betting threads" not in _posted(post)[1]["content"]


def test_run_reply_ammo_posts_via_plain_messages(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    monkeypatch.setattr(td, "get_ev_feed", lambda *a, **k: _feed(nba=[_outlier(), _outlier(player="Jayson Tatum", player_norm="jayson tatum", ev=0.09)]))
    monkeypatch.setattr(td, "get_projections", lambda sp: {"rows": []})
    monkeypatch.setattr(td, "_gemini_json_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gemini down")))
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.run_reply_ammo()
    contents = [m.get("content", "") for m in _posted(post)]
    assert sum("big tweets" in c for c in contents) == 2 and any(c.startswith("DraftKings is still hanging +115") for c in contents)


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


# -- Reply reasoning: projection vs line + price math, never trends (2026-10-20) ---------------------------------------------

TREND_TALK = [
    "Luka has hit 4 of his last 5 and DraftKings is +115",
    "DraftKings +115 on Luka, he's been on a streak lately",
    "Luka Over 32.5: over in 6 of his last 10, DraftKings +115",
    "DraftKings +115 on Luka, the last 5 games say over",
    "DraftKings +115 on Luka, trending up with a hot hit rate",
    "DraftKings +115 on Luka, three overs in a row",
]


@pytest.mark.parametrize("text", TREND_TALK)
def test_trend_and_streak_talk_is_banned_everywhere(text):
    """A scam-tout tell, per the owner: no last-N, hit rates, streaks or trends in any copy."""
    assert td.violates_voice(text)
    assert not td.reply_variant_ok(text, _play()["facts"])


def test_recent_form_wording_is_out_of_the_prompts_and_the_persona_forbids_it():
    for prompt in (td.PERSONA, td.REPLY_SYSTEM_PROMPT):
        assert "scam touts" in td.PERSONA and "No trends" in td.PERSONA
    assert "No injury, pace, weather or recent-form talk" in td.REPLY_SYSTEM_PROMPT
    assert "no matchup talk unless the play lists a matchup note" in td.REPLY_SYSTEM_PROMPT
    assert td.REPLY_VARIANTS == ("price_check", "pushback", "driver_note") and "slow_book" not in td.REPLY_SYSTEM_PROMPT
    assert "240 characters" in td.REPLY_SYSTEM_PROMPT and td.REPLY_SNIPPET_MAX == 240
    assert not td.violates_voice("Sunday's slate went 2-6 last night")        # a day's result is not a trend


def test_price_math_numbers():
    f = _play()["facts"]
    assert td.breakeven_pct(f) == pytest.approx(46.51, abs=0.01)             # +115 needs 46.5% to break even
    assert td.breakeven_pct({"american": -110}) == pytest.approx(52.38, abs=0.01)
    assert td.breakeven_pct({"american": 0}) is None
    assert td.market_pct(f) == pytest.approx(51.92)
    assert td.proj_gap(f) == pytest.approx(34.1 - 32.5)
    assert td.proj_gap(dict(f, proj_mean=None)) is None


def test_all_three_reply_angles_pass_the_guards_for_real_numbers():
    f = _play()["facts"]
    assert all(td.reply_variant_ok(t, f, style) for style, t in GOOD.items())
    ok = "We have him closer to 34.1 against a 32.5 line, 1.6 above it, so DraftKings at +115 is worth a look."
    assert td.reply_variant_ok(ok, f)                                                                           # the gap is allowed
    assert not td.reply_variant_ok("We give Luka 63% to go over, so DraftKings at +115 is worth a look.", f)    # invented win probability
    mins = "Projected for 36 minutes, we have Luka at 34.1 against 32.5, so DraftKings at +115 is worth a look."
    assert td.reply_variant_ok(mins, dict(f, proj_minutes=35.6)) and not td.reply_variant_ok(mins, f)           # no minutes were given


def test_price_check_must_name_the_book_and_its_price_but_pushback_may_quote_the_market():
    f = _play()["facts"]
    only_market = "Hard to back the under when the other 8 books are sitting near -108 on the over."
    assert td.reply_variant_ok(only_market, f, "pushback")
    assert not td.reply_variant_ok(only_market, f, "price_check")
    assert not td.reply_variant_ok("Somebody is still hanging +115 if you shop around, while the other 8 books are near -108.", f, "price_check")


@pytest.mark.parametrize("bot", [
    "Luka Doncic Over 32.5 Points: DraftKings is hanging +115 while the other 8 books are around -108.",       # the EV-ticker template
    "DraftKings +115 on the over: the other 8 books are near -108 while this one is soft.",                    # a colon anywhere
    "Luka is worth a look at DraftKings +115 while the other 8 books are near -108.",                          # opens on the player
    "Doncic is priced at DraftKings +115 while the other 8 books are near -108.",                              # opens on the surname
    "DraftKings is at +115. The other 8 books are near -108.",                                                 # no connective
    "Worth a shop. DraftKings has +115 while the other 8 books sit near -108.",                                # choppy fragment
    "DraftKings is at +115 while the rest sit near -108. We like it. It is soft. Shop it.",                    # too many sentences
])
def test_reply_variant_ok_rejects_bot_structure(bot):
    assert td.reads_like_a_template(bot, _play()["facts"]) and not td.reply_variant_ok(bot, _play()["facts"])


def test_the_prompt_bans_the_player_line_opener_and_asks_for_connectives_and_caveats():
    p = td.REPLY_SYSTEM_PROMPT
    assert "NEVER open with the player's name" in p and "no colons anywhere" in p
    for word in ("because", "while", "even with", "though", "semicolon"):
        assert word in p
    assert "weave it into the sentence" in p and "never say a line" in p
    assert "steamed" in p and "no line-movement data" in p       # we hold no line history, so the model may not claim movement


def test_prompt_carries_the_price_math_and_the_projection_but_no_form_data():
    play = _play()
    play["facts"]["proj_minutes"] = 35.6
    block = td._fodder_prompt([play]).split("\n\n")[1]
    assert block == ("[0] NBA\nPick: Luka Doncic Over 32.5 Points at DraftKings +115, which needs 47% to break even.\n"
                     "The other 8 books are around -108 (they imply 52%).\n"
                     "Our projection is 34.1 against a line of 32.5, 1.6 above it.")
    assert not any(w in block.lower() for w in ("last", "hit rate", "streak", "trend"))
    play["facts"]["proj_mean"] = None
    assert "No projection." in td._fodder_prompt([play])


def test_build_reply_ammo_carries_projected_minutes_only_for_minutes_labels():
    feed = _feed(nba=[_outlier()])
    rows = lambda label: {"nba": {"rows": [{"player_norm": "luka doncic", "stat": "points", "proj_mean": 34.1, "usage_proj": 35.6, "usage_label": label}]}}
    assert td.build_reply_ammo(feed, rows("Minutes"))[0]["facts"]["proj_minutes"] == pytest.approx(35.6)
    assert td.build_reply_ammo(feed, rows("TOI (min)"))[0]["facts"]["proj_minutes"] == pytest.approx(35.6)
    assert td.build_reply_ammo(feed, rows("Snaps"))[0]["facts"]["proj_minutes"] is None
    assert td.build_reply_ammo(feed, {})[0]["facts"]["proj_minutes"] is None


def test_generate_reply_fodder_returns_the_three_angles(monkeypatch):
    monkeypatch.setattr(td, "_gemini_json_text", _gemini_reply({"0": GOOD}))
    assert td.generate_reply_fodder([_play()])[0]["variants"] == GOOD


def test_driver_note_needs_a_projection_and_bad_replies_fall_back_to_the_snippet(monkeypatch):
    play = _play()
    play["facts"]["proj_mean"] = None
    play["snippet"] = td.build_reply_snippet(play["facts"])
    market_only = "Hard to back the under when the other 8 books are sitting near -108 on the over."
    bad_check = "Luka Over 32.5 points: DraftKings +115, the other 8 books around -108."
    monkeypatch.setattr(td, "_gemini_json_text", _gemini_reply({"0": dict(GOOD, price_check=bad_check, pushback=market_only)}))
    v = td.generate_reply_fodder([play])[0]["variants"]
    # the bad price_check is replaced by the deterministic snippet, driver_note is skipped (no projection), the grounded pushback stays
    assert v == {"price_check": play["snippet"], "pushback": market_only}
    # GOOD's pushback and driver_note cite a projection this play was not given: rejected, only the snippet is left
    monkeypatch.setattr(td, "_gemini_json_text", _gemini_reply({"0": dict(GOOD, price_check=bad_check)}))
    assert td.generate_reply_fodder([play])[0]["variants"] == {"price_check": play["snippet"]}
    monkeypatch.setattr(td, "_gemini_json_text", _gemini_reply({"0": {s: TREND_TALK[0] for s in td.REPLY_VARIANTS}}))
    assert td.generate_reply_fodder([_play()])[0]["variants"] == {"price_check": _play()["snippet"]}


def test_a_projection_on_the_wrong_side_of_the_line_is_never_cited():
    """Live 2026-10-20: Strome Under 1.5 SOG with a 1.57 projection. Counts are discrete so the Under can still be right, but a reply
    saying 'we project 1.57 against 1.5' then backing the Under contradicts itself."""
    assert td.proj_supports_side(1.44, 1.5, "UNDER") and td.proj_supports_side(7.0, 6.5, "OVER")
    assert not td.proj_supports_side(1.57, 1.5, "UNDER") and not td.proj_supports_side(6.0, 6.5, "OVER")
    assert not td.proj_supports_side(None, 1.5, "UNDER") and not td.proj_supports_side(1.0, 1.0, "OVER")      # equal is not support
    rows = lambda mean: {"nba": {"rows": [{"player_norm": "luka doncic", "stat": "points", "proj_mean": mean, "usage_proj": 35.6, "usage_label": "Minutes"}]}}
    ok = td.build_reply_ammo(_feed(nba=[_outlier()]), rows(34.1))[0]            # Over 32.5, projection 34.1: supports it
    assert ok["facts"]["proj_mean"] == pytest.approx(34.1) and "closer to 34.1" in ok["snippet"]
    bad = td.build_reply_ammo(_feed(nba=[_outlier()]), rows(30.0))[0]           # Over 32.5, projection 30.0: against it
    assert bad["facts"]["proj_mean"] is None and bad["facts"]["proj_minutes"] is None
    assert "closer to" not in bad["snippet"] and "30" not in bad["snippet"]


def test_reply_prompt_says_never_call_the_market_an_average():
    assert 'never an "average"' in td.REPLY_SYSTEM_PROMPT


def test_projection_is_quoted_to_one_decimal_never_three():
    f = dict(_play()["facts"], proj_mean=1.442, point=1.5, side="UNDER")
    assert td.reply_variant_ok("We project 1.4 shots against a 1.5 line, so DraftKings +115 is worth a shop.", f)
    assert not td.reply_variant_ok("We project 1.442 shots against a 1.5 line, so DraftKings +115 is worth a shop.", f)
    assert "Our projection is 1.4 against a line of 1.5" in td._fodder_prompt([dict(_play(), facts=f)])
    c = dict(f, stat_label="Shots on Goal", stat="sog")
    assert "closer to 1.4." in td.build_reply_snippet(c, 1.442) and "1.442" not in td.build_reply_snippet(c, 1.442)


# -- Stale slates are never recapped (2026-10-21: the 5am recap drafted Monday's results on Wednesday) ----------------------------
TODAY = date(2026, 10, 21)          # a Wednesday; Tuesday's slate is the only fresh one
TUESDAY, MONDAY = "2026-10-20", "2026-10-19"


def test_slate_is_fresh_allows_yesterday_and_rejects_older_or_unparseable():
    assert td.slate_is_fresh(TUESDAY, today=TODAY) and td.slate_is_fresh("2026-10-21", today=TODAY)
    assert not td.slate_is_fresh(MONDAY, today=TODAY)
    assert not td.slate_is_fresh("garbage", today=TODAY) and not td.slate_is_fresh(None, today=TODAY)


def test_recap_and_settlement_refuse_a_stale_slate(monkeypatch):
    monkeypatch.setattr(td, "central_today", lambda: TODAY)
    monday = [_bet(MONDAY, "win", 1.0), _bet(MONDAY, "loss", -1.0)]
    assert td.build_recap_prompt(monday, {"overall": {}}) is None
    assert td.build_settlement(monday, {}) is None
    assert "older than" in td.skip_reason(monday) and MONDAY in td.skip_reason(monday)
    assert td.skip_reason([]) == "No settled bets found -- skipping."
    tuesday = [_bet(TUESDAY, "win", 1.0)]
    assert td.build_recap_prompt(tuesday, {"overall": {}}) is not None and td.build_settlement(tuesday, {})["date"] == TUESDAY


def test_main_recap_posts_nothing_when_the_newest_slate_is_stale(monkeypatch, capsys):
    monkeypatch.setattr(td.sys, "argv", ["tweet_drafter.py", "--mode", "recap"])
    monkeypatch.setattr(td, "central_today", lambda: TODAY)
    monkeypatch.setattr(td, "get_recent_settled", lambda limit=10, sport=None: [_bet(MONDAY, "win", 1.0)])
    monkeypatch.setattr(td, "get_stats", lambda: {"overall": {}})
    monkeypatch.setattr(td, "generate_tweets", lambda prompt: pytest.fail("must not draft a stale recap"))
    monkeypatch.setattr(td, "post_drafts_to_discord", lambda *a, **k: pytest.fail("must not post a stale recap"))
    td.main()
    assert f"Newest graded slate is {MONDAY}" in capsys.readouterr().out


def test_settlement_job_skips_a_stale_slate(monkeypatch, capsys):
    monkeypatch.setattr(td, "central_today", lambda: TODAY)
    monkeypatch.setattr(td, "get_settled_all_sports", lambda: [_bet(MONDAY, "win", 1.0)])
    monkeypatch.setattr(td, "get_stats", lambda: {})
    monkeypatch.setattr(td, "post_settlement_to_discord", lambda *a, **k: pytest.fail("must not post a stale settlement"))
    td.run_settlement()
    assert "older than" in capsys.readouterr().out


# -- L7 / L30 summary line ------------------------------------------------------------------------------------------------------
def _win(sport, days=7, **kw):
    row = {"sport": sport, "days": days, "since": "2026-10-14", "until": "2026-10-21", "bets": 10, "wins": 6, "losses": 4,
           "pushes": 0, "profit": 120.0, "stake": 1000.0, "units": 1.5}
    row.update(kw)
    return row


def test_format_window_pools_sports_and_labels_the_span():
    line = td.format_window(7, [_win("mlb"), _win("nhl", wins=3, losses=1, bets=4, profit=-40.0, stake=400.0, units=-0.4)])
    assert line == "**L7** (Oct 14-20): 9-5 (64.3%) | +1.10u flat 1u/bet | ROI +5.7% | 14 bets (MLB 6-4, NHL 3-1)"


def test_format_window_empty_and_month_crossing():
    empty = _win("mlb", bets=0, wins=0, losses=0, profit=0.0, stake=0.0, units=0.0, since="2026-09-21", until="2026-10-21")
    assert td.format_window(30, [empty]) == "**L30** (Sep 21-Oct 20): no graded bets"


def test_window_summary_skips_a_window_whole_when_a_sport_is_missing_or_the_backend_is_stale(monkeypatch):
    def fake(days, sport):
        if days == 30 and sport == "nba":
            raise RuntimeError("503")
        return _win(sport, days)
    monkeypatch.undo()                                            # the autouse stub replaced build_window_summary
    real = td.get_window_stats
    monkeypatch.setattr(td, "get_window_stats", fake)
    out = td.build_window_summary()
    assert out.startswith("**L7**") and "L30" not in out          # a pooled line that omits a sport would be wrong, not short

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"overall": {}}                    # an older revision: no `days` echo
    monkeypatch.setattr(td, "get_window_stats", real)
    monkeypatch.setattr(td.requests, "get", lambda *a, **k: Resp())
    assert td.build_window_summary() == ""


def test_header_message_carries_the_stats_lines(monkeypatch):
    sent = []
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://hook.test/t")
    monkeypatch.setattr(td, "post_messages", lambda hook, messages: sent.extend(messages) or "  Discord post ok")
    td.post_drafts_to_discord("recap", ["a. beezy.fyi"], stats_line="**L7** x\n**L30** y")
    assert sent[0]["content"].endswith("\n**L7** x\n**L30** y")
    td.post_drafts_to_discord("recap", ["a. beezy.fyi"])
    assert "L7" not in sent[-2]["content"]


# reply_ammo dossier: engine numbers in betting words, caveats, one call (2026-10-25) ----------------------------------------------

ENGINE_ROW = {"player_norm": "luka doncic", "stat": "points", "proj_mean": 34.1, "proj_raw": 35.0, "calib_mult": 0.9743,
              "opp_factor": 1.04, "usage_proj": 36.2, "usage_label": "Minutes", "n_games": 40, "szn_avg": 31.0,
              "proj_p10": 26.0, "proj_p90": 42.0}
JARGON = ("calibration", "calib", "recent weighting", "opponent adjustment", "opponent factor", "proj_raw", "multiplier")


def _eplay(**engine):
    feed = _feed(nba=[_outlier()])
    row = {**ENGINE_ROW, **engine}
    return td.build_reply_ammo(feed, {"nba": {"rows": [row]}})[0]


def test_projection_terms_are_human_and_leave_the_calibration_step_out():
    f = _eplay()["facts"]
    terms = td.projection_terms(f)
    assert [lab for lab, _ in terms] == ["the season average against the line", "the projected workload against the season average",
                                         "the matchup"]
    assert sum(v for _, v in terms) == pytest.approx(35.0 - 32.5)         # the projection before calibration, which is never mentioned


def test_projection_terms_without_a_season_average_use_the_base_projection():
    terms = td.projection_terms(_eplay(szn_avg=None)["facts"])
    assert terms[0][0] == "the base projection against the line"
    assert sum(v for _, v in terms) == pytest.approx(35.0 - 32.5)


def test_dossier_text_speaks_betting_not_engine_internals():
    d = td.build_dossier(_eplay()["facts"])
    for needle in ("Our projection is 34.1 against a line of 32.5, 1.6 above it", "Season average is 31.0", "Projected for 36.2 minutes",
                   "Matchup note: a soft matchup bump (+1.3)", "Biggest push toward the Over: the projected workload",
                   "10th to 90th percentile range is 26.0 to 42.0; the line is inside it"):
        assert needle in d["text"], needle
    assert not any(j in d["text"].lower() for j in JARGON) and "0.97" not in d["text"] and "1.04" not in d["text"]
    prompt = td._fodder_prompt([_eplay()])
    assert not any(j in prompt.lower() for j in JARGON) and not any(j in td.REPLY_SYSTEM_PROMPT.lower() for j in JARGON[:3])


@pytest.mark.parametrize("opp,note", [(1.04, "a soft matchup bump (+1.3)"), (0.95, "a tough matchup downgrade (-1.8)")])
def test_opponent_factor_becomes_a_matchup_note_only_outside_the_neutral_band(opp, note):
    e = dict(ENGINE_ROW, opp_factor=opp)
    assert td._matchup(e)[0] == note
    assert td._matchup(dict(ENGINE_ROW, opp_factor=1.0)) is None and td._matchup(dict(ENGINE_ROW, opp_factor=1.02)) is None
    assert td._matchup(dict(ENGINE_ROW, opp_factor=None)) is None


def test_usage_phrase_names_the_unit():
    assert td._usage_phrase({"usage_proj": 18.24, "usage_label": "TOI (min)"}) == "projected for 18.2 minutes"
    assert td._usage_phrase({"usage_proj": 7.2, "usage_label": "Targets"}) == "projected usage of 7.2 targets"
    assert td._usage_phrase({"usage_proj": 7.2, "usage_label": None}) is None and td._usage_phrase({}) is None


def test_caveats_come_from_the_range_the_season_average_and_the_sample():
    f = _eplay()["facts"]       # Over 32.5: the line is inside 26-42 and the 31.0 season average is below it
    assert td.reply_caveats(f) == ["the line sits inside our range of 26.0 to 42.0",
                                   "the 31.0 season average is on the other side of the line"]
    assert "Caveat to work in: the line sits inside our range" in td.build_dossier(f)["text"]
    clean = _eplay(szn_avg=33.0, proj_p10=33.0, proj_p90=40.0, n_games=40)["facts"]
    assert td.reply_caveats(clean) == [] and "Caveat" not in td.build_dossier(clean)["text"]
    assert td.reply_caveats(_eplay(szn_avg=33.0, proj_p10=33.0, proj_p90=40.0, n_games=8)["facts"]) == ["it rests on only 8 games of history"]


def test_engine_numbers_are_quotable_only_next_to_a_projection_that_supports_the_pick():
    f = _eplay()["facts"]
    short = "Season average is 31.0, but on 36 projected minutes we have him closer to 34.1 with DraftKings at +115."
    assert td.reply_variant_ok(short, f)
    for n in ("31.0", "26.0", "42.0", "36.2", "36", "+1.3", "1.3"):
        assert n in td._allowed_numbers(f), n
    hidden = _eplay(proj_mean=31.0, proj_raw=32.0)["facts"]       # the projection sits against the Over: engine numbers stay private
    assert hidden["proj_mean"] is None and hidden["engine"]["szn_avg"] == 31.0
    assert not {"31.0", "26.0", "42.0", "36.2"} & td._allowed_numbers(hidden)
    assert td.build_dossier(hidden) is None and "No projection." in td._fodder_prompt([dict(_eplay(), facts=hidden)])
    assert not td.reply_variant_ok(short, hidden)


def test_plays_without_an_engine_row_still_get_price_math_and_no_dossier():
    play = td.build_reply_ammo(_feed(nba=[_outlier()]), {})[0]
    assert play["facts"]["engine"] is None and td.build_dossier(play["facts"]) is None
    assert td.projection_terms(play["facts"]) is None and td.reply_caveats(play["facts"]) == []
    assert "No projection." in td._fodder_prompt([play])


def test_there_is_one_gemini_call_and_no_separate_analysis_step(monkeypatch):
    assert not hasattr(td, "generate_analyses") and not hasattr(td, "ANALYSIS_SYSTEM_PROMPT")
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    monkeypatch.setattr(td, "get_ev_feed", lambda *a, **k: _feed(nba=[_outlier()]))
    monkeypatch.setattr(td, "get_projections", lambda sp: {"rows": [ENGINE_ROW]})
    calls = []
    monkeypatch.setattr(td, "_gemini_json_text", lambda system, user, max_tokens=0: calls.append(system) or json.dumps({"0": GOOD}))
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.run_reply_ammo()
    assert calls == [td.REPLY_SYSTEM_PROMPT]
    assert not any("analysis" in m.get("content", "") for m in _posted(post))


# 160-Character Tweet Engine Tests

def test_enforce_160_cap_strips_markdown_and_emojis():
    raw = '```json\n"FD hanging Tatum o26.5 Pts at +112 vs -108 fair! 🔥 #GamblingX beezy.fyi/picks"\n```'
    clean = td.enforce_160_cap(raw, max_chars=160)
    assert len(clean) <= 160
    assert "```" not in clean
    assert "🔥" not in clean
    assert "!" not in clean
    assert "#" not in clean
    assert clean.startswith("FD hanging Tatum o26.5 Pts")


@pytest.mark.parametrize("banned", [
    "lock", "smash", "parlay", "AI", "algorithm", "guaranteed", "let's dive in", "fire", "tail",
])
def test_enforce_160_cap_blocks_banned_words(banned):
    bad = f"This pick is an absolute {banned} tonight on the slate. beezy.fyi/picks"
    assert td.enforce_160_cap(bad, max_chars=160) == ""


def test_enforce_160_cap_allows_locked_and_retail():
    ok1 = "3 official confluence plays locked on today's card. beezy.fyi/picks"
    assert td.enforce_160_cap(ok1, max_chars=160) == ok1

    ok2 = "Retail books are hanging -115 on the over. beezy.fyi/picks"
    assert td.enforce_160_cap(ok2, max_chars=160) == ok2


@pytest.mark.parametrize("k", [1, 3, 5])
def test_generate_short_tweet_copy_morning_lengths_and_voice(k):
    top = {
        "player": "Jayson Tatum",
        "line": "o26.5 Pts",
        "book": "FanDuel",
        "odds": "+112",
        "fair": "-108",
        "n_books": 8,
        "ev": "+5.4%",
    }
    copy = td.generate_short_tweet_copy("morning", {"count": k, "top_play": top})
    assert len(copy) <= 160
    assert not td.contains_banned_word(copy)
    assert "beezy.fyi/picks" in copy
    assert "Tatum" in copy
    assert f"{k} official" in copy


def test_generate_short_tweet_copy_dislocation():
    payload = {
        "gap": "1.5-pt",
        "sport": "NBA",
        "player": "Fox",
        "stat": "Pts",
        "side1": "o24.5",
        "odds1": "-105",
        "book1": "FD",
        "side2": "u26.5",
        "odds2": "-108",
        "book2": "DK",
        "hold": "1.6",
        "win_rate": "14.2",
    }
    copy = td.generate_short_tweet_copy("dislocation", payload)
    assert len(copy) <= 160
    assert not td.contains_banned_word(copy)
    assert "beezy.fyi/ev" in copy
    assert "Fox Pts" in copy
    assert "1.6% hold" in copy


def test_generate_short_tweet_copy_settlement_winning_day():
    payload = {
        "w": 3,
        "l": 1,
        "units": 2.18,
        "hero": "Cole o6.5 Ks (+115)",
        "roi": 8.2,
        "total_bets": 7500,
        "total_units": 568.0,
    }
    copy = td.generate_short_tweet_copy("settlement", payload)
    assert len(copy) <= 160
    assert not td.contains_banned_word(copy)
    assert "beezy.fyi/results" in copy
    assert "3-1" in copy
    assert "+2.18u" in copy
    assert "+8.2% flat ROI" in copy


def test_generate_short_tweet_copy_settlement_losing_day():
    payload = {
        "w": 1,
        "l": 3,
        "units": -1.40,
        "hero": "",
        "roi": 8.1,
        "total_bets": 7500,
        "total_units": 568.0,
    }
    copy = td.generate_short_tweet_copy("settlement", payload)
    assert len(copy) <= 160
    assert not td.contains_banned_word(copy)
    assert "beezy.fyi/results" in copy
    assert "1-3" in copy
    assert "-1.40u" in copy
    assert "+8.1% flat ROI" in copy

