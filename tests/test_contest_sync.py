"""contest.espn / contest.sync: scoreboard parsing, the stored board, live state and grading. Offline: SQLite and hand-built payloads."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from tests._contest_engine import make_engine

from contest import store
from contest.espn import parse_event, parse_odds, status_of
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.slips import Leg, Slip
from contest.sync import quotes_from_odds, sync_events

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=5)


def odds(spread="+5.5", total="o47.5", ml=("+185", "-225"), provider="DraftKings"):
    return [{"provider": {"name": provider}, "overUnder": 47.5, "spread": 5.5,
             "pointSpread": {"home": {"close": {"line": spread, "odds": "-112"}}, "away": {"close": {"line": "-5.5", "odds": "-108"}}},
             "total": {"over": {"close": {"line": total, "odds": "-108"}}, "under": {"close": {"line": "u47.5", "odds": "-112"}}},
             "moneyline": {"home": {"close": {"odds": ml[0]}}, "away": {"close": {"odds": ml[1]}}}}]


def event(eid="1", state="pre", name="STATUS_SCHEDULED", completed=False, scores=(None, None), detail="8:42 - 3rd Quarter", with_odds=True,
          situation=None, notes=None, rank=(99, 10), date=None):
    comp = {"neutralSite": False, "notes": notes or [], "odds": odds() if with_odds else [], "situation": situation,
            "competitors": [
                {"homeAway": "home", "score": scores[0], "curatedRank": {"current": rank[0]}, "team": {"id": "2305", "abbreviation": "KU", "shortDisplayName": "Kansas"}},
                {"homeAway": "away", "score": scores[1], "curatedRank": {"current": rank[1]}, "team": {"id": "252", "abbreviation": "BYU", "shortDisplayName": "BYU"}}]}
    return {"id": eid, "date": date or KICK.strftime("%Y-%m-%dT%H:%MZ"), "season": {"slug": "regular-season"},
            "status": {"type": {"name": name, "state": state, "completed": completed, "detail": detail}}, "competitions": [comp]}


def test_pre_game_odds_parse_into_home_handicap_and_prices():
    ev = parse_event(event())
    assert ev["status"] == "scheduled" and ev["home"] == {"abbr": "KU", "name": "Kansas", "espn_id": 2305, "rank": None}
    assert ev["away"]["rank"] == 10 and ev["round"] == "Regular Season"
    assert ev["odds"]["spread"] == {"line": 5.5, "home": -112, "away": -108}
    assert ev["odds"]["total"] == {"line": 47.5, "over": -108, "under": -112} and ev["odds"]["ml"] == {"home": 185, "away": -225}


def test_a_missing_market_stays_none_and_no_odds_means_none():
    comp = event()["competitions"][0]
    comp["odds"][0].pop("moneyline")
    assert parse_odds(comp)["ml"] is None
    assert parse_odds({"odds": []}) is None
    assert parse_event(event(with_odds=False))["odds"] is None


def test_status_mapping():
    assert status_of(event(state="in", name="STATUS_IN_PROGRESS")) == "live"
    assert status_of(event(state="post", name="STATUS_FINAL", completed=True)) == "final"
    assert status_of(event(state="post", name="STATUS_POSTPONED")) == "void"
    assert status_of(event(state="pre")) == "scheduled"


def test_live_and_final_carry_score_and_state_but_no_odds():
    live = parse_event(event(state="in", name="STATUS_IN_PROGRESS", scores=("14", "21"),
                             situation={"possession": "252", "downDistanceText": "2nd & 7 at KU 35", "isRedZone": False}))
    assert live["status"] == "live" and live["odds"] is None
    assert (live["home_score"], live["away_score"]) == (14, 21)
    assert live["state"] == {"detail": "8:42 - 3rd Quarter", "possession": "away", "down_distance": "2nd & 7 at KU 35"}
    final = parse_event(event(state="post", name="STATUS_FINAL", completed=True, scores=("28", "31"), detail="Final"))
    assert final["state"]["detail"] == "Final" and final["away_score"] == 31


def test_bowl_headline_becomes_the_round_label():
    ev = parse_event(event(notes=[{"headline": "Rose Bowl Game"}]))
    assert ev["round"] == "Rose Bowl Game"


def test_quotes_follow_the_board_convention():
    q = quotes_from_odds(parse_event(event())["odds"], NOW)
    assert q[("spread", "home")].line == 5.5 and q[("spread", "away")].line == -5.5
    assert q[("total", "over")].line == q[("total", "under")].line == 47.5
    assert q[("ml", "home")].line is None and q[("ml", "away")].price == -225


@pytest.fixture
def world(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, "sync", "Sync", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    return engine, cid


def test_a_pass_writes_the_game_its_quotes_and_its_meta_and_is_idempotent(world):
    engine, cid = world
    ev = parse_event(event())
    r1 = sync_events(engine, cid, [ev], NOW)
    r2 = sync_events(engine, cid, [ev], NOW + timedelta(minutes=20))
    assert r1["games"] == r2["games"] == 1 and r1["quotes"] == 6 and r1["errors"] == []
    board = store.load_board(engine, cid)
    assert len(board) == 6
    q = board[(1, "spread", "home")]
    assert (q.line, q.price) == (5.5, -112) and q.fetched_at == NOW + timedelta(minutes=20)
    with engine.connect() as c:
        row = c.execute(sa.text("SELECT away_team, home_team, round_label, teams_json FROM contest_game")).one()
    assert row[:3] == ("BYU", "Kansas", "Regular Season") and '"espn_id": 2305' in row[3]


def test_a_live_game_keeps_its_last_pre_game_quotes_and_stores_the_state(world):
    engine, cid = world
    sync_events(engine, cid, [parse_event(event())], NOW)
    live = parse_event(event(state="in", name="STATUS_IN_PROGRESS", scores=("7", "3")))
    r = sync_events(engine, cid, [live], NOW + timedelta(hours=5, minutes=30))
    assert r["quotes"] == 0 and len(store.load_board(engine, cid)) == 6
    with engine.connect() as c:
        assert '"home_score": 7' in c.execute(sa.text("SELECT state_json FROM contest_game")).scalar_one()


def test_a_final_score_settles_a_bet_and_a_repeat_changes_nothing(world):
    engine, cid = world
    ev = parse_event(event())
    sync_events(engine, cid, [ev], NOW)
    store.join_contest(engine, cid, "u1", "Alex", "WELCOME", NOW)
    board = store.load_board(engine, cid)
    q = board[(1, "spread", "home")]
    store.place_bet(engine, cid, "u1", Slip("straight", (Leg(1, "spread", "home", q.line, q.price),), 10_000, "k1"), NOW, board)
    final = parse_event(event(state="post", name="STATUS_FINAL", completed=True, scores=("21", "20"), detail="Final"))   # home +5.5 wins outright
    r = sync_events(engine, cid, [final], KICK + timedelta(hours=4))
    again = sync_events(engine, cid, [final], KICK + timedelta(hours=5))
    assert r["finals"] == 1 and r["settled_bets"] == 1 and again["errors"] == []
    with engine.connect() as c:
        assert c.execute(sa.text("SELECT status FROM contest_bet")).scalar_one() == "won"
    assert store.reconcile(engine, cid) == []


def test_a_postponed_game_voids_and_a_conflicting_final_is_an_error_not_a_crash(world):
    engine, cid = world
    sync_events(engine, cid, [parse_event(event("1")), parse_event(event("2"))], NOW)
    post = parse_event(event("1", state="post", name="STATUS_POSTPONED"))
    f2 = parse_event(event("2", state="post", name="STATUS_FINAL", completed=True, scores=("10", "7"), detail="Final"))
    f2b = parse_event(event("2", state="post", name="STATUS_FINAL", completed=True, scores=("10", "8"), detail="Final"))
    r = sync_events(engine, cid, [post, f2, f2b], KICK + timedelta(hours=4))
    assert r["voided_games"] == 1 and r["finals"] == 1
    assert len(r["errors"]) == 1 and "SCORE_MISMATCH" in r["errors"][0]["error"]


def test_include_filters_games_out(world):
    engine, cid = world
    r = sync_events(engine, cid, [parse_event(event("1", notes=[{"headline": "Rose Bowl"}])), parse_event(event("2"))], NOW,
                    include=lambda e: "Bowl" in e["round"])
    assert r["games"] == 1


def _live(situation):
    e = event(state="in", name="STATUS_IN_PROGRESS", scores=("6", "0"), situation=situation)
    return parse_event(e)["state"]


def test_the_live_football_payloads_seen_on_2026_11_17():
    # a normal down: the text ESPN sends, with no possession team id (so no dot)
    on_down = {"down": 1, "yardLine": 84, "distance": 10, "downDistanceText": "1st & 10 at EMU 16", "shortDownDistanceText": "1st & 10",
               "possessionText": "EMU 16", "isRedZone": False, "homeTimeouts": 3, "awayTimeouts": 3}
    assert _live(on_down) == {"detail": "8:42 - 3rd Quarter", "possession": None, "down_distance": "1st & 10 at EMU 16"}
    # right after a touchdown: down and distance are -1, no text, and ESPN still says red zone: nothing is shown
    after_td = {"down": -1, "yardLine": 100, "distance": 0, "isRedZone": True, "homeTimeouts": 3, "awayTimeouts": 3}
    assert _live(after_td) == {"detail": "8:42 - 3rd Quarter", "possession": None}
    # a red-zone DOWN does carry the flag
    assert _live({**on_down, "down": 2, "downDistanceText": "2nd & 3 at EMU 5", "isRedZone": True})["red_zone"] is True
    # when ESPN does send the possession team id it is used (home id 2305 in this fixture)
    assert _live({**on_down, "possession": "2305"})["possession"] == "home"
