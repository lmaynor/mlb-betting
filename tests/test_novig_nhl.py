"""NHL wiring for the Novig taker executor (2026-11-12): the league-parameterized mapper, the NHL leg
loader, and the OddsAccum -> consensus snapshot route. Shapes are the live ones probed 2026-11-12
(Novig catalog: "Patrick Kane 3.5 SHOTS_ON_GOAL" / "Ilya Sorokin 24.5 SAVES"; ParlayAPI NHL outcomes:
name "Over <player>", description = player)."""
import json
import os
from datetime import datetime, timezone

import pytest

from ev.novig import exec as nx
from ev.novig import nhl_snapshot as nsnap
from ev.novig.map import NovigLeg, event_index, map_prop_market
from ev.props_config import props_config
from mlb.execution.kalshi.mapping import MapFail

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_nhl_markets.json")))
EVENTS = event_index(FX["events"], "NHL")
BY = {m["marketType"]: m for m in FX["markets"]}
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)          # 13:00 ET on game day


# ---------------------------------------------------------------- mapper
def test_nhl_event_index_uses_nhl_api_abbrevs_and_et_date():
    gk, start = EVENTS["ev-kings-flames"]
    assert gk == "nhl:2026-11-12:CGY@LAK" and start.hour == 3      # 22:00 ET puck drop, ET date 11-12
    assert EVENTS["ev-mammoth-sharks"][0] == "nhl:2026-11-12:SJS@UTA"   # Utah Mammoth is in the table
    assert "ev-unknown" not in EVENTS                              # unknown team -> fail closed, skipped


def test_nhl_sog_and_saves_map():
    sog = map_prop_market(BY["SHOTS_ON_GOAL"], EVENTS, "NHL")
    assert isinstance(sog, NovigLeg), sog
    assert (sog.stat, sog.player, sog.player_norm, sog.strike) == ("sog", "Patrick Kane", "patrick kane", 3.5)
    assert sog.game_key == "nhl:2026-11-12:CGY@LAK"
    assert sog.fee_charged == "WHEN_LIVE" and sog.fee_coefficient == 0.06   # coefficient arrives as a string
    names = {o["outcomeId"]: o["name"] for o in BY["SHOTS_ON_GOAL"]["outcomes"]}
    assert names[sog.over_id] == "Over 3.5" and names[sog.under_id] == "Under 3.5"
    sav = map_prop_market(BY["SAVES"], EVENTS, "NHL")              # outcomes are Under-first in the fixture
    assert isinstance(sav, NovigLeg) and sav.stat == "saves" and sav.strike == 24.5
    names = {o["outcomeId"]: o["name"] for o in BY["SAVES"]["outcomes"]}
    assert names[sav.over_id] == "Over 24.5" and names[sav.under_id] == "Under 24.5"   # by NAME, not position


def test_nhl_fail_closed_cases():
    assert isinstance(map_prop_market(BY["PLAYER_GOALS"], EVENTS, "NHL"), MapFail)      # not an enabled stat
    m = BY["SHOTS_ON_GOAL"]
    for patch in (dict(strike="3"), dict(description="Patrick Kane 4.5 SHOTS_ON_GOAL"), dict(status="CLOSED"),
                  dict(eventId="nope"), dict(voids="WEIRD")):
        assert isinstance(map_prop_market(dict(m, **patch), EVENTS, "NHL"), MapFail), patch
    # the same NHL market under the NFL tables must not map (league parameter is load-bearing)
    assert isinstance(map_prop_market(m, event_index(FX["events"])), MapFail)


# ---------------------------------------------------------------- leg loading
class _FakeNC:
    def __init__(self, events, markets):
        self._events, self._markets = events, markets

    def list_events(self, league):
        assert league == "NHL"
        return self._events

    def list_markets(self, league):
        assert league == "NHL"
        return self._markets


def test_load_novig_legs_nhl_keys_and_future_filter():
    legs = nx.load_novig_legs(_FakeNC(FX["events"], FX["markets"]), NOW, "NHL")
    assert set(legs) == {("nhl:2026-11-12:CGY@LAK", "sog", "patrick kane", 3.5),
                         ("nhl:2026-11-12:SJS@UTA", "saves", "ilya sorokin", 24.5)}
    later = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    assert nx.load_novig_legs(_FakeNC(FX["events"], FX["markets"]), later, "NHL") == {}


