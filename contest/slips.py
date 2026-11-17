"""Bet slips: the Leg / Slip value objects, validation against the contest rules, and pricing.

Line convention (same as the banked game-line feeds): for a SPREAD leg `line` is the handicap of the side the bettor picked
(home -3.5 -> -3.5, away +3.5 -> +3.5); for a TOTAL leg it is the total number; an ML leg has line None. A teaser moves the line in
the bettor's favour by `teaser_points` (see `adjusted_line`); the leg keeps the RAW quoted line so it can be checked against the board.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Sequence

from contest.odds import american_to_decimal, parlay_decimal, payout_cents
from contest.rules import ContestRules

KINDS = ("straight", "parlay", "teaser")
SIDES = {"spread": ("home", "away"), "ml": ("home", "away"), "total": ("over", "under")}


class SlipError(Exception):
    """A slip the contest refuses. `code` is machine-readable (the API maps it to a message), `detail` carries any data the
    client needs (for PRICE_CHANGED: the current quote)."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.detail = code, message, detail or {}


@dataclass(frozen=True)
class Leg:
    game_id: int
    market: str                      # spread | total | ml
    side: str                        # home|away for spread/ml, over|under for total
    line: float | None
    price: int                       # American, as quoted to the bettor
    teaser_points: float = 0.0


@dataclass(frozen=True)
class Slip:
    kind: str                        # straight | parlay | teaser
    legs: tuple[Leg, ...]
    stake_cents: int
    idempotency_key: str


@dataclass(frozen=True)
class Quote:
    """The current board price for (game, market, side). `fetched_at` is the FEED's own timestamp, never the read time."""
    line: float | None
    price: int
    fetched_at: datetime


@dataclass(frozen=True)
class GameInfo:
    commence_time: datetime          # aware UTC; the DB value, never the client's
    status: str                      # scheduled | final | void


def adjusted_line(leg: Leg) -> float | None:
    """The line used for grading: the raw line, moved `teaser_points` in the bettor's favour."""
    if leg.line is None:
        return None
    if leg.market == "spread":
        return leg.line + leg.teaser_points
    if leg.market == "total":
        return leg.line - leg.teaser_points if leg.side == "over" else leg.line + leg.teaser_points
    return leg.line


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validate_slip(slip: Slip, rules: ContestRules, balance_cents: int, open_risk_cents: int, now: datetime,
                  games: Mapping[int, GameInfo], board: Mapping[tuple, Quote] | None = None) -> None:
    """Raise SlipError for the first problem found; return None when the slip may be placed.

    `board` maps (game_id, market, side) -> Quote. Pass None only in tests / admin tools: placement through the API always checks
    the board, so a client can never name its own price."""
    if slip.kind not in KINDS:
        raise SlipError("BAD_KIND", f"unknown bet kind {slip.kind!r}")
    n = len(slip.legs)
    lo, hi = {"straight": (1, 1), "parlay": (rules.parlay_min_legs, rules.parlay_max_legs),
              "teaser": (rules.teaser_min_legs, rules.teaser_max_legs)}[slip.kind]
    if not lo <= n <= hi:
        raise SlipError("LEG_COUNT", f"{slip.kind} needs {lo}-{hi} legs, got {n}")
    if len({leg.game_id for leg in slip.legs}) != n:
        raise SlipError("DUP_GAME", "one leg per game on a slip")
    for leg in slip.legs:
        if leg.market not in SIDES or leg.side not in SIDES[leg.market]:
            raise SlipError("BAD_LEG", f"bad market/side {leg.market}/{leg.side}")
        if (leg.line is None) != (leg.market == "ml"):
            raise SlipError("BAD_LEG", "ml has no line; spread and total need one")
        american_to_decimal(leg.price)
    if slip.kind == "teaser":
        pts = {leg.teaser_points for leg in slip.legs}
        if len(pts) != 1 or rules.teaser_price(next(iter(pts)), n) is None:
            raise SlipError("TEASER_POINTS", f"teaser points {sorted(pts)} not offered for {n} legs")
        if any(leg.market == "ml" for leg in slip.legs):
            raise SlipError("TEASER_MARKET", "teasers are spreads and totals only")
    elif any(leg.teaser_points for leg in slip.legs):
        raise SlipError("TEASER_POINTS", "teaser points on a non-teaser slip")

    if slip.stake_cents < rules.min_stake_cents:
        raise SlipError("STAKE_MIN", f"minimum stake is {rules.min_stake_cents} cents")
    if slip.stake_cents > balance_cents:
        raise SlipError("INSUFFICIENT", "stake exceeds balance")
    # the percentage cap never shuts a player out: the minimum stake is always allowed (balance permitting), otherwise a balance
    # under min / pct would be neither able to bet nor eliminated
    if slip.stake_cents > rules.min_stake_cents and slip.stake_cents * 10_000 > balance_cents * rules.max_stake_bp:
        raise SlipError("STAKE_MAX", f"maximum stake is {rules.max_stake_bp / 100:g}% of balance (minimum stake always allowed)")
    if (open_risk_cents + slip.stake_cents) * 10_000 > (balance_cents + open_risk_cents) * rules.max_open_risk_bp:
        raise SlipError("OPEN_RISK", "over the open-risk cap")

    close_before = timedelta(minutes=rules.close_buffer_min)
    for leg in slip.legs:
        g = games.get(leg.game_id)
        if g is None:
            raise SlipError("NO_GAME", f"game {leg.game_id} is not in this contest")
        if g.status != "scheduled" or now >= g.commence_time - close_before:
            raise SlipError("GAME_CLOSED", f"betting on game {leg.game_id} is closed")
        if board is None:
            continue
        q = board.get((leg.game_id, leg.market, leg.side))
        if q is None:
            raise SlipError("NO_QUOTE", f"no current price for game {leg.game_id} {leg.market} {leg.side}")
        if (now - q.fetched_at).total_seconds() > rules.quote_max_age_s:
            raise SlipError("QUOTE_STALE", "price is too old; refresh the board")
        # a teaser is priced from the table, not the leg price, so only the LINE must still match there
        price_ok = slip.kind == "teaser" or leg.price == q.price
        if leg.line != q.line or not price_ok:
            raise SlipError("PRICE_CHANGED", "the price moved", {"game_id": leg.game_id, "market": leg.market, "side": leg.side,
                                                                  "line": q.line, "price": q.price})


