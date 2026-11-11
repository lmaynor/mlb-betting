"""
ev.kalshi_resting -- resting post-only combo maker for Kalshi MVE tickers (DEMO ONLY).

Source of truth for every design constant: Argon resting-seat consult Part A
(~/workspace/beezy/profitability/argon-resting-seat-consult-VERBATIM-2026-11-11.md).
The RFQ responder seat stays closed; this module is the INVERSE seat: post resting
post_only NO limit orders on already-created MVE combo tickers, after the 10-minute
RFQ toxicity window, and cancel freely at any time.

Pure / offline core: tier admission, fee math, pricing, queue policy, velocity gate,
exposure caps and the reconciliation kill switch are all unit-testable with a fake
venue. The only network surface is RestingVenue, a narrow Protocol consumed through
KalshiClient.request (which exists on main).

PR-1 DEPENDENCY: branch fix/kalshi-resting-prereqs adds first-class resting submit,
queue-position reads, the market_ticker cancel fix and amend to the client / venue
layer. That branch is NOT merged into the base of this PR, so this module defines its
own adapter (KalshiRequestVenue) directly on KalshiClient.request. When PR-1 lands,
KalshiRequestVenue can be swapped for PR-1's venue methods without touching the
engine, the pricer, or any control in this file.

DEMO-ONLY BY CONSTRUCTION: assert_demo_only() refuses every env except "demo" and
every base URL except the demo host. The daemon calls it before credentials are read.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from ev.kalshi_rfq_pricer import (
    LegFairValue,
    PricerConfig,
    floor_to_tick,
)

# ---------------------------------------------------------------------------
# Constants (Argon Part A, verbatim values)
# ---------------------------------------------------------------------------

DEMO_ENV = "demo"
DEMO_HOST = "external-api.demo.kalshi.co"
DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"

KILL_REASON = "kalshi_resting"

# MVE maker fee: fee_mult 2 -> coefficient 0.035 (fees.py taker base 0.07 * 0.5 maker
# share at multiplier 2; see PricerConfig.fee_mult note in kalshi_rfq_pricer).
MVE_MAKER_COEFF = 0.035

LEG_UNCERTAINTY_PAD = 0.008        # P_cons pad per leg
PROT_YES_PAD = 0.008               # prot-leg pad (mirrors PricerConfig.prot_yes_pad)

EDGE_SGP_DIFF_PLAYER = 0.040
EDGE_SGP_SAME_PLAYER = 0.045
EDGE_CROSS_GAME = 0.050
EDGE_SAVES_ANCHORED = 0.035

MIN_NET_ROI = 0.035                # net collateral ROI floor, net of fee reserve
MIN_BOOKS = 4
MAX_DISPERSION = 0.08
CROSS_GAME_MAX_P_YES = 0.35
LONG_TAIL_MAX_P_YES = 0.15         # k >= 5 escape hatch: joint p_yes ceiling
SAVES_MODEL_MIN_EDGE = 0.030       # trained saves model must beat the median by >= 3.0pp

TOXICITY_WINDOW_SEC = 600.0        # never post on a ticker created < 10 minutes ago

HOLD_BAND = 0.010                  # +/-1.0c fair drift: preserve queue
HOLD_MIN_NET_EDGE = 0.025          # hold requires net edge >= 2.5c
ADVERSE_CANCEL_MOVE = 0.015        # >1.5c adverse fair move: cancel + repost

VELOCITY_LEG_MOVE = 0.030          # consensus move >= 3.0pp ...
VELOCITY_LEG_WINDOW_SEC = 5.0      # ... within <= 5s
VELOCITY_PRINT_MOVE = 0.15         # Kalshi single-leg print jump >= 15c ...
VELOCITY_PRINT_WINDOW_SEC = 2.0    # ... within <= 2s
VELOCITY_COOLDOWN_SEC = 120.0

RECONCILE_INTERVAL_SEC = 30.0
READ_AFTER_WRITE_GRACE_SEC = 1.0   # ~1s venue visibility lag (demo test, Nov 11)
CANCEL_GRACE_SEC = 5.0             # Argon mechanical kill: orphan persisting > 5s

# Same-player pairs admissible for resting, keyed (sport, stat_lo, stat_hi) with the
# stats sorted -- the same canonicalisation as kalshi_rfq_pricer.sgp_corr_key, minus
# the relation (a resting same-player pair is same_player by construction). Ships
# EMPTY in this PR by instruction: Argon Part B advanced the NFL rec_yds x
# receptions pair for the RFQ pricer's shadow defaults, but resting promotion is a
# later config change after the Nov 13 judgment. Do not add pairs here.
SAME_PLAYER_ALLOWED_PAIRS: frozenset = frozenset()

# Fair inputs hard-excluded by Argon, independent of price:
#   * NBA assists legs (any fair source)
#   * NHL skater models: an NHL non-saves leg whose fair comes from a trained model
#   * NFL hierarchical props: a leg flagged hierarchical (a prop mechanically
#     containing another, e.g. a combined-stat line)
EXCLUDED_STAT_BY_SPORT = {"nba": frozenset({"assists"})}
TRAINED_MODEL_SOURCE = "trained_model"
CONSENSUS_SOURCE = "consensus"
BLEND_SOURCE = "walk_forward_blend"
SAVES_MODEL_SOURCE = "saves_model"


def same_player_pair_key(sport: str, stat_a: str, stat_b: str) -> tuple:
    lo, hi = sorted((stat_a, stat_b))
    return (str(sport).lower(), lo, hi)


# ---------------------------------------------------------------------------
# Demo-only guard
# ---------------------------------------------------------------------------

class DemoOnlyError(RuntimeError):
    pass


def assert_demo_only(env: str | None, base_url: str | None) -> None:
    """Fail closed unless env is the demo env AND the base URL is the demo host.

    Production, production-readonly, public-data, a missing value, or any non-demo
    host (including the production host on the demo env) all refuse."""
    if (env or "").strip().lower() != DEMO_ENV:
        raise DemoOnlyError(
            f"resting engine is DEMO-ONLY: env {env!r} refused (only 'demo' may post resting orders)")
    parsed = urlparse(base_url or "")
    if parsed.scheme != "https" or parsed.hostname != DEMO_HOST:
        raise DemoOnlyError(
            f"resting engine is DEMO-ONLY: base URL {base_url!r} refused "
            f"(only https://{DEMO_HOST} may be used)")


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RestingLeg:
    """One combo leg as the resting engine sees it. p_cons_yes is the leave-Kalshi-out
    de-vigged consensus P(YES) BEFORE the resting pads; None means the leg is unpriced.
    p_model_yes is the trained-model probability when one produced the fair (saves)."""
    ticker: str
    side: str                              # 'yes' | 'no'
    game_key: str                          # '{sport}:{date}:{AWAY}@{HOME}'
    sport: str                             # 'nfl' | 'nba' | 'nhl' | ...
    stat: str                              # 'saves' | 'points' | 'rec_yds' | ...
    n_books: int = 0
    disp: float = 1.0
    p_cons_yes: float | None = None
    p_model_yes: float | None = None
    player_norm: str | None = None
    fair_source: str = CONSENSUS_SOURCE    # consensus | walk_forward_blend | saves_model | trained_model
    exact_priced: bool = False             # exact rung priced (vs pooled / bounded)
    protected: bool = False                # *PROT ticker leg
    hierarchical: bool = False             # NFL hierarchical prop flag

    @property
    def priced(self) -> bool:
        return self.p_cons_yes is not None

    @property
    def is_saves(self) -> bool:
        return self.sport.lower() == "nhl" and self.stat == "saves"

    def directional_cons(self) -> float | None:
        """P(this leg wins) on consensus, in the leg's own side direction."""
        if self.p_cons_yes is None:
            return None
        return self.p_cons_yes if self.side == "yes" else 1.0 - self.p_cons_yes

    def saves_model_edge(self) -> float | None:
        """Signed edge of the trained saves model over the >=4-book median, in the
        leg's side direction. Positive means the model beats the median."""
        if not self.is_saves or self.p_model_yes is None or self.p_cons_yes is None:
            return None
        if self.side == "yes":
            return self.p_model_yes - self.p_cons_yes
        return self.p_cons_yes - self.p_model_yes


