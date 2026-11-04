"""College basketball ('cbb') through the BestBook live crawl: slate parsing, BestBook team codes as identity, game-line ladders only
(the feed carries no cbb player props; probed live 2026-11-03: 27 games, section 'lines' only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ev import bestbook_events as bbe
from mlb_core.odds import bestbook as bb

MINUS = chr(0x2212)
NOW = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
GAME = "cbb:2026-11-03-SC-STET"


def _pct(american):
    p = 100.0 / (american + 100.0) if american > 0 else -american / (-american + 100.0)
    return round(p * 100.0, 4)


def _q(venue, yes, no):
    return {"venue": venue, "yes": _pct(yes), "no": _pct(no),
            "yesNative": f"{yes:+d}".replace("-", MINUS), "noNative": f"{no:+d}".replace("-", MINUS)}


def _row(rid, family, rung, line, quotes, rung_team=None):
    return {"id": f"match:{GAME}|{rid}", "venue": "match", "game": GAME, "gameTeams": ["STET", "SC"], "section": "lines",
            "family": f"{GAME}|{family}", "rung": rung, "line": line, "rungTeam": rung_team, "team": None, "quotes": quotes}


def test_parse_game_key_and_slate_know_cbb():
    assert bb.parse_game_key("cbb:2026-11-03-MAN-UK") == {"sport": "cbb", "date": "2026-11-03", "away": "MAN", "home": "UK"}
    assert bb.parse_game_key("cbb:2026-11-03-SDST-NKUUKY")["sport"] == "cbb"
    status = {"slate:cbb": {"games": [{"key": "cbb:2026-11-03-MAN-UK", "away": "MAN", "home": "UK", "start": "2026-11-04T00:01:34Z", "venues": ["dk"]}]}}
    assert [g["sport"] for g in bb.parse_slate(status)] == ["cbb"]


def test_cbb_is_a_crawlable_sport_and_keeps_bestbook_codes():
    assert "cbb" in bbe.SPORTS and "cbb" in bbe.DEFAULT_DAYS
    assert bbe.bb_team_code("cbb", " uk ") == "UK"
    assert bbe.bb_team_code("cbb", None) is None


def test_cbb_game_lines_captured_without_props():
    rows = [
        _row("ml|SC", "ml", "SC", None, [_q("dk", -300, 240), _q("fan", -310, 250)], rung_team="SC"),
        _row("total|full|140.5", "total|full", "Over 140.5", 140.5, [_q("dk", -110, -110), _q("fan", -108, -112)]),
        _row("spread|full|SC|7.5", "spread|full", f"SC {MINUS}7.5", 7.5, [_q("dk", -110, -110), _q("fan", -112, -108)], rung_team="SC"),
    ]
    slate = [{"key": GAME, "away": "STET", "home": "SC", "start": (NOW + timedelta(hours=5)).isoformat()}]
    gl: list = []
    events, counts = bbe.build_events_from_snapshots("cbb", slate, {GAME: {"rows": rows}}, NOW, game_lines=gl)
    assert events == [] and counts["events_unknown_team"] == 0
    assert counts["game_lines_games"] == 1 and len(gl) == 1
    g = gl[0]
    assert (g["away_team"], g["home_team"]) == ("STET", "SC")
    markets = {(x["market"], x["line"]) for x in g["quotes"]}
    assert markets == {("ml", None), ("total", 140.5), ("spread", -7.5)}      # spread line is the HOME handicap
    assert bbe.publishable({"counts": counts}) == (True, "priced")
