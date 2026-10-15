"""nba.data.espn_injuries: pure parse against a saved real-shape fixture, status classes, fail-closed behaviour."""
import json
from pathlib import Path

import pandas as pd
import pytest

from nba.data import espn_injuries as E

FX = Path(__file__).parent / "fixtures" / "espn_nba_injuries_sample.json"


@pytest.fixture(scope="module")
def payload():
    return json.loads(FX.read_text())


def test_parse_fixture_shape_and_ids(payload):
    df = E.parse_injuries(payload)
    assert list(df.columns) == E.COLUMNS and len(df) == 6 and df.attrs["ok"] is True
    atl = df[df["team_abbrev"] == "ATL"]
    assert set(atl["espn_player_id"]) == {4585618, 4397183, 4431680, 5105571}
    out = df[df["status"] == "Out"].iloc[0]
    assert out["player"] == "Henri Veesaar" and out["espn_player_id"] == 5105571
    assert "return 2027-07-01" in out["detail"] and out["updated"].startswith("2026-09-21")
    assert set(df["team_abbrev"]) == {"ATL", "BOS", "BKN"}                 # ESPN 'BKN' abbreviation maps to our tricode
    assert df["espn_player_id"].map(type).eq(int).all()


def test_status_classification():
    for raw, want in [("Out", "Out"), ("OUT", "Out"), ("Out For Season", "Out"), ("Doubtful", "Doubtful"),
                      ("Questionable", "Questionable"), ("Day-To-Day", "Day-To-Day"), ("Probable", "Probable"),
                      ("Weird", "Other"), (None, "Other")]:
        assert E.classify_status(raw) == want


def test_absent_ids_by_status_and_team_vocabulary(payload):
    df = E.parse_injuries(payload)
    assert E.absent_ids("ATL", frame=df) == {5105571}
    assert E.absent_ids("Atlanta Hawks", frame=df) == {5105571}
    assert E.absent_ids("ATL", statuses=("Out", "Day-To-Day"), frame=df) == {5105571, 4585618, 4397183, 4431680}
    assert E.absent_ids("BOS", frame=df) == set() and E.absent_ids("???", frame=df) == set()


def test_absent_by_team_covers_all_teams_only_when_the_report_is_usable(payload):
    df = E.parse_injuries(payload)
    m = E.absent_by_team(df)
    assert len(m) == 30 and m["ATL"] == {5105571} and m["LAL"] == set()      # unlisted team = healthy
    assert E.absent_by_team(E.parse_injuries({"nope": 1})) == {}             # unknown must not look healthy


def test_fail_closed_never_raises(payload):
    for bad in (None, [], "x", {}, {"injuries": "x"}, {"injuries": None}):
        df = E.parse_injuries(bad)
        assert len(df) == 0 and df.attrs["ok"] is False
    broken = json.loads(json.dumps(payload))
    broken["injuries"][0]["injuries"][0]["athlete"] = {"displayName": "No Ids"}     # unparseable entry
    broken["injuries"][1]["injuries"].append("garbage")
    df = E.parse_injuries(broken)
    assert df.attrs["ok"] is False and df.attrs["n_skipped"] == 2 and len(df) == 5   # the rest still parsed, flagged
    assert E.absent_by_team(df) == {}


def test_fetch_never_raises_and_flags_failure():
    class Boom:
        def get(self, *a, **k):
            raise ConnectionError("down")

    class Bad:
        def get(self, *a, **k):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    raise ValueError("not json")
            return R()

    class Good:
        def __init__(self, p):
            self.p = p

        def get(self, url, headers=None, timeout=None):
            assert url == E.ESPN_INJURIES_URL

            class R:
                def raise_for_status(s):
                    pass

                def json(s):
                    return self.p
            return R()

    assert E.fetch_injuries(Boom()).attrs["ok"] is False
    assert E.fetch_injuries(Bad()).attrs["ok"] is False
    assert E.fetch_injuries(Good(json.loads(FX.read_text()))).attrs["ok"] is True


def test_attach_ids_uses_crosswalk_and_bridge(payload):
    df = E.parse_injuries(payload)

    class FakeBridge:
        def sb_id(self, e):
            return ("sb-" + str(e), "ok") if e == 5105571 else (None, "unknown")

    xw = pd.DataFrame({"espn_id": [5105571, 4585618]})
    out = E.attach_ids(df, xw, FakeBridge())
    assert out["in_xwalk"].sum() == 2 and out["sb_player_id"].notna().sum() == 1
    assert out.attrs["ok"] is True and "in_xwalk" not in df.columns
