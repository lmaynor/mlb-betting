"""The blackjack lifeline: a hidden table that opens when a player's BANKROLL (balance plus stakes still at risk) is under $100.

Why it exists: a contest ends the first time someone cannot place a bet. The lifeline gives a broke player a way back with the little
they have left, and nothing more: it is a rescue, not an edge.

* eligible only while bankroll < `rules.blackjack_threshold_cents` (the page shows the table only then, or while a hand is open)
* every hand is staked from the player's balance through the ledger (BJ_STAKE) and paid through it (BJ_PAYOUT), so the books balance
* the cap: winnings can never lift the bankroll above the threshold. Whatever a win would add beyond $100 is not credited, and the hand
  says so ("capped"). Losses are never capped. The house edge is about the usual 0.5 percent, so the table does not print money.
* one open hand per player (a unique index), the deck and the hole card stay on the server, every step runs under the entry row lock
* a player is eliminated only when they cannot afford one chip, have nothing pending and no hand open (store._eliminate_if_bust)
"""
from __future__ import annotations

import json
import random
from datetime import datetime

from sqlalchemy import text

from contest import blackjack as bj
from contest.store import ContestError, _eliminate_if_bust, _ledger, _lock, _open_entry, _open_risk, iso

ACTIONS = ("hit", "stand", "double")


def _row(c, entry_id: int):
    return c.execute(text(_lock(c, """SELECT id, state_json, bankroll_before_cents FROM contest_blackjack
                                       WHERE entry_id = :e AND status = 'open'""")), {"e": entry_id}).first()


def _summary(rules, balance: int, open_risk: int, status: str, has_hand: bool) -> dict:
    bankroll = balance + open_risk
    eligible = rules.blackjack_enabled and status == "active" and bankroll < rules.blackjack_threshold_cents
    return {"enabled": rules.blackjack_enabled, "eligible": eligible, "visible": eligible or has_hand, "bankroll_cents": bankroll, "balance_cents": balance,
            "threshold_cents": rules.blackjack_threshold_cents, "min_wager_cents": rules.blackjack_min_chip_cents,
            "max_wager_cents": min(balance, rules.blackjack_threshold_cents)}


def _settle(c, entry_id: int, hand_id: int, hand: bj.Hand, bankroll_before: int, rules, now: datetime) -> dict:
    """Pay a finished hand through the ledger, applying the cap, and return what happened."""
    payout = bj.payout_cents(hand)
    bankroll_now = bankroll_before - hand.total_stake                                   # both stakes are already debited
    cap = max(0, max(bankroll_before, rules.blackjack_threshold_cents) - bankroll_now)    # a win may not lift the bankroll above the threshold
    credited = min(payout, cap)
    if credited > 0:
        _ledger(c, entry_id, now, "BJ_PAYOUT", credited, note=f"blackjack {hand.outcome}")
    c.execute(text("""UPDATE contest_blackjack SET status = 'done', state_json = :s, outcome = :o, payout_cents = :p, credited_cents = :cr, settled_at = :t
                      WHERE id = :i"""), {"s": json.dumps(hand.to_json()), "o": hand.outcome, "p": payout, "cr": credited, "t": iso(now), "i": hand_id})
    _eliminate_if_bust(c, entry_id, rules, now)
    return {"payout_cents": payout, "credited_cents": credited, "capped": credited < payout}


def _result(c, rules, entry_id: int, hand: bj.Hand | None, extra: dict | None = None) -> dict:
    balance, status = c.execute(text("SELECT balance_cents, status FROM contest_entry WHERE id = :e"), {"e": entry_id}).one()
    has_hand = hand is not None and not hand.done
    out = _summary(rules, int(balance), _open_risk(c, entry_id), status, has_hand)
    out["entry_status"] = status
    out["hand"] = {**bj.view(hand), **(extra or {})} if hand is not None else None
    return out


