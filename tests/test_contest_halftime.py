"""The halftime window: Kalshi pricing, the window state machine, the engine gate, hidden bets, and the refresh pass. Offline."""
from datetime import datetime, timedelta, timezone

import pytest

from contest import halftime, service, store
from contest.espn import parse_event
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.service import RequestError
from contest.slips import Leg, Quote, Slip, SlipError
from contest.sync import sync_events
from tests._contest_engine import make_engine
from tests.test_contest_service import KICK, espn_event

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
HALF = KICK + timedelta(minutes=95)
SLUG = "bowls"
RULES_ON = ContestRules(sport="ncaaf", halftime_enabled=True)


def m(title, bid, ask, strike=None, event="26DEC19BYUKU"):
    return {"event_ticker": f"KXSERIES-{event}", "title": title, "yes_bid_dollars": f"{bid:.4f}", "yes_ask_dollars": f"{ask:.4f}", **({"floor_strike": strike} if strike is not None else {})}


KALSHI = {
    "game": [m("Kansas wins", 0.58, 0.60), m("BYU wins", 0.40, 0.42)],
    "spread": [m("Kansas wins by over 3.5 points", 0.49, 0.51, 3.5), m("Kansas wins by over 6.5 points", 0.36, 0.38, 6.5), m("Kansas wins by over 0.5 points", 0.66, 0.68, 0.5)],
    "total": [m("Full Game: Over 47.5 points scored", 0.50, 0.52, 47.5), m("Full Game: Over 50.5 points scored", 0.38, 0.40, 50.5)],
}
HOME, AWAY = {"kansas"}, {"byu"}


def test_pricing_uses_the_mid_adds_the_margin_and_picks_the_rung_nearest_a_coin_flip():
    q = halftime.game_quotes(HOME, AWAY, KALSHI, RULES_ON, HALF)
    assert (q[("spread", "home")].line, q[("spread", "away")].line) == (-3.5, 3.5)             # Kansas -3.5 is the 50% rung
    assert q[("total", "over")].line == q[("total", "under")].line == 47.5
    assert q[("ml", "home")].line is None and q[("ml", "home")].price < 0 < q[("ml", "away")].price   # the 59% side is the favourite
    assert all(v.fetched_at == HALF for v in q.values())
    assert -125 <= q[("spread", "home")].price <= -105 and -125 <= q[("spread", "away")].price <= -105        # a coin flip plus 7% is about -110


def test_other_games_markets_in_the_same_series_never_leak_in():
    other = {"game": [m("Duke wins", 0.50, 0.52, event="26DEC19DUKEUNC"), m("UNC wins", 0.48, 0.50, event="26DEC19DUKEUNC")],
             "spread": [m("Duke wins by over 0.5 points", 0.50, 0.50, 0.5, event="26DEC19DUKEUNC")],
             "total": [m("Full Game: Over 60.5 points scored", 0.50, 0.50, 60.5, event="26DEC19DUKEUNC")]}
    mixed = {k: KALSHI[k] + other[k] for k in KALSHI}
    q = halftime.game_quotes(HOME, AWAY, mixed, RULES_ON, HALF)
    assert q[("total", "over")].line == 47.5 and q[("spread", "home")].line == -3.5               # not Duke-UNC's 60.5 / 0.5
    assert halftime.game_quotes({"duke"}, {"unc"}, mixed, RULES_ON, HALF)[("total", "over")].line == 60.5


def test_a_game_with_no_or_ambiguous_kalshi_event_gets_nothing():
    assert halftime.game_quotes({"nobody"}, {"nowhere"}, KALSHI, RULES_ON, HALF) == {}
    two = {"game": [m("Kansas wins", 0.5, 0.52, event="A"), m("BYU wins", 0.48, 0.5, event="A"), m("Kansas wins", 0.5, 0.52, event="B")]}
    assert halftime.game_quotes(HOME, AWAY, two, RULES_ON, HALF) == {}


