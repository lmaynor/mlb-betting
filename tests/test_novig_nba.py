"""NBA wiring for the Novig taker executor (2026-11-13): the league-parameterized mapper, the NBA leg
loader, and the served-boards snapshot route. Shapes are the live ones probed 2026-11-13 (Novig catalog:
"Brook Lopez 1.5 REBOUNDS" / "Darius Garland 6.5 THREE_POINTERS_MADE", plus the parallel *_PROTECTED
listings; NBA served boards: game keys nba:<ET date>:<AWAY>@<HOME> with tricodes)."""
import json
import os
from datetime import datetime, timezone

import pytest

from ev.novig import exec as nx
from ev.novig import nba_snapshot as nsnap
from ev.novig.map import NovigLeg, event_index, map_prop_market
from ev.props_config import props_config
from mlb.execution.kalshi.mapping import MapFail

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_nba_markets.json")))
EVENTS = event_index(FX["events"], "NBA")
BY = {m["marketType"]: m for m in FX["markets"]}
NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)          # 13:00 ET on game day
GK = "nba:2026-11-13:OKC@LAC"


# ---------------------------------------------------------------- mapper
def test_nba_event_index_uses_tricodes_and_et_date():
    gk, start = EVENTS[FX["events"][0]["eventId"]]
    assert gk == GK and start.hour == 3                            # 22:35 ET tip, ET date 11-13
    assert "ev-unknown" not in EVENTS                              # unknown team -> fail closed, skipped


def test_nba_rebounds_and_threes_map():
    reb = map_prop_market(BY["REBOUNDS"], EVENTS, "NBA")
    assert isinstance(reb, NovigLeg), reb
    assert (reb.stat, reb.player, reb.player_norm, reb.strike) == ("rebounds", "Brook Lopez", "brook lopez", 1.5)
    assert reb.game_key == GK
    assert reb.fee_charged == "WHEN_LIVE" and reb.fee_coefficient == 0.06   # coefficient arrives as a string
    names = {o["outcomeId"]: o["name"] for o in BY["REBOUNDS"]["outcomes"]}
    assert names[reb.over_id] == "Over 1.5" and names[reb.under_id] == "Under 1.5"
    thr = map_prop_market(BY["THREE_POINTERS_MADE"], EVENTS, "NBA")
    assert isinstance(thr, NovigLeg) and thr.stat == "threes" and thr.strike == 6.5
    # outcomes are identified by NAME, not position: reversing the array changes nothing
    rev = dict(BY["REBOUNDS"], outcomes=list(reversed(BY["REBOUNDS"]["outcomes"])))
    reb2 = map_prop_market(rev, EVENTS, "NBA")
    assert (reb2.over_id, reb2.under_id) == (reb.over_id, reb.under_id)


def test_nba_fail_closed_cases():
    # points is display-only on the served board; assists was removed from the signal allowlist
    # 2026-11-10 and is banned as a fair input in the Novig ruling; combos are not main-line props;
    # the *_PROTECTED twin is a different listed product. All stay unmapped.
    for t in ("POINTS", "ASSISTS", "POINTS_REBOUNDS_ASSISTS", "REBOUNDS_PROTECTED"):
        assert isinstance(map_prop_market(BY[t], EVENTS, "NBA"), MapFail), t
    m = BY["REBOUNDS"]
    for patch in (dict(strike="2"), dict(description="Brook Lopez 2.5 REBOUNDS"), dict(status="CLOSED"),
                  dict(eventId="nope"), dict(voids="WEIRD")):
        assert isinstance(map_prop_market(dict(m, **patch), EVENTS, "NBA"), MapFail), patch
    # the same NBA market under the NFL / NHL tables must not map (league parameter is load-bearing)
    assert isinstance(map_prop_market(m, event_index(FX["events"])), MapFail)
    assert isinstance(map_prop_market(m, event_index(FX["events"], "NHL"), "NHL"), MapFail)


# ---------------------------------------------------------------- leg loading
class _FakeNC:
    def __init__(self, events, markets):
        self._events, self._markets = events, markets

    def list_events(self, league):
        assert league == "NBA"
        return self._events

    def list_markets(self, league):
        assert league == "NBA"
        return self._markets


