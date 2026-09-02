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