@dataclass(frozen=True)
class ComboCandidate:
    market_ticker: str
    legs: tuple                            # tuple[RestingLeg, ...]
    created_ts: float                      # MVE ticker creation time (toxicity window)
    unpriced_legs: int | None = None       # explicit count; defaults to derived

    @property
    def k(self) -> int:
        return len(self.legs)

    @property
    def n_unpriced(self) -> int:
        derived = sum(1 for leg in self.legs if not leg.priced)
        return derived if self.unpriced_legs is None else max(derived, int(self.unpriced_legs))

    @property
    def game_keys(self) -> tuple:
        seen: list = []
        for leg in self.legs:
            if leg.game_key not in seen:
                seen.append(leg.game_key)
        return tuple(seen)

    @property
    def is_same_game(self) -> bool:
        return len(self.game_keys) == 1 and self.k >= 2

    @property
    def is_cross_game(self) -> bool:
        return len(self.game_keys) == self.k and self.k >= 2

    @property
    def has_same_player_pair(self) -> bool:
        seen: dict = {}
        for leg in self.legs:
            if leg.player_norm is None:
                continue
            key = (leg.game_key, leg.player_norm)
            if key in seen:
                return True
            seen[key] = leg
        return False

    @property
    def saves_anchor(self) -> RestingLeg | None:
        for leg in self.legs:
            edge = leg.saves_model_edge()
            if edge is not None and edge >= SAVES_MODEL_MIN_EDGE and leg.n_books >= MIN_BOOKS:
                return leg
        return None


# ---------------------------------------------------------------------------
# Tier admission (pure)
# ---------------------------------------------------------------------------

# Reject codes (resting-specific; leg-gate codes mirror the RFQ pricer's names).
REJ_SINGLE_LEG = "REJ_SINGLE_LEG"
REJ_UNPRICED_LEG = "REJ_UNPRICED_LEG"
REJ_LOW_BOOKS = "REJ_LOW_BOOKS"
REJ_HIGH_DISPERSION = "REJ_HIGH_DISPERSION"
REJ_LONG_TAIL = "REJ_LONG_TAIL"
REJ_EXCLUDED_FAIR_INPUT = "REJ_EXCLUDED_FAIR_INPUT"
REJ_SAME_PLAYER_UNMEASURED = "REJ_SAME_PLAYER_UNMEASURED"
REJ_NOT_LONGSHOT = "REJ_NOT_LONGSHOT"
REJ_TOO_MANY_LEGS = "REJ_TOO_MANY_LEGS"
REJ_NO_TIER = "REJ_NO_TIER"