# ---------------------------------------------------------------- snapshot route
def _strip(book, over, under):
    return {"book": book, "over": over, "under": under}


PROJ = {"ts": "2026-11-12T18:30:00+00:00", "rows": [
    {"sport": "nhl", "stat": "sog", "player": "Patrick Kane", "player_norm": "patrick kane", "team": "DET",
     "game_key": "nhl:2026-11-12:CGY@LAK", "commence": "2026-11-13T03:00:00+00:00", "has_line": True,
     "line": 3.5, "p_over_blend": 0.55, "p_over_market": 0.52, "n_books": 7},
    {"sport": "nhl", "stat": "goals", "player": "Patrick Kane", "player_norm": "patrick kane", "team": "DET",
     "game_key": "nhl:2026-11-12:CGY@LAK", "commence": "2026-11-13T03:00:00+00:00", "has_line": True,
     "line": 0.5, "p_over_blend": 0.3, "n_books": 5},                       # not an enabled stat: excluded
    {"sport": "nhl", "stat": "saves", "player": "Ilya Sorokin", "player_norm": "ilya sorokin", "team": "NYI",
     "game_key": "nhl:2026-11-12:SJS@UTA", "commence": "2026-11-13T01:00:00+00:00", "has_line": False,
     "line": None, "p_over_blend": None, "n_books": 0},                    # no line yet: excluded
]}

BOARD = {"ts": "2026-11-12T18:30:00+00:00", "rows": [
    {"game_key": "nhl:2026-11-12:CGY@LAK", "player_norm": "patrick kane", "stat": "sog", "line": 3.5,
     "is_alt": False, "books": [_strip("betmgm", -110, -110), _strip("fanduel", -115, -105),
                                _strip("caesars", -108, -112), _strip("espnbet", -112, -108),
                                # the aggregator carries Novig ONE-sided here -- the live-board discrepancy shape
                                _strip("novig", 102, None),
                                _strip("sleeper", -112, -112)]},          # DFS: never a reference book
]}


def test_nhl_snapshot_shape_and_fair():
    snap = nsnap.build_snapshot(PROJ, BOARD)
    (ev,) = snap["events"]
    assert ev["game_key"] == "nhl:2026-11-12:CGY@LAK" and ev["commence"].startswith("2026-11-13T03:00")
    (pt,) = ev["points"]                                                   # goals + line-less saves excluded
    assert pt["player_norm"] == "patrick kane" and pt["stat"] == "sog" and pt["point"] == 3.5
    assert pt["p_over"] == 0.55                                            # the SERVED blend is the fair
    assert set(pt["book_fair"]) == {"betmgm", "fanduel", "caesars", "espnbet"}


def test_candidates_nhl_join_and_leave_out_regression():
    """Discrepancy regression (2026-11-12): admission must not depend on how completely the aggregator
    represents Novig. The strip's Novig entry is one-sided here and excluded from its own reference
    regardless; the candidate's fair stays the served blend while n_books/disp are the leave-out strip's.
    A point whose strip has no other two-sided book can never become a candidate."""
    snap = nsnap.build_snapshot(PROJ, BOARD)
    legs = nx.load_novig_legs(_FakeNC(FX["events"], FX["markets"]), NOW, "NHL")
    cands = nsnap.candidates_nhl(snap["events"], legs, props_config(), NOW)
    assert [(leg.stat, leg.player_norm) for leg, _p, _m in cands] == [("sog", "patrick kane")]
    _leg, point, _mins = cands[0]
    assert point["p_over"] == 0.55 and point["n_books"] == 4               # 4 real books, Novig left out

    thin_board = {"ts": BOARD["ts"], "rows": [dict(BOARD["rows"][0], books=[_strip("novig", -110, -110)])]}
    snap2 = nsnap.build_snapshot(PROJ, thin_board)
    assert nsnap.candidates_nhl(snap2["events"], legs, props_config(), NOW) == []
