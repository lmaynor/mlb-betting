"""tests/test_gif_fetcher.py -- celebration GIF resolver: query building, provider parsing, never-None fallback."""
from unittest.mock import MagicMock, patch

import pytest

from mlb_core.social import gif_fetcher as gf


def _resp(payload):
    m = MagicMock()
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def test_team_tables_cover_every_franchise():
    assert (len(gf.MLB_TEAMS), len(gf.NFL_TEAMS), len(gf.NHL_TEAMS), len(gf.NBA_TEAMS)) == (30, 32, 32, 30)
    assert len(gf.fallback_queries()) == 124


def test_build_query_prefers_player():
    assert gf.build_query({"player": "Alex Ovechkin", "sport": "nhl", "bet_type": "SOG_OVER_2.5"}) == "Alex Ovechkin celebration"


def test_build_query_game_bet_uses_winning_team():
    q = gf.build_query({"bet_type": "HOME", "home_team": "NYY", "away_team": "BOS", "sport": "mlb"})
    assert q == "New York Yankees celebration"
    q = gf.build_query({"bet_type": "AWAY", "home_team": "NYY", "away_team": "BOS", "sport": "mlb"})
    assert q == "Boston Red Sox celebration"


@pytest.mark.parametrize("bet,expected", [
    ({"bet_type": "NRFI"}, "baseball strikeout celebration"),
    ({"bet_type": "K_OVER_6.5"}, "pitcher strikeout celebration"),
    ({"bet_type": "X", "sport": "nfl"}, "touchdown celebration"),
    ({"bet_type": "X", "sport": "nhl"}, "hockey goal celebration"),
    ({"bet_type": "X", "sport": "nba"}, "three pointer celebration"),
])
def test_build_query_outcome_fallbacks(bet, expected):
    assert gf.build_query(bet) == expected


def test_no_keys_returns_fallback_not_none(monkeypatch):
    monkeypatch.delenv("TENOR_API_KEY", raising=False)
    monkeypatch.delenv("GIPHY_API_KEY", raising=False)
    out = gf.fetch_celebration_gif({"player": "Aaron Judge"})
    assert out["source"] == "fallback" and out["gif_url"] == "" and out["query"] == "Aaron Judge celebration"
    assert out["search_url"].startswith("https://tenor.com/search/aaron-judge-celebration")


def test_tenor_happy_path(monkeypatch):
    monkeypatch.setenv("TENOR_API_KEY", "k")
    payload = {"results": [{"media_formats": {"gif": {"url": "https://x/g.gif"}, "mp4": {"url": "https://x/g.mp4"}, "tinygif": {"url": "https://x/t.gif"}}}]}
    with patch.object(gf.requests, "get", return_value=_resp(payload)):
        out = gf.fetch_celebration_gif({"player": "Aaron Judge"})
    assert out == {"gif_url": "https://x/g.gif", "mp4_url": "https://x/g.mp4", "preview_url": "https://x/t.gif",
                   "query": "Aaron Judge celebration", "source": "tenor"}


def test_tenor_error_falls_through_to_giphy(monkeypatch):
    monkeypatch.setenv("TENOR_API_KEY", "k")
    monkeypatch.setenv("GIPHY_API_KEY", "g")
    giphy = {"data": [{"images": {"original": {"url": "https://g/o.gif"}, "original_mp4": {"mp4": "https://g/o.mp4"}, "fixed_width_still": {"url": "https://g/s.gif"}}}]}

    def fake_get(url, **kw):
        if "tenor" in url:
            raise RuntimeError("boom")
        return _resp(giphy)

    with patch.object(gf.requests, "get", side_effect=fake_get):
        out = gf.fetch_celebration_gif({"bet_type": "NRFI"})
    assert out["source"] == "giphy" and out["gif_url"] == "https://g/o.gif"


def test_empty_results_and_errors_never_raise(monkeypatch):
    monkeypatch.setenv("TENOR_API_KEY", "k")
    with patch.object(gf.requests, "get", return_value=_resp({"results": []})):
        assert gf.fetch_celebration_gif({"bet_type": "HR"})["source"] == "fallback"
    with patch.object(gf.requests, "get", side_effect=RuntimeError("down")):
        assert gf.fetch_celebration_gif(None)["source"] == "fallback"


def test_module_is_ascii():
    import pathlib
    assert pathlib.Path(gf.__file__).read_text(encoding="utf8").isascii()