@dataclass(frozen=True)
class TierDecision:
    admitted: bool
    tier: int | None                       # 1 | 2 | None
    reason: str                            # 'TIER_1' | 'TIER_2' | REJ_*
    edge_class: float = 0.0
    detail: str = ""


def _reject(reason: str, detail: str = "") -> TierDecision:
    return TierDecision(False, None, reason, 0.0, detail)


def _excluded_fair_input(leg: RestingLeg) -> str | None:
    stats = EXCLUDED_STAT_BY_SPORT.get(leg.sport.lower(), frozenset())
    if leg.stat in stats:
        return f"{leg.sport} {leg.stat} is a hard-excluded fair input"
    if leg.hierarchical:
        return "NFL hierarchical prop is a hard-excluded fair input"
    if leg.sport.lower() == "nhl" and not leg.is_saves and leg.fair_source == TRAINED_MODEL_SOURCE:
        return "NHL skater model is a hard-excluded fair input"
    return None


def _same_player_violation(combo: ComboCandidate) -> str | None:
    by_player: dict = {}
    for leg in combo.legs:
        if leg.player_norm is None:
            continue
        by_player.setdefault((leg.game_key, leg.sport, leg.player_norm), []).append(leg)
    for (_gk, sport, _player), legs in by_player.items():
        if len(legs) < 2:
            continue
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                key = same_player_pair_key(sport, legs[i].stat, legs[j].stat)
                if key not in SAME_PLAYER_ALLOWED_PAIRS:
                    return f"unmeasured same-player pair {key} not in SAME_PLAYER_ALLOWED_PAIRS"
    return None


def evaluate_tier(combo: ComboCandidate, p_cons_yes: float | None) -> TierDecision:
    """Tier admission for a candidate combo. p_cons_yes is the padded joint P(YES)
    (see joint_probabilities); it is only consulted where a rule names it (the
    cross-game longshot gate and the k>=5 escape hatch) and may be None otherwise,
    in which case a rule that needs it fails closed."""
    k = combo.k
    if k < 2:
        return _reject(REJ_SINGLE_LEG, "the resting engine only ever emits combo orders")
    if combo.n_unpriced >= 1:
        return _reject(REJ_UNPRICED_LEG, f"unpriced_legs={combo.n_unpriced} (resting requires 0)")
    for leg in combo.legs:
        why = _excluded_fair_input(leg)
        if why:
            return _reject(REJ_EXCLUDED_FAIR_INPUT, why)
    violation = _same_player_violation(combo)
    if violation:
        return _reject(REJ_SAME_PLAYER_UNMEASURED, violation)
    for leg in combo.legs:
        if leg.n_books < MIN_BOOKS:
            return _reject(REJ_LOW_BOOKS, f"leg {leg.ticker} priced from {leg.n_books} books (< {MIN_BOOKS})")
        if leg.disp > MAX_DISPERSION:
            return _reject(REJ_HIGH_DISPERSION, f"leg {leg.ticker} dispersion {leg.disp:.3f} > {MAX_DISPERSION}")
    if k >= 5:
        # Escape hatch only: every leg exact-priced AND the joint is a deep longshot.
        if not all(leg.exact_priced for leg in combo.legs):
            return _reject(REJ_LONG_TAIL, "k>=5 requires every leg exact-priced")
        if p_cons_yes is None or p_cons_yes > LONG_TAIL_MAX_P_YES:
            return _reject(REJ_LONG_TAIL, f"k>=5 requires joint p_yes <= {LONG_TAIL_MAX_P_YES}")
        return TierDecision(True, 1, "TIER_1", EDGE_CROSS_GAME if combo.is_cross_game else EDGE_SGP_DIFF_PLAYER,
                            "k>=5 exact-priced longshot escape hatch")
    # Tier 2 first: a saves-anchored combo prices at the saves edge even when it
    # would also clear Tier 1.
    if 2 <= k <= 3 and combo.saves_anchor is not None:
        return TierDecision(True, 2, "TIER_2", EDGE_SAVES_ANCHORED,
                            "NHL saves leg: trained model beats >=4-book median by >= 3.0pp")
    if 2 <= k <= 4:
        if combo.is_cross_game:
            if p_cons_yes is None or p_cons_yes > CROSS_GAME_MAX_P_YES:
                return _reject(REJ_NOT_LONGSHOT,
                               f"cross-game requires joint p_yes <= {CROSS_GAME_MAX_P_YES}")
            return TierDecision(True, 1, "TIER_1", EDGE_CROSS_GAME, "cross-game fully priced")
        if combo.has_same_player_pair:
            return TierDecision(True, 1, "TIER_1", EDGE_SGP_SAME_PLAYER, "approved same-player SGP")
        return TierDecision(True, 1, "TIER_1", EDGE_SGP_DIFF_PLAYER, "same-game (SGP) fully priced")
    return _reject(REJ_NO_TIER, f"no tier admits k={k}")


# ---------------------------------------------------------------------------
# Joint probabilities (reuses the PR #214 relation-keyed rho machinery)
# ---------------------------------------------------------------------------

