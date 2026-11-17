"""contest.blackjack (pure rules) and contest.lifeline (money, eligibility, the $100 cap). Decks are fixed so every rule is exact."""
import random
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from contest import blackjack as bj
from contest import lifeline, store
from contest.rules import ContestRules
from contest.schema import ensure_schema

NOW = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)


def stack(*cards):
    """A deck whose cards come out in the order given: player, dealer up card, player, dealer hole card, then the draws."""
    return list(cards)


# ------------------------------------------------------------------------------------------------------------------ pure rules

def test_hand_values_and_aces():
    assert bj.hand_value(["AS", "KS"]) == (21, True)
    assert bj.hand_value(["AS", "AH"]) == (12, True)
    assert bj.hand_value(["AS", "6H", "TD"]) == (17, False)            # the ace has to count 1
    assert bj.hand_value(["TS", "6H", "5D"]) == (21, False)
    assert bj.hand_value(["9S", "9H", "9D"]) == (27, False)
    assert bj.is_blackjack(["AS", "KD"]) and not bj.is_blackjack(["TS", "6H", "5D"])


def test_new_deck_is_52_unique_cards_and_shuffles_from_the_rng():
    d = bj.new_deck(random.Random(1))
    assert len(d) == 52 == len(set(d))
    assert d == bj.new_deck(random.Random(1)) and d != bj.new_deck(random.Random(2))
    assert len(bj.new_deck()) == 52                                       # the system RNG path


def test_blackjack_pays_three_to_two_and_a_dealer_blackjack_ends_the_hand():
    h = bj.deal(1_000, stack("AS", "9H", "KS", "8D"))
    assert h.done and h.outcome == "blackjack" and bj.payout_cents(h) == 2_500
    h = bj.deal(1_000, stack("9S", "AH", "8S", "KD"))                    # dealer shows an ace and has the ten: the player loses at once
    assert h.done and h.outcome == "lose" and bj.payout_cents(h) == 0
    h = bj.deal(1_000, stack("AS", "AH", "KS", "KD"))                    # both have it
    assert h.outcome == "push" and bj.payout_cents(h) == 1_000


def test_dealer_draws_to_17_and_stands_on_soft_17():
    h = bj.stand(bj.deal(1_000, stack("TS", "6H", "8D", "TD", "5C")))    # player 18; dealer 16 draws a 5 for 21
    assert h.outcome == "lose" and bj.hand_value(h.dealer)[0] == 21
    h = bj.stand(bj.deal(1_000, stack("TS", "AH", "8D", "6C", "9S")))    # dealer soft 17 stands (the 9 is never drawn); 18 beats 17
    assert h.dealer == ["AH", "6C"] and h.outcome == "win" and bj.payout_cents(h) == 2_000
    h = bj.stand(bj.deal(1_000, stack("TS", "6H", "8D", "TD", "TC")))    # dealer busts on 26
    assert h.outcome == "win"
    h = bj.stand(bj.deal(1_000, stack("TS", "8H", "8D", "TD")))          # 18 against 18
    assert h.outcome == "push" and bj.payout_cents(h) == 1_000


def test_hit_bust_and_automatic_stand_on_21():
    h = bj.hit(bj.deal(1_000, stack("TS", "9H", "6D", "8D", "TC")))      # 16 + 10
    assert h.done and h.outcome == "bust" and bj.payout_cents(h) == 0
    h = bj.hit(bj.deal(1_000, stack("5S", "TH", "6D", "7D", "TC")))      # 11 + 10 = 21: the dealer plays at once (17), the player wins
    assert h.done and h.outcome == "win"
    with pytest.raises(bj.BlackjackError) as e:
        bj.hit(h)
    assert e.value.code == "HAND_OVER"


