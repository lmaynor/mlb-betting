"""contest.store on SQLite (same SQL as Postgres minus FOR UPDATE): placing, settling, hiding picks, the ledger invariant."""
import os
import random
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from contest import store
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.slips import Leg, Quote, Slip, SlipError

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=5)
AFTER = KICK + timedelta(hours=4)
RULES = ContestRules(sport="ncaaf")


def _engine(tmp_path):
    """SQLite by default. Set CONTEST_TEST_PG_URL (e.g. postgresql+pg8000://postgres@127.0.0.1:55432/contest_test) to run the SAME
    tests against a throwaway local Postgres, which exercises SERIAL, RETURNING and FOR UPDATE through pg8000 (not run in CI)."""
    url = os.environ.get("CONTEST_TEST_PG_URL")
    if not url:
        return sa.create_engine(f"sqlite:///{tmp_path / 'contest.db'}")
    engine = sa.create_engine(url)
    with engine.begin() as c:
        for t in ("contest_halftime", "contest_notice", "contest_admin_log", "contest_ledger", "contest_blackjack", "contest_quote", "contest_leg", "contest_bet", "contest_game", "contest_entry", "contest_invite", "contest"):
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
    return engine


def _world(tmp_path, rules):
    engine = _engine(tmp_path)
    ensure_schema(engine)
    cid = store.create_contest(engine, "bowls-2026", "Bowl Bankroll", rules, NOW)
    store.create_invite(engine, cid, "WELCOME", 10, NOW)
    games = [store.upsert_game(engine, cid, f"ev{i}", KICK + timedelta(minutes=30 * i), f"A{i}", f"H{i}", round_label="Bowl") for i in range(1, 5)]
    return engine, cid, games


@pytest.fixture
def world(tmp_path):
    return _world(tmp_path, RULES)


@pytest.fixture
def world_no_lifeline(tmp_path):
    """The same contest with the blackjack lifeline off: a player is out as soon as the balance cannot cover the minimum stake."""
    return _world(tmp_path, ContestRules(sport="ncaaf", blackjack_enabled=False))


def join(w, user="u1", name=None):
    engine, cid, _ = w
    name = name or ("Alex" if user == "u1" else f"Player {user}")         # names are unique per contest
    return store.join_contest(engine, cid, user, name, "WELCOME", NOW)


def leg(gid, market="spread", side="home", line=-3.5, price=-110, pts=0.0):
    return Leg(gid, market, side, line, price, pts)


def board(*legs, at=NOW):
    return {(g.game_id, g.market, g.side): Quote(g.line, g.price, at - timedelta(seconds=30)) for g in legs}


def place(w, user, kind, legs, stake=10_000, key="k1", now=NOW):
    engine, cid, _ = w
    slip = Slip(kind, tuple(legs), stake, key)
    return store.place_bet(engine, cid, user, slip, now, board(*legs, at=now))


def balance(w, user="u1"):
    engine, cid, _ = w
    with engine.connect() as c:
        return c.execute(text("SELECT balance_cents FROM contest_entry WHERE contest_id = :c AND user_id = :u"), {"c": cid, "u": user}).scalar_one()


def books_balance(w):
    assert store.reconcile(w[0], w[1]) == []


# ------------------------------------------------------------------------------------------------------------------ join

def test_join_credits_the_starting_bankroll_once(world):
    eid = join(world)
    assert balance(world) == 500_000
    assert join(world) == eid and balance(world) == 500_000          # idempotent; invite not consumed twice
    with world[0].connect() as c:
        assert c.execute(text("SELECT uses FROM contest_invite WHERE code = 'WELCOME'")).scalar_one() == 1
    books_balance(world)


def test_invite_must_be_valid_and_has_a_use_limit(world):
    engine, cid, _ = world
    with pytest.raises(store.ContestError) as e:
        store.join_contest(engine, cid, "x", "X", "WRONG", NOW)
    assert e.value.code == "INVITE_INVALID"
    store.create_invite(engine, cid, "ONCE", 1, NOW)
    store.join_contest(engine, cid, "a", "Aaron", "ONCE", NOW)
    with pytest.raises(store.ContestError):
        store.join_contest(engine, cid, "b", "Bea", "ONCE", NOW)


# ------------------------------------------------------------------------------------------------------------------ place