def test_half_and_quarter_totals_are_ignored():
    markets = {"game": KALSHI["game"], "total": [m("1st Half: Over 23.5 points scored", 0.50, 0.50, 23.5), m("Full Game: Over 47.5 points scored", 0.45, 0.47, 47.5)]}
    assert halftime.game_quotes(HOME, AWAY, markets, RULES_ON, HALF)[("total", "over")].line == 47.5


def test_wide_or_lopsided_markets_are_not_offered():
    wide = {"game": [m("Kansas wins", 0.50, 0.62), m("BYU wins", 0.40, 0.42)], "spread": [m("Kansas wins by over 3.5 points", 0.30, 0.50, 3.5)], "total": [m("Full Game: Over 47.5 points scored", 0.97, 0.99, 47.5)]}
    assert halftime.game_quotes(HOME, AWAY, wide, RULES_ON, HALF) == {}
    one_side = {"game": [m("Kansas wins", 0.58, 0.60)]}
    assert halftime.game_quotes(HOME, AWAY, one_side, RULES_ON, HALF) == {}


def test_the_other_teams_spread_ladder_gives_the_same_line_from_the_dogs_side():
    away_ladder = {"spread": [m("BYU wins by over 2.5 points", 0.30, 0.32, 2.5), m("BYU wins by over 0.5 points", 0.46, 0.48, 0.5)]}
    q = halftime.game_quotes(HOME, AWAY, away_ladder, RULES_ON, HALF)
    assert (q[("spread", "away")].line, q[("spread", "home")].line) == (-0.5, 0.5)


# --- the window ---

