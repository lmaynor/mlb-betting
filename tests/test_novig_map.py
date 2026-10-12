import json
import os

import pytest

from ev.novig.map import NovigLeg, event_index, map_prop_market
from mlb.execution.kalshi.mapping import MapFail

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_markets.json")))
EVENTS = event_index(FX["events"])
BY = {m["marketType"]: m for m in FX["markets"]}


def test_event_index_maps_full_team_names_and_et_date():
    assert EVENTS and all(k.startswith("nfl:2026-") and "@" in k for k, _ in EVENTS.values())


@pytest.mark.parametrize("t,stat", [("RECEIVING_YARDS", "rec_yds"), ("RUSHING_YARDS", "rush_yds"),
                                    ("PASSING_YARDS", "pass_yds"), ("RECEPTIONS", "receptions")])
def test_real_prop_markets_map(t, stat):
    m = BY[t]
    leg = map_prop_market(m, EVENTS)
    assert isinstance(leg, NovigLeg), leg
    assert leg.stat == stat and leg.fee_charged == "WHEN_LIVE" and leg.fee_coefficient == 0.03
    names = {o["outcomeId"]: o["name"] for o in m["outcomes"]}
    assert names[leg.over_id] == f"Over {m['strike']}" and names[leg.under_id] == f"Under {m['strike']}"


def test_outcome_order_never_matters():
    m = dict(BY["RECEIVING_YARDS"], outcomes=list(reversed(BY["RECEIVING_YARDS"]["outcomes"])))
    a, b = map_prop_market(BY["RECEIVING_YARDS"], EVENTS), map_prop_market(m, EVENTS)
    assert (a.over_id, a.under_id) == (b.over_id, b.under_id)


def test_fail_closed_cases():
    m = BY["RECEIVING_YARDS"]
    for patch in (dict(strike="22"), dict(description="Someone 99.5 RECEIVING_YARDS"), dict(status="CLOSED"),
                  dict(eventId="nope"), dict(marketType="DPOTY"), dict(voids="WEIRD"),
                  dict(outcomes=[{"outcomeId": "a", "name": "Over 22.5"}, {"outcomeId": "b", "name": "Yes"}])):
        assert isinstance(map_prop_market(dict(m, **patch), EVENTS), MapFail), patch
    # SPREAD/TOTAL are game lines, deliberately not enabled here
    assert isinstance(map_prop_market(BY["SPREAD"], EVENTS), MapFail)
