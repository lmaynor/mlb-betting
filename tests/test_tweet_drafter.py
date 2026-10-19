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


def test_build_x_intent_url_round_trips_text_with_reserved_characters():
    text = "DK +115 vs -108 fair & 7.1% EV (paper)? #GamblingX\nbeezy.fyi"
    url = td.build_x_intent_url(text)
    assert url.startswith("https://x.com/intent/tweet?text=")
    assert " " not in url and "\n" not in url and "&" not in url.split("text=", 1)[1]
    assert parse_qs(urlparse(url).query)["text"] == [text]


def test_build_x_search_url_is_live_search_with_min_faves():
    url = td.build_x_search_url("Luka Doncic min_faves:20")
    parts = urlparse(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "x.com", "/search")
    q = parse_qs(parts.query)
    assert q["q"] == ["Luka Doncic min_faves:20"] and q["f"] == ["live"]


def _embed_total(batch):
    return sum(td._embed_len(e) for e in batch)


def test_post_embeds_splits_on_count_and_text_budget_and_attaches_content_once():
    hook = "https://discord.test/h"
    many = [{"title": f"e{i}", "description": "x"} for i in range(23)]
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        out = td.post_embeds(hook, "hello", many)
    bodies = [c.kwargs["json"] for c in post.call_args_list]
    assert [len(b["embeds"]) for b in bodies] == [10, 10, 3] and "3 messages" in out
    assert [("content" in b) for b in bodies] == [True, False, False]

    fat = [{"title": "t", "description": "y" * 2400} for _ in range(3)]
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_embeds(hook, "", fat)
    bodies = [c.kwargs["json"] for c in post.call_args_list]
    assert len(bodies) == 2 and all(_embed_total(b["embeds"]) <= 5500 for b in bodies)


def test_post_embeds_reports_failure_status_without_raising():
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=400)):
        assert "FAILED (400)" in td.post_embeds("https://d.test/h", "c", [{"title": "t", "description": "d"}])


def test_post_drafts_to_discord_has_card_first_intent_links_and_reply_text(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    tweets = ["Variant one: DK +115 vs -108 fair. beezy.fyi", "Variant two. beezy.fyi"]
    card = "https://beezy.fyi/api/og/picks-card"
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        out = td.post_drafts_to_discord("picks", tweets, card_url=card, reply_url="https://beezy.fyi/picks")
    assert "ok" in out and post.call_count == 1
    body = post.call_args.kwargs["json"]
    assert body["embeds"][0]["image"]["url"] == card                       # card on top, saveable
    descs = [e["description"] for e in body["embeds"][1:]]
    for tweet, desc in zip(tweets, descs):
        assert f"```\n{tweet}\n```" in desc
        assert f"[1-Tap Post on X]({td.build_x_intent_url(tweet)})" in desc
    assert "Full live board + model projections -> https://beezy.fyi/picks" in body["content"]
    assert len(body["content"]) <= 2000


def test_post_drafts_to_discord_without_webhook_skips(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_TWEETS", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_OPS", raising=False)
    assert "SKIPPED" in td.post_drafts_to_discord("picks", ["t"])


@pytest.mark.parametrize("sport", ["mlb", "nhl", "nba"])
def test_build_settlement_across_tracked_sports(sport):
    rows = [_bet(YESTERDAY, "win", 1.15, player="Some Player", odds=115, sport=sport),
            _bet(YESTERDAY, "loss", -1.0, sport=sport)]
    s = td.build_settlement(rows, {"overall": {"roi": 8.2, "total_bets": 7550}})
    assert (s["w"], s["l"]) == (1, 1) and s["net"] == pytest.approx(0.15)
    assert not td.has_url(s["text"]) and s["text"].endswith(td.WATERMARK)   # main tweet is link-free
    assert s["reply_url"] == f"{td.BEEZY_SITE_URL}/results"
    assert len(s["text"]) <= 280 and not td.violates_voice(s["text"])


def test_nfl_is_not_a_settlement_sport_because_nfl_bets_are_not_tracked():
    """bets.sport is mlb|nhl|nba, so /api/public/picks/recent?sport=nfl answers 400 and there is nothing to settle. If NFL
    bets are ever tracked, add it here AND to the settlement-card route's SPORTS, and flip this test."""
    assert td.SETTLEMENT_SPORTS == ("mlb", "nhl", "nba")
    from mlb_core.tracking.bet_tracker import VALID_SPORTS
    assert set(td.SETTLEMENT_SPORTS) == set(VALID_SPORTS)


def test_post_settlement_has_intent_link_and_link_reply(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    summary = {"date": "2026-10-13", "text": "tweet body. beezy.fyi", "card_url": "https://beezy.fyi/api/og/settlement-card?date=2026-10-13",
               "reply_url": "https://beezy.fyi/results"}
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.post_settlement_to_discord(summary, {"gif_url": "", "query": "q", "search_url": "https://tenor.com/s"})
    content = post.call_args.kwargs["json"]["content"]
    assert f"[1-Tap Post on X]({td.build_x_intent_url(summary['text'])})" in content
    assert "full ledger -> https://beezy.fyi/results" in content and len(content) <= 2000


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
    assert text == ("Luka Doncic Over 32.5 Points: DraftKings is hanging +115 while the 8-book de-vigged fair sits at -108 "
                    "(+11.6% EV vs consensus). Our projection: 34.1.")
    assert len(text) <= td.REPLY_SNIPPET_MAX
    assert td.is_link_free(text) and not td.violates_voice(text)


def test_reply_snippet_drops_optional_parts_to_fit_and_never_truncates():
    long_name = "Maximilian Bartholomew Featherstonehaugh-Cholmondeley"
    c = td.reply_candidates(_feed(nba=[_outlier(player=long_name)]))[0]
    text = td.build_reply_snippet(c, proj_mean=34.1)
    assert text is not None and len(text) <= td.REPLY_SNIPPET_MAX and "projection" not in text
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
    assert plays[0]["snippet"].endswith("Our projection: 34.12.")
    assert "projection" not in td.build_reply_ammo(feed, {})[0]["snippet"]


def test_run_reply_ammo_posts_one_embed_per_play_with_search_link(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_TWEETS", "https://discord.test/hook")
    monkeypatch.setattr(td, "get_ev_feed", lambda *a, **k: _feed(nba=[_outlier(), _outlier(player="Jayson Tatum", player_norm="jayson tatum", ev=0.09)]))
    monkeypatch.setattr(td, "get_projections", lambda sp: (_ for _ in ()).throw(RuntimeError("down")) if sp == "nfl" else {"rows": []})
    with patch.object(td.requests, "post", return_value=MagicMock(status_code=204)) as post:
        td.run_reply_ammo()
    body = post.call_args.kwargs["json"]
    assert len(body["embeds"]) == 2 and "REPLY" in body["content"]
    for e in body["embeds"]:
        assert "x.com/search?q=" in e["description"] and "&f=live" in e["description"]
        assert "http" not in e["description"].split("```")[1]                # the copyable block itself has no link


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