def test_double_down():
    h = bj.double_down(bj.deal(1_000, stack("5S", "TH", "6D", "7D", "TC")))   # 11 + 10 = 21 against 17, stake doubled
    assert h.doubled and h.total_stake == 2_000 and h.outcome == "win" and bj.payout_cents(h) == 4_000
    h = bj.double_down(bj.deal(1_000, stack("5S", "TH", "6D", "7D", "2C")))   # 13 against 17
    assert h.outcome == "lose" and bj.payout_cents(h) == 0
    h = bj.double_down(bj.deal(1_000, stack("TS", "TH", "6D", "7D", "TC")))   # 16 + 10 busts after doubling
    assert h.outcome == "bust"
    started = bj.hit(bj.deal(1_000, stack("2S", "TH", "3D", "7D", "2C", "2D")))
    assert not bj.can_double(started)
    with pytest.raises(bj.BlackjackError) as e:
        bj.double_down(started)
    assert e.value.code == "CANNOT_DOUBLE"


def test_payout_needs_a_finished_hand():
    with pytest.raises(bj.BlackjackError):
        bj.payout_cents(bj.deal(1_000, stack("TS", "6H", "8D", "TD", "5C")))


def test_the_view_never_shows_the_hole_card_or_the_deck_until_the_hand_is_over():
    h = bj.deal(1_000, stack("TS", "6H", "8D", "TD", "5C"))
    v = bj.view(h)
    assert v["dealer"] == ["6H", "??"] and v["dealer_total"] == 6 and "deck" not in v and not v["done"]
    bj.stand(h)
    assert bj.view(h)["dealer"] == ["6H", "TD", "5C"] and bj.view(h)["outcome"] == "lose"
    assert bj.Hand.from_json(h.to_json()).player == h.player


# ------------------------------------------------------------------------------------------------------------------ the lifeline

