"""ev.rosters: the active-roster layer (offline: injected fetchers / frames / clients, in-memory storage)."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ev import rosters as R

NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)


def _rows(n_teams=30, per=2, sport="nba"):
    codes = {"nba": ["ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM",
                     "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"]}
    out = []
    for t in codes[sport][:n_teams]:
        out += [{"name": f"{t} Guy {i}", "team": t} for i in range(per)]
    return out


@pytest.fixture
def mem_storage(monkeypatch):
    import mlb_core.storage as st
    store: dict = {}
    monkeypatch.setattr(st, "write_bytes", lambda data, key: store.__setitem__(key, data))
    monkeypatch.setattr(st, "read_bytes", lambda key: store[key])
    monkeypatch.setattr(st, "exists", lambda key: key in store)
    return store


# ------------------------------------------------------------------------------------------------ build + lookup
def test_build_roster_folds_names_canonicalises_teams_and_flags_namesakes():
    rows = [{"name": "Ja'Marr Chase Jr.", "team": "CIN"}, {"name": "Marcus Harris", "team": "LA"},
            {"name": "Marcus Harris", "team": "JAX"}, {"name": "", "team": "KC"}, {"name": "Nobody", "team": "XXXXXXX"}]
    r = R.build_roster("nfl", rows, [], "t", NOW)
    assert r.players == {"jamarr chase": "CIN"}                                  # same fold as the game-log index
    assert r.ambiguous == {"marcus harris": ["JAC", "LAR"]}                      # two active teams: never a guessed one
    assert r.partial is True and r.teams == ["CIN", "JAC", "LAR"]                # < MIN_TEAMS covered = partial


def test_lookup_on_off_unknown():
    rows = _rows()
    full = R.build_roster("nba", rows + [{"name": "Twin Name", "team": "LAL"}, {"name": "Twin Name", "team": "BOS"}], [], "t", NOW)
    assert full.partial is False
    assert full.lookup("lal guy 0") == ("on", "LAL")
    assert full.lookup("retired legend") == ("off", None)                         # complete roster, not on it
    assert full.lookup("twin name", "BOS") == ("on", "BOS")                        # the game log picks between candidates
    assert full.lookup("twin name", "MIA") == ("unknown", None) and full.lookup("twin name") == ("unknown", None)
    partial = R.build_roster("nba", rows, ["MIA"], "t", NOW)
    assert partial.partial is True and partial.failed == ["MIA"]
    assert partial.lookup("retired legend") == ("unknown", None)                  # absence proves nothing on a partial pull


def test_json_roundtrip():
    r = R.build_roster("nba", _rows(), [], "src", NOW)
    r2 = R.Roster.from_json(json.loads(json.dumps(r.to_json())))
    assert r2.players == r.players and r2.teams == r.teams and r2.source == "src" and r2.partial is False


# ------------------------------------------------------------------------------------------------ sources
def test_nfl_rows_keep_only_the_newest_row_when_it_is_active():
    df = pd.DataFrame([
        {"gsis_id": "1", "full_name": "Active Guy", "team": "KC", "status": "ACT", "week": 6},
        {"gsis_id": "2", "full_name": "Cut Guy", "team": "KC", "status": "ACT", "week": 3},       # was active ...
        {"gsis_id": "2", "full_name": "Cut Guy", "team": "KC", "status": "CUT", "week": 5},       # ... then cut
        {"gsis_id": "3", "full_name": "Traded Guy", "team": "BUF", "status": "ACT", "week": 2},
        {"gsis_id": "3", "full_name": "Traded Guy", "team": "KC", "status": "ACT", "week": 6},    # newest row = new team
        {"gsis_id": "4", "full_name": "IR Guy", "team": "KC", "status": "RES", "week": 6},
        {"gsis_id": "5", "full_name": "Practice Squad", "team": "KC", "status": "DEV", "week": 6},
        {"gsis_id": "6", "full_name": "Retired Guy", "team": "KC", "status": "RET", "week": 6},
        {"gsis_id": "7", "full_name": "Gameday Inactive", "team": "JAX", "status": "INA", "week": 6},
    ])
    rows = R.nfl_rows_from_frame(df)
    assert {(r["name"], r["team"]) for r in rows} == {("Active Guy", "KC"), ("Traded Guy", "KC"),
                                                      ("Gameday Inactive", "JAC")}
    assert R.nfl_rows_from_frame(pd.DataFrame()) == []


def test_nba_xwalk_ignores_rows_the_daily_run_did_not_restamp():
    run = NOW - timedelta(hours=5)
    rows = [{"full_name": f"P{i}", "team": t, "updated_at": run.isoformat()}
            for i, t in enumerate(x["team"] for x in _rows(30, 1))]
    retired = {"full_name": "Retired Vet", "team": "LAL", "updated_at": (NOW - timedelta(days=40)).isoformat()}
    out = R.nba_rows_from_xwalk(pd.DataFrame(rows + [retired]), NOW)
    assert out is not None and len(out) == 30 and "Retired Vet" not in {r["name"] for r in out}
    old = [{**r, "updated_at": (NOW - timedelta(hours=60)).isoformat()} for r in rows]
    assert R.nba_rows_from_xwalk(pd.DataFrame(old), NOW) is None                  # the daily job missed: do not trust it
    assert R.nba_rows_from_xwalk(pd.DataFrame(rows[:10]), NOW) is None            # too few teams = a partial run
    assert R.nba_rows_from_xwalk(pd.DataFrame(), NOW) is None


def test_fetch_nhl_retries_failed_teams_once_and_reports_leftovers(monkeypatch):
    from nhl.config import TEAMS
    calls = {}

    class Client:
        def roster(self, abbrev):
            calls[abbrev] = calls.get(abbrev, 0) + 1
            if abbrev == "TBL" and calls[abbrev] == 1:
                raise RuntimeError("429")
            if abbrev == "UTA":
                return None                                                         # never loads
            return {"forwards": [{"id": 1, "firstName": {"default": "Test"}, "lastName": {"default": f"{abbrev} Skater"},
                                  "positionCode": "C"}], "defensemen": [], "goalies": []}
    res = R.fetch_nhl(Client(), retry_pause=0)
    assert calls["TBL"] == 2 and res["failed"] == ["UTA"] and len(res["rows"]) == len(TEAMS) - 1
    assert {"name": "Test TBL Skater", "team": "TBL"} in res["rows"]


# ------------------------------------------------------------------------------------------------ load / cache
def test_load_uses_a_fresh_cache_and_refetches_a_stale_one(mem_storage):
    fetches = []

    def fetch(now):
        fetches.append(now)
        return {"rows": _rows(), "failed": [], "source": "fake"}
    r1 = R.load("nba", NOW, fetch=fetch)
    assert r1.source == "fake" and len(fetches) == 1 and "Rosters/nba.json" in mem_storage
    r2 = R.load("nba", NOW + timedelta(hours=1), fetch=fetch)
    assert len(fetches) == 1 and r2.players == r1.players                          # fresh cache: no network
    R.load("nba", NOW + timedelta(hours=R.TTL_HOURS + 1), fetch=fetch)
    assert len(fetches) == 2
    R.load("nba", NOW + timedelta(hours=R.TTL_HOURS + 1), fetch=lambda n: pytest.fail("cache-only"), refresh=False)


def test_failed_refresh_falls_back_to_the_last_good_roster_then_to_none(mem_storage):
    R.load("nba", NOW, fetch=lambda n: {"rows": _rows(), "failed": [], "source": "fake"})

    def down(now):
        raise RuntimeError("ESPN down")
    later = NOW + timedelta(days=2)
    r = R.load("nba", later, fetch=down)
    assert r is not None and r.stale is True and r.lookup("atl guy 0") == ("on", "ATL")   # last good copy, flagged
    assert R.load("nba", NOW + timedelta(days=R.MAX_STALE_DAYS + 1), fetch=down) is None   # too old: no roster layer
    mem_storage.clear()
    assert R.load("nba", NOW, fetch=down) is None                                           # nothing ever fetched


def test_a_pull_covering_too_few_teams_is_a_failure_not_a_roster(mem_storage):
    R.load("nba", NOW, fetch=lambda n: {"rows": _rows(), "failed": [], "source": "good"})
    thin = lambda n: {"rows": _rows(12), "failed": [], "source": "thin"}                     # noqa: E731
    r = R.load("nba", NOW + timedelta(hours=8), fetch=thin)
    assert r.source == "good" and r.stale is True                                            # the good cache, not the thin pull
    assert json.loads(mem_storage["Rosters/nba.json"])["source"] == "good"                   # and it was not overwritten


def test_partial_pull_is_accepted_but_refreshed_sooner_and_never_excludes(mem_storage):
    fetches = []

    def partial(now):
        fetches.append(now)
        return {"rows": _rows(29), "failed": ["UTA"], "source": "p"}
    r = R.load("nba", NOW, fetch=partial)
    assert r.partial and r.lookup("nobody") == ("unknown", None)
    R.load("nba", NOW + timedelta(minutes=10), fetch=partial)
    assert len(fetches) == 1
    R.load("nba", NOW + timedelta(minutes=45), fetch=partial)
    assert len(fetches) == 2                                                                  # PARTIAL_TTL_HOURS = 0.5


def test_cache_write_failure_does_not_lose_the_roster(monkeypatch):
    import mlb_core.storage as st
    monkeypatch.setattr(st, "read_bytes", lambda key: (_ for _ in ()).throw(FileNotFoundError(key)))
    monkeypatch.setattr(st, "write_bytes", lambda data, key: (_ for _ in ()).throw(PermissionError("403")))
    r = R.load("nba", NOW, fetch=lambda n: {"rows": _rows(), "failed": [], "source": "fake"})
    assert r is not None and r.lookup("atl guy 0") == ("on", "ATL")


def test_dry_run_never_writes_the_cache(mem_storage):
    r = R.load("nba", NOW, fetch=lambda n: {"rows": _rows(), "failed": [], "source": "fake"}, write=False)
    assert r is not None and mem_storage == {}