def lifeline_status(engine, contest_id: int, user_id: str) -> dict:
    """Whether the table is open for this player and the open hand, if any (the hole card stays hidden)."""
    with engine.begin() as c:
        rules, entry_id, _bal, _status = _open_entry(c, contest_id, user_id)
        row = _row(c, entry_id)
        return _result(c, rules, entry_id, bj.Hand.from_json(json.loads(row[1])) if row else None)


def start_hand(engine, contest_id: int, user_id: str, stake_cents: int, now: datetime, deck: list[str] | None = None,
               rng: random.Random | None = None) -> dict:
    """Sit down: stake a hand from the balance and deal. `deck` is for tests; real hands shuffle a fresh deck with the system RNG."""
    with engine.begin() as c:
        rules, entry_id, balance, status = _open_entry(c, contest_id, user_id)
        if not rules.blackjack_enabled:
            raise ContestError("LIFELINE_OFF", "the blackjack table is closed in this contest")
        if status != "active":
            raise ContestError("ELIMINATED", "this entry is out of the contest")
        if _row(c, entry_id):
            raise ContestError("HAND_OPEN", "finish your current hand first")
        risk = _open_risk(c, entry_id)
        if balance + risk >= rules.blackjack_threshold_cents:
            raise ContestError("NOT_ELIGIBLE", "the table opens when your bankroll is under the threshold")
        stake = int(stake_cents)
        if stake < rules.blackjack_min_chip_cents or stake > balance or stake > rules.blackjack_threshold_cents:
            raise ContestError("STAKE_INVALID", f"stake between {rules.blackjack_min_chip_cents} and {min(balance, rules.blackjack_threshold_cents)} cents")
        hand = bj.deal(stake, deck=deck, rng=rng)
        _ledger(c, entry_id, now, "BJ_STAKE", -stake, note="blackjack stake")
        hand_id = c.execute(text("""INSERT INTO contest_blackjack (entry_id, status, stake_cents, state_json, bankroll_before_cents, created_at)
                                    VALUES (:e, 'open', :s, :j, :b, :t) RETURNING id"""),
                            {"e": entry_id, "s": stake, "j": json.dumps(hand.to_json()), "b": balance + risk, "t": iso(now)}).scalar_one()
        extra = _settle(c, entry_id, int(hand_id), hand, balance + risk, rules, now) if hand.done else None
        return _result(c, rules, entry_id, hand, extra)


def act(engine, contest_id: int, user_id: str, action: str, now: datetime) -> dict:
    """hit, stand or double on the open hand. Double debits a second stake first (refused if the balance cannot cover it)."""
    if action not in ACTIONS:
        raise ContestError("BAD_ACTION", f"action must be one of {ACTIONS}")
    with engine.begin() as c:
        rules, entry_id, balance, _status = _open_entry(c, contest_id, user_id)
        row = _row(c, entry_id)
        if row is None:
            raise ContestError("NO_OPEN_HAND", "there is no hand in play")
        hand_id, hand, before = int(row[0]), bj.Hand.from_json(json.loads(row[1])), int(row[2])
        try:
            if action == "double":
                if not bj.can_double(hand):
                    raise bj.BlackjackError("CANNOT_DOUBLE", "you can only double on your first two cards")
                if balance < hand.stake_cents:
                    raise ContestError("INSUFFICIENT", "not enough balance to double")
                _ledger(c, entry_id, now, "BJ_STAKE", -hand.stake_cents, note="blackjack double")
                bj.double_down(hand)
            elif action == "hit":
                bj.hit(hand)
            else:
                bj.stand(hand)
        except bj.BlackjackError as e:
            raise ContestError(e.code, e.message) from e
        if hand.done:
            extra = _settle(c, entry_id, hand_id, hand, before, rules, now)
        else:
            c.execute(text("UPDATE contest_blackjack SET state_json = :s WHERE id = :i"), {"s": json.dumps(hand.to_json()), "i": hand_id})
            extra = None
        return _result(c, rules, entry_id, hand, extra)