@pytest.fixture
def table(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    ensure_schema(engine)
    cid = store.create_contest(engine, "bj", "BJ", ContestRules(sport="ncaaf"), NOW)
    store.create_invite(engine, cid, "W", 10, NOW)
    eid = store.join_contest(engine, cid, "u1", "Alex", "W", NOW)
    return engine, cid, eid


def set_balance(t, cents):
    engine, _, eid = t
    with engine.connect() as c:
        have = c.execute(text("SELECT balance_cents FROM contest_entry WHERE id = :e"), {"e": eid}).scalar_one()
    store.adjust(engine, eid, cents - int(have), "test: set balance", NOW)


def bal(t):
    with t[0].connect() as c:
        return int(c.execute(text("SELECT balance_cents FROM contest_entry WHERE id = :e"), {"e": t[2]}).scalar_one())


def books(t):
    assert store.reconcile(t[0], t[1]) == []


def test_the_table_opens_only_under_the_threshold_and_counts_stakes_at_risk(table):
    engine, cid, _ = table
    assert lifeline.lifeline_status(engine, cid, "u1")["visible"] is False                    # $5,000: no table
    with pytest.raises(store.ContestError) as e:
        lifeline.start_hand(engine, cid, "u1", 500, NOW, deck=stack("TS", "6H", "8D", "TD", "5C"))
    assert e.value.code == "NOT_ELIGIBLE"
    set_balance(table, 9_999)
    s = lifeline.lifeline_status(engine, cid, "u1")
    assert s["eligible"] and s["visible"] and s["threshold_cents"] == 10_000 and s["max_wager_cents"] == 9_999
    set_balance(table, 10_000)
    assert lifeline.lifeline_status(engine, cid, "u1")["eligible"] is False                  # exactly $100 is not "under"
    set_balance(table, 5_000)
    with engine.begin() as c:                                                                   # $50 in hand but $60 riding on a bet: a $110 bankroll
        c.execute(text("INSERT INTO contest_game (contest_id, sport, espn_event_id, commence_time) VALUES (:c, 'ncaaf', 'e', '2027-01-01T00:00:00+00:00')"), {"c": cid})
        c.execute(text("INSERT INTO contest_bet (entry_id, kind, stake_cents, status, idempotency_key, placed_at) VALUES (:e, 'straight', 6000, 'pending', 'k', 't')"), {"e": table[2]})
    assert lifeline.lifeline_status(engine, cid, "u1")["eligible"] is False


def test_a_hand_stakes_through_the_ledger_and_hides_the_hole_card(table):
    engine, cid, _ = table
    set_balance(table, 6_000)
    r = lifeline.start_hand(engine, cid, "u1", 2_000, NOW, deck=stack("TS", "6H", "8D", "TD", "5C"))
    assert bal(table) == 4_000 and r["hand"]["dealer"] == ["6H", "??"] and not r["hand"]["done"]
    assert "deck" not in r["hand"] and r["visible"]
    with pytest.raises(store.ContestError) as e:
        lifeline.start_hand(engine, cid, "u1", 500, NOW, deck=stack("TS", "6H", "8D", "TD", "5C"))
    assert e.value.code == "HAND_OPEN"
    again = lifeline.lifeline_status(engine, cid, "u1")                                        # reload: the same hand, still hiding the hole card
    assert again["hand"]["dealer"] == ["6H", "??"] and again["hand"]["player"] == ["TS", "8D"]
    books(table)


def test_a_win_pays_through_the_ledger_and_a_loss_costs_the_stake(table):
    engine, cid, _ = table
    set_balance(table, 6_000)
    lifeline.start_hand(engine, cid, "u1", 2_000, NOW, deck=stack("TS", "7H", "TD", "TD"))     # 20 against 17
    r = lifeline.act(engine, cid, "u1", "stand", NOW)
    assert r["hand"]["outcome"] == "win" and r["hand"]["credited_cents"] == 4_000 and not r["hand"]["capped"]
    assert bal(table) == 4_000 + 4_000
    books(table)
    set_balance(table, 6_000)
    lifeline.start_hand(engine, cid, "u1", 2_000, NOW, deck=stack("TS", "TH", "8D", "9D"))     # 18 against 19
    r = lifeline.act(engine, cid, "u1", "stand", NOW)
    assert r["hand"]["outcome"] == "lose" and r["hand"]["credited_cents"] == 0 and bal(table) == 4_000
    books(table)


def test_winnings_never_lift_the_bankroll_above_the_threshold(table):
    engine, cid, _ = table
    set_balance(table, 9_000)                                                                   # bankroll $90
    lifeline.start_hand(engine, cid, "u1", 5_000, NOW, deck=stack("TS", "7H", "TD", "TD"))     # stake $50, 20 beats 17: payout $100
    r = lifeline.act(engine, cid, "u1", "stand", NOW)
    assert r["hand"]["payout_cents"] == 10_000 and r["hand"]["credited_cents"] == 6_000 and r["hand"]["capped"]
    assert bal(table) == 10_000                                                                 # $40 left + $60 credited = exactly the $100 cap
    assert r["visible"] is False and r["eligible"] is False                                     # back to $100: the table closes
    books(table)


def test_blackjack_is_paid_three_to_two_but_capped_too(table):
    engine, cid, _ = table
    set_balance(table, 5_000)
    r = lifeline.start_hand(engine, cid, "u1", 4_000, NOW, deck=stack("AS", "9H", "KS", "8D"))  # natural: payout $100, bankroll after stake $10
    assert r["hand"]["outcome"] == "blackjack" and r["hand"]["done"] and r["hand"]["payout_cents"] == 10_000
    assert r["hand"]["credited_cents"] == 9_000 and r["hand"]["capped"] and bal(table) == 10_000
    books(table)


def test_double_down_debits_a_second_stake_and_refuses_without_the_money(table):
    engine, cid, _ = table
    set_balance(table, 6_000)
    lifeline.start_hand(engine, cid, "u1", 2_000, NOW, deck=stack("5S", "TH", "6D", "7D", "TC"))
    r = lifeline.act(engine, cid, "u1", "double", NOW)                                          # 11 + 10 = 21 against 17: wins $80 total
    assert r["hand"]["doubled"] and r["hand"]["stake_cents"] == 4_000 and r["hand"]["outcome"] == "win" and r["hand"]["payout_cents"] == 8_000
    assert bal(table) == 10_000                                                                 # $60 - $40 staked = $20, plus the $80 payout = exactly the $100 cap
    books(table)
    set_balance(table, 2_500)
    lifeline.start_hand(engine, cid, "u1", 2_000, NOW, deck=stack("5S", "TH", "6D", "7D", "TC"))
    with pytest.raises(store.ContestError) as e:
        lifeline.act(engine, cid, "u1", "double", NOW)                                          # only $5 left, a double needs $20
    assert e.value.code == "INSUFFICIENT"
    r = lifeline.act(engine, cid, "u1", "hit", NOW)
    assert r["hand"]["done"]                                                                    # 21 stands automatically
    with pytest.raises(store.ContestError) as e2:
        lifeline.act(engine, cid, "u1", "hit", NOW)
    assert e2.value.code == "NO_OPEN_HAND"
    books(table)


def test_stake_and_action_validation(table):
    engine, cid, _ = table
    set_balance(table, 5_000)
    for bad in (99, 5_001, 0, -5):
        with pytest.raises(store.ContestError) as e:
            lifeline.start_hand(engine, cid, "u1", bad, NOW, deck=stack("TS", "6H", "8D", "TD", "5C"))
        assert e.value.code == "STAKE_INVALID"
    with pytest.raises(store.ContestError) as e2:
        lifeline.act(engine, cid, "u1", "split", NOW)
    assert e2.value.code == "BAD_ACTION"
    with pytest.raises(store.ContestError) as e3:
        lifeline.act(engine, cid, "u1", "hit", NOW)
    assert e3.value.code == "NO_OPEN_HAND"
    assert bal(table) == 5_000


def test_the_lifeline_can_be_switched_off(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    ensure_schema(engine)
    cid = store.create_contest(engine, "off", "Off", ContestRules(sport="ncaaf", blackjack_enabled=False), NOW)
    store.create_invite(engine, cid, "W", 1, NOW)
    eid = store.join_contest(engine, cid, "u1", "Alex", "W", NOW)
    store.adjust(engine, eid, -499_000, "test", NOW)
    assert lifeline.lifeline_status(engine, cid, "u1")["visible"] is False
    with pytest.raises(store.ContestError) as e:
        lifeline.start_hand(engine, cid, "u1", 500, NOW, deck=stack("TS", "6H", "8D", "TD", "5C"))
    assert e.value.code == "LIFELINE_OFF"


def test_a_player_is_out_only_when_they_cannot_afford_a_chip(table):
    engine, cid, eid = table
    set_balance(table, 1_700)                                                                   # $17: cannot place a $25 bet, can still play blackjack
    lifeline.start_hand(engine, cid, "u1", 1_000, NOW, deck=stack("TS", "TH", "8D", "9D"))
    lifeline.act(engine, cid, "u1", "stand", NOW)                                               # loses $10 -> $7
    assert lifeline.lifeline_status(engine, cid, "u1")["entry_status"] == "active" and bal(table) == 700
    lifeline.start_hand(engine, cid, "u1", 650, NOW, deck=stack("TS", "TH", "8D", "9D"))
    assert lifeline.lifeline_status(engine, cid, "u1")["entry_status"] == "active"             # $0.50 left but a hand is open: not out yet
    r = lifeline.act(engine, cid, "u1", "stand", NOW)                                           # loses it
    assert bal(table) == 50 and r["entry_status"] == "eliminated"                               # under one $1 chip, nothing pending: out
    with pytest.raises(store.ContestError) as e:
        lifeline.start_hand(engine, cid, "u1", 100, NOW, deck=stack("TS", "TH", "8D", "9D"))
    assert e.value.code in ("ELIMINATED", "STAKE_INVALID")
    books(table)


def test_a_climb_back_from_a_dollar_to_the_threshold_keeps_the_books_balanced(table):
    """Fifty random hands from $50: the balance is never negative, never above the cap from blackjack alone, and the ledger always agrees."""
    engine, cid, _ = table
    set_balance(table, 5_000)
    rng = random.Random(11)
    for _ in range(50):
        if not lifeline.lifeline_status(engine, cid, "u1")["eligible"] or bal(table) < 100:
            break
        stake = min(bal(table), 500)
        r = lifeline.start_hand(engine, cid, "u1", stake, NOW, rng=rng)
        while r["hand"] and not r["hand"]["done"]:
            r = lifeline.act(engine, cid, "u1", rng.choice(["hit", "stand"]), NOW)
        assert 0 <= bal(table) <= 10_000
        books(table)