def _to_fair_leg(leg: RestingLeg) -> LegFairValue:
    return LegFairValue(
        ticker=leg.ticker, sport=leg.sport.lower(), game_key=leg.game_key,
        market_class="player_prop", player_norm=leg.player_norm, stat=leg.stat,
        strike=0.0, p_cons_yes=float(leg.p_cons_yes), n_books=leg.n_books, disp=leg.disp,
        p_model_yes=leg.p_model_yes, pooled=not leg.exact_priced, updated_ts=0.0,
        protected=leg.protected)


def joint_probabilities(combo: ComboCandidate) -> tuple[float, float]:
    """(p_true_yes, p_cons_yes) for a fully-priced combo.

    Delegates the copula to KalshiRFQPricer._joint (the relation-keyed SGP rho table
    from PR #214) with the pair buffer zeroed: Argon's resting P_cons carries ONLY
    the 0.008/leg uncertainty pad plus the 0.008 prot pad on *PROT YES legs (inside
    _joint via _leg_p), not the RFQ pricer's extra per-pair buffer. p_true is the
    unbiased joint (signed rho, no pad). Raises ValueError when a same-game pair has
    no priceable rho (unmeasured same-player pair): fail closed."""
    from ev.kalshi_rfq_pricer import KalshiRFQPricer  # local: avoids a cycle at import time

    cfg = PricerConfig(leg_safety_pad=LEG_UNCERTAINTY_PAD, prot_yes_pad=PROT_YES_PAD,
                       sgp_pair_buffer=0.0, sgp_buffer_cap=0.0)
    pricer = KalshiRFQPricer.__new__(KalshiRFQPricer)
    pricer.cfg = cfg
    groups: dict = {}
    for leg in combo.legs:
        fv = _to_fair_leg(leg)
        p_dir = fv.p_true_yes if leg.side == "yes" else 1.0 - fv.p_true_yes
        groups.setdefault(leg.game_key, []).append((fv, leg.side, p_dir))
    p_true = pricer._joint(groups, pad=0.0, conservative=False)
    p_cons = pricer._joint(groups, pad=LEG_UNCERTAINTY_PAD)
    if p_true is None or p_cons is None:
        raise ValueError("same-game pair has no priceable rho (unmeasured same-player pair)")
    return float(p_true), float(p_cons)


# ---------------------------------------------------------------------------
# Fees + pricing (cents-exact, floor to cent)
# ---------------------------------------------------------------------------

def fee_entry(price_no: float) -> float:
    """One-way MVE maker fee per contract at NO price P: 0.035 * P * (1 - P).
    At 0.50 -> 0.00875 ($0.875c); at 0.85 -> ~0.0044625 (~0.45c)."""
    if price_no <= 0.0 or price_no >= 1.0:
        return 0.0
    return MVE_MAKER_COEFF * price_no * (1.0 - price_no)


def fee_reserve(price_no: float, unwind: bool) -> float:
    """Fee reserve per contract: 2x Fee_entry for positions subject to pre-settlement
    unwind, 1x for hold-to-settlement. `unwind` is an explicit per-order choice."""
    return (2.0 if unwind else 1.0) * fee_entry(price_no)


@dataclass(frozen=True)
class RestingQuote:
    market_ticker: str
    no_bid: float
    p_true_yes: float
    p_cons_yes: float
    edge_class: float
    fee_reserve_per_contract: float
    net_roi: float
    tier: int
    unwind: bool


def price_quote(combo: ComboCandidate, tier: TierDecision, p_true_yes: float,
                p_cons_yes: float, unwind: bool = True) -> RestingQuote | None:
    """P_no_bid = floor_to_cent((1 - P_cons_yes) - Edge_class - Fee_reserve(P_no)).

    Fee_reserve is evaluated at the pre-fee bid and iterated twice so the reserve is
    (to sub-cent precision) the reserve of the final floored bid itself. Returns
    None when the bid is off-grid or the net collateral ROI floor fails:
    (1 - P_true_yes) - P_no_bid - Fee_reserve >= 0.035."""
    gross = (1.0 - p_cons_yes) - tier.edge_class
    if gross <= 0.0:
        return None
    bid = gross
    for _ in range(3):
        bid = floor_to_tick(gross - fee_reserve(bid, unwind), 0.01)
        if bid <= 0.0 or bid >= 1.0:
            return None
    reserve = fee_reserve(bid, unwind)
    net_roi = ((1.0 - p_true_yes) - bid - reserve) / bid if bid > 0 else 0.0
    # Argon's stated test, literally: (1.0 - P_true_yes) - P_no_bid - Fee_reserve >= 0.035.
    if (1.0 - p_true_yes) - bid - reserve < MIN_NET_ROI - 1e-12:
        return None
    return RestingQuote(combo.market_ticker, bid, p_true_yes, p_cons_yes,
                        tier.edge_class, reserve, net_roi, tier.tier or 0, unwind)


# ---------------------------------------------------------------------------
# Exposure / sizing (Argon demo limits, exact defaults)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RestingLimits:
    contracts_per_order: int = 2
    max_liability_per_order: float = 1.90
    max_open_orders: int = 20
    max_total_collateral: float = 35.00
    max_contracts_per_market: int = 4
    max_liability_per_game: float = 10.00
    daily_loss_limit: float = 15.00


