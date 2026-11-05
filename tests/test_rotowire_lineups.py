"""RotoWire lineups parser + confirmed-goalie wiring. Offline: the HTML below is a hand-written stand-in for the page's
text nodes (team nickname and record as separate nodes, as the live page writes them), not a copy of RotoWire content."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nhl.data import rotowire_lineups as rw
from nhl.game.goalie_model import GoalieTracker

HTML = """
<html><head><script>var x = "Confirmed";</script></head><body>
<div>7:30 PM ET</div>
<span>Senators</span><span>(10-6-1)</span><span>Red Wings</span><span>(7-5-3)</span>
<div>Goalie One</div><div>Confirmed</div>
<div>POWER PLAY #1</div><div>C</div><div>Center A</div><div>LW</div><div>Wing B</div>
<div>INJURIES</div><div>LW</div><div>Hurt C</div><div>IR</div>
<div>Goalie Two</div><div>Expected</div>
<div>POWER PLAY #1</div><div>C</div><div>Center D</div>
<div>LINE</div>
</body></html>
"""


def test_parse_merges_record_nodes_and_ignores_script():
    games = rw.parse_lineups(rw.html_to_lines(HTML))
    assert len(games) == 1
    g = games[0]
    assert g["teams"] == ["Senators", "Red Wings"]
    assert [b["goalie"] for b in g["blocks"]] == ["Goalie One", "Goalie Two"]
    assert g["blocks"][0]["lineup"][0] == {"pp": 1, "pos": "C", "name": "Center A"}
    assert g["blocks"][0]["injuries"] == [{"pos": "LW", "name": "Hurt C", "status": "IR"}]


def test_only_confirmed_counts_and_nicknames_resolve():
    by = rw.goalies_by_team(rw.parse_lineups(rw.html_to_lines(HTML)))
    assert by["OTT"]["confirmed"] and not by["DET"]["confirmed"]
    for nick, code in [("Maple Leafs", "TOR"), ("Golden Knights", "VGK"), ("Blue Jackets", "CBJ"), ("Blues", "STL")]:
        assert rw.team_code(nick) == code


def test_confirmed_goalies_dates_and_fail_open(monkeypatch):
    now = datetime(2026, 11, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    calls = []
    monkeypatch.setattr(rw, "fetch_games", lambda tomorrow=False: calls.append(tomorrow) or
                        rw.parse_lineups(rw.html_to_lines(HTML)))
    assert rw.confirmed_goalies("2026-11-04", now) == {"OTT": "Goalie One"}
    rw.confirmed_goalies("2026-11-05", now)
    assert calls == [False, True]
    assert rw.confirmed_goalies("2026-11-09", now) == {} and len(calls) == 2   # not on the page: no request

    def boom(tomorrow=False):
        raise RuntimeError("down")
    monkeypatch.setattr(rw, "fetch_games", boom)
    assert rw.confirmed_goalies("2026-11-04", now) == {}


def test_resolve_goalie_id_and_confirmed_projection():
    t = GoalieTracker()
    for i, (gid, name) in enumerate([("1", "Samuel Ersson"), ("2", "Linus Ullmark")]):
        t.record_start(gid, name, "OTT", f"2026-10-0{i + 1}", f"g{i}", 60.0, 30.0, 2.0)
    assert t.resolve_goalie_id("OTT", "Samuel Ersson") == "1"
    assert t.resolve_goalie_id("OTT", "L. Ullmark") == "2"
    assert t.resolve_goalie_id("OTT", "Nobody Known") is None
    p = t.project_team_goalie("OTT", "2026-11-04", confirmed_starter_id="1", confirmed_starter_name="Samuel Ersson")
    assert p.is_confirmed and p.starter_name == "Samuel Ersson"


def test_cache_roundtrip_staleness_and_empty_page(monkeypatch):
    store = {}
    monkeypatch.setattr(rw.storage, "write_bytes", lambda data, key: store.__setitem__(key, data))
    monkeypatch.setattr(rw.storage, "read_bytes", lambda key: store[key])
    now = datetime(2026, 11, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(rw, "fetch_games", lambda tomorrow=False: rw.parse_lineups(rw.html_to_lines(HTML)))
    assert rw.refresh_cache(now) == {"OTT": "Goalie One"}
    assert rw.cached_confirmed_goalies("2026-11-04", now) == {"OTT": "Goalie One"}
    assert rw.cached_confirmed_goalies("2026-11-05", now) == {}                       # another day
    later = datetime(2026, 11, 4, 15, tzinfo=ZoneInfo("America/New_York"))
    assert rw.cached_confirmed_goalies("2026-11-04", later) == {}                     # older than CACHE_MAX_AGE_MIN
    before = store[rw.CACHE_KEY]
    monkeypatch.setattr(rw, "fetch_games", lambda tomorrow=False: [])
    try:
        rw.refresh_cache(now)
        raise AssertionError("an empty page must raise")
    except RuntimeError:
        pass
    assert store[rw.CACHE_KEY] == before                                              # good cache kept