@pytest.fixture
def w(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, SLUG, "Bowl Bankroll", RULES_ON, NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    sync_events(engine, cid, [espn_event("1")], NOW)
    service.join(engine, SLUG, "u1", {"inviteCode": "WELCOME", "displayName": "Alex"}, NOW)
    return engine, cid


def adv(engine, half, score, quote_at, now):
    return halftime.advance_window(engine, 1, half, score, quote_at, now, RULES_ON)


def test_the_window_opens_only_after_two_polls_with_a_quote_newer_than_first_sight(w):
    engine, _ = w
    t0 = HALF
    assert adv(engine, True, (21, 17), None, t0) == "watching"                                    # first sight, no quote
    assert adv(engine, True, (21, 17), t0, t0 + timedelta(seconds=30)) == "watching"             # a quote at first_seen is not AFTER it
    assert adv(engine, True, (21, 17), t0 + timedelta(seconds=40), t0 + timedelta(seconds=60)) == "open"


def test_a_score_change_while_watching_restarts_the_count(w):
    engine, _ = w
    adv(engine, True, (21, 17), None, HALF)
    assert adv(engine, True, (21, 17), None, HALF + timedelta(seconds=10)) == "watching"                 # two polls but no quote yet
    assert adv(engine, True, (24, 17), HALF + timedelta(seconds=15), HALF + timedelta(seconds=20)) == "watching"      # a late score post: start over
    assert halftime.window_row(engine, 1)["polls"] == 1
    assert adv(engine, True, (24, 17), HALF + timedelta(seconds=25), HALF + timedelta(seconds=30)) == "open"


def test_open_windows_close_on_score_change_stale_quote_cap_and_leaving_halftime(w):
    engine, cid = w
    def open_it(g=1):
        with engine.begin() as c:
            import sqlalchemy as sa
            c.execute(sa.text("DELETE FROM contest_halftime"))
        adv(engine, True, (21, 17), None, HALF)
        adv(engine, True, (21, 17), HALF + timedelta(seconds=5), HALF + timedelta(seconds=10))
        assert adv(engine, True, (21, 17), HALF + timedelta(seconds=40), HALF + timedelta(seconds=45)) == "open"
    open_it()
    assert adv(engine, True, (21, 20), HALF + timedelta(seconds=70), HALF + timedelta(seconds=75)) == "closed"
    assert halftime.window_row(engine, 1)["close_reason"] == "score_changed"
    open_it()
    assert adv(engine, True, (21, 17), HALF + timedelta(seconds=40), HALF + timedelta(seconds=200)) == "closed"          # quote is 160 s old
    assert halftime.window_row(engine, 1)["close_reason"] == "stale_quote"
    open_it()
    assert adv(engine, True, (21, 17), HALF + timedelta(seconds=500), HALF + timedelta(seconds=510)) == "closed"
    assert halftime.window_row(engine, 1)["close_reason"] == "cap"
    open_it()
    assert adv(engine, False, (21, 17), None, HALF + timedelta(seconds=60)) == "closed"
    assert halftime.window_row(engine, 1)["close_reason"] == "left_halftime"
    assert adv(engine, True, (21, 17), HALF + timedelta(seconds=100), HALF + timedelta(seconds=101)) == "closed"      # never reopens


def test_a_watching_window_that_never_opened_just_disappears(w):
    engine, _ = w
    adv(engine, True, (21, 17), None, HALF)
    assert adv(engine, False, (21, 17), None, HALF + timedelta(seconds=30)) == "none" and halftime.window_row(engine, 1) is None
    assert adv(engine, False, None, None, HALF) == "none"


# --- the engine gate and the hidden bets ---

def open_window(engine, cid):
    adv(engine, True, (21, 17), None, HALF)
    q = halftime.game_quotes(HOME, AWAY, KALSHI, RULES_ON, HALF + timedelta(seconds=30))
    store.write_quotes(engine, 1, q, HALF + timedelta(seconds=30), source="kalshi-halftime")
    return adv(engine, True, (21, 17), HALF + timedelta(seconds=30), HALF + timedelta(seconds=40))


def hb(engine, cid):
    return store.load_board(engine, cid)


def test_a_started_game_is_closed_until_the_window_opens_and_then_takes_straights_only(w):
    engine, cid = w
    now = HALF + timedelta(seconds=45)
    with pytest.raises(RequestError) as e:
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [{"gameId": 1, "market": "ml", "side": "away", "line": None, "price": 150}], "stakeCents": 5_000, "key": "a"}, now)
    assert e.value.code == "GAME_CLOSED"
    assert open_window(engine, cid) == "open"
    q = hb(engine, cid)[(1, "ml", "away")]
    pick = {"gameId": 1, "market": "ml", "side": "away", "line": None, "price": q.price}
    f = service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick], "stakeCents": 5_000, "key": "b"}, now)
    assert f["myBets"][0]["kind"] == "straight" and f["games"][0]["halftime"] == {"open": True}
    with pytest.raises(RequestError) as e:                                   # a parlay is not allowed in the window
        service.place(engine, SLUG, "u1", {"kind": "parlay", "picks": [pick, {**pick, "gameId": 2}], "stakeCents": 5_000, "key": "c"}, now)
    assert e.value.code in ("GAME_CLOSED", "NO_GAME", "LEG_COUNT")
    with pytest.raises(RequestError) as e:                                   # over the 10% cap
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick], "stakeCents": 60_000, "key": "d"}, now)
    assert e.value.code == "STAKE_MAX"
    with pytest.raises(RequestError) as e:                                   # the price the player saw is not the one on offer
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [{**pick, "price": q.price + 40}], "stakeCents": 5_000, "key": "e"}, now)
    assert e.value.code == "PRICE_CHANGED"


def test_a_stale_halftime_quote_is_refused_and_the_window_flag_off_refuses_everything(w):
    engine, cid = w
    open_window(engine, cid)
    q = hb(engine, cid)[(1, "ml", "away")]
    pick = {"gameId": 1, "market": "ml", "side": "away", "line": None, "price": q.price}
    with pytest.raises(RequestError) as e:
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick], "stakeCents": 5_000, "key": "a"}, HALF + timedelta(seconds=30 + 100))
    assert e.value.code == "QUOTE_STALE"
    store.set_rule_switches(engine, cid, {"halftime_enabled": False})
    with pytest.raises(RequestError) as e:
        service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [pick], "stakeCents": 5_000, "key": "b"}, HALF + timedelta(seconds=50))
    assert e.value.code == "GAME_CLOSED"