def order_liability(count: float, no_price: float) -> float:
    """Collateral locked by a NO order: count * P_no (the max loss per contract is
    P_no, since a NO bought at P_no settles at 1 - P_no profit or -P_no)."""
    return float(count) * float(no_price)


@dataclass
class ExposureBook:
    """Tracks resting orders for cap enforcement. An order's full liability counts
    against EACH of its game_keys (the RFQ gate's convention)."""
    limits: RestingLimits = field(default_factory=RestingLimits)

    def check(self, orders: list, market_ticker: str, game_keys: tuple,
              count: float, no_price: float) -> str | None:
        """None when the new order fits every cap, else a REJ_*-style reason."""
        lim = self.limits
        new_liab = order_liability(count, no_price)
        if count > lim.contracts_per_order:
            return f"REJ_SIZE: {count} contracts > {lim.contracts_per_order} per order"
        if new_liab > lim.max_liability_per_order + 1e-9:
            return f"REJ_SIZE: liability ${new_liab:.2f} > ${lim.max_liability_per_order:.2f} per order"
        if len(orders) >= lim.max_open_orders:
            return f"REJ_SIZE: {len(orders)} open orders >= cap {lim.max_open_orders}"
        total = sum(o.liability for o in orders) + new_liab
        if total > lim.max_total_collateral + 1e-9:
            return f"REJ_SIZE: total collateral ${total:.2f} > ${lim.max_total_collateral:.2f}"
        per_market = sum(o.count for o in orders if o.market_ticker == market_ticker) + count
        if per_market > lim.max_contracts_per_market:
            return f"REJ_SIZE: {per_market} contracts on {market_ticker} > cap {lim.max_contracts_per_market}"
        for gk in game_keys:
            per_game = sum(o.liability for o in orders if gk in o.game_keys) + new_liab
            if per_game > lim.max_liability_per_game + 1e-9:
                return f"REJ_SIZE: game {gk} liability ${per_game:.2f} > ${lim.max_liability_per_game:.2f}"
        return None


# ---------------------------------------------------------------------------
# Toxicity window + discovery abstraction
# ---------------------------------------------------------------------------

def toxicity_ok(created_ts: float, now: float) -> bool:
    """Only post on MVE combo tickers created >= 10 minutes ago."""
    return (now - created_ts) >= TOXICITY_WINDOW_SEC


@dataclass(frozen=True)
class DiscoveredCombo:
    candidate: ComboCandidate


class ComboDiscovery(Protocol):
    """Discovery input (poll or stream) injected into the engine; tests drive it
    with fixtures. Implementations yield candidates whose created_ts is the MVE
    ticker creation time."""

    def poll(self, now: float) -> list: ...


# ---------------------------------------------------------------------------
# Venue protocol + adapter on KalshiClient.request (PR-1 seam)
# ---------------------------------------------------------------------------

class RestingVenue(Protocol):
    """The narrow resting surface the engine needs. PR-1 provides these on the
    client/venue layer; until it merges, KalshiRequestVenue implements them over
    KalshiClient.request."""

    def submit_resting_no(self, *, market_ticker: str, client_order_id: str,
                          count: float, no_price: float) -> dict: ...

    def cancel(self, *, order_id: str, market_ticker: str) -> dict: ...

    def amend(self, *, order_id: str, market_ticker: str,
              count: float | None = None, expiration_ts: float | None = None) -> dict: ...

    def queue_position(self, *, order_id: str) -> float: ...

    def get_orders(self) -> list: ...

    def get_positions(self) -> list: ...

    def get_fills(self) -> list: ...

    def get_balance(self) -> dict: ...


class KalshiRequestVenue:
    """RestingVenue over any client exposing KalshiClient.request(method, path,
    params, body). NO orders are expressed as a YES ask at 1 - P_no (buy NO ==
    sell YES), post_only + good_till_canceled. Cancel uses the market_ticker
    routing parameter (the demo test's fix), NOT the stale `ticker` param that
    KalshiClient.cancel_order still sends on main."""

    def __init__(self, client):
        self._client = client

    def _req(self, method: str, path: str, params: dict | None = None,
             body: dict | None = None) -> dict:
        return self._client.request(method, path, params=params, body=body)

    def submit_resting_no(self, *, market_ticker, client_order_id, count, no_price):
        yes_price = round(1.0 - float(no_price), 4)
        return self._req("POST", "/portfolio/events/orders", body={
            "ticker": market_ticker,
            "client_order_id": client_order_id,
            "side": "ask",                       # sell YES at 1-P_no == buy NO at P_no
            "count": f"{float(count):.2f}",
            "price": f"{yes_price:.4f}",
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
        })

    def cancel(self, *, order_id, market_ticker):
        return self._req("DELETE", f"/portfolio/events/orders/{order_id}",
                         params={"market_ticker": market_ticker})

    def amend(self, *, order_id, market_ticker, count=None, expiration_ts=None):
        body: dict = {"market_ticker": market_ticker}
        if count is not None:
            body["count_fp"] = f"{float(count):.2f}"
        if expiration_ts is not None:
            body["expiration_ts"] = int(expiration_ts)
        return self._req("POST", f"/portfolio/events/orders/{order_id}/amend", body=body)

    def queue_position(self, *, order_id):
        data = self._req("GET", f"/portfolio/orders/{order_id}/queue_position")
        return float(data.get("queue_position_fp") or 0.0)

    def get_orders(self):
        data = self._req("GET", "/portfolio/orders", params={"status": "resting", "limit": 1000})
        return data.get("orders", []) or []

    def get_positions(self):
        data = self._req("GET", "/portfolio/positions", params={"limit": 1000})
        return data.get("market_positions", []) or []

    def get_fills(self):
        data = self._req("GET", "/portfolio/fills", params={"limit": 1000})
        return data.get("fills", []) or []

    def get_balance(self):
        return self._req("GET", "/portfolio/balance")


