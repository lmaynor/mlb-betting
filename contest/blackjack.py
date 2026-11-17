"""Blackjack, the contest's hidden lifeline for a player who has fallen under a $100 bankroll. Pure game logic, no database.

House rules (a plain, slightly player-friendly table; the lifeline is a rescue, not an edge):
  * one 52-card deck, freshly shuffled for every hand with a cryptographic RNG, so there is nothing to count
  * the player gets two cards, the dealer two (one face up); the dealer peeks, so a dealer blackjack ends the hand at once
  * blackjack pays 3 to 2; dealer stands on ALL 17s (soft 17 included); a tie is a push
  * the player may hit, stand, or double down on the first two cards (one more card, stake doubled); no splits, insurance or surrender
  * cards are 'RS': rank in 23456789TJQKA, suit in SHDC

The dealer's hole card and the order of the deck never leave the server (see `view`); money and the $100 cap live in contest/lifeline.py.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

RANKS, SUITS = "23456789TJQKA", "SHDC"


def new_deck(rng: random.Random | None = None) -> list[str]:
    deck = [r + s for r in RANKS for s in SUITS]
    (rng or random.SystemRandom()).shuffle(deck)
    return deck


def hand_value(cards: list[str]) -> tuple[int, bool]:
    """(best total, is_soft). An ace counts 11 while that does not bust, otherwise 1."""
    total = sum(10 if c[0] in "TJQK" else 1 if c[0] == "A" else int(c[0]) for c in cards)
    if "A" in (c[0] for c in cards) and total + 10 <= 21:
        return total + 10, True
    return total, False


def is_blackjack(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


@dataclass
class Hand:
    deck: list[str]
    player: list[str] = field(default_factory=list)
    dealer: list[str] = field(default_factory=list)
    stake_cents: int = 0           # the ORIGINAL stake; a double adds `stake_cents` again (see total_stake)
    doubled: bool = False
    done: bool = False
    outcome: str | None = None     # blackjack | win | push | lose | bust

    @property
    def total_stake(self) -> int:
        return self.stake_cents * (2 if self.doubled else 1)

    def to_json(self) -> dict:
        return {"deck": self.deck, "player": self.player, "dealer": self.dealer, "stake": self.stake_cents, "doubled": self.doubled,
                "done": self.done, "outcome": self.outcome}

    @classmethod
    def from_json(cls, d: dict) -> "Hand":
        return cls(deck=list(d["deck"]), player=list(d["player"]), dealer=list(d["dealer"]), stake_cents=int(d["stake"]),
                   doubled=bool(d["doubled"]), done=bool(d["done"]), outcome=d["outcome"])


class BlackjackError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message = code, message


def deal(stake_cents: int, deck: list[str] | None = None, rng: random.Random | None = None) -> Hand:
    """Start a hand from `deck` (tests) or a fresh shuffled one. Deals player, dealer, player, dealer from the front of the deck."""
    d = list(deck) if deck is not None else new_deck(rng)
    h = Hand(deck=d, stake_cents=int(stake_cents))
    h.player.append(d.pop(0))
    h.dealer.append(d.pop(0))                       # the dealer's UP card
    h.player.append(d.pop(0))
    h.dealer.append(d.pop(0))                       # the HOLE card
    p_bj, d_bj = is_blackjack(h.player), is_blackjack(h.dealer)
    if p_bj or d_bj:
        h.done = True
        h.outcome = "push" if p_bj and d_bj else "blackjack" if p_bj else "lose"
    return h


def _dealer_play(h: Hand) -> None:
    while hand_value(h.dealer)[0] < 17:             # stands on every 17, soft included
        h.dealer.append(h.deck.pop(0))
    p, d = hand_value(h.player)[0], hand_value(h.dealer)[0]
    h.outcome = "win" if d > 21 or p > d else "push" if p == d else "lose"
    h.done = True


def _live(h: Hand) -> None:
    if h.done:
        raise BlackjackError("HAND_OVER", "this hand is finished")


def hit(h: Hand) -> Hand:
    _live(h)
    h.player.append(h.deck.pop(0))
    total = hand_value(h.player)[0]
    if total > 21:
        h.done, h.outcome = True, "bust"
    elif total == 21:
        _dealer_play(h)                             # nothing left to decide
    return h


def stand(h: Hand) -> Hand:
    _live(h)
    _dealer_play(h)
    return h


def can_double(h: Hand) -> bool:
    return not h.done and not h.doubled and len(h.player) == 2


def double_down(h: Hand) -> Hand:
    """One more card, the stake doubled (the caller debits the second stake first). Ends the player's turn."""
    _live(h)
    if not can_double(h):
        raise BlackjackError("CANNOT_DOUBLE", "you can only double on your first two cards")
    h.doubled = True
    h.player.append(h.deck.pop(0))
    if hand_value(h.player)[0] > 21:
        h.done, h.outcome = True, "bust"
    else:
        _dealer_play(h)
    return h


def payout_cents(h: Hand) -> int:
    """Total returned to the player (stake included) for a finished hand, before the lifeline cap."""
    if not h.done:
        raise BlackjackError("HAND_OPEN", "the hand is not finished")
    s = h.total_stake
    return {"blackjack": s + (s * 3) // 2, "win": 2 * s, "push": s, "lose": 0, "bust": 0}[h.outcome]


def view(h: Hand) -> dict:
    """What the player may see: both their cards, and the dealer's UP card only until the hand is over. Never the deck."""
    shown = h.dealer if h.done else [h.dealer[0], "??"]
    return {"player": list(h.player), "dealer": list(shown), "player_total": hand_value(h.player)[0],
            "dealer_total": hand_value(h.dealer)[0] if h.done else hand_value(h.dealer[:1])[0],
            "stake_cents": h.total_stake, "doubled": h.doubled, "done": h.done, "outcome": h.outcome, "can_double": can_double(h)}
