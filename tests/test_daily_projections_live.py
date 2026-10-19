"""ev.daily_projections, 2026-10-17 live-lines work: the two-source line union, the roster layer's effect on teams, the
day-first row order, the games list, the Central-day archive and the `date` / `day` API params. Offline throughout."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import mlb.runners.public_api as pa
from ev import daily_projections as dp
from ev import rosters as R
from mlb_core.data import cockpit_form as cf
from tests.test_daily_projections import FAIR, NOW, START, book, event, nba_logs, nba_rows

REAL_WRITE_ARCHIVE = dp.write_archive


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(dp, "write_archive", lambda payload, now: [])          # tests of the archive call REAL_WRITE_ARCHIVE
    dp._ARCHIVE_CACHE.clear()
    yield
    dp._ARCHIVE_CACHE.clear()


@pytest.fixture
def mem_storage(monkeypatch):
    import mlb_core.storage as st
    store: dict = {}
    monkeypatch.setattr(st, "write_bytes", lambda data, key: store.__setitem__(key, data))
    monkeypatch.setattr(st, "read_bytes", lambda key: store[key])
    monkeypatch.setattr(st, "exists", lambda key: key in store)
    return store


def roster_of(mapping: dict, partial=False, ambiguous=None) -> R.Roster:
    return R.Roster(sport="nba", fetched_at=NOW.isoformat(), source="test", players=dict(mapping),
                    ambiguous=dict(ambiguous or {}), teams=sorted(set(mapping.values())), partial=partial)


# ------------------------------------------------------------------------------------------------ constants
def test_horizons_reach_nba_opening_week_and_rotation_is_bounded():
    assert dp.HORIZON_HOURS == {"nfl": 168.0, "nhl": 72.0, "nba": 168.0}
    assert dp.MAX_ROWS >= 2000
    per_game_nba = len(dp.ROTATION_STATS["nba"]) * dp.ROTATION_PER_TEAM["nba"] * 2
    assert per_game_nba <= 80                                           # a night of 12 games stays under ~1,000 filler rows


# ------------------------------------------------------------------------------------------------ line union
def pev(books, start=START, away="Boston Celtics", home="Los Angeles Lakers", eid="p1"):
    return {**event(books, start=start, away=away, home=home), "id": eid}


def bev(books, start=START, away="BOS", home="LAL", eid="bp9"):
    return {"id": eid, "source": "bettingpros", "commence_time": start.isoformat().replace("+00:00", "Z"),
            "away_team": away, "home_team": home, "bookmakers": books}


def _books_by_stat(ev, sport="nba"):
    from ev.props import parse_event_props
    qs, _ = parse_event_props(ev, sport)
    out = {}
    for q in qs:
        out.setdefault((q.stat, q.player_norm), []).append(q.book)
    return {k: sorted(v) for k, v in out.items()}


def test_union_matches_the_same_game_across_spellings_and_never_doubles_a_book():
    parlay = ([pev([book("draftkings", -110, -110), book("fanduel", -112, -108)])], {"age_min": 90.0, "banked_events": 1})
    bp = ([bev([book("draftkings", -105, -115), book("novig", -108, -108), book("fanduel", -120, 100)])],
          {"age_min": 20.0, "events": 1})
    events, meta = dp.union_events("nba", parlay, bp)
    assert len(events) == 1 and meta["merge"]["games_matched"] == 1
    assert meta["lines_source"] == ["parlayapi", "bettingpros"] and meta["age_min"] == 20.0     # the NEWEST feed's age
    books = _books_by_stat(events[0])[("points", "jo doe")]
    assert books == ["draftkings", "fanduel", "novig"]                                         # novig added, none twice
    # BettingPros is strictly fresher here (20 < 90 min): its draftkings / fanduel prices win the shared (book, stat)
    from ev.props import parse_event_props
    qs, _ = parse_event_props(events[0], "nba")
    assert {q.book: q.over for q in qs} == {"draftkings": -105, "fanduel": -120, "novig": -108}
    assert meta["merge"]["book_stats_from_bp"] == 2 and meta["merge"]["books_added"] == 1


def test_a_tie_or_unknown_age_goes_to_parlayapi():
    parlay = ([pev([book("draftkings", -110, -110)])], {"age_min": 30.0})
    bp = ([bev([book("draftkings", -105, -115)])], {"age_min": 30.0})
    from ev.props import parse_event_props
    qs, _ = parse_event_props(dp.union_events("nba", parlay, bp)[0][0], "nba")
    assert [(q.book, q.over) for q in qs] == [("draftkings", -110)]
    qs, _ = parse_event_props(dp.union_events("nba", ([pev([book("draftkings", -110, -110)])], {"age_min": None}), bp)[0][0], "nba")
    assert [(q.book, q.over) for q in qs] == [("draftkings", -110)]


def test_union_fills_missing_stats_of_a_shared_book_and_adds_unmatched_games_whole():
    other_start = START + timedelta(days=1)
    parlay = ([pev([book("draftkings", -110, -110)]), pev([book("fanduel", -110, -110)], start=START + timedelta(days=2),
                                                         away="Miami Heat", home="Denver Nuggets", eid="p2")], {"age_min": 50.0})
    bp = ([bev([book("draftkings", -105, -115, player="Ann Rebound", mkey="player_rebounds")]),
           bev([book("novig", -110, -110)], start=other_start, away="NYK", home="BKN", eid="bp3")], {"age_min": 10.0})
    events, meta = dp.union_events("nba", parlay, bp)
    assert len(events) == 3 and meta["merge"] == {
        "games_matched": 1, "games_bp_only": 1, "games_parlay_only": 1, "books_added": 0, "book_stats_added": 1,
        "book_stats_from_bp": 0, "book_stats_from_parlay": 0}
    merged = _books_by_stat(events[0])
    assert merged[("points", "jo doe")] == ["draftkings"] and merged[("rebounds", "ann rebound")] == ["draftkings"]
    assert events[1]["id"] == "p2" and events[2]["id"] == "bp9" or events[2]["id"] == "bp3"


def test_one_source_down_degrades_to_the_other_both_ways():
    good = ([pev(FAIR)], {"age_min": 30.0, "banked_events": 1})
    down = (None, {"reason": "no latest_props.json pointer yet"})
    e, m = dp.union_events("nba", good, down)
    assert e == good[0] and m["lines_source"] == ["parlayapi"] and m["age_min"] == 30.0
    assert m["sources"]["bettingpros"] == {"reason": "no latest_props.json pointer yet", "used": False, "events": 0}
    e, m = dp.union_events("nba", down, ([bev(FAIR)], {"age_min": 12.0, "events": 1}))
    assert e[0]["source"] == "bettingpros" and m["lines_source"] == ["bettingpros"] and m["age_min"] == 12.0
    e, m = dp.union_events("nba", down, (None, {"reason": "x"}))
    assert e is None and m["lines_source"] == [] and "parlayapi: no latest_props" in m["reason"] and "bettingpros: x" in m["reason"]


def test_a_stale_carry_feed_is_dropped_while_a_live_one_exists():
    old = ([pev([book("draftkings", -110, -110)])], {"age_min": 700.0})                       # carry window only
    live = ([bev([book("fanduel", -110, -110)])], {"age_min": 15.0})
    e, m = dp.union_events("nba", old, live)
    assert m["lines_source"] == ["bettingpros"] and m["age_min"] == 15.0 and e == live[0]       # no stale dilution
    assert m["sources"]["parlayapi"]["used"] is False
    e, m = dp.union_events("nba", old, ([bev(FAIR)], {"age_min": 800.0}))
    assert m["lines_source"] == ["parlayapi", "bettingpros"] and m["age_min"] == 700.0           # all stale: newest age, both used


def test_load_events_guards_each_feed(monkeypatch):
    monkeypatch.setattr(dp, "load_parlay_events", lambda s, n, m=0: (_ for _ in ()).throw(RuntimeError("gcs 503")))
    monkeypatch.setattr(dp, "load_bp_events", lambda s, n, m=0: ([bev(FAIR)], {"age_min": 9.0}))
    e, m = dp.load_events("nba", NOW, dp.CARRY_MAX_AGE_MIN)
    assert m["lines_source"] == ["bettingpros"] and "gcs 503" in m["sources"]["parlayapi"]["reason"]
    monkeypatch.setattr(dp, "load_parlay_events", lambda s, n, m=0: ([pev(FAIR)], {"age_min": 40.0}))
    monkeypatch.setattr(dp, "load_bp_events", lambda s, n, m=0: (_ for _ in ()).throw(ValueError("bad json")))
    e, m = dp.load_events("nba", NOW, dp.CARRY_MAX_AGE_MIN)
    assert m["lines_source"] == ["parlayapi"] and m["age_min"] == 40.0


def test_run_sport_reports_sources_and_freshness_of_the_newest_feed(monkeypatch):
    monkeypatch.setattr(dp, "load_parlay_events", lambda s, n, m=0: ([pev(FAIR)], {"age_min": 600.0}))
    monkeypatch.setattr(dp, "load_bp_events", lambda s, n, m=0: ([bev(FAIR)], {"age_min": 20.0}))
    out = dp.run_sport("nba", NOW, logs=nba_logs(), games=[], write=False, roster=None)
    assert out["lines_source"] == ["bettingpros"] and out["lines_age_minutes"] == 20.0
    assert out["lines_stale"] is False and out["status"] == "live"                  # BettingPros freshness, not the 600-min file
    assert out["summary"]["sources"]["parlayapi"]["used"] is False
    monkeypatch.setattr(dp, "load_bp_events", lambda s, n, m=0: (None, {"reason": "outage"}))
    out = dp.run_sport("nba", NOW, logs=nba_logs(), games=[], write=False, roster=None)
    assert out["lines_source"] == ["parlayapi"] and out["lines_stale"] is True and out["lines_age_minutes"] == 600.0
    out = dp.run_sport("nba", NOW, logs=nba_logs(), events=[event(FAIR)], meta={"age_min": 12.0}, games=[], write=False,
                       roster=None)
    assert out["lines_source"] == ["parlayapi"]                                         # injected events keep the old label


# ------------------------------------------------------------------------------------------------ roster: line rows
def _moved_logs():
    """Jo Doe's last game-log row says LAL, but he has been traded to MIA; a retired Vet's says BOS."""
    return nba_logs({cf.norm_name("Mia Mover"): nba_rows("Mia Mover", "LAL", [21.5, 29.5] * 10),
                     cf.norm_name("Old Vet"): nba_rows("Old Vet", "BOS", [14.5, 16.5] * 10, opp="LAL")})


def _event_for(player, away="Miami Heat", home="Denver Nuggets"):
    return event([book(b, -110, -110, player=player) for b in ("draftkings", "fanduel", "betmgm")], away=away, home=home)


def test_roster_recovers_a_traded_player_the_game_log_would_drop():
    ev = _event_for("Mia Mover")                                       # quoted in MIA @ DEN; the log still says LAL
    rows, st = dp.build_line_rows("nba", [ev], _moved_logs(), NOW)
    assert rows == [] and st["ambiguous_team"] == 1                    # the old behaviour: dropped
    roster = roster_of({"mia mover": "MIA"})
    rows, st = dp.build_line_rows("nba", [ev], _moved_logs(), NOW, roster)
    assert [(r["player"], r["team"], r["opp"]) for r in rows] == [("Mia Mover", "MIA", "DEN")]
    assert st["ambiguous_team"] == 0 and st["roster_recovered"] == 1 and st["roster_team_changed"] == 1
    assert rows[0]["game_key"] == dp.game_key_of("nba", START, "MIA", "DEN")


def test_a_quoted_player_on_no_roster_keeps_the_old_behaviour():
    roster = roster_of({"someone else": "MIA"})
    ok, st = dp.build_line_rows("nba", [event(FAIR)], nba_logs(), NOW, roster)      # Jo Doe: log LAL, game BOS@LAL
    assert [r["player"] for r in ok if r["stat"] == "points"] == ["Jo Doe"] and st["roster_recovered"] == 0
    other = _event_for("Mia Mover")
    rows, st = dp.build_line_rows("nba", [other], _moved_logs(), NOW, roster)        # no roster entry, log team not in game
    assert rows == [] and st["ambiguous_team"] == 1


def test_a_roster_team_that_is_not_in_the_quoted_game_does_not_beat_the_game_log():
    roster = roster_of({"jo doe": "MIA"})                              # stale roster: the quote proves he is in BOS @ LAL
    rows, st = dp.build_line_rows("nba", [event(FAIR)], nba_logs(), NOW, roster)
    r = next(x for x in rows if x["player"] == "Jo Doe")
    assert r["team"] == "LAL" and r["opp"] == "BOS" and st["roster_recovered"] == 0


def test_roster_layer_absent_is_exactly_the_old_behaviour():
    a, sa = dp.build_line_rows("nba", [event(FAIR)], nba_logs(), NOW)
    b, sb = dp.build_line_rows("nba", [event(FAIR)], nba_logs(), NOW, None)
    assert a == b and sa == sb


# ------------------------------------------------------------------------------------------------ roster: rotation
GAMES = [{"away": "BOS", "home": "LAL", "start": START}, {"away": "MIA", "home": "DEN", "start": START + timedelta(hours=3)}]


def test_retired_player_is_not_a_rotation_row_and_is_counted():
    """The Kopitar case: his last game-log row is a team that plays soon, so the old code listed him."""
    logs = _moved_logs()
    old, _ = dp.rotation_rows("nba", logs, GAMES, NOW, set())
    vet_stats = {r["stat"] for r in old if r["player"] == "Old Vet"}
    assert vet_stats                                                   # listed (once per stat he clears) before the roster
    roster = roster_of({"jo doe": "LAL", "max celtic": "BOS", "sam bench": "LAL", "mia mover": "MIA"})
    new, st = dp.rotation_rows("nba", logs, GAMES, NOW, set(), roster)
    assert "Old Vet" not in {r["player"] for r in new} and st["rotation_off_roster"] == len(vet_stats)
    assert {"Jo Doe", "Max Celtic"} <= {r["player"] for r in new}


def test_traded_player_is_projected_against_the_roster_team_and_its_game():
    logs = _moved_logs()
    roster = roster_of({"jo doe": "LAL", "max celtic": "BOS", "mia mover": "MIA"})
    old, _ = dp.rotation_rows("nba", logs, GAMES, NOW, set())
    assert {r["team"] for r in old if r["player"] == "Mia Mover"} == {"LAL"}          # stale log team
    new, st = dp.rotation_rows("nba", logs, GAMES, NOW, set(), roster)
    mm = [r for r in new if r["player"] == "Mia Mover"]
    assert mm and {r["team"] for r in mm} == {"MIA"} and {r["opp"] for r in mm} == {"DEN"}
    assert {r["game_key"] for r in mm} == {dp.game_key_of("nba", GAMES[1]["start"], "MIA", "DEN")}
    assert {r["commence"] for r in mm} == {GAMES[1]["start"].isoformat()}               # the roster team's game time
    assert st["rotation_team_changed"] == len(mm)


def test_partial_roster_does_not_exclude_players_it_cannot_judge():
    logs = _moved_logs()
    partial = roster_of({"jo doe": "LAL"}, partial=True)
    rows, st = dp.rotation_rows("nba", logs, GAMES, NOW, set(), partial)
    assert "Old Vet" in {r["player"] for r in rows} and st["rotation_off_roster"] == 0


def test_rotation_quota_counts_priced_players_and_is_per_sport():
    rows = {}
    for i in range(8):                                                 # eight LAL scorers, all above the usage bar
        n = f"Laker {i}"
        rows[cf.norm_name(n)] = nba_rows(n, "LAL", [20.0 + i] * 20)
    logs = dp.GameLogs("nba", {"all": rows})
    games = [{"away": "BOS", "home": "LAL", "start": START}]
    q = dp.ROTATION_PER_TEAM["nba"]
    free, _ = dp.rotation_rows("nba", logs, games, NOW, set())
    assert len([r for r in free if r["stat"] == "points"]) == q
    some, _ = dp.rotation_rows("nba", logs, games, NOW, set(), None, {("LAL", "points"): q - 1})
    assert len([r for r in some if r["stat"] == "points"]) == 1                         # only the unfilled slot
    none, _ = dp.rotation_rows("nba", logs, games, NOW, set(), None, {("LAL", "points"): q + 3})
    assert not [r for r in none if r["stat"] == "points"]                                # covered team: no filler


# ------------------------------------------------------------------------------------------------ order / games
def _row(day_offset, has_line, score=0, player="P", stat="points", hour=18):
    start = datetime(2026, 10, 17 + day_offset, hour, 0, tzinfo=timezone.utc)
    return {"commence": start.isoformat(), "has_line": has_line, "confluence": False, "beezy_score": score,
            "model_edge": None, "n_books": 3, "player": player, "player_norm": player.lower(), "stat": stat,
            "game_key": f"nba:2026-10-{17 + day_offset}:A@B"}


def test_order_and_cut_drops_the_farthest_games_never_a_near_priced_row():
    rows = [_row(5, True, 90, "FarPriced"), _row(0, False, 0, "NearRotation"), _row(0, True, 10, "NearPriced"),
            _row(5, False, 0, "FarRotation"), _row(1, False, 0, "NextRotation")]
    kept, cut = dp.order_and_cut(rows, limit=3)
    assert [r["player"] for r in kept] == ["NearPriced", "NearRotation", "NextRotation"] and cut == 2
    kept, cut = dp.order_and_cut(rows, limit=10)
    assert [r["player"] for r in kept] == ["NearPriced", "NearRotation", "NextRotation", "FarPriced", "FarRotation"] and cut == 0
    # the day is the CENTRAL day: 02:00 UTC on the 18th is still the evening of the 17th at the site
    late = _row(1, True, 1, "LateTipoff", hour=2)
    assert dp.ct_day(late["commence"]) == "2026-10-17"
    same_day = dp.order_and_cut([_row(0, False, 0, "A"), late], 2)[0]
    assert [r["player"] for r in same_day] == ["LateTipoff", "A"]       # one Central day: priced first


def test_build_games_unions_schedule_and_line_only_games():
    sched = [{"away": "BOS", "home": "LAL", "start": START}, {"away": "MIA", "home": "DEN", "start": START + timedelta(hours=2)},
             {"away": "OLD", "home": "GME", "start": NOW - timedelta(hours=1)}]
    lines_only = event(FAIR, start=START + timedelta(days=3), away="New York Knicks", home="Brooklyn Nets")
    same = event(FAIR, start=START + timedelta(minutes=5), away="Boston Celtics", home="Los Angeles Lakers")
    games = dp.build_games("nba", sched, [lines_only, same], NOW)
    assert [g["game_key"] for g in games] == [
        dp.game_key_of("nba", START, "BOS", "LAL"), dp.game_key_of("nba", START + timedelta(hours=2), "MIA", "DEN"),
        dp.game_key_of("nba", START + timedelta(days=3), "NYK", "BKN")]
    assert games[0]["start"] == (START + timedelta(minutes=5)).astimezone(timezone.utc).isoformat()   # the event's time wins
    assert set(games[0]) == {"game_key", "away", "home", "start"}
    assert dp.build_games("nba", [], None, NOW) == []


def test_run_sport_payload_has_games_with_row_counts_and_day_first_order():
    logs = nba_logs()
    tomorrow = START + timedelta(days=1)
    games = [{"away": "BOS", "home": "LAL", "start": START}, {"away": "BOS", "home": "LAL", "start": tomorrow}]
    out = dp.run_sport("nba", NOW, logs=logs, events=[event(FAIR), event(FAIR, start=tomorrow)],
                       meta={"age_min": 5.0}, games=games, write=False, roster=None)
    assert [g["game_key"] for g in out["games"]] == [dp.game_key_of("nba", START, "BOS", "LAL"),
                                                     dp.game_key_of("nba", tomorrow, "BOS", "LAL")]
    for g in out["games"]:
        assert g["n_rows"] >= g["n_priced"] >= 1
        assert sum(1 for r in out["rows"] if r["game_key"] == g["game_key"]) == g["n_rows"]
    days = [dp.ct_day(r["commence"]) for r in out["rows"]]
    assert days == sorted(days)                                          # nearest game day first
    first_day = [r for r in out["rows"] if dp.ct_day(r["commence"]) == days[0]]
    assert [r["has_line"] for r in first_day] == sorted((r["has_line"] for r in first_day), reverse=True)
    assert out["summary"]["rows_cut"] == 0 and out["summary"]["games"] == 2 and out["horizon_hours"] == 168.0


# ------------------------------------------------------------------------------------------------ archive
def test_archive_is_keyed_by_the_central_start_day_and_keeps_rows_after_the_game_starts(mem_storage):
    late = datetime(2026, 10, 18, 4, 30, tzinfo=timezone.utc)            # 23:30 CT on the 17th, 00:30 ET on the 18th
    early = datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)           # 18:00 CT on the 18th
    r_late = {**_row(0, True, 50, "Late"), "commence": late.isoformat(), "game_key": "nba:2026-10-18:A@B"}   # ET-dated key
    r_early = {**_row(1, True, 40, "Early"), "commence": early.isoformat(), "game_key": "nba:2026-10-18:C@D"}
    g_late = {"game_key": "nba:2026-10-18:A@B", "away": "A", "home": "B", "start": late.isoformat()}
    g_early = {"game_key": "nba:2026-10-18:C@D", "away": "C", "home": "D", "start": early.isoformat()}
    payload = {"sport": "nba", "engine": dp.ENGINE, "stats": ["points"], "rows": [r_late, r_early], "games": [g_late, g_early],
               "lines_source": ["bettingpros"], "lines_age_minutes": 7.0, "gamelog_latest": "2026-06-13"}
    days = REAL_WRITE_ARCHIVE(payload, NOW)
    assert days == ["2026-10-17", "2026-10-18"]                           # CT dates of the starts, not the game_key's ET date
    a17 = json.loads(mem_storage["EVSweep/nba_props/archive/2026-10-17.json"])
    assert list(a17["rows"]) == ["nba:2026-10-18:A@B|late|points"] and list(a17["games"]) == ["nba:2026-10-18:A@B"]
    # the NEXT compute no longer carries the late game's rows (it started): the archive keeps them
    payload2 = {**payload, "rows": [r_early], "games": [g_early]}
    REAL_WRITE_ARCHIVE(payload2, NOW + timedelta(minutes=30))
    a17 = json.loads(mem_storage["EVSweep/nba_props/archive/2026-10-17.json"])
    assert "nba:2026-10-18:A@B|late|points" in a17["rows"]


def test_archive_row_replacement_rules():
    old = {**_row(0, True, 10, "Ann"), "line": 20.5}
    new = {**old, "line": 21.5, "beezy_score": 30}
    arch = dp.merge_archive(None, [old], [], {"sport": "nba"}, "2026-10-17", NOW)
    arch = dp.merge_archive(arch, [new], [], {"sport": "nba"}, "2026-10-17", NOW)
    assert arch["rows"][dp.row_key(old)]["line"] == 21.5                   # the newer version replaces the older
    rotation = {**old, "has_line": False, "line": None}
    arch = dp.merge_archive(arch, [rotation], [], {"sport": "nba"}, "2026-10-17", NOW)
    assert arch["rows"][dp.row_key(old)]["line"] == 21.5                   # a pulled prop never erases the posted price
    unpriced = {**_row(0, False, 0, "Bob")}
    arch = dp.merge_archive(arch, [unpriced], [], {"sport": "nba"}, "2026-10-17", NOW)
    priced_bob = {**unpriced, "has_line": True, "line": 5.5}
    arch = dp.merge_archive(arch, [priced_bob], [], {"sport": "nba"}, "2026-10-17", NOW)
    assert arch["rows"][dp.row_key(unpriced)]["line"] == 5.5               # a rotation row IS replaced by a priced one
    g1 = {"game_key": "g", "away": "A", "home": "B", "start": "s1"}
    arch = dp.merge_archive(arch, [], [g1, {**g1, "start": "s2"}], {"sport": "nba"}, "2026-10-17", NOW)
    assert arch["games"]["g"]["start"] == "s2"


def test_archive_writes_are_best_effort(monkeypatch):
    import mlb_core.storage as st

    def boom(*a, **k):
        raise PermissionError("403")
    monkeypatch.setattr(st, "read_bytes", boom)
    monkeypatch.setattr(st, "exists", boom)
    monkeypatch.setattr(st, "write_bytes", boom)
    payload = {"sport": "nba", "rows": [_row(0, True, 1, "A")], "games": []}
    assert REAL_WRITE_ARCHIVE(payload, NOW) == []                          # no exception
    monkeypatch.setattr(dp, "write_archive", REAL_WRITE_ARCHIVE)           # the real archive writer, storage still failing
    out = dp.run_sport("nba", NOW, logs=nba_logs(), events=[event(FAIR)], meta={}, games=[], write=True, roster=None)
    assert out["rows"]                                                       # the compute itself did not fail


def test_run_sport_merges_the_payload_into_the_archive(monkeypatch, mem_storage):
    monkeypatch.setattr(dp, "write_archive", REAL_WRITE_ARCHIVE)
    out = dp.run_sport("nba", NOW, logs=nba_logs(), events=[event(FAIR)], meta={}, games=[], write=True, roster=None)
    day = dp.ct_day(START.isoformat())
    assert f"EVSweep/nba_props/archive/{day}.json" in mem_storage and "EVSweep/nba_props/projections_latest.json" in mem_storage
    got = dp.get_archive("nba", day)
    assert got["archive"] is True and got["count"] == len([r for r in out["rows"] if dp.ct_day(r["commence"]) == day])
    assert got["status"] == "live" and got["date"] == day and got["games"][0]["n_rows"] >= 1
    assert dp.get_archive("nba", "2020-01-01") is None


# ------------------------------------------------------------------------------------------------ public API
def _stored_board():
    rows = [{**_row(0, True, 9, "Today", "points"), "stat": "points"}, {**_row(2, False, 0, "Later", "rebounds"), "stat": "rebounds"}]
    return {"sport": "nba", "engine": dp.ENGINE, "validated": True, "ts": datetime.now(timezone.utc).isoformat(),
            "rows": rows, "status": "live", "stats": ["points"], "summary": {}, "gamelog_latest": "x", "stale_gamelog": False,
            "lines_age_minutes": 5.0, "lines_stale": False, "lines_source": ["parlayapi", "bettingpros"],
            "games": [{"game_key": rows[0]["game_key"], "away": "A", "home": "B", "start": rows[0]["commence"]},
                      {"game_key": rows[1]["game_key"], "away": "A", "home": "B", "start": rows[1]["commence"]}]}


def test_public_projections_carries_games_sources_and_day_filter(monkeypatch):
    dp._PROJ_CACHE.clear()
    monkeypatch.setattr(dp, "read_precomputed", lambda sp: _stored_board())
    out = pa.get_sport_projections("nba")
    assert out["archive"] is False and out["lines_source"] == ["parlayapi", "bettingpros"] and out["lines_stale"] is False
    assert len(out["games"]) == 2 and out["count"] == 2
    day = dp.ct_day(_stored_board()["rows"][0]["commence"])
    one = pa.get_sport_projections("nba", day=day)
    assert [r["player"] for r in one["rows"]] == ["Today"] and len(one["games"]) == 1 and one["day"] == day
    assert pa.get_sport_projections("nba", market="reb")["rows"][0]["player"] == "Later"
    dp._PROJ_CACHE.clear()


def test_public_projections_date_param_serves_the_archive_or_unavailable(monkeypatch, mem_storage):
    monkeypatch.setattr(dp, "get_daily_projections", lambda *a, **k: pytest.fail("archive requests never touch the live board"))
    arch = dp.merge_archive(None, [_row(0, True, 5, "Ann"), _row(0, False, 0, "Bob", "rebounds")],
                            [{"game_key": "nba:2026-10-17:A@B", "away": "A", "home": "B",
                              "start": _row(0, True)["commence"]}], {"sport": "nba", "engine": dp.ENGINE, "stats": ["points"]},
                            "2026-10-17", NOW)
    mem_storage["EVSweep/nba_props/archive/2026-10-17.json"] = json.dumps(arch).encode()
    out = pa.get_sport_projections("NBA", date="2026-10-17")
    assert out["archive"] is True and out["date"] == "2026-10-17" and out["count"] == 2 and out["status"] == "live"
    assert out["stale"] is False and out["validated"] is False and out["games"][0]["n_rows"] == 2
    assert [r["player"] for r in pa.get_sport_projections("nba", date="2026-10-17", market="reb")["rows"]] == ["Bob"]
    gone = pa.get_sport_projections("nba", date="2026-10-16")
    assert gone["status"] == "unavailable" and gone["rows"] == [] and gone["archive"] is True and gone["date"] == "2026-10-16"
    for bad in ("2026-13-45", "yesterday", "10/17/2026"):
        with pytest.raises(ValueError):
            pa.get_sport_projections("nba", date=bad)
    with pytest.raises(ValueError):
        pa.get_sport_projections("nba", day="2026-1-1")


def test_route_passes_date_and_day_only_when_given(monkeypatch):
    import main
    from unittest.mock import patch
    monkeypatch.setattr(main, "_auth_required", lambda req: None)
    client = main.app.test_client()
    with patch("main.get_sport_projections", return_value={"sport": "nba", "rows": []}) as g:
        client.get("/api/public/projections?sport=nba&date=2026-10-16&day=2026-10-17")
        client.get("/api/public/projections?sport=nba")
    assert g.call_args_list[0].kwargs == {"market": None, "limit": 300, "date": "2026-10-16", "day": "2026-10-17"}
    assert g.call_args_list[1].kwargs == {"market": None, "limit": 300}
    with patch("main.get_sport_projections", side_effect=ValueError("date must be YYYY-MM-DD")):
        assert client.get("/api/public/projections?sport=nba&date=bad").status_code == 400


# ------------------------------------------------------------------------------------------------ job entry point
def test_main_all_sports_continues_past_a_failing_sport(monkeypatch, capsys):
    seen = []

    def fake(sp, write=True):
        seen.append((sp, write))
        if sp == "nhl":
            raise RuntimeError("master unreadable")
        return {"sport": sp, "status": "live", "count": 3, "games": [], "lines_source": ["bettingpros"],
                "lines_age_minutes": 4.0, "gamelog_latest": "x", "summary": {}}
    monkeypatch.setattr(dp, "run_sport", fake)
    assert dp.main(["--all", "--write-gcs"]) == 1                          # non-zero: Cloud Run shows the failure ...
    assert seen == [("nfl", True), ("nhl", True), ("nba", True)]           # ... but nfl and nba were still refreshed
    seen.clear()
    monkeypatch.setattr(dp, "run_sport", lambda sp, write=True: {"sport": sp, "status": "live", "count": 1, "games": [],
                                                                  "lines_source": [], "lines_age_minutes": None,
                                                                  "gamelog_latest": None, "summary": {}})
    assert dp.main(["--sport", "nba", "--no-write"]) == 0