def test_load_novig_legs_nba_keys_and_future_filter():
    legs = nx.load_novig_legs(_FakeNC(FX["events"], FX["markets"]), NOW, "NBA")
    # exactly the two enabled stats: the protected twin (same player/line/event) neither enters nor
    # collides with the plain market's leg
    assert set(legs) == {(GK, "rebounds", "brook lopez", 1.5),
                         (GK, "threes", "darius garland", 6.5)}
    later = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    assert nx.load_novig_legs(_FakeNC(FX["events"], FX["markets"]), later, "NBA") == {}


# ---------------------------------------------------------------- snapshot route
def _strip(book, over, under):
    return {"book": book, "over": over, "under": under}


PROJ = {"ts": "2026-11-13T18:30:00+00:00", "rows": [
    {"sport": "nba", "stat": "rebounds", "player": "Brook Lopez", "player_norm": "brook lopez", "team": "LAC",
     "game_key": GK, "commence": "2026-11-14T03:35:00+00:00", "has_line": True,
     "line": 1.5, "p_over_blend": 0.62, "p_over_market": 0.58, "n_books": 6},
    {"sport": "nba", "stat": "points", "player": "Brook Lopez", "player_norm": "brook lopez", "team": "LAC",
     "game_key": GK, "commence": "2026-11-14T03:35:00+00:00", "has_line": True,
     "line": 19.5, "p_over_blend": 0.4, "n_books": 5},                        # not an enabled stat: excluded
    {"sport": "nba", "stat": "threes", "player": "Darius Garland", "player_norm": "darius garland", "team": "CLE",
     "game_key": GK, "commence": "2026-11-14T03:35:00+00:00", "has_line": False,
     "line": None, "p_over_blend": None, "n_books": 0},                      # no line yet: excluded
]}

BOARD = {"ts": "2026-11-13T18:30:00+00:00", "rows": [
    {"game_key": GK, "player_norm": "brook lopez", "stat": "rebounds", "line": 1.5,
     "is_alt": False, "books": [_strip("betmgm", -110, -110), _strip("fanduel", -115, -105),
                                _strip("caesars", -108, -112), _strip("espnbet", -112, -108),
                                # the aggregator carries Novig ONE-sided here -- the live-board discrepancy shape
                                _strip("novig", 102, None),
                                _strip("sleeper", -112, -112)]},          # DFS: never a reference book
]}


def test_nba_snapshot_shape_and_fair():
    snap = nsnap.build_snapshot(PROJ, BOARD)
    (ev,) = snap["events"]
    assert ev["game_key"] == GK and ev["commence"].startswith("2026-11-14T03:35")
    (pt,) = ev["points"]                                                   # points + line-less threes excluded
    assert pt["player_norm"] == "brook lopez" and pt["stat"] == "rebounds" and pt["point"] == 1.5
    assert pt["p_over"] == 0.62                                            # the SERVED blend is the fair
    assert set(pt["book_fair"]) == {"betmgm", "fanduel", "caesars", "espnbet"}


def test_candidates_nba_join_and_leave_out_regression():
    """Discrepancy regression (the NHL lesson, 2026-11-12): admission must not depend on how completely
    the aggregator represents Novig. The strip's Novig entry is one-sided here and excluded from its own
    reference regardless; the candidate's fair stays the served blend while n_books/disp are the
    leave-out strip's. A point whose strip has no other two-sided book can never become a candidate."""
    snap = nsnap.build_snapshot(PROJ, BOARD)
    legs = nx.load_novig_legs(_FakeNC(FX["events"], FX["markets"]), NOW, "NBA")
    cands = nsnap.candidates_nba(snap["events"], legs, props_config(), NOW)
    assert [(leg.stat, leg.player_norm) for leg, _p, _m in cands] == [("rebounds", "brook lopez")]
    _leg, point, _mins = cands[0]
    assert point["p_over"] == 0.62 and point["n_books"] == 4               # 4 real books, Novig left out

    thin_board = {"ts": BOARD["ts"], "rows": [dict(BOARD["rows"][0], books=[_strip("novig", -110, -110)])]}
    snap2 = nsnap.build_snapshot(PROJ, thin_board)
    assert nsnap.candidates_nba(snap2["events"], legs, props_config(), NOW) == []