def test_place_debits_and_records_the_quote(world):
    join(world)
    g = world[2]
    b = place(world, "u1", "straight", [leg(g[0])], stake=10_000)
    assert b["status"] == "pending" and b["stake_cents"] == 10_000 and balance(world) == 490_000
    assert b["legs"][0]["line"] == -3.5 and b["legs"][0]["price"] == -110
    books_balance(world)


def test_retry_with_same_key_moves_no_money(world):
    join(world)
    g = world[2]
    a = place(world, "u1", "straight", [leg(g[0])], key="same")
    b = place(world, "u1", "straight", [leg(g[0])], key="same")
    assert a["id"] == b["id"] and balance(world) == 490_000
    place(world, "u1", "straight", [leg(g[0])], key="other")
    assert balance(world) == 480_000


def test_rejections_write_nothing(world):
    engine, cid, g = world
    join(world)
    for bad in (
        lambda: place(world, "u1", "straight", [leg(g[0])], stake=1),                              # below minimum
        lambda: place(world, "u1", "straight", [leg(g[0])], stake=999_999),                        # over balance
        lambda: place(world, "u1", "parlay", [leg(g[0]), leg(g[0], market="total", side="over", line=40.5)]),   # same game twice
        lambda: place(world, "u1", "straight", [leg(g[0])], now=KICK + timedelta(minutes=25)),    # game 1 kicks off at KICK+30m: inside the 15-minute closing buffer
        lambda: store.place_bet(engine, cid, "u1", Slip("straight", (leg(g[0], price=-125),), 10_000, "pc"), NOW, board(leg(g[0]))),  # price moved
    ):
        with pytest.raises(SlipError):
            bad()
    assert balance(world) == 500_000
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM contest_bet")).scalar_one() == 0
    books_balance(world)


def test_must_be_entered_and_active(world):
    g = world[2]
    with pytest.raises(store.ContestError) as e:
        place(world, "stranger", "straight", [leg(g[0])])
    assert e.value.code == "NOT_ENTERED"


def test_a_leg_from_another_contest_is_refused(world):
    engine, cid, g = world
    join(world)
    other = store.create_contest(engine, "other", "Other", RULES, NOW)
    foreign = store.upsert_game(engine, other, "evX", KICK)
    with pytest.raises(SlipError) as e:
        place(world, "u1", "straight", [leg(foreign)])
    assert e.value.code == "NO_GAME"


# ------------------------------------------------------------------------------------------------------------------ settle

