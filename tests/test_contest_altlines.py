"""contest.altlines / contest.margin_model: contest-priced alt lines and their validation. Pure functions, no database."""
from datetime import datetime, timedelta, timezone

import pytest

from contest.altlines import alt_ladder, alt_price, on_grid, prob_to_american
from contest.margin_model import fair_prob_at
from contest.odds import OPPOSITE
from contest.rules import ContestRules
from contest.slips import GameInfo, Leg, Quote, Slip, SlipError, validate_slip

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=5)
GAMES = {1: GameInfo(KICK, "scheduled"), 2: GameInfo(KICK, "scheduled")}
FLAT = ContestRules(sport="ncaaf", cashout_sigmas={"spread": 13.5, "total": 13.0}, key_weights_override={0: 1.0})
KEYED = ContestRules(sport="ncaaf", cashout_sigmas={"spread": 13.5, "total": 13.0}, key_weights_override={3: 1.6, 7: 1.4})


def q(line, price=-110, at=NOW - timedelta(seconds=30)):
    return Quote(line, price, at)


def board(game, market, side, line, price=-110, opp_price=-110):
    opp_line = None if market == "ml" else (-line if market == "spread" else line)
    return {(game, market, side): q(line, price), (game, market, OPPOSITE[side]): q(opp_line, opp_price)}


def test_prob_to_american_rounds_to_five_and_never_inside_100():
    assert prob_to_american(0.5238) == -110 and prob_to_american(0.30) == 235 and prob_to_american(0.70) == -235
    assert prob_to_american(0.5) == 100 or prob_to_american(0.5) == -100
    assert abs(prob_to_american(0.5001)) >= 100 and abs(prob_to_american(0.4999)) >= 100


def test_grid_is_half_points():
    assert on_grid(-3.5) and on_grid(7.0) and not on_grid(-3.25)


def test_alt_price_is_monotone_in_the_bettors_favour():
    main, opp = q(-3.5), q(3.5)
    prices = {h: alt_price("spread", "home", main, opp, h, FLAT) for h in (-7.5, -5.5, -3.5, -1.5, 0.5)}
    imp = {h: (-p / (-p + 100) if p < 0 else 100 / (p + 100)) for h, p in prices.items()}
    ordered = [imp[h] for h in (-7.5, -5.5, -3.5, -1.5, 0.5)]
    assert ordered == sorted(ordered)                          # a better number for the bettor is a shorter price (higher implied)
    assert prices[-7.5] > 100 and prices[0.5] < -110           # laying 7.5 pays plus money; taking +0.5 costs more than the main


def test_alt_price_at_a_coin_flip_includes_the_margin_once():
    # a flat 50/50 main line with the default 5% margin: the contest quotes about -110 back at the same number's neighbour
    p = alt_price("spread", "home", q(-3.5), q(3.5), -3.0, FLAT)
    assert -130 <= p <= -105


def test_key_number_costs_more_than_the_same_distance_elsewhere():
    main, opp = q(-3.5), q(3.5)
    across = fair_prob_at("spread", "home", 0.5, -3.5, -2.5, 13.5, KEYED.key_weights()) - 0.5       # buying through the 3
    plain = fair_prob_at("spread", "home", 0.5, -3.5, -2.5, 13.5, None) - 0.5
    assert across > plain * 1.4
    far = fair_prob_at("spread", "home", 0.5, -11.5, -10.5, 13.5, KEYED.key_weights()) - fair_prob_at("spread", "home", 0.5, -11.5, -11.5, 13.5, KEYED.key_weights())
    assert far < across


def test_totals_and_both_sides_are_complements_before_the_margin():
    over = fair_prob_at("total", "over", 0.5, 50.5, 47.5, 13.0)
    under = fair_prob_at("total", "under", 0.5, 50.5, 47.5, 13.0)
    assert over + under == pytest.approx(1.0, abs=1e-6) and over > 0.5


def test_out_of_range_and_off_grid_lines_are_not_offered():
    main, opp = q(-3.5), q(3.5)
    assert alt_price("spread", "home", main, opp, -3.25, FLAT) is None               # off the half-point grid
    assert alt_price("spread", "home", main, opp, -3.5 - 14.5, FLAT) is None         # past the reach
    big, small = q(-20.5, -1000), q(20.5, 650)                                              # a heavy favourite: moving toward the dog
    assert alt_price("spread", "home", big, small, -10.5, FLAT) is None                      # p would pass 0.96, so the leg is withheld
    assert alt_price("ml", "home", main, opp, -3.5, FLAT) is None
    off = ContestRules(sport="ncaaf", alt_lines_enabled=False)
    assert alt_price("spread", "home", main, opp, -2.5, off) is None


def test_ladder_has_the_main_line_at_its_own_price_and_stays_in_reach():
    lad = alt_ladder("spread", "home", q(-3.5, -112), q(3.5, -108), FLAT)
    d = dict(lad)
    assert d[-3.5] == -112
    assert min(d) >= -3.5 - FLAT.alt_max_points and max(d) <= -3.5 + FLAT.alt_max_points
    assert all(abs(line * 2 - round(line * 2)) < 1e-9 for line in d)


def slip(legs, kind="straight", stake=10_000):
    return Slip(kind, tuple(legs), stake, "k")


def check(s, brd, rules=FLAT):
    validate_slip(s, rules, 500_000, 0, NOW, GAMES, brd)


def test_a_priced_alt_leg_places():
    brd = board(1, "spread", "home", -3.5)
    price = alt_price("spread", "home", brd[(1, "spread", "home")], brd[(1, "spread", "away")], -6.5, FLAT)
    check(slip([Leg(1, "spread", "home", -6.5, price)]), brd)


def test_an_alt_leg_with_the_wrong_price_is_price_changed_with_the_right_one():
    brd = board(1, "spread", "home", -3.5)
    good = alt_price("spread", "home", brd[(1, "spread", "home")], brd[(1, "spread", "away")], -6.5, FLAT)
    with pytest.raises(SlipError) as e:
        check(slip([Leg(1, "spread", "home", -6.5, good - 20)]), brd)
    assert e.value.code == "PRICE_CHANGED" and e.value.detail["price"] == good and e.value.detail["line"] == -6.5


def test_a_line_outside_the_ladder_is_bad_line():
    brd = board(1, "spread", "home", -3.5)
    with pytest.raises(SlipError) as e:
        check(slip([Leg(1, "spread", "home", -3.25, -110)]), brd)
    assert e.value.code == "BAD_LINE"


def test_alt_lines_never_ride_in_a_teaser():
    brd = {**board(1, "spread", "home", -3.5), **board(2, "spread", "home", -3.5)}
    t = slip([Leg(1, "spread", "home", -4.5, -110, 6.0), Leg(2, "spread", "home", -3.5, -110, 6.0)], kind="teaser")
    with pytest.raises(SlipError) as e:
        check(t, brd)
    assert e.value.code == "PRICE_CHANGED"


def test_alt_lines_are_off_when_the_flag_is_off():
    brd = board(1, "spread", "home", -3.5)
    off = ContestRules(sport="ncaaf", cashout_sigmas=FLAT.cashout_sigmas, key_weights_override={0: 1.0}, alt_lines_enabled=False)
    with pytest.raises(SlipError) as e:
        check(slip([Leg(1, "spread", "home", -6.5, 150)]), brd, off)
    assert e.value.code == "BAD_LINE"


def test_rules_json_round_trip_keeps_key_weights():
    again = ContestRules.from_json(KEYED.to_json())
    assert again.key_weights() == {3: 1.6, 7: 1.4} and again.alt_margin_bp == 500