def price_slip(slip: Slip, rules: ContestRules) -> int:
    """Total return in cents (stake included) if every leg wins."""
    return payout_cents(slip.stake_cents, win_decimal(slip.kind, slip.legs, rules))


def win_decimal(kind: str, legs, rules: ContestRules) -> Decimal:
    """Decimal odds of an all-win bet. `legs` may be a subset (pushed / voided legs dropped); a teaser reprices from the table."""
    legs = tuple(legs)
    if kind == "straight":
        return american_to_decimal(legs[0].price)
    if kind == "parlay":
        return parlay_decimal(leg.price for leg in legs)
    price = rules.teaser_price(legs[0].teaser_points, len(legs))
    if price is None:
        raise SlipError("TEASER_POINTS", f"no teaser price for {len(legs)} legs at {legs[0].teaser_points}")
    return american_to_decimal(price)


# ------------------------------------------------------------------------------------------------------------ round robins

def round_robin_slips(picks: Sequence[Leg], sizes: Sequence[int], unit_cents: int, key: str) -> list[Slip]:
    """Expand N picks into every k-leg parlay for each k in `sizes` ("by 2s", "by 3s" ...), each staked at `unit_cents`.
    A round robin is nothing but these parlays: they place, grade and settle as ordinary parlays, tied together by a group id."""
    out: list[Slip] = []
    for k in sorted(set(sizes)):
        for combo in combinations(range(len(picks)), k):
            out.append(Slip("parlay", tuple(picks[i] for i in combo), unit_cents, f"{key}#{len(out)}"))
    return out


def validate_round_robin(picks: Sequence[Leg], sizes: Sequence[int], unit_cents: int, rules: ContestRules, balance_cents: int,
                         open_risk_cents: int, now: datetime, games: Mapping[int, GameInfo],
                         board: Mapping[tuple, Quote] | None = None) -> list[Slip]:
    """Raise SlipError for the first problem; return the parlays to place. The stake limits apply to the TOTAL at risk (unit x
    parlays), not to one unit, so a round robin cannot slip a bigger exposure past the per-bet cap."""
    n = len(picks)
    if not rules.round_robin_min_picks <= n <= rules.round_robin_max_picks:
        raise SlipError("RR_PICKS", f"a round robin needs {rules.round_robin_min_picks}-{rules.round_robin_max_picks} picks, got {n}")
    if len({p.game_id for p in picks}) != n:
        raise SlipError("DUP_GAME", "one pick per game in a round robin")
    ks = sorted(set(int(k) for k in sizes))
    if not ks or ks[0] < 2 or ks[-1] > min(n, rules.parlay_max_legs):
        raise SlipError("RR_SIZES", f"round robin sizes must be between 2 and {min(n, rules.parlay_max_legs)}")
    if any(p.teaser_points for p in picks):
        raise SlipError("TEASER_POINTS", "teaser points on a round robin pick")
    count = sum(len(list(combinations(range(n), k))) for k in ks)
    if count > rules.round_robin_max_parlays:
        raise SlipError("RR_TOO_MANY", f"{count} parlays is over the limit of {rules.round_robin_max_parlays}")
    total = unit_cents * count
    if unit_cents < rules.min_stake_cents:
        raise SlipError("STAKE_MIN", f"minimum stake per parlay is {rules.min_stake_cents} cents")
    if total > balance_cents:
        raise SlipError("INSUFFICIENT", f"{count} parlays x {unit_cents} cents is more than the balance")
    if total > rules.min_stake_cents and total * 10_000 > balance_cents * rules.max_stake_bp:
        raise SlipError("STAKE_MAX", f"total risk is over {rules.max_stake_bp / 100:g}% of balance")
    if (open_risk_cents + total) * 10_000 > (balance_cents + open_risk_cents) * rules.max_open_risk_bp:
        raise SlipError("OPEN_RISK", "over the open-risk cap")
    slips = round_robin_slips(picks, ks, unit_cents, "rr")
    for sl in slips:
        validate_slip(sl, rules, balance_cents, open_risk_cents, now, games, board)
    return slips


def price_round_robin(slips: Sequence[Slip], rules: ContestRules) -> int:
    """Total return in cents if every pick wins (the sum of every parlay's all-win payout)."""
    return sum(price_slip(sl, rules) for sl in slips)
