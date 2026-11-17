"""contest.odds / rules / slips / grading: pure functions, no database, no network."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from contest.grading import grade_leg, settle_bet
from contest.odds import american_to_decimal, parlay_decimal, payout_cents
from contest.rules import ContestRules
from contest.slips import (GameInfo, Leg, Quote, Slip, SlipError, adjusted_line, price_round_robin, price_slip, round_robin_slips,
                           validate_round_robin, validate_slip)

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
RULES = ContestRules(sport="ncaaf")
KICK = NOW + timedelta(hours=5)
GAMES = {1: GameInfo(KICK, "scheduled"), 2: GameInfo(KICK, "scheduled"), 3: GameInfo(KICK, "scheduled"), 4: GameInfo(KICK, "scheduled")}


def L(game=1, market="spread", side="home", line=-3.5, price=-110, pts=0.0):
    return Leg(game, market, side, line, price, pts)


def S(kind="straight", legs=None, stake=10_000, key="k1"):
    return Slip(kind, tuple(legs or [L()]), stake, key)


# ------------------------------------------------------------------------------------------------------------------ odds

def test_american_to_decimal():
    assert american_to_decimal(-110) == Decimal(1) + Decimal(100) / Decimal(110)
    assert american_to_decimal(150) == Decimal("2.5")
    assert american_to_decimal(-100) == Decimal(2) and american_to_decimal(100) == Decimal(2)
    for bad in (0, 99, -99, 50):
        with pytest.raises(ValueError):
            american_to_decimal(bad)


def test_payout_rounds_half_up_to_the_cent():
    assert payout_cents(10_000, american_to_decimal(-110)) == 19_091        # 100.00 at -110 returns 190.909... -> 190.91
    assert payout_cents(1, Decimal("1.5")) == 2                              # 1.5 cents rounds UP, never banker's rounding
    assert payout_cents(3, Decimal("1.5")) == 5                              # 4.5 -> 5
    assert payout_cents(10_000, parlay_decimal([-110, -110])) == 36_446


# ------------------------------------------------------------------------------------------------------------------ grading

@pytest.mark.parametrize("leg,home,away,expected", [
    (L(side="home", line=-3.5), 24, 20, "win"),             # wins by 4, covers -3.5
    (L(side="home", line=-3.5), 23, 20, "loss"),            # wins by 3, does not cover
    (L(side="home", line=-3.0), 23, 20, "push"),
    (L(side="away", line=3.5), 23, 20, "win"),              # away +3.5 loses by 3 -> covers
    (L(market="ml", side="home", line=None, price=-150), 17, 14, "win"),
    (L(market="ml", side="away", line=None, price=130), 17, 14, "loss"),
    (L(market="total", side="over", line=44.5), 24, 21, "win"),     # total 45
    (L(market="total", side="under", line=45.0), 24, 21, "push"),
    (L(market="total", side="under", line=45.5), 24, 21, "win"),
])
def test_grade_leg(leg, home, away, expected):
    assert grade_leg(leg, home, away) == expected


def test_teaser_moves_the_line_for_the_bettor():
    fav = L(side="home", line=-7.0, pts=6.0)
    dog = L(game=2, side="away", line=2.5, pts=6.0)
    over = L(game=3, market="total", side="over", line=50.5, pts=6.0)
    under = L(game=4, market="total", side="under", line=50.5, pts=6.0)
    assert adjusted_line(fav) == -1.0 and adjusted_line(dog) == 8.5
    assert adjusted_line(over) == 44.5 and adjusted_line(under) == 56.5
    assert grade_leg(fav, 21, 20) == "push"                  # wins by 1, teased to -1.0
    assert grade_leg(fav, 24, 20) == "win"
    assert grade_leg(over, 24, 21) == "win"                  # 45 > 44.5


# ------------------------------------------------------------------------------------------------------------------ settlement

def test_straight_settlement():
    leg = L(price=-110)
    assert settle_bet("straight", 10_000, [(leg, "win")], RULES).payout_cents == 19_091
    assert settle_bet("straight", 10_000, [(leg, "loss")], RULES).status == "lost"
    assert settle_bet("straight", 10_000, [(leg, "push")], RULES) == settle_bet("straight", 10_000, [(leg, "push")], RULES)
    assert settle_bet("straight", 10_000, [(leg, "push")], RULES).payout_cents == 10_000
    assert settle_bet("straight", 10_000, [(leg, "void")], RULES).status == "void"
    assert settle_bet("straight", 10_000, [(leg, None)], RULES) is None


def test_parlay_loss_is_immediate_pending_waits_push_reprices():
    a, b, c = L(1, price=-110), L(2, price=150), L(3, price=-120)
    assert settle_bet("parlay", 10_000, [(a, "loss"), (b, None), (c, None)], RULES).status == "lost"   # early loss
    assert settle_bet("parlay", 10_000, [(a, "win"), (b, None)], RULES) is None
    both = settle_bet("parlay", 10_000, [(a, "win"), (b, "win")], RULES)
    assert both.status == "won" and both.payout_cents == payout_cents(10_000, parlay_decimal([-110, 150]))
    pushed = settle_bet("parlay", 10_000, [(a, "win"), (b, "push")], RULES)           # drops to a straight on a
    assert pushed.payout_cents == payout_cents(10_000, american_to_decimal(-110))
    assert settle_bet("parlay", 10_000, [(a, "push"), (b, "void")], RULES) == settle_bet("parlay", 10_000, [(a, "push"), (b, "void")], RULES)
    assert settle_bet("parlay", 10_000, [(a, "push"), (b, "void")], RULES).payout_cents == 10_000
    assert settle_bet("parlay", 10_000, [(a, "void"), (b, "void")], RULES).status == "void"


def test_teaser_settlement_uses_the_table_and_reduces_on_pushes():
    legs3 = [L(i, pts=6.0) for i in (1, 2, 3)]
    win3 = settle_bet("teaser", 10_000, [(x, "win") for x in legs3], RULES)
    assert win3.payout_cents == payout_cents(10_000, american_to_decimal(RULES.teaser_price(6.0, 3)))
    two_left = settle_bet("teaser", 10_000, [(legs3[0], "win"), (legs3[1], "win"), (legs3[2], "push")], RULES)
    assert two_left.payout_cents == payout_cents(10_000, american_to_decimal(RULES.teaser_price(6.0, 2)))
    one_left = settle_bet("teaser", 10_000, [(legs3[0], "win"), (legs3[1], "push"), (legs3[2], "void")], RULES)
    assert one_left.status == "push" and one_left.payout_cents == 10_000          # a one-leg teaser is a push
    assert settle_bet("teaser", 10_000, [(legs3[0], "loss"), (legs3[1], None), (legs3[2], None)], RULES).status == "lost"


# ------------------------------------------------------------------------------------------------------------------ validation

def board_for(*legs, age_s=60, price=None, line="same"):
    return {(g.game_id, g.market, g.side): Quote(g.line if line == "same" else line, g.price if price is None else price,
                                                 NOW - timedelta(seconds=age_s)) for g in legs}


def ok(slip, balance=500_000, open_risk=0, board="auto", games=GAMES, now=NOW):
    b = board_for(*slip.legs) if board == "auto" else board
    return validate_slip(slip, RULES, balance, open_risk, now, games, b)


def code(slip, **kw):
    with pytest.raises(SlipError) as e:
        ok(slip, **kw)
    return e.value.code


def test_valid_slips_pass():
    ok(S())
    ok(S("parlay", [L(1), L(2, side="away", line=3.5)]))
    ok(S("teaser", [L(1, pts=6.0), L(2, market="total", side="over", line=47.5, pts=6.0)]))


def test_structure_rules():
    assert code(S("straight", [L(1), L(2)])) == "LEG_COUNT"
    assert code(S("parlay", [L(1)])) == "LEG_COUNT"
    assert code(S("parlay", [L(i) for i in range(1, 10)]), games={i: GameInfo(KICK, "scheduled") for i in range(1, 10)}) == "LEG_COUNT"
    assert code(S("parlay", [L(1), L(1, market="total", side="over", line=40.5)])) == "DUP_GAME"
    assert code(S("teaser", [L(1, pts=6.0), L(2, market="ml", side="home", line=None, price=-150, pts=6.0)])) == "TEASER_MARKET"
    assert code(S("teaser", [L(1, pts=5.0), L(2, pts=5.0)])) == "TEASER_POINTS"          # 5 points is a basketball table
    assert code(S("teaser", [L(1, pts=6.0), L(2, pts=6.5)])) == "TEASER_POINTS"          # mixed points
    assert code(S("parlay", [L(1, pts=6.0), L(2)])) == "TEASER_POINTS"
    assert code(S("straight", [L(market="ml", side="home", line=-3.5)])) == "BAD_LEG"
    assert code(S("straight", [L(market="total", side="home", line=40.5)])) == "BAD_LEG"
    assert code(S(kind="exotic")) == "BAD_KIND"


def test_stake_rules():
    assert code(S(stake=2_499)) == "STAKE_MIN"
    assert code(S(stake=600_000)) == "INSUFFICIENT"
    assert code(S(stake=125_001)) == "STAKE_MAX"                 # 25% of 5,000.00 is 1,250.00
    ok(S(stake=125_000))
    ok(S(stake=125_000), balance=500_000, open_risk=300_000)     # default open-risk cap is off
    ok(S(stake=2_500), balance=3_000)                            # the minimum stake is always allowed, even above the % cap
    assert code(S(stake=3_000), balance=3_000) == "STAKE_MAX"
    assert code(S(stake=2_500), balance=2_000) == "INSUFFICIENT"


def test_open_risk_cap():
    rules = ContestRules(sport="ncaaf", max_open_risk_bp=5_000)
    validate_slip(S(stake=100_000), rules, 400_000, 100_000, NOW, GAMES, None)       # open 200k of 500k total = 40%: fine
    with pytest.raises(SlipError) as e:
        validate_slip(S(stake=100_000), rules, 400_000, 250_000, NOW, GAMES, None)   # open 350k of 650k = 54%: refused
    assert e.value.code == "OPEN_RISK"


def test_closing_buffer_and_game_state():
    soon = {1: GameInfo(NOW + timedelta(minutes=14), "scheduled")}
    assert code(S(), games=soon) == "GAME_CLOSED"
    edge = {1: GameInfo(NOW + timedelta(minutes=15), "scheduled")}
    assert code(S(), games=edge) == "GAME_CLOSED"                # closes AT kickoff minus buffer
    assert code(S(), games={1: GameInfo(KICK, "final")}) == "GAME_CLOSED"
    assert code(S(), games={}) == "NO_GAME"
    ok(S(), games={1: GameInfo(NOW + timedelta(minutes=16), "scheduled")})


def test_board_checks():
    s = S()
    assert code(s, board={}) == "NO_QUOTE"
    assert code(s, board=board_for(*s.legs, age_s=1_801)) == "QUOTE_STALE"
    ok(s, board=board_for(*s.legs, age_s=1_800))
    with pytest.raises(SlipError) as e:
        ok(s, board=board_for(*s.legs, price=-125))
    assert e.value.code == "PRICE_CHANGED" and e.value.detail["price"] == -125
    assert code(s, board=board_for(*s.legs, line=-4.5)) == "PRICE_CHANGED"
    t = S("teaser", [L(1, pts=6.0), L(2, pts=6.0)])
    ok(t, board=board_for(*t.legs, price=-125))                  # teaser leg price is ignored, only the line matters


def test_price_slip():
    assert price_slip(S(stake=10_000), RULES) == 19_091
    assert price_slip(S("parlay", [L(1, price=-110), L(2, price=-110)], stake=10_000), RULES) == 36_446
    t = S("teaser", [L(1, pts=6.0), L(2, pts=6.0)], stake=12_000)
    assert price_slip(t, RULES) == payout_cents(12_000, american_to_decimal(-120))


def test_rules_json_round_trip():
    again = ContestRules.from_json(RULES.to_json())
    assert again.teaser_price(6.0, 3) == RULES.teaser_price(6.0, 3) and again.min_stake_cents == RULES.min_stake_cents
    assert ContestRules(sport="ncaab").teaser_price(4.0, 2) == -120


# ------------------------------------------------------------------------------------------------------------------ round robins

G8 = {i: GameInfo(KICK, "scheduled") for i in range(1, 9)}


def picks(n, price=-110):
    return [L(i, price=price) for i in range(1, n + 1)]


def rr(n=4, sizes=(2,), unit=5_000, balance=500_000, open_risk=0, board="auto", games=G8, now=NOW, rules=RULES):
    pk = picks(n)
    b = board_for(*pk) if board == "auto" else board
    return validate_round_robin(pk, sizes, unit, rules, balance, open_risk, now, games, b)


def rr_code(**kw):
    with pytest.raises(SlipError) as e:
        rr(**kw)
    return e.value.code


def test_round_robin_expands_to_every_combination():
    assert len(rr(4, (2,))) == 6                      # by 2s from 4 picks
    assert len(rr(4, (3,))) == 4
    assert len(rr(5, (2, 3))) == 10 + 10              # by 2s and 3s from 5 picks
    assert len(rr(8, (4,), unit=2_500, rules=ContestRules(sport="ncaaf", max_stake_bp=10_000))) == 70
    slips = rr(4, (2, 3))
    assert all(sl.kind == "parlay" and sl.stake_cents == 5_000 for sl in slips)
    assert len({sl.idempotency_key for sl in slips}) == len(slips) == 10
    assert {tuple(g.game_id for g in sl.legs) for sl in round_robin_slips(picks(3), [2], 100, "k")} == {(1, 2), (1, 3), (2, 3)}


def test_round_robin_prices_as_the_sum_of_its_parlays():
    slips = rr(3, (2,), unit=10_000)                  # three 2-leg parlays at -110, $100 each
    assert price_round_robin(slips, RULES) == 3 * 36_446


def test_round_robin_limits():
    assert rr_code(n=2, sizes=(2,)) == "RR_PICKS"                      # fewer than 3 picks
    assert rr_code(n=9, sizes=(2,), games={i: GameInfo(KICK, "scheduled") for i in range(1, 10)}) == "RR_PICKS"
    assert rr_code(n=4, sizes=(1,)) == "RR_SIZES"                      # a "by 1s" is just straights
    assert rr_code(n=4, sizes=(5,)) == "RR_SIZES"
    assert rr_code(n=4, sizes=()) == "RR_SIZES"
    assert rr_code(n=8, sizes=(2, 3, 4, 5)) == "RR_TOO_MANY"           # 28 + 56 + 70 + 56 parlays
    assert rr_code(n=4, sizes=(2,), unit=2_499) == "STAKE_MIN"
    pk = [L(1), L(1, market="total", side="over", line=40.5), L(2)]
    with pytest.raises(SlipError) as e:
        validate_round_robin(pk, (2,), 5_000, RULES, 500_000, 0, NOW, G8, None)
    assert e.value.code == "DUP_GAME"


def test_round_robin_stake_limits_apply_to_the_total():
    assert rr_code(n=4, sizes=(2,), unit=50_000, balance=500_000) == "STAKE_MAX"       # 6 x 500 = 3,000 > 25% of 5,000 (1,250)
    rr(4, (2,), unit=20_000, balance=500_000)                                          # 6 x 200 = 1,200 <= 1,250
    assert rr_code(n=4, sizes=(2,), unit=20_000, balance=100_000) == "INSUFFICIENT"
    rules = ContestRules(sport="ncaaf", max_stake_bp=10_000, max_open_risk_bp=3_000)
    assert rr_code(n=4, sizes=(2,), unit=10_000, balance=400_000, open_risk=100_000, rules=rules) == "OPEN_RISK"


def test_round_robin_checks_every_pick_against_the_board_and_clock():
    assert rr_code(n=4, sizes=(2,), board={}) == "NO_QUOTE"
    assert rr_code(n=4, sizes=(2,), now=KICK) == "GAME_CLOSED"
    pk = picks(4)
    stale = board_for(*pk)
    stale[(3, "spread", "home")] = Quote(-3.5, -125, NOW)
    assert rr_code(n=4, sizes=(2,), board=stale) == "PRICE_CHANGED"