# ---------------------------------------------------------------------------
# Resting order state + queue policy
# ---------------------------------------------------------------------------

@dataclass
class RestingOrder:
    client_order_id: str
    market_ticker: str
    game_keys: tuple
    leg_tickers: tuple
    count: float
    no_price: float
    p_true_yes: float                        # at posting: implied fair NO = 1 - p_true
    p_cons_yes: float
    fee_reserve_per_contract: float
    unwind: bool = True
    tier: int = 1
    order_id: str | None = None
    status: str = "resting"                  # resting | cancel_pending | cancelled | filled
    posted_ts: float = 0.0
    cancel_requested_ts: float | None = None

    @property
    def liability(self) -> float:
        return order_liability(self.count, self.no_price)

    @property
    def implied_fair_no(self) -> float:
        return 1.0 - self.p_true_yes


QUEUE_HOLD = "HOLD"
QUEUE_AMEND = "AMEND"
QUEUE_CANCEL_REPOST = "CANCEL_REPOST"
QUEUE_CANCEL = "CANCEL"


def queue_decision(order: RestingOrder, current_p_true_yes: float, *,
                   size_change: bool = False, expiry_change: bool = False,
                   velocity_tripped: bool = False) -> str:
    """Hold-in-place while the fair stays within +/-1.0c of the resting order's
    implied fair AND net edge >= 2.5c. AMEND for size/expiry changes (queue
    preserving). CANCEL (and repost only after cooldown) on a >1.5c adverse fair
    move or a velocity trip; a vanished net edge also cancels."""
    if velocity_tripped:
        return QUEUE_CANCEL_REPOST
    current_fair_no = 1.0 - current_p_true_yes
    drift = current_fair_no - order.implied_fair_no      # negative drift is adverse for a NO bid
    net_edge = current_fair_no - order.no_price - fee_reserve(order.no_price, order.unwind)
    if drift < -ADVERSE_CANCEL_MOVE - 1e-9:      # strictly beyond 1.5c (float-guarded)
        return QUEUE_CANCEL_REPOST
    if size_change or expiry_change:
        return QUEUE_AMEND
    if abs(drift) <= HOLD_BAND and net_edge >= HOLD_MIN_NET_EDGE:
        return QUEUE_HOLD
    if drift <= HOLD_BAND and net_edge >= HOLD_MIN_NET_EDGE:
        # Adverse drift inside the cancel threshold with edge intact: keep queue.
        # (Favourable drift beyond the band also lands here and only helps.)
        return QUEUE_HOLD
    if net_edge < HOLD_MIN_NET_EDGE:
        return QUEUE_CANCEL_REPOST
    return QUEUE_HOLD


# ---------------------------------------------------------------------------
# Velocity gate
# ---------------------------------------------------------------------------

@dataclass
class VelocityGate:
    cooldowns: dict = field(default_factory=dict)        # game_key -> cooldown-until ts
    _leg_obs: dict = field(default_factory=dict)         # (game_key, ticker) -> [(ts, p)]
    _print_obs: dict = field(default_factory=dict)       # (game_key, ticker) -> [(ts, price)]

    def in_cooldown(self, game_key: str, now: float) -> bool:
        return self.cooldowns.get(game_key, 0.0) > now

    def _trip(self, game_key: str, now: float) -> bool:
        self.cooldowns[game_key] = now + VELOCITY_COOLDOWN_SEC
        return True

    def observe_leg(self, game_key: str, ticker: str, p_cons: float, ts: float) -> bool:
        """True (and cooldown imposed) when this leg's consensus moved >= 3.0pp
        within <= 5s of a previous observation."""
        hist = self._leg_obs.setdefault((game_key, ticker), [])
        tripped = any(ts - t0 <= VELOCITY_LEG_WINDOW_SEC and abs(p_cons - p0) >= VELOCITY_LEG_MOVE
                      for t0, p0 in hist)
        hist.append((ts, p_cons))
        self._leg_obs[(game_key, ticker)] = [x for x in hist if ts - x[0] <= 60.0]
        return self._trip(game_key, ts) if tripped else False

    def observe_print(self, game_key: str, ticker: str, price: float, ts: float) -> bool:
        """True (and cooldown imposed) when a Kalshi single-leg print jumped
        >= 15c within <= 2s of a previous print."""
        hist = self._print_obs.setdefault((game_key, ticker), [])
        tripped = any(ts - t0 <= VELOCITY_PRINT_WINDOW_SEC and abs(price - p0) >= VELOCITY_PRINT_MOVE
                      for t0, p0 in hist)
        hist.append((ts, price))
        self._print_obs[(game_key, ticker)] = [x for x in hist if ts - x[0] <= 60.0]
        return self._trip(game_key, ts) if tripped else False


