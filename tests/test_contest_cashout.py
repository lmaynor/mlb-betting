"""contest.cashout: valuation of an open bet from the current board. Pure functions, no database."""
import math
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

import pytest

from contest.cashout import OPPOSITE, CashoutRefused, cashout_offer, devig, leg_probability
from contest.rules import ContestRules
from contest.slips import GameInfo, Leg, Quote

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=5)
N = NormalDist()
FLAT = ContestRules(sport="ncaaf", cashout_sigmas={"spread": 13.5, "total": 13.0}, key_weights_override={0: 1.0})   # no key numbers: a plain normal
RULES = FLAT                                             # 5% margin, football sigmas 13.5 (spread) / 13.0 (total), plain normal
GAMES = {1: GameInfo(KICK, "scheduled"), 2: GameInfo(KICK, "scheduled")}


def board(game_id, market, side, line, price, opp_price, at=NOW):
    """Both sides of one market: `side` at (line, price), the other side at the mirrored line and `opp_price`."""
    opp_line = None if market == "ml" else (-line if market == "spread" else line)
    t = at - timedelta(seconds=30)
    return {(game_id, market, side): Quote(line, price, t), (game_id, market, OPPOSITE[side]): Quote(opp_line, opp_price, t)}


def L(game=1, market="spread", side="home", line=-3.5, price=-110):
    return Leg(game, market, side, line, price)


def offer(kind="straight", stake=10_000, legs=None, brd=None, rules=RULES, games=GAMES, now=NOW):
    legs = legs or [L()]
    return cashout_offer(kind, stake, legs, brd if brd is not None else board(1, "spread", "home", -3.5, -110, -110), rules, games, now)


def refusal(**kw):
    with pytest.raises(CashoutRefused) as e:
        offer(**kw)
    return e.value.code


def test_devig_removes_the_vig():
    assert devig(-110, -110) == pytest.approx(0.5)
    assert devig(-150, 130) == pytest.approx(0.6 / (0.6 + 100 / 230))
    assert devig(-150, 130) + devig(130, -150) == pytest.approx(1.0)


def test_leg_probability_at_the_same_line_is_the_devigged_price():
    b = board(1, "spread", "home", -3.5, -125, 105)
    assert leg_probability(L(price=-125), b, 13.5, FLAT) == pytest.approx(devig(-125, 105))
    ml = board(1, "ml", "home", None, -200, 170)
    assert leg_probability(Leg(1, "ml", "home", None, -200), ml, 13.5, FLAT) == pytest.approx(devig(-200, 170))


def test_a_held_line_that_beats_the_board_lifts_the_probability():
    now_board = board(1, "spread", "home", -6.5, -110, -110)               # home is now -6.5; the player holds home -3.5 (3 points better)
    p = leg_probability(L(line=-3.5), now_board, 13.5, FLAT)
    assert p == pytest.approx(N.cdf(3 / 13.5), abs=0.01) and p > 0.5
    worse = leg_probability(L(line=-9.5), now_board, 13.5, FLAT)                  # holds -9.5 against a board of -6.5: 3 points worse
    assert worse == pytest.approx(N.cdf(-3 / 13.5), abs=0.01) and worse < 0.5


def test_total_orientation_over_and_under():
    b_over = board(1, "total", "over", 49.5, -110, -110)                   # total is now 49.5
    assert leg_probability(Leg(1, "total", "over", 47.5, -110), b_over, 13.0, FLAT) == pytest.approx(N.cdf(2 / 13.0), abs=0.01)     # held over 47.5: easier than 49.5
    assert leg_probability(Leg(1, "total", "under", 47.5, -110), board(1, "total", "under", 49.5, -110, -110), 13.0, FLAT) == pytest.approx(N.cdf(-2 / 13.0), abs=0.01)


def test_spread_orientation_for_the_away_side():
    b = board(1, "spread", "away", 6.5, -110, -110)                          # away +6.5 now; the player holds away +9.5 (3 points better)
    assert leg_probability(Leg(1, "spread", "away", 9.5, -110), b, 13.5, FLAT) == pytest.approx(N.cdf(3 / 13.5), abs=0.01)