def test_halftime_bets_stay_hidden_from_others_until_the_window_closes(w):
    engine, cid = w
    service.join(engine, SLUG, "u2", {"inviteCode": "WELCOME", "displayName": "Blake"}, NOW)
    open_window(engine, cid)
    q = hb(engine, cid)[(1, "ml", "away")]
    now = HALF + timedelta(seconds=50)
    service.place(engine, SLUG, "u1", {"kind": "straight", "picks": [{"gameId": 1, "market": "ml", "side": "away", "line": None, "price": q.price}], "stakeCents": 5_000, "key": "a"}, now)
    pool = service.feed(engine, SLUG, "u2", now)["pool"]
    assert pool[0]["legs"] == [None] and pool[0]["stakeCents"] is None                       # the game has kicked off, but the window is open
    adv(engine, False, (21, 17), None, now + timedelta(minutes=2))                           # ESPN leaves halftime: the window closes
    shown = service.feed(engine, SLUG, "u2", now + timedelta(minutes=2))["pool"][0]
    assert shown["legs"][0]["market"] == "ml" and shown["stakeCents"] == 5_000


def test_the_pass_reads_espn_and_kalshi_and_moves_the_window(w):
    engine, cid = w
    def ev(state_name, scores=("21", "17")):
        e = espn_event("1", kick=KICK, state="in", name=state_name, scores=scores)
        e["halftime"] = state_name == "STATUS_HALFTIME"
        return e
    t = HALF
    assert halftime.halftime_pass(engine, cid, [ev("STATUS_HALFTIME")], KALSHI, t) == {1: "watching"}
    assert halftime.halftime_pass(engine, cid, [ev("STATUS_HALFTIME")], KALSHI, t + timedelta(seconds=30)) == {1: "open"}
    assert store.load_board(engine, cid)[(1, "spread", "home")].fetched_at == t + timedelta(seconds=30)
    assert halftime.halftime_pass(engine, cid, [ev("STATUS_IN_PROGRESS")], None, t + timedelta(seconds=90)) == {1: "closed"}


def test_the_pass_does_nothing_when_the_rule_is_off(tmp_path):
    engine = make_engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, SLUG, "Bowl", ContestRules(sport="ncaaf"), NOW)
    sync_events(engine, cid, [espn_event("1")], NOW)
    e = espn_event("1", state="in", name="STATUS_HALFTIME", scores=("1", "0"))
    e["halftime"] = True
    assert halftime.halftime_pass(engine, cid, [e], KALSHI, HALF) == {}


def test_espn_flags_halftime():
    assert parse_event({"id": "9", "date": "2026-12-19T17:00Z", "status": {"type": {"name": "STATUS_HALFTIME", "state": "in", "completed": False, "detail": "Halftime"}},
                        "competitions": [{"competitors": [{"homeAway": "home", "score": "1", "team": {"id": "1", "abbreviation": "H"}}, {"homeAway": "away", "score": "0", "team": {"id": "2", "abbreviation": "A"}}]}]})["halftime"] is True
    assert espn_event("1")["halftime"] is False


def test_fetch_reads_every_listed_total_series_and_pages():
    seen = []
    def http(url, params):
        seen.append((params["series_ticker"], params.get("cursor")))
        if params["series_ticker"] == "KXNCAAFTOTAL" and not params.get("cursor"):
            return {"markets": [{"title": "Full Game: Over 47.5 points scored"}], "cursor": "next"}
        return {"markets": [{"title": params["series_ticker"]}], "cursor": None}
    out = halftime.fetch_kalshi("ncaaf", http)
    assert [t for t, _ in seen] == ["KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTOTAL", "KXNCAFTOTAL"]
    assert len(out["total"]) == 3 and len(out["game"]) == 1
