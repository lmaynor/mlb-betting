"""nhl.config team vocabulary, nhl.data.players (roster crosswalk), nhl.data.teams (team master)."""
import json
from datetime import date
from pathlib import Path

import pytest

from nhl import config as C
from nhl.data import masters, players, teams

FIX = Path(__file__).parent / "fixtures" / "nhl_roster_edm_sample.json"


class MemStore:
    def __init__(self):
        self.csvs = {}

    def exists(self, key):
        return key in self.csvs

    def read_csv(self, key, **kw):
        return self.csvs[key].copy()

    def write_csv(self, df, key):
        self.csvs[key] = df.copy()


@pytest.fixture
def mem(monkeypatch):
    m = MemStore()
    for n in ("exists", "read_csv", "write_csv"):
        monkeypatch.setattr(masters.storage, n, getattr(m, n))
    return m


# ----------------------------------------------------------------------------- config / team vocabulary
def test_team_table_is_32_unique_and_matches_kalshi_adapter():
    from ev.sports.nhl import CODES
    assert len(C.TEAMS) == 32 and len({t[0] for t in C.TEAMS}) == 32
    assert set(C.KALSHI_OF.values()) == set(CODES)                 # one vocabulary, two modules


@pytest.mark.parametrize("raw,abbr", [
    ("Montreal Canadiens", "MTL"), ("Montr\u00e9al Canadiens", "MTL"), ("LA", "LAK"), ("LAK", "LAK"), ("NJ", "NJD"),
    ("TB", "TBL"), ("SJ", "SJS"), ("St. Louis Blues", "STL"), ("Utah Mammoth", "UTA"), ("WSH", "WSH")])
def test_team_abbrev_across_vocabularies(raw, abbr):
    assert C.team_abbrev(raw) == abbr


def test_team_abbrev_unknown_is_none():
    assert C.team_abbrev("Springfield Isotopes") is None and C.team_abbrev("") is None


def test_master_keys_cover_the_crosswalk_masters():
    assert C.MASTER_KEYS[C.PLAYERS_MASTER] == ["player_id"] and C.MASTER_KEYS[C.TEAMS_MASTER] == ["abbrev"]


# ----------------------------------------------------------------------------- players
def test_flatten_roster_real_shape():
    rows = players.flatten_roster("EDM", json.load(open(FIX)))
    assert len(rows) == 9
    d = {r["player_id"]: r for r in rows}
    dach = d[8482703]
    assert dach["full_name"] == "Colton Dach" and dach["team"] == "EDM" and dach["position_group"] == "F"
    assert dach["headshot_latest_url"] == "https://assets.nhle.com/mugs/nhl/latest/8482703.png"
    assert dach["headshot_url"].startswith("https://assets.nhle.com/mugs/nhl/")
    assert {r["position_group"] for r in rows} == {"F", "D", "G"}
    assert all(r["name_norm"] == r["name_norm"].lower() for r in rows)


def test_refresh_players_upserts_and_tolerates_a_failed_team(mem):
    class C_:
        def roster(self, a):
            if a == "BOS":
                raise RuntimeError("503")
            if a == "TOR":
                return None
            return json.load(open(FIX)) if a == "EDM" else {}
    res = players.refresh_players(C_(), abbrevs=["EDM", "BOS", "TOR"], retry_pause=0)
    assert res["rows"] == 9 and res["added"] == 9 and sorted(res["failed_teams"]) == ["BOS", "TOR"]
    assert len(mem.csvs[C.PLAYERS_MASTER]) == 9
    assert players.refresh_players(C_(), abbrevs=["EDM"], retry_pause=0)["added"] == 0          # idempotent


def test_resolve_with_team_hint_in_any_vocabulary():
    rows = [{"player_id": 1, "full_name": "Jack Hughes", "team": "NJD"},
            {"player_id": 2, "full_name": "Jack Hughes", "team": "VAN"},
            {"player_id": 3, "full_name": "Anze Kopitar", "team": "LAK"}]
    r = players.build_resolver(rows)
    assert players.resolve(r, "Jack Hughes", "New Jersey Devils")[0] == 1
    assert players.resolve(r, "Jack Hughes", "VAN")[0] == 2
    assert players.resolve(r, "Anze Kopitar", "LA")[0] == 3                      # Kalshi vocabulary 'LA' -> LAK
    assert players.resolve(r, "Jack Hughes") == (None, "ambiguous")


# ----------------------------------------------------------------------------- teams
def test_team_rows_have_logos_and_kalshi_codes():
    rows = teams.build_rows({"EDM": 22})
    e = next(r for r in rows if r["abbrev"] == "EDM")
    assert e["team_id"] == 22 and e["logo_dark"].endswith("EDM_dark.svg") and e["logo_light"].endswith("EDM_light.svg")
    assert next(r for r in rows if r["abbrev"] == "LAK")["kalshi_code"] == "LA"
    assert len(rows) == 32


def test_team_ids_learned_from_schedule_weeks_and_refresh_survives_failure(mem):
    class Sched:
        def __init__(self):
            self.calls = 0

        def schedule(self, day):
            self.calls += 1
            return {"gameWeek": [{"date": day, "games": [
                {"awayTeam": {"abbrev": "MTL", "id": 8}, "homeTeam": {"abbrev": "WSH", "id": 15}}]}]}
    c = Sched()
    ids = teams.team_ids_from_schedule(c, start=date(2026, 10, 14), weeks=3)
    assert ids == {"MTL": 8, "WSH": 15} and c.calls == 3

    class Boom:
        def schedule(self, day):
            raise RuntimeError("down")
    res = teams.refresh_teams(Boom())                                           # ids are optional: still writes teams
    assert res["teams"] == 32 and res["ids_known"] == 0 and len(mem.csvs[C.TEAMS_MASTER]) == 32


def test_failed_team_gets_a_second_pass_and_partial_is_reported(mem):
    calls = {"BOS": 0}

    class Flaky:
        def roster(self, a):
            if a == "BOS":
                calls["BOS"] += 1
                if calls["BOS"] == 1:
                    raise RuntimeError("429")
            return json.load(open(FIX)) if a in ("EDM", "BOS") else {}
    res = players.refresh_players(Flaky(), abbrevs=["EDM", "BOS"], retry_pause=0)
    assert res["failed_teams"] == [] and calls["BOS"] == 2                    # recovered on the retry pass

    class Dead:
        def roster(self, a):
            raise RuntimeError("down")
    assert players.refresh_players(Dead(), abbrevs=["EDM"], retry_pause=0)["failed_teams"] == ["EDM"]


def test_covered_teams_exposes_a_partial_crosswalk():
    rows = [{"player_id": 1, "full_name": "A B", "team": "NJD"}]
    assert players.covered_teams(rows) == {"NJD"} and "VAN" not in players.covered_teams(rows)


def test_client_honours_retry_after_on_429(monkeypatch):
    import requests
    from nhl.data import client as cl
    sleeps = []
    monkeypatch.setattr(cl.time, "sleep", lambda s: sleeps.append(s))

    class Resp:
        def __init__(self, code, headers=None):
            self.status_code, self.headers = code, headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

        def json(self):
            return {"ok": True}

    seq = [Resp(429, {"Retry-After": "7"}), Resp(200)]

    class Sess:
        headers = {}

        def get(self, url, timeout=0):
            return seq.pop(0)
    out = cl.NhlClient(session=Sess(), delay=0)._get("roster/EDM/current")
    assert out == {"ok": True} and sleeps == [7.0]
