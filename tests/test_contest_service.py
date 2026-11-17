"""contest.service: requests in, the page's feed out. Offline: SQLite, hand-built ESPN events."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from tests._contest_engine import make_engine

from contest import service, store
from contest.espn import parse_event
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.service import RequestError
from contest.sync import sync_events

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=5)
SLUG = "bowls"


def espn_event(eid, kick=KICK, home_id="2305", away_id="252", spread="+5.5", state="pre", name="STATUS_SCHEDULED", completed=False, scores=(None, None)):
    odds = [{"provider": {"name": "DraftKings"}, "pointSpread": {"home": {"close": {"line": spread, "odds": "-112"}}, "away": {"close": {"line": "-5.5", "odds": "-108"}}},
             "total": {"over": {"close": {"line": "o47.5", "odds": "-108"}}, "under": {"close": {"line": "u47.5", "odds": "-112"}}},
             "moneyline": {"home": {"close": {"odds": "+185"}}, "away": {"close": {"odds": "-225"}}}}]
    comp = {"odds": odds, "competitors": [
        {"homeAway": "home", "score": scores[0], "curatedRank": {"current": 99}, "team": {"id": home_id, "abbreviation": "KU", "shortDisplayName": "Kansas"}},
        {"homeAway": "away", "score": scores[1], "curatedRank": {"current": 10}, "team": {"id": away_id, "abbreviation": "BYU", "shortDisplayName": "BYU"}}]}
    return parse_event({"id": eid, "date": kick.strftime("%Y-%m-%dT%H:%MZ"), "season": {"slug": "postseason"},
                        "status": {"type": {"name": name, "state": state, "completed": completed, "detail": "Final" if completed else "x"}}, "competitions": [comp]})


@pytest.fixture
def w(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, SLUG, "Bowl Bankroll", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    sync_events(engine, cid, [espn_event("1"), espn_event("2", kick=KICK + timedelta(hours=3))], NOW)
    return engine, cid


def joined(w, user="u1", name="Alex"):
    engine, _ = w
    service.join(engine, SLUG, user, {"inviteCode": "WELCOME", "displayName": name}, NOW)


def pick(gid=1, market="spread", side="home", line=5.5, price=-112):
    return {"gameId": gid, "market": market, "side": side, "line": line, "price": price}


def test_the_feed_before_joining_shows_the_board_and_no_money(w):
    engine, _ = w
    f = service.feed(engine, SLUG, "u1", NOW, suggested_name="alex")
    assert f["me"] == {"joined": False, "displayName": "", "suggestedName": "alex", "renamesLeft": 3}
    assert f["balanceCents"] == 0 and f["myBets"] == [] and f["sample"] is False
    g = f["games"][0]
    assert g["status"] == "scheduled" and g["home"] == {"abbr": "KU", "name": "Kansas", "espnId": 2305, "rank": None} and g["away"]["rank"] == 10
    assert g["spread"] == {"line": 5.5, "away": -108, "home": -112} and g["total"] == {"line": 47.5, "over": -108, "under": -112} and g["ml"] == {"away": -225, "home": 185}
    assert f["rules"]["teaser"]["6.0"]["2"] == -120 and f["rules"]["cashoutSigma"] == {"spread": 15.0, "total": 15.5} and f["rules"]["keyWeights"] == {"3": 2.5, "7": 1.9}


def test_joining_gives_the_starting_bankroll_and_a_bad_invite_is_403(w):
    engine, _ = w
    with pytest.raises(RequestError) as e:
        service.join(engine, SLUG, "u1", {"inviteCode": "NOPE", "displayName": "Alex"}, NOW)
    assert e.value.http == 403 and e.value.code == "INVITE_INVALID"
    joined(w)
    f = service.feed(engine, SLUG, "u1", NOW)
    assert f["me"]["joined"] and f["me"]["displayName"] == "Alex" and f["balanceCents"] == 500_000
    assert [r["name"] for r in f["leaderboard"]] == ["Alex"] and f["leaderboard"][0]["mine"] is True


def test_a_name_that_is_taken_or_invalid_is_refused_with_its_message(w):
    engine, _ = w
    joined(w)
    with pytest.raises(RequestError) as e:
        service.join(engine, SLUG, "u2", {"inviteCode": "WELCOME", "displayName": "alex"}, NOW)
    assert e.value.code in ("NAME_TAKEN", "NAME_INVALID") and e.value.message
    with pytest.raises(RequestError) as e2:
        service.join(engine, SLUG, "u3", {"inviteCode": "WELCOME", "displayName": "x"}, NOW)
    assert e2.value.http == 400 and e2.value.message


def test_placing_a_straight_debits_and_lists_the_bet_with_its_payout(w):
    engine, _ = w
    joined(w)
    f = service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k1"}, NOW)
    assert f["balanceCents"] == 490_000
    b = f["myBets"][0]
    assert b["kind"] == "straight" and b["status"] == "pending" and b["stakeCents"] == 10_000 and b["toWinCents"] == 8_929
    assert b["legs"] == [{"gameId": 1, "market": "spread", "side": "home", "line": 5.5, "price": -112, "result": None}]


def test_a_retry_with_the_same_key_places_nothing_twice(w):
    engine, _ = w
    joined(w)
    body = {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k1"}
    service.place(engine, SLUG, "u1", body, NOW)
    f = service.place(engine, SLUG, "u1", body, NOW)
    assert f["balanceCents"] == 490_000 and len(f["myBets"]) == 1


def test_a_price_the_player_did_not_see_is_409_with_the_current_price(w):
    engine, _ = w
    joined(w)
    with pytest.raises(RequestError) as e:
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick(price=-150)], "stakeCents": 10_000, "key": "k1"}, NOW)
    assert e.value.http == 409 and e.value.code == "PRICE_CHANGED" and e.value.detail["price"] == -112
    assert service.feed(engine, SLUG, "u1", NOW)["balanceCents"] == 500_000


def test_a_player_who_has_not_joined_cannot_bet(w):
    engine, _ = w
    with pytest.raises(RequestError) as e:
        service.place(engine, SLUG, "u9", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k1"}, NOW)
    assert e.value.http == 403 and e.value.code == "NOT_ENTERED"


def test_a_parlay_a_round_robin_and_several_straights_place(w):
    engine, cid = w
    sync_events(engine, cid, [espn_event("3", kick=KICK + timedelta(hours=6))], NOW)
    joined(w)
    p1, p2, p3 = pick(1), pick(2), pick(3)
    f = service.place(engine, SLUG, "u1", {"kind": "parlay", "picks": [p1, p2], "stakeCents": 5_000, "key": "p"}, NOW)
    assert f["myBets"][0]["kind"] == "parlay" and len(f["myBets"][0]["legs"]) == 2
    f = service.place(engine, SLUG, "u1", {"kind": "round_robin", "picks": [p1, p2, p3], "robinSizes": [2], "stakeCents": 1_000, "key": "rr"}, NOW)
    rr = [b for b in f["myBets"] if b["groupId"] == "rr"]
    assert len(rr) == 3 and all(b["kind"] == "parlay" for b in rr)
    f = service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [p1, p2], "stakeCents": 2_500, "key": "s"}, NOW)
    assert sum(1 for b in f["myBets"] if b["kind"] == "straight") == 2
    assert f["balanceCents"] == 500_000 - 5_000 - 3_000 - 5_000


def test_an_alt_line_and_an_sgp_place_with_their_server_prices(w):
    from contest.altlines import alt_price
    from contest.sgp import sgp_price
    from contest.slips import Leg
    engine, cid = w
    joined(w)
    board = store.load_board(engine, cid)
    alt = alt_price("spread", "home", board[(1, "spread", "home")], board[(1, "spread", "away")], 4.5, ContestRules(sport="ncaaf"))
    f = service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick(line=4.5, price=alt)], "stakeCents": 2_500, "key": "alt"}, NOW)
    assert f["myBets"][0]["legs"][0]["line"] == 4.5
    legs = (Leg(1, "spread", "home", 5.5, -112), Leg(1, "total", "over", 47.5, -108))
    ticket = sgp_price(legs, board, ContestRules(sport="ncaaf"))
    f = service.place(engine, SLUG, "u1", {"kind": "sgp", "picks": [pick(), pick(market="total", side="over", line=47.5, price=-108)], "stakeCents": 2_500, "key": "sgp", "ticketPrice": ticket}, NOW)
    b = f["myBets"][0]
    assert b["kind"] == "sgp" and b["ticketPrice"] == ticket and b["toWinCents"] > 0


def test_other_players_picks_are_hidden_until_kickoff_and_visible_after(w):
    engine, _ = w
    joined(w)
    joined(w, "u2", "Blake")
    service.place(engine, SLUG, "u2", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "b"}, NOW)
    pool = service.feed(engine, SLUG, "u1", NOW)["pool"]
    assert len(pool) == 1 and pool[0]["owner"] == "Blake" and pool[0]["legs"] == [None] and pool[0]["stakeCents"] is None
    after = service.feed(engine, SLUG, "u1", KICK + timedelta(minutes=1))["pool"]
    assert after[0]["legs"][0]["gameId"] == 1 and after[0]["stakeCents"] == 10_000
    assert service.feed(engine, SLUG, "u2", NOW)["pool"] == []                 # your own bets live in myBets, not the pool


def test_a_game_that_started_shows_live_and_a_final_pays(w):
    engine, cid = w
    joined(w)
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    assert service.feed(engine, SLUG, "u1", KICK + timedelta(minutes=5))["games"][0]["status"] == "live"
    sync_events(engine, cid, [espn_event("1", state="post", name="STATUS_FINAL", completed=True, scores=("21", "20"))], KICK + timedelta(hours=4))
    f = service.feed(engine, SLUG, "u1", KICK + timedelta(hours=4))
    assert f["games"][0]["status"] == "final" and f["games"][0]["state"] == {"homeScore": 21, "awayScore": 20, "detail": "Final"}
    assert f["myBets"][0]["status"] == "won" and f["balanceCents"] == 490_000 + 10_000 + 8_929


def test_cash_out_pays_the_quoted_value_and_a_stale_value_is_409(w):
    engine, _ = w
    joined(w)
    f = service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    bet_id = int(f["myBets"][0]["id"])
    with pytest.raises(RequestError) as e:
        service.cash_out(engine, SLUG, "u1", {"betIds": [bet_id], "expectedCents": 99_999}, NOW)
    assert e.value.http == 409 and e.value.code == "VALUE_CHANGED"
    value = e.value.detail["cents"]
    f = service.cash_out(engine, SLUG, "u1", {"betIds": [bet_id], "expectedCents": value}, NOW)
    assert f["myBets"][0]["status"] == "cashed_out" and f["myBets"][0]["payoutCents"] == value and f["balanceCents"] == 490_000 + value


def test_rename_updates_the_leaderboard_and_counts_down(w):
    engine, _ = w
    joined(w)
    f = service.rename(engine, SLUG, "u1", {"displayName": "Alexander"}, NOW)
    assert f["me"]["displayName"] == "Alexander" and f["me"]["renamesLeft"] == 2 and f["leaderboard"][0]["name"] == "Alexander"


def test_bad_requests_are_400_and_an_unknown_contest_is_404(w):
    engine, _ = w
    joined(w)
    for body in ({"kind": "straight", "picks": [pick()], "key": "k"}, {"kind": "straight", "picks": [pick()], "stakeCents": 10_000},
                 {"kind": "straight", "picks": [{"gameId": 1}], "stakeCents": 10_000, "key": "k"}):
        with pytest.raises(RequestError) as e:
            service.place(engine, SLUG, "u1", body, NOW)
        assert e.value.http == 400
    with pytest.raises(RequestError) as e:
        service.feed(engine, "nope", "u1", NOW)
    assert e.value.http == 404
    with pytest.raises(RequestError) as e:
        service.cash_out(engine, SLUG, "u1", {"betIds": [], "expectedCents": 0}, NOW)
    assert e.value.http == 400


def test_bankroll_history_follows_settled_bets_not_placed_ones(w):
    engine, cid = w
    joined(w)
    assert [p["cents"] for p in service.feed(engine, SLUG, "u1", NOW)["history"]] == [500_000]
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    assert [p["cents"] for p in service.feed(engine, SLUG, "u1", NOW)["history"]] == [500_000]          # a stake moves balance to risk: bankroll unchanged
    sync_events(engine, cid, [espn_event("1", state="post", name="STATUS_FINAL", completed=True, scores=("21", "20"))], KICK + timedelta(hours=4))
    assert [p["cents"] for p in service.feed(engine, SLUG, "u1", KICK + timedelta(hours=4))["history"]] == [500_000, 508_929]
    store.adjust(engine, store.entry_of(engine, cid, "u1")["entry_id"], -5_000, "refund a duplicate", KICK + timedelta(hours=5))
    assert service.feed(engine, SLUG, "u1", KICK + timedelta(hours=5))["history"][-1]["cents"] == 503_929
    assert service.feed(engine, SLUG, "nobody", NOW)["history"] == []


def test_a_lost_bet_is_a_step_down_by_the_stake(w):
    engine, cid = w
    joined(w)
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick()], "stakeCents": 10_000, "key": "k"}, NOW)
    sync_events(engine, cid, [espn_event("1", state="post", name="STATUS_FINAL", completed=True, scores=("10", "30"))], KICK + timedelta(hours=4))
    h = service.feed(engine, SLUG, "u1", KICK + timedelta(hours=4))["history"]
    assert [p["cents"] for p in h] == [500_000, 490_000]
    f = service.feed(engine, SLUG, "u1", KICK + timedelta(hours=4))
    assert f["balanceCents"] == h[-1]["cents"]                      # nothing at risk, so bankroll equals balance