def test_straight_at_a_flat_board_pays_the_fair_value_less_the_margin():
    o = offer()
    assert o.payout_cents == 19_091 and o.win_prob == pytest.approx(0.5)
    assert o.cents == math.floor(19_091 * 0.5 * 0.95) == 9_068                 # below the $100 stake: the vig is already in the payout, plus the margin


def test_a_line_that_moved_in_your_favour_can_cash_out_above_the_stake():
    o = offer(brd=board(1, "spread", "home", -6.5, -110, -110))              # bought -3.5, now -6.5
    assert o.cents == pytest.approx(19_091 * N.cdf(3 / 13.5) * 0.95, abs=100)    # the model is a discretised normal: within a dollar
    assert o.cents > 10_000                                                  # closing-line value shows up as a profit
    against = offer(brd=board(1, "spread", "home", -0.5, -110, -110))        # bought -3.5, now -0.5: the board moved against the holder
    assert against.cents < 9_068


def test_parlay_multiplies_the_legs_and_takes_the_margin_once():
    legs = [L(1), L(2)]
    brd = {**board(1, "spread", "home", -3.5, -110, -110), **board(2, "spread", "home", -3.5, -110, -110)}
    o = offer("parlay", legs=legs, brd=brd)
    assert o.payout_cents == 36_446 and o.win_prob == pytest.approx(0.25)
    assert o.cents == math.floor(36_446 * 0.25 * 0.95)


def test_the_value_is_never_above_the_payout():
    rules = ContestRules(sport="ncaaf", cashout_margin_bp=0)
    o = offer(brd=board(1, "spread", "home", -60.5, -110, -110), rules=rules)  # a 57-point gift: p is about 1
    assert o.win_prob > 0.999 and o.cents <= o.payout_cents


def test_margin_is_configurable():
    assert offer(rules=ContestRules(sport="ncaaf", cashout_margin_bp=1_000)).cents == math.floor(19_091 * 0.5 * 0.90)


def test_basketball_uses_its_own_sigmas():
    rules = ContestRules(sport="ncaab")
    assert rules.cashout_sigma("spread") == 11.1 and rules.cashout_sigma("total") == 17.2
    assert ContestRules(sport="ncaaf").cashout_sigma("total") == 15.5


def test_refusals():
    assert refusal(kind="teaser", legs=[L(1), L(2)]) == "TEASER"
    assert refusal(rules=ContestRules(sport="ncaaf", cashout_enabled=False)) == "DISABLED"
    assert refusal(now=KICK - timedelta(minutes=10)) == "GAME_CLOSED"                      # inside the 15-minute close
    assert refusal(games={1: GameInfo(KICK, "live")}) == "GAME_CLOSED"
    assert refusal(games={}) == "GAME_CLOSED"
    assert refusal(brd={(1, "spread", "home"): Quote(-3.5, -110, NOW)}) == "NO_QUOTE"       # the other side is missing: cannot de-vig
    stale = board(1, "spread", "home", -3.5, -110, -110, at=NOW - timedelta(minutes=45))
    assert refusal(brd=stale) == "QUOTE_STALE"


def test_a_moneyline_bet_cashes_out_without_any_line_model():
    held = Leg(1, "ml", "home", None, -150)                                  # bought home at -150
    now_board = board(1, "ml", "home", None, -200, 170)                      # the market now makes home a bigger favourite
    o = offer(legs=[held], brd=now_board)
    p = devig(-200, 170)
    assert o.win_prob == pytest.approx(p) and p > devig(-150, 130)
    assert o.payout_cents == 16_667 and o.cents == math.floor(16_667 * p * 0.95)
    mixed = offer("parlay", legs=[held, L(2)], brd={**now_board, **board(2, "spread", "home", -3.5, -110, -110)})   # a moneyline leg inside a parlay too
    assert mixed.win_prob == pytest.approx(p * 0.5)