# ---------------------------------------------------------------------------
# Engine: posting, revalidation, reconciliation kill switch
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreshSnapshot:
    """Fresh fairs for one resting order's combo, supplied on every book/leg
    update for full revalidation."""
    candidate: ComboCandidate
    p_true_yes: float
    p_cons_yes: float


@dataclass
class RestingEngine:
    venue: object                            # RestingVenue (fake in tests)
    limits: RestingLimits = field(default_factory=RestingLimits)
    unwind_default: bool = True              # per-order fee-reserve choice; tested default: unwind (2x)
    orders: dict = field(default_factory=dict)   # client_order_id -> RestingOrder
    velocity: VelocityGate = field(default_factory=VelocityGate)
    exposure: ExposureBook = field(default_factory=ExposureBook)
    killed: bool = False
    kill_reason: str | None = None
    daily_pnl: float = 0.0                   # realized + mark-to-market, dollars

    def __post_init__(self):
        self.exposure = ExposureBook(self.limits)

    # -- kill switch --
    def trip_kill(self, why: str = "") -> None:
        if not self.killed:
            self.killed = True
            self.kill_reason = KILL_REASON
        self.cancel_all()

    def check_daily_loss(self) -> bool:
        if self.daily_pnl <= -self.limits.daily_loss_limit:
            self.trip_kill(f"daily loss {self.daily_pnl:.2f}")
            return True
        return False

    # -- posting --
    def consider(self, combo: ComboCandidate, now: float, *,
                 unwind: bool | None = None) -> dict:
        """Full admission path for one discovered combo. Returns a decision dict;
        posts through the venue only when every gate passes."""
        if self.killed:
            return {"action": "REJECT", "reason": "KILLED", "kill_reason": self.kill_reason}
        if self.check_daily_loss():
            return {"action": "REJECT", "reason": "KILLED", "kill_reason": self.kill_reason}
        if not toxicity_ok(combo.created_ts, now):
            return {"action": "REJECT", "reason": "REJ_TOXICITY_WINDOW",
                    "detail": "ticker created < 10 minutes ago"}
        for gk in combo.game_keys:
            if self.velocity.in_cooldown(gk, now):
                return {"action": "REJECT", "reason": "REJ_COOLDOWN", "detail": gk}
        if combo.n_unpriced >= 1:
            # Tier admission rejects on the unpriced count before any joint is
            # needed (a joint over a missing fair is not computable anyway).
            tier = evaluate_tier(combo, None)
            return {"action": "REJECT", "reason": tier.reason, "detail": tier.detail}
        try:
            p_true, p_cons = joint_probabilities(combo)
        except ValueError as e:
            return {"action": "REJECT", "reason": REJ_SAME_PLAYER_UNMEASURED, "detail": str(e)}
        tier = evaluate_tier(combo, p_cons)
        if not tier.admitted:
            return {"action": "REJECT", "reason": tier.reason, "detail": tier.detail}
        use_unwind = self.unwind_default if unwind is None else unwind
        quote = price_quote(combo, tier, p_true, p_cons, unwind=use_unwind)
        if quote is None:
            return {"action": "REJECT", "reason": "REJ_LOW_ROI",
                    "detail": "pricing floor / net ROI gate failed"}
        count = float(self.limits.contracts_per_order)
        open_orders = [o for o in self.orders.values() if o.status == "resting"]
        breach = self.exposure.check(open_orders, combo.market_ticker, combo.game_keys,
                                     count, quote.no_bid)
        if breach:
            return {"action": "REJECT", "reason": breach}
        client_order_id = "kxrest" + uuid.uuid4().hex[:24]
        resp = self.venue.submit_resting_no(market_ticker=combo.market_ticker,
                                            client_order_id=client_order_id,
                                            count=count, no_price=quote.no_bid)
        order = RestingOrder(
            client_order_id=client_order_id, market_ticker=combo.market_ticker,
            game_keys=combo.game_keys, leg_tickers=tuple(leg.ticker for leg in combo.legs),
            count=count, no_price=quote.no_bid, p_true_yes=p_true, p_cons_yes=p_cons,
            fee_reserve_per_contract=quote.fee_reserve_per_contract, unwind=use_unwind,
            tier=tier.tier or 0, order_id=resp.get("order_id"), status="resting",
            posted_ts=now)
        self.orders[client_order_id] = order
        return {"action": "POSTED", "reason": tier.reason, "order": order, "quote": quote}

    # -- cancellation --
    def cancel_order(self, order: RestingOrder, now: float) -> bool:
        if order.status == "cancelled":
            return True
        self.venue.cancel(order_id=order.order_id or "", market_ticker=order.market_ticker)
        order.status = "cancel_pending"
        order.cancel_requested_ts = now
        return True

    def cancel_all(self, now: float = 0.0) -> int:
        n = 0
        for order in list(self.orders.values()):
            if order.status == "resting":
                self.cancel_order(order, now)
                n += 1
        return n

    def cancel_game(self, game_key: str, now: float) -> int:
        n = 0
        for order in list(self.orders.values()):
            if order.status == "resting" and game_key in order.game_keys:
                self.cancel_order(order, now)
                n += 1
        return n

    # -- velocity gate (control 3) --
    def on_leg_consensus(self, game_key: str, ticker: str, p_cons: float, ts: float) -> bool:
        """Feed one leg consensus observation. On a trip, cancel ALL resting
        orders containing game_key (the 120s cooldown is set by the gate)."""
        if self.velocity.observe_leg(game_key, ticker, p_cons, ts):
            self.cancel_game(game_key, ts)
            return True
        return False

    def on_kalshi_print(self, game_key: str, ticker: str, price: float, ts: float) -> bool:
        if self.velocity.observe_print(game_key, ticker, price, ts):
            self.cancel_game(game_key, ts)
            return True
        return False

    # -- full revalidation (control 2) --
    def revalidate(self, fresh: dict, now: float) -> list:
        """Re-check tier admission, the pricing floor and exposure caps for every
        resting order against fresh fairs. Cancels any order that fails; returns
        the cancelled client_order_ids. Exposure is re-checked sequentially: an
        order that no longer fits alongside the orders kept before it is cancelled."""
        cancelled: list = []
        kept: list = []
        for order in list(self.orders.values()):
            if order.status != "resting":
                continue
            snap = fresh.get(order.market_ticker)
            if snap is None:
                continue                       # no fresh fair this tick: leave resting
            tier = evaluate_tier(snap.candidate, snap.p_cons_yes)
            ok = tier.admitted
            quote = None
            if ok:
                quote = price_quote(snap.candidate, tier, snap.p_true_yes, snap.p_cons_yes,
                                    unwind=order.unwind)
                ok = quote is not None and quote.no_bid >= order.no_price - 1e-9
            if ok:
                breach = self.exposure.check(kept, order.market_ticker, order.game_keys,
                                             order.count, order.no_price)
                ok = breach is None
            if ok:
                kept.append(order)
            else:
                self.cancel_order(order, now)
                cancelled.append(order.client_order_id)
        return cancelled

    # -- queue policy application --
    def apply_queue_policy(self, order: RestingOrder, current_p_true_yes: float, now: float, *,
                           new_count: float | None = None, new_expiration_ts: float | None = None,
                           velocity_tripped: bool = False) -> str:
        action = queue_decision(order, current_p_true_yes,
                                size_change=new_count is not None and new_count != order.count,
                                expiry_change=new_expiration_ts is not None,
                                velocity_tripped=velocity_tripped)
        if action == QUEUE_AMEND:
            self.venue.amend(order_id=order.order_id or "", market_ticker=order.market_ticker,
                             count=new_count, expiration_ts=new_expiration_ts)
            if new_count is not None:
                order.count = float(new_count)
        elif action in (QUEUE_CANCEL, QUEUE_CANCEL_REPOST):
            self.cancel_order(order, now)
        return action

    # -- reconciliation kill switch (control 1) --
    def reconcile(self, now: float) -> dict:
        """One 30s reconciliation poll. Keyed on client_order_id, tolerating the
        ~1s read-after-write lag. Any orphan fill, position without a known order,
        unknown resting order, or order still resting after its cancel grace trips
        kill_reason='kalshi_resting', halts posting and cancels everything."""
        venue_orders = self.venue.get_orders()
        venue_positions = self.venue.get_positions()
        venue_fills = self.venue.get_fills()
        problems: list = []

        venue_by_coid = {}
        for vo in venue_orders:
            coid = vo.get("client_order_id")
            status = str(vo.get("status") or "resting").lower()
            if coid not in self.orders:
                created = vo.get("created_ts")
                if created is not None and now - float(created) <= READ_AFTER_WRITE_GRACE_SEC:
                    continue                   # possibly our own just-posted order, not yet keyed locally
                if status == "resting":
                    problems.append(f"unknown resting order {vo.get('order_id')} coid={coid}")
                continue
            venue_by_coid[coid] = vo

        for coid, order in self.orders.items():
            vo = venue_by_coid.get(coid)
            if order.status == "cancel_pending":
                if vo is not None and str(vo.get("status") or "").lower() == "resting":
                    since = order.cancel_requested_ts or now
                    if now - since > CANCEL_GRACE_SEC:
                        problems.append(f"order {order.order_id} still resting {now - since:.1f}s after cancel")
                elif vo is None:
                    order.status = "cancelled"
            elif order.status == "resting" and vo is None:
                # Missing from the venue's resting list is only alarming once the
                # read-after-write grace has passed AND no fill explains it.
                filled = any(f.get("client_order_id") == coid for f in venue_fills)
                if not filled and now - order.posted_ts > READ_AFTER_WRITE_GRACE_SEC:
                    pass                       # filled/settled between polls is reconciled via fills below

        known_tickers = {o.market_ticker for o in self.orders.values()}
        for fill in venue_fills:
            coid = fill.get("client_order_id")
            if coid is not None and coid not in self.orders:
                problems.append(f"orphan fill {fill.get('fill_id') or fill.get('id')} coid={coid}")
            elif coid is None and fill.get("order_id") not in {o.order_id for o in self.orders.values()}:
                problems.append(f"orphan fill {fill.get('fill_id') or fill.get('id')} (no client_order_id)")

        for pos in venue_positions:
            qty = pos.get("position_fp", pos.get("position", 0))
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                qty = 0.0
            ticker = pos.get("ticker") or pos.get("market_ticker")
            if qty != 0.0 and ticker not in known_tickers:
                problems.append(f"position without a known order: {ticker} qty={qty}")

        if problems:
            self.trip_kill("; ".join(problems))
            return {"ok": False, "kill_reason": self.kill_reason, "problems": problems}
        return {"ok": True, "kill_reason": None, "problems": []}