def test_straight_win_and_loss(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    place(world, "u1", "straight", [leg(g[0], side="home", line=-3.5)], key="a")      # home -3.5
    place(world, "u2", "straight", [leg(g[0], side="away", line=3.5)], key="b")       # away +3.5
    settled = store.settle_game(engine, g[0], 24, 20, AFTER)                           # home wins by 4
    assert len(settled) == 2
    assert balance(world, "u1") == 490_000 + 19_091 and balance(world, "u2") == 490_000
    books_balance(world)


def test_settle_is_idempotent_and_refuses_a_different_score(world):
    engine, cid, g = world
    join(world)
    place(world, "u1", "straight", [leg(g[0])])
    store.settle_game(engine, g[0], 24, 20, AFTER)
    before = balance(world)
    assert store.settle_game(engine, g[0], 24, 20, AFTER) == []                       # nothing left to settle
    assert balance(world) == before
    with pytest.raises(store.ContestError) as e:
        store.settle_game(engine, g[0], 10, 20, AFTER)
    assert e.value.code == "SCORE_MISMATCH"


def test_parlay_loses_early_and_wins_when_all_legs_land(world):
    engine, cid, g = world
    join(world, "u1")
    join(world, "u2")
    p1 = [leg(g[0], line=-3.5), leg(g[1], side="away", line=7.5)]
    place(world, "u1", "parlay", p1, key="p")
    place(world, "u2", "parlay", p1, key="p")
    store.settle_game(engine, g[0], 17, 20, AFTER)                                     # home -3.5 loses: both parlays dead now
    assert balance(world, "u1") == 490_000 and balance(world, "u2") == 490_000
    with engine.connect() as c:
        assert {r[0] for r in c.execute(text("SELECT status FROM contest_bet")).all()} == {"lost"}
    store.settle_game(engine, g[1], 20, 21, AFTER)                                     # later leg grades; nothing more to settle
    books_balance(world)


def test_parlay_win_pays_the_product(world):
    engine, cid, g = world
    join(world)
    place(world, "u1", "parlay", [leg(g[0], line=-3.5), leg(g[1], side="away", line=7.5)])
    assert store.settle_game(engine, g[0], 24, 20, AFTER) == []                        # one leg still open
    assert len(store.settle_game(engine, g[1], 20, 21, AFTER)) == 1
    assert balance(world) == 490_000 + 36_446
    books_balance(world)


def test_teaser_push_reduces_to_the_remaining_table_price(world):
    engine, cid, g = world
    join(world, "u1")
    join(world, "u2")
    legs = [leg(g[0], line=-7.0, pts=6.0), leg(g[1], side="away", line=2.5, pts=6.0), leg(g[2], market="total", side="over", line=50.5, pts=6.0)]
    place(world, "u1", "teaser", legs, stake=10_000, key="t")
    place(world, "u2", "teaser", legs, stake=10_000, key="t")
    store.settle_game(engine, g[0], 21, 20, AFTER)                                     # -7 teased to -1.0: wins by 1 = push
    store.settle_game(engine, g[1], 10, 20, AFTER)                                     # away +2.5 teased to +8.5: wins
    assert balance(world, "u1") == 490_000                                             # one leg still open
    store.settle_game(engine, g[2], 30, 20, AFTER)                                     # total 50 > 44.5: wins
    two_leg = 10_000 + 8_333                                                           # table price -120 for the 2 surviving legs
    assert balance(world, "u1") == 490_000 + two_leg and balance(world, "u2") == 490_000 + two_leg
    books_balance(world)


def test_teaser_with_one_loss_loses(world):
    engine, cid, g = world
    join(world)
    place(world, "u1", "teaser", [leg(g[0], line=-7.0, pts=6.0), leg(g[1], side="away", line=2.5, pts=6.0)])
    store.settle_game(engine, g[1], 30, 10, AFTER)                                     # away +8.5 loses by 20
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM contest_bet")).scalar_one() == "lost"
    books_balance(world)


def test_void_game_refunds_a_straight_and_drops_a_parlay_leg(world):
    engine, cid, g = world
    join(world, "u1")
    join(world, "u2")
    place(world, "u1", "straight", [leg(g[0])], key="s")
    place(world, "u2", "parlay", [leg(g[0]), leg(g[1], side="away", line=7.5)], key="p")
    store.void_game(engine, g[0], AFTER)
    assert balance(world, "u1") == 500_000                                             # refunded
    assert balance(world, "u2") == 490_000                                             # parlay now rides on one leg
    store.settle_game(engine, g[1], 20, 21, AFTER)                                     # away +7.5 wins -> straight at -110
    assert balance(world, "u2") == 490_000 + 19_091
    books_balance(world)
    with pytest.raises(store.ContestError):
        store.settle_game(engine, g[0], 1, 0, AFTER)                                   # a voided game cannot be graded


def test_busting_eliminates_only_when_nothing_is_pending(world_no_lifeline):
    world = world_no_lifeline
    engine, cid, g = world
    join(world)
    store.adjust(engine, 1, -480_000, "test: leave $200", NOW)                          # balance 20,000
    # 25% max stake: place the largest legal bet repeatedly until under the $25 minimum
    bal = balance(world)
    i = 0
    while bal >= 2_500:
        stake = max(2_500, min(bal * 25 // 100, bal))
        place(world, "u1", "straight", [leg(g[i % 4])], stake=stake, key=f"k{i}")
        bal = balance(world)
        i += 1
        assert i < 60
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM contest_entry")).scalar_one() == "active"     # still pending bets: not out yet
    for gid in g:
        store.settle_game(engine, gid, 0, 30, AFTER)                                    # every home -3.5 loses
    with engine.connect() as c:
        status, bal = c.execute(text("SELECT status, balance_cents FROM contest_entry")).one()
    assert status == "eliminated" and bal < 2_500
    with pytest.raises(store.ContestError) as e:
        place(world, "u1", "straight", [leg(g[0])], key="after")
    assert e.value.code == "ELIMINATED"
    books_balance(world)


def test_adjust_needs_a_reason_and_keeps_books_balanced(world):
    engine, _, _ = world
    join(world)
    with pytest.raises(store.ContestError):
        store.adjust(engine, 1, 500, " ", NOW)
    assert store.adjust(engine, 1, 500, "goodwill", NOW) == 500_500
    books_balance(world)


# ------------------------------------------------------------------------------------------------------------------ reading

def test_picks_are_hidden_from_others_until_kickoff(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    place(world, "u1", "parlay", [leg(g[0]), leg(g[3], side="away", line=7.5)], stake=10_000)      # g0 kicks +30m, g3 +2h
    mine = store.visible_bets(engine, cid, "u1", NOW)[0]
    assert mine["mine"] and mine["stake_cents"] == 10_000 and all("hidden" not in x for x in mine["legs"])
    theirs = store.visible_bets(engine, cid, "u2", NOW)[0]
    assert theirs["legs"] == [{"hidden": True}, {"hidden": True}] and theirs["stake_cents"] is None and theirs["owner"] == "Alex"
    mid = store.visible_bets(engine, cid, "u2", KICK + timedelta(minutes=45))[0]                  # g0 started, g3 not
    assert mid["legs"][0]["game_id"] == g[0] and mid["legs"][1] == {"hidden": True} and mid["stake_cents"] is None
    late = store.visible_bets(engine, cid, "u2", KICK + timedelta(hours=3))[0]                    # both started
    assert late["stake_cents"] == 10_000 and all("hidden" not in x for x in late["legs"])


def test_a_lost_parlay_still_hides_its_unstarted_legs(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    place(world, "u1", "parlay", [leg(g[0]), leg(g[3], side="away", line=7.5)])
    store.settle_game(engine, g[0], 0, 30, KICK + timedelta(minutes=40))                          # leg 1 lost, bet settled lost
    theirs = store.visible_bets(engine, cid, "u2", KICK + timedelta(minutes=40))[0]
    assert theirs["status"] == "lost" and theirs["legs"][1] == {"hidden": True}


def test_leaderboard_ranks_by_bankroll_including_open_risk(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    place(world, "u1", "straight", [leg(g[0])], stake=50_000)                                      # at risk, still counts
    place(world, "u2", "straight", [leg(g[1], side="away", line=3.5)], stake=50_000, key="b")
    store.settle_game(engine, g[1], 20, 30, AFTER)                                                 # away +3.5 wins
    board_ = store.leaderboard(engine, cid)
    assert [r["display_name"] for r in board_] == ["Blair", "Alex"] and board_[0]["rank"] == 1
    assert board_[1]["bankroll_cents"] == 500_000 and board_[1]["open_risk_cents"] == 50_000
    assert board_[0]["won"] == 1 and board_[0]["bankroll_cents"] > 500_000


def test_ledger_always_matches_balance_over_a_random_season(world):
    engine, cid, g = world
    rng = random.Random(7)
    users = [f"u{i}" for i in range(4)]
    for u in users:
        join(world, u, u)
    n = 0
    for gid in g:
        for u in users:
            for _ in range(3):
                kind = rng.choice(["straight", "parlay", "teaser"])
                others = [x for x in g if x != gid]
                if kind == "straight":
                    legs = [leg(gid, side=rng.choice(["home", "away"]), line=rng.choice([-3.5, 3.5, -7.0, 2.5]))]
                else:
                    legs = [leg(gid, line=-3.5, pts=6.0 if kind == "teaser" else 0.0),
                            leg(rng.choice(others), side="away", line=rng.choice([3.5, 7.5]), pts=6.0 if kind == "teaser" else 0.0)]
                try:
                    place(world, u, kind, legs, stake=rng.choice([2_500, 5_000, 10_000]), key=f"{u}-{n}")
                except SlipError:
                    pass
                n += 1
    books_balance(world)
    for gid in g:
        store.settle_game(engine, gid, rng.randint(0, 45), rng.randint(0, 45), AFTER)
    books_balance(world)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM contest_bet WHERE status = 'pending'")).scalar_one() == 0


# ------------------------------------------------------------------------------------------------------------------ concurrency

needs_pg = pytest.mark.skipif(not os.environ.get("CONTEST_TEST_PG_URL"), reason="row locking needs Postgres (set CONTEST_TEST_PG_URL)")


def _hammer(engine, cid, user, slips):
    from concurrent.futures import ThreadPoolExecutor

    def go(slip):
        try:
            return store.place_bet(engine, cid, user, slip, NOW, None)["id"]
        except SlipError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=len(slips)) as pool:
        return list(pool.map(go, slips))


@needs_pg
def test_concurrent_bets_cannot_overspend_the_balance(world):
    engine, cid, g = world
    wide = store.create_contest(engine, "wide", "Wide", ContestRules(sport="ncaaf", max_stake_bp=10_000), NOW)
    store.create_invite(engine, wide, "W", 1, NOW)
    store.join_contest(engine, wide, "racer", "Racer", "W", NOW)
    gid = store.upsert_game(engine, wide, "evW", KICK)
    # 8 simultaneous $1,000 bets against a $5,000 balance: exactly 5 may land, never 6
    out = _hammer(engine, wide, "racer", [Slip("straight", (leg(gid),), 100_000, f"r{i}") for i in range(8)])
    assert sum(isinstance(x, int) for x in out) == 5 and out.count("INSUFFICIENT") == 3
    with engine.connect() as c:
        assert c.execute(text("SELECT balance_cents FROM contest_entry WHERE contest_id = :c"), {"c": wide}).scalar_one() == 0
    assert store.reconcile(engine, wide) == []


@needs_pg
def test_concurrent_retries_of_one_slip_place_it_once(world):
    engine, cid, g = world
    join(world)
    out = _hammer(engine, cid, "u1", [Slip("straight", (leg(g[0]),), 10_000, "same") for _ in range(8)])
    assert len(set(out)) == 1 and isinstance(out[0], int)                  # every caller got the same bet back
    assert balance(world) == 490_000
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM contest_bet")).scalar_one() == 1
    books_balance(world)


# ------------------------------------------------------------------------------------------------------------------ round robins

def rr_picks(g, n=4):
    return [leg(g[i], side="home", line=-3.5) for i in range(n)]


def place_rr(w, user, picks_, sizes, unit=5_000, key="rr1", now=NOW):
    engine, cid, _ = w
    return store.place_round_robin(engine, cid, user, picks_, sizes, unit, key, now, board(*picks_, at=now))


def test_round_robin_places_every_parlay_in_one_group(world):
    join(world)
    g = world[2]
    bets = place_rr(world, "u1", rr_picks(g), (2,), unit=5_000)
    assert len(bets) == 6 and {b["group_id"] for b in bets} == {"rr1"} and all(b["kind"] == "parlay" and len(b["legs"]) == 2 for b in bets)
    assert balance(world) == 500_000 - 6 * 5_000
    books_balance(world)


def test_round_robin_retry_moves_no_money_and_failure_writes_nothing(world):
    engine, cid, g = world
    join(world)
    a = place_rr(world, "u1", rr_picks(g), (2,))
    b = place_rr(world, "u1", rr_picks(g), (2,))
    assert [x["id"] for x in a] == [x["id"] for x in b] and balance(world) == 470_000
    with pytest.raises(SlipError):                                                  # one pick is inside the closing buffer
        place_rr(world, "u1", rr_picks(g), (2, 3), key="late", now=KICK + timedelta(minutes=25))
    assert balance(world) == 470_000
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM contest_bet WHERE group_id = 'late'")).scalar_one() == 0
    books_balance(world)


def test_round_robin_settles_each_parlay_on_its_own_legs(world):
    engine, cid, g = world
    join(world)
    place_rr(world, "u1", rr_picks(g), (2,), unit=10_000)                           # 4 picks, 6 parlays, 600.00 at risk
    for gid, (h, a) in zip(g, [(24, 20), (24, 20), (24, 20), (17, 20)]):            # picks 1-3 win, pick 4 loses
        store.settle_game(engine, gid, h, a, AFTER)
    # parlays among picks 1-3 win (3 of them); the 3 that include pick 4 lose
    assert balance(world) == 500_000 - 60_000 + 3 * 36_446
    with engine.connect() as c:
        rows = c.execute(text("SELECT status, COUNT(*) FROM contest_bet GROUP BY status")).all()
    assert dict(rows) == {"won": 3, "lost": 3}
    books_balance(world)


def test_round_robin_group_id_is_visible_but_picks_stay_hidden(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    place_rr(world, "u1", rr_picks(g, 3), (2,))
    theirs = store.visible_bets(engine, cid, "u2", NOW)
    assert len(theirs) == 3 and {b["group_id"] for b in theirs} == {"rr1"}
    assert all(b["legs"] == [{"hidden": True}, {"hidden": True}] and b["stake_cents"] is None for b in theirs)


def test_eleven_pick_round_robin_places_55_parlays_and_settles(world):
    engine, cid, g = world
    join(world)
    extra = [store.upsert_game(engine, cid, f"evx{i}", KICK + timedelta(hours=1, minutes=i)) for i in range(7)]
    gids = list(g) + extra                                                                 # 11 games
    picks_ = [leg(gid) for gid in gids]
    bets = place_rr(world, "u1", picks_, (2,), unit=500)
    assert len(bets) == 55 and {b["group_id"] for b in bets} == {"rr1"}
    assert balance(world) == 500_000 - 55 * 500
    for gid in gids:                                                                       # every home -3.5 wins
        store.settle_game(engine, gid, 24, 20, AFTER)
    from contest.odds import parlay_decimal, payout_cents
    assert balance(world) == 500_000 - 55 * 500 + 55 * payout_cents(500, parlay_decimal([-110, -110]))
    books_balance(world)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM contest_bet WHERE status = 'won'")).scalar_one() == 55


# ------------------------------------------------------------------------------------------------------------------ cash out

from contest.cashout import CashoutRefused, OPPOSITE                                   # noqa: E402


def two_sided(game_id, line, price=-110, opp_price=-110, at=NOW, market="spread", side="home"):
    """The current board for one spread market: `side` at (line, price), the other side mirrored."""
    t = at - timedelta(seconds=30)
    return {(game_id, market, side): Quote(line, price, t), (game_id, market, OPPOSITE[side]): Quote(-line, opp_price, t)}


def place_straight(w, user="u1", game_idx=0, line=-3.5, stake=10_000, key="s1"):
    g = w[2]
    return place(w, user, "straight", [leg(g[game_idx], line=line)], stake=stake, key=key)


def test_cash_out_pays_the_current_value_and_closes_the_bet(world):
    engine, cid, g = world
    join(world)
    bet = place_straight(world)
    now = NOW + timedelta(hours=1)
    brd = two_sided(g[0], -3.5, at=now)
    value = store.cashout_quote(engine, cid, "u1", [bet["id"]], now, brd)
    assert value == 9_068                                                               # 19,091 x 0.5 x 0.95
    out = store.cash_out(engine, cid, "u1", [bet["id"]], value, now, brd)
    assert out[0]["status"] == "cashed_out" and out[0]["payout_cents"] == 9_068
    assert [x["result"] for x in out[0]["legs"]] == ["void"]                            # closed: a later score cannot settle it
    assert balance(world) == 500_000 - 10_000 + 9_068
    with engine.connect() as c:
        assert c.execute(text("SELECT kind, amount_cents FROM contest_ledger WHERE kind = 'CASHOUT'")).one() == ("CASHOUT", 9_068)
    books_balance(world)
    assert store.settle_game(engine, g[0], 24, 20, AFTER) == []                         # the score does not pay it again
    assert balance(world) == 500_000 - 10_000 + 9_068
    books_balance(world)


def test_a_line_that_moved_your_way_cashes_out_above_the_stake(world):
    engine, cid, g = world
    join(world)
    bet = place_straight(world, line=-3.5)
    now = NOW + timedelta(hours=1)
    brd = two_sided(g[0], -6.5, at=now)                                                         # the same side now costs 3 more points
    value = store.cashout_quote(engine, cid, "u1", [bet["id"]], now, brd)
    assert value > 10_000
    store.cash_out(engine, cid, "u1", [bet["id"]], value, now, brd)
    assert balance(world) == 500_000 - 10_000 + value > 500_000                          # a profit before the game is played
    books_balance(world)


def test_value_changed_writes_nothing(world):
    engine, cid, g = world
    join(world)
    bet = place_straight(world)
    now = NOW + timedelta(hours=1)
    shown = store.cashout_quote(engine, cid, "u1", [bet["id"]], now, two_sided(g[0], -3.5, at=now))
    with pytest.raises(CashoutRefused) as e:                                            # the board moved between the quote and the click
        store.cash_out(engine, cid, "u1", [bet["id"]], shown, now, two_sided(g[0], -5.5, at=now))
    assert e.value.code == "VALUE_CHANGED" and e.value.detail["cents"] != shown
    assert balance(world) == 490_000
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM contest_bet")).scalar_one() == "pending"
    books_balance(world)


def test_cannot_cash_out_twice_a_stranger_or_a_closed_game(world):
    engine, cid, g = world
    join(world, "u1")
    join(world, "u2")
    bet = place_straight(world)
    now = NOW + timedelta(hours=1)
    brd = two_sided(g[0], -3.5, at=now)
    with pytest.raises(store.ContestError) as e:
        store.cash_out(engine, cid, "u2", [bet["id"]], 9_068, now, brd)                 # someone else's bet
    assert e.value.code == "NO_BET"
    late = KICK + timedelta(minutes=25)                                                 # game 0 kicks off at KICK+30m: inside the 15-minute close
    with pytest.raises(CashoutRefused) as e2:
        store.cash_out(engine, cid, "u1", [bet["id"]], 9_068, late, two_sided(g[0], -3.5, at=late))
    assert e2.value.code == "GAME_CLOSED"
    store.cash_out(engine, cid, "u1", [bet["id"]], 9_068, now, brd)
    with pytest.raises(store.ContestError) as e3:
        store.cash_out(engine, cid, "u1", [bet["id"]], 9_068, now, brd)
    assert e3.value.code == "NOT_PENDING"
    books_balance(world)


def test_a_round_robin_cashes_out_all_or_nothing(world):
    engine, cid, g = world
    join(world)
    picks_ = [leg(g[i]) for i in range(3)]
    bets = place_rr(world, "u1", picks_, (2,), unit=5_000)                              # 3 parlays
    ids = [b["id"] for b in bets]
    now = NOW + timedelta(hours=1)
    brd = {}
    for i in range(3):
        brd.update(two_sided(g[i], -3.5, at=now))
    total = store.cashout_quote(engine, cid, "u1", ids, now, brd)
    assert total == 3 * int(18_223 * 0.25 * 0.95)                                        # each parlay: 5,000 at -110/-110 pays 18,223
    with pytest.raises(CashoutRefused):
        store.cash_out(engine, cid, "u1", ids, total + 1, now, brd)                     # a wrong total refuses the whole group
    assert balance(world) == 500_000 - 15_000
    out = store.cash_out(engine, cid, "u1", ids, total, now, brd)
    assert {b["status"] for b in out} == {"cashed_out"} and len(out) == 3
    assert balance(world) == 500_000 - 15_000 + total
    books_balance(world)


def test_the_record_counts_a_cash_out_by_its_profit(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    win = place_straight(world, "u1", 0, key="a")
    lose = place_straight(world, "u1", 1, key="b")
    now = NOW + timedelta(hours=1)
    brd = {**two_sided(g[0], -6.5, at=now), **two_sided(g[1], -3.5, at=now)}                            # bet a moved your way (profit), bet b is flat (a small loss)
    store.cash_out(engine, cid, "u1", [win["id"]], store.cashout_quote(engine, cid, "u1", [win["id"]], now, brd), now, brd)
    store.cash_out(engine, cid, "u1", [lose["id"]], store.cashout_quote(engine, cid, "u1", [lose["id"]], now, brd), now, brd)
    row = store.leaderboard(engine, cid)[0]
    assert (row["won"], row["lost"], row["pushed"]) == (1, 1, 0) and row["open_risk_cents"] == 0


def test_others_still_cannot_see_a_cashed_out_pick_before_kickoff(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    bet = place_straight(world)
    now = NOW + timedelta(hours=1)
    brd = two_sided(g[0], -3.5, at=now)
    store.cash_out(engine, cid, "u1", [bet["id"]], 9_068, now, brd)
    theirs = store.visible_bets(engine, cid, "u2", now)[0]
    assert theirs["status"] == "cashed_out" and theirs["legs"] == [{"hidden": True}] and theirs["stake_cents"] is None and theirs["payout_cents"] is None


# ------------------------------------------------------------------------------------------------------------------ display names

def test_names_are_unique_per_contest_by_key(world):
    engine, cid, _ = world
    store.join_contest(engine, cid, "u1", "Alex", "WELCOME", NOW)
    for clash in ("alex", "A.L.E.X", " ALEX "):
        with pytest.raises(store.ContestError) as e:
            store.join_contest(engine, cid, "u2", clash, "WELCOME", NOW)
        assert e.value.code == "NAME_TAKEN"
    other = store.create_contest(engine, "other", "Other", RULES, NOW)
    store.create_invite(engine, other, "O", 5, NOW)
    store.join_contest(engine, other, "u2", "Alex", "O", NOW)                         # the same name in another contest is fine


def test_a_bad_name_refuses_the_join_and_does_not_burn_the_invite(world):
    engine, cid, _ = world
    for raw, code in (("A", "NAME_INVALID"), ("Admin", "NAME_RESERVED"), ("bad@name", "NAME_INVALID")):
        with pytest.raises(store.ContestError) as e:
            store.join_contest(engine, cid, "u1", raw, "WELCOME", NOW)
        assert e.value.code == code
    with engine.connect() as c:
        assert c.execute(text("SELECT uses FROM contest_invite WHERE code = 'WELCOME'")).scalar_one() == 0
        assert c.execute(text("SELECT COUNT(*) FROM contest_entry")).scalar_one() == 0


def test_rename_changes_what_everyone_sees_and_is_limited(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    assert store.rename_entry(engine, cid, "u1", "Alexander", NOW) == "Alexander"
    assert {r["display_name"] for r in store.leaderboard(engine, cid)} == {"Alexander", "Blair"}
    with pytest.raises(store.ContestError) as e:
        store.rename_entry(engine, cid, "u1", "blair", NOW)                           # taken by another player
    assert e.value.code == "NAME_TAKEN"
    assert store.rename_entry(engine, cid, "u1", "ALEXANDER", NOW) == "ALEXANDER"      # re-casing the same name is free
    for n in ("Al", "Lex"):
        store.rename_entry(engine, cid, "u1", n, NOW)                                  # changes 2 and 3 (Alexander was the first)
    with pytest.raises(store.ContestError) as e2:
        store.rename_entry(engine, cid, "u1", "Fourth One", NOW)
    assert e2.value.code == "RENAME_LIMIT"
    with pytest.raises(store.ContestError) as e3:
        store.rename_entry(engine, cid, "stranger", "Whoever", NOW)
    assert e3.value.code == "NOT_ENTERED"


def test_admin_rename_ignores_the_limit_but_not_the_rules(world):
    engine, cid, _ = world
    eid = join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    for n in ("Al", "Lex", "Xander"):
        try:
            store.rename_entry(engine, cid, "u1", n, NOW)
        except store.ContestError:
            pass                                                                       # the player is out of renames; the organiser is not
    assert store.admin_rename(engine, eid, "Moved Along") == "Moved Along"
    with pytest.raises(store.ContestError):
        store.admin_rename(engine, eid, "Blair")
    with pytest.raises(store.ContestError):
        store.admin_rename(engine, eid, "House")
    with pytest.raises(store.ContestError) as e:
        store.admin_rename(engine, 999, "Anyone")
    assert e.value.code == "NO_ENTRY"


def test_pool_and_leaderboard_show_the_current_name(world):
    engine, cid, g = world
    join(world, "u1", "Alex")
    join(world, "u2", "Blair")
    place(world, "u1", "straight", [leg(g[0])])
    store.rename_entry(engine, cid, "u1", "Alexander", NOW)
    assert store.visible_bets(engine, cid, "u2", NOW)[0]["owner"] == "Alexander"


def test_equal_bankrolls_share_a_rank_and_are_marked_tied(world):
    engine, cid, _ = world
    for i, u in enumerate(("u1", "u2", "u3", "u4")):
        join(world, u)
    eid = {e["user_id"]: e["entry_id"] for e in store.entries_of(engine, cid)}
    store.adjust(engine, eid["u1"], 10_000, "tie test", NOW)
    store.adjust(engine, eid["u4"], -10_000, "tie test", NOW)
    board_ = store.leaderboard(engine, cid)
    assert [(r["rank"], r["tied"]) for r in board_] == [(1, False), (2, True), (2, True), (4, False)]
