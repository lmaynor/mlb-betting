"""contest.sgp: same-game parlay pricing, validation, placement, settlement. SQLite, no network."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from contest import store
from contest.cashout import CashoutRefused, cashout_offer
from contest.odds import american_to_decimal, payout_cents
from contest.rules import ContestRules
from contest.schema import ensure_schema
from contest.sgp import bvn_cdf, pair_ok, sgp_price
from contest.slips import GameInfo, Leg, Quote, Slip, SlipError, validate_slip

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=5)
AFTER = KICK + timedelta(hours=4)
R = ContestRules(sport="ncaaf")


def full_board(game_id=1, spread=-3.5, total=50.5, ml=(-170, 145), at=NOW):
    t = at - timedelta(seconds=30)
    return {
        (game_id, "spread", "home"): Quote(spread, -110, t), (game_id, "spread", "away"): Quote(-spread, -110, t),
        (game_id, "total", "over"): Quote(total, -110, t), (game_id, "total", "under"): Quote(total, -110, t),
        (game_id, "ml", "home"): Quote(None, ml[0], t), (game_id, "ml", "away"): Quote(None, ml[1], t),
    }


def legs(spread_side="home", total_side="over", game_id=1, board=None):
    b = board or full_board(game_id)
    return (Leg(game_id, "spread", spread_side, b[(game_id, "spread", spread_side)].line, -110),
            Leg(game_id, "total", total_side, b[(game_id, "total", total_side)].line, -110))


def test_the_bivariate_normal_matches_known_values():
    assert bvn_cdf(0, 0, 0.0) == pytest.approx(0.25, abs=1e-6)
    assert bvn_cdf(0, 0, 0.5) == pytest.approx(1 / 3, abs=1e-6)           # 1/4 + asin(rho) / (2 pi)
    assert bvn_cdf(-9, 0, 0.3) == 0.0


def test_aligned_pairs_pay_less_than_opposed_ones_and_both_beat_nothing_at_rho_zero():
    prices = {(s, t): sgp_price(legs(s, t), full_board(), R) for s in ("home", "away") for t in ("over", "under")}
    assert prices[("home", "over")] == prices[("away", "under")] < prices[("home", "under")] == prices[("away", "over")]   # favourite + over is the shorter price
    independent = american_to_decimal(-110) ** 2                                       # a plain parlay of the same two prices: +264
    assert float(american_to_decimal(prices[("home", "over")])) < float(independent)    # positive correlation: shorter than a parlay
    assert float(american_to_decimal(prices[("home", "under")])) > float(independent)   # negative correlation: longer


def test_the_margin_is_taken_once():
    flat = ContestRules(sport="ncaaf", sgp_rho_bp=0, sgp_margin_bp=0)
    p = sgp_price(legs(), full_board(), flat)
    assert p == 295 or p == 300                                                       # 0.25 -> +300, to the nearest 5 (a 0.25 joint is exactly +300)
    assert sgp_price(legs(), full_board(), ContestRules(sport="ncaaf", sgp_rho_bp=0, sgp_margin_bp=700)) < p


def test_a_moneyline_pairs_with_the_total_and_a_spread_with_a_moneyline_does_not():
    b = full_board()
    ml_total = (Leg(1, "ml", "home", None, -170), Leg(1, "total", "over", 50.5, -110))
    assert pair_ok(ml_total) and sgp_price(ml_total, b, R) is not None
    nested = (Leg(1, "spread", "home", -3.5, -110), Leg(1, "ml", "home", None, -170))
    assert not pair_ok(nested) and sgp_price(nested, b, R) is None
    assert sgp_price(legs(), b, ContestRules(sport="ncaaf", sgp_enabled=False)) is None


def test_an_off_board_leg_cannot_be_priced():
    b = full_board()
    off = (Leg(1, "spread", "home", -4.5, -110), Leg(1, "total", "over", 50.5, -110))
    assert sgp_price(off, b, R) is None


GAMES = {1: GameInfo(KICK, "scheduled"), 2: GameInfo(KICK, "scheduled")}


def check(slip, board=None):
    validate_slip(slip, R, 500_000, 0, NOW, GAMES, board if board is not None else full_board())


def sgp_slip(ticket="auto", **kw):
    lg = kw.pop("legs", legs())
    price = sgp_price(lg, full_board(), R) if ticket == "auto" else ticket
    return Slip("sgp", tuple(lg), kw.pop("stake", 10_000), "k", price)


def test_a_priced_ticket_places_and_a_wrong_ticket_price_is_price_changed():
    check(sgp_slip())
    with pytest.raises(SlipError) as e:
        check(sgp_slip(ticket=sgp_price(legs(), full_board(), R) + 40))
    assert e.value.code == "PRICE_CHANGED" and e.value.detail["ticket_price"] == sgp_price(legs(), full_board(), R)


@pytest.mark.parametrize("lg,code", [
    ((Leg(1, "spread", "home", -3.5, -110), Leg(2, "total", "over", 50.5, -110)), "SGP_GAME"),
    ((Leg(1, "spread", "home", -3.5, -110), Leg(1, "spread", "away", 3.5, -110)), "SGP_PAIR"),
    ((Leg(1, "spread", "home", -3.5, -110),), "LEG_COUNT"),
])
def test_sgp_refusals(lg, code):
    with pytest.raises(SlipError) as e:
        check(Slip("sgp", lg, 10_000, "k", -110))
    assert e.value.code == code


def test_a_ticket_with_no_price_is_refused_and_so_is_an_sgp_when_the_flag_is_off():
    with pytest.raises(SlipError) as e:
        check(Slip("sgp", legs(), 10_000, "k", None))
    assert e.value.code == "SGP_PRICE"
    with pytest.raises(SlipError) as e:
        validate_slip(sgp_slip(), ContestRules(sport="ncaaf", sgp_enabled=False), 500_000, 0, NOW, GAMES, full_board())
    assert e.value.code == "SGP_OFF"


def test_a_ticket_cannot_be_cashed_out():
    lg = legs()
    with pytest.raises(CashoutRefused) as e:
        cashout_offer("sgp", 10_000, lg, full_board(), R, GAMES, NOW)
    assert e.value.code == "SGP"


# ------------------------------------------------------------------------------------------------ store: place and settle

@pytest.fixture
def world(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    ensure_schema(engine)
    cid = store.create_contest(engine, "sgp", "SGP test", R, NOW)
    store.create_invite(engine, cid, "WELCOME", 5, NOW)
    gid = store.upsert_game(engine, cid, "ev1", KICK, "A1", "H1", round_label="Bowl")
    store.join_contest(engine, cid, "u1", "Alex", "WELCOME", NOW)
    return engine, cid, gid


def place(world, ticket=None, stake=10_000):
    engine, cid, gid = world
    b = full_board(gid)
    lg = legs(board=b, game_id=gid)
    return store.place_bet(engine, cid, "u1", Slip("sgp", lg, stake, "k1", ticket or sgp_price(lg, b, R)), NOW, b), sgp_price(lg, b, R)


def bal(world):
    engine, cid, _ = world
    return int(engine.connect().execute(sa.text("SELECT balance_cents FROM contest_entry WHERE user_id = 'u1'")).scalar_one())


def test_both_legs_win_pays_the_ticket_price(world):
    engine, cid, gid = world
    bet, ticket = place(world)
    assert bet["kind"] == "sgp" and bet["ticket_price"] == ticket
    store.settle_game(engine, gid, 31, 20, AFTER)                      # home by 11 covers -3.5; 51 points beats 50.5
    assert bal(world) == 500_000 - 10_000 + payout_cents(10_000, american_to_decimal(ticket))
    assert store.reconcile(engine, cid) == []


def test_one_leg_loses_the_ticket_loses(world):
    engine, cid, gid = world
    place(world)
    store.settle_game(engine, gid, 31, 10, AFTER)                      # covers, but 41 is under 50.5
    assert bal(world) == 490_000


def test_a_pushed_leg_pays_the_other_leg_at_its_own_price(world):
    engine, cid, gid = world
    b = full_board(gid, spread=-3.0, total=50.0)
    lg = legs(board=b, game_id=gid)
    store.place_bet(engine, cid, "u1", Slip("sgp", lg, 10_000, "k2", sgp_price(lg, b, R)), NOW, b)
    store.settle_game(engine, gid, 28, 25, AFTER)                      # home by exactly 3 pushes -3.0; 53 beats 50.0
    assert bal(world) == 500_000 - 10_000 + payout_cents(10_000, american_to_decimal(-110))
    assert store.reconcile(engine, cid) == []


def test_a_retry_with_the_same_key_places_nothing_twice(world):
    engine, cid, gid = world
    a, _ = place(world)
    b, _ = place(world)
    assert a["id"] == b["id"] and bal(world) == 490_000


def test_a_stale_ticket_price_is_refused_and_nothing_is_written(world):
    engine, cid, gid = world
    with pytest.raises(SlipError) as e:
        place(world, ticket=-110)
    assert e.value.code == "PRICE_CHANGED" and bal(world) == 500_000
